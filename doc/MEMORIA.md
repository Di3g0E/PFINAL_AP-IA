# Memoria del proyecto P6_AP-IA

**Sistema unificado multiagente de gestión financiera personal**
*Curso 2025-26 — Grado en Ingeniería en Inteligencia Artificial — URJC*

---

## 1. Visión general

P6 integra las funcionalidades de las prácticas anteriores (P1–P5) bajo un único sistema controlado por agentes de **LangGraph**, con autenticación biométrica, soporte multiusuario y persistencia en base de datos.

### Topología del sistema

```
                    USUARIO
                       │ (chat o endpoint HTTP)
                       ▼
              ┌─────────────────┐
              │  Orchestrator   │  ← LLM (Groq/OpenAI/Anthropic/Google)
              │  (router +      │     · structured output: OrchestratorDecision
              │   narrator)     │     · narración en español
              └────────┬────────┘
                       │ delegate_*
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌─────────┐    ┌──────────┐   ┌──────────┐
   │Security │    │Registrar │   │ Analyst  │
   │         │    │          │   │          │
   │ - validate   │ - manual │   │ - 8 ops  │
   │ - register   │ - OCR    │   │ - forecast
   │ - login      │ - clf    │   │ - goals  │
   └────┬────┘    └─────┬────┘   └─────┬────┘
        │ valida        │                │
        └───────────────┘                │
                                         │
              ┌──────────────────────────┘
              ▼
     PostgreSQL / SQLite (transactions, users, settings, events)
```

### Reglas de oro del diseño

1. **Solo el Orquestador habla con el usuario**. Los sub-agentes devuelven dataclasses Pydantic, nunca prosa libre.
2. **Solo Orchestrator usa LLM**. Security, Registrar y Analyst son deterministas (modelos ML clásicos + reglas).
3. **Aislamiento de estado**. Cada sub-agente escribe en su slot del `OrchestratorState`.
4. **Bucle controlado**. Routing → Subagente → Narración → END (cinturón anti-bucles a 6 iteraciones).
5. **Notificaciones disparadas en agente de origen**, nunca desde el frontend.
6. **Datos del usuario solo se validan contra su propio histórico** (Security usa `load_user_history_db_only`, no fallback a CSV de demo).

---

## 2. Cronología de implementación

### Ciclo 0 — Cimentación (`requirements`, esquema, contratos)

| Entregable | Detalle |
|---|---|
| `requirements.txt` consolidado | NumPy `1.26.4`, sklearn `1.5.0`, paddle `2.6.2`, torch `2.5.1`, langgraph + 4 providers LLM, FastAPI, Postgres. Ancla en NumPy 1.x por PaddleOCR. |
| `doc/agent_contracts.md` | Pydantic dataclasses de la API interna entre agentes (firmados). |
| Estructura `src/` por agentes | `orchestrator/`, `security/`, `registrar/`, `analyst/`, más `api/`, `data/`, `utils/`. |
| Cifrado y notificaciones | `EncryptedEmbeddingStore` (Fernet+PBKDF2+HMAC) y `notifications.py` (Telegram primario + WhatsApp opcional) copiados/adaptados de P5 con soporte multiusuario. |
| Esquema SQL | Modelos SQLAlchemy: `users`, `user_settings`, `transactions`, `goals`, `events`. |
| Docker Compose | `db` (Postgres 15) + `api` con `cloudflared` opcional. |

### Ciclo 1 — Agente Analyst

| Componente | Origen | Destino |
|---|---|---|
| Loader CSV (parser EUR `'10,00€'`) | P4/data/loader.py | `src/agents/analyst/data_source.py` |
| 7 funciones de analytics | P4/features/analytics.py | `src/agents/analyst/analytics.py` |
| 3 forecasters (RF, HGB, ARIMA) | P1/models/trainer.py (sin TF/XGB/pmdarima) | `src/agents/analyst/forecasters.py` |
| Agente con 8 operaciones | Nuevo | `src/agents/analyst/agent.py` |

**12 tests pasan** sobre 887 transacciones reales.

### Ciclo 2 — Orquestador + LLM factory

