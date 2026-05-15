"""
Tools que los agentes LangGraph usan para invocar P1-P5 vía REST.

Cada submódulo expone funciones LangChain `@tool` que internamente hacen
peticiones HTTP a `/modules/p*/...` (los microservicios de Fase 2a).

Cumple el requisito "los agentes deberán utilizarlos a través de tools" +
"arquitectura orientada a microservicios" del enunciado: los nodos del
grafo no llaman a `analyst.monthly_summary(df, ...)` in-process, sino que
disparan un POST a `/modules/p4/monthly-summary` y reconstruyen el
`AnalysisReport` desde la respuesta JSON.
"""

from src.agents.tools import (  # noqa: F401
    analyst_tools,
    registrar_tools,
    security_tools,
)
