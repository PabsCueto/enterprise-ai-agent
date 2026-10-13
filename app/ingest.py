"""Ingesta: lee data/raw/*.txt, los divide en fragmentos y los indexa en un
almacén vectorial en memoria usando embeddings de Gemini.

El índice se guarda en data/index.json para no gastar cuota gratuita de
embeddings cada vez que arranca la API."""
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

RAW_DIR = Path(os.getenv("RAW_DOCS_DIR", ROOT / "data" / "raw"))
INDEX_PATH = ROOT / "data" / "index.json"
CHUNK_SIZE = 1000           # caracteres por fragmento
CHUNK_OVERLAP = 200         # caracteres repetidos entre fragmentos vecinos
BATCH_SIZE = 20             # fragmentos por lote
PAUSE_BETWEEN_BATCHES = 5   # segundos entre lotes
WAIT_ON_LIMIT = 60          # segundos de espera si Gemini responde 429


def get_embeddings() -> GoogleGenerativeAIEmbeddings:
    """Crea el cliente de embeddings. La librería busca GOOGLE_API_KEY;
    reutilizamos GEMINI_API_KEY."""
    os.environ.setdefault("GOOGLE_API_KEY", os.getenv("GEMINI_API_KEY", ""))
    return GoogleGenerativeAIEmbeddings(
        model=os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
    )


def load_documents(raw_dir=None) -> list[Document]:
    """Lee cada archivo de data/raw. La primera línea trae 'Fuente: <url>'."""
    docs = []
    for path in sorted(Path(raw_dir or RAW_DIR).glob("*.txt")):
        lines = path.read_text(encoding="utf-8").splitlines()
        source = lines[0].removeprefix("Fuente: ").strip()
        body = "\n".join(lines[2:])  # se omiten la fuente y la línea vacía
        docs.append(Document(page_content=body, metadata={"source": source, "file": path.name}))
    return docs


def split_documents(docs: list[Document]) -> list[Document]:
    """Divide los documentos en fragmentos. Cada fragmento conserva su fuente."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    return splitter.split_documents(docs)


def build_store(embeddings=None) -> InMemoryVectorStore:
    """Construye el índice por lotes para respetar el límite gratuito de Gemini."""
    store = InMemoryVectorStore(embeddings or get_embeddings())
    chunks = split_documents(load_documents())
    print(f"Fragmentos a indexar: {len(chunks)}")

    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start:start + BATCH_SIZE]
        for attempt in range(1, 4):
            try:
                store.add_documents(batch)
                break
            except Exception as error:
                if "RESOURCE_EXHAUSTED" not in str(error) or attempt == 3:
                    raise
                print(f"Límite de cuota alcanzado. Espero {WAIT_ON_LIMIT} s y reintento...")
                time.sleep(WAIT_ON_LIMIT)
        print(f"Indexados {min(start + BATCH_SIZE, len(chunks))}/{len(chunks)}")
        time.sleep(PAUSE_BETWEEN_BATCHES)
    return store


def get_store(embeddings=None, rebuild: bool = False) -> InMemoryVectorStore:
    """Devuelve el índice: lo carga de disco si existe; si no, lo construye y lo guarda."""
    embeddings = embeddings or get_embeddings()
    if INDEX_PATH.exists() and not rebuild:
        return InMemoryVectorStore.load(str(INDEX_PATH), embeddings)
    store = build_store(embeddings)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    store.dump(str(INDEX_PATH))
    return store
