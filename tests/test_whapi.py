"""Traductor del webhook de Whapi y llamadas directas a su API."""
import asyncio

import httpx

import whatsapp
from conftest import mensaje_whapi, registrar, webhook

DESCARGAR_REAL = whatsapp.descargar_audio      # la fixture automática la reemplaza durante las pruebas


def cuerpo(*mensajes, tipo="messages", evento="post"):
    return {"messages": list(mensajes), "event": {"type": tipo, "event": evento}, "channel_id": "X"}


def test_extrae_texto_y_voz():
    texto = whatsapp.extraer_mensajes(cuerpo(mensaje_whapi("593988391178", {"conversation": "hola"})))
    assert texto == [{"telefono": "593988391178", "tipo": "text", "texto": "hola", "voz": None}]
    voz = whatsapp.extraer_mensajes(cuerpo(mensaje_whapi("593988391178", {"audioMessage": {}})))[0]
    assert voz["texto"] is None and voz["voz"]["id"] == "oga-1"


def test_ignora_lo_que_no_es_un_mensaje_nuevo_de_una_persona():
    base = mensaje_whapi("593988391178", {"conversation": "hola"})
    assert whatsapp.extraer_mensajes(cuerpo(base, tipo="statuses")) == []          # lecturas y estados
    assert whatsapp.extraer_mensajes(cuerpo(base, evento="put")) == []             # ediciones
    assert whatsapp.extraer_mensajes(cuerpo({**base, "from_me": True})) == []      # mensajes propios
    assert whatsapp.extraer_mensajes(cuerpo({**base, "chat_id": "123-456@g.us"})) == []   # grupos
    assert whatsapp.extraer_mensajes(cuerpo({**base, "chat_id": "123@newsletter"})) == []  # canales
    assert whatsapp.extraer_mensajes({}) == []


def test_varios_mensajes_en_un_mismo_webhook():
    ms = whatsapp.extraer_mensajes(cuerpo(mensaje_whapi("593988391178", {"conversation": "uno"}),
                                         mensaje_whapi("593911111111", {"conversation": "dos"})))
    assert [m["telefono"] for m in ms] == ["593988391178", "593911111111"]


def test_webhook_responde_200_y_no_procesa_eventos_ajenos(admin, wsp):
    r = admin.post("/api/whatsapp/webhook?token=tok", json={"event": {"type": "statuses", "event": "post"},
                                                             "statuses": [{"status": "read"}]})
    assert r.status_code == 200 and r.json()["ignorado"]
    assert wsp == []


def test_webhook_exige_el_token_y_json(admin, monkeypatch):
    assert admin.post("/api/whatsapp/webhook", json={}).status_code == 401
    assert admin.post("/api/whatsapp/webhook?token=otra", json={}).status_code == 401
    assert admin.post("/api/whatsapp/webhook?token=tok", content="no es json").status_code == 400
    monkeypatch.setattr("main.WEBHOOK_TOKEN", "")          # sin secreto configurado se rechaza todo
    assert admin.post("/api/whatsapp/webhook?token=", json={}).status_code == 401


def test_mensaje_propio_o_de_grupo_no_responde(admin, wsp):
    registrar(admin)
    webhook(admin, "593988391178", {"conversation": "viene Juan"}, from_me=True)
    webhook(admin, "593988391178", {"conversation": "viene Juan"}, chat_id="1203-99@g.us")
    assert wsp == []


def test_envio_directo_a_whapi(monkeypatch):
    pedidos = []

    def responder(request):
        pedidos.append(request)
        return httpx.Response(200, json={"sent": True})

    cliente_real = httpx.AsyncClient
    monkeypatch.setattr(whatsapp.httpx, "AsyncClient",
                        lambda **kw: cliente_real(transport=httpx.MockTransport(responder), **kw))
    monkeypatch.setattr(whatsapp.config, "WHAPI_TOKEN", "t0ken")
    asyncio.run(whatsapp.enviar_texto("+593 98-839-1178", "hola"))
    r = pedidos[0]
    assert str(r.url) == "https://gate.whapi.cloud/messages/text"
    assert r.headers["authorization"] == "Bearer t0ken"
    assert r.read() == b'{"to":"593988391178","body":"hola"}'


def test_descarga_de_audio_por_enlace_o_por_id(monkeypatch):
    pedidos = []

    def responder(request):
        pedidos.append(str(request.url))
        return httpx.Response(200, content=b"OGG")

    cliente_real = httpx.AsyncClient
    monkeypatch.setattr(whatsapp.httpx, "AsyncClient",
                        lambda **kw: cliente_real(transport=httpx.MockTransport(responder), **kw))
    assert asyncio.run(DESCARGAR_REAL({"id": "a1", "link": "https://nube.test/a1.oga"})) == b"OGG"
    assert asyncio.run(DESCARGAR_REAL({"id": "a2"})) == b"OGG"
    assert pedidos == ["https://nube.test/a1.oga", "https://gate.whapi.cloud/media/a2"]
