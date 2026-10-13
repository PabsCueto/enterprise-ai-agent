"""Usuarios y mapa de acceso de prueba."""
from app.security import User, hash_key

ADMIN_KEY = "clave-admin-de-prueba"
ADMIN = User("admin", hash_key(ADMIN_KEY), ("*",), True)
ACCESS_MAP = {"example.com/lambda": "compute", "example.com/iam": "security"}


def patch_security(monkeypatch, users=None):
    """Sustituye la carga de usuarios y del mapa de acceso en la API."""
    import app.main as main

    monkeypatch.setattr(main, "load_users", lambda: users if users is not None else [ADMIN])
    monkeypatch.setattr(main, "load_access_map", lambda: ACCESS_MAP)