- `LLM factory` con catálogo cerrado de modelos (Groq, OpenAI, Anthropic, Google) + cifrado de API keys con clave maestra Fernet.
- **Doble modo del orchestrator_node**: `_route` (structured output) cuando no hay datos vs `_narrate` (LLM plano) cuando ya tiene resultado de un sub-agente. Resuelve el bucle infinito.
- `MemorySaver` con `JsonPlusSerializer(allowed_msgpack_modules=[...])` para silenciar warnings de Pydantic.
- Demo CLI `main.py`.

**6 tests del routing** con LLM mockeado.

### Ciclo 3 — Agente Registrar

| Componente | Origen | Adaptaciones |
|---|---|---|
| `FinancialClassifier` (TF-IDF+SGDClassifier) | P2 | Copiado tal cual |
| `OCRTotalExtractor` (PaddleOCR + GB scoring) | P3 | Sin EasyOCR fallback. Lazy-load de PaddleOCR. Patrón regex EUR (`25,50`, `91,88`) añadido al original CORD (KRW). Min amount EUR=1.0. |
| `add_manual_transaction` / `add_from_image` | Nuevo | Clasifica → valida (Security) → persiste |
| Modelos joblib pre-entrenados | P2 + P3 | `models/area_classifier.joblib`, `models/ocr_total_extractor.joblib` |

**7 tests del Registrar**.

### Ciclo 4 — Persistencia BD (Postgres + SQLite)

- Schema portable: `Uuid` y `JSON` (en vez de PG-only `UUID`, `ARRAY`, `JSONB`).
- Default a SQLite local (`data/p6.db`) cuando `DATABASE_URL` esté vacía o sea inválida (`...`). Validador Pydantic.
- `scripts/init_db.py` idempotente que crea tablas + usuario demo + migra el CSV.
- `_persist` real con `SQLAlchemy.INSERT` y fallback a stub en memoria si BD no disponible.
- `_load_user_dataframe` lee de BD primero; cae a CSV si no hay datos del usuario.
- `DEMO_USER_ID = "00000000-0000-0000-0000-000000000001"` fijo.

**5 tests de integración BD** con SQLite real (fixture aislado por test).

### Ciclo 5 — Refinamiento del orquestador

Cuatro bugs detectados en la demo en vivo y arreglados:

1. **`'str' object has no attribute 'hex'`** en lookup de `user_settings` → conversión `uuid.UUID(user_id)` antes de la query SQLAlchemy.
2. **LLM aluciné fechas** ("2024-04-30" para "hoy"). Fix: `get_router_system_prompt()` que inyecta `date.today().isoformat()` en cada turno.
3. **LLM no respondía sobre el último registro**. Fix: nueva operación `recent_transactions(n)` en el Analyst, registrada en el routing.
4. **Crash si CSV demo no existe** Y BD vacía. Fix: `_safe_csv_fallback()` devuelve DataFrame vacío con esquema correcto.

### Ciclo 6 — Agente Security: anti-anomalías

- `FinancialAnomalyDetector` (IsolationForest + 3-Sigma) adaptado a P6:
  - Columnas P6 (`Amount_clean`, `Date_parsed`, `Area` multilabel).
  - Umbral mínimo de historia (`MIN_HISTORY_FOR_RULES=5`) para no falsear positivos en usuarios nuevos.
  - "Categoría nunca antes vista" solo si el usuario ya tiene ≥3 categorías.
- `validate_transaction` operacional desde el chat. Reemplaza el stub del Registrar.
- `notify_finance_anomaly` se dispara con respeto a `notification_level` (RGPD).

**9 tests del Security anti-anomalías**.

### Ciclo 7 — Agente Security: biometría

- `BiometricPipeline` con **lazy-load**: MTCNN + FaceNet + DenseNet201 (~150 MB). Singleton.
- `register_user`: consentimiento RGPD obligatorio + liveness ≥0.95 + bcrypt + embedding cifrado en disco.
- `login_user`: lookup → lockout → bcrypt → liveness → cosine sim ≥0.60. `AccessController` 5 fallos / 5 min.
- **Fix bcrypt**: `passlib 1.7.4` + `bcrypt 4.x` incompatibles → migrado a API nativa `bcrypt.hashpw` / `bcrypt.checkpw`.

**El chat NO ejecuta biometría** (no puede subir bytes). Las funciones se exponen para los endpoints HTTP de un futuro ciclo.

