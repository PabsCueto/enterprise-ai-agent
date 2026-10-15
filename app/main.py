"""API REST del asistente RAG y del agente.

Arranque (desde la raíz del proyecto, con el entorno virtual activo):
    uvicorn app.main:app --reload
Documentación interactiva: http://127.0.0.1:8000/docs
"""
import logging
import math
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app import rag
from app.agent import build_agent, summarize_turn
from app.ingest import get_store
from app.observability import (
    KNOWN_PATHS,
    REQUEST_ID_PATTERN,
    annotate,
    describe_text,
    metrics,
    request_fields_var,
    request_id_var,
    setup_logging,
)
from app.rag import NO_ANSWER, answer_question
from app.security import (
    RateLimiter,
    User,
    UsersFileError,
    authenticate,
    load_access_map,
    load_users,
    make_doc_filter,
    users_path,
)

MAX_AGENT_STEPS = 10  # límite de pasos del grafo, evita bucles infinitos

setup_logging()
access_logger = logging.getLogger("app.access")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # El índice se carga una sola vez al arrancar y se reutiliza en cada pregunta.
    app.state.store = get_store()
    app.state.agent = None  # el agente se construye en la primera petición a /agent
    startup_logger = logging.getLogger("app")
    app.state.users = []
    try:
        app.state.users = load_users()
    except UsersFileError as error:
        # Falla cerrado: el servidor arranca (para poder ver el error) pero rechaza toda petición protegida.
        startup_logger.error(str(error))
    app.state.access_map = load_access_map()
    app.state.limiter = RateLimiter(int(os.getenv("RATE_LIMIT_PER_MIN", "20")))
    users_file = users_path()
    startup_logger.info("usuarios cargados", extra={"fields": {
        "users_file": str(users_file),
        "exists": users_file.exists(),
        "bytes": users_file.stat().st_size if users_file.exists() else 0,
        "count": len(app.state.users),
    }})
    if not app.state.users:
        startup_logger.warning(
            "No hay usuarios cargados: se rechazarán todas las peticiones protegidas. "
            "Crea uno con scripts/create_api_key.py"
        )
    yield


def docs_kwargs() -> dict:
    """Con DOCS_ENABLED=false se apagan /docs, /redoc y /openapi.json (recomendado en despliegues públicos)."""
    if os.getenv("DOCS_ENABLED", "true").lower() == "false":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


app = FastAPI(title="Enterprise AI Agent", version="0.5.0", lifespan=lifespan, **docs_kwargs())


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    """Asigna un request_id, mide la latencia y emite una línea de log JSON por petición."""
    incoming = request.headers.get("X-Request-ID", "")
    # Solo se acepta un id del cliente si es seguro de escribir en logs; si no, se genera uno.
    request_id = incoming if REQUEST_ID_PATTERN.fullmatch(incoming) else uuid.uuid4().hex[:12]
    fields: dict = {}
    id_token = request_id_var.set(request_id)
    fields_token = request_fields_var.set(fields)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        path = request.url.path if request.url.path in KNOWN_PATHS else "other"
        metrics.record(path, status, latency_ms, fields.get("usage"), fields.get("refused"))
        level = logging.DEBUG if path in {"/health", "/metrics"} else logging.INFO
        access_logger.log(level, "request", extra={"fields": {
            "event": "request", "method": request.method, "path": path,
            "status": status, "latency_ms": latency_ms, **fields,
        }})
        request_id_var.reset(id_token)
        request_fields_var.reset(fields_token)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class Source(BaseModel):
    url: str
    score: float
    snippet: str


class Usage(BaseModel):
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]
    usage: Usage = Usage()


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
    usage: Usage = Usage()


def get_agent():
    if app.state.agent is None:
        app.state.agent = build_agent(rag._get_llm(), app.state.store, access_map=app.state.access_map)
    return app.state.agent


def _http_error(error: Exception) -> HTTPException:
    annotate(error=type(error).__name__)
    if "RESOURCE_EXHAUSTED" in str(error):
        return HTTPException(status_code=429, detail="Límite gratuito de Gemini alcanzado. Intenta de nuevo en un minuto.")
    return HTTPException(status_code=500, detail="Error interno al procesar la solicitud.")


async def require_user(request: Request, x_api_key: str | None = Header(default=None)) -> User:
    """Autenticación por API key (cabecera X-API-Key). Sin usuarios configurados, rechaza todo."""
    user = authenticate(x_api_key, request.app.state.users)
    if user is None:
        annotate(auth="rechazada", auth_reason="sin_clave" if not x_api_key else "clave_invalida")
        raise HTTPException(status_code=401, detail="API key ausente o inválida.", headers={"WWW-Authenticate": "ApiKey"})
    annotate(user=user.name)
    return user


async def limited_user(request: Request, user: User = Depends(require_user)) -> User:
    """Además de autenticar, aplica el límite de peticiones por minuto de cada usuario."""
    retry_after = request.app.state.limiter.check(user.name)
    if retry_after is not None:
        annotate(rate_limited=True)
        raise HTTPException(
            status_code=429,
            detail="Demasiadas peticiones. Espera un momento.",
            headers={"Retry-After": str(math.ceil(retry_after))},
        )
    return user


async def admin_user(user: User = Depends(require_user)) -> User:
    if not user.admin:
        annotate(auth="prohibida")
        raise HTTPException(status_code=403, detail="Se requiere un usuario administrador.")
    return user


@app.api_route("/", methods=["GET", "HEAD"])
def root() -> dict:
    """Página de bienvenida mínima: quien abre la URL en el navegador entiende qué es y cómo usarlo."""
    return {
        "service": "enterprise-ai-agent",
        "status": "ok",
        "endpoints": ["GET /health", "POST /ask", "POST /agent"],
        "auth": "se requiere la cabecera X-API-Key en /ask y /agent",
        "repository": "https://github.com/PabsCueto/enterprise-ai-agent",
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "chunks": len(app.state.store.store)}


@app.get("/metrics")
def metrics_endpoint(user: User = Depends(admin_user)) -> dict:
    """Resumen en memoria: peticiones, latencias, tokens y tasa de rechazo."""
    return metrics.snapshot()


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, user: User = Depends(limited_user)) -> dict:
    annotate(**describe_text("question", body.question))
    doc_filter = make_doc_filter(user.groups, app.state.access_map)  # solo documentos permitidos
    try:
        result = answer_question(app.state.store, body.question, doc_filter=doc_filter)
    except Exception as error:
        raise _http_error(error)
    annotate(usage=result["usage"], sources=len(result["sources"]), refused=result["answer"].strip() == NO_ANSWER)
    return result


@app.post("/agent", response_model=AgentResponse)
def agent_endpoint(body: AgentRequest, user: User = Depends(limited_user)) -> dict:
    annotate(thread_id=body.thread_id, **describe_text("message", body.message))
    try:
        result = get_agent().invoke(
            {"messages": [("human", body.message)]},
            config={
                # El hilo lleva el nombre del usuario: dos usuarios con el mismo thread_id
                # nunca comparten conversación.
                "configurable": {"thread_id": f"{user.name}:{body.thread_id}", "groups": list(user.groups)},
                "recursion_limit": MAX_AGENT_STEPS,
            },
        )
    except Exception as error:
        raise _http_error(error)

    summary = summarize_turn(result["messages"])
    annotate(
        usage=summary["usage"],
        tools=[call["tool"] for call in summary["tools_used"]],  # solo nombres: los argumentos pueden ser sensibles
        sources=len(summary["sources"]),
        refused=summary["answer"].strip() == NO_ANSWER,
    )
    return summary
