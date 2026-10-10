"""Pruebas de la API sin llamar a Gemini: se usan embeddings y modelo falsos."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.vectorstores import InMemoryVectorStore

import app.main as main
import app.rag as rag


def fake_store():
    store = InMemoryVectorStore(DeterministicFakeEmbedding(size=32))
    store.add_documents([
        Document(page_content="Lambda runs code without servers", metadata={"source": "https://example.com/lambda"}),
        Document(page_content="IAM controls access to resources", metadata={"source": "https://example.com/iam"}),
    ])
    return store


class FakeLLM:
    def invoke(self, messages):
        return SimpleNamespace(text="respuesta de prueba")


class CountingLLM(FakeLLM):
    calls = 0

    def invoke(self, messages):
        CountingLLM.calls += 1
        return super().invoke(messages)


def make_client(monkeypatch, min_score="-1", llm=None):
    # Los embeddings falsos dan similitudes arbitrarias: se desactiva el umbral salvo que se pida.
    monkeypatch.setenv("MIN_SCORE", min_score)
    monkeypatch.setattr(main, "get_store", fake_store)
    monkeypatch.setattr(rag, "_get_llm", lambda: llm or FakeLLM())
    return TestClient(main.app)


def test_health(monkeypatch):
    with make_client(monkeypatch) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["chunks"] == 2


def test_ask_returns_answer_and_sources(monkeypatch):
    with make_client(monkeypatch) as client:
        response = client.post("/ask", json={"question": "What is Lambda?"})
        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == "respuesta de prueba"
        assert len(body["sources"]) >= 1
        assert body["sources"][0]["url"].startswith("https://example.com")


def test_ask_rejects_short_question(monkeypatch):
    with make_client(monkeypatch) as client:
        assert client.post("/ask", json={"question": "a"}).status_code == 422


def test_ask_without_relevant_fragments_skips_model(monkeypatch):
    CountingLLM.calls = 0
    with make_client(monkeypatch, min_score="2", llm=CountingLLM()) as client:
        body = client.post("/ask", json={"question": "What is the capital of France?"}).json()
        assert body["answer"] == rag.NO_ANSWER
        assert body["sources"] == []
        assert CountingLLM.calls == 0


def test_ask_hides_sources_when_model_says_it_does_not_know(monkeypatch):
    class DontKnowLLM:
        def invoke(self, messages):
            return SimpleNamespace(text=rag.NO_ANSWER)

    with make_client(monkeypatch, llm=DontKnowLLM()) as client:
        body = client.post("/ask", json={"question": "What is Lambda?"}).json()
        assert body["answer"] == rag.NO_ANSWER
        assert body["sources"] == []
