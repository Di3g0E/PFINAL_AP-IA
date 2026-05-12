"""
Servicio de notificaciones multiusuario (Telegram + WhatsApp).

Origen: P5_AP-IA/src/utils/notification_service.py — adaptado para P6:
  - Multi-usuario: cada usuario configura sus propios destinatarios
    (telegram_chat_id, whatsapp_phone) en `user_settings`.
  - Privacidad: nivel de detalle configurable por usuario:
      'redacted' (defecto) — solo evento + categoría, sin importes ni descripciones
      'full' — incluye todos los datos de la transacción (opt-in explícito)
  - Telegram: canal primario (HTTP API limpia, sirve en cloud headless).
    El TOKEN del bot vive en .env del servidor (compartido).
  - WhatsApp: canal secundario opcional vía pywhatkit. Limitación:
    requiere navegador con WhatsApp Web logueado en la máquina del backend.
    Solo intentado si el usuario configura su número y `notifications_enabled=True`.
  - Nuevo helper `notify_goal_threshold` para alertas de objetivos >80%.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Optional

import requests
from loguru import logger

from src.utils.config import settings

# pywhatkit arrastra pyautogui → mouseinfo, que al importarse trata de abrir
# un display X. En entornos headless (HF Spaces, Docker sin GUI) eso lanza
# KeyError('DISPLAY') o similar al importar, no ImportError. Capturamos
# Exception para que el módulo siga cargando con WhatsApp deshabilitado.
try:
    import pywhatkit
    PYWHATKIT_AVAILABLE = True
except Exception as e:
    pywhatkit = None
    PYWHATKIT_AVAILABLE = False
    logger.warning(f"pywhatkit deshabilitado ({type(e).__name__}: {e}) — WhatsApp no disponible")


NotificationLevel = Literal["redacted", "full"]


@dataclass
class UserNotificationConfig:
    """Configuración de notificaciones por usuario (lee de la tabla user_settings)."""
    user_id: str
    notifications_enabled: bool = False
    telegram_chat_id: Optional[str] = None
    whatsapp_phone: Optional[str] = None      # formato internacional, ej. +34600000000
    notification_level: NotificationLevel = "redacted"


# Servicios de canal

class TelegramService:
    """Bot de Telegram vía HTTP API (Markdown soportado, invisible para el usuario)."""

    # HF Spaces tiene latencia variable hacia api.telegram.org; un timeout
    # generoso evita falsos negativos. Tuple (connect, read).
    _TIMEOUT: tuple[float, float] = (10.0, 25.0)
    # Reintentos ante errores transitorios (ReadTimeout, ConnectionError, 5xx).
    _MAX_ATTEMPTS: int = 3

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send_message(self, message: str) -> tuple[bool, Optional[str]]:
        """Envía un mensaje. Devuelve (ok, error_detail).

        Estrategia de reintentos: hasta `_MAX_ATTEMPTS` intentos con backoff
        exponencial (1s, 2s, 4s) solo para fallos transitorios — ReadTimeout,
        ConnectionError y 5xx del servidor de Telegram. Para errores 4xx
        (chat not found, bot blocked, etc.) no reintenta porque son
        permanentes y la culpa la tiene la configuración del usuario.

        Cuando Telegram responde con 4xx/5xx, extrae el campo `description`
        del JSON oficial para que el caller pueda mostrárselo al usuario.
        """
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
        }
        last_detail: Optional[str] = None

        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                response = requests.post(self.base_url, json=payload, timeout=self._TIMEOUT)
            except (requests.Timeout, requests.ConnectionError) as e:
                last_detail = f"{type(e).__name__}: {e}"
                logger.warning(
                    f"Telegram intento {attempt}/{self._MAX_ATTEMPTS} falló por red: {last_detail}"
                )
                if attempt < self._MAX_ATTEMPTS:
                    time.sleep(2 ** (attempt - 1))
                continue
            except requests.RequestException as e:
                detail = f"{type(e).__name__}: {e}"
                logger.error(f"Telegram request error: {detail}")
                return False, detail
            except Exception as e:
                detail = f"{type(e).__name__}: {e}"
                logger.error(f"Telegram unexpected error: {detail}")
                return False, detail

            if response.status_code >= 500:
                # 5xx: el servidor de Telegram está mal, merece reintento.
                try:
                    err_desc = response.json().get("description") or response.text
                except Exception:
                    err_desc = response.text
                last_detail = f"Telegram API {response.status_code}: {err_desc}"
                logger.warning(
                    f"Telegram intento {attempt}/{self._MAX_ATTEMPTS} → {last_detail}"
                )
                if attempt < self._MAX_ATTEMPTS:
                    time.sleep(2 ** (attempt - 1))
                continue

            if response.status_code >= 400:
                # 4xx: error permanente; no reintenta.
                try:
                    err_desc = response.json().get("description") or response.text
                except Exception:
                    err_desc = response.text
                detail = f"Telegram API {response.status_code}: {err_desc}"
                logger.error(detail)
                return False, detail

            logger.info(f"Telegram → chat {self.chat_id} OK (intento {attempt})")
            return True, None

        # Agotamos los reintentos sin éxito.
        return False, last_detail or "Telegram inalcanzable tras varios reintentos"


class WhatsAppService:
    """WhatsApp Web vía pywhatkit. Best-effort: requiere sesión activa de WhatsApp Web."""

    def __init__(self, phone_number: str):
        if not PYWHATKIT_AVAILABLE:
            raise ImportError("pywhatkit no disponible")
        self.phone_number = phone_number

    def send_message(self, message: str, wait_time: int = 15) -> bool:
        try:
            pywhatkit.sendwhatmsg_instantly(
                phone_no=self.phone_number,
                message=message,
                wait_time=wait_time,
                tab_close=True,
                close_time=3,
            )
            logger.info(f"WhatsApp → {self.phone_number} OK")
            return True
        except Exception as e:
            logger.error(f"WhatsApp error: {e}")
            return False


# Plantillas de mensajes

def _redact(value, level: NotificationLevel, placeholder: str = "[oculto]"):
    """Devuelve el valor o un placeholder según el nivel de privacidad configurado."""
    return value if level == "full" else placeholder


def get_notification_message(
    action: str,
    level: NotificationLevel = "redacted",
    success: bool = True,
    **kwargs,
) -> str:
    """Genera el texto Markdown del mensaje según la acción y nivel de privacidad."""
    timestamp = time.strftime("%H:%M:%S")
    user_id = kwargs.get("user_id", "?")

    if action == "register":
        return (
            "👤 *Nuevo registro de usuario*\n"
            f"🆔 ID: `{user_id}`\n"
            f"⏰ Hora: {timestamp}"
        )

    if action == "login":
        emoji = "✅" if success else "❌"
        status = "SUCCESS" if success else "FAILED"
        msg = kwargs.get("message", "Acceso concedido" if success else "Acceso denegado")
        lines = [
            f"{emoji} *Login {status}*",
            f"👤 Usuario: `{user_id}`",
            f"📝 {msg}",
            f"⏰ Hora: {timestamp}",
        ]
        if level == "full":
            sim = kwargs.get("similarity")
            live = kwargs.get("liveness")
            if sim is not None:
                lines.append(f"🎯 Similitud: {sim:.3f}")
            if live is not None:
                lines.append(f"🛡️ Liveness: {live:.3f}")
        return "\n".join(lines)

    if action == "finance_anomaly":
        amount = _redact(kwargs.get("amount"), level)
        area = _redact(kwargs.get("area"), level)
        desc = _redact(kwargs.get("description"), level)
        date_str = kwargs.get("date")
        type_val = kwargs.get("type")
        reasons = kwargs.get("reasons") or []

        lines = [
            "⚠️ *Posible anomalía financiera detectada*",
            f"👤 Usuario: `{user_id}`",
            f"📅 Fecha: {date_str}",
            f"📂 Área: {area}",
            f"🔖 Tipo: {type_val}",
            f"💶 Cantidad: {amount}",
        ]
        if level == "full" and desc:
            lines.append(f"📝 Descripción: {desc}")
        if reasons:
            lines.append("🔍 *Detalle*:")
            for r in reasons:
                lines.append(f"  • {r}")
        lines.append(f"⏰ Hora: {timestamp}")
        return "\n".join(lines)

    if action == "goal_threshold":
        area = _redact(kwargs.get("area"), level)
        current = _redact(kwargs.get("current"), level)
        limit = _redact(kwargs.get("limit"), level)
        pct = kwargs.get("pct", 0.0)
        severity = kwargs.get("severity", "warning")
        emoji = "🚨" if severity == "critical" else "⚠️"

        lines = [
            f"{emoji} *Alerta de objetivo*",
            f"👤 Usuario: `{user_id}`",
            f"📂 Área: {area}",
            f"📊 Progreso: {pct * 100:.0f}% del límite",
        ]
        if level == "full":
            lines.append(f"💶 Gastado: {current} / Límite: {limit}")
        lines.append(f"⏰ Hora: {timestamp}")
        return "\n".join(lines)

    return f"Notificación de sistema: {action} para `{user_id}`"


# Despachador

def notify(config: UserNotificationConfig, action: str, success: bool = True, **kwargs) -> dict:
    """
    Envía la notificación al usuario por todos los canales que tenga configurados.

    Args:
        config: configuración del usuario (de tabla user_settings).
        action: tipo de evento ('register', 'login', 'finance_anomaly', 'goal_threshold').
        success: solo relevante para 'login'.
        **kwargs: payload del evento.

    Returns:
        Dict con resultado por canal: {
            "telegram": "ok"|"error"|"skipped",
            "telegram_error": Optional[str],
            "whatsapp": "ok"|"error"|"skipped",
            "whatsapp_error": Optional[str],
        }
    """
    result: dict = {"telegram": "skipped", "whatsapp": "skipped"}

    if not config.notifications_enabled:
        logger.debug(f"Notificaciones deshabilitadas para {config.user_id}")
        result["telegram_error"] = "notifications_enabled=False"
        return result

    kwargs["user_id"] = config.user_id
    message = get_notification_message(action, level=config.notification_level,
                                       success=success, **kwargs)

    # 1. Telegram (canal primario)
    bot_token = settings.telegram_bot_token
    if bot_token and config.telegram_chat_id:
        ok, err = TelegramService(bot_token, config.telegram_chat_id).send_message(message)
        result["telegram"] = "ok" if ok else "error"
        if not ok:
            result["telegram_error"] = err
    elif config.telegram_chat_id and not bot_token:
        msg = "TELEGRAM_BOT_TOKEN no configurado en el servidor"
        logger.warning(msg)
        result["telegram"] = "error"
        result["telegram_error"] = msg
    elif not config.telegram_chat_id:
        result["telegram_error"] = "Sin telegram_chat_id"

    # 2. WhatsApp (canal secundario, best-effort)
    if config.whatsapp_phone and PYWHATKIT_AVAILABLE:
        plain_message = message.replace("*", "").replace("`", "")
        try:
            WhatsAppService(config.whatsapp_phone).send_message(plain_message)
            result["whatsapp"] = "ok"
        except Exception as e:
            logger.warning(f"WhatsApp falló para {config.user_id}: {e}")
            result["whatsapp"] = "error"
            result["whatsapp_error"] = str(e)

    return result


# Helpers de alto nivel (uno por trigger del sistema)
#
# Devuelven el dict de `notify()` para que el caller pueda loguear o propagar el
# resultado. Hasta P6 estos helpers devolvían None; lo cambiamos al diagnosticar
# que los fallos de Telegram se perdían silenciosamente (p. ej. notificación de
# login que nunca llegaba a pesar de que el "Probar" sí).

def notify_register(config: UserNotificationConfig) -> dict:
    return notify(config, "register", success=True)


def notify_login(
    config: UserNotificationConfig,
    *,
    success: bool,
    similarity: Optional[float] = None,
    liveness: Optional[float] = None,
    message: Optional[str] = None,
) -> dict:
    return notify(config, "login", success=success,
                  similarity=similarity, liveness=liveness, message=message)


def notify_finance_anomaly(
    config: UserNotificationConfig,
    *,
    reasons: list[str],
    date: str,
    amount,
    area: str,
    type_val: str,
    description: Optional[str] = None,
) -> dict:
    return notify(config, "finance_anomaly", success=False,
                  reasons=reasons, date=date, amount=amount,
                  area=area, type=type_val, description=description)


def notify_goal_threshold(
    config: UserNotificationConfig,
    *,
    area: str,
    current,
    limit,
    pct: float,
) -> dict:
    severity = "critical" if pct >= 1.0 else "warning"
    return notify(config, "goal_threshold",
                  area=area, current=current, limit=limit, pct=pct, severity=severity)
