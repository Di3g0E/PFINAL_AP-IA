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

# PFINAL_AP-IA — Sistema multiagente financiero

Asistente financiero conversacional construido sobre **LangGraph** con dos
chats diferenciados según el rol del usuario:

- **Usuario** → chat de gestión financiera (registrar gastos, OCR de
  facturas, resúmenes, predicciones, anomalías…).
- **Admin** → chat de operaciones que pregunta al sistema sobre sí mismo
  (eventos, anomalías de log, uso de tokens del LLM, salud, grafo agéntico…).

El backend es **FastAPI + LangGraph + Groq** (o el LLM que el usuario
configure). El frontend es **Next.js + Recharts**. La BD es **Postgres**
(Supabase) y la observabilidad va a **Langfuse**.

---

## Estructura del proyecto

```text
PFINAL_AP-IA/
├── src/
│   ├── agents/
│   │   ├── orchestrator/   # Grafo LangGraph + router/narrator/conversational
│   │   ├── analyst/        # Analytics (P4) + predicción (P1)
│   │   ├── registrar/      # OCR (P3) + clasificador (P2)
│   │   ├── security/       # Biometría (P5) + anti-anomalías financieras
│   │   ├── monitor/        # MonitorAgent (eventos agregados)
│   │   ├── observability/  # Chat de admin (tools de inspección del sistema)
│   │   └── tools/          # Tools LangChain que consumen /modules/* vía HTTP
│   ├── api/
│   │   ├── main.py
│   │   └── routers/
│   │       ├── auth.py             # /auth/register, /auth/login, /auth/login-admin
│   │       ├── chat.py             # Chat de usuario + sesiones
│   │       ├── admin_chat.py       # Chat de admin (ops)
│   │       ├── user_agent_graph.py # GET /me/agent-graph
│   │       ├── admin_agent_graph.py# GET /admin/agent-graph
│   │       ├── transactions.py
│   │       ├── monitor.py          # GET /monitor/health-detailed
│   │       ├── settings.py
│   │       └── modules/p{1..5}.py  # Microservicios REST
│   ├── data/                       # Schema SQLAlchemy + engine
│   └── utils/
│       ├── agent_graph_builder.py  # Constructor de grafos (user/admin)
│       ├── log_analyzer.py         # Parser de logs/app.log (anomalías)
│       ├── langfuse_integration.py # Trazas + CallbackHandler
│       └── langfuse_fetch.py       # Consultas a Langfuse SaaS
├── frontend/                       # Next.js (Vercel)
│   └── src/app/
│       ├── chat/                   # Chat de usuario
│       ├── graph/                  # /me/agent-graph
│       ├── admin/chat/             # Chat de admin (ops)
│       ├── admin/graph/            # /admin/agent-graph (app + ops)
│       └── admin/monitor/          # KPIs del MonitorAgent
├── tests/
│   ├── test_*.py                   # 21 tests unitarios
│   └── stress/                     # Tests de carga con Locust
├── doc/                            # MEMORIA, prompts_aspect.md, evoluciones.md
├── scripts/                        # init_db, eval, verify_langfuse, etc.
└── requirements.txt
```

---

## Setup desde cero (copiar y pegar)

### Requisitos previos

