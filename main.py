"""Bitácora residencial por voz: visitas, garita en tiempo real y WhatsApp.

Módulos:  config.py (ajustes) · db.py (base de datos portable) · auth.py (login y roles)
          realtime.py (avisos en vivo) · reportes.py (reportes y Excel) · main.py (visitas, residentes, WhatsApp)
"""
import logging
import os
import re
import secrets
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional

from fastapi import (BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, UploadFile, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel
from sqlalchemy import and_, delete, func, insert, or_, select, text, update
from sqlalchemy.exc import IntegrityError

import auth
import config
import parametros
import whatsapp
from auth import pagina, solo_admin, usuario_actual, usuario_ws
from db import (accesos, ahora_iso, asegurar_casa, casa_al_dia, casas, clave_casa, engine, fila, init_db,
                movimientos, registrar_bloqueo, residentes)
from importar import router as importar_router
from realtime import manager, vista_para
from reportes import _norm
from resumen import AYUDA_ADMIN, armar_resumen, detectar_periodo
from reportes import router as reportes_router
from util import normalizar_telefono, solo_digitos

STATIC_DIR = config.STATIC_DIR
EXTRACTION_MODEL = config.EXTRACTION_MODEL

app = FastAPI(title="Bitácora Residencial por Voz")
app.include_router(auth.router)
app.include_router(reportes_router)
app.include_router(importar_router)


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


class TextoIn(BaseModel):
    texto: str


class ResidenteIn(BaseModel):
    telefono: Optional[str] = None  # celular local (0988391178) o con código de país (593988391178); opcional
    casa: str
    nombre: Optional[str] = None
    cedula: Optional[str] = None


class ResidentePatch(BaseModel):
    """Edición parcial: solo se cambian los campos que vienen en la petición."""
    nombre: Optional[str] = None
    cedula: Optional[str] = None
    telefono: Optional[str] = None
    casa: Optional[str] = None


class CasaPagoIn(BaseModel):
    casa: str
    al_dia: bool


class SinAviso(BaseModel):
    mz: str
    villa: str
    nombre_visitante: str
    tipo: TipoVisita = TipoVisita.VISITA
    placa: Optional[str] = None
    observaciones: Optional[str] = None
    # A quién de la casa se le pregunta. Sin este campo se pregunta a todos; lista vacía = a nadie (se registra igual)
    residente_ids: Optional[List[int]] = None


MAX_MZ_VILLA = 50                       # caracteres de la manzana y de la villa
MAX_CASA = 3 + MAX_MZ_VILLA + 7 + MAX_MZ_VILLA   # "Mz " + mz + " Villa " + villa

# Mensajes neutros: no revelan el motivo (el guardia puede ver el chat del número de garita)
def msg_mora_sin_aviso(casa: str) -> str:
    return (f"El residente de {casa} no está al día en sus pagos. Debe comunicarse con administración. "
            "No se puede avisar de esta visita.")


MSG_BLOQUEO_GARITA = "Esta casa no puede recibir visitas por ahora. Comuníquese con administración."
MSG_BLOQUEO_WSP = "Por ahora no es posible registrar visitas desde tu casa. Por favor comunícate con la administración."


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    auth.bootstrap_admin()


@app.get("/salud")
def salud():
    """Para monitoreo: responde 200 si la app y la base de datos funcionan."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"ok": True}


# ---------- Tiempo real ----------
@app.websocket("/ws/bitacora")
async def ws_bitacora(ws: WebSocket):
    u = usuario_ws(ws)
    if not u or u["debe_cambiar_clave"]:
        await ws.close(code=4401)
        return
    await manager.connect(ws, u["rol"])
    try:
        while True:
            await ws.receive_text()  # mantiene viva la conexión
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ---------- Visitas ----------
@app.get("/api/config")
def api_config(_: dict = Depends(usuario_actual)):
    """Parámetros que necesita la pantalla de garita, y la hora del servidor (para que los 'hace N min' no
    dependan de la zona horaria del navegador)."""
    return {"vigencia_horas": parametros.valor("vigencia_horas"),
            "rechazo_visible_minutos": parametros.valor("rechazo_visible_minutos"),
            "control_salidas": bool(parametros.valor("control_salidas")),
            "ahora_servidor": ahora_iso()}


@app.get("/api/accesos")
def listar_accesos(todos: bool = False, u: dict = Depends(usuario_actual)):
    """Lo que necesita la garita: visitas vigentes por llegar, las que están adentro y rechazos recientes.
    `todos=1` (solo ADMIN) devuelve todo, marcando como VENCIDA lo que superó su vigencia."""
    if todos and u["rol"] != "ADMIN":
        raise HTTPException(403, "No tienes permiso para esta acción")
    limite = parametros.limite_vigencia()
    stmt = select(accesos)
    if not todos:
        rechazo_desde = (datetime.now() - timedelta(minutes=parametros.valor("rechazo_visible_minutos"))
                         ).isoformat(timespec="seconds")
        condiciones = [
            and_(accesos.c.estado.in_(parametros.ESTADOS_CON_VIGENCIA), accesos.c.fecha_hora >= limite),
            and_(accesos.c.estado == "RECHAZADO", accesos.c.hora_respuesta >= rechazo_desde)]
        if parametros.valor("control_salidas"):   # solo si se controlan las salidas se muestran los que están adentro
            condiciones.append(accesos.c.estado == "ADENTRO")
        stmt = stmt.where(or_(*condiciones))
    with engine.connect() as conn:
        regs = [fila(r) for r in conn.execute(stmt.order_by(accesos.c.id.desc())).all()]
    if todos:
        for r in regs:
            r["estado"] = parametros.estado_efectivo(r, limite)
    return [vista_para(u["rol"], r) for r in regs]


# ---------- Parámetros de operación (solo ADMIN) ----------
class ParametroIn(BaseModel):
    valor: int


@app.get("/api/parametros")
def api_parametros(_: dict = Depends(solo_admin)):
    return parametros.listar()


@app.put("/api/parametros/{clave}")
def api_guardar_parametro(clave: str, datos: ParametroIn, u: dict = Depends(solo_admin)):
    try:
        parametros.guardar(clave, datos.valor, u["usuario"])
    except KeyError:
        raise HTTPException(404, "Parámetro desconocido")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return next(p for p in parametros.listar() if p["clave"] == clave)


SYSTEM_PROMPT = (
    "Eres el asistente de la garita de un conjunto residencial. Del mensaje de un residente (escrito o "
    "transcrito de una nota de voz), extrae TODAS las visitas que está pre-autorizando. Para cada una devuelve: "
    "casa (número o nombre de la casa del residente), nombre_visitante, tipo (VISITA, DELIVERY, TAXI_UBER "
    "o SERVICIO_TECNICO), placa (null si no se menciona) y observaciones (null si no hay). "
    "Si no se menciona el nombre del visitante usa una descripción corta (p. ej. 'Repartidor', 'Taxi'). "
    "Si el audio no contiene ninguna visita devuelve una lista vacía."
)


@app.post("/api/procesar-voz")
async def procesar_voz(audio: UploadFile = File(...), _: dict = Depends(solo_admin)):
    data = await audio.read()
    return await procesar_audio(data, audio.filename or "nota.webm", audio.content_type or "audio/webm")


async def procesar_audio(
    data: bytes, filename: str, content_type: str, casa_forzada: Optional[str] = None,
    origen: str = "web", telefono: Optional[str] = None,
) -> dict:
    """Transcribe, extrae visitas, guarda y notifica. Si casa_forzada viene
    (p. ej. por el número de WhatsApp), reemplaza la casa que dedujo la IA."""
    return await extraer_y_guardar(transcribir(data, filename, content_type), casa_forzada, origen, telefono)


def transcribir(data: bytes, filename: str, content_type: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(500, "OPENAI_API_KEY no está configurada en el archivo .env")
    if not data:
        raise HTTPException(400, "Audio vacío")
    client = OpenAI(api_key=api_key)
    try:
        return client.audio.transcriptions.create(
            model="whisper-1", file=(filename, data, content_type), language="es").text
    except Exception as exc:
        raise HTTPException(502, f"Error transcribiendo el audio con OpenAI: {exc}")


MAX_TEXTO = 1000


async def procesar_texto(
    texto: str, casa_forzada: Optional[str] = None, origen: str = "web", telefono: Optional[str] = None
) -> dict:
    """Igual que procesar_audio pero para un mensaje escrito (no necesita Whisper)."""
    texto = (texto or "").strip()
    if not texto:
        raise HTTPException(400, "Mensaje vacío")
    if len(texto) > MAX_TEXTO:
        raise HTTPException(400, f"Mensaje demasiado largo (máximo {MAX_TEXTO} caracteres)")
    return await extraer_y_guardar(texto, casa_forzada, origen, telefono)


@app.post("/api/procesar-texto")
async def api_procesar_texto(t: TextoIn, _: dict = Depends(solo_admin)):
    return await procesar_texto(t.texto)


async def extraer_y_guardar(
    texto: str, casa_forzada: Optional[str] = None, origen: str = "web", telefono: Optional[str] = None
) -> dict:
    """Extrae las visitas del texto con IA, las guarda y avisa a la garita por WebSocket."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(500, "OPENAI_API_KEY no está configurada en el archivo .env")
    client = OpenAI(api_key=api_key)
    try:
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
        raise HTTPException(502, f"Error procesando el mensaje con OpenAI: {exc}")

    visitas = extraccion.visitas if extraccion else []
    creados = []
    ahora = ahora_iso()
    with engine.begin() as conn:
        for v in visitas:
            casa = casa_forzada or v.casa
            nuevo_id = conn.execute(insert(accesos).values(
                casa=casa, nombre_visitante=v.nombre_visitante, tipo=v.tipo.value, placa=v.placa,
                observaciones=v.observaciones, estado="PRE_AUTORIZADO", autorizado_por=f"Residente Casa {casa}",
                fecha_hora=ahora, transcripcion=texto, origen=origen, telefono=telefono,
            )).inserted_primary_key[0]
            conn.execute(insert(movimientos).values(acceso_id=nuevo_id, evento="REGISTRADO", fecha_hora=ahora, origen=origen))
            creados.append(fila(conn.execute(select(accesos).where(accesos.c.id == nuevo_id)).first()))

    for reg in creados:
        await manager.broadcast({"evento": "nuevo", "registro": reg})

    return {"transcripcion": texto, "registros": creados}


PERMITIDAS = {
    "PRE_AUTORIZADO": {"ADENTRO", "SALIO"},
    "ADENTRO": {"SALIO"},
    "SALIO": set(),
    "PENDIENTE": {"ADENTRO", "SALIO"},  # ADENTRO = el guardia ingresa sin respuesta del residente
    "RECHAZADO": set(),                  # el residente dijo que no: no se puede ingresar
}


@app.post("/api/cambiar-estado/{acceso_id}")
async def cambiar_estado(acceso_id: int, cambio: CambioEstado, background: BackgroundTasks,
                         u: dict = Depends(usuario_actual)):
    nuevo = cambio.estado.upper()
    if nuevo not in ("ADENTRO", "SALIO"):
        raise HTTPException(400, "Estado inválido: use ADENTRO o SALIO")

    with engine.connect() as conn:
        row = fila(conn.execute(select(accesos).where(accesos.c.id == acceso_id)).first())
        if not row:
            raise HTTPException(404, "Registro no encontrado")
        en_mora = nuevo == "ADENTRO" and config.BLOQUEO_POR_MORA and not casa_al_dia(conn, row["casa"])
    actual = row["estado"]
    if nuevo not in PERMITIDAS[actual]:
        raise HTTPException(409, f"No se puede pasar de {actual} a {nuevo}")
    if nuevo == "ADENTRO" and parametros.estado_efectivo(row, parametros.limite_vigencia()) == "VENCIDA":
        raise HTTPException(409, f"Esta visita venció (pasaron más de {parametros.valor('vigencia_horas')} horas desde "
                                 "que se registró). Debe registrarse de nuevo.")
    if en_mora and u["rol"] != "ADMIN":  # solo el administrador puede hacer una excepción
        with engine.begin() as conn:
            registrar_bloqueo(conn, row["casa"], "garita", f"Intento de ingreso: {row['nombre_visitante']}",
                              usuario=u["usuario"])
        raise HTTPException(409, MSG_BLOQUEO_GARITA)

    ahora = ahora_iso()
    columna_hora = "hora_ingreso" if nuevo == "ADENTRO" else "hora_salida"
    with engine.begin() as conn:
        r = conn.execute(update(accesos).where(accesos.c.id == acceso_id, accesos.c.estado == actual)
                         .values(estado=nuevo, **{columna_hora: ahora}))
        if r.rowcount == 0:
            raise HTTPException(409, "El registro cambió mientras lo procesabas. Recarga la pantalla.")
        conn.execute(insert(movimientos).values(
            acceso_id=acceso_id, evento="INGRESO" if nuevo == "ADENTRO" else "SALIDA", fecha_hora=ahora,
            origen="garita" if u["rol"] == "GUARDIA" else "admin", usuario=u["usuario"]))
        reg = fila(conn.execute(select(accesos).where(accesos.c.id == acceso_id)).first())

    await manager.broadcast({"evento": "estado", "registro": reg})
    if nuevo == "ADENTRO":  # en segundo plano: si WhatsApp tarda, el guardia no espera
        background.add_task(notificar_ingreso, reg, actual == "PENDIENTE")
    return vista_para(u["rol"], reg)


# ---------- Residentes y estado de pago ----------
@app.post("/api/residentes")
def registrar_residente(r: ResidenteIn, _: dict = Depends(solo_admin)):
    """Registra a una persona en una casa. El celular (WhatsApp) es opcional y se puede completar después.
    Si la casa es nueva, nace 'al día'."""
    casa = " ".join(r.casa.split())
    if not casa:
        raise HTTPException(400, "La casa es obligatoria")
    if len(casa) > MAX_CASA:
        raise HTTPException(400, f"La casa es demasiado larga (máximo {MAX_CASA} caracteres)")
    nombre = (r.nombre or "").strip() or None
    cedula = (r.cedula or "").replace(" ", "").strip() or None
    if cedula and len(cedula) > 20:
        raise HTTPException(400, "La cédula admite hasta 20 caracteres")
    tel = None
    if (r.telefono or "").strip():
        tel = normalizar_telefono(r.telefono)
        if not 11 <= len(tel) <= 15:
            raise HTTPException(400, "Celular inválido: use el formato 0988391178 o 593988391178")
    with engine.begin() as conn:
        if tel and conn.execute(select(residentes.c.id).where(residentes.c.telefono == tel)).first():
            conn.execute(update(residentes).where(residentes.c.telefono == tel).values(casa=casa, nombre=nombre))
        elif not tel and nombre and conn.execute(select(residentes.c.id).where(
                func.lower(residentes.c.nombre) == nombre.lower(), residentes.c.casa == casa)).first():
            raise HTTPException(409, "Esa persona ya está registrada en esa casa")
        else:
            conn.execute(insert(residentes).values(telefono=tel, cedula=cedula, nombre=nombre, casa=casa))
        asegurar_casa(conn, casa)
    return {"telefono": tel, "casa": casa, "nombre": nombre, "cedula": cedula}


def _con_pago(r: dict, pagos: dict) -> dict:
    c = pagos.get(clave_casa(r["casa"]))
    r["al_dia"] = True if c is None else bool(c.al_dia)
    r["pago_actualizado"] = c.actualizado_en if c is not None else None
    r["pago_actualizado_por"] = c.actualizado_por if c is not None else None
    return r


@app.get("/api/residentes")
def listar_residentes(_: dict = Depends(solo_admin)):
    with engine.connect() as conn:
        rs = [fila(x) for x in conn.execute(select(residentes)).all()]
        pagos = {c.clave: c for c in conn.execute(select(casas)).all()}
    return [_con_pago(r, pagos) for r in rs]


@app.patch("/api/residentes/{rid}")
def editar_residente(rid: int, datos: ResidentePatch, _: dict = Depends(solo_admin)):
    """Edita nombre, cédula, celular o casa de una persona (la pantalla lo usa en línea, un campo a la vez)."""
    campos = datos.model_dump(exclude_unset=True)
    cambios: dict = {}
    if "nombre" in campos:
        nombre = (campos["nombre"] or "").strip()
        if len(nombre) > 200:
            raise HTTPException(400, "El nombre admite hasta 200 caracteres")
        cambios["nombre"] = nombre or None
    if "cedula" in campos:
        cedula = (campos["cedula"] or "").replace(" ", "").strip()
        if len(cedula) > 20:
            raise HTTPException(400, "La cédula admite hasta 20 caracteres")
        cambios["cedula"] = cedula or None
    if "telefono" in campos:
        crudo = (campos["telefono"] or "").strip()
        if not crudo:
            cambios["telefono"] = None          # vaciar el campo quita el celular
        else:
            tel = normalizar_telefono(crudo)
            if not 11 <= len(tel) <= 15:
                raise HTTPException(400, "Celular inválido: use el formato 0988391178 o 593988391178")
            cambios["telefono"] = tel
    if "casa" in campos:
        casa = " ".join((campos["casa"] or "").split())
        if not casa:
            raise HTTPException(400, "La casa es obligatoria")
        if len(casa) > MAX_CASA:
            raise HTTPException(400, f"La casa es demasiado larga (máximo {MAX_CASA} caracteres)")
        cambios["casa"] = casa
    if not cambios:
        raise HTTPException(400, "No hay nada que cambiar")

    try:
        with engine.begin() as conn:
            actual = fila(conn.execute(select(residentes).where(residentes.c.id == rid)).first())
            if not actual:
                raise HTTPException(404, "Residente no encontrado")
            if cambios.get("telefono"):
                otro = fila(conn.execute(select(residentes).where(
                    residentes.c.telefono == cambios["telefono"], residentes.c.id != rid)).first())
                if otro:
                    raise HTTPException(409, f"Ese celular ya lo tiene {otro['nombre'] or 'otra persona'} ({otro['casa']})")
            conn.execute(update(residentes).where(residentes.c.id == rid).values(**cambios))
            if "casa" in cambios:
                asegurar_casa(conn, cambios["casa"])
            nuevo = fila(conn.execute(select(residentes).where(residentes.c.id == rid)).first())
            pagos = {c.clave: c for c in conn.execute(select(casas)).all()}
    except IntegrityError:
        raise HTTPException(409, "Ese celular ya está registrado en otra persona")
    return _con_pago(nuevo, pagos)


@app.delete("/api/residentes/{rid}")
def borrar_residente(rid: int, _: dict = Depends(solo_admin)):
    with engine.begin() as conn:
        r = conn.execute(delete(residentes).where(residentes.c.id == rid))
    if r.rowcount == 0:
        raise HTTPException(404, "Residente no encontrado")
    return {"borrado": rid}


@app.post("/api/casas/pago")
def cambiar_pago(datos: CasaPagoIn, u: dict = Depends(solo_admin)):
    """Marca una casa como al día o impaga (afecta a todos los celulares de esa casa)."""
    clave = clave_casa(datos.casa)
    if not clave:
        raise HTTPException(400, "La casa es obligatoria")
    valores = dict(al_dia=datos.al_dia, actualizado_por=u["usuario"], actualizado_en=ahora_iso())
    with engine.begin() as conn:
        if conn.execute(select(casas.c.clave).where(casas.c.clave == clave)).first():
            conn.execute(update(casas).where(casas.c.clave == clave).values(**valores))
        else:
            conn.execute(insert(casas).values(clave=clave, casa=" ".join(datos.casa.split()), **valores))
    return {"casa": " ".join(datos.casa.split()), "al_dia": datos.al_dia}


# ---------- WhatsApp (Whapi.Cloud, conexión directa: ver whatsapp.py) ----------
WEBHOOK_TOKEN = config.WEBHOOK_TOKEN


class _OcultarToken(logging.Filter):
    """El token del webhook viaja en la URL: que no quede escrito en los registros de acceso."""
    def filter(self, record):
        record.args = tuple(re.sub(r"token=[^\s&\"]+", "token=***", a) if isinstance(a, str) else a
                            for a in (record.args or ()))
        return True


logging.getLogger("uvicorn.access").addFilter(_OcultarToken())


async def responder_wsp(numero: str, texto: str) -> None:
    try:
        await whatsapp.enviar_texto(numero, texto)
    except Exception as exc:
        print(f"[wsp] no se pudo responder a {numero}: {exc}")


def telefonos_admin() -> set:
    """Celulares de administradores (ADMIN_TELEFONOS del .env) en formato internacional."""
    return {normalizar_telefono(t) for t in config.ADMIN_TELEFONOS.split(",") if t.strip()}


AYUDA_WSP = ("Escríbeme o mándame una *nota de voz* con las visitas que esperas. "
             "Ejemplo: \"Hoy viene Juan Pérez en taxi, placa ABC123\".")

TIPOS_TXT = {"VISITA": "Visita", "DELIVERY": "Delivery", "TAXI_UBER": "Taxi/Uber", "SERVICIO_TECNICO": "Servicio técnico"}


def telefonos_de_casa(casa: str) -> List[str]:
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(
            select(residentes.c.telefono).where(func.lower(residentes.c.casa) == " ".join(casa.lower().split()),
                                                residentes.c.telefono.is_not(None)))]


