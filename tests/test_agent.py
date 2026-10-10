"""Pruebas del agente sin llamar a Gemini: un modelo falso sigue un guion."""
import json

from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import app.main as main
import app.rag as rag
from app.tools import make_tools
from tests.test_api import fake_store


class ScriptedModel(BaseChatModel):
    """Modelo falso: devuelve en orden los mensajes del guion."""

    script: list
    position: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.script[self.position]
        self.position += 1
        return ChatResult(generations=[ChatGeneration(message=message)])


def tool_call(name, args, call_id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def make_client(monkeypatch, script):
    monkeypatch.setenv("MIN_SCORE", "-1")  # embeddings falsos: sin umbral
    monkeypatch.setattr(main, "get_store", fake_store)
    monkeypatch.setattr(rag, "_get_llm", lambda: ScriptedModel(script=script))
    return TestClient(main.app)


def get_tool(tools, name):
    return next(t for t in tools if t.name == name)


def test_agent_searches_docs_and_returns_sources(monkeypatch):
    script = [tool_call("buscar_en_documentacion", {"consulta": "Lambda"}), AIMessage(content="Lambda ejecuta código sin servidores.")]
    with make_client(monkeypatch, script) as client:
        body = client.post("/agent", json={"message": "¿Qué es Lambda?"}).json()
        assert body["answer"] == "Lambda ejecuta código sin servidores."
        assert body["tools_used"][0]["tool"] == "buscar_en_documentacion"
        assert body["sources"] and body["sources"][0]["url"].startswith("https://example.com")


def test_agent_answers_without_tools(monkeypatch):
    with make_client(monkeypatch, [AIMessage(content="Hola, ¿en qué te ayudo?")]) as client:
        body = client.post("/agent", json={"message": "Hola"}).json()
        assert body["tools_used"] == [] and body["sources"] == []


def test_agent_rejects_bad_thread_id(monkeypatch):
    with make_client(monkeypatch, []) as client:
        response = client.post("/agent", json={"message": "Hola", "thread_id": "../etc"})
        assert response.status_code == 422


def test_service_status_known_and_unknown():
    tools = make_tools(fake_store())
    status = get_tool(tools, "consultar_estado_servicio")
    assert json.loads(status.invoke({"servicio": "ECS"}))["estado"] == "degradado"
    assert "desconocido" in status.invoke({"servicio": "inexistente"})


def test_ticket_tool_writes_draft(tmp_path):
    tools = make_tools(fake_store(), tickets_dir=tmp_path)
    result = get_tool(tools, "crear_ticket_borrador").invoke(
        {"titulo": "ECS lento", "descripcion": "Latencia alta en producción", "prioridad": "alta"}
    )
    files = list(tmp_path.glob("TCK-*.json"))
    assert len(files) == 1 and files[0].stem in result
    ticket = json.loads(files[0].read_text(encoding="utf-8"))
    assert ticket["estado"] == "borrador" and ticket["prioridad"] == "alta"


def test_ticket_tool_accepts_string_path(tmp_path):
    tools = make_tools(fake_store(), tickets_dir=str(tmp_path))
    get_tool(tools, "crear_ticket_borrador").invoke({"titulo": "t", "descripcion": "d"})
    assert len(list(tmp_path.glob("TCK-*.json"))) == 1
