"""
Motor de analytics financiero (origen: P4_AP-IA/src/features/analytics.py).

Trabaja sobre un DataFrame con las columnas:
  - Description (str)
  - Date (str DD/MM/YYYY) o Date_parsed (datetime)
  - Amount_clean (float, parseado desde 'XX,XX€')
  - Area (str, puede contener varias separadas por coma)
  - Type ('Income' | 'Expenses')
  - Year, Month, YearMonth (Period[M]) — añadidas por el loader

Las funciones devuelven dicts con métricas; el agente Analyst las envuelve
en `AnalysisReport` para entregar al Orquestador.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_monthly_summary(
    df: pd.DataFrame, year: int | None = None, month: int | None = None
) -> dict:
    """Resumen del mes: ingresos, gastos, ahorro, breakdown por Área.

    Si no se especifica year/month, usa el mes completo más reciente.
    """
    if year and month:
        mask = (df["Year"] == year) & (df["Month"] == month)
        month_df = df[mask]
    else:
        latest_period = df["YearMonth"].max()
        month_df = df[df["YearMonth"] == latest_period]

    expenses = month_df[month_df["Type"] == "Expenses"]
    income = month_df[month_df["Type"] == "Income"]

    total_inc = income["Amount_clean"].sum()
    total_exp = expenses["Amount_clean"].sum()

    breakdown = (
        expenses.groupby("Area")["Amount_clean"]
        .sum()
        .sort_values(ascending=False)
        .to_dict()
    )

    return {
        "period": str(month_df["YearMonth"].iloc[0]) if not month_df.empty else "N/A",
        "income": float(total_inc),
        "expenses": float(total_exp),
        "net_savings": float(total_inc - total_exp),
        "savings_rate": float((total_inc - total_exp) / total_inc * 100) if total_inc > 0 else 0.0,
        "n_transactions": int(len(month_df)),
        "breakdown": {k: float(v) for k, v in breakdown.items()},
    }


def compute_spending_trends(df: pd.DataFrame, n_months: int = 6) -> dict:
    """Tendencias de gasto mensual: totales y cambios porcentuales."""
    expenses = df[df["Type"] == "Expenses"]
    monthly_totals = (
        expenses.groupby("YearMonth")["Amount_clean"]
        .sum()
        .sort_index(ascending=False)
        .head(n_months)
    )

    pct_changes = monthly_totals.pct_change(periods=-1) * 100

    recent_periods = monthly_totals.index[:n_months]
    category_trends = {}
    for area in expenses["Area"].unique():
        area_monthly = (
            expenses[expenses["Area"] == area]
            .groupby("YearMonth")["Amount_clean"]
            .sum()
        )
        area_recent = area_monthly[area_monthly.index.isin(recent_periods)]
        if len(area_recent) >= 2:
            first_half = area_recent.iloc[len(area_recent) // 2:].mean()
            second_half = area_recent.iloc[:len(area_recent) // 2].mean()
            if first_half > 0:
                change = (second_half - first_half) / first_half * 100
                category_trends[area] = round(float(change), 1)

    return {
        "monthly_totals": {str(k): round(float(v), 2) for k, v in monthly_totals.items()},
        "pct_changes": {str(k): round(float(v), 1) for k, v in pct_changes.dropna().items()},
        "category_trends": category_trends,
    }


def compute_category_breakdown(df: pd.DataFrame, period: str | None = None) -> dict:
    """Desglose de gastos por Área: absoluto y porcentaje.

    period: 'YYYY-MM' para un mes específico, None para todo el histórico.
    """
    expenses = df[df["Type"] == "Expenses"]
    if period:
        expenses = expenses[expenses["YearMonth"] == pd.Period(period, freq="M")]

    total = expenses["Amount_clean"].sum()
    breakdown = expenses.groupby("Area")["Amount_clean"].agg(["sum", "count", "mean"])
    breakdown = breakdown.sort_values("sum", ascending=False)
    breakdown["pct"] = breakdown["sum"] / total * 100 if total > 0 else 0

    return {
        "total_expenses": round(float(total), 2),
        "categories": {
            area: {
                "total": round(float(row["sum"]), 2),
                "count": int(row["count"]),
                "mean": round(float(row["mean"]), 2),
                "pct": round(float(row["pct"]), 1),
            }
            for area, row in breakdown.iterrows()
        },
    }


def compute_savings_rate(df: pd.DataFrame, n_months: int = 6) -> dict:
    """Tasa de ahorro mensual: (ingreso - gasto) / ingreso."""
    monthly = (
        df.groupby(["YearMonth", "Type"])["Amount_clean"]
        .sum()
        .unstack(fill_value=0)
    )

    if "Income" not in monthly.columns:
        monthly["Income"] = 0
    if "Expenses" not in monthly.columns:
        monthly["Expenses"] = 0

    monthly = monthly.sort_index(ascending=False).head(n_months)
    monthly["savings"] = monthly["Income"] - monthly["Expenses"]
    monthly["rate"] = np.where(
        monthly["Income"] > 0,
        monthly["savings"] / monthly["Income"] * 100,
        0,
    )

    avg_rate = monthly["rate"].mean()

    return {
        "monthly_rates": {str(k): round(float(row["rate"]), 1) for k, row in monthly.iterrows()},
        "average_rate": round(float(avg_rate), 1),
        "monthly_savings": {str(k): round(float(row["savings"]), 2) for k, row in monthly.iterrows()},
    }


def detect_anomalies(df: pd.DataFrame) -> list[dict]:
    """Detecta gastos anómalos: transacciones > media + 1.5*std por categoría."""
    expenses = df[df["Type"] == "Expenses"]
    anomalies = []

    for area in expenses["Area"].unique():
        area_data = expenses[expenses["Area"] == area]["Amount_clean"]
        if len(area_data) < 5:
            continue

        mean_val = area_data.mean()
        std_val = area_data.std()
        threshold = mean_val + 1.5 * std_val

        area_anomalies = expenses[
            (expenses["Area"] == area) & (expenses["Amount_clean"] > threshold)
        ]

        for _, row in area_anomalies.iterrows():
            anomalies.append({
                "date": row["Date"],
                "description": row["Description"],
                "amount": round(float(row["Amount_clean"]), 2),
                "area": area,
                "mean": round(float(mean_val), 2),
                "threshold": round(float(threshold), 2),
            })

    return sorted(anomalies, key=lambda x: x["amount"], reverse=True)


def compute_recurring_expenses(df: pd.DataFrame) -> list[dict]:
    """Detecta gastos recurrentes (suscripciones, facturas) por patrón de descripción."""
    expenses = df[df["Type"] == "Expenses"]
    keywords = [
        "Gimnasio", "Netflix", "Spotify", "Seguro", "Gas",
        "Internet", "Agua", "Telefono", "HBO", "Amazon Prime",
    ]

    recurring = []
    for kw in keywords:
        matches = expenses[expenses["Description"].str.contains(kw, case=False, na=False)]
        if len(matches) >= 2:
            recurring.append({
                "name": kw,
                "avg_amount": round(float(matches["Amount_clean"].mean()), 2),
                "n_payments": int(len(matches)),
                "n_months": int(matches["YearMonth"].nunique()),
                "total": round(float(matches["Amount_clean"].sum()), 2),
            })

    return sorted(recurring, key=lambda x: x["avg_amount"], reverse=True)
