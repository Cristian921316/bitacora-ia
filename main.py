import json
import os
import sqlite3
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from openai import OpenAI
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "bitacora.db"
STATIC_DIR = BASE_DIR / "static"
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "gpt-4o-mini")

app = FastAPI(title="Bitácora Residencial por Voz")


# ---------- Modelos ----------
class TipoVisita(str, Enum):
    VISITA = "VISITA"
    DELIVERY = "DELIVERY"
    TAXI_UBER = "TAXI_UBER"
    SERVICIO_TECNICO = "SERVICIO_TECNICO"


class Visita(BaseModel):
    casa: str
    nombre_visitante: str
    tipo: TipoVisita
    placa: Optional[str] = None
    observaciones: Optional[str] = None


class ExtraccionVisitas(BaseModel):
    visitas: List[Visita]


class CambioEstado(BaseModel):
    estado: str


# ---------- Base de datos ----------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accesos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                casa TEXT NOT NULL,
                nombre_visitante TEXT NOT NULL,
                tipo TEXT NOT NULL,
                placa TEXT,
                observaciones TEXT,
                estado TEXT NOT NULL DEFAULT 'PRE_AUTORIZADO'
                    CHECK (estado IN ('PRE_AUTORIZADO','ADENTRO','SALIO')),
                autorizado_por TEXT,
                fecha_hora TEXT NOT NULL
            )
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM accesos").fetchone()[0]
        if count == 0:
            now = datetime.now().isoformat(timespec="seconds")
            seeds = [
                ("12", "María González", "VISITA", "ABC-123", "Viene a almorzar", "PRE_AUTORIZADO", "Residente Casa 12", now),
                ("7", "Pedro Ramírez", "SERVICIO_TECNICO", "XYZ-789", "Reparación de aire acondicionado", "ADENTRO", "Residente Casa 7", now),
                ("3", "Rappi", "DELIVERY", None, "Pedido de comida", "PRE_AUTORIZADO", "Residente Casa 3", now),
            ]
            conn.executemany(
                """INSERT INTO accesos
                (casa, nombre_visitante, tipo, placa, observaciones, estado, autorizado_por, fecha_hora)
                VALUES (?,?,?,?,?,?,?,?)""",
                seeds,
            )


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


# ---------- WebSocket ----------
class ConnectionManager:
    def __init__(self) -> None:
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        text = json.dumps(message, ensure_ascii=False)
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:
                self.disconnect(ws)


manager = ConnectionManager()


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.websocket("/ws/bitacora")
async def ws_bitacora(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # mantiene viva la conexión
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ---------- API ----------
@app.get("/api/accesos")
def listar_accesos():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM accesos ORDER BY id DESC").fetchall()
    return [row_to_dict(r) for r in rows]


SYSTEM_PROMPT = (
    "Eres el asistente de la garita de un conjunto residencial. Del texto transcrito de una nota de voz "
    "de un residente, extrae TODAS las visitas que está pre-autorizando. Para cada una devuelve: "
    "casa (número o nombre de la casa del residente), nombre_visitante, tipo (VISITA, DELIVERY, TAXI_UBER "
    "o SERVICIO_TECNICO), placa (null si no se menciona) y observaciones (null si no hay). "
    "Si no se menciona el nombre del visitante usa una descripción corta (p. ej. 'Repartidor', 'Taxi'). "
    "Si el audio no contiene ninguna visita devuelve una lista vacía."
)


@app.post("/api/procesar-voz")
async def procesar_voz(audio: UploadFile = File(...)):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(500, "OPENAI_API_KEY no está configurada en el archivo .env")

    data = await audio.read()
    if not data:
        raise HTTPException(400, "Audio vacío")

    client = OpenAI(api_key=api_key)
    filename = audio.filename or "nota.webm"
    try:
        transcripcion = client.audio.transcriptions.create(
            model="whisper-1",
            file=(filename, data, audio.content_type or "audio/webm"),
            language="es",
        )
        texto = transcripcion.text

        completion = client.chat.completions.parse(
            model=EXTRACTION_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": texto},
            ],
            response_format=ExtraccionVisitas,
        )
        extraccion = completion.choices[0].message.parsed
    except Exception as exc:  # errores de OpenAI
        raise HTTPException(502, f"Error procesando el audio con OpenAI: {exc}")

    visitas = extraccion.visitas if extraccion else []
    creados = []
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        for v in visitas:
            cur = conn.execute(
                """INSERT INTO accesos
                (casa, nombre_visitante, tipo, placa, observaciones, estado, autorizado_por, fecha_hora)
                VALUES (?,?,?,?,?,'PRE_AUTORIZADO',?,?)""",
                (v.casa, v.nombre_visitante, v.tipo.value, v.placa, v.observaciones,
                 f"Residente Casa {v.casa}", now),
            )
            row = conn.execute("SELECT * FROM accesos WHERE id=?", (cur.lastrowid,)).fetchone()
            creados.append(row_to_dict(row))

    for reg in creados:
        await manager.broadcast({"evento": "nuevo", "registro": reg})

    return {"transcripcion": texto, "registros": creados}


@app.post("/api/cambiar-estado/{acceso_id}")
async def cambiar_estado(acceso_id: int, cambio: CambioEstado):
    nuevo = cambio.estado.upper()
    if nuevo not in ("ADENTRO", "SALIO"):
        raise HTTPException(400, "Estado inválido: use ADENTRO o SALIO")

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM accesos WHERE id=?", (acceso_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Registro no encontrado")
        actual = row["estado"]
        permitidas = {"PRE_AUTORIZADO": {"ADENTRO", "SALIO"}, "ADENTRO": {"SALIO"}, "SALIO": set()}
        if nuevo not in permitidas[actual]:
            raise HTTPException(409, f"No se puede pasar de {actual} a {nuevo}")
        conn.execute("UPDATE accesos SET estado=? WHERE id=?", (nuevo, acceso_id))
        row = conn.execute("SELECT * FROM accesos WHERE id=?", (acceso_id,)).fetchone()

    reg = row_to_dict(row)
    await manager.broadcast({"evento": "estado", "registro": reg})
    return reg


# ---------- Frontend ----------
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "residente.html")


@app.get("/residente")
def residente():
    return FileResponse(STATIC_DIR / "residente.html")


@app.get("/garita")
def garita():
    return FileResponse(STATIC_DIR / "garita.html")
