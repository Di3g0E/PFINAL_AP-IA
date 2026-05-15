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

Continuación de **P6_AP-IA** que integra P1-P5 bajo un único sistema multiagente con detección de intención semántica, persistencia conversacional, visualización dinámica con XAI, observabilidad con Langfuse + agente Monitor propio, y arquitectura orientada a microservicios. Stack productivo: **backend local (uvicorn) + ngrok + Supabase (Postgres) + Vercel (frontend)**.

## Descripción del sistema

Grafo LangGraph con **cinco agentes**:

- **Agente Orquestador** (LLM, ASPECCT): router con structured-output decide a quién delegar, narrador adapta el tono al perfil del usuario.
- **Agente Conversational** (LLM, ASPECCT): small-talk, saludos, preguntas sobre el sistema. Distinto del orquestador técnico.
- **Agente Analyst** (P1 + P4): analytics (resumen, tendencias, categorías, ahorro, anomalías, recurrentes, objetivos) + predicciones temporales (RF / HistGradBoosting / ARIMA).
- **Agente Registrar** (P2 + P3): alta de transacciones manuales o vía OCR + clasificación automática de categoría.
- **Agente Security** (P5): biometría facial, cifrado de embeddings, anti-anomalía financiera.
- **Agente Monitor**: evalúa la salud del sistema (error rate, p50/p95 de latencia, sesiones activas) en ventanas móviles. Background task que se ejecuta cada 5 min.

Cada usuario accede mediante login (email + passphrase) y verificación biométrica (foto). Los datos financieros se guardan en Postgres con TDE del proveedor; los embeddings biométricos se cifran a nivel aplicación con Fernet (AES-128) + PBKDF2-HMAC-SHA256.

## Capacidades del sistema (los 9 puntos del enunciado)