async def notificar_ingreso(reg: dict, sin_respuesta: bool = False) -> None:
    """Avisa por WhatsApp al residente que su visita acaba de ingresar."""
    telefonos = [reg["telefono"]] if reg.get("telefono") else telefonos_de_casa(reg["casa"])
    hora = (reg.get("hora_ingreso") or "")[11:16]
    placa = f" · placa {reg['placa']}" if reg.get("placa") else ""
    titulo = "⚠️ *Ingresó una visita sin tu respuesta*" if sin_respuesta else "🚪 *Ingresó tu visita*"
    texto = (f"{titulo}\n{reg['nombre_visitante']} "
             f"({TIPOS_TXT.get(reg['tipo'], reg['tipo'])}){placa}\nHora: {hora}")
    for tel in telefonos:
        await responder_wsp(tel, texto)


# ---------- Visitas sin aviso: el guardia las registra y el residente autoriza o no por WhatsApp ----------
def _suelto(x: str) -> str:
    """Forma comparable de una manzana o villa: sin separadores, en minúsculas y sin ceros a la izquierda
    (así 06 = 6 y 12A = 12-A = 12 A)."""
    t = re.sub(r"[\s\-_./]+", "", (x or "").lower())
    return t.lstrip("0") or ("0" if t else "")


def _casas_que_coinciden(conn, mz: str, villa: str) -> list:
    """Casas guardadas cuya manzana y villa, en forma suelta, equivalen a lo escrito (1 = 01, 12A = 12-A)."""
    buscado = (_suelto(mz), _suelto(villa))
    casas: dict = {}
    for r in conn.execute(select(residentes.c.id, residentes.c.nombre, residentes.c.telefono, residentes.c.casa)
                          .order_by(residentes.c.nombre, residentes.c.id)).all():
        m = re.match(r"^Mz\s+(.+?)\s+Villa\s+(.+)$", r.casa or "", re.I)
        if m and (_suelto(m.group(1)), _suelto(m.group(2))) == buscado:
            g = casas.setdefault(r.casa, {"casa": r.casa, "mz": m.group(1), "villa": m.group(2), "residentes": []})
            g["residentes"].append({"id": r.id, "nombre": r.nombre or "Sin nombre", "avisable": bool(r.telefono)})
    return sorted(casas.values(), key=lambda g: (g["mz"], g["villa"]))


