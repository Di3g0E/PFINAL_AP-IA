"""
Tests de carga con Locust.

Simulan dos perfiles de usuario hablando con la API en paralelo:
  - ChatUser: usuario "normal" que registra cuenta y hace preguntas al chat.
  - AnalyticsUser: usuario que también usa el chat pero para pedir análisis
    financieros (gasta más tokens en el LLM real).

Uso:
    # 1. Levanta el backend en otra terminal:
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000

    # 2. Lanza Locust contra él:
    locust -f tests/stress/locustfile.py --host http://localhost:8000

    # 3. Abre http://localhost:8089 y configura usuarios + spawn rate.

Para un test rápido sin UI (10 usuarios durante 30s):
    locust -f tests/stress/locustfile.py --host http://localhost:8000 \\
           --users 10 --spawn-rate 2 --run-time 30s --headless

NOTA: necesita que la base de datos esté limpia o que `auth/register`
acepte emails únicos generados por cada locust user (lo hacemos abajo
añadiendo un uuid al email).
"""
from __future__ import annotations

import io
import uuid

from locust import HttpUser, between, task


# PNG 16x16 gris generado con PIL — bytes literales para no añadir
# dependencias al locustfile. Lo bastante real para que el decoder de
# imágenes acepte el archivo y el flujo de registro llegue hasta el
# pipeline biométrico (que sí va a fallar el match, pero los timings
# de la API y la BD ya se han medido).
_FAKE_FACE_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000010000000100802000000909168"
    "360000002349444154789c636c68686020053091a49a61540371808948757030"
    "aa81184072280100c94c01a0729cad8d0000000049454e44ae426082"
)


def _fake_face_bytes() -> bytes:
    """PNG 16x16 válido — pasa el decoder, falla el match biométrico."""
    return _FAKE_FACE_PNG


class _BaseUser(HttpUser):
    """Helpers compartidos entre los perfiles."""

    abstract = True
    wait_time = between(1, 3)
    token: str | None = None

    def on_start(self):
        """Registra una cuenta nueva al arrancar este usuario virtual."""
        email = f"locust-{uuid.uuid4().hex[:8]}@p6.local"
        files = {"face": ("face.png", io.BytesIO(_fake_face_bytes()), "image/png")}
        data = {
            "email": email,
            "passphrase": "stresspass99",
            "biometric_consent": "true",
        }
        with self.client.post("/auth/register", data=data, files=files,
                              catch_response=True, name="POST /auth/register") as r:
            if r.status_code in (200, 201):
                self.token = r.json().get("access_token")
                r.success()
            else:
                # El backend puede tardar en responder bajo carga; lo marcamos
                # como fallo pero el resto de tasks no se ejecutarán.
                r.failure(f"register devolvió {r.status_code}: {r.text[:120]}")

    @property
    def _auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}


class ChatUser(_BaseUser):
    """Usuario que conversa con el chat con mensajes triviales."""

    weight = 3  # 3 ChatUser por cada AnalyticsUser (el caso de uso más común)

    @task(2)
    def healthcheck(self):
        """Endpoint público — siempre exitoso, sirve de baseline de latencia."""
        self.client.get("/", name="GET / (health)")

    @task(3)
    def send_chat_message(self):
        if not self.token:
            return
        self.client.post(
            "/chat",
            headers=self._auth_headers,
            json={"message": "Hola, ¿cómo estás?"},
            name="POST /chat (small-talk)",
        )

    @task(1)
    def list_sessions(self):
        if not self.token:
            return
        self.client.get(
            "/chat/sessions",
            headers=self._auth_headers,
            name="GET /chat/sessions",
        )


class AnalyticsUser(_BaseUser):
    """Usuario que pide análisis financieros (consultas más caras)."""

    weight = 1

    @task
    def ask_for_summary(self):
        if not self.token:
            return
        self.client.post(
            "/chat",
            headers=self._auth_headers,
            json={"message": "Resúmeme mis gastos del último mes"},
            name="POST /chat (analyst)",
        )