- Python 3.12 (recomendado [uv](https://github.com/astral-sh/uv))
- Node 18+ y npm
- Cuentas gratuitas en: [Supabase](https://supabase.com) (BD),
  [Groq](https://console.groq.com) (LLM), [ngrok](https://ngrok.com) (túnel)
- Opcional: [Langfuse Cloud](https://cloud.langfuse.com) (observabilidad)

### Pasos

```bash
# 1) Clonar y crear entorno virtual
git clone https://github.com/Di3g0E/PFINAL_AP-IA.git
cd PFINAL_AP-IA
uv venv .venv --python 3.12
uv pip install -r requirements.txt

# 2) Variables de entorno
cp .env.example .env
# Edita .env y rellena:
#   DATABASE_URL=postgresql+psycopg://postgres.<ref>:<PASS>@aws-0-<region>.pooler.supabase.com:6543/postgres
#   MASTER_FERNET_KEY=<la salida del comando de abajo>
#   EMBEDDING_STORE_PASSPHRASE=<cualquier string >=32 chars>
#   JWT_SECRET=<cualquier string aleatorio>
#   GROQ_API_KEY=<desde console.groq.com>
#   CORS_ORIGINS=http://localhost:3000
# Generar MASTER_FERNET_KEY:
.venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 3) Crear tablas en Supabase
.venv/Scripts/python.exe scripts/init_db.py

# 4) (Opcional) Crear un usuario admin para el chat de ops
.venv/Scripts/python.exe scripts/create_admin_user.py admin@local.dev mi-passphrase-segura

# 5) Frontend
cd frontend
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000" > .env.local
cd ..
```

### Arrancar en local (2 terminales)

```bash
# Terminal 1 — backend
.venv/Scripts/python.exe -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — frontend
cd frontend && npm run dev
```

Abre <http://localhost:3000>. Comprueba el backend con
`curl http://127.0.0.1:8000/`.

### Ejecutar repetidamente (workflow diario)

Mismas dos terminales del bloque anterior. Para cerrar: `Ctrl+C` en
cada una. Cambios en `.env` requieren reiniciar uvicorn manualmente
(`--reload` solo vigila código Python).

---

## Funciones disponibles por rol

| Función | Usuario | Admin | Endpoint principal |
|---|:-:|:-:|---|
| Login con biometría facial + passphrase | ✅ | ❌ | `POST /auth/login` |
| Login solo con passphrase | ❌ | ✅ | `POST /auth/login-admin` |
| Chat financiero (transacciones, análisis) | ✅ | ❌ | `POST /chat` |
| Chat de ops (consultar el sistema) | ❌ | ✅ | `POST /admin/chat` |
| Listar / retomar sesiones de chat | ✅ | ✅ | `GET /chat/sessions`, `GET /admin/chat/sessions` |
| Alta manual de transacción | ✅ | ❌ | `POST /transactions` |
| OCR de factura → borrador | ✅ | ❌ | `POST /transactions/ocr-extract` |
| Revisar / confirmar transacciones anómalas | ✅ | ❌ | `GET /transactions/pending` |
| Cambiar rol (basic / advanced) | ✅ | ❌ | `PUT /api/user/role` |
| Configurar LLM propio (Groq/OpenAI/...) | ✅ | ❌ | `PUT /api/user/settings/llm` |
| Notificaciones Telegram | ✅ | ❌ | `PUT /api/user/settings/notifications` |
| Ver MI grafo agéntico personal | ✅ | ✅ | `GET /me/agent-graph` |
| Ver grafo system-wide (app + ops) | ❌ | ✅ | `GET /admin/agent-graph` |
| Snapshot MonitorAgent | ❌ | ✅ | `GET /monitor/health-detailed` |
| Health endpoint público | ✅ | ✅ | `GET /` |

---

## Ejemplos para testear todas las tools de cada grafo

### Usuario — chat financiero

El orquestador delega en **Analyst** (P1+P4), **Registrar** (P2+P3),
**Security** (P5) o **Conversational** según la intención detectada.
Estos prompts cubren cada delegación al menos una vez:

| # | Prompt | Tool que dispara |
|---|---|---|
| 1 | `Hola, ¿qué puedes hacer?` | conversational (small-talk) |
| 2 | `Añade un gasto de 18,50€ en pizza el 15 de abril` | registrar.add_manual_transaction |
| 3 | `Adjunto factura del super` *(con imagen)* | registrar.add_from_image (OCR) |
| 4 | `¿Cuánto he gastado este mes?` | analyst.monthly_summary |
| 5 | `Reparte mis gastos por categoría del último trimestre` | analyst.category_breakdown |
| 6 | `Muéstrame mis gastos mensuales en gráfico de líneas` | analyst.spending_trends (con chart XAI) |
| 7 | `¿Cuánto voy a gastar el mes que viene en comida?` | analyst.predict_next_month |
| 8 | `Detecta movimientos raros en mis transacciones` | analyst.detect_anomalies |
| 9 | `Quiero ahorrar 500€ al mes en ocio` | analyst.set_goal |
| 10 | `Cambia mi perfil a avanzado` *(después: pídele otro resumen)* | role basic → advanced (cambia el tono del narrator) |

> 💡 La spec del chart se devuelve en `ChatResponse.chart`. Pídele
> *"sin gráfico"* o *"en barras"* para verificar la visualización dinámica.

### Admin — chat de ops

El agente Observability tiene **5 tools**, una por cada fuente de
telemetría. Estos prompts las cubren todas:

| # | Prompt | Tool que dispara |
|---|---|---|
| 1 | `¿Cómo está el sistema en la última hora?` | get_system_health |
| 2 | `Muéstrame los últimos 20 errores del registrar` | query_recent_events (filtra por agent+status) |
| 3 | `¿Hay anomalías en los logs de hoy?` | analyze_log_anomalies |
| 4 | `¿Cuánto hemos gastado en tokens de LLM esta semana?` | get_llm_usage (Langfuse) |
| 5 | `¿Cuál es el agente más lento esta semana?` | get_agent_flow_summary |
| 6 | `Resúmeme la salud del sistema y dime si hay algo que mirar` | combina get_system_health + analyze_log_anomalies |

El campo `tools_used` de la respuesta lista qué tools invocó el agente en
ese turno — útil para verificar la auditoría.

---

## Tests

### Tests unitarios (rápidos, sin red)

```bash
.venv/Scripts/python.exe -m pytest -q
# 21 passed
```

Reparto:

| Fichero | Tests | Cubre |
|---|--:|---|
| `test_api_endpoints.py` | 5 | Healthcheck, register/login JWT, chat con LLM mock |
| `test_orchestrator_routing.py` | 3 | Routing del grafo (respond_final / delegate_analyst / ask_user) |
| `test_classifier_hybrid.py` | 3 | Zero-shot + bootstrap del HybridClassifier |
| `test_agent_graph.py` | 5 | Builder de grafos + parser de logs |
| `test_pending_review_flow.py` | 2 | Detección de transacción anómala |
| `test_registrar_smoke.py` | 3 | OCR + clasificador de P2 |

### Tests de carga (Locust)

Necesita el backend levantado en otro terminal:

```bash
# Terminal A — backend
.venv/Scripts/python.exe -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000

# Terminal B — Locust (UI interactiva en http://localhost:8089)
.venv/Scripts/locust.exe -f tests/stress/locustfile.py --host http://localhost:8000

# o modo headless (10 usuarios, 30s):
.venv/Scripts/locust.exe -f tests/stress/locustfile.py --host http://localhost:8000 \
    --users 10 --spawn-rate 2 --run-time 30s --headless
```

Más detalle en [tests/stress/README.md](tests/stress/README.md).

---

## Documentación adicional

- **Memoria del proyecto:** [doc/MEMORIA.md](doc/MEMORIA.md)
- **Evoluciones incrementales (P2, P3, P5):** [doc/evoluciones.md](doc/evoluciones.md)
- **Metodología de prompts (ASPECCT):** [doc/prompts_aspect.md](doc/prompts_aspect.md)
- **API interactiva:** Swagger UI en `http://127.0.0.1:8000/docs`
