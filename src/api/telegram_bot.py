"""
Servicio simple para que el bot de Telegram responda /start y muestre el Chat ID.
"""

import os
import json
import logging
from typing import Dict, Any

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

class TelegramBotService:
    """Servicio básico para manejar comandos del bot de Telegram."""
    
    def __init__(self, token: str):
        self.token = token
        self.api_url = f"https://api.telegram.org/bot{token}"
    
    def send_message(self, chat_id: str, text: str) -> bool:
        """Envía un mensaje a un chat de Telegram."""
        try:
            url = f"{self.api_url}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown"
            }
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Mensaje enviado a chat {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Error enviando mensaje a {chat_id}: {e}")
            return False
    
    def handle_start_command(self, chat_id: str, first_name: str = None) -> bool:
        """Maneja el comando /start y muestra el Chat ID del usuario."""
        welcome_text = f"""👋 *¡Bienvenido a P6 Security Bot!*

Tu *Chat ID* es: `{chat_id}`

📋 *Pasos para configurar notificaciones:*
1. Copia este Chat ID: `{chat_id}`
2. Ve a la configuración de la aplicación
3. Activa "Notificaciones de seguridad"
4. Pega tu Chat ID en el campo correspondiente
5. Guarda y prueba con el botón "Probar"

🔒 *Este bot te enviará alertas de seguridad y financieras importantes.*

{f'¡Hola {first_name}!' if first_name else ''}"""
        
        return self.send_message(chat_id, welcome_text)
    
    def handle_help_command(self, chat_id: str) -> bool:
        """Maneja el comando /help."""
        help_text = """🤖 *Comandos disponibles:*

/start - Muestra tu Chat ID y guía de configuración
/help - Muestra esta ayuda

📱 *Para soporte adicional:*
- Revisa la documentación del sistema
- Contacta al administrador

🔐 *Tu privacidad es importante:*
- Solo recibirás notificaciones si las activas explícitamente
- Puedes desactivarlas en cualquier momento"""
        
        return self.send_message(chat_id, help_text)
    
    def process_update(self, update: Dict[str, Any]) -> bool:
        """Procesa una actualización de Telegram."""
        try:
            # Verificar si es un mensaje
            if "message" in update:
                message = update["message"]
                chat_id = str(message["chat"]["id"])
                
                # Verificar si es un comando
                if "text" in message and message["text"].startswith("/"):
                    command = message["text"].lower()
                    first_name = message.get("from", {}).get("first_name", "")
                    
                    if command == "/start":
                        return self.handle_start_command(chat_id, first_name)
                    elif command == "/help":
                        return self.handle_help_command(chat_id)
                    else:
                        # Comando no reconocido
                        return self.send_message(
                            chat_id, 
                            "❌ Comando no reconocido. Usa /start para obtener tu Chat ID o /help para ayuda."
                        )
            
            return True
        except Exception as e:
            logger.error(f"Error procesando update: {e}")
            return False

# Instancia global del servicio
_bot_service: TelegramBotService = None

def get_bot_service() -> TelegramBotService:
    """Obtiene la instancia del servicio del bot."""
    global _bot_service
    if _bot_service is None:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN no está configurado")
        _bot_service = TelegramBotService(token)
    return _bot_service

def setup_webhook(app: FastAPI) -> bool:
    """Configura el webhook para recibir actualizaciones del bot."""
    try:
        bot_service = get_bot_service()
        
        # Configurar webhook (opcional - puedes usar polling en su lugar)
        webhook_url = "https://tu-dominio.com/telegram-webhook"  # Cambia esto si usas webhook
        
        url = f"{bot_service.api_url}/setWebhook"
        payload = {"url": webhook_url, "drop_pending_updates": True}
        
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        
        logger.info("Webhook configurado correctamente")
        return True
    except Exception as e:
        logger.error(f"Error configurando webhook: {e}")
        return False

# Endpoint para recibir actualizaciones (si usas webhook)
async def telegram_webhook(request: Request) -> JSONResponse:
    """Endpoint para recibir actualizaciones de Telegram."""
    try:
        update = await request.json()
        bot_service = get_bot_service()
        
        success = bot_service.process_update(update)
        
        return JSONResponse({"status": "ok" if success else "error"})
    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail="Error procesando actualización")
