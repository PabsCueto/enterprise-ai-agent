"""Herramientas que el agente puede decidir usar.

1. buscar_en_documentacion  -> el RAG de la fase 1 (lectura).
2. consultar_estado_servicio -> API interna SIMULADA (lectura).
3. crear_ticket_borrador     -> escribe un borrador en data/tickets/ (acción).
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from langchain_core.tools import tool

from app.rag import build_sources, retrieve

ROOT = Path(__file__).resolve().parent.parent
TICKETS_DIR = ROOT / "data" / "tickets"

# Datos de demostración: simulan lo que devolvería una API interna de monitoreo.
SERVICE_STATUS = {
    "lambda": {"estado": "operativo", "latencia_ms": 120, "incidentes": []},
    "ecs": {"estado": "degradado", "latencia_ms": 480, "incidentes": ["Latencia elevada en el clúster de producción"]},
    "s3": {"estado": "operativo", "latencia_ms": 60, "incidentes": []},
    "dynamodb": {"estado": "operativo", "latencia_ms": 15, "incidentes": []},
    "iam": {"estado": "operativo", "latencia_ms": 90, "incidentes": []},
    "cloudwatch": {"estado": "operativo", "latencia_ms": 200, "incidentes": []},
}


def make_tools(store, tickets_dir: Path = TICKETS_DIR) -> list:
    """Crea las herramientas ligadas a un índice vectorial y a una carpeta de tickets."""
    tickets_dir = Path(tickets_dir)  # acepta str o Path

    @tool(response_format="content_and_artifact")
    def buscar_en_documentacion(consulta: str):
        """Busca en la documentación oficial de AWS indexada (Lambda, ECS, ALB, S3,
        DynamoDB, IAM, CloudWatch). Úsala para cualquier pregunta técnica sobre AWS.
        Devuelve fragmentos con su fuente."""
        results = retrieve(store, consulta)
        if not results:
            return "No se encontraron fragmentos relevantes en la documentación.", []
        content = "\n\n".join(
            f"[Fuente: {doc.metadata['source']}]\n{doc.page_content}" for doc, _score in results
        )
        return content, build_sources(results)

    @tool
    def consultar_estado_servicio(servicio: str) -> str:
        """Consulta el estado actual de un servicio interno (lambda, ecs, s3, dynamodb,
        iam, cloudwatch): estado, latencia e incidentes abiertos. Los datos son de demostración."""
        key = servicio.strip().lower()
        if key not in SERVICE_STATUS:
            return f"Servicio desconocido: {servicio!r}. Disponibles: {', '.join(sorted(SERVICE_STATUS))}."
        return json.dumps({"servicio": key, "datos_simulados": True, **SERVICE_STATUS[key]}, ensure_ascii=False)

    @tool
    def crear_ticket_borrador(
        titulo: str,
        descripcion: str,
        prioridad: Literal["baja", "media", "alta"] = "media",
    ) -> str:
        """Crea un ticket en estado BORRADOR (no se envía a ningún sistema). Úsala solo
        cuando el usuario pida explícitamente crear un ticket."""
        tickets_dir.mkdir(parents=True, exist_ok=True)
        ticket_id = f"TCK-{uuid.uuid4().hex[:6].upper()}"
        ticket = {
            "id": ticket_id,
            "titulo": titulo[:120],
            "descripcion": descripcion[:2000],
            "prioridad": prioridad,
            "estado": "borrador",
            "creado": datetime.now(timezone.utc).isoformat(),
        }
        (tickets_dir / f"{ticket_id}.json").write_text(
            json.dumps(ticket, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return f"Ticket borrador {ticket_id} creado: {ticket['titulo']} (prioridad {prioridad})."

    return [buscar_en_documentacion, consultar_estado_servicio, crear_ticket_borrador]
