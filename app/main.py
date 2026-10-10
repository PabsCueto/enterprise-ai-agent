"""API REST del asistente RAG y del agente.

Arranque (desde la raíz del proyecto, con el entorno virtual activo):
    uvicorn app.main:app --reload
Documentación interactiva: http://127.0.0.1:8000/docs
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import rag
from app.agent import build_agent, summarize_turn
from app.ingest import get_store
from app.rag import answer_question

MAX_AGENT_STEPS = 10  # límite de pasos del grafo, evita bucles infinitos


@asynccontextmanager
async def lifespan(app: FastAPI):
    # El índice se carga una sola vez al arrancar y se reutiliza en cada pregunta.
    app.state.store = get_store()
    app.state.agent = None  # el agente se construye en la primera petición a /agent
    yield


app = FastAPI(title="Enterprise AI Agent", version="0.2.0", lifespan=lifespan)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class Source(BaseModel):
    url: str
    score: float
    snippet: str


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]


class AgentRequest(BaseModel):
    message: str = Field(min_length=3, max_length=1000)
    thread_id: str = Field(default="default", max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ToolCall(BaseModel):
    tool: str
    args: dict


class AgentResponse(BaseModel):
    answer: str
    sources: list[Source]
    tools_used: list[ToolCall]


def get_agent():
    if app.state.agent is None:
        app.state.agent = build_agent(rag._get_llm(), app.state.store)
    return app.state.agent


def _http_error(error: Exception) -> HTTPException:
    if "RESOURCE_EXHAUSTED" in str(error):
        return HTTPException(status_code=429, detail="Límite gratuito de Gemini alcanzado. Intenta de nuevo en un minuto.")
    return HTTPException(status_code=500, detail="Error interno al procesar la solicitud.")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "chunks": len(app.state.store.store)}


@app.post("/ask", response_model=AskResponse)
def ask(request: AskRequest) -> dict:
    try:
        return answer_question(app.state.store, request.question)
    except Exception as error:
        raise _http_error(error)


@app.post("/agent", response_model=AgentResponse)
def agent_endpoint(request: AgentRequest) -> dict:
    try:
        result = get_agent().invoke(
            {"messages": [("human", request.message)]},
            config={"configurable": {"thread_id": request.thread_id}, "recursion_limit": MAX_AGENT_STEPS},
        )
    except Exception as error:
        raise _http_error(error)

    return summarize_turn(result["messages"])
