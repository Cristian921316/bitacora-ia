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

# WhatsApp (Whapi.Cloud): token del canal y secreto que va en la URL del webhook
WHAPI_URL = os.getenv("WHAPI_URL", "https://gate.whapi.cloud")
WHAPI_TOKEN = os.getenv("WHAPI_TOKEN", "")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
# Celulares de administradores que pueden pedir el resumen por WhatsApp ("reporte de hoy"). Separados por coma.
ADMIN_TELEFONOS = os.getenv("ADMIN_TELEFONOS", "")
