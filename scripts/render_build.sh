#!/usr/bin/env bash
# Comando de build para Render: instala dependencias, descarga los documentos públicos de AWS
# y construye el índice. El índice queda en data/index.json y se despliega junto con el código,
# así el arranque (y cada despertar tras 15 min de inactividad) no vuelve a gastar embeddings.
set -euo pipefail

: "${GEMINI_API_KEY:?GEMINI_API_KEY no está definida en el entorno de build}"

pip install -r requirements.txt
python scripts/download_docs.py
python scripts/build_index.py
