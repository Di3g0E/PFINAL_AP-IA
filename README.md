---
title: PFINAL AP-IA Backend
emoji: 🤖
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: Sistema multiagente financiero (FastAPI + LangGraph)
---

# PFINAL_AP-IA — Sistema unificado multiagente de gestión financiera personal

Continuación de **P6_AP-IA** desplegada íntegramente en infraestructura gratuita: backend en HuggingFace Spaces, base de datos en Supabase y frontend en Vercel. Integra las funcionalidades de P1-P5 bajo un único sistema controlado por agentes con autenticación biométrica, soporte multiusuario y persistencia en Postgres.

## Descripción del sistema

Grafo jerárquico de LangGraph:

- **Agente Orquestador** (LLM): único interlocutor con el usuario. Delega en sub-agentes y narra resultados.
- **Agente Security** (determinista, P5): biometría facial, cifrado de embeddings, control de intentos, anti-anomalías.
- **Agente Registrar** (determinista, P2 + P3): alta de transacciones manuales o vía OCR, categorización automática.
- **Agente Analyst** (determinista + LLM opcional, P1 + P4): analytics, tendencias, predicción temporal, evaluación de objetivos.

Cada usuario accede mediante login (email + passphrase) y verificación biométrica (foto). Los datos financieros se guardan en Postgres con TDE del proveedor; los embeddings biométricos se cifran a nivel aplicación con Fernet (AES-128) + PBKDF2-HMAC-SHA256.

## Reutilización de prácticas anteriores

| De | Qué se reutiliza | A dónde |
|---|---|---|
| **P1** | Predictores temporales (RF, HistGradBoosting, ARIMA) | `src/agents/analyst/forecasters.py` |
| **P2** | `FinancialClassifier` (SGDClassifier + char n-grams) | `src/agents/registrar/classifier.py` |
| **P3** | Motor OCR (PaddleOCR + GB scoring) | `src/agents/registrar/ocr_engine.py` |
| **P4** | Esqueleto LangGraph + analytics | `src/agents/orchestrator/graph.py`, `src/agents/analyst/analytics.py` |
| **P5** | Cifrado, biometría, lockout, notificaciones, anomalías | `src/utils/security.py`, `src/utils/notifications.py`, `src/agents/security/` |

## Stack técnico

```
Frontend:    Next.js 14 (App Router)  → Vercel free
Backend:     FastAPI + LangGraph + uvicorn (Docker)  → HuggingFace Spaces free
LLM:         Groq por defecto + plug-in OpenAI / Anthropic / Google AI Studio por usuario
Base datos:  Postgres 15  → Supabase free (Connection pooling, puerto 6543)
Memoria:     LangGraph PostgresSaver (thread_id = user:session)
Logs:        JSON estructurado (stdout + tabla events)
```

## Estructura del proyecto

```text
PFINAL_AP-IA/
├── src/
│   ├── agents/
│   │   ├── orchestrator/    # Grafo LangGraph + LLM router + narración
│   │   ├── security/        # Biometría, cifrado, lockout, anomalías
│   │   ├── registrar/       # OCR (P3) + clasificador (P2)
│   │   ├── analyst/         # Analytics (P4) + predicción (P1)
│   │   └── contracts.py     # Dataclasses Pydantic compartidas
│   ├── api/
│   │   ├── main.py          # FastAPI app
│   │   └── routers/         # auth, chat, transactions, settings, me (RGPD)
│   ├── data/
│   │   ├── schema.py        # Modelos SQLAlchemy
│   │   └── database.py      # Engine + factory de sesiones
│   └── utils/
│       ├── security.py      # Fernet + PBKDF2 + lockout (de P5)
│       ├── notifications.py # Telegram + WhatsApp multiusuario
│       ├── logging_config.py
│       └── config.py
├── models/                  # Binarios entrenados (van dentro de la imagen Docker)
├── data/                    # Datasets — solo dev local
├── frontend/                # Next.js (Vercel)
├── doc/, docs/, references/, playground/
├── tests/
├── Dockerfile               # Optimizado para HF Spaces (UID 1000, puerto 7860)
├── docker-compose.yml       # Stack local opcional (Postgres + API)
├── .env.example
├── requirements.txt
└── main.py                  # Demo CLI
```

---

## Despliegue en producción (free stack)

Tres servicios, cero coste, sin ngrok ni backend local.

### 1. Base de datos — Supabase

