"""
Tests del routing del orquestador (LangGraph).

Mockeamos el LLM porque pegarle a Groq en cada test sería caro y lento.
El mock implementa `with_structured_output()` (canal del router) y `invoke()`
(canal del narrator) para reproducir el flujo completo.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agents.contracts import OrchestratorDecision
from src.agents.orchestrator.graph import build_graph


class _FakeStructured:
    def __init__(self, decisions):
        self._decisions = list(decisions)
        self.calls = []

    def invoke(self, messages, **_kw):
        self.calls.append(messages)
        if not self._decisions:
            raise RuntimeError("Sin decisiones programadas")
        return self._decisions.pop(0)


class _FakeLLM:
    """Canal `structured` para el router + canal plano para el narrator."""

    def __init__(self, decisions=None, narrations=None):
        self._structured = _FakeStructured(decisions or [])
        self._narrations = list(narrations or [])
        self.narration_calls = []

    def with_structured_output(self, _schema):
        return self._structured

    def invoke(self, messages, **_kw):
        self.narration_calls.append(messages)
        if not self._narrations:
            raise RuntimeError("Sin narraciones programadas")
        return AIMessage(content=self._narrations.pop(0))


@pytest.fixture
def install_fake_llm(monkeypatch):
    def _install(decisions=None, narrations=None):
        fake = _FakeLLM(decisions=decisions, narrations=narrations)
        monkeypatch.setattr("src.agents.orchestrator.nodes.get_llm", lambda **kw: fake)
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


# Tests

def test_router_responds_directly_without_delegating(install_fake_llm):
    """Pregunta trivial → router decide respond_final sin llamar a sub-agentes."""
    fake = install_fake_llm(
        decisions=[OrchestratorDecision(action="respond_final",
                                        user_message="Hola, ¿en qué te ayudo?")],
    )

    graph = build_graph()
    final = graph.invoke(_initial_state("hola"),
                         config={"configurable": {"thread_id": "t1"}})

    assert len(fake._structured.calls) == 1
    assert len(fake.narration_calls) == 0  # narrator NO se llamó
    assert "ayudo" in final["messages"][-1].content.lower()


def test_router_delegates_to_analyst_and_narrates(install_fake_llm):
    """delegate_analyst → analyst rellena report → narrator lo explica."""
    fake = install_fake_llm(
        decisions=[OrchestratorDecision(action="delegate_analyst",
                                        target_op="monthly_summary")],
        narrations=["En el último mes ingresaste X y gastaste Y."],
    )
    graph = build_graph()
    final = graph.invoke(_initial_state("resume mis gastos"),
                         config={"configurable": {"thread_id": "t2"}})

    assert final["analysis_report"].type == "summary"
    assert "ingresaste" in final["messages"][-1].content
    assert len(fake.narration_calls) == 1


def test_router_can_ask_user_for_clarification(install_fake_llm):
    """ask_user termina el grafo sin pasar por el narrator."""
    fake = install_fake_llm(
        decisions=[OrchestratorDecision(action="ask_user",
                                        user_message="¿De qué mes quieres el resumen?")],
    )
    graph = build_graph()
    final = graph.invoke(_initial_state("dime el resumen"),
                         config={"configurable": {"thread_id": "t3"}})

    assert final["last_decision"].action == "ask_user"
    assert len(fake.narration_calls) == 0
    assert "mes" in final["messages"][-1].content.lower()