@app.get("/api/casa/residentes")
def residentes_de_casa(mz: str, villa: str, exacta: bool = False, u: dict = Depends(usuario_actual)):
    """Para la visita sin aviso: quién vive en esa casa, para que el guardia confirme con el visitante a quién va a ver.
    Devuelve SOLO nombres (y si se les puede avisar): nunca celulares, cédulas ni el estado de pago.
    La búsqueda ignora ceros a la izquierda, mayúsculas y separadores (1 encuentra 01, 12A encuentra 12-A).
    Si coincide una sola casa se devuelve directa; si coinciden varias, se listan en `sugerencias` para elegir
    (con exacta=1 solo se busca la casa tal cual está guardada)."""
    mz, villa = mz.strip(), villa.strip()
    if not mz or not villa:
        raise HTTPException(400, "Escribe la manzana y la villa")
    if max(len(mz), len(villa)) > MAX_MZ_VILLA:
        raise HTTPException(400, f"La manzana y la villa admiten hasta {MAX_MZ_VILLA} caracteres")
    casa = f"Mz {mz} Villa {villa}"
    with engine.connect() as conn:
        coinciden = _casas_que_coinciden(conn, mz, villa)
    if exacta:
        coinciden = [g for g in coinciden if g["casa"].lower() == clave_casa(casa)] or coinciden[:0]
    with engine.connect() as conn:
        for g in coinciden:
            g["bloqueada"] = bool(config.BLOQUEO_POR_MORA and not casa_al_dia(conn, g["casa"]))
    if len(coinciden) == 1:
        g = coinciden[0]
        if g["bloqueada"]:      # no se muestran ni los nombres: el guardia solo debe saber que no se puede avisar
            return {"casa": g["casa"], "existe": True, "bloqueada": True, "mensaje": msg_mora_sin_aviso(g["casa"]),
                    "residentes": [], "sugerencias": []}
        return {"casa": g["casa"], "existe": True, "bloqueada": False, "residentes": g["residentes"], "sugerencias": []}
    return {"casa": casa, "existe": bool(coinciden), "bloqueada": False, "residentes": [], "sugerencias": [
        {"casa": g["casa"], "mz": g["mz"], "villa": g["villa"], "bloqueada": g["bloqueada"],
         "residentes": [] if g["bloqueada"] else [x["nombre"] for x in g["residentes"]]} for g in coinciden[:6]]}


