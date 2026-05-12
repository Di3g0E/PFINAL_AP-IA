"""
Agente Analyst — operaciones expuestas al Orquestador.

Cada operación devuelve un `AnalysisReport` (dataclass del contrato), nunca
texto libre: el Orquestador es el único que narra al usuario.

Operaciones (ver doc/agent_contracts.md):
  - monthly_summary
  - category_breakdown
  - spending_trends
  - savings_rate
  - detect_anomalies
  - recurring_expenses
  - predict_next_month
  - check_goals  (dispara `notify_goal_threshold` si pct >= 0.80)
  - set_goal     (crea/actualiza un objetivo activo en BD)
  - list_goals   (lista los objetivos activos del usuario)
  - remove_goal  (soft-delete del objetivo activo de un area)
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

import pandas as pd
from loguru import logger

from src.agents.analyst import analytics, forecasters
from src.agents.analyst.data_source import (
    delete_user_goal, load_user_goals, upsert_user_goal,
)
from src.agents.contracts import (
    AnalysisReport, DataPoint, Goal, GoalAlert,
)
from src.utils.notifications import (
    UserNotificationConfig, notify_goal_threshold,
)


# Operaciones puramente analíticas (devuelven AnalysisReport)

def monthly_summary(df: pd.DataFrame, year: Optional[int] = None,
                    month: Optional[int] = None) -> AnalysisReport:
    if df.empty:
        return AnalysisReport(type="summary", metrics={"empty": True})
    metrics = analytics.compute_monthly_summary(df, year=year, month=month)
    return AnalysisReport(type="summary", period=metrics.get("period"),
                          metrics=metrics)


def category_breakdown(df: pd.DataFrame, period: Optional[str] = None) -> AnalysisReport:
    if df.empty:
        return AnalysisReport(type="category", metrics={"empty": True})
    metrics = analytics.compute_category_breakdown(df, period=period)
    return AnalysisReport(type="category", period=period, metrics=metrics)


def spending_trends(df: pd.DataFrame, n_months: int = 6) -> AnalysisReport:
    if df.empty:
        return AnalysisReport(type="trend", metrics={"empty": True})
    metrics = analytics.compute_spending_trends(df, n_months=n_months)
    series = [DataPoint(label=k, value=v) for k, v in metrics["monthly_totals"].items()]
    return AnalysisReport(type="trend", metrics=metrics, series=series)


def savings_rate(df: pd.DataFrame, n_months: int = 6) -> AnalysisReport:
    if df.empty:
        return AnalysisReport(type="savings_rate", metrics={"empty": True})
    metrics = analytics.compute_savings_rate(df, n_months=n_months)
    series = [DataPoint(label=k, value=v) for k, v in metrics["monthly_rates"].items()]
    return AnalysisReport(type="savings_rate", metrics=metrics, series=series)


def detect_anomalies(df: pd.DataFrame) -> AnalysisReport:
    if df.empty:
        return AnalysisReport(type="anomaly", metrics={"empty": True, "anomalies": []})
    items = analytics.detect_anomalies(df)
    return AnalysisReport(type="anomaly", metrics={"anomalies": items, "count": len(items)})


def recurring_expenses(df: pd.DataFrame) -> AnalysisReport:
    if df.empty:
        return AnalysisReport(type="recurring", metrics={"empty": True, "items": []})
    items = analytics.compute_recurring_expenses(df)
    return AnalysisReport(type="recurring", metrics={"items": items, "count": len(items)})


def recent_transactions(df: pd.DataFrame, n: int = 10) -> AnalysisReport:
    """
    Devuelve las N transacciones más recientes (orden descendente por fecha).

    Útil para "muéstrame mi último gasto" / "qué he registrado hoy".
    """
    if df.empty:
        return AnalysisReport(type="summary", metrics={"empty": True, "items": []})

    # `df` viene ya ordenado descendente por Date_parsed desde el data_source.
    head = df.head(max(1, int(n)))
    items = [
        {
            "date": row["Date"],
            "description": row["Description"],
            "amount": round(float(row["Amount_clean"]), 2),
            "area": row["Area"],
            "type": row["Type"],
        }
        for _, row in head.iterrows()
    ]
    return AnalysisReport(
        type="summary",
        metrics={"items": items, "count": len(items), "kind": "recent_transactions"},
    )


def predict_next_month(df: pd.DataFrame, area: Optional[str] = None,
                       method: str = "rf") -> AnalysisReport:
    """Predice gasto del próximo mes. Si `area` se especifica, filtra por categoría."""
    if df.empty:
        return AnalysisReport(type="prediction", metrics={"empty": True})

    all_expenses = df[df["Type"] == "Expenses"]
    expenses = all_expenses
    area_fallback_note: Optional[str] = None
    if area:
        # Match por subcadena case-insensitive: cubre "Leisure" frente a
        # "Leisure, Vacations" y traducciones aproximadas que use el LLM.
        mask = all_expenses["Area"].str.contains(area, case=False, na=False)
        filtered = all_expenses[mask]
        # Fallback: si el filtro deja menos histórico del minimo, predecir sobre
        # el total y avisar al narrador en el report. Mejor una respuesta util
        # que un "histórico insuficiente" cuando hay 5 años de datos.
        if filtered.groupby("YearMonth").ngroups >= 6:
            expenses = filtered
        else:
            area_fallback_note = (
                f"sin datos suficientes para '{area}'; "
                f"prediccion calculada sobre el total de gastos"
            )

    monthly = expenses.groupby("YearMonth")["Amount_clean"].sum().sort_index()
    if len(monthly) < 6:
        return AnalysisReport(type="prediction",
                              metrics={"error": f"Histórico insuficiente: {len(monthly)} meses (mínimo 6)"})

    try:
        result = forecasters.predict_next_month(monthly, method=method)
    except Exception as e:
        return AnalysisReport(type="prediction", metrics={"error": str(e)})

    series = [DataPoint(label=str(k), value=float(v)) for k, v in monthly.items()]
    metrics = {
        "area": area or "all",
        "history_months": len(monthly),
        **result,
    }
    if area_fallback_note:
        metrics["note"] = area_fallback_note
        metrics["area"] = "all"
    return AnalysisReport(type="prediction", metrics=metrics, series=series)


# Operación con efectos: check_goals dispara notificaciones

def check_goals(
    df: pd.DataFrame,
    goals: Optional[list[Goal]] = None,
    notif_config: Optional[UserNotificationConfig] = None,
    user_id: Optional[str] = None,
) -> AnalysisReport:
    """Evalúa cada objetivo activo y dispara notificación si pct >= 0.80.

    Args:
        df: transacciones del usuario.
        goals: lista de objetivos activos. Si es None y se proporciona
            `user_id`, se cargan automáticamente desde la BD.
        notif_config: si se proporciona, se envía `notify_goal_threshold`
            por cada objetivo que supere el umbral.
        user_id: necesario si `goals` es None — para cargarlos de BD.
    """
    if goals is None:
        goals = load_user_goals(user_id) if user_id else []

    alerts: list[GoalAlert] = []

    for g in goals:
        if not g.active:
            continue

        # Evaluamos en el mes en curso (último presente en df)
        if df.empty or "YearMonth" not in df.columns:
            continue

        latest_period = df["YearMonth"].max()
        period_df = df[(df["YearMonth"] == latest_period) & (df["Type"] == "Expenses")]
        spent = period_df[period_df["Area"] == g.area]["Amount_clean"].sum()

        limit = float(g.max_amount)
        pct = float(spent) / limit if limit > 0 else 0.0
        if pct < 0.80:
            continue

        severity: str = "critical" if pct >= 1.0 else "warning"
        alert = GoalAlert(
            goal_id=g.id or "",
            area=g.area,
            current=Decimal(str(round(float(spent), 2))),
            limit=g.max_amount,
            pct=round(pct, 3),
            severity=severity,  # type: ignore[arg-type]
        )
        alerts.append(alert)

        if notif_config is not None:
            try:
                notify_goal_threshold(
                    notif_config,
                    area=g.area, current=alert.current, limit=alert.limit, pct=alert.pct,
                )
            except Exception as e:
                logger.warning(f"Fallo al notificar goal_threshold: {e}")

    return AnalysisReport(
        type="goal_status",
        metrics={"alerts_count": len(alerts), "goals_evaluated": len(goals)},
        goal_alerts=alerts,
    )


# CRUD de objetivos — semántica P4 portada a la BD de P6.

def set_goal(
    user_id: str,
    area: str,
    max_amount,
    period: str = "monthly",
) -> AnalysisReport:
    """Crea o actualiza un objetivo activo para (user_id, area).

    Si ya existe uno activo para ese area, se sobrescribe (upsert).
    """
    if not user_id or not area:
        return AnalysisReport(type="goal_status",
                              metrics={"action": "set", "error": "missing_args"})
    try:
        amount_dec = Decimal(str(max_amount))
    except (InvalidOperation, ValueError, TypeError):
        return AnalysisReport(type="goal_status",
                              metrics={"action": "set", "error": "invalid_amount",
                                       "raw_amount": str(max_amount)})
    if amount_dec <= 0:
        return AnalysisReport(type="goal_status",
                              metrics={"action": "set", "error": "non_positive_amount"})
    if period not in ("monthly", "weekly"):
        period = "monthly"

    goal = upsert_user_goal(user_id, area=area, max_amount=amount_dec, period=period)
    if goal is None:
        return AnalysisReport(type="goal_status",
                              metrics={"action": "set", "error": "db_unavailable"})

    return AnalysisReport(
        type="goal_status",
        metrics={
            "action": "set",
            "area": goal.area,
            "max_amount": float(goal.max_amount),
            "period": goal.period,
        },
    )


def list_goals(user_id: str) -> AnalysisReport:
    """Lista los objetivos activos del usuario."""
    goals = load_user_goals(user_id) if user_id else []
    return AnalysisReport(
        type="goal_status",
        metrics={
            "action": "list",
            "count": len(goals),
            "goals": [
                {
                    "area": g.area,
                    "max_amount": float(g.max_amount),
                    "period": g.period,
                }
                for g in goals
            ],
        },
    )


def remove_goal(user_id: str, area: str) -> AnalysisReport:
    """Soft-delete del objetivo activo para (user_id, area)."""
    if not user_id or not area:
        return AnalysisReport(type="goal_status",
                              metrics={"action": "remove", "error": "missing_args"})
    removed = delete_user_goal(user_id, area=area)
    return AnalysisReport(
        type="goal_status",
        metrics={"action": "remove", "area": area, "removed": removed},
    )
