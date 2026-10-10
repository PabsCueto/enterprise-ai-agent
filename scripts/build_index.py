"""Construye el índice vectorial y lo guarda en data/index.json.

Hazlo una sola vez (o cuando cambie el corpus). Tarda un par de minutos por el
límite gratuito de Gemini.

Uso:
    python scripts/build_index.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.ingest import INDEX_PATH, get_store  # noqa: E402

store = get_store(rebuild=True)
print(f"Índice guardado en {INDEX_PATH} con {len(store.store)} fragmentos.")