async def notificar_solicitud(reg: dict, telefonos: List[str]) -> None:
    placa = f" · placa {reg['placa']}" if reg.get("placa") else ""
    obs = f"\n📝 {reg['observaciones']}" if reg.get("observaciones") else ""
    texto = (f"🔔 *Visita sin aviso en la garita*\n{reg['nombre_visitante']} "
             f"({TIPOS_TXT.get(reg['tipo'], reg['tipo'])}){placa}{obs}\n\n"
             f"Responde:\n*1* ✅ Autorizar\n*2* ❌ No autorizar\n(Ref. #{reg['id']})")
    for tel in telefonos:
        await responder_wsp(tel, texto)


@app.post("/api/visita-sin-aviso")
async def visita_sin_aviso(v: SinAviso, background: BackgroundTasks, u: dict = Depends(usuario_actual)):
    mz, villa, nombre = v.mz.strip(), v.villa.strip(), v.nombre_visitante.strip()
    if not (mz and villa and nombre):
        raise HTTPException(400, "Manzana, villa y nombre del visitante son obligatorios")
    if max(len(mz), len(villa)) > MAX_MZ_VILLA or len(nombre) > 80:
        raise HTTPException(400, "Datos demasiado largos")
    casa = f"Mz {mz} Villa {villa}"
    with engine.connect() as conn:
        coinciden = _casas_que_coinciden(conn, mz, villa)
        if v.residente_ids:   # la casa es la del residente elegido, tal como está guardada (no como la escribió el guardia)
            casas_el = {c for (c,) in conn.execute(select(residentes.c.casa).where(residentes.c.id.in_(v.residente_ids))).all()}
            if not casas_el <= {g["casa"] for g in coinciden}:
                raise HTTPException(400, "Las personas elegidas no pertenecen a esa casa")
            if len(casas_el) == 1:
                casa = next(iter(casas_el))
        elif len(coinciden) == 1:
            casa = coinciden[0]["casa"]
        elif not coinciden:
            raise HTTPException(400, f"No existe la casa Mz {mz} Villa {villa}. Confirma con el visitante la manzana y la villa.")
        else:
            raise HTTPException(400, "Hay varias casas que coinciden; elige la correcta en la pantalla.")
    with engine.connect() as conn:
        en_mora = config.BLOQUEO_POR_MORA and not casa_al_dia(conn, casa)
    if en_mora:
        with engine.begin() as conn:
            registrar_bloqueo(conn, casa, "garita", f"Visita sin aviso: {nombre}", usuario=u["usuario"])
        raise HTTPException(409, msg_mora_sin_aviso(casa))

    # ¿a quién se le pregunta? A los elegidos por el guardia; si no eligió, a todos los de la casa
    with engine.connect() as conn:
        de_casa = conn.execute(select(residentes.c.id, residentes.c.nombre, residentes.c.telefono)
                               .where(func.lower(residentes.c.casa) == clave_casa(casa))).all()
    if v.residente_ids is None:
        elegidos = de_casa
    else:
        elegidos = [r for r in de_casa if r.id in set(v.residente_ids)]
        if v.residente_ids and not elegidos:
            raise HTTPException(400, "Las personas elegidas no pertenecen a esa casa")
    avisables = [r for r in elegidos if r.telefono]
    telefonos = [r.telefono for r in avisables]
    preguntado_a = [r.nombre or "Residente" for r in avisables]
    quien = f"Sin aviso (se preguntó a {', '.join(preguntado_a)[:150]})" if preguntado_a else "Sin aviso (registrado en garita)"

    ahora = ahora_iso()
    with engine.begin() as conn:
        nuevo_id = conn.execute(insert(accesos).values(
            casa=casa, nombre_visitante=nombre, tipo=v.tipo.value, placa=(v.placa or "").strip() or None,
            observaciones=(v.observaciones or "").strip() or None, estado="PENDIENTE",
            autorizado_por=quien, fecha_hora=ahora, origen="garita",
        )).inserted_primary_key[0]
        conn.execute(insert(movimientos).values(acceso_id=nuevo_id, evento="SOLICITUD", fecha_hora=ahora,
                                                origen="garita", usuario=u["usuario"]))
        reg = fila(conn.execute(select(accesos).where(accesos.c.id == nuevo_id)).first())
    await manager.broadcast({"evento": "nuevo", "registro": reg})
    if telefonos:
        background.add_task(notificar_solicitud, reg, telefonos)
    return {"registro": vista_para(u["rol"], reg), "residentes_avisados": len(telefonos), "preguntado_a": preguntado_a}