**9 tests biométricos** con `BiometricPipeline` mockeado (sin descargar modelos reales).

### Ciclo 8 — Revisión de transacciones pendientes

Hasta este ciclo, cuando Security marcaba una transacción como `challenge`,
el `RegistryResult.pending_review` contenía un `ReviewItem` con el `draft`
sin persistir. Al cerrar el turno se perdía: no había forma de revisarlo
después.

**Cambios introducidos**:

| Fichero | Cambio |
|---|---|
| `src/data/schema.py` | Nueva columna `Transaction.status` ∈ {`accepted`,`pending`,`rejected`} + `anomaly_reasons` JSON. CheckConstraint y default `'accepted'`. |
| `src/agents/contracts.py` | `TransactionRecord` añade `status` y `anomaly_reasons`. `ReviewItem.draft` → `ReviewItem.record` (la transacción ya persistida). |
| `src/agents/registrar/agent.py` | `_persist(draft, status, anomaly_reasons)` admite el status. `add_manual_transaction` y `add_from_image` ahora **persisten también las pendientes** con `status='pending'`. |
| `src/agents/registrar/agent.py` | Tres operaciones nuevas: `list_pending_reviews(user_id)`, `confirm_pending(user_id, tx_id)`, `reject_pending(user_id, tx_id)`. |
| `src/agents/analyst/data_source.py` | `load_from_db(..., only_accepted=True)` filtra para que las pendientes/rechazadas **NO cuenten en analytics**. |
| `src/agents/orchestrator/nodes.py` | `registrar_node` enruta las 3 nuevas ops. |
| `src/agents/orchestrator/prompts.py` | Sistema prompt del router instruye al LLM cuándo usar cada nueva op. |

**9 tests end-to-end** (`tests/test_pending_review_flow.py`) con SQLite real verifican:
persistencia de pending, listado, confirm/reject, doble confirm rechazado, UUID inválido,
filtrado correcto en analytics.

#### Bug detectado tras prueba en vivo: schema drift en Postgres

Al ejecutar el flujo de revisión contra una BD Postgres ya inicializada en
ciclos anteriores (sin las nuevas columnas `status` y `anomaly_reasons`), el
INSERT y el SELECT fallaban con:

```
column "status" of relation "transactions" does not exist
```

Causa: `Base.metadata.create_all()` solo crea tablas que NO existen; **no
añade columnas a tablas existentes**. La opción brutal era `init_db.py
--reset` (drop + recreate), pero borra todos los datos.

**Fix: migración ligera idempotente en `scripts/init_db.py`.** Una nueva
función `_apply_lightweight_migrations(engine)` con un dict
`{tabla: {columna: DDL}}`:

1. Inspecciona la tabla con `sqlalchemy.inspect`.
2. Detecta columnas declaradas en `schema.py` que faltan en la BD real.
3. Aplica `ALTER TABLE ... ADD COLUMN ...` sin tocar los datos existentes
   (las filas viejas reciben el `DEFAULT` declarado).

Funciona en Postgres y SQLite. Idempotente. Para cambios más complejos
(rename, drop, type change) hará falta Alembic en un ciclo futuro.

### Ciclo 9 — API REST FastAPI

Hasta ahora el sistema solo era accesible vía CLI (`main.py`). Este ciclo
expone el sistema multiagente a través de **endpoints HTTP** con auth JWT.

| Fichero | Cambio |
|---|---|
| `src/api/dependencies.py` | `create_access_token()` y `get_current_user_id()` (extrae user_id del header `Authorization: Bearer`). |
| `src/api/routers/auth.py` | `POST /auth/register` y `POST /auth/login` con `multipart/form-data` (incluye foto). Devuelven JWT. Internamente delegan a `security.register_user/login_user`. |
| `src/api/routers/chat.py` | `POST /chat` (auth) — invoca el grafo LangGraph; soporta `session_id` para conversaciones multi-turno con el `MemorySaver`. |
| `src/api/routers/transactions.py` | `POST /transactions` (alta manual), `GET /transactions/pending` (lista revisiones), `POST /transactions/pending/{id}/confirm`, `DELETE /transactions/pending/{id}`. |
| `src/api/main.py` | App FastAPI con CORS, lifespan que llama a `configure_logging()`. |
| `tests/test_api_endpoints.py` | 11 tests con `TestClient`: register/login/JWT/chat/transacciones/pending lifecycle. Mockean BiometricPipeline y LLM. |

