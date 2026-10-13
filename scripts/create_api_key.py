"""Crea un usuario con su API key en data/users.json (el archivo guarda solo el hash).

Ejemplos (desde la raíz del proyecto, con el entorno virtual activo):
    python scripts/create_api_key.py admin --all --admin
    python scripts/create_api_key.py ana --groups compute storage

Grupos disponibles: ver corpus/access.json (compute, networking, storage, security, operations).
La API key se muestra UNA sola vez: cópiala ahora.
"""
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.security import add_user, load_access_map  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("name")
parser.add_argument("--groups", nargs="*", default=[])
parser.add_argument("--all", action="store_true", help="acceso a todos los documentos")
parser.add_argument("--admin", action="store_true", help="puede consultar /metrics")
args = parser.parse_args()

known = set(load_access_map().values())
unknown = set(args.groups) - known
if unknown:
    sys.exit(f"Grupos desconocidos: {sorted(unknown)}. Disponibles: {sorted(known)}")

try:
    key = add_user(args.name, ["*"] if args.all else args.groups, args.admin)
except ValueError as error:
    sys.exit(str(error))

print(f"Usuario creado: {args.name}")
print(f"API key (cópiala ahora, no se vuelve a mostrar): {key}")