| # | Capacidad | Implementación |
|---|---|---|
| 1 | Histórico conversacional disponible para el LLM | Tabla `chat_messages` + resumen rolling cada 8 turnos |
| 2 | Recuperación de chats previos | Tabla `chat_sessions`, sidebar en `/chat`, rehidratación vía `localStorage.current_session_id` |
| 3 | Diagramas dinámicos + XAI | `ChatResponse.chart` (Recharts) con explicación XAI; el usuario pide tipo ("en barras", "sin gráfico") y el router lo respeta vía `chart_type` |
| 4 | Agente Monitor automático | `MonitorAgent` evalúa `events` cada 5 min; `GET /monitor/health-detailed` + panel `/admin/monitor` |
| 5 | ≥2 roles diferenciados | 5 agentes en grafo + perfil de usuario `basic`/`advanced` (modifica tono); badge coloreado por agente en cada respuesta |
| 6 | P1-P5 como microservicios REST | Routers `/modules/p1..p5/*` + tools LangChain que los consumen vía httpx loopback |
| 7 | Detección de intención semántica | Router LLM con structured-output (`OrchestratorDecision`); 6 acciones posibles |
| 8 | Langfuse | Trazas por turno con `session_id`/`user_id`/`agent`/`action`; degrada elegante sin claves |
| 9 | Prompts ASPECCT | Router/Narrator/Conversational/Summary con cabeceras `[A][S][P][E][C][C][T]` (ver `doc/prompts_aspect.md`) |

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
Frontend:     Next.js 14 (App Router) + Recharts (charts XAI)  → Vercel free
Backend:      FastAPI + LangGraph + uvicorn (local)  → expuesto vía ngrok
LLM:          Groq por defecto + plug-in OpenAI / Anthropic / Google AI Studio por usuario
Base datos:   Postgres 15  → Supabase free (Connection pooling, puerto 6543)
Memoria chat: Tabla `chat_messages` (rolling 20 msgs) + `chat_sessions.summary`
Tools REST:   httpx loopback al propio uvicorn (cada agente consume P1-P5 vía /modules/*)
Logs:         JSON estructurado (stdout + tabla `events`)
Observability: Langfuse v4 (trazas humanas) + MonitorAgent (agregados internos)
```

## Estructura del proyecto

```text
PFINAL_AP-IA/
├── src/
│   ├── agents/
│   │   ├── orchestrator/    # Grafo LangGraph + router/narrator/conversational
│   │   ├── analyst/         # Analytics (P4) + predicción (P1)
│   │   ├── registrar/       # OCR (P3) + clasificador (P2)
│   │   ├── security/        # Biometría, cifrado, anti-anomalías (P5)
│   │   ├── monitor/         # MonitorAgent — evalúa events agregados
│   │   ├── tools/           # Tools LangChain que llaman a /modules/* vía httpx
│   │   └── contracts.py     # Modelos Pydantic compartidos
│   ├── api/
│   │   ├── main.py          # FastAPI app + lifespan + monitor_loop
│   │   └── routers/
│   │       ├── auth.py      # /auth/register, /auth/login (biometría)
│   │       ├── chat.py      # /chat + CRUD /chat/sessions
│   │       ├── transactions.py
│   │       ├── settings.py  # /api/user/role, /telegram-status, /test-notification
│   │       ├── monitor.py   # /monitor/health-detailed
│   │       └── modules/     # P1-P5 expuestos como microservicios REST
│   ├── data/
│   │   ├── schema.py        # SQLAlchemy: User, ChatSession, ChatMessage, Event…
│   │   └── database.py      # Engine + migraciones ligeras
│   └── utils/
│       ├── config.py
│       ├── logging_config.py        # log_event + Stopwatch (persiste en events)
│       ├── langfuse_integration.py  # start_observation context manager
│       ├── notifications.py         # Telegram (IPv4-forced, reintentos)
│       └── security.py              # Fernet + PBKDF2
├── frontend/                # Next.js + Recharts (Vercel)
│   └── src/app/
│       ├── chat/            # Sidebar histórico, badges, ChatChart inline
│       ├── settings/        # Selector rol + Telegram + diagnóstico
│       ├── pending/         # Cola de revisiones anómalas
│       └── admin/monitor/   # Dashboard del MonitorAgent
├── models/                  # Binarios entrenados (Git LFS)
├── data/                    # Datasets — solo dev local
├── doc/                     # MEMORIA, prompts_aspect.md, agent_contracts.md
├── tests/                   # 72 tests pytest (conftest.py para tools)
├── scripts/
│   ├── init_db.py           # Crea esquema en Supabase
│   └── test_langfuse.py     # Smoke test de las API keys de Langfuse
├── Dockerfile               # Opcional (HF Spaces / Render / Fly)
├── docker-compose.yml       # Opcional (Postgres local)
├── .env.example             # Plantilla de variables
└── requirements.txt
```

---

## Setup desde cero — desarrollo local + Vercel

Arquitectura del setup actual: backend en tu máquina (uvicorn), expuesto a internet vía **ngrok** con dominio estático, BD en **Supabase** (gratis), frontend en **Vercel** (gratis).

```
[Browser] → [Vercel (Next.js)] → [ngrok tunnel] → [uvicorn local:8000]
                                                        ↓ httpx loopback
                                                  [/modules/p1-p5/*]
                                                        ↓
                                                   [Supabase Postgres]
```

### 1. Requisitos previos

- Python 3.12 (recomendado vía [uv](https://github.com/astral-sh/uv))
- Node 18+ y npm (para el frontend)
- Cuenta gratuita en [Supabase](https://supabase.com), [Vercel](https://vercel.com), [Groq](https://console.groq.com) y [ngrok](https://ngrok.com)
- (Opcional) cuenta en [Langfuse Cloud](https://cloud.langfuse.com) para observabilidad
- (Opcional) bot de Telegram via @BotFather para notificaciones

### 2. Base de datos — Supabase

1. Crea proyecto en Supabase → **Connect** → **Connection pooling** (puerto `6543`, modo *Transaction*).
2. La URI se prepara con prefijo `+psycopg`:
   ```
   postgresql+psycopg://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```

### 3. Backend local

```bash
# 1) Clonar repo y crear venv
git clone https://github.com/Di3g0E/PFINAL_AP-IA.git
cd PFINAL_AP-IA
uv venv .venv --python 3.12
uv pip install -r requirements.txt

# 2) Configurar variables de entorno
cp .env.example .env
# Edita .env con tu editor favorito:
#  - DATABASE_URL = (la URI de Supabase del paso 2)
#  - MASTER_FERNET_KEY = (generar nueva, ver abajo)
#  - EMBEDDING_STORE_PASSPHRASE = (cualquier string ≥32 chars)
#  - JWT_SECRET = (cualquier string aleatorio)
#  - GROQ_API_KEY = (desde console.groq.com)
#  - CORS_ORIGINS = http://localhost:3000   (añade el dominio de Vercel cuando lo tengas)
#  - LANGFUSE_PUBLIC_KEY/SECRET_KEY/BASE_URL = (opcional, ver paso 6)
#  - TELEGRAM_BOT_TOKEN = (opcional)

# Generar MASTER_FERNET_KEY:
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 3) Inicializar tablas en Supabase (crea esquema + usuario demo + datos CSV)
.venv/Scripts/python.exe scripts/init_db.py

# 4) Arrancar el backend (Terminal 1)
.venv/Scripts/python.exe -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Comprueba con `curl http://127.0.0.1:8000/` → debe devolver `{"name":"P6_AP-IA",...}`.

> ⚠️ En Windows usa `127.0.0.1` en lugar de `localhost`: localhost resuelve a `::1` (IPv6) por defecto y uvicorn solo escucha IPv4 (`0.0.0.0`).

### 4. Túnel público con ngrok

1. Crea cuenta gratis en ngrok y configura tu authtoken: `ngrok config add-authtoken <TOKEN>`.
2. En el dashboard de ngrok → **Domains → New Domain** → te da un dominio estático tipo `mi-dominio.ngrok-free.dev` que **NO cambia entre reinicios** (clave para no tocar Vercel cada vez).
3. Arranca el túnel (Terminal 2):
   ```bash
   ngrok http --domain=mi-dominio.ngrok-free.dev 8000
   ```
4. Verifica: `curl https://mi-dominio.ngrok-free.dev/` debe devolver el mismo JSON.

### 5. Frontend — Vercel

```bash
# Para desarrollo local (recomendado al iterar):
cd frontend
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000" > .env.local
npm run dev
# Abre http://localhost:3000
```

Para deploy en Vercel:
1. Push del repo a GitHub.
2. Vercel → **Import Project** → selecciona el repo → **Root Directory** = `frontend/`.
3. **Environment Variables**: `NEXT_PUBLIC_API_BASE_URL` = `https://mi-dominio.ngrok-free.dev`.
4. **Deploy**. URL final: `https://<proyecto>.vercel.app`.
5. **Importante**: vuelve a tu `.env` local y añade ese dominio a `CORS_ORIGINS` (sin barra final), separado por coma del localhost si lo tenías. Reinicia uvicorn para que lo coja.

### 6. (Opcional) Langfuse para observabilidad

1. Crea cuenta en [cloud.langfuse.com](https://cloud.langfuse.com), nuevo proyecto, copia las API keys.
2. Añade al `.env`:
   ```
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_BASE_URL=https://cloud.langfuse.com
   ```
3. Reinicia uvicorn. En los logs verás `Langfuse inicializado correctamente.`
4. Smoke test: `.venv/Scripts/python.exe scripts/test_langfuse.py` — debe crear una trace de prueba.
5. Tras enviar mensajes en el chat, cada llamada al LLM aparecerá en Langfuse Cloud agrupada por `session_id`.

### 7. Workflow diario

Cada vez que abras el proyecto:

```bash
# Terminal 1: backend (auto-reload con --reload)
.venv\Scripts\python.exe -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: túnel ngrok (URL estable)
ngrok http --domain=mi-dominio.ngrok-free.dev 8000

# (Opcional) Terminal 3: frontend con hot reload
cd frontend && npm run dev
```

Cuando termines: Ctrl+C en cada terminal. Editar `.env` requiere **reiniciar uvicorn manualmente** (`--reload` solo vigila código Python).

---

## Tests

72 tests con pytest. Mocks de LLM (sin Groq real), SQLite efímero por test, y un `conftest.py` que redirige `http_client.*` a las funciones in-process equivalentes (los routers REST no necesitan estar levantados):

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
# 72 passed in ~25s
```

Cobertura por área:
- `test_orchestrator_routing.py` — 8 tests del router LLM + analyst dispatch
- `test_pending_review_flow.py` — 9 tests de la cola de revisión
- `test_registrar_smoke.py` — 7 tests del clasificador + OCR
- `test_security_smoke.py` — 9 tests anti-anomalía
- `test_security_biometrics.py` — 9 tests biometría facial
- `test_analyst_smoke.py` — 8 tests de cada operación analítica
- `test_db_integration.py` — 6 tests de persistencia
- `test_api_endpoints.py` — 16 tests de los endpoints REST

---

## Endpoints REST

Documentación interactiva en `http://127.0.0.1:8000/docs` (Swagger UI con autenticación Bearer).

### Autenticación
| Método | Path | Auth | Función |
|---|---|---|---|
| GET | `/` | — | Healthcheck |
| POST | `/auth/register` | — | Multipart con foto + email + passphrase + consent → JWT |
| POST | `/auth/login` | — | Multipart con foto + credenciales → JWT |

### Chat + sesiones persistentes
| Método | Path | Función |
|---|---|---|
| POST | `/chat` | Mensaje al orquestador (devuelve respuesta + `chart` opcional + `last_action`) |
| GET | `/chat/sessions` | Lista las sesiones del usuario |
| POST | `/chat/sessions` | Crea sesión vacía |
| GET | `/chat/sessions/{id}` | Sesión + últimos K mensajes + summary |
| PATCH | `/chat/sessions/{id}` | Rename / archive |
| DELETE | `/chat/sessions/{id}` | Borra sesión (cascada a mensajes) |

### Transacciones
| Método | Path | Función |
|---|---|---|
| POST | `/transactions` | Alta manual |
| POST | `/transactions/ocr-extract` | OCR de imagen → borrador (sin persistir) |
| GET | `/transactions/pending` | Transacciones marcadas para revisión |
| POST | `/transactions/pending/{id}/confirm` | Aprobar |
| DELETE | `/transactions/pending/{id}` | Rechazar |

### Microservicios P1-P5 (Fase 2)
| Método | Path | Función |
|---|---|---|
| POST | `/modules/p1/predict-next-month` | Predicción (RF / HGB / ARIMA) |
| POST | `/modules/p2/classify-area` | Clasificador SGDC + char n-grams |
| POST | `/modules/p3/ocr-extract` | OCR PaddleOCR multipart |
| POST | `/modules/p4/{monthly-summary, category-breakdown, spending-trends, savings-rate, detect-anomalies, recurring-expenses, recent-transactions, check-goals, goals}` | Analytics |
| GET / DELETE | `/modules/p4/goals[/{area}]` | CRUD objetivos |
| POST | `/modules/p5/validate-transaction` | Anti-anomalía |
| GET | `/modules/p5/lockout-status` | Estado del lockout del usuario |

### Settings, monitor, usuario
| Método | Path | Función |
|---|---|---|
| GET / PUT | `/api/user/settings` | Notificaciones Telegram |
| GET / PUT | `/api/user/role` | Perfil del usuario (`basic` \| `advanced`) |
| POST | `/api/user/test-notification` | Envía notificación de prueba |
| GET | `/api/user/telegram-status` | Diagnóstico del bot (verifica token + chat_id) |
| GET | `/monitor/health-detailed?window_minutes=60` | Snapshot del MonitorAgent |

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

### 📈 Visualización dinámica (Punto 3)

El usuario puede crear, modificar o eliminar diagramas durante la conversación:

- "Tendencia de gastos de los últimos 6 meses" → línea con histórico mensual
- "Muéstralo como gráfico de barras" → mismo dato, otro tipo
- "Como un pie" → ahora pie chart
- "Sin gráfico" → solo texto, sin diagrama

Cada gráfico va acompañado de una **explicación XAI** colapsable que enuncia QUÉ representa y destaca el dato clave (tendencia al alza/baja, categoría dominante, etc.).

### 🤖 Conversational (Punto 5)

- "Hola, ¿qué puedes hacer por mí?" → badge 💬 Conversational
- "Gracias, eso es todo" → badge 💬 Conversational
- "¿Cuánto he gastado este mes?" → badge 📊 Analyst (cambia el agente automáticamente)

### 👤 Perfil del usuario (Punto 5)

En `/settings` el usuario elige entre:

- 🌱 **Básico**: frases cortas, lenguaje cotidiano, cifras redondeadas, sin tecnicismos.
- 🎓 **Avanzado**: 3-4 frases, decimales, porcentajes, términos financieros (tasa de ahorro, percentiles, varianza).

El narrador adapta el tono en cada respuesta sin necesidad de reformular la pregunta.

### 📊 Panel del Monitor (Punto 4)

En `/admin/monitor` (auth required) el usuario ve un dashboard en vivo del estado del sistema:

- Total de eventos · tasa de error · sesiones activas · usuarios activos
- Badge de health (`ok` / `warning` / `critical`)
- Bar chart p50/p95 por (agente, acción) — top 10
- Top 5 errores
- Auto-refresca cada 30 s · selector de ventana (15 min / 1h / 6h / 24h)

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
