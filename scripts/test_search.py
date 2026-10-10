"""Construye el índice y muestra los fragmentos más parecidos a una pregunta.

Uso:
    python scripts/test_search.py "¿Qué es una función de AWS Lambda?"
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.ingest import build_store  # noqa: E402

question = sys.argv[1] if len(sys.argv) > 1 else "¿Qué es una función de AWS Lambda?"

store = build_store()
print(f"Fragmentos indexados: {len(store.store)}")

for doc, score in store.similarity_search_with_score(question, k=3):
    print(f"\n--- score {score:.3f} | {doc.metadata['source']}")
    print(doc.page_content[:300])
