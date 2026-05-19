# Contratos de los agentes — P6_AP-IA

Este documento fija la **API interna** entre el Orquestador y los 3 sub-agentes especializados. Los contratos se traducen directamente a `dataclasses` Pydantic en `src/agents/contracts.py`.

## Reglas de oro del sistema multiagente

1. **Solo el Orquestador habla con el usuario.** Los sub-agentes devuelven dataclasses, nunca prosa libre.
2. **Solo Orquestador (y Analyst opcionalmente) usan LLM.** Security y Registrar son tools deterministas envueltas en un nodo del grafo.
3. **Aislamiento de estado.** Cada sub-agente solo lee/escribe su slot del `OrchestratorState`. No comparten estado privado.
4. **Bucle controlado.** El Orquestador decide en cada turno si delega, pregunta al usuario o termina (`END`).
5. **Notificaciones disparadas en agente de origen**, nunca desde el frontend.

---

## OrchestratorState (raíz del grafo)

```python
class OrchestratorState(TypedDict):
    # Identidad y sesión
    user_id: str                                # UUID del usuario autenticado
    session_id: str                             # UUID de la sesión activa
    messages: Annotated[list[BaseMessage], add_messages]

    # Slots de los sub-agentes (cada agente escribe el suyo)
    pending_action: Optional[PendingAction]     # qué quiere el usuario
    security_verdict: Optional[SecurityVerdict] # último veredicto de Security
    registry_result: Optional[RegistryResult]   # último resultado del Registrar
    analysis_report: Optional[AnalysisReport]   # último análisis del Analyst
```

Persistido por `PostgresSaver` con `thread_id = f"{user_id}:{session_id}"`.

---

## 1. Agente Security

**Responsabilidades:** autenticación biométrica, cifrado de embeddings, control de acceso, validación anti-anomalías, lanzamiento de notificaciones de seguridad.

**Origen del código (P5):** `src/utils/security.py`, `src/utils/notification_service.py`, modelos de liveness y embedding facial.

**LLM:** NO. 100% determinista.

### Operaciones expuestas al Orquestador

| Op | Input | Output |
|---|---|---|
| `register_user` | `RegisterRequest` (email, passphrase, image) | `SecurityVerdict` |
| `login_user` | `LoginRequest` (email, passphrase, image) | `SecurityVerdict` |
| `validate_transaction` | `TransactionDraft` | `SecurityVerdict` (con `anomaly_reasons`) |

### Dataclasses

```python
@dataclass
class RegisterRequest:
    email: str
    passphrase: str
    face_image: bytes               # JPEG/PNG bytes
    consent_biometric: bool         # RGPD: debe ser True

@dataclass
class LoginRequest:
    email: str
    passphrase: str
    face_image: bytes

@dataclass
class TransactionDraft:
    user_id: str
    description: str
    date: date
    amount: Decimal
    area: list[str]
    type: Literal["Income", "Expenses"]
    source: Literal["manual", "ocr", "import"]

@dataclass
class SecurityVerdict:
    decision: Literal["allow", "deny", "challenge"]
    user_id: Optional[str]          # set si decision="allow" en login/register
    reason: str                     # human-readable, sin PII en claro
    anomaly_reasons: list[str] = []  # solo en validate_transaction
    similarity: Optional[float] = None
    liveness_score: Optional[float] = None
    locked_until: Optional[datetime] = None
```

### Triggers de notificación dentro de Security

- `register_user` OK → `notify_register(user_id)`
- `login_user` cualquier resultado → `notify_login(user_id, success, similarity, liveness)`
- `validate_transaction` con anomalía → `notify_finance_anomaly(reasons, ...)`

### Reutilización P5

- `EncryptedEmbeddingStore` (Fernet + PBKDF2 + HMAC) — copiado tal cual
- `AccessController` (lockout de 5 intentos / 5 min) — copiado tal cual
- Pipeline liveness (DenseNet201) + embedder (FaceNet vía facenet-pytorch)
- Detector de anomalías híbrido (IsolationForest + 3-sigma)

---

## 2. Agente Registrar

**Responsabilidades:** alta de transacciones (manual u OCR), categorización automática, persistencia tras validación de Security.

**Origen del código:**
- P3 (`src/models/ocr_engine.py` → PaddleOCR + GB scoring para totales)
- P2 (`src/models/classifier.py` → SGDClassifier con n-gramas chars para Area)

**LLM:** NO. Determinista.

### Operaciones

