"""Ejecuta la evaluación y genera evals/report.md.

Uso (desde la raíz del proyecto, con el entorno virtual activo):
    python evals/run_eval.py            # recuperación y rechazo (solo embeddings)
    python evals/run_eval.py --agent    # además, las decisiones del agente (llama al modelo)
    python evals/run_eval.py --answers  # además, la respuesta final a preguntas de AWS fuera del corpus
    python evals/run_eval.py --injection  # además, documentos envenenados (llama al modelo)
    python evals/run_eval.py --agent --answers
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT))

from app import rag  # noqa: E402
from app.agent import build_agent  # noqa: E402
from app.ingest import get_store  # noqa: E402
from evals.metrics import (  # noqa: E402
    evaluate_retrieval,
    render_report,
    run_agent_cases,
    run_answer_cases,
    run_injection_cases,
)

parser = argparse.ArgumentParser()
parser.add_argument("--agent", action="store_true", help="evaluar también al agente")
parser.add_argument("--answers", action="store_true", help="evaluar la respuesta final a las preguntas de AWS fuera del corpus")
parser.add_argument("--injection", action="store_true", help="probar documentos envenenados (prompt injection)")
parser.add_argument("--k", type=int, default=rag.TOP_K)
parser.add_argument("--min-score", type=float, default=float(os.getenv("MIN_SCORE", rag.DEFAULT_MIN_SCORE)))
parser.add_argument("--out", default=str(ROOT / "evals" / "report.md"))
args = parser.parse_args()

dataset = json.loads((ROOT / "evals" / "dataset.json").read_text(encoding="utf-8"))
store = get_store()

print(f"Evaluando recuperación ({len(dataset['retrieval'])} preguntas)...")
retrieval = evaluate_retrieval(store, dataset["retrieval"], k=args.k, min_score=args.min_score)
summary = retrieval["summary"]
print(f"  hit rate: {summary['hit_rate']}  MRR: {summary['mrr']}")
print(f"  corpus respondidas: {summary['positive_pass']}  sin relación rechazadas: {summary['negative_pass']}  AWS fuera del corpus filtradas: {summary['adjacent_filtered']}")
print(f"  score mín. corpus: {summary['positive_min_score']}  máx. sin relación: {summary['negative_max_score']}  máx. AWS fuera del corpus: {summary['adjacent_max_score']}  umbral sugerido: {summary['suggested_min_score']}")

answer_rows = None
if args.answers:
    adjacent = [c for c in dataset["retrieval"] if c["kind"] == "adjacent"]
    print(f"Evaluando la respuesta final a {len(adjacent)} preguntas de AWS fuera del corpus...")
    answer_rows = run_answer_cases(store, adjacent, rag.answer_question)

agent_rows = None
if args.agent:
    print(f"Evaluando al agente ({len(dataset['agent'])} casos)...")
    # Los tickets de la evaluación van a una carpeta temporal, no a data/tickets.
    agent = build_agent(rag._get_llm(), store, tickets_dir=Path(tempfile.mkdtemp()))
    agent_rows = run_agent_cases(agent, dataset["agent"])
    print(f"  correctos: {sum(r['ok'] for r in agent_rows)}/{len(agent_rows)}")

injection_rows = None
if args.injection:
    from langchain_core.vectorstores import InMemoryVectorStore  # noqa: E402

    from app.ingest import get_embeddings, load_documents  # noqa: E402

    cases = json.loads((ROOT / "evals" / "security_cases.json").read_text(encoding="utf-8"))["cases"]
    # Índice aparte con los documentos envenenados (el índice real no se toca).
    poisoned_store = InMemoryVectorStore(get_embeddings())
    poisoned_store.add_documents(load_documents(ROOT / "evals" / "poisoned"))
    print(f"Probando {len(cases)} casos de prompt injection...")
    poisoned_agent = build_agent(rag._get_llm(), poisoned_store, tickets_dir=Path(tempfile.mkdtemp()))
    injection_rows = run_injection_cases(poisoned_store, cases, poisoned_agent, rag.answer_question, rag.retrieve)
    print(f"  resistió: {sum(r['status'] == 'OK' for r in injection_rows)}/{len(injection_rows)}  "
          f"obedeció: {sum(r['status'] == 'FALLA' for r in injection_rows)}  "
          f"inconclusos: {sum(r['status'] == 'INCONCLUSO' for r in injection_rows)}")

Path(args.out).write_text(render_report(retrieval, agent_rows, answer_rows, injection_rows), encoding="utf-8")
print(f"Informe guardado en {args.out}")
