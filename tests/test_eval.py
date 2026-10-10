"""Pruebas de las métricas de evaluación con embeddings por palabras clave (sin Gemini)."""
import json
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage
from langchain_core.vectorstores import InMemoryVectorStore

from app.rag import NO_ANSWER
from app.agent import build_agent
from evals.metrics import check_agent_case, evaluate_retrieval, render_report, run_agent_cases, run_answer_cases
from tests.test_agent import ScriptedModel, tool_call
from tests.test_api import fake_store

DATASET = json.loads((Path(__file__).resolve().parent.parent / "evals" / "dataset.json").read_text(encoding="utf-8"))


class KeywordEmbeddings(Embeddings):
    """Un eje por palabra clave y un eje extra para 'otro tema'."""

    KEYS = ["lambda", "iam", "s3"]

    def _vector(self, text: str) -> list[float]:
        found = [1.0 if key in text.lower() else 0.0 for key in self.KEYS]
        return found + [0.0 if any(found) else 1.0]

    def embed_documents(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)


def keyword_store():
    store = InMemoryVectorStore(KeywordEmbeddings())
    store.add_documents([
        Document(page_content="Lambda runs code", metadata={"source": "https://x.com/lambda/welcome.html"}),
        Document(page_content="IAM controls access", metadata={"source": "https://x.com/IAM/intro.html"}),
        Document(page_content="S3 stores objects", metadata={"source": "https://x.com/AmazonS3/welcome.html"}),
    ])
    return store


CASES = [
    {"id": "p1", "kind": "positive", "expected": "lambda", "question": "what is lambda"},
    {"id": "p2", "kind": "positive", "expected": "/iam/", "question": "how does iam work"},
    {"id": "n1", "kind": "negative", "question": "capital de francia"},
]


def test_retrieval_metrics_perfect_case():
    result = evaluate_retrieval(keyword_store(), CASES, k=2, min_score=0.6)
    summary = result["summary"]
    assert summary["hit_rate"] == 1.0 and summary["mrr"] == 1.0
    assert summary["positive_pass"] == 1.0 and summary["negative_pass"] == 1.0
    assert 0 < summary["suggested_min_score"] < 1


def test_retrieval_detects_missed_positive():
    cases = [{"id": "p3", "kind": "positive", "expected": "lambda", "question": "how to run code"}]  # sin palabra clave
    result = evaluate_retrieval(keyword_store(), cases, k=2, min_score=0.6)
    assert result["rows"][0]["ok"] is False
    assert result["summary"]["positive_pass"] == 0.0


def test_agent_case_checks():
    turn = {"answer": "La capital es París", "tools_used": [{"tool": "a", "args": {}}, {"tool": "b", "args": {}}]}
    assert check_agent_case({"expected_tools": ["a", "b"]}, turn) == []
    assert check_agent_case({"expected_tools": ["b", "a"]}, turn)          # orden incorrecto
    assert check_agent_case({"exact_tools": []}, turn)                     # usó herramientas
    assert check_agent_case({"forbidden_tools": ["b"]}, turn)
    assert check_agent_case({"answer_must_not_contain": ["parís"]}, turn)  # sin distinguir mayúsculas


def test_report_renders_tables():
    report = render_report(evaluate_retrieval(keyword_store(), CASES, k=2, min_score=0.6))
    assert "# Informe de evaluación" in report and "| p1 |" in report


def test_dataset_is_well_formed():
    ids = [c["id"] for c in DATASET["retrieval"]] + [c["id"] for c in DATASET["agent"]]
    assert len(ids) == len(set(ids))
    for case in DATASET["retrieval"]:
        assert case["kind"] in {"positive", "negative", "adjacent"}
        assert (case["kind"] != "positive") or case.get("expected")


def test_adjacent_questions_do_not_distort_threshold():
    cases = CASES + [{"id": "a1", "kind": "adjacent", "question": "lambda pricing"}]  # comparte vocabulario
    summary = evaluate_retrieval(keyword_store(), cases, k=2, min_score=0.6)["summary"]
    assert summary["adjacent_filtered"] == 0.0           # pasa el umbral
    assert summary["negative_max_score"] < 0.6           # el umbral solo se calibra con temas sin relación
    assert summary["suggested_min_score"] is not None


def test_run_answer_cases_flags_non_refusals():
    cases = [{"id": "a1", "question": "q1"}, {"id": "a2", "question": "q2"}]
    answers = {"q1": NO_ANSWER, "q2": "Estos son los pasos..."}
    rows = run_answer_cases(None, cases, lambda store, q: {"answer": answers[q], "sources": []}, pause=0)
    assert [r["ok"] for r in rows] == [True, False]
    assert "REVISAR" in render_report(None, None, rows)


def test_run_agent_cases_with_real_graph_and_string_tickets_dir(tmp_path):
    """Reproduce el caso g03: encadenar estado y ticket pasando la carpeta como str."""
    script = [
        tool_call("consultar_estado_servicio", {"servicio": "ecs"}, "c1"),
        tool_call("crear_ticket_borrador", {"titulo": "ECS lento", "descripcion": "480 ms", "prioridad": "alta"}, "c2"),
        AIMessage(content="Ticket creado."),
    ]
    agent = build_agent(ScriptedModel(script=script), fake_store(), tickets_dir=str(tmp_path))
    case = {"id": "g03", "message": "Revisa ECS y crea un ticket",
            "expected_tools": ["consultar_estado_servicio", "crear_ticket_borrador"]}
    rows = run_agent_cases(agent, [case], pause=0)
    assert rows[0]["ok"], rows[0]["failures"]
    assert len(list(tmp_path.glob("TCK-*.json"))) == 1