| Op | Input | Output |
|---|---|---|
| `add_manual_transaction` | `ManualEntry` | `RegistryResult` |
| `add_from_image` | `ImageUpload` | `RegistryResult` |
| `bulk_import_csv` | `CsvImport` | `RegistryResult` (n resultados) |

### Dataclasses

```python
@dataclass
class ManualEntry:
    user_id: str
    description: str
    date: date
    amount: Decimal
    area: Optional[list[str]] = None     # si None → autoclasificar (P2)
    type: Literal["Income", "Expenses"]

@dataclass
class ImageUpload:
    user_id: str
    image: bytes                          # factura/recibo
    description_hint: Optional[str] = None
    date_hint: Optional[date] = None

@dataclass
class RegistryResult:
    accepted: list[TransactionRecord]     # persistidos tras validación
    pending_review: list[ReviewItem]      # bloqueados por anomalía
    rejected: list[RejectedItem]          # OCR falló o input inválido

@dataclass
class ReviewItem:
    draft: TransactionDraft
    anomaly_reasons: list[str]            # del SecurityVerdict
```

### Flujo interno

1. **Si imagen:** OCR (PaddleOCR) → extracción de total (GB scoring) → descripción/fecha.
2. **Categorización:** si no hay `area`, se llama al `FinancialClassifier` (P2) para predecir.
3. **Construcción del `TransactionDraft`.**
4. **Llamada a Security `validate_transaction`** (validación anti-anomalía obligatoria).
5. Si `decision == "allow"` → persiste en Postgres y devuelve `accepted`.
6. Si `decision == "challenge"` → encola en `pending_review` (no persiste, devuelve para que Orquestador pregunte al usuario).
7. Si `decision == "deny"` → `rejected`.

### Reutilización P2 / P3

- `FinancialClassifier.predict()` (P2) — copiado a `src/agents/registrar/classifier.py`
- `OCREngine.extract_total()` (P3) — copiado a `src/agents/registrar/ocr_engine.py`
- Modelos `.joblib` (P2 + P3) — copiados a `models/`

---

## 3. Agente Analyst

**Responsabilidades:** análisis financiero, detección de tendencias, predicción temporal, evaluación de objetivos.

**Origen del código:**
- P1 (`src/models/` → Random Forest, HistGradBoosting, ARIMA → predicción)
- P4 (`src/features/analytics.py` → resúmenes mensuales, anomalías, recurrentes)

**LLM:** opcional para narrar el `AnalysisReport`. Por defecto **devuelve solo dataclass** y deja que el Orquestador narre.

### Operaciones

| Op | Input | Output |
|---|---|---|
| `monthly_summary` | `user_id, month, year` | `AnalysisReport` |
| `category_breakdown` | `user_id, period` | `AnalysisReport` |
| `spending_trends` | `user_id, n_months` | `AnalysisReport` |
| `detect_anomalies` | `user_id, period` | `AnalysisReport` |
| `recurring_expenses` | `user_id` | `AnalysisReport` |
| `predict_next_month` | `user_id, area` | `AnalysisReport` |
| `check_goals` | `user_id` | `AnalysisReport` (+ disparo notificación si >80%) |
| `set_goal` / `list_goals` / `remove_goal` | ver `Goal` | `AnalysisReport` |

### Dataclasses

```python
@dataclass
class AnalysisReport:
    type: Literal["summary", "trend", "prediction", "goal_status", "anomaly", "recurring"]
    period: Optional[str] = None
    metrics: dict[str, Any] = {}          # estructurado, NO prosa
    series: list[DataPoint] = []          # opcional para gráficas
    goal_alerts: list[GoalAlert] = []     # si check_goals detectó >80%

@dataclass
class GoalAlert:
    goal_id: str
    area: str
    current: Decimal
    limit: Decimal
    pct: float                            # 0.0 - 1.5
    severity: Literal["info", "warning", "critical"]

@dataclass
class Goal:
    id: str
    user_id: str
    area: str
    max_amount: Decimal
    period: Literal["monthly", "weekly"]
    active: bool
```

### Triggers de notificación dentro de Analyst

- `check_goals` con `pct >= 0.80` → `notify_goal_threshold(user_id, goal, current, limit, pct)`
- Severity mapping: `0.80 ≤ pct < 1.0` → warning · `pct ≥ 1.0` → critical

### Reutilización P1 / P4