RESP_SI = {"1", "si", "ok", "dale", "autorizo", "autorizar", "acepto", "adelante"}
RESP_NO = {"2", "no", "rechazo", "rechazar", "niego", "no autorizo"}
PATRON_RESPUESTA = re.compile(
    r"(1|2|si|ok|dale|autorizo|autorizar|acepto|adelante|no|no autorizo|rechazo|rechazar|niego)"
    r"(?:(?:\s+|\s*#)(\d+))?"
)


async def resolver_solicitud(telefono: str, texto: str) -> Optional[str]:
    """Si el mensaje es un 'sí/no' a una solicitud pendiente de la casa del residente, la resuelve y
    devuelve el texto de respuesta. Devuelve None si NO es una respuesta de autorización (entonces
    el mensaje sigue su camino normal como anuncio de visitas)."""
    m = PATRON_RESPUESTA.fullmatch(_norm(texto).strip().strip(".!¡¿?"))
    if not m:
        return None
    aprobar = m.group(1) in RESP_SI
    ref = int(m.group(2)) if m.group(2) else None
    mis_casas = select(func.lower(residentes.c.casa)).where(residentes.c.telefono == telefono)
    with engine.connect() as conn:
        pendientes = [fila(r) for r in conn.execute(
            select(accesos).where(accesos.c.estado == "PENDIENTE", func.lower(accesos.c.casa).in_(mis_casas),
                                  accesos.c.fecha_hora >= parametros.limite_vigencia())
            .order_by(accesos.c.id.desc())).all()]
    if not pendientes:
        # ¿Otra persona de la casa acaba de responder? Entonces este "sí/no" llegó tarde, no es un anuncio.
        hace = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
        with engine.connect() as conn:
            tarde = conn.execute(
                select(accesos.c.id).where(func.lower(accesos.c.casa).in_(mis_casas), accesos.c.hora_respuesta >= hace,
                                           accesos.c.respondido_por != telefono)).first()
        return "Esa solicitud ya fue respondida por otra persona de tu casa." if tarde else None
    if ref is not None:
        elegido = next((p for p in pendientes if p["id"] == ref), None)
        if not elegido:
            return f"No encuentro una solicitud pendiente con la referencia #{ref}."
    elif len(pendientes) == 1:
        elegido = pendientes[0]
    else:
        lista = "\n".join(f"• #{p['id']} {p['nombre_visitante']}" for p in pendientes)
        return (f"Tienes {len(pendientes)} solicitudes pendientes:\n{lista}\n\n"
                "Indica cuál: por ejemplo *1 12* para autorizar la #12 o *2 12* para no autorizarla.")

    nuevo = "PRE_AUTORIZADO" if aprobar else "RECHAZADO"
    ahora = ahora_iso()
    with engine.begin() as conn:
        r = conn.execute(
            update(accesos).where(accesos.c.id == elegido["id"], accesos.c.estado == "PENDIENTE").values(
                estado=nuevo,
                autorizado_por=f"Residente Casa {elegido['casa']} ({'autorizó' if aprobar else 'rechazó'} en puerta)",
                hora_respuesta=ahora, respondido_por=telefono))
        if r.rowcount == 0:  # otro residente de la casa respondió primero
            return "Esa solicitud ya fue respondida."
        conn.execute(insert(movimientos).values(
            acceso_id=elegido["id"], evento="AUTORIZADO" if aprobar else "RECHAZADO", fecha_hora=ahora,
            origen="whatsapp"))
        reg = fila(conn.execute(select(accesos).where(accesos.c.id == elegido["id"])).first())
    await manager.broadcast({"evento": "estado", "registro": reg})
    if aprobar:
        return f"✅ Autorizaste el ingreso de {reg['nombre_visitante']}. Avisé a la garita."
    return f"❌ No autorizaste a {reg['nombre_visitante']}. Avisé a la garita."


