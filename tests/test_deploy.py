"""Pruebas de lo necesario para el despliegue público: página raíz y /docs desactivable."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from tests.test_api import make_client


def test_root_is_public_and_describes_the_service(monkeypatch):
    with make_client(monkeypatch) as client:
        anonymous = TestClient(main.app)  # sin cabecera X-API-Key
        body = anonymous.get("/").json()
    assert body["service"] == "enterprise-ai-agent" and body["status"] == "ok"
    assert "X-API-Key" in body["auth"]


def test_docs_can_be_disabled_with_an_environment_variable(monkeypatch):
    monkeypatch.setenv("DOCS_ENABLED", "false")
    kwargs = main.docs_kwargs()
    assert kwargs == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    assert TestClient(FastAPI(**kwargs)).get("/docs").status_code == 404

    monkeypatch.setenv("DOCS_ENABLED", "true")
    assert main.docs_kwargs() == {}
    assert TestClient(FastAPI(**main.docs_kwargs())).get("/docs").status_code == 200


def test_root_accepts_head_requests_used_by_platform_probes(monkeypatch):
    with make_client(monkeypatch):
        assert TestClient(main.app).head("/").status_code == 200