- `analytics.py` completo (P4) — copiado a `src/agents/analyst/analytics.py`
- Predictores P1 (RF, HistGradBoosting, ARIMA) — copiados a `src/agents/analyst/forecasters.py`
- Reentrenar modelos P1 con sklearn 1.5.0 al primer arranque (script `scripts/train_p1_models.py`)

---

## 4. Agente Orquestador

**Responsabilidades:** routing de la conversación, narración al usuario, decisión de cuándo delegar y cuándo terminar.

**LLM:** sí (proveedor configurado por el usuario, fallback Groq).

### Esquema del grafo (LangGraph)

```
                        START
                          ↓
                   ┌─────────────┐
              ┌───►│ orchestrator│◄──────┐
              │    │ (LLM router)│       │
              │    └──────┬──────┘       │
              │           │              │
              │     conditional_edges    │
              │           │              │
              │   ┌───────┼────────┐     │
              │   ▼       ▼        ▼     │
              │ security registrar analyst
              │   │       │        │     │
              │   └───────┼────────┘     │
              │           │              │
              │     resultado ──────────►│
              │     en estado            │
              │                          │
              └──────── responde ───────►END
```

- `orchestrator` (LLM) razona sobre `messages` + slots actuales y emite una decisión.
- Routing function lee la decisión y elige el siguiente nodo (`security` | `registrar` | `analyst` | `END`).
- Cada sub-agente termina con edge → `orchestrator` para que narre el resultado o re-delegue.
- Bucle máximo de 6 saltos (recursión limitada por `recursion_limit` de LangGraph).

### Decisiones del Orquestador

```python
@dataclass
class OrchestratorDecision:
    action: Literal["delegate_security", "delegate_registrar",
                    "delegate_analyst", "ask_user", "respond_final"]
    target_op: Optional[str] = None       # ej. "monthly_summary"
    target_args: dict = {}
    user_message: Optional[str] = None    # solo si ask_user o respond_final
```

El LLM produce esta decisión via `with_structured_output(OrchestratorDecision)`.

### Narración

Cuando un sub-agente devuelve un `AnalysisReport`, el Orquestador:
1. Lee `metrics` y `series`.
2. LLM compone una respuesta natural en español usando los datos como contexto.
3. NO inventa cifras (regla en system prompt: "usa exclusivamente las cifras de `metrics`").
4. Sirve el mensaje al usuario.

---

## 5. Resumen de qué se reutiliza de cada práctica

| De | Qué | A dónde en P6 |
|---|---|---|
| **P1** | Predictores temporales (RF, HistGradBoosting, ARIMA) | `src/agents/analyst/forecasters.py` |
| **P2** | `FinancialClassifier` (SGDClassifier + char n-grams) | `src/agents/registrar/classifier.py` |
| **P2** | Preprocessing multilingüe | `src/agents/registrar/preprocessing.py` |
| **P3** | OCR engine (PaddleOCR + GB scoring) | `src/agents/registrar/ocr_engine.py` |
| **P3** | Modelos joblib (`mobile_pipeline_gb_np1.joblib`) | `models/` |
| **P4** | `analytics.py` | `src/agents/analyst/analytics.py` |
| **P4** | Esqueleto LangGraph (`graph.py`) | `src/agents/orchestrator/graph.py` (generalizado) |
| **P4** | Sistema de tools | `src/agents/{security,registrar,analyst}/tools.py` |
| **P5** | `security.py` (Fernet, PBKDF2, lockout, HMAC) | `src/utils/security.py` |
| **P5** | `notification_service.py` (WhatsApp + Telegram) | `src/utils/notifications.py` |
| **P5** | Pipeline biométrico (liveness + embedder) | `src/agents/security/biometrics.py` |
| **P5** | Detector de anomalías financieras | `src/agents/security/anomaly_detector.py` |

---

## 6. Lo que **no** se reutiliza (y por qué)

| Descartado | Origen | Razón |
|---|---|---|
| Streamlit UI | P4 | Sustituido por FastAPI + Next.js |
| CLI argparse | P1, P2, P3, P5 | Sustituido por endpoints FastAPI |
| `coach_classico` (intent router) | P4 | Redundante con el Orquestador LLM |
| EasyOCR fallback | P3 | PaddleOCR como motor único |
| Transformers para Area | P2 | SGDClassifier es suficiente y mucho más ligero |
| pmdarima | P1 | statsmodels.tsa cubre ARIMA con menos build issues |
| deepface | P5 | facenet-pytorch evita dependencia de TensorFlow |
| Memoria JSON por usuario | P4 | Sustituida por `PostgresSaver` de LangGraph |
