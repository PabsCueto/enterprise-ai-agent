"""Observabilidad sin servicios externos: logs JSON, request_id, métricas en memoria y tokens.

- Cada petición genera UNA línea de log JSON con latencia, estado, herramientas y tokens.
- No se registra el texto de las preguntas (pueden contener datos sensibles): solo su
  longitud y un hash corto. Para depurar en local: LOG_QUESTIONS=true.
"""
import hashlib
import json
import logging
import os
import re
import threading
from collections import defaultdict, deque
from contextvars import ContextVar
from datetime import datetime, timezone

REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")
KNOWN_PATHS = {"/ask", "/agent", "/health", "/metrics"}  # el resto se agrupa como "other"

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
request_fields_var: ContextVar["dict | None"] = ContextVar("request_fields", default=None)


def annotate(**fields) -> None:
    """Añade datos a la línea de log de la petición en curso (no hace nada fuera de una petición)."""
    context = request_fields_var.get()
    if context is not None:
        context.update(fields)


def describe_text(name: str, text: str) -> dict:
    """Describe un texto de usuario sin guardarlo: longitud y hash (o el texto si LOG_QUESTIONS=true)."""
    fields = {
        f"{name}_chars": len(text),
        f"{name}_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
    }
    if os.getenv("LOG_QUESTIONS", "false").lower() == "true":
        fields[name] = text
    return fields


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging() -> None:
    """Configura el logger 'app' con salida JSON. Se puede llamar varias veces."""
    logger = logging.getLogger("app")
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    if not any(getattr(h, "_json_handler", False) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._json_handler = True
        logger.addHandler(handler)


# --- Uso de tokens -------------------------------------------------------------------

def empty_usage() -> dict:
    return {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def usage_from_message(message) -> dict:
    """Uso de una llamada al modelo (los tokens vienen en message.usage_metadata)."""
    meta = getattr(message, "usage_metadata", None) or {}
    return {
        "llm_calls": 1,
        "input_tokens": int(meta.get("input_tokens", 0)),
        "output_tokens": int(meta.get("output_tokens", 0)),
        "total_tokens": int(meta.get("total_tokens", 0)),
    }


def add_usage(a: dict, b: dict) -> dict:
    return {key: a[key] + b[key] for key in a}


# --- Métricas en memoria -------------------------------------------------------------

class Metrics:
    """Contadores por endpoint. Viven en memoria: se reinician al reiniciar el servidor."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._requests = defaultdict(int)
            self._errors = defaultdict(int)
            self._latency = defaultdict(lambda: deque(maxlen=1000))
            self._usage = empty_usage()
            self._answered = 0
            self._refused = 0

    def record(self, endpoint: str, status: int, latency_ms: float, usage: dict | None = None,
               refused: bool | None = None) -> None:
        with self._lock:
            self._requests[endpoint] += 1
            if status >= 400:
                self._errors[endpoint] += 1
            self._latency[endpoint].append(latency_ms)
            if usage:
                self._usage = add_usage(self._usage, usage)
            if refused is True:
                self._refused += 1
            elif refused is False:
                self._answered += 1

    def snapshot(self) -> dict:
        with self._lock:
            endpoints = {}
            for path, count in self._requests.items():
                values = sorted(self._latency[path])
                endpoints[path] = {
                    "requests": count,
                    "errors": self._errors[path],  # respuestas con estado >= 400
                    "avg_ms": round(sum(values) / len(values), 1),
                    "p95_ms": values[min(len(values) - 1, int(0.95 * len(values)))],
                }
            total = self._answered + self._refused
            return {
                "endpoints": endpoints,
                "tokens": dict(self._usage),
                "answers": {
                    "answered": self._answered,
                    "refused": self._refused,
                    "refusal_rate": round(self._refused / total, 3) if total else None,
                },
            }


metrics = Metrics()
