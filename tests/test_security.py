"""Pruebas de seguridad: API keys, acceso por grupo, aislamiento de hilos y límite de uso."""
import json
import logging
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import app.main as main
import app.rag as rag
from app.security import (
    RateLimiter,
    User,
    UsersFileError,
    add_user,
    authenticate,
    group_for_source,
    hash_key,
    load_users,
    make_doc_filter,
)
from app.tools import make_tools
from tests.helpers import ACCESS_MAP, ADMIN, ADMIN_KEY, patch_security
from tests.test_agent import ScriptedModel, get_tool, tool_call
from tests.test_api import fake_store

ANA = User("ana", hash_key("clave-ana"), ("compute",), False)   # solo documentos de compute
BEA = User("bea", hash_key("clave-bea"), ("compute",), False)
HEADERS_ANA = {"X-API-Key": "clave-ana"}
HEADERS_BEA = {"X-API-Key": "clave-bea"}
HEADERS_ADMIN = {"X-API-Key": ADMIN_KEY}


class CapturingLLM:
    """Guarda el prompt que recibe, para comprobar qué documentos llegaron al modelo."""

    def __init__(self):
        self.prompts = []

    def invoke(self, messages):
        self.prompts.append(str(messages))
        return SimpleNamespace(text="respuesta")


class RecordingModel(ScriptedModel):
    """Modelo falso que anota cuántos mensajes recibe en cada llamada."""

    seen: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(len(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)


def client_with(monkeypatch, users, llm=None, env=None):
    monkeypatch.setenv("MIN_SCORE", "-1")
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(main, "get_store", fake_store)
    monkeypatch.setattr(rag, "_get_llm", lambda: llm or CapturingLLM())
    patch_security(monkeypatch, users)
    return TestClient(main.app)


# --- Autenticación ---------------------------------------------------------------------

def test_protected_endpoints_reject_missing_and_wrong_keys(monkeypatch):
    with client_with(monkeypatch, [ADMIN]) as client:
        for path, body in [("/ask", {"question": "¿Qué es Lambda?"}), ("/agent", {"message": "Hola"})]:
            assert client.post(path, json=body).status_code == 401
            assert client.post(path, json=body, headers={"X-API-Key": "incorrecta"}).status_code == 401
        assert client.get("/metrics").status_code == 401
        assert client.get("/health").status_code == 200  # el health check queda abierto


def test_server_fails_closed_without_configured_users(monkeypatch):
    with client_with(monkeypatch, []) as client:
        response = client.post("/ask", json={"question": "¿Qué es Lambda?"}, headers=HEADERS_ADMIN)
        assert response.status_code == 401


def test_metrics_requires_admin(monkeypatch):
    with client_with(monkeypatch, [ADMIN, ANA]) as client:
        assert client.get("/metrics", headers=HEADERS_ANA).status_code == 403
        assert client.get("/metrics", headers=HEADERS_ADMIN).status_code == 200


def test_key_hashing_and_user_creation(tmp_path):
    path = tmp_path / "users.json"
    key = add_user("carla", ["storage"], path=path)
    stored = path.read_text(encoding="utf-8")
    assert key not in stored and hash_key(key) in stored          # solo se guarda el hash
    users = load_users(path)
    assert authenticate(key, users).name == "carla" and authenticate("otra", users) is None
    assert authenticate(None, users) is None
    try:
        add_user("carla", [], path=path)
        raise AssertionError("debía rechazar el duplicado")
    except ValueError:
        pass
    try:
        add_user("mal:nombre", [], path=path)
        raise AssertionError("debía rechazar el nombre")
    except ValueError:
        pass


# --- Acceso por grupo de documentos ----------------------------------------------------

def test_group_mapping_and_filters():
    assert group_for_source("https://docs.aws.amazon.com/IAM/latest/UserGuide/introduction.html",
                            {"/IAM/": "security"}) == "security"
    assert group_for_source("https://otro.com/x", ACCESS_MAP) == "unmapped"
    assert make_doc_filter(["*"], ACCESS_MAP) is None and make_doc_filter(None, ACCESS_MAP) is None


def test_user_never_sends_forbidden_documents_to_the_model_in_ask(monkeypatch):
    llm = CapturingLLM()
    with client_with(monkeypatch, [ADMIN, ANA], llm=llm) as client:
        restricted = client.post("/ask", json={"question": "¿Cómo controla IAM el acceso?"}, headers=HEADERS_ANA).json()
        assert "IAM controls access" not in llm.prompts[-1]      # el texto prohibido no llegó al modelo
        assert all("/iam" not in s["url"] for s in restricted["sources"])

        client.post("/ask", json={"question": "¿Cómo controla IAM el acceso?"}, headers=HEADERS_ADMIN)
        assert "IAM controls access" in llm.prompts[-1]          # el administrador sí lo recibe


def test_agent_search_respects_user_groups(monkeypatch):
    def script():
        return [tool_call("buscar_en_documentacion", {"consulta": "iam lambda"}), AIMessage(content="ok")]

    for headers, expected in [(HEADERS_ANA, {"lambda"}), (HEADERS_ADMIN, {"lambda", "iam"})]:
        model = ScriptedModel(script=script())
        with client_with(monkeypatch, [ADMIN, ANA], llm=model) as client:
            body = client.post("/agent", json={"message": "Busca sobre IAM y Lambda"}, headers=headers).json()
        urls = {s["url"].rsplit("/", 1)[-1] for s in body["sources"]}
        assert urls == expected


def test_status_tool_respects_user_groups():
    tools = make_tools(fake_store(), access_map=ACCESS_MAP)
    status = get_tool(tools, "consultar_estado_servicio")
    restricted = {"configurable": {"groups": ["compute"]}}
    assert "No tienes acceso" in status.invoke({"servicio": "iam"}, config=restricted)
    assert json.loads(status.invoke({"servicio": "lambda"}, config=restricted))["estado"] == "operativo"
    assert json.loads(status.invoke({"servicio": "iam"}, config={"configurable": {"groups": ["*"]}}))["estado"]


def test_tool_config_parameter_is_hidden_from_the_model():
    tools = make_tools(fake_store(), access_map=ACCESS_MAP)
    assert all("config" not in tool.args for tool in tools)


def test_threads_are_isolated_between_users(monkeypatch):
    RecordingModel.seen = []
    model = RecordingModel(script=[AIMessage(content="uno"), AIMessage(content="dos"), AIMessage(content="tres")])
    with client_with(monkeypatch, [ANA, BEA], llm=model) as client:
        body = {"message": "Hola, soy yo", "thread_id": "mismo-hilo"}
        client.post("/agent", json=body, headers=HEADERS_ANA)
        client.post("/agent", json=body, headers=HEADERS_BEA)   # mismo thread_id, otro usuario
        client.post("/agent", json=body, headers=HEADERS_ANA)   # el mismo usuario sí continúa su hilo
    first_ana, first_bea, second_ana = model.seen
    assert first_bea == first_ana          # Bea empieza de cero: no ve el historial de Ana
    assert second_ana > first_ana          # Ana conserva su memoria (control positivo)


# --- Límite de uso ---------------------------------------------------------------------

def test_rate_limit_per_user_with_retry_after(monkeypatch):
    with client_with(monkeypatch, [ANA, BEA], env={"RATE_LIMIT_PER_MIN": "2"}) as client:
        body = {"question": "¿Qué es Lambda?"}
        assert client.post("/ask", json=body, headers=HEADERS_ANA).status_code == 200
        assert client.post("/ask", json=body, headers=HEADERS_ANA).status_code == 200
        blocked = client.post("/ask", json=body, headers=HEADERS_ANA)
        assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) >= 1
        assert client.post("/ask", json=body, headers=HEADERS_BEA).status_code == 200  # otro usuario no se ve afectado