**Diseño**:
- **JWT** firmado con `settings.jwt_secret`. Payload: `{sub: user_id, exp: ...}`.
- **Singleton del grafo**: `_GRAPH = build_graph()` se reutiliza entre requests
  para que `MemorySaver` mantenga estado entre peticiones del mismo
  `thread_id = user:session`.
- **Mapeo de errores → HTTP**: `decision='deny'` por consent → 400; passphrase
  errónea / token inválido → 401; transacción no encontrada → 404; doble
  confirm → 409.
- **Multipart en register/login**: la imagen llega como `UploadFile`, se lee
  a bytes y se pasa al `RegisterRequest`/`LoginRequest` que ya existían.

Arranque:
```bash
uvicorn src.api.main:app --reload --port 8000
# → Swagger UI en http://localhost:8000/docs
```

Total suite: **72 tests** verde, 0 warnings.

### Ciclo 10 — Frontend Next.js

Cliente web del sistema, viviendo en `frontend/` dentro del propio
repositorio del proyecto.

**Stack**: Next.js 14 (App Router), TypeScript, Tailwind CSS, fetch nativo
(sin axios). Captura de webcam con `navigator.mediaDevices.getUserMedia`
+ `<canvas>` → `Blob` JPEG.

| Fichero | Función |
|---|---|
| `frontend/src/app/layout.tsx` | Header + nav + body. |
| `frontend/src/app/page.tsx` | Landing con redirección si hay token. |
| `frontend/src/app/login/page.tsx` | Email + passphrase + webcam → POST `/auth/login` → JWT. |
| `frontend/src/app/register/page.tsx` | Email + passphrase + consent + webcam → POST `/auth/register`. |
| `frontend/src/app/chat/page.tsx` | Chat con el orquestador. Mantiene `session_id` en estado para multi-turno. |
| `frontend/src/app/pending/page.tsx` | Lista pendientes y permite aprobar (POST confirm) o rechazar (DELETE). |
| `frontend/src/components/WebcamCapture.tsx` | Encapsula el flujo getUserMedia → canvas → blob. Libera el stream al desmontar. |
| `frontend/src/lib/api.ts` | Wrapper de `fetch` con JWT en `Authorization: Bearer`. |

**Decisiones**:
- **Ruta protegida sin middleware**: cada página protegida (chat, pending)
  comprueba `localStorage.token` en `useEffect` y redirige a `/login` si
  falta. Es suficiente para el alcance académico; en producción real
  iría un middleware de Next.js o un layout segmentado.
- **Token en localStorage**: trade-off frente a cookies httpOnly. Más
  simple para el demo CLI/Vercel; menos seguro contra XSS. Documentado
  en el README como punto a mejorar en v2.
- **Sin axios**: `fetch` nativo + `FormData` para multipart. Cliente más
  ligero, sin dependencias.
- **Sin tests del frontend**: el alcance es UI + integración con la API
  ya testeada. Los flujos críticos (auth, chat, pending) los validamos
  end-to-end en `tests/test_api_endpoints.py`.

**Arranque**:
```bash
cd frontend
npm install
cp .env.local.example .env.local      # NEXT_PUBLIC_API_BASE_URL=...
npm run dev                            # http://localhost:3000
```

CORS ya permite `http://localhost:3000` desde `settings.cors_origins`.

---

## 3. Decisiones arquitectónicas clave

### 3.1 NumPy 1.26 como ancla

PaddleOCR 2.6.2 exige `numpy<2.0`. En vez de aislar P3 en un microservicio aparte, anclamos **todo el stack** a NumPy 1.26.4. sklearn, langchain, fastapi y torch funcionan perfectamente con esa versión. Una sola imagen Docker, un solo entorno.

### 3.2 Sin TensorFlow

`deepface` (P5 ArcFace) traía TensorFlow. Lo eliminamos y usamos solo `facenet-pytorch` (`InceptionResnetV1` VGGFace2). Beneficio: sin Paddle + Torch + TF coexistiendo (3 frameworks DL) → instalación más estable y ~600 MB menos en la imagen.

