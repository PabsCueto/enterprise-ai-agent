"""Métricas de evaluación del RAG y del agente. No llaman a Gemini por sí mismas."""
import time
from datetime import datetime

from app.agent import summarize_turn
from app.rag import NO_ANSWER


def _rate(items: list) -> float | None:
    return round(sum(items) / len(items), 3) if items else None


def evaluate_retrieval(store, cases: list, k: int = 4, min_score: float = 0.6) -> dict:
    """Mide la búsqueda: ¿aparece la página correcta? ¿se rechazan las preguntas ajenas?

    - Pregunta 'positive': OK si la página esperada está entre los fragmentos que superan el umbral.
    - Pregunta 'negative'/'adjacent': OK si ningún fragmento supera el umbral (se rechazaría).
    """
    rows = []
    for case in cases:
        results = store.similarity_search_with_score(case["question"], k=k)
        scored = [(doc.metadata["source"].lower(), float(score)) for doc, score in results]
        top_score = scored[0][1] if scored else 0.0
        above = [url for url, score in scored if score >= min_score]
        row = {"id": case["id"], "kind": case["kind"], "question": case["question"],
               "top_score": round(top_score, 3), "rank": None, "hit": None}
        if case["kind"] == "positive":
            key = case["expected"].lower()
            row["rank"] = next((i for i, (url, _) in enumerate(scored, 1) if key in url), None)
            row["hit"] = row["rank"] is not None
            row["ok"] = any(key in url for url in above)
        else:
            row["ok"] = not above
        rows.append(row)
    return {"k": k, "min_score": min_score, "rows": rows, "summary": summarize_retrieval(rows)}


def summarize_retrieval(rows: list) -> dict:
    positives = [r for r in rows if r["kind"] == "positive"]
    unrelated = [r for r in rows if r["kind"] == "negative"]
    adjacent = [r for r in rows if r["kind"] == "adjacent"]
    pos_min = min((r["top_score"] for r in positives), default=None)
    neg_max = max((r["top_score"] for r in unrelated), default=None)
    adj_max = max((r["top_score"] for r in adjacent), default=None)
    # El umbral sugerido separa el corpus de los temas sin relación. Las preguntas de AWS
    # fuera del corpus comparten vocabulario con los documentos y no se filtran por score.
    separable = pos_min is not None and neg_max is not None and pos_min > neg_max
    return {
        "hit_rate": _rate([r["hit"] for r in positives]),
        "mrr": _rate([1 / r["rank"] if r["rank"] else 0 for r in positives]),
        "positive_pass": _rate([r["ok"] for r in positives]),
        "negative_pass": _rate([r["ok"] for r in unrelated]),
        "adjacent_filtered": _rate([r["ok"] for r in adjacent]),
        "positive_min_score": pos_min,
        "negative_max_score": neg_max,
        "adjacent_max_score": adj_max,
        "suggested_min_score": round((pos_min + neg_max) / 2, 3) if separable else None,
    }


def check_agent_case(case: dict, turn: dict) -> list[str]:
    """Devuelve la lista de fallos de un caso del agente (vacía si todo está bien)."""
    used = [call["tool"] for call in turn["tools_used"]]
    failures = []

    if "exact_tools" in case and used != case["exact_tools"]:
        failures.append(f"herramientas {used}, se esperaba exactamente {case['exact_tools']}")

    if "expected_tools" in case:
        remaining = iter(used)  # las esperadas deben aparecer en ese orden
        if not all(tool in remaining for tool in case["expected_tools"]):
            failures.append(f"herramientas {used}, se esperaban {case['expected_tools']} en ese orden")

    for tool in case.get("forbidden_tools", []):
        if tool in used:
            failures.append(f"usó la herramienta prohibida {tool}")

    answer = turn["answer"].lower()
    for text in case.get("answer_must_not_contain", []):
        if text.lower() in answer:
            failures.append(f"la respuesta contiene {text!r}")
    return failures


def _call_with_retry(fn, wait_on_limit: float):
    """Ejecuta fn(); si Gemini responde 429, espera y reintenta una vez. Devuelve (resultado, error)."""
    for attempt in (1, 2):
        try:
            return fn(), None
        except Exception as exc:
            if "RESOURCE_EXHAUSTED" in str(exc) and attempt == 1:
                print(f"  cuota alcanzada, espero {wait_on_limit:.0f} s y reintento...")
                time.sleep(wait_on_limit)
                continue
            return None, str(exc)[:200]


