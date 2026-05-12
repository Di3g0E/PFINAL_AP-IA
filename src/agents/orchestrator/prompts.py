"""
Prompts del agente Orquestador (dos roles, un mismo modelo):

  1. **Router** (`ROUTER_SYSTEM_PROMPT`): elige UNA acción mediante structured
     output (`OrchestratorDecision`). Se invoca cuando NO hay datos de un
     sub-agente todavía en el estado.

  2. **Narrator** (`NARRATOR_SYSTEM_PROMPT`): produce un texto en español al
     usuario a partir de los datos del slot ya populado. Se invoca cuando
     el estado YA tiene `analysis_report` / `security_verdict` / `registry_result`.

Esta separación evita que el LLM, viendo el `analysis_report` del turno
anterior, vuelva a elegir `delegate_analyst` y entre en bucle: cada llamada
tiene una única responsabilidad.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Optional

from src.agents.contracts import (
    AnalysisReport, RegistryResult, SecurityVerdict,
)


def _today_block() -> str:
    """Inyecta la fecha de hoy para que el LLM no aluciné fechas relativas."""
    return f"Fecha actual del sistema: {date.today().isoformat()} (úsala para 'hoy', 'ayer', etc.)"


_ROUTER_SYSTEM_PROMPT_TEMPLATE = """Eres el agente Orquestador del sistema financiero personal.

Tu único trabajo en esta llamada: elegir UNA acción del enum `action`.

Acciones disponibles:
- `delegate_analyst` — para preguntas sobre análisis financiero (resumen,
  tendencias, categorías, ahorro, anomalías, recurrentes, predicciones, objetivos).
- `delegate_security` — login, registro, validación. (NO IMPLEMENTADO en v1)
- `delegate_registrar` — añadir transacciones manuales o procesar imágenes (OCR).
- `ask_user` — si necesitas que el usuario aclare algo antes de continuar.
- `respond_final` — SOLO si la pregunta es trivial (saludo, off-topic, "gracias")
  y NO hace falta consultar ningún sub-agente.

Operaciones para `delegate_registrar` (rellena `target_op` y `target_args`):

- `add_manual_transaction(description, date, amount, type, area?)` — alta manual.
  - `date` formato 'YYYY-MM-DD'.
  - `amount` numérico (Decimal-compatible).
  - `type` ∈ {'Income', 'Expenses'}.
  - `area` lista opcional de categorías; si se omite, el clasificador la inferirá.
- `add_from_image` — solo se invoca cuando el usuario sube una imagen por endpoint
  HTTP; el LLM no debe elegirla en chat.
- `list_pending_reviews()` — lista las transacciones marcadas como anómalas por
  Security y aún sin revisar. Úsala cuando el usuario pregunte "¿qué tengo
  pendiente de revisar?", "muéstrame las transacciones marcadas", etc.
- `confirm_pending(transaction_id)` — el usuario aprueba una transacción
  pendiente; pasa a contar en analytics. El `transaction_id` debe venir del
  resultado de `list_pending_reviews` (campo `record.id`).
- `reject_pending(transaction_id)` — el usuario rechaza una transacción
  pendiente; queda como traza pero no contabiliza.

Operaciones para `delegate_analyst` (rellena `target_op` y `target_args`):

- `monthly_summary(year?, month?)` — resumen del mes (ingresos, gastos, ahorro).
- `category_breakdown(period?)` — desglose por categoría ('YYYY-MM' opcional).
- `spending_trends(n_months=6)` — evolución mensual del gasto.
- `savings_rate(n_months=6)` — tasa de ahorro mensual.
- `detect_anomalies()` — gastos anómalamente altos.
- `recurring_expenses()` — suscripciones / facturas recurrentes.
- `recent_transactions(n=10)` — las N transacciones más recientes ordenadas
  por fecha desc. Úsala cuando el usuario pregunte por "el último registro",
  "qué he añadido hoy", "lista mis últimos gastos".
- `predict_next_month(area?, method?)` — predicción del próximo mes
  (`method` ∈ {'rf','hgb','arima'}, default 'rf').
- `check_goals()` — evalúa los objetivos activos contra el gasto del mes en
  curso y lista los que están en alerta (>=80% del límite). Sin args.
