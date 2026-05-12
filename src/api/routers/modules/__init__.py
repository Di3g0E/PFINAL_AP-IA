"""
Routers REST que exponen P1-P5 como microservicios.

Cada submódulo (p1.py ... p5.py) define un `router: APIRouter` con prefijo
`/modules/pX` que reexpone la lógica de la práctica correspondiente.

  - P1: predicción temporal (RF, HistGradientBoosting, ARIMA).
  - P2: clasificación de categoría (Area) por descripción.
  - P3: OCR (PaddleOCR) de tickets/facturas.
  - P4: analytics + objetivos (monthly_summary, trends, goals…).
  - P5: validación de transacciones (anomalías) y biometría facial.

Los routers son wrappers finos sobre los módulos existentes en
`src/agents/...` — no duplican lógica. Los agentes LangGraph podrán
llamar a estos endpoints como tools, satisfaciendo el requisito de
"arquitectura orientada a microservicios" del enunciado.
"""
