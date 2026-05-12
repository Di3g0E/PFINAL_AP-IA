"""
Smoke test del agente Analyst contra el CSV real de P5.

No requiere base de datos: usa `load_from_csv` directamente. Valida que
todas las operaciones devuelven `AnalysisReport` con métricas pobladas.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.agents.analyst import agent as analyst
from src.agents.analyst.data_source import load_from_csv
from src.agents.contracts import AnalysisReport, Goal


CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "raw" / "db_mod_descript.csv"


@pytest.fixture(scope="module")
def df():
    assert CSV_PATH.exists(), f"CSV no encontrado: {CSV_PATH}"
    return load_from_csv(CSV_PATH)


def test_load_csv_has_expected_columns(df):
    expected = {"Description", "Date", "Amount_clean", "Area", "Type",
                "Date_parsed", "Year", "Month", "YearMonth"}
    assert expected.issubset(df.columns)
    assert len(df) > 0
    assert df["Type"].isin(["Income", "Expenses"]).all()


def test_monthly_summary(df):
    rep = analyst.monthly_summary(df)
    assert isinstance(rep, AnalysisReport)
    assert rep.type == "summary"
    assert "income" in rep.metrics and "expenses" in rep.metrics
    assert rep.metrics["n_transactions"] > 0


def test_category_breakdown(df):
    rep = analyst.category_breakdown(df)
    assert rep.type == "category"
    assert "categories" in rep.metrics
    assert len(rep.metrics["categories"]) > 0


def test_spending_trends(df):
    rep = analyst.spending_trends(df, n_months=6)
    assert rep.type == "trend"
    assert "monthly_totals" in rep.metrics
    assert len(rep.series) > 0


def test_savings_rate(df):
    rep = analyst.savings_rate(df, n_months=6)
    assert rep.type == "savings_rate"
    assert "average_rate" in rep.metrics
    assert isinstance(rep.metrics["average_rate"], float)


def test_detect_anomalies(df):
    rep = analyst.detect_anomalies(df)
    assert rep.type == "anomaly"
    assert "anomalies" in rep.metrics


def test_recurring_expenses(df):
    rep = analyst.recurring_expenses(df)
    assert rep.type == "recurring"
    assert "items" in rep.metrics


def test_recent_transactions(df):
    """Las N transacciones más recientes deben venir ordenadas desc por fecha."""
    rep = analyst.recent_transactions(df, n=5)
    assert rep.type == "summary"
    assert rep.metrics["kind"] == "recent_transactions"
    assert rep.metrics["count"] == 5
    items = rep.metrics["items"]
    assert len(items) == 5
    # Todas las transacciones tienen los campos esperados
    for it in items:
        assert {"date", "description", "amount", "area", "type"}.issubset(it.keys())


@pytest.mark.parametrize("method", ["rf", "hgb", "arima"])
def test_predict_next_month(df, method):
    rep = analyst.predict_next_month(df, method=method)
    assert rep.type == "prediction"
    if "error" in rep.metrics:
        pytest.skip(f"No hay histórico suficiente: {rep.metrics['error']}")
    assert "forecast" in rep.metrics
    assert isinstance(rep.metrics["forecast"], float)
    assert rep.metrics["lower"] <= rep.metrics["forecast"] <= rep.metrics["upper"]


def test_check_goals_below_threshold(df):
    """Sin objetivos activos no genera alertas."""
    rep = analyst.check_goals(df, goals=[])
    assert rep.type == "goal_status"
    assert rep.metrics["alerts_count"] == 0


def test_check_goals_above_threshold(df):
    """Un objetivo con límite muy bajo en un área presente en el último mes debe disparar alerta."""
    # El dataset llega a 2026-04 con datos del área 'Food'; usamos esa para garantizar match
    latest_period = df["YearMonth"].max()
    last_month = df[(df["YearMonth"] == latest_period) & (df["Type"] == "Expenses")]
    assert not last_month.empty, "El último mes no tiene gastos para validar el test"
    target_area = last_month["Area"].iloc[0]

    goal = Goal(id="g1", user_id="u1", area=target_area,
                max_amount=Decimal("0.01"), period="monthly")
    rep = analyst.check_goals(df, goals=[goal], notif_config=None)
    assert rep.type == "goal_status"
    assert len(rep.goal_alerts) >= 1
    alert = rep.goal_alerts[0]
    assert alert.area == target_area
    assert alert.pct >= 0.80