### 3.3 SQLite por defecto en dev

El `.env.example` deja `DATABASE_URL=` vacío. Un validator Pydantic detecta URLs inválidas o el placeholder `...` y cae al SQLite local automáticamente. Cero setup para el desarrollador; Postgres se activa solo cuando se quiera con Docker o Supabase.

### 3.4 Tipos portables (Uuid, JSON)

Se evitan los tipos de `dialects.postgresql` (UUID, ARRAY, JSONB). Se usa `sqlalchemy.Uuid` y `sqlalchemy.JSON`. SQLAlchemy hace la conversión transparente: PostgreSQL recibe nativos, SQLite recibe TEXT.

### 3.5 Doble modo del orquestador (route vs narrate)

El bug del bucle infinito se resolvió **separando responsabilidades por llamada al LLM**:

- **Route**: structured output (`OrchestratorDecision`). Sin texto al usuario.
- **Narrate**: LLM plano (`invoke`). Texto en español a partir del slot poblado.

La detección de modo es trivial: `_has_subagent_data(state)` mira si `analysis_report` / `security_verdict` / `registry_result` están poblados.

### 3.6 Filtro de kwargs alucinados

El LLM puede pasar argumentos que no existen en la función real (vimos `period` a `monthly_summary`). En vez de detectar el error y narrar el fallo, **se filtran silenciosamente**:

```python
def _safe_kwargs(func, args):
    sig = inspect.signature(func)
    accepted = {p.name for p in sig.parameters.values() if ...}
    return {k: v for k, v in args.items() if k in accepted}
```

### 3.7 Inyección de `date.today()` en el prompt

Llama 3.3 (corte de entrenamiento ~2024) no conoce la fecha real. `get_router_system_prompt()` añade en cada turno la fecha actual del sistema. Los `add_manual_transaction` con "hoy" ya van con fecha correcta.

### 3.8 Security NO usa CSV ajeno

Validar contra el histórico de OTRO usuario sería un sinsentido. El detector de anomalías usa `load_user_history_db_only` (sin fallback CSV). El Analyst sí cae al CSV demo (mejor mostrar algo que nada en la respuesta narrativa).

### 3.9 Umbral mínimo de historia para anomalías

Con `n_history < 5` el detector devuelve `allow` por defecto: marcar como anómala la primera transacción de un usuario nuevo es contraproducente. La regla "categoría nunca antes vista" solo se activa si el usuario ya tiene ≥3 categorías diferentes.

### 3.10 Lazy-loading de modelos pesados

PaddleOCR (~500 MB) y FaceNet+DenseNet201 (~150 MB) se inicializan **solo en la primera operación**, no al arrancar. El `main.py` arranca instantáneamente; los modelos se cargan transparentemente en el primer uso real.

### 3.11 Logging estructurado JSON con `Stopwatch`

Cada operación de cada agente emite un evento `{ts, agent, action, status, latency_ms, user_id, session_id, payload}`. Esto deja la puerta abierta a la tabla `events` y a Grafana/Loki en v2 sin tocar el código.

### 3.12 RGPD por defecto

- Consentimiento biométrico explícito en `register_user`.
- `notification_level='redacted'` por defecto: las notificaciones nunca exponen importes ni descripciones a Telegram/WhatsApp salvo que el usuario active `'full'`.
- Embeddings biométricos cifrados con Fernet (AES-128) + clave derivada con PBKDF2-HMAC-SHA256 a 310 000 iteraciones.

---

## 4. Bugs encontrados y correcciones

