"""Crea un índice mínimo con embeddings falsos para comprobar que el contenedor arranca.

Lo usa el CI (.github/workflows/docker.yml). NO sirve para responder preguntas reales.

Uso:
    python scripts/make_smoke_index.py data/index.json
"""
import sys

from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.vectorstores import InMemoryVectorStore

path = sys.argv[1] if len(sys.argv) > 1 else "data/index.json"
store = InMemoryVectorStore(DeterministicFakeEmbedding(size=8))
store.add_documents([
    Document(page_content="Documento de prueba para el CI.", metadata={"source": "https://docs.example.test/smoke"})
])
store.dump(path)
print(f"Índice de prueba guardado en {path} con {len(store.store)} fragmento")
