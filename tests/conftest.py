"""Pruebas automáticas. Por defecto usan una SQLite temporal; para probar en PostgreSQL:
    DATABASE_URL=postgresql://usuario:clave@localhost:5433/bd  pytest
(la base indicada se BORRA entre pruebas: usa una base de pruebas, nunca la real)."""
import os
import sys
import tempfile
import types
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/prueba.db"
os.environ["PBKDF2_ITER"] = "1000"          # claves rápidas solo en pruebas
os.environ["OPENAI_API_KEY"] = "falsa"
os.environ["WEBHOOK_TOKEN"] = "tok"
os.environ["BLOQUEO_POR_MORA"] = "1"
for var in ("ADMIN_USUARIO", "ADMIN_PASSWORD"):
    os.environ.pop(var, None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402
import main  # noqa: E402

ES_SQLITE = db.engine.dialect.name == "sqlite"


@pytest.fixture(autouse=True)
def base_limpia():
    db.init_db()
    with db.engine.begin() as conn:
        for tabla in reversed(db.metadata.sorted_tables):
            conn.execute(delete(tabla))
    auth._fallos.clear()
    yield


@pytest.fixture
def wsp(monkeypatch):
    """Intercepta los mensajes de WhatsApp que la app intenta enviar."""
    enviados = []

    async def falso(numero, texto):
        enviados.append((numero, texto))

    monkeypatch.setattr(main, "responder_wsp", falso)
    return enviados


@pytest.fixture(autouse=True)
def sin_admins_del_env(monkeypatch):
    """Las pruebas no deben depender de los números reales que haya en el .env local."""
    monkeypatch.setattr(main.config, "ADMIN_TELEFONOS", "")


@pytest.fixture(autouse=True)
def openai_falso(monkeypatch):
    class Falso:
        def __init__(self, api_key=None):
            self.audio = types.SimpleNamespace(transcriptions=types.SimpleNamespace(
                create=lambda **k: types.SimpleNamespace(text="Viene Pedro Gómez en moto, placa MOT456")))

            def parse(messages=None, **k):
                txt = messages[-1]["content"]
                vis = []
                if "viene" in txt.lower():
                    vis = [main.Visita(casa="X", nombre_visitante="Pedro Gómez" if "Pedro" in txt else "Juan Pérez",
                                       tipo=main.TipoVisita.VISITA, placa="ABC123" if "ABC" in txt else None)]
                return types.SimpleNamespace(choices=[types.SimpleNamespace(
                    message=types.SimpleNamespace(parsed=main.ExtraccionVisitas(visitas=vis)))])

            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(parse=parse))

    monkeypatch.setattr(main, "OpenAI", Falso)


def _cliente(usuario, rol):
    auth.crear_usuario(usuario, usuario.title(), rol, "clave-segura-1", debe_cambiar=False)
    c = TestClient(main.app)
    r = c.post("/api/login", json={"usuario": usuario, "clave": "clave-segura-1"})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def admin(base_limpia):
    return _cliente("admin", "ADMIN")


@pytest.fixture
def guardia(base_limpia):
    return _cliente("guardia1", "GUARDIA")


@pytest.fixture
def anonimo(base_limpia):
    return TestClient(main.app, follow_redirects=False)


def registrar(admin, telefono="0988391178", casa="Mz 6 Villa 23", nombre="Cristian"):
    r = admin.post("/api/residentes", json={"telefono": telefono, "casa": casa, "nombre": nombre})
    assert r.status_code == 200, r.text
    return r.json()


def webhook(cliente, telefono, mensaje):
    cuerpo = {"event": "messages.upsert", "data": {
        "key": {"remoteJid": f"{telefono}@s.whatsapp.net", "fromMe": False, "id": "X"}, "message": mensaje}}
    r = cliente.post("/api/whatsapp/webhook?token=tok", json=cuerpo)
    assert r.status_code == 200, r.text
    return r.json()


def texto(cliente, telefono, t):
    return webhook(cliente, telefono, {"conversation": t})
