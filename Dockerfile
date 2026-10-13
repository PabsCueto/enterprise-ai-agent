# Imagen de enterprise-ai-agent.
# No contiene documentos, índice, usuarios ni claves: se montan o se inyectan al ejecutar.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Las dependencias primero: esta capa se reutiliza mientras requirements.txt no cambie.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Solo el código que necesita el servidor.
COPY app/ app/
COPY corpus/ corpus/
COPY scripts/ scripts/

# Usuario sin privilegios; data/ es donde la app lee el índice y los usuarios y escribe los tickets.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.getenv('PORT', '8000'), timeout=3)"

# exec: uvicorn pasa a ser el proceso principal y recibe las señales de parada.
# PORT permite que plataformas como Render elijan el puerto.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