@app.post("/api/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    """Whapi llama aquí desde internet: el secreto va en la URL (?token=...). Sin WEBHOOK_TOKEN configurado se rechaza todo."""
    token = request.query_params.get("token", "")
    if not WEBHOOK_TOKEN or not secrets.compare_digest(token.encode(), WEBHOOK_TOKEN.encode()):
        raise HTTPException(401, "Token inválido")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Cuerpo inválido")
    resultados = [await procesar_mensaje_wsp(m["telefono"], m["texto"], m["voz"])
                  for m in whatsapp.extraer_mensajes(body)]
    if not resultados:
        return {"ok": True, "ignorado": "no es un mensaje nuevo de una persona"}
    return resultados[0] if len(resultados) == 1 else {"ok": True, "mensajes": resultados}


async def procesar_mensaje_wsp(telefono: str, texto_msg: Optional[str], voz: Optional[dict]) -> dict:
    """Un mensaje de WhatsApp ya traducido: texto o nota de voz (voz != None) de un celular."""
    es_audio = voz is not None
    if not es_audio and not texto_msg:
        await responder_wsp(telefono, AYUDA_WSP)
        return {"ok": True, "ignorado": "tipo de mensaje no soportado"}

    if telefono in telefonos_admin():       # ¿un administrador pide el resumen? (texto o nota de voz)
        try:
            if es_audio:
                texto_msg = transcribir(await whatsapp.descargar_audio(voz), "nota.ogg", "audio/ogg")
                es_audio = False            # ya está transcrito: si no era un pedido de resumen, sigue como texto normal
            periodo = detectar_periodo(texto_msg or "")
        except Exception as exc:
            print(f"[wsp] error con el audio del administrador {telefono}: {exc}")
            await responder_wsp(telefono, "No pude escuchar el audio. Intenta de nuevo o escribe: reporte de hoy.")
            return {"ok": False, "error": str(exc)}
        if periodo:
            with engine.connect() as conn:
                await responder_wsp(telefono, armar_resumen(conn, periodo))
            return {"ok": True, "resumen": periodo}

    with engine.connect() as conn:
        res = fila(conn.execute(select(residentes.c.casa).where(residentes.c.telefono == telefono)).first())
    if not res and telefono in telefonos_admin():
        await responder_wsp(telefono, AYUDA_ADMIN)
        return {"ok": True, "ignorado": "administrador sin pedido de resumen"}
    if not res:
        await responder_wsp(telefono, "Tu número no está registrado en la bitácora. Pide a la administración que te registre.")
        return {"ok": True, "ignorado": "residente no registrado"}

    if texto_msg and not es_audio:
        respuesta = await resolver_solicitud(telefono, texto_msg)  # ¿es un "1/2/sí/no" a una visita sin aviso?
        if respuesta is not None:
            await responder_wsp(telefono, respuesta)
            return {"ok": True, "autorizacion": True}

    if config.BLOQUEO_POR_MORA:
        with engine.begin() as conn:
            en_mora = not casa_al_dia(conn, res["casa"])
            if en_mora:
                registrar_bloqueo(conn, res["casa"], "whatsapp", texto_msg or "[nota de voz]", telefono=telefono)
        if en_mora:
            await responder_wsp(telefono, MSG_BLOQUEO_WSP)
            return {"ok": True, "bloqueado": True}

    try:
        if es_audio:
            audio = await whatsapp.descargar_audio(voz)
            resultado = await procesar_audio(audio, "nota.ogg", "audio/ogg", casa_forzada=res["casa"],
                                              origen="whatsapp", telefono=telefono)
        else:
            resultado = await procesar_texto(texto_msg, casa_forzada=res["casa"],
                                              origen="whatsapp", telefono=telefono)
    except Exception as exc:
        print(f"[wsp] error procesando mensaje de {telefono}: {exc}")
        detalle = getattr(exc, "detail", "")
        if es_audio or "largo" not in str(detalle):
            await responder_wsp(telefono, "No pude procesar tu mensaje. Intenta de nuevo, por favor.")
        else:
            await responder_wsp(telefono, f"Tu mensaje es muy largo (máximo {MAX_TEXTO} caracteres). Envíalo más corto 🙏")
        return {"ok": False, "error": str(exc)}

    regs = resultado["registros"]
    if not regs:
        await responder_wsp(telefono, "No entendí ninguna visita. " + AYUDA_WSP)
    else:
        lineas = "\n".join(
            f"• {r['nombre_visitante']} ({r['tipo']})" + (f" – placa {r['placa']}" if r["placa"] else "")
            for r in regs
        )
        await responder_wsp(telefono, f"✅ Pre-autorizado en Casa {res['casa']}:\n{lineas}")
    return {"ok": True, "registros": len(regs)}




# ---------- Pantallas ----------
@app.get("/")
def index():
    return RedirectResponse("/garita", status_code=303)


@app.get("/admin")
def admin():
    return RedirectResponse("/garita", status_code=303)


@app.get("/garita")
def garita(request: Request):
    return pagina(request, ("ADMIN", "GUARDIA")) or FileResponse(STATIC_DIR / "garita.html")


@app.get("/configuracion")
def configuracion_admin(request: Request):
    return pagina(request, ("ADMIN",)) or FileResponse(STATIC_DIR / "configuracion.html")


@app.get("/reporte")
def reporte(request: Request):
    return pagina(request, ("ADMIN",)) or FileResponse(STATIC_DIR / "reporte.html")


@app.get("/residentes")
def residentes_admin(request: Request):
    return pagina(request, ("ADMIN",)) or FileResponse(STATIC_DIR / "residentes.html")


@app.get("/residente")
def residente(request: Request):
    """Pantalla de demostración para el residente (solo administrador: los residentes usan WhatsApp)."""
    return pagina(request, ("ADMIN",)) or FileResponse(STATIC_DIR / "residente.html")


# Solo los archivos compartidos de la interfaz (menú, etc.); las pantallas se sirven con control de acceso.
app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