| # | Síntoma | Causa raíz | Fix |
|---|---|---|---|
| 1 | Bucle infinito hasta `iteration_limit` en cada turno | El LLM, con structured output, ignoraba el slot `analysis_report` y volvía a delegar | Separar `_route` y `_narrate` en el orquestador |
| 2 | `Could not parse SQLAlchemy URL` | El `.env.example` traía `DATABASE_URL=...` (literal) | Validator Pydantic que sustituye URLs inválidas por SQLite |
| 3 | `Deserializing unregistered type` (msgpack) | LangGraph 1.x avisa de tipos Pydantic no registrados | Pasar `allowed_msgpack_modules=[...]` a `JsonPlusSerializer` |
| 4 | Slots con datos viejos en el siguiente turno | Falta de reset entre turnos del usuario | `main.py` resetea `analysis_report=None`, etc. en cada `invoke` |
| 5 | `'str' object has no attribute 'hex'` | `user_settings.user_id` es `Uuid`, llegaba un str | `uuid.UUID(user_id)` antes del SELECT |
| 6 | LLM dijo "del día 2024-04-30" cuando el usuario escribió "hoy" | Llama 3.3 corte ~2024, no conoce la fecha real | Inyectar `date.today()` en el system prompt del router |
| 7 | "No tengo información sobre el último registro" | No existía la operación; el LLM elegía `recurring_expenses` (más cercana) | Añadir `recent_transactions(n)` al Analyst |
| 8 | Crash si CSV demo no existe Y BD vacía | `pd.read_csv` sin verificar | `_safe_csv_fallback()` devuelve DF vacío con esquema correcto |
| 9 | `monthly_summary() unexpected keyword 'period'` | LLM alucinó nombre de argumento | `_safe_kwargs(func, args)` filtra contra `inspect.signature(func)` |
| 10 | Tests del Registrar fallaban tras integrar Security | Security marcaba todo como anomalía vs CSV ajeno | `load_user_history_db_only` (sin fallback CSV); umbral `MIN_HISTORY_FOR_RULES=5` |
| 11 | `UserWarning: X does not have valid feature names` (sklearn) | IsolationForest entrenado con DataFrame, predict con ndarray | Predict con `pd.DataFrame(columns=[...])` |
| 12 | `bcrypt.__about__.__version__` AttributeError | Incompatibilidad `passlib 1.7.4` + `bcrypt 4.x` | Migrar a API nativa `bcrypt.hashpw` / `bcrypt.checkpw`; eliminar passlib |
| 13 | `column "status" of relation "transactions" does not exist` | `Base.metadata.create_all()` no añade columnas a tablas existentes (schema drift en BDs pre-existentes) | Migración ligera en `init_db.py`: `_apply_lightweight_migrations` detecta columnas faltantes vía `sqlalchemy.inspect` y las añade con `ALTER TABLE ADD COLUMN`. Idempotente, no destructiva |

---

## 5. Estado actual

### Tests

```
52 passed in 11.25s, 0 warnings
├── test_analyst_smoke.py        (13 tests)
├── test_db_integration.py        (5 tests)
├── test_orchestrator_routing.py  (9 tests)
├── test_registrar_smoke.py       (7 tests)
├── test_security_biometrics.py   (9 tests)
└── test_security_smoke.py        (9 tests)
```

### Agentes

| Agente | Estado | Operaciones |
|---|---|---|
| Orchestrator | Completo | `route` + `narrate` con cinturón anti-bucles |
| Analyst | Completo | `monthly_summary`, `category_breakdown`, `spending_trends`, `savings_rate`, `detect_anomalies`, `recurring_expenses`, `recent_transactions`, `predict_next_month`, `check_goals` |
| Registrar | Completo | `add_manual_transaction`, `add_from_image` |
| Security (anomalías) | Operacional desde chat | `validate_transaction` |
| Security (biometría) | Listo, requiere endpoints HTTP | `register_user`, `login_user` |

### Estructura de directorios

```
P6_AP-IA/
├── src/
│   ├── agents/
│   │   ├── contracts.py         # Pydantic dataclasses
│   │   ├── orchestrator/        # Router LLM + narrator + grafo + estado
│   │   ├── security/            # Anomalías + biometría
│   │   ├── registrar/           # OCR + clasificador + persistencia
│   │   └── analyst/             # Analytics + forecasters
│   ├── api/                     # FastAPI (pendiente)
│   ├── data/
│   │   ├── schema.py            # SQLAlchemy ORM
│   │   └── database.py          # engine + session factory
│   └── utils/
│       ├── security.py          # Fernet, PBKDF2, AccessController
│       ├── notifications.py     # Telegram + WhatsApp multiusuario
│       ├── config.py            # Pydantic Settings
│       └── logging_config.py    # JSON logger + Stopwatch
├── scripts/init_db.py           # Crea tablas + usuario demo + migra CSV
├── models/                      # area_classifier.joblib, ocr_total_extractor.joblib
├── data/raw/db_mod_descript.csv # Dataset de demo
├── tests/                       # 52 tests
├── doc/agent_contracts.md       # Contratos de la API interna
├── doc/MEMORIA.md               # Este documento
├── Dockerfile + docker-compose.yml
├── pyproject.toml               # Config pytest
├── .env.example
├── requirements.txt
└── main.py                      # Demo CLI
```