1. Crea cuenta en [Supabase](https://supabase.com) → **New project**.
2. **Connect** → **Connection pooling** (puerto `6543`, modo *Transaction*).
3. Copia la URI y prepárala con el prefijo `+psycopg`:
   ```
   postgresql+psycopg://postgres.<ref>:<PASSWORD>@aws-1-<region>.pooler.supabase.com:6543/postgres
   ```
4. Desde tu máquina local (con `.venv` activado y `.env` apuntando a esa URL) inicializa las tablas y, si quieres, datos demo:
   ```bash
   .venv/Scripts/python.exe scripts/init_db.py
   ```

### 2. Backend — HuggingFace Spaces (Docker SDK)

1. Crea cuenta en [HuggingFace](https://huggingface.co) → **New Space**.
2. **Space SDK = Docker**, **Hardware = CPU basic (free)**, **Visibility = Public**.
3. Sube el contenido de `PFINAL_AP-IA/` al repo del Space (`git push` al remote, `huggingface-cli upload`, o subida por UI).
4. En **Settings → Variables and secrets**, añade (Secret para todo lo sensible, Variable para el resto):
   - `DATABASE_URL` *(secret)* — la URI de Supabase del paso 1.
   - `MASTER_FERNET_KEY` *(secret)* — generar UNA sola vez con:
     ```bash
     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```
   - `EMBEDDING_STORE_PASSPHRASE` *(secret)*
   - `JWT_SECRET` *(secret)*
   - `GROQ_API_KEY` *(secret)* — desde [console.groq.com](https://console.groq.com)
   - `TELEGRAM_BOT_TOKEN` *(secret)* — opcional, desde @BotFather
   - `CORS_ORIGINS` *(variable)* — `https://<tu-proyecto>.vercel.app`
   - `LIVENESS_THRESHOLD`, `FACE_SIMILARITY_THRESHOLD`, `GROQ_DEFAULT_MODEL` *(variable)* — opcionales.
5. El Space empieza a buildear (10-15 min la primera vez por Torch + PaddleOCR). Cuando aparezca "Running", el endpoint público será:
   ```
   https://<usuario>-<nombre-space>.hf.space
   ```
6. Comprueba healthcheck:
   ```bash
   curl https://<usuario>-<nombre-space>.hf.space/
   ```

### 3. Frontend — Vercel

1. Sube el repo a GitHub e importa en [Vercel](https://vercel.com) → **Import Project**.
2. **Root Directory** = `frontend/`.
3. Framework Preset = **Next.js** (autodetect).
4. **Environment Variables**:
   - `NEXT_PUBLIC_API_BASE_URL` = `https://<usuario>-<nombre-space>.hf.space`
5. **Deploy**. URL final: `https://<tu-proyecto>.vercel.app`.
6. **Importante**: vuelve al Space y verifica que `CORS_ORIGINS` incluye exactamente ese dominio (sin barra final).

### 4. Post-despliegue

- **Cargar datos demo en tu cuenta real**:
  ```bash
  .venv/Scripts/python.exe scripts/transfer_demo_data.py <tu_email>
  ```
- **Probar**: abre la URL de Vercel, regístrate (webcam + passphrase) y prueba `/chat`. Las anomalías aparecerán en `/pending`.

---

## Desarrollo local

### Opción A — uv (rápido, sin Docker)

Usa **SQLite** por defecto.

1. `uv venv .venv --python 3.12`
2. `uv pip install -r requirements.txt`
3. `cp .env.example .env` (configura `GROQ_API_KEY` y `MASTER_FERNET_KEY`).
4. `.venv/Scripts/python.exe scripts/init_db.py`
5. CLI: `.venv/Scripts/python.exe main.py`
6. API: `.venv/Scripts/python.exe -m uvicorn src.api.main:app --reload --port 8000`

### Opción B - ngrok

```bash
.venv\Scripts\python.exe -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
# en otra terminal:
ngrok http --domain=aware-uncoiled-raffle.ngrok-free.dev 8000
```

---

## Endpoints v1

| Método | Path | Auth | Función |
|---|---|---|---|
| GET | `/` | — | Healthcheck |
| POST | `/auth/register` | — | Multipart con foto + email + passphrase + consent → JWT |
| POST | `/auth/login` | — | Multipart con foto + credenciales → JWT |
| POST | `/chat` | Bearer | Mensaje al orquestador |
| POST | `/transactions` | Bearer | Alta manual |
| GET | `/transactions/pending` | Bearer | Transacciones marcadas para revisión |
| POST | `/transactions/pending/{id}/confirm` | Bearer | Aprobar |
| DELETE | `/transactions/pending/{id}` | Bearer | Rechazar |

Documentación interactiva: `https://<usuario>-<nombre-space>.hf.space/docs` (Swagger UI).

## Ejemplos de interacción

### 📊 Agente Analyst
- "Resume mis gastos del último mes por categoría."
- "¿Cuál es la predicción de mis gastos para el próximo mes?"
- "¿Cuánto he gastado en ocio (Leisure) en los últimos 6 meses?"
- "Analiza la tendencia de mis gastos de este año."

### 📝 Agente Registrar
- "Añade un gasto de 15€ en transporte hoy."
- "Ayer me gasté 2500€ en una cena en La Tagliatella."
- "He pagado 20€ de parking esta mañana."

*Al introducir un importe inusualmente alto, el agente Security detecta la anomalía y marca la transacción para revisión en `/pending`.*

### 🤖 General
- "Hola, ¿qué puedes hacer por mí?"
- "¿Tengo alguna transacción pendiente de revisar?"
- "Dime el estado de mis ahorros."

## Notificaciones

- **Telegram (primario)**: `TELEGRAM_BOT_TOKEN` del bot vive en secrets del Space. Cada usuario añade su `chat_id`.
- **WhatsApp**: NO funciona en HF Spaces (pywhatkit necesita WhatsApp Web interactivo). Sólo disponible en deploy local.

| Evento | Origen |
|---|---|
| Alta de usuario | Security |
| Login (éxito / fallo) | Security |
| Anomalía financiera detectada | Security |
| Objetivo de gasto > 80 % | Analyst |

## Cumplimiento RGPD

- Consentimiento biométrico explícito al registro.
- `DELETE /me` purga embedding + transacciones + objetivos + eventos.
- `GET /me/export` genera ZIP con todos los datos del usuario.
- Logs sin PII en claro; tabla `events` solo guarda IDs/hashes.
- TLS por defecto (HF Spaces + Vercel).

## Autores

- Diego Esclarín
- Sofía Contreras

*Curso 2025-26 — Grado en Ingeniería en Inteligencia Artificial — URJC*
