"""Login, sesiones y usuarios con dos roles:

- ADMIN: ve todo (menú, reportes, residentes, usuarios).
- GUARDIA: solo la pantalla de garita, con los datos mínimos para operar.

Las sesiones viven en la base de datos (se pueden revocar y funcionan con varias copias de la app).
Las claves se guardan con PBKDF2-SHA256 y sal propia; nunca en texto plano.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, update

import config
from db import ahora_iso, engine, fila, sesiones, usuarios

router = APIRouter()

COOKIE = "bitacora_sid"
ITERACIONES = int(os.getenv("PBKDF2_ITER", "600000"))
MIN_CLAVE = 8
RUTAS_CLAVE = {"/api/yo", "/api/cambiar-clave", "/api/salir"}  # permitidas aunque deba cambiar la clave


# ---------- claves ----------
def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_clave(clave: str) -> str:
    sal = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", clave.encode(), sal, ITERACIONES)
    return f"pbkdf2_sha256${ITERACIONES}${_b64(sal)}${_b64(dk)}"


def verificar_clave(clave: str, guardado: str) -> bool:
    try:
        alg, iters, sal, dk = guardado.split("$")
        calculado = hashlib.pbkdf2_hmac("sha256", clave.encode(), base64.b64decode(sal), int(iters))
        return hmac.compare_digest(calculado, base64.b64decode(dk))
    except Exception:
        return False


_HASH_FALSO = hash_clave("clave-que-nadie-usa")  # para gastar el mismo tiempo cuando el usuario no existe


def _validar_clave_nueva(clave: str) -> None:
    if len(clave) < MIN_CLAVE:
        raise HTTPException(400, f"La contraseña debe tener al menos {MIN_CLAVE} caracteres")


# ---------- sesiones ----------
def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def crear_sesion(usuario_id: int) -> str:
    token = secrets.token_urlsafe(32)
    ahora = datetime.now()
    with engine.begin() as conn:
        conn.execute(delete(sesiones).where(sesiones.c.expira < ahora.isoformat(timespec="seconds")))  # limpieza
        conn.execute(insert(sesiones).values(
            token_hash=_hash_token(token), usuario_id=usuario_id, creada=ahora.isoformat(timespec="seconds"),
            expira=(ahora + timedelta(hours=config.SESION_HORAS)).isoformat(timespec="seconds")))
    return token


def usuario_desde_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    with engine.connect() as conn:
        r = conn.execute(
            select(usuarios.c.id, usuarios.c.usuario, usuarios.c.nombre, usuarios.c.rol, usuarios.c.debe_cambiar_clave)
            .select_from(sesiones.join(usuarios, usuarios.c.id == sesiones.c.usuario_id))
            .where(sesiones.c.token_hash == _hash_token(token), sesiones.c.expira > ahora_iso(),
                   usuarios.c.activo.is_(True))
        ).first()
    return fila(r)


def usuario_opcional(request: Request) -> Optional[dict]:
    return usuario_desde_token(request.cookies.get(COOKIE))


def usuario_actual(request: Request) -> dict:
    u = usuario_opcional(request)
    if not u:
        raise HTTPException(401, "Inicia sesión para continuar")
    if u["debe_cambiar_clave"] and request.url.path not in RUTAS_CLAVE:
        raise HTTPException(403, "CAMBIAR_CLAVE")
    return u


def solo_admin(u: dict = Depends(usuario_actual)) -> dict:
    if u["rol"] != "ADMIN":
        raise HTTPException(403, "No tienes permiso para esta acción")
    return u


def usuario_ws(ws: WebSocket) -> Optional[dict]:
    return usuario_desde_token(ws.cookies.get(COOKIE))


def pagina(request: Request, roles: Tuple[str, ...] = ("ADMIN", "GUARDIA")) -> Optional[Response]:
    """Para las pantallas HTML: devuelve una redirección si no corresponde mostrarla, o None si sí."""
    u = usuario_opcional(request)
    if not u:
        return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)
    if u["debe_cambiar_clave"]:
        return RedirectResponse("/clave", status_code=303)
    if u["rol"] not in roles:
        return RedirectResponse("/garita", status_code=303)
    return None


# ---------- control de intentos de login ----------
_fallos: Dict[str, Tuple[int, float]] = {}   # clave -> (fallos seguidos, bloqueado hasta)
MAX_FALLOS, BLOQUEO_SEG = 5, 300


def _clave_intentos(request: Request, usuario: str) -> str:
    ip = request.client.host if request.client else "?"
    return f"{ip}|{usuario}"


def _verificar_bloqueo(clave: str) -> None:
    n, hasta = _fallos.get(clave, (0, 0.0))
    if hasta and time.time() < hasta:
        raise HTTPException(429, "Demasiados intentos fallidos. Espera unos minutos e inténtalo de nuevo.")
    if hasta and time.time() >= hasta:
        _fallos.pop(clave, None)


def _registrar_fallo(clave: str) -> None:
    n, _ = _fallos.get(clave, (0, 0.0))
    n += 1
    _fallos[clave] = (n, time.time() + BLOQUEO_SEG if n >= MAX_FALLOS else 0.0)


# ---------- usuarios ----------
def crear_usuario(usuario: str, nombre: str, rol: str, clave: str, debe_cambiar: bool = True) -> int:
    usuario = usuario.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{3,30}", usuario):
        raise HTTPException(400, "Usuario inválido: use 3 a 30 letras minúsculas, números, punto, guion o guion bajo")
    if rol not in ("ADMIN", "GUARDIA"):
        raise HTTPException(400, "Rol inválido: use ADMIN o GUARDIA")
    if not nombre.strip():
        raise HTTPException(400, "El nombre es obligatorio")
    _validar_clave_nueva(clave)
    with engine.begin() as conn:
        if conn.execute(select(usuarios.c.id).where(usuarios.c.usuario == usuario)).first():
            raise HTTPException(409, "Ese usuario ya existe")
        r = conn.execute(insert(usuarios).values(
            usuario=usuario, nombre=nombre.strip()[:100], rol=rol, clave_hash=hash_clave(clave), activo=True,
            debe_cambiar_clave=debe_cambiar, creado=ahora_iso()))
        return r.inserted_primary_key[0]


def bootstrap_admin() -> None:
    """Si no hay ningún usuario, crea el primer administrador con ADMIN_USUARIO / ADMIN_PASSWORD (del .env).
    Queda obligado a cambiar la clave en su primer ingreso."""
    with engine.connect() as conn:
        hay = conn.execute(select(func.count()).select_from(usuarios)).scalar()
    usuario, clave = os.getenv("ADMIN_USUARIO"), os.getenv("ADMIN_PASSWORD")
    if hay or not usuario or not clave:
        if not hay:
            print("[auth] No hay usuarios y faltan ADMIN_USUARIO / ADMIN_PASSWORD en .env: nadie podrá ingresar.")
        return
    crear_usuario(usuario, "Administrador", "ADMIN", clave, debe_cambiar=True)
    print(f"[auth] Administrador inicial '{usuario}' creado (debe cambiar la clave al ingresar).")


# ---------- API ----------
class LoginIn(BaseModel):
    usuario: str
    clave: str


class CambioClaveIn(BaseModel):
    clave_actual: str
    clave_nueva: str


class UsuarioIn(BaseModel):
    usuario: str
    nombre: str
    rol: str
    clave: str


class UsuarioPatch(BaseModel):
    nombre: Optional[str] = None
    rol: Optional[str] = None
    activo: Optional[bool] = None


class ClaveIn(BaseModel):
    clave: str


@router.post("/api/login")
def login(datos: LoginIn, request: Request, response: Response):
    usuario = datos.usuario.strip().lower()
    clave_intentos = _clave_intentos(request, usuario)
    _verificar_bloqueo(clave_intentos)
    with engine.connect() as conn:
        u = fila(conn.execute(select(usuarios).where(usuarios.c.usuario == usuario)).first())
    ok = verificar_clave(datos.clave, u["clave_hash"] if u else _HASH_FALSO)
    if not (u and ok and u["activo"]):
        _registrar_fallo(clave_intentos)
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    _fallos.pop(clave_intentos, None)
    token = crear_sesion(u["id"])
    response.set_cookie(COOKIE, token, max_age=config.SESION_HORAS * 3600, httponly=True,
                        samesite="lax", secure=config.COOKIE_SECURE, path="/")
    return {"usuario": u["usuario"], "nombre": u["nombre"], "rol": u["rol"],
            "debe_cambiar_clave": bool(u["debe_cambiar_clave"]),
            "destino": "/clave" if u["debe_cambiar_clave"] else "/garita"}


@router.post("/api/salir")
def salir(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with engine.begin() as conn:
            conn.execute(delete(sesiones).where(sesiones.c.token_hash == _hash_token(token)))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/api/yo")
def yo(u: dict = Depends(usuario_actual)):
    return {"usuario": u["usuario"], "nombre": u["nombre"], "rol": u["rol"],
            "debe_cambiar_clave": bool(u["debe_cambiar_clave"])}


@router.post("/api/cambiar-clave")
def cambiar_clave(datos: CambioClaveIn, request: Request, u: dict = Depends(usuario_actual)):
    _validar_clave_nueva(datos.clave_nueva)
    with engine.begin() as conn:
        actual = conn.execute(select(usuarios.c.clave_hash).where(usuarios.c.id == u["id"])).scalar()
        if not verificar_clave(datos.clave_actual, actual):
            raise HTTPException(400, "La contraseña actual no es correcta")
        if datos.clave_nueva == datos.clave_actual:
            raise HTTPException(400, "La contraseña nueva debe ser distinta de la actual")
        conn.execute(update(usuarios).where(usuarios.c.id == u["id"])
                     .values(clave_hash=hash_clave(datos.clave_nueva), debe_cambiar_clave=False))
        # cierra las demás sesiones de este usuario (por si la clave anterior estaba expuesta)
        conn.execute(delete(sesiones).where(sesiones.c.usuario_id == u["id"],
                                            sesiones.c.token_hash != _hash_token(request.cookies.get(COOKIE, ""))))
    return {"ok": True}


def _admins_activos(conn) -> int:
    return conn.execute(select(func.count()).select_from(usuarios)
                        .where(usuarios.c.rol == "ADMIN", usuarios.c.activo.is_(True))).scalar()


@router.get("/api/usuarios")
def listar_usuarios(_: dict = Depends(solo_admin)):
    with engine.connect() as conn:
        rows = conn.execute(select(usuarios.c.id, usuarios.c.usuario, usuarios.c.nombre, usuarios.c.rol,
                                   usuarios.c.activo, usuarios.c.debe_cambiar_clave, usuarios.c.creado)
                            .order_by(usuarios.c.id)).all()
    return [fila(r) for r in rows]


@router.post("/api/usuarios")
def api_crear_usuario(datos: UsuarioIn, _: dict = Depends(solo_admin)):
    uid = crear_usuario(datos.usuario, datos.nombre, datos.rol, datos.clave, debe_cambiar=True)
    return {"id": uid, "usuario": datos.usuario.strip().lower()}


@router.patch("/api/usuarios/{uid}")
def editar_usuario(uid: int, datos: UsuarioPatch, yo_: dict = Depends(solo_admin)):
    cambios = {}
    if datos.nombre is not None:
        if not datos.nombre.strip():
            raise HTTPException(400, "El nombre es obligatorio")
        cambios["nombre"] = datos.nombre.strip()[:100]
    if datos.rol is not None:
        if datos.rol not in ("ADMIN", "GUARDIA"):
            raise HTTPException(400, "Rol inválido")
        cambios["rol"] = datos.rol
    if datos.activo is not None:
        cambios["activo"] = datos.activo
    if not cambios:
        raise HTTPException(400, "Nada que cambiar")
    with engine.begin() as conn:
        u = fila(conn.execute(select(usuarios).where(usuarios.c.id == uid)).first())
        if not u:
            raise HTTPException(404, "Usuario no encontrado")
        quita_admin = u["rol"] == "ADMIN" and u["activo"] and (
            cambios.get("rol", "ADMIN") != "ADMIN" or cambios.get("activo", True) is False)
        if quita_admin and (uid == yo_["id"] or _admins_activos(conn) <= 1):
            raise HTTPException(409, "No puedes quitarte el rol de administrador ni desactivar al único administrador")
        conn.execute(update(usuarios).where(usuarios.c.id == uid).values(**cambios))
        if cambios.get("activo") is False or "rol" in cambios:
            conn.execute(delete(sesiones).where(sesiones.c.usuario_id == uid))  # cierra sus sesiones
    return {"ok": True}


@router.post("/api/usuarios/{uid}/clave")
def restablecer_clave(uid: int, datos: ClaveIn, _: dict = Depends(solo_admin)):
    _validar_clave_nueva(datos.clave)
    with engine.begin() as conn:
        r = conn.execute(update(usuarios).where(usuarios.c.id == uid)
                         .values(clave_hash=hash_clave(datos.clave), debe_cambiar_clave=True))
        if r.rowcount == 0:
            raise HTTPException(404, "Usuario no encontrado")
        conn.execute(delete(sesiones).where(sesiones.c.usuario_id == uid))
    return {"ok": True}


# ---------- pantallas ----------
@router.get("/login")
def pagina_login(request: Request):
    u = usuario_opcional(request)
    if u:
        return RedirectResponse("/clave" if u["debe_cambiar_clave"] else "/garita", status_code=303)
    return FileResponse(config.STATIC_DIR / "login.html")


@router.get("/clave")
def pagina_clave(request: Request):
    if not usuario_opcional(request):
        return RedirectResponse("/login?next=/clave", status_code=303)
    return FileResponse(config.STATIC_DIR / "clave.html")


@router.get("/usuarios")
def pagina_usuarios(request: Request):
    return pagina(request, ("ADMIN",)) or FileResponse(config.STATIC_DIR / "usuarios.html")
