"""Configuración central. Todo sale de variables de entorno (o del archivo .env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")  # no pisa variables que ya vengan del entorno

STATIC_DIR = BASE_DIR / "static"


def _normalizar_url(url: str) -> str:
    # Los proveedores en la nube suelen dar "postgres://..." o "postgresql://...": se usa el driver psycopg 3
    for prefijo in ("postgres://", "postgresql://"):
        if url.startswith(prefijo):
            return "postgresql+psycopg://" + url[len(prefijo):]
    return url


# Hoy: SQLite en un archivo. Mañana: basta cambiar esta variable, p. ej.
# DATABASE_URL=postgresql://usuario:clave@servidor:5432/bitacora
DATABASE_URL = _normalizar_url(os.getenv("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'bitacora.db'}")

EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "gpt-4o-mini")

# Sesiones
SESION_HORAS = int(os.getenv("SESION_HORAS", "12"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"  # poner COOKIE_SECURE=1 cuando haya HTTPS

# Una casa impaga no puede recibir visitas (visitas anunciadas, sin aviso ni ingreso). 0 = desactivado.
BLOQUEO_POR_MORA = os.getenv("BLOQUEO_POR_MORA", "1") == "1"

# WhatsApp (Evolution API)
EVOLUTION_URL = os.getenv("EVOLUTION_URL", "http://localhost:8080")
EVOLUTION_APIKEY = os.getenv("EVOLUTION_APIKEY", "")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "bitacora")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
