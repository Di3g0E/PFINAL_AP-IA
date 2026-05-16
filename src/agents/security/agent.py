"""
Agente Security — operaciones expuestas al Orquestador y al Registrar.

Operaciones (ver doc/agent_contracts.md):
  - validate_transaction(draft) → SecurityVerdict
  - register_user(request) → SecurityVerdict
  - login_user(request) → SecurityVerdict

`validate_transaction` valida transacciones contra el histórico del propio
usuario usando `FinancialAnomalyDetector` (IsolationForest + 3-Sigma).

`register_user` y `login_user` exigen:
  - Imagen facial (bytes JPEG/PNG)
  - Liveness > umbral
  - Embedding compatible con el almacenado (en login)
  - Passphrase válida (bcrypt)
  - Consentimiento biométrico (RGPD, en register)
  - Lockout tras N fallos consecutivos

Notificaciones:
  - register OK         → notify_register
  - login OK / KO       → notify_login
  - validate anómala    → notify_finance_anomaly
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import bcrypt
from loguru import logger
from sqlalchemy import select

from src.agents.analyst.data_source import load_user_history_db_only
from src.agents.contracts import (
    LoginRequest, RegisterRequest, SecurityVerdict, TransactionDraft,
)
from src.agents.security.anomaly_detector import FinancialAnomalyDetector
from src.utils.config import settings
from src.utils.notifications import (
    UserNotificationConfig, notify_finance_anomaly,
    notify_login, notify_register,
)
from src.utils.security import AccessController, EncryptedEmbeddingStore


# Recursos compartidos (lazy)

_EMBEDDING_STORE: Optional[EncryptedEmbeddingStore] = None
_ACCESS_CONTROLLER = AccessController(max_attempts=5, lockout_seconds=300)


def _format_db_error(e: Exception) -> str:
    """
    Convierte una excepción de SQLAlchemy/psycopg en un mensaje útil para el
    cliente (sin filtrar credenciales ni paths sensibles).

    SQLAlchemy envuelve el error de la BD; el mensaje original suele estar
    en `e.orig` (DBAPIError). Truncamos a 200 caracteres para no devolver
    trazas enormes.
    """
    detail = ""
    orig = getattr(e, "orig", None)
    if orig is not None:
        detail = str(orig)
    if not detail:
        detail = str(e)
    detail = detail.strip().splitlines()[0]   # primera línea, sin la traza SQL
    if len(detail) > 200:
        detail = detail[:200] + "…"
    return f"{type(e).__name__}: {detail}" if detail else type(e).__name__


def _get_embedding_store() -> EncryptedEmbeddingStore:
    """Almacén cifrado de embeddings biométricos. Singleton lazy.

    Backend Postgres (tabla `biometric_embeddings`) — sin estado en disco.
    """
    global _EMBEDDING_STORE
    if _EMBEDDING_STORE is None:
        _EMBEDDING_STORE = EncryptedEmbeddingStore(
            passphrase=settings.embedding_store_passphrase,
        )
    return _EMBEDDING_STORE


def _get_notif_config(user_id: str) -> Optional[UserNotificationConfig]:
    """Carga la config de notificaciones del usuario desde BD. None si no hay.

    Loguea explícitamente cada razón por la que devuelve None para poder
    diagnosticar en producción por qué una notificación no se dispara
    (BD no configurada, user_id no es UUID, row inexistente, etc.).
    """
    try:
        from src.data.database import get_session, is_database_configured
        from src.data.schema import UserSettings
    except Exception as e:
        logger.warning(f"_get_notif_config: import falló: {e}")
        return None

    if not is_database_configured():
        logger.warning(f"_get_notif_config: BD no configurada (user_id={user_id})")
        return None

    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        logger.warning(f"_get_notif_config: user_id no es UUID válido: {user_id}")
        return None

    try:
        with get_session() as session:
            row = session.execute(
                select(UserSettings).where(UserSettings.user_id == user_uuid)
            ).scalar_one_or_none()
            if row is None:
                logger.info(f"_get_notif_config: sin UserSettings para {user_id}")
                return None
            cfg = UserNotificationConfig(
                user_id=user_id,
                notifications_enabled=row.notifications_enabled,
                telegram_chat_id=row.telegram_chat_id,
                whatsapp_phone=row.whatsapp_phone,
                notification_level=row.notification_level,  # type: ignore[arg-type]
            )
            logger.debug(
                f"_get_notif_config OK: enabled={cfg.notifications_enabled} "
                f"telegram={'set' if cfg.telegram_chat_id else 'none'}"
            )
            return cfg
    except Exception as e:
        logger.warning(f"Lookup de notification config falló: {e}")
        return None


# Operación 1: validate_transaction (anomaly detection)

def validate_transaction(
    draft: TransactionDraft,
    notif_config: Optional[UserNotificationConfig] = None,
) -> SecurityVerdict:
    """
    Valida una transacción candidata contra el histórico del usuario.

    - decision='allow' si no hay señales de anomalía (o no hay histórico).
    - decision='challenge' si una o más reglas la marcan: el Registrar
      debe encolarla en `pending_review` para que el usuario confirme.
    """
    df = load_user_history_db_only(draft.user_id)

    if df.empty:
        logger.debug(
            f"validate_transaction: sin histórico para {draft.user_id}; allow por defecto"
        )
        return SecurityVerdict(
            decision="allow",
            user_id=draft.user_id,
            reason="Sin histórico para validar; aceptada por defecto.",
        )

    detector = FinancialAnomalyDetector(df)
    is_anomalous, reasons = detector.predict(
        date=draft.date,
        amount=float(draft.amount),
        area=draft.area,
        type_val=draft.type,
    )

    if not is_anomalous:
        return SecurityVerdict(
            decision="allow",
            user_id=draft.user_id,
            reason="Validación anti-anomalía pasada.",
        )

    # Notificación opt-in
    cfg = notif_config or _get_notif_config(draft.user_id)
    if cfg is not None:
        try:
            area_str = ", ".join(draft.area) if draft.area else "Other"
            notify_finance_anomaly(
                cfg, reasons=reasons, date=draft.date.isoformat(),
                amount=float(draft.amount), area=area_str,
                type_val=draft.type, description=draft.description,
            )
        except Exception as e:
            logger.warning(f"notify_finance_anomaly falló: {e}")

    return SecurityVerdict(
        decision="challenge",
        user_id=draft.user_id,
        reason=f"Anomalía detectada ({len(reasons)} señal/es).",
        anomaly_reasons=reasons,
    )


# Operaciones 2 y 3: register_user / login_user

def register_user(request: RegisterRequest) -> SecurityVerdict:
    """
    Alta de usuario nueva con foto + passphrase.

    Pasos:
      1. RGPD: consentimiento biométrico obligatorio.
      2. Pipeline: detección + alineación + liveness + embedding.
      3. Liveness >= 0.95 (rechaza fotos impresas / pantallas).
      4. Crea user en BD, hash bcrypt de la passphrase.
      5. Almacena embedding cifrado (Fernet + PBKDF2) en disco.
      6. Notifica vía Telegram/WhatsApp si tiene canal configurado.
    """
    # 1. RGPD
    if not request.biometric_consent:
        return SecurityVerdict(
            decision="deny",
            reason="Consentimiento biométrico obligatorio para registrarse.",
        )

    # 2-3. Biometría
    try:
        from src.agents.security.biometrics import (
            BiometricPipeline, decode_image_bytes, LIVENESS_THRESHOLD,
        )
    except Exception as e:
        return SecurityVerdict(decision="deny",
                               reason=f"Pipeline biométrico no disponible: {e}")

    try:
        image = decode_image_bytes(request.face_image)
        features = BiometricPipeline.shared().extract(image)
    except ValueError as e:
        return SecurityVerdict(decision="deny", reason=str(e))
    except Exception as e:
        logger.exception(f"register_user: pipeline biométrico falló: {e}")
        return SecurityVerdict(decision="deny",
                               reason=f"Error procesando la imagen: {type(e).__name__}")

    if not features.is_live:
        return SecurityVerdict(
            decision="deny",
            reason="Liveness insuficiente: la imagen parece un ataque de presentación.",
            similarity=None,
            liveness_score=features.liveness_score,
        )

    # 4. Crear user en BD
    try:
        from src.data.database import get_session, is_database_configured
        from src.data.schema import User
    except Exception as e:
        return SecurityVerdict(decision="deny",
                               reason=f"BD no disponible: {e}")

    if not is_database_configured():
        return SecurityVerdict(decision="deny", reason="BD no configurada.")

    try:
        with get_session() as session:
            existing = session.execute(
                select(User).where(User.email == request.email)
            ).scalar_one_or_none()
            if existing is not None:
                return SecurityVerdict(
                    decision="deny",
                    reason=f"El email '{request.email}' ya está registrado.",
                )

            user = User(
                id=uuid.uuid4(),
                email=request.email,
                passphrase_hash=bcrypt.hashpw(
                    request.passphrase.encode(), bcrypt.gensalt(),
                ).decode(),
                biometric_consent=True,
                biometric_consent_at=datetime.now(timezone.utc),
            )
            session.add(user)
            session.flush()
            user_id = str(user.id)
            
            # Crear UserSettings con configuración de notificaciones
            from src.data.schema import UserSettings
            settings = UserSettings(
                user_id=user.id,
                notifications_enabled=request.notifications_enabled,
                telegram_chat_id=request.telegram_chat_id if request.notifications_enabled else None,
                notification_level="redacted",  # Nivel seguro por defecto
            )
            session.add(settings)
    except Exception as e:
        logger.exception(f"register_user: INSERT falló: {e}")
        # Devolvemos el detalle al cliente para facilitar el diagnóstico:
        # OperationalError suele ser BD caída o tabla/columna inexistente.
        msg = _format_db_error(e)
        return SecurityVerdict(decision="deny",
                               reason=f"Error al crear el usuario: {msg}")

    # 5. Almacén cifrado de embedding (persistente en Postgres)
    try:
        _get_embedding_store().store(user_id, features.embedding)
    except Exception as e:
        logger.exception(f"register_user: store de embedding falló: {e}")
        return SecurityVerdict(
            decision="deny",
            reason=f"No se pudo almacenar el embedding: {_format_db_error(e)}",
        )

    # 6. Notificación opt-in - Usar el sistema de notificaciones integrado
    if request.notifications_enabled and request.telegram_chat_id:
        try:
            from src.utils.notifications import UserNotificationConfig, notify_register
            cfg = UserNotificationConfig(
                user_id=user_id,
                notifications_enabled=request.notifications_enabled,
                telegram_chat_id=request.telegram_chat_id,
                notification_level="redacted"  # Por defecto seguro
            )
            notify_register(cfg)
            logger.info(f"Notificación de registro enviada a {request.telegram_chat_id}")
        except Exception as e:
            logger.warning(f"notify_register falló: {e}")
    else:
        logger.debug(f"Notificaciones desactivadas o sin Chat ID para {user_id}")

    logger.info(f"Usuario registrado: {user_id} ({request.email})")
    return SecurityVerdict(
        decision="allow",
        user_id=user_id,
        reason=f"Usuario registrado. Liveness={features.liveness_score:.2f}",
        liveness_score=features.liveness_score,
    )


def login_user(request: LoginRequest) -> SecurityVerdict:
    """
    Autenticación: passphrase (bcrypt) + biometría (FaceNet + Liveness).

    Pasos:
      1. Lookup por email; si no existe → deny (mensaje genérico para no
         filtrar qué emails están registrados).
      2. Lockout: si el usuario está bloqueado, deny inmediato.
      3. Verificar passphrase con bcrypt.
      4. Pipeline biométrico: liveness + embedding.
      5. Comparar con embedding almacenado vía coseno.
      6. Si similitud >= umbral y liveness OK → allow + reset attempts.
      7. Cualquier fallo → record_failure + deny.
    """
    # 1. Lookup
    try:
        from src.data.database import get_session, is_database_configured
        from src.data.schema import User
    except Exception as e:
        return SecurityVerdict(decision="deny", reason=f"BD no disponible: {e}")

    if not is_database_configured():
        return SecurityVerdict(decision="deny", reason="BD no configurada.")

    user_id: Optional[str] = None
    pass_hash: Optional[str] = None
    is_admin = False
    try:
        with get_session() as session:
            user = session.execute(
                select(User).where(User.email == request.email)
            ).scalar_one_or_none()
            if user is not None:
                user_id = str(user.id)
                pass_hash = user.passphrase_hash
                is_admin = bool(user.is_admin)
    except Exception as e:
        logger.warning(f"login_user lookup falló: {e}")

    if user_id is None or pass_hash is None:
        return SecurityVerdict(decision="deny", reason="Credenciales incorrectas.")

    # 2. Lockout
    if _ACCESS_CONTROLLER.is_locked(user_id):
        return SecurityVerdict(
            decision="deny",
            user_id=user_id,
            reason="Cuenta bloqueada temporalmente por múltiples intentos fallidos.",
        )

    # 3. Passphrase
    try:
        ok = bcrypt.checkpw(request.passphrase.encode(), pass_hash.encode())
    except (ValueError, TypeError):
        ok = False
    if not ok:
        _ACCESS_CONTROLLER.record_failure(user_id)
        _maybe_notify_login(user_id, success=False, reason="passphrase")
        return SecurityVerdict(decision="deny", user_id=user_id,
                               reason="Credenciales incorrectas.")

    # 3.5. Bypass biometría para cuentas admin: tras passphrase OK devolvemos
    # allow sin tocar el pipeline. Esto permite que un admin entre por el
    # endpoint normal /auth/login aunque el frontend mande foto/vídeo, lo cual
    # es necesario mientras Vercel no haya redeployado /admin/login. La foto
    # se ignora completamente — no se almacena ni se compara.
    if is_admin:
        _ACCESS_CONTROLLER.record_success(user_id)
        logger.info(f"login_user (admin bypass): {user_id} ({request.email})")
        return SecurityVerdict(
            decision="allow", user_id=user_id,
            reason="Admin login OK (biometría omitida).",
        )

    # 4. Biometría
    try:
        from src.agents.security.biometrics import (
            BiometricPipeline, SIMILARITY_THRESHOLD, decode_image_bytes,
        )
    except Exception as e:
        return SecurityVerdict(decision="deny", user_id=user_id,
                               reason=f"Pipeline biométrico no disponible: {e}")

    # Umbral de similitud configurable vía .env (settings.face_similarity_threshold).
    # Fallback al constante si settings no se puede importar.
    sim_threshold = getattr(settings, "face_similarity_threshold", SIMILARITY_THRESHOLD)

    # E3 Fase 1: si hay vídeo y el feature flag está activo, usa el pipeline
    # de voto promedio de N frames. Si no, single-frame legacy.
    use_video = (
        request.face_video is not None
        and len(request.face_video) > 0
        and getattr(settings, "security_video_enabled", False)
    )
    n_frames = getattr(settings, "security_video_n_frames", 10)

    try:
        if use_video:
            logger.info(f"login_user: modo vídeo (n_frames={n_frames})")
            features = BiometricPipeline.shared().extract_from_video(
                request.face_video, n_frames=n_frames,
            )
        else:
            image = decode_image_bytes(request.face_image)
            features = BiometricPipeline.shared().extract(image)
    except ValueError as e:
        _ACCESS_CONTROLLER.record_failure(user_id)
        return SecurityVerdict(decision="deny", user_id=user_id, reason=str(e))
    except Exception as e:
        logger.exception(f"login_user: pipeline biométrico falló: {e}")
        return SecurityVerdict(decision="deny", user_id=user_id,
                               reason=f"Error procesando la imagen: {type(e).__name__}")

    if not features.is_live:
        _ACCESS_CONTROLLER.record_failure(user_id)
        _maybe_notify_login(user_id, success=False, reason="liveness",
                            liveness=features.liveness_score)
        return SecurityVerdict(
            decision="deny", user_id=user_id,
            reason="Liveness insuficiente: posible ataque de presentación.",
            liveness_score=features.liveness_score,
        )

    # 4b. Anti-spoofing (E3 Fase 2)
    antispoof_threshold = getattr(settings, "security_antispoof_threshold", 0.55)
    if (getattr(settings, "security_antispoof_enabled", False)
            and features.antispoof_score < antispoof_threshold):
        _ACCESS_CONTROLLER.record_failure(user_id)
        logger.warning(
            f"login_user: anti-spoofing bloqueó el login "
            f"(score={features.antispoof_score:.3f} < threshold={antispoof_threshold})"
        )
        _maybe_notify_login(user_id, success=False, reason="antispoof",
                            liveness=features.liveness_score)
        return SecurityVerdict(
            decision="deny", user_id=user_id,
            reason="Anti-spoofing: la imagen no parece un rostro real.",
            liveness_score=features.liveness_score,
        )

    # 4c. Challenge-response (E3 Fase 3)
    if getattr(settings, "security_challenges_enabled", False) and not features.challenge_passed:
        _ACCESS_CONTROLLER.record_failure(user_id)
        logger.warning(f"login_user: challenge_response bloqueó el login (no parpadeo detectado)")
        _maybe_notify_login(user_id, success=False, reason="challenge",
                            liveness=features.liveness_score)
        return SecurityVerdict(
            decision="deny", user_id=user_id,
            reason="Challenge fallido: No se detectó parpadeo en el vídeo.",
            liveness_score=features.liveness_score,
        )

    # 5. Comparación de embeddings
    stored = _get_embedding_store().retrieve(user_id)
    if stored is None:
        _ACCESS_CONTROLLER.record_failure(user_id)
        return SecurityVerdict(
            decision="deny", user_id=user_id,
            reason="Embedding biométrico no encontrado para este usuario.",
        )

    similarity = BiometricPipeline.cosine_similarity(stored, features.embedding)
    if similarity < sim_threshold:
        _ACCESS_CONTROLLER.record_failure(user_id)
        _maybe_notify_login(user_id, success=False, reason="face_mismatch",
                            similarity=similarity, liveness=features.liveness_score)
        return SecurityVerdict(
            decision="deny", user_id=user_id,
            reason=f"El rostro no coincide (sim={similarity:.3f} < {sim_threshold:.3f}).",
            similarity=similarity, liveness_score=features.liveness_score,
        )

    # 6. Éxito
    _ACCESS_CONTROLLER.record_success(user_id)
    _maybe_notify_login(user_id, success=True,
                        similarity=similarity, liveness=features.liveness_score)
    return SecurityVerdict(
        decision="allow", user_id=user_id,
        reason=f"Acceso concedido. sim={similarity:.3f} liveness={features.liveness_score:.2f}",
        similarity=similarity, liveness_score=features.liveness_score,
    )


def _maybe_notify_login(user_id: str, *, success: bool, reason: str = "",
                        similarity: Optional[float] = None,
                        liveness: Optional[float] = None) -> None:
    """Dispara la notificación de login. Loguea el resultado por canal.

    El resultado de `notify_login` (dict por canal) se loguea siempre para que
    podamos diagnosticar en producción por qué un mensaje no llega.
    """
    logger.info(f"_maybe_notify_login: user_id={user_id} success={success}")
    cfg = _get_notif_config(user_id)
    if cfg is None:
        logger.info(f"_maybe_notify_login: cfg=None, no se notifica a {user_id}")
        return
    if not cfg.notifications_enabled:
        logger.info(f"_maybe_notify_login: notifications_enabled=False para {user_id}")
        return
    if not cfg.telegram_chat_id:
        logger.info(f"_maybe_notify_login: sin telegram_chat_id para {user_id}")
        return
    try:
        result = notify_login(
            cfg, success=success, similarity=similarity, liveness=liveness,
            message=("Acceso concedido" if success else f"Fallo: {reason}"),
        )
        logger.info(f"_maybe_notify_login result: {result}")
    except Exception as e:
        logger.warning(f"notify_login falló: {e}")


def login_admin(email: str, passphrase: str) -> SecurityVerdict:
    """Login alternativo para cuentas operacionales (`users.is_admin=True`).

    Salta el pipeline biométrico. Sigue aplicando lockout y check bcrypt.
    Rechaza con el mismo mensaje genérico que `login_user` ante credenciales
    incorrectas o un usuario que NO sea admin, para no filtrar qué emails
    están registrados ni cuáles tienen privilegios.
    """
    try:
        from src.data.database import get_session, is_database_configured
        from src.data.schema import User
    except Exception as e:
        return SecurityVerdict(decision="deny", reason=f"BD no disponible: {e}")

    if not is_database_configured():
        return SecurityVerdict(decision="deny", reason="BD no configurada.")

    user_id: Optional[str] = None
    pass_hash: Optional[str] = None
    is_admin = False
    try:
        with get_session() as session:
            user = session.execute(
                select(User).where(User.email == email)
            ).scalar_one_or_none()
            if user is not None:
                user_id = str(user.id)
                pass_hash = user.passphrase_hash
                is_admin = bool(user.is_admin)
    except Exception as e:
        logger.warning(f"login_admin lookup falló: {e}")

    # Mensaje único para no distinguir "no existe" / "no es admin" / "pwd mala".
    generic_deny = SecurityVerdict(decision="deny", reason="Credenciales incorrectas.")

    if user_id is None or pass_hash is None or not is_admin:
        return generic_deny

    if _ACCESS_CONTROLLER.is_locked(user_id):
        return SecurityVerdict(
            decision="deny", user_id=user_id,
            reason="Cuenta bloqueada temporalmente por múltiples intentos fallidos.",
        )

    try:
        ok = bcrypt.checkpw(passphrase.encode(), pass_hash.encode())
    except (ValueError, TypeError):
        ok = False
    if not ok:
        _ACCESS_CONTROLLER.record_failure(user_id)
        return SecurityVerdict(decision="deny", user_id=user_id,
                               reason="Credenciales incorrectas.")

    _ACCESS_CONTROLLER.record_success(user_id)
    logger.info(f"login_admin OK: {user_id} ({email})")
    return SecurityVerdict(
        decision="allow", user_id=user_id,
        reason="Admin login OK (sin biometría).",
    )
