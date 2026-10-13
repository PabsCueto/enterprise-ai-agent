"""Seguridad: API keys (solo se guarda su hash), acceso por grupo de documentos y límite de uso.

- Cada usuario tiene una API key. El servidor guarda únicamente su hash SHA-256.
- Cada usuario pertenece a grupos ("compute", "storage"...) o a "*" (todos).
- Cada documento pertenece a un grupo según corpus/access.json. El filtro se aplica en la
  búsqueda, ANTES de que el modelo vea nada: un documento sin permiso nunca entra al prompt.
- Un documento que no aparece en access.json queda en el grupo "unmapped": solo lo ve "*".
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNMAPPED = "unmapped"
NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")  # se usa en logs y en el id del hilo


@dataclass(frozen=True)
class User:
    name: str
    key_hash: str
    groups: tuple = ()
    admin: bool = False


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def users_path() -> Path:
    return Path(os.getenv("USERS_FILE", ROOT / "data" / "users.json"))


def load_users(path=None) -> list:
    path = Path(path) if path else users_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        User(u["name"], u["key_sha256"], tuple(u.get("groups", [])), bool(u.get("admin", False)))
        for u in data.get("users", [])
    ]


def add_user(name: str, groups: list, admin: bool = False, path=None) -> str:
    """Crea un usuario y devuelve su API key. La key solo se muestra esta vez: se guarda su hash."""
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError("El nombre solo admite letras, números, guion y guion bajo (máx. 32).")
    path = Path(path) if path else users_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"users": []}
    if any(u["name"] == name for u in data["users"]):
        raise ValueError(f"El usuario {name!r} ya existe.")
    key = "eaa_" + secrets.token_urlsafe(32)
    data["users"].append({"name": name, "key_sha256": hash_key(key), "groups": list(groups), "admin": admin})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)  # solo lectura/escritura para tu usuario
    return key


def authenticate(key, users: list):
    """Devuelve el usuario dueño de la key, o None. Compara en tiempo constante."""
    if not key:
        return None
    candidate = hash_key(key)
    found = None
    for user in users:  # se recorren todos para no revelar por tiempo cuál coincidió
        if hmac.compare_digest(candidate, user.key_hash):
            found = user
    return found


def load_access_map(path=None) -> dict:
    path = Path(path or os.getenv("ACCESS_FILE", ROOT / "corpus" / "access.json"))
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def group_for_source(source: str, access_map: dict) -> str:
    lowered = source.lower()
    for fragment, group in access_map.items():
        if fragment.lower() in lowered:
            return group
    return UNMAPPED


def make_doc_filter(groups, access_map: dict):
    """Filtro para la búsqueda vectorial. None significa sin restricción (admin o uso interno)."""
    if groups is None or "*" in groups:
        return None
    allowed = set(groups)
    return lambda doc: group_for_source(doc.metadata.get("source", ""), access_map) in allowed


class RateLimiter:
    """Ventana deslizante en memoria: como máximo `limit` peticiones por usuario cada `window` segundos."""

    def __init__(self, limit: int, window: float = 60.0, clock=time.monotonic) -> None:
        self.limit, self.window, self._clock = limit, window, clock
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str):
        """Registra la petición. Devuelve None si pasa, o los segundos de espera si se excede el límite."""
        if self.limit <= 0:
            return None
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(self.window - (now - hits[0]), 0.1)
            hits.append(now)
            return None