---

## 6. Reutilización de las prácticas P1–P5

| Origen | Qué se reutiliza | Destino en P6 |
|---|---|---|
| **P1** | Predictores RF, HGB, ARIMA (sin TF/XGB/pmdarima) | `src/agents/analyst/forecasters.py` |
| **P2** | `FinancialClassifier` (SGDClassifier + char n-grams) + preprocessing | `src/agents/registrar/{classifier,preprocessing}.py` |
| **P3** | Motor OCR (PaddleOCR + GB scoring) | `src/agents/registrar/ocr_engine.py` |
| **P3** | Modelo `mobile_pipeline_gb_np1.joblib` | `models/ocr_total_extractor.joblib` |
| **P4** | Esqueleto LangGraph + analytics | `src/agents/orchestrator/graph.py`, `src/agents/analyst/analytics.py` |
| **P4** | Loader CSV con parser EUR | `src/agents/analyst/data_source.py` |
| **P5** | `EncryptedEmbeddingStore` + `AccessController` (Fernet+PBKDF2) | `src/utils/security.py` |
| **P5** | `notification_service.py` Telegram + WhatsApp | `src/utils/notifications.py` (adaptado multi-usuario) |
| **P5** | `FaceDetector` (MTCNN) + `FaceNetEmbedder` + `LivenessDetector` (DenseNet201) | `src/agents/security/biometrics.py` |
| **P5** | `FinancialAnomalyDetector` (IsolationForest + 3-Sigma) | `src/agents/security/anomaly_detector.py` |
| **P5** | Dataset `db_mod_descript.csv` | `data/raw/db_mod_descript.csv` |

### Lo que NO se reutiliza

| Descartado | Razón |
|---|---|
| Streamlit UI (P4) | Sustituido por FastAPI + Next.js |
| CLI argparse (P1, P2, P3, P5) | Sustituido por endpoints |
| `coach_clasico` con intent router (P4) | Redundante con el orquestador LLM |
| EasyOCR fallback (P3) | PaddleOCR como motor único |
| Transformers para Area (P2) | SGDClassifier es suficiente y mucho más ligero |
| pmdarima (P1) | `statsmodels.tsa` cubre ARIMA con menos build issues |
| deepface / ArcFace (P5) | `facenet-pytorch` evita TensorFlow |
| Memoria JSON por usuario (P4) | Sustituida por `PostgresSaver` / `MemorySaver` de LangGraph |

---

## 7. Stack técnico

```
Lenguaje:       Python 3.12
Numerics:       NumPy 1.26.4 (ancla por PaddleOCR)
ML:             scikit-learn 1.5.0, statsmodels, facenet-pytorch
DL:             PyTorch 2.5.1 (CPU), DenseNet201 (ImageNet)
OCR:            PaddleOCR 2.8.1 + Gradient Boosting scoring
LLM:            LangGraph 1.x + LangChain (Groq, OpenAI, Anthropic, Google)
HTTP:           FastAPI 0.115 + uvicorn (pendiente integrar endpoints)
BD:             SQLAlchemy 2.0 + psycopg 3 (Postgres) | sqlite (dev)
Cripto:         cryptography (Fernet, PBKDF2-HMAC-SHA256), bcrypt
Logging:        loguru (JSON estructurado)
Notif:          requests (Telegram), pywhatkit (WhatsApp opcional)
Tests:          pytest 9 + pytest-asyncio
Despliegue:     Docker Compose (api + db) + cloudflared opcional
```

### LLM por usuario

Catálogo cerrado en frontend para evitar typos:

```
groq:      llama-3.3-70b-versatile, llama-3.1-8b-instant, mixtral-8x7b-32768
openai:    gpt-4o, gpt-4o-mini, gpt-4.1-mini
anthropic: claude-sonnet-4-5, claude-haiku-4-5
google:    gemini-2.0-flash, gemini-1.5-pro, gemini-1.5-flash
```

