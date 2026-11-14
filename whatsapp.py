"""Conexión directa con Whapi.Cloud (WhatsApp). Sin intermediarios: solo dos llamadas HTTP y un traductor del webhook.

- Enviar:   POST {WHAPI_URL}/messages/text   {"to": "593988391178", "body": "..."}   (Authorization: Bearer <token>)
- Recibir:  Whapi llama a /api/whatsapp/webhook?token=...  con {"messages": [...], "event": {"type": "messages", "event": "post"}}
- Audios:   la nota de voz trae "voice": {"id", "link"?}. Con "Auto Download" activo viene un enlace; si no, se pide por /media/{id}.
"""
import httpx

import config
from util import solo_digitos


def _cabeceras() -> dict:
    return {"Authorization": f"Bearer {config.WHAPI_TOKEN}"}


async def enviar_texto(numero: str, texto: str) -> None:
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(f"{config.WHAPI_URL}/messages/text", json={"to": solo_digitos(numero), "body": texto},
                               headers=_cabeceras())
        resp.raise_for_status()


async def descargar_audio(voz: dict) -> bytes:
    """Devuelve los bytes de una nota de voz recibida (formato ogg/opus)."""
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
        if voz.get("link"):
            resp = await http.get(voz["link"])
        else:
            resp = await http.get(f"{config.WHAPI_URL}/media/{voz['id']}", headers=_cabeceras())
        resp.raise_for_status()
        return resp.content


def extraer_mensajes(cuerpo: dict) -> list:
    """Traduce el webhook de Whapi a [{telefono, tipo, texto, voz}], dejando fuera lo que no es un mensaje nuevo
    de una persona: estados de lectura, eventos del canal, mensajes propios, grupos y canales."""
    evento = cuerpo.get("event") or {}
    if evento.get("type") != "messages" or evento.get("event") != "post":
        return []
    salida = []
    for m in cuerpo.get("messages") or []:
        if m.get("from_me"):
            continue
        chat = str(m.get("chat_id") or "")
        if not (chat.endswith("@s.whatsapp.net") or chat.endswith("@lid")):   # grupos (@g.us) y canales: no
            continue
        telefono = solo_digitos(m.get("from") or chat.split("@")[0])
        if not telefono:
            continue
        tipo = m.get("type")
        texto = (m.get("text") or {}).get("body") if tipo == "text" else None
        voz = (m.get("voice") or m.get("audio")) if tipo in ("voice", "audio") else None
        salida.append({"telefono": telefono, "tipo": tipo, "texto": texto, "voz": voz})
    return salida
