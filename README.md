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
│   │   ├── orchestrator/   # Grafo LangGraph + router/narrador/conversacional
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
├── doc/                            # MEMORIA, prompts_aspect, evoluciones, agent_contracts, TELEGRAM_SETUP, results/
├── scripts/
│   ├── init_db.py                  # Crea tablas + usuario demo + seed CSV
│   ├── create_admin_user.py        # Crea cuenta con `is_admin=True`
│   ├── transfer_demo_data.py       # Copia las transacciones demo a otro user
│   └── eval/                       # Genera los baselines de `docs/results/*.json`
└── requirements.txt
```

---

## Setup desde cero (copiar y pegar)

### Requisitos previos

- Python 3.12 (recomendado [uv](https://github.com/astral-sh/uv))
- Node 18+ y npm
- **[Git LFS](https://git-lfs.com)** — los modelos ML (`models/*.joblib`,
  `models/liveness_kaggle.pth`, ~78 MB) viajan por LFS. Sin él, `git clone`
  baja punteros de 134 bytes y el backend falla al cargar el clasificador.
- Cuentas gratuitas en: [Supabase](https://supabase.com) (BD),
  [Groq](https://console.groq.com) (LLM), [ngrok](https://ngrok.com) (túnel)
- [Langfuse Cloud](https://cloud.langfuse.com) — opcional, pero **muy
  recomendado**: sin `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` el
  backend arranca igual, pero el panel admin no podrá responder a
  *"¿cuánto hemos gastado en tokens?"* (la tool `get_llm_usage` devuelve
  `enabled=False`).

### Pasos

```bash
# 0) Una sola vez por máquina — habilitar Git LFS
git lfs install
# (después al clonar el repo, los modelos se descargan reales)

# 1) Crear entorno virtual
uv venv .venv --python 3.12
uv pip install -r requirements.txt

# 2) Variables de entorno
cp .env.example .env
# Editar .env y rellenar:
#   DATABASE_URL=postgresql+psycopg://postgres.<ref>:<PASS>@aws-0-<region>.pooler.supabase.com:6543/postgres
#   MASTER_FERNET_KEY=<la salida del comando de abajo>
#   EMBEDDING_STORE_PASSPHRASE=<cualquier string >=32 chars>
#   JWT_SECRET=<cualquier string aleatorio>
#   GROQ_API_KEY=<desde console.groq.com>
#   CORS_ORIGINS=http://localhost:3000
# Generar MASTER_FERNET_KEY:
.venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 3) Crear tablas en Supabase + usuario demo + seed CSV
.venv/Scripts/python.exe scripts/init_db.py

# 4) (Opcional) Crear un usuario admin para el chat de ops
.venv/Scripts/python.exe scripts/create_admin_user.py admin@local.dev mi-passphrase-segura

# 5) (Opcional, recomendado) Tras registrarte como usuario en la UI,
#    copia las transacciones del usuario demo a tu cuenta para que el
#    Analyst tenga datos con los que trabajar:
.venv/Scripts/python.exe scripts/transfer_demo_data.py tu-email@example.com

# 6) Frontend
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

Abrir <http://localhost:3000>. Comprobar el backend con
`curl http://127.0.0.1:8000/`.


---

## Despliegue con Vercel + Supabase

Arquitectura del setup en producción:

```
[Navegador] → [Vercel · Next.js] → [ngrok tunnel] → [uvicorn local:8000]
                                                         ↓
                                                 [Supabase · Postgres]
```

Supabase ya hospeda la BD del paso 2 del setup local; añadir **ngrok**
para exponer el backend local a internet y **Vercel** para servir el frontend.

### 1. Túnel público con ngrok

```bash
# Una sola vez: configurar authtoken
ngrok config add-authtoken <TOKEN>

# En el dashboard de ngrok → Domains → New Domain da uno estático
# como `mi-app.ngrok-free.dev` que no cambia entre reinicios.

# Lanzar el túnel (Terminal 3 — además del backend y el frontend)
ngrok http --domain=aware-uncoiled-raffle.ngrok-free.dev 8000
```

Comprobar: `curl https://aware-uncoiled-raffle.ngrok-free.dev/` debe devolver el mismo
JSON que `http://127.0.0.1:8000/`.

### 2. Frontend en Vercel

1. Subir el repo a GitHub.
2. <https://vercel.com> → **Import Project** → seleccionar el repo →
   **Root Directory** = `frontend/`.
3. En **Environment Variables** añadir:

   ```
   NEXT_PUBLIC_API_BASE_URL = https://aware-uncoiled-raffle.ngrok-free.dev
   ```
4. **Deploy**. Vercel devuelve una URL tipo
   `https://pfinal-ap-ia.vercel.app`.

### 3. Actualizar CORS en el backend

Añadir el dominio de Vercel a `CORS_ORIGINS` en el `.env` local
**(separado por coma, sin barra final)** y **reinicia uvicorn**:

```
CORS_ORIGINS=http://localhost:3000,https://pfinal-ap-ia.vercel.app
```

### 4. Workflow diario con Vercel

```bash
# Terminal 1 — backend
.venv/Scripts/python.exe -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — túnel ngrok (URL estable)
ngrok http --domain=aware-uncoiled-raffle.ngrok-free.dev 8000
```

Ya no es necesario `npm run dev`: el frontend lo sirve Vercel. Cualquier
push a `main` redespliega el frontend automáticamente.

> ⚠️ Si se cambia de dominio ngrok, se debe actualizar `NEXT_PUBLIC_API_BASE_URL`
> en Vercel **y** `CORS_ORIGINS` en el `.env` local. Por eso es importante
> tener un dominio estático ngrok.

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
| 5 | `Quiero ahorrar 500€ al mes en ocio` | analyst.set_goal |
| 6 | `Muéstrame mis gastos mensuales en gráfico de líneas` | analyst.spending_trends (con chart XAI) |
| 7 | `¿Cuánto voy a gastar el mes que viene en comida?` | analyst.predict_next_month |
| 8 | `Detecta movimientos raros en mis transacciones` | analyst.detect_anomalies |
| 9 | `Cambia mi perfil a avanzado` *(después: pídele otro resumen)* | role basic → advanced (cambia el tono del narrator) |
| 10 | `Reparte mis gastos por categoría del último trimestre` | analyst.category_breakdown |

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
.venv/Scripts/locust.exe -f tests/stress/locustfile.py --host http://localhost:8000 --users 10 --spawn-rate 2 --run-time 30s --headless
```

Más detalle en [tests/stress/README.md](tests/stress/README.md).

### Evaluaciones (baselines de las evoluciones P2 / P3 / P5)

Los scripts de `scripts/eval/` regeneran los JSON de `docs/results/` que
[doc/evoluciones.md](doc/evoluciones.md) cita como prueba cuantitativa
del 20 % de mejora pedida por el enunciado:

```bash
# Accuracy del clasificador (P2) — produce docs/results/p2_baseline.json y p2_post.json
.venv/Scripts/python.exe scripts/eval/eval_p2_classifier.py

# OCR (P3) — sobre CORD y sobre facturas en euros
.venv/Scripts/python.exe scripts/eval/eval_p3_ocr.py

# Biometría (P5) — accuracy, FAR/FRR
.venv/Scripts/python.exe scripts/eval/eval_p5_biometrics.py
```

---

## Documentación adicional

- **Evoluciones incrementales (P2, P3, P5):** [doc/evoluciones.md](doc/evoluciones.md)
- **Metodología de prompts (ASPECCT):** [doc/prompts_aspect.md](doc/prompts_aspect.md)
- **Contratos de los sub-agentes:** [doc/agent_contracts.md](doc/agent_contracts.md)
- **Notificaciones Telegram (opcional):** [docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md)
- **API interactiva:** Swagger UI en `http://127.0.0.1:8000/docs`