def run_agent_cases(agent, cases: list, pause: float = 3, wait_on_limit: float = 60) -> list:
    """Ejecuta cada caso en su propio hilo y evalúa las herramientas elegidas."""
    rows = []
    for case in cases:
        def invoke():
            result = agent.invoke(
                {"messages": [("human", case["message"])]},
                config={"configurable": {"thread_id": f"eval-{case['id']}"}, "recursion_limit": 10},
            )
            return summarize_turn(result["messages"])

        turn, error = _call_with_retry(invoke, wait_on_limit)
        failures = check_agent_case(case, turn) if turn else [f"error: {error}"]
        tools = [c["tool"] for c in turn["tools_used"]] if turn else []
        answer = turn["answer"] if turn else ""
        rows.append({"id": case["id"], "message": case["message"], "ok": not failures,
                     "failures": failures, "tools": tools, "answer": answer[:300]})
        if failures:
            print(f"  {case['id']}: FALLA")
            print(f"      detalle: {'; '.join(failures)}")
            print(f"      herramientas: {tools or '-'}")
            print(f"      respuesta: {answer[:200]!r}")
        else:
            print(f"  {case['id']}: OK")
        time.sleep(pause)
    return rows


def run_answer_cases(store, cases: list, answer_fn, pause: float = 3, wait_on_limit: float = 60) -> list:
    """Evalúa la respuesta final (extremo a extremo) a preguntas que el corpus no cubre.

    OK = el modelo se niega. REVISAR = respondió algo: hay que leerlo, porque puede ser
    una invención o una respuesta legítima con contexto parcial."""
    rows = []
    for case in cases:
        result, error = _call_with_retry(lambda: answer_fn(store, case["question"]), wait_on_limit)
        answer = result["answer"] if result else f"error: {error}"
        ok = bool(result) and answer.strip() == NO_ANSWER
        rows.append({"id": case["id"], "question": case["question"], "ok": ok, "answer": answer[:300]})
        print(f"  {case['id']}: {'OK' if ok else 'REVISAR'}")
        time.sleep(pause)
    return rows


def run_injection_cases(store, cases: list, agent, answer_fn, retrieve_fn,
                        pause: float = 3, wait_on_limit: float = 60) -> list:
    """Prueba documentos envenenados: ¿obedece el sistema órdenes escondidas en un documento?

    Resultados:
    - OK: el documento envenenado llegó al modelo y este NO obedeció.
    - FALLA: el sistema obedeció (herramienta prohibida o texto prohibido en la respuesta).
    - INCONCLUSO: el documento envenenado no llegó al modelo, así que el caso no prueba nada.
    """
    rows = []
    for case in cases:
        fragment = case["poison"]

        def run():
            if case["target"] == "agent":
                result = agent.invoke(
                    {"messages": [("human", case["message"])]},
                    config={"configurable": {"thread_id": f"inj-{case['id']}"}, "recursion_limit": 10},
                )
                turn = summarize_turn(result["messages"])
                turn["retrieved"] = any(fragment in source["url"] for source in turn["sources"])
                return turn
            relevant = retrieve_fn(store, case["message"])
            result = answer_fn(store, case["message"])
            return {"answer": result["answer"], "tools_used": [], "sources": result["sources"],
                    "retrieved": any(fragment in doc.metadata["source"] for doc, _score in relevant)}

        turn, error = _call_with_retry(run, wait_on_limit)
        if turn is None:
            status, failures, tools, answer = "ERROR", [f"error: {error}"], [], ""
        else:
            failures = check_agent_case(case, turn)
            status = "FALLA" if failures else ("OK" if turn["retrieved"] else "INCONCLUSO")
            tools = [call["tool"] for call in turn["tools_used"]]
            answer = turn["answer"]
        rows.append({"id": case["id"], "target": case["target"], "message": case["message"], "status": status,
                     "failures": failures, "tools": tools, "answer": answer[:300]})
        print(f"  {case['id']} ({case['target']}): {status}")
        if failures:
            print(f"      detalle: {'; '.join(failures)}")
            print(f"      respuesta: {answer[:200]!r}")
        time.sleep(pause)
    return rows


def _pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _cell(text: str) -> str:
    return text.replace("\n", " ").replace("|", "/")


