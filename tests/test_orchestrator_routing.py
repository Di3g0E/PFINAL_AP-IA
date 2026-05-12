"""
Tests del routing del Orquestador con LLM **mockeado** (no llama a Groq/OpenAI/etc.).

El nuevo `orchestrator_node` tiene dos modos:
  - **Router**: structured output `OrchestratorDecision`. Se invoca cuando NO
    hay datos de sub-agente.
  - **Narrator**: invoke plano que produce texto. Se invoca cuando SÍ hay datos.

El mock implementa ambos: `with_structured_output(...)` devuelve la siguiente
decisión programada y `invoke(...)` (sin schema) devuelve la siguiente narración.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
import pytest

from src.agents.contracts import OrchestratorDecision
from src.agents.orchestrator.graph import build_graph


class _FakeStructuredLLM:
    """Devuelve la siguiente decisión programada en cada `invoke`."""

    def __init__(self, decisions: list[OrchestratorDecision]):
        self._decisions = list(decisions)
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(messages)
        if not self._decisions:
            raise RuntimeError("FakeStructuredLLM: no quedan decisiones programadas")
        return self._decisions.pop(0)


class _FakeLLM:
    """LLM fake con dos canales: structured (router) y plain (narrator)."""

    def __init__(self,
                 decisions: list[OrchestratorDecision] | None = None,
                 narrations: list[str] | None = None):
        self._structured = _FakeStructuredLLM(decisions or [])
        self._narrations = list(narrations or [])
        self.narration_calls: list[list] = []

    def with_structured_output(self, schema):
        del schema  # no inspeccionamos el schema en el mock
        return self._structured

    def invoke(self, messages):
        """Canal de narración: devuelve un AIMessage con el siguiente texto."""
        self.narration_calls.append(messages)
        if not self._narrations:
            raise RuntimeError("FakeLLM: no quedan narraciones programadas")
        return AIMessage(content=self._narrations.pop(0))


@pytest.fixture
def install_fake_llm(monkeypatch):
    """Devuelve una función para programar las respuestas del LLM mock."""
    def _install(decisions=None, narrations=None) -> _FakeLLM:
        fake = _FakeLLM(decisions=decisions, narrations=narrations)
        monkeypatch.setattr("src.agents.orchestrator.nodes.get_llm",
                            lambda **kw: fake)
        return fake
    return _install


def _initial_state(message: str) -> dict:
    return {
        "messages": [HumanMessage(content=message)],
        "user_id": "test-user",
        "session_id": "session-1",
        "iterations": 0,
        "analysis_report": None,
        "security_verdict": None,
        "registry_result": None,
        "last_decision": None,
    }


def _config(thread: str = "test-user:session-1") -> dict:
    return {"configurable": {"thread_id": thread}}


# Tests

def test_route_to_analyst_then_narrate(install_fake_llm):
    """
    Flujo: usuario pide resumen → router decide delegate_analyst → analyst_node
    rellena analysis_report → orchestrator detecta has_data y narra (canal plano).
    """
    fake = install_fake_llm(
        decisions=[OrchestratorDecision(action="delegate_analyst",
                                        target_op="monthly_summary",
                                        target_args={})],
        narrations=["En el último mes ingresaste X y gastaste Y."],
    )

    graph = build_graph()
    final = graph.invoke(
        _initial_state("Resume mis gastos del último mes"),
        config=_config(),
    )

    # Router fue llamado 1 vez, narrator también 1 vez
    assert len(fake._structured.calls) == 1
    assert len(fake.narration_calls) == 1

    assert final.get("analysis_report") is not None
    assert final["analysis_report"].type == "summary"
    assert final["last_decision"].action == "respond_final"

    last_msg = final["messages"][-1]
    assert "ingresaste" in last_msg.content


def test_router_decides_respond_final_directly(install_fake_llm):
    """Pregunta trivial sin necesidad de sub-agente: router responde directo."""
    fake = install_fake_llm(
        decisions=[OrchestratorDecision(action="respond_final",
                                        user_message="Hola, ¿en qué te ayudo?")],
        narrations=[],   # no debería llamarse al narrator
    )

    graph = build_graph()
    final = graph.invoke(_initial_state("hola"), config=_config())

    # Solo se llamó al canal structured (1 vez); narrator nunca
    assert len(fake._structured.calls) == 1
    assert len(fake.narration_calls) == 0

    last_msg = final["messages"][-1]
    assert "ayudo" in last_msg.content.lower()


def test_route_to_security_stub_then_narrate(install_fake_llm):
    """Security stub rellena verdict → narrator explica al usuario."""
    fake = install_fake_llm(
        decisions=[OrchestratorDecision(action="delegate_security", target_op="login_user")],
        narrations=["El login se implementa en una próxima iteración."],
    )

    graph = build_graph()
    final = graph.invoke(_initial_state("Quiero hacer login"), config=_config())

    verdict = final.get("security_verdict")
    assert verdict is not None
    assert verdict.decision == "deny"
    assert "próxima" in final["messages"][-1].content.lower()
    assert len(fake.narration_calls) == 1


def test_route_to_registrar_add_manual_then_narrate(install_fake_llm):
    """El LLM emite delegate_registrar con args válidos → Registrar persiste y narra."""
    fake = install_fake_llm(
        decisions=[OrchestratorDecision(
            action="delegate_registrar",
            target_op="add_manual_transaction",
            target_args={
                "description": "Cena en pizzeria",
                "date": "2026-04-15",
                "amount": "18.50",
                "type": "Expenses",
            },
        )],
        narrations=["He registrado tu gasto de 18.50€ en categoría Food."],
    )

    graph = build_graph()
    final = graph.invoke(_initial_state("añade una cena"), config=_config(thread="reg"))

    result = final.get("registry_result")
    assert result is not None
    assert len(result.accepted) == 1
    assert result.accepted[0].source == "manual"
    assert len(fake.narration_calls) == 1


def test_route_to_registrar_invalid_op_returns_rejected(install_fake_llm):
    """Si el LLM elige una operación desconocida del Registrar, queda rechazada."""
    fake = install_fake_llm(
        decisions=[OrchestratorDecision(
            action="delegate_registrar",
            target_op="op_inexistente",
            target_args={},
        )],
        narrations=["No he podido registrar esa operación."],
    )

    graph = build_graph()
    final = graph.invoke(_initial_state("haz algo raro"), config=_config(thread="reg-bad"))

    result = final.get("registry_result")
    assert result is not None
    assert len(result.rejected) == 1
    assert "desconocida" in result.rejected[0].reason.lower()
    assert len(fake.narration_calls) == 1


def test_ask_user_terminates(install_fake_llm):
    """Router puede pedir aclaración sin delegar."""
    fake = install_fake_llm(
        decisions=[OrchestratorDecision(action="ask_user",
                                        user_message="¿De qué mes quieres el resumen?")],
        narrations=[],
    )
    graph = build_graph()
    final = graph.invoke(_initial_state("dime el resumen"), config=_config())

    assert final["last_decision"].action == "ask_user"
    assert len(fake.narration_calls) == 0
    assert "mes" in final["messages"][-1].content.lower()


def test_analyst_real_data_flow(install_fake_llm):
    """
    Verifica que analyst_node ejecute realmente la operación contra el CSV
    y que los datos lleguen al narrator (que devuelve un texto fijo en el mock).
    """
    install_fake_llm(
        decisions=[OrchestratorDecision(action="delegate_analyst",
                                        target_op="monthly_summary")],
        narrations=["resumen narrado"],
    )
    graph = build_graph()
    final = graph.invoke(_initial_state("resumen"), config=_config())

    report = final["analysis_report"]
    assert report.type == "summary"
    assert "income" in report.metrics
    assert "expenses" in report.metrics
    assert report.metrics["n_transactions"] >= 0


def test_analyst_drops_hallucinated_kwargs(install_fake_llm):
    """
    Si el LLM pasa un kwarg que la operación NO acepta (p. ej. `period` a
    `monthly_summary`), `_safe_kwargs` lo descarta y la operación se ejecuta
    con éxito. Reproduce el fallo real visto en producción con Llama 3.3.
    """
    install_fake_llm(
        decisions=[OrchestratorDecision(
            action="delegate_analyst",
            target_op="monthly_summary",
            target_args={"period": "2026-04", "foobar": 42},   # ← argumentos alucinados
        )],
        narrations=["resumen narrado"],
    )
    graph = build_graph()
    final = graph.invoke(_initial_state("resumen último mes"), config=_config(thread="halluc"))

    report = final["analysis_report"]
    # El report debe tener métricas reales, NO un campo `error`
    assert report.type == "summary"
    assert "error" not in report.metrics
    assert "income" in report.metrics


def test_iteration_limit_safety_belt(install_fake_llm, monkeypatch):
    """
    El cinturón anti-bucles dispara cuando `iterations >= MAX_ITERATIONS`
    incluso si el LLM nunca decide respond_final. Forzamos un estado con
    iteraciones altas para ejercitarlo.
    """
    # Bajamos MAX_ITERATIONS para no agotar decisiones del mock
    import src.agents.orchestrator.nodes as nodes_module
    monkeypatch.setattr(nodes_module, "MAX_ITERATIONS", 1)

    install_fake_llm(decisions=[], narrations=[])
    graph = build_graph()
    state = _initial_state("loop test")
    state["iterations"] = 5     # ya por encima del límite
    final = graph.invoke(state, config=_config(thread="iter-limit"))

    last = final["messages"][-1]
    assert "límite" in last.content.lower()