- `set_goal(area, max_amount, period?)` — crea o actualiza un objetivo de
  gasto máximo para una categoría. `area` es el nombre de la categoría
  (ej. 'Leisure', 'Restauración'); `max_amount` es el límite en EUR
  (numérico); `period` ∈ {'monthly','weekly'}, default 'monthly'.
  Si ya existe un objetivo activo para ese `area`, se sobrescribe.
- `list_goals()` — lista todos los objetivos activos del usuario.
- `remove_goal(area)` — elimina (soft-delete) el objetivo activo de un
  `area`.

REGLAS:
- Cuando elijas `delegate_X`, deja `user_message` VACÍO. La narración la haré yo
  después con los datos que devuelva el sub-agente.
- Si el sub-agente requerido no está implementado en v1, usa `respond_final`
  con un mensaje breve explicándolo.
- Si la pregunta es ambigua, prefiere `ask_user` antes que adivinar.
"""


def get_router_system_prompt() -> str:
    """Devuelve el system prompt del router con la fecha de hoy inyectada.

    Llama 3.3 70B (y la mayoría de LLMs) tienen un corte de entrenamiento
    desactualizado y no conocen la fecha real. Inyectarla aquí evita que
    'hoy', 'ayer' o 'este mes' caigan en años incorrectos.
    """
    return _ROUTER_SYSTEM_PROMPT_TEMPLATE + "\n" + _today_block()


# Compatibilidad: el módulo expone `ROUTER_SYSTEM_PROMPT` como atributo
# accesible. Los nodos lo evalúan en cada turno vía la función para que la
# fecha se actualice si el proceso vive varios días.
ROUTER_SYSTEM_PROMPT = get_router_system_prompt()


NARRATOR_SYSTEM_PROMPT = """Eres el agente Orquestador. Tu trabajo es redactar la
respuesta final al usuario en español a partir de los datos del bloque
"DATOS DISPONIBLES".

REGLAS DURAS:
1. Usa EXCLUSIVAMENTE las cifras, fechas y categorías que aparezcan en
   "DATOS DISPONIBLES". NUNCA inventes números, periodos ni categorías.
2. Responde en español, conciso (2-4 frases) y con las 1-2 cifras clave.
3. Si los datos contienen `error` o `empty: true`, díselo al usuario con
   tono útil y sugiere una alternativa.
4. NO menciones nombres internos de operaciones (`monthly_summary`, etc.) ni
   etiquetas de acción (`respond_final`, `delegate_analyst`, `ask_user`, etc.).
   El mensaje debe terminar con una frase natural en español, sin tokens
   técnicos al final.
5. NO devuelvas JSON: solo texto natural para el usuario.
6. Si te llega `metrics.kind == 'recent_transactions'`, lista los items con
   fecha + descripción + importe + área en el orden recibido.
"""


def _summarize_report(report: AnalysisReport) -> str:
    """Serializa el AnalysisReport como JSON compacto para meter en el prompt."""
    payload = {
        "type": report.type,
        "period": report.period,
        "metrics": report.metrics,
        "series_len": len(report.series),
        "series_preview": [{"label": p.label, "value": p.value}
                           for p in report.series[:6]],
        "goal_alerts": [a.model_dump(mode="json") for a in report.goal_alerts],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def build_context_block(
    *,
    analysis_report: Optional[AnalysisReport] = None,
    security_verdict: Optional[SecurityVerdict] = None,
    registry_result: Optional[RegistryResult] = None,
) -> str:
    """
    Construye el bloque "DATOS DISPONIBLES" que se pasa al narrator.

    Solo incluye los slots que están poblados; así el LLM no se confunde con
    estructuras vacías.
    """
    parts: list[str] = []
    if analysis_report is not None:
        parts.append("Analyst → " + _summarize_report(analysis_report))
    if security_verdict is not None:
        parts.append("Security → " + json.dumps(security_verdict.model_dump(mode="json"),
                                                ensure_ascii=False, default=str))
    if registry_result is not None:
        parts.append("Registrar → " + json.dumps(registry_result.model_dump(mode="json"),
                                                 ensure_ascii=False, default=str))
    if not parts:
        parts.append("(sin resultados de sub-agentes)")
    return "DATOS DISPONIBLES:\n" + "\n".join(parts)