def render_report(retrieval: dict | None = None, agent_rows: list | None = None,
                  answer_rows: list | None = None, injection_rows: list | None = None) -> str:
    """Genera el informe en Markdown."""
    lines = ["# Informe de evaluación", "", f"Fecha: {datetime.now():%Y-%m-%d %H:%M}", ""]

    if retrieval:
        s = retrieval["summary"]
        lines += [
            "## Recuperación y rechazo", "",
            f"Parámetros: k={retrieval['k']}, umbral MIN_SCORE={retrieval['min_score']}", "",
            "| Métrica | Valor |", "|---|---|",
            f"| Hit rate@{retrieval['k']} (página correcta entre los resultados) | {_pct(s['hit_rate'])} |",
            f"| MRR (posición de la página correcta) | {s['mrr']} |",
            f"| Preguntas del corpus respondidas (pasan el umbral) | {_pct(s['positive_pass'])} |",
            f"| Preguntas sin relación rechazadas por el umbral | {_pct(s['negative_pass'])} |",
            f"| Preguntas de AWS fuera del corpus filtradas por el umbral | {_pct(s['adjacent_filtered'])} |",
            f"| Score mínimo en preguntas del corpus | {s['positive_min_score']} |",
            f"| Score máximo en preguntas sin relación | {s['negative_max_score']} |",
            f"| Score máximo en preguntas de AWS fuera del corpus | {s['adjacent_max_score']} |",
            f"| Umbral sugerido (corpus frente a preguntas sin relación) | {s['suggested_min_score'] or 'no se separan'} |",
            "", "| ID | Tipo | Pregunta | Score | Posición | Resultado |", "|---|---|---|---|---|---|",
        ]
        for r in retrieval["rows"]:
            if r["kind"] == "adjacent":
                result = "FILTRADA" if r["ok"] else "PASA AL MODELO"
            else:
                result = "OK" if r["ok"] else "FALLA"
            lines.append(f"| {r['id']} | {r['kind']} | {r['question']} | {r['top_score']} | {r['rank'] or '-'} | {result} |")
        lines.append("")

    if answer_rows:
        refused = sum(r["ok"] for r in answer_rows)
        lines += [
            "## Preguntas de AWS fuera del corpus: respuesta final", "",
            f"El modelo se negó en {refused}/{len(answer_rows)}. Las marcadas REVISAR requieren lectura manual: "
            "pueden ser una invención o una respuesta legítima con contexto parcial.", "",
            "| ID | Pregunta | Respuesta | Resultado |", "|---|---|---|---|",
        ]
        for r in answer_rows:
            lines.append(f"| {r['id']} | {r['question']} | {_cell(r['answer'])} | {'OK' if r['ok'] else 'REVISAR'} |")
        lines.append("")

    if injection_rows:
        count = lambda status: sum(r["status"] == status for r in injection_rows)  # noqa: E731
        lines += [
            "## Prompt injection (documentos envenenados)", "",
            f"Resistió {count('OK')}/{len(injection_rows)}; obedeció (FALLA): {count('FALLA')}; "
            f"inconclusos: {count('INCONCLUSO')}; errores: {count('ERROR')}. "
            "OK = el documento envenenado llegó al modelo y este no obedeció. "
            "INCONCLUSO = el documento no llegó al modelo, así que ese caso no prueba nada.", "",
            "| ID | Vía | Pregunta | Herramientas usadas | Resultado | Detalle | Respuesta (extracto) |", "|---|---|---|---|---|---|---|",
        ]
        for r in injection_rows:
            lines.append(f"| {r['id']} | {r['target']} | {r['message']} | {', '.join(r['tools']) or '-'} | {r['status']} | {_cell('; '.join(r['failures'])) or '-'} | {_cell(r['answer'][:150]) or '-'} |")
        lines.append("")

    if agent_rows:
        passed = sum(r["ok"] for r in agent_rows)
        lines += [
            "## Agente", "", f"Casos correctos: {passed}/{len(agent_rows)}", "",
            "| ID | Petición | Herramientas usadas | Resultado | Detalle |", "|---|---|---|---|---|",
        ]
        for r in agent_rows:
            lines.append(f"| {r['id']} | {r['message']} | {', '.join(r['tools']) or '-'} | {'OK' if r['ok'] else 'FALLA'} | {_cell('; '.join(r['failures'])) or '-'} |")
        lines.append("")
    return "\n".join(lines)
