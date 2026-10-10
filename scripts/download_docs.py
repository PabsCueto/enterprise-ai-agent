"""Descarga las páginas de corpus/sources.txt y guarda su texto en data/raw/.

Uso (desde la raíz del proyecto, con el entorno virtual activo):
    python scripts/download_docs.py
"""
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "corpus" / "sources.txt"
OUT_DIR = Path(os.getenv("RAW_DOCS_DIR", ROOT / "data" / "raw"))
HEADERS = {"User-Agent": "enterprise-ai-agent-portfolio/0.1 (aprendizaje personal)"}


def slug_from_url(url: str) -> str:
    """Convierte una URL en un nombre de archivo seguro."""
    path = urlparse(url).path.strip("/").replace("/", "_")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", path) or "index"


def extract_text(html: str) -> str:
    """Extrae el texto principal de la página, sin menús ni scripts."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    main = soup.find("main") or soup.body or soup
    lines = [line.strip() for line in main.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    urls = [
        line.strip()
        for line in SOURCES.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for url in urls:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        response.encoding = "utf-8"
        text = extract_text(response.text)
        target = OUT_DIR / f"{slug_from_url(url)}.txt"
        target.write_text(f"Fuente: {url}\n\n{text}", encoding="utf-8")
        print(f"OK  {len(text):>8} caracteres  ->  {target.name}")
        time.sleep(1)  # Cortesía con el servidor: una petición por segundo.


if __name__ == "__main__":
    main()
