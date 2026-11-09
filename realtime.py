"""Avisos en vivo por WebSocket. Cada conexión sabe el rol de su usuario: al guardia solo se le envían
los campos que necesita para operar (sin teléfonos ni texto de los audios)."""
import json
from typing import List, Optional, Tuple

from fastapi import WebSocket

CAMPOS_GUARDIA = (
    "id", "casa", "nombre_visitante", "tipo", "placa", "observaciones", "estado",
    "autorizado_por", "fecha_hora", "hora_respuesta", "hora_ingreso",
)


def vista_guardia(reg: dict) -> dict:
    return {k: reg.get(k) for k in CAMPOS_GUARDIA}


def vista_para(rol: str, reg: dict) -> dict:
    return reg if rol == "ADMIN" else vista_guardia(reg)


class ConnectionManager:
    def __init__(self) -> None:
        self.active: List[Tuple[WebSocket, str]] = []

    async def connect(self, ws: WebSocket, rol: str) -> None:
        await ws.accept()
        self.active.append((ws, rol))

    def disconnect(self, ws: WebSocket) -> None:
        self.active = [(w, r) for (w, r) in self.active if w is not ws]

    async def broadcast(self, message: dict) -> None:
        reg: Optional[dict] = message.get("registro")
        for ws, rol in list(self.active):
            msg = dict(message)
            if reg is not None:
                msg["registro"] = vista_para(rol, reg)
            try:
                await ws.send_text(json.dumps(msg, ensure_ascii=False))
            except Exception:
                self.disconnect(ws)


manager = ConnectionManager()