def test_rate_limiter_window_expires():
    now = [0.0]
    limiter = RateLimiter(limit=2, window=60, clock=lambda: now[0])
    assert limiter.check("u") is None and limiter.check("u") is None
    assert limiter.check("u") is not None
    now[0] = 61
    assert limiter.check("u") is None


# --- Archivo de usuarios inválido --------------------------------------------------------

def test_invalid_users_file_gives_a_clear_error_without_leaking_content(tmp_path):
    cases = {"vacio.json": "", "texto.json": "eaa_clave-secreta-pegada-por-error", "incompleto.json": '{"users": [{"name": "x"}]}'}
    for name, content in cases.items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        try:
            load_users(path)
            raise AssertionError("debía fallar")
        except UsersFileError as error:
            message = str(error)
        assert name in message and "bytes" in message
        assert "clave-secreta" not in message          # nunca se imprime el contenido
    assert "0 bytes" in _error_for(tmp_path / "vacio.json") and "'e'" in _error_for(tmp_path / "texto.json")


def _error_for(path):
    try:
        load_users(path)
    except UsersFileError as error:
        return str(error)
    return ""


def test_server_starts_without_users_when_the_file_is_invalid(monkeypatch, tmp_path, caplog):
    bad_file = tmp_path / "users.json"
    bad_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("USERS_FILE", str(bad_file))
    monkeypatch.setattr(main, "get_store", fake_store)
    monkeypatch.setattr(main, "load_access_map", lambda: ACCESS_MAP)
    caplog.set_level(logging.INFO, logger="app")
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200       # el servicio queda visible
        assert client.post("/ask", json={"question": "¿Qué es Lambda?"}, headers=HEADERS_ADMIN).status_code == 401
    assert any("no es válido" in r.getMessage() and "0 bytes" in r.getMessage() for r in caplog.records)
    loaded = [r for r in caplog.records if r.getMessage() == "usuarios cargados"][0]
    assert loaded.fields["exists"] is True and loaded.fields["bytes"] == 0 and loaded.fields["count"] == 0


def test_startup_logs_how_many_users_were_loaded(monkeypatch, tmp_path, caplog):
    users_file = tmp_path / "users.json"
    add_user("demo", ["compute"], path=users_file)
    monkeypatch.setenv("USERS_FILE", str(users_file))
    monkeypatch.setattr(main, "get_store", fake_store)
    monkeypatch.setattr(main, "load_access_map", lambda: ACCESS_MAP)
    caplog.set_level(logging.INFO, logger="app")
    with TestClient(main.app):
        pass
    loaded = [r for r in caplog.records if r.getMessage() == "usuarios cargados"][0]
    assert loaded.fields["count"] == 1 and loaded.fields["bytes"] > 0