API key del usuario cifrada con `MASTER_FERNET_KEY` del servidor (en `.env`). Fallback a Groq compartido si el usuario no tiene clave propia.

---

## 7-bis. Evoluciones incrementales (P2, P3, P5)

Esta práctica final incorpora **tres evoluciones** sobre módulos
previos, cada una representando aproximadamente el 20 % del esfuerzo
del módulo original (P4 se considera cubierto por consenso del
enunciado):

| Evolución | Módulo | Foco |
|---|---|---|
| E1 | P2 | Clasificador de `Area` con embeddings de transformer multilingüe + LinearSVM + i18n |
| E2 | P3 | OCR de facturas en euros con campos enriquecidos (NIF, comercio, IVA, líneas) y reducción de latencia percibida |
| E3 | P5 | Biometría con vídeo + anti-spoofing escalonado en 6 fases (vídeo, Silent-Face, blink, gestos, Moiré, rPPG) |

La planificación detallada, las métricas pre/post, los archivos
afectados y los criterios de aceptación se encuentran en
[`doc/evoluciones.md`](evoluciones.md). Ese documento es la fuente de
verdad para cuantificar el cumplimiento del punto 10 del enunciado.

---

## 8. Roadmap pendiente

### Próximos ciclos (orden recomendado)

1. **Endpoints FastAPI**
   - `POST /auth/register` — multipart con email + passphrase + foto (consume `security.register_user`).
   - `POST /auth/login` — devuelve JWT.
   - `POST /transactions/manual` y `POST /transactions/ocr` (consume Registrar).
   - `POST /chat` — mensaje al orquestador (autenticado por JWT).
   - `GET /me/pending-reviews`, `POST /me/pending-reviews/{id}/confirm`, `DELETE /me/pending-reviews/{id}` — revisión de transacciones marcadas.
   - `GET /me/export` — ZIP RGPD.
   - `DELETE /me` — cascade RGPD.

2. **Frontend Next.js** (Vercel free)
   - Login con webcam (`navigator.mediaDevices.getUserMedia`).
   - Chat con el orquestador.
   - Vista de transacciones pendientes con botones aprobar/rechazar.
   - Settings: LLM provider + API key + canales de notificación.

3. **Memoria persistente de objetivos**
   - Operaciones `set_goal` / `list_goals` / `remove_goal` que persistan en la tabla `goals`.
   - El Analyst.`check_goals` ya lee `Goal` objects, falta el flujo CRUD.

4. **Testing en Postgres real**
   - Hoy todos los tests usan SQLite. Añadir un test que verifique el flujo en Postgres usando `docker compose up db`.

5. **Migraciones con Alembic**
   - Hoy `Base.metadata.create_all()` solo crea tablas nuevas. Para evolucionar el esquema sin perder datos, falta inicializar Alembic.

### v2 (más adelante)

- Local-first: SQLite/IndexedDB en cliente con sync por eventos al backend.
- E2E encrypted backup en servidor.
- Observabilidad: Grafana/Loki sobre la tabla `events`.
- WhatsApp Business API (sustituye a pywhatkit, que requiere navegador).
- Liveness fine-tuneado en NUAA/CASIA-FASD/Replay-Attack (hoy usa pesos ImageNet sin ajuste antispoof).

---

## 9. Cómo arrancar el proyecto

### Setup mínimo (cero Docker)

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe --link-mode=copy -r requirements.txt
cp .env.example .env
# editar .env y rellenar GROQ_API_KEY (al menos)
.venv/Scripts/python.exe scripts/init_db.py    # crea tablas + migra CSV
.venv/Scripts/python.exe main.py
```

### Suite completa

```bash
.venv/Scripts/python.exe -m pytest tests/ -v
```

### Postgres con Docker

```bash
# en .env: DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/p6
docker compose up -d db
.venv/Scripts/python.exe scripts/init_db.py
.venv/Scripts/python.exe main.py
```

---

## 10. Autores

- Diego Esclarín
- Sofía Contreras

*Curso 2025-26 — Grado en Ingeniería en Inteligencia Artificial — URJC*
