"""Pruebas de logs, request_id, tokens y métricas (sin llamar a Gemini)."""
import json
import logging
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

import app.main as main
import app.rag as rag
from app.observability import JsonFormatter, metrics, request_id_var
from tests.test_agent import ScriptedModel, make_client as make_agent_client
from tests.test_api import make_client as make_ask_client

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


@pytest.fixture(autouse=True)
def clean_metrics():
    metrics.reset()


def access_records(caplog):
    return [r for r in caplog.records if r.name == "app.access"]


def test_request_id_is_generated_and_safe_client_id_is_kept(monkeypatch):
    with make_ask_client(monkeypatch) as client:
        generated = client.get("/health").headers["X-Request-ID"]
        assert len(generated) == 12
        assert client.get("/health", headers={"X-Request-ID": "mi-id_01"}).headers["X-Request-ID"] == "mi-id_01"
        unsafe = client.get("/health", headers={"X-Request-ID": "mal id\nlinea"}).headers["X-Request-ID"]
        assert unsafe != "mal id\nlinea" and len(unsafe) == 12


def test_json_formatter_outputs_valid_json_with_request_id():
    record = logging.LogRecord("app.x", logging.INFO, __file__, 1, "hola", None, None)
    record.fields = {"status": 200}
    token = request_id_var.set("abc123")
    try:
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert payload["request_id"] == "abc123" and payload["status"] == 200 and payload["message"] == "hola"


def test_ask_logs_one_structured_line_with_tokens_and_without_question_text(monkeypatch, caplog):
    class UsageLLM:
        def invoke(self, messages):
            return SimpleNamespace(text="respuesta", usage_metadata=USAGE)

    caplog.set_level(logging.INFO, logger="app.access")
    with make_ask_client(monkeypatch, llm=UsageLLM()) as client:
        body = client.post("/ask", json={"question": "¿Qué es Lambda? dato-secreto"}).json()
    assert body["usage"] == {"llm_calls": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    records = [r for r in access_records(caplog) if r.fields["path"] == "/ask"]
    assert len(records) == 1
    fields = records[0].fields
    assert fields["status"] == 200 and fields["latency_ms"] >= 0
    assert fields["usage"]["total_tokens"] == 15 and fields["refused"] is False
    assert fields["question_chars"] > 0 and len(fields["question_hash"]) == 12
    assert "dato-secreto" not in json.dumps(fields)  # el texto de la pregunta no se registra


def test_question_text_is_logged_only_when_enabled(monkeypatch, caplog):
    monkeypatch.setenv("LOG_QUESTIONS", "true")
    caplog.set_level(logging.INFO, logger="app.access")
    with make_ask_client(monkeypatch) as client:
        client.post("/ask", json={"question": "pregunta visible"})
    fields = [r for r in access_records(caplog) if r.fields["path"] == "/ask"][0].fields
    assert fields["question"] == "pregunta visible"


def test_agent_sums_tokens_and_logs_tool_names_only(monkeypatch, caplog):
    script = [
        AIMessage(content="", usage_metadata=USAGE,
                  tool_calls=[{"name": "buscar_en_documentacion", "args": {"consulta": "Lambda"}, "id": "c1"}]),
        AIMessage(content="Listo.", usage_metadata=USAGE),
    ]
    caplog.set_level(logging.INFO, logger="app.access")
    with make_agent_client(monkeypatch, script) as client:
        body = client.post("/agent", json={"message": "¿Qué es Lambda?"}).json()
    assert body["usage"] == {"llm_calls": 2, "input_tokens": 20, "output_tokens": 10, "total_tokens": 30}

    fields = [r for r in access_records(caplog) if r.fields["path"] == "/agent"][0].fields
    assert fields["tools"] == ["buscar_en_documentacion"] and fields["usage"]["llm_calls"] == 2
    assert fields["thread_id"] == "default"


def test_errors_are_logged_with_status_and_error_type(monkeypatch, caplog):
    def broken(store, question, doc_filter=None):
        raise RuntimeError("RESOURCE_EXHAUSTED: cuota")

    caplog.set_level(logging.INFO, logger="app.access")
    monkeypatch.setattr(main, "answer_question", broken)
    with make_ask_client(monkeypatch) as client:
        assert client.post("/ask", json={"question": "¿Qué es Lambda?"}).status_code == 429
    fields = [r for r in access_records(caplog) if r.fields["path"] == "/ask"][0].fields
    assert fields["status"] == 429 and fields["error"] == "RuntimeError"


def test_metrics_endpoint_counts_requests_tokens_and_refusals(monkeypatch):
    class UsageLLM:
        def invoke(self, messages):
            return SimpleNamespace(text="respuesta", usage_metadata=USAGE)

    with make_ask_client(monkeypatch, llm=UsageLLM()) as client:
        client.post("/ask", json={"question": "¿Qué es Lambda?"})          # respondida
        monkeypatch.setenv("MIN_SCORE", "2")                                  # nada supera el umbral
        client.post("/ask", json={"question": "¿Capital de Francia?"})     # rechazada sin llamar al modelo
        client.post("/ask", json={"question": "a"})                           # 422
        snapshot = client.get("/metrics").json()

    ask = snapshot["endpoints"]["/ask"]
    assert ask["requests"] == 3 and ask["errors"] == 1 and ask["p95_ms"] >= ask["avg_ms"] * 0  # p95 presente
    assert snapshot["tokens"]["total_tokens"] == 15 and snapshot["tokens"]["llm_calls"] == 1
    assert snapshot["answers"] == {"answered": 1, "refused": 1, "refusal_rate": 0.5}


def test_unknown_paths_are_grouped_to_limit_cardinality(monkeypatch):
    with make_ask_client(monkeypatch) as client:
        client.get("/ruta-inventada-1")
        client.get("/ruta-inventada-2")
        endpoints = client.get("/metrics").json()["endpoints"]
    assert endpoints["other"]["requests"] == 2 and "/ruta-inventada-1" not in endpoints
