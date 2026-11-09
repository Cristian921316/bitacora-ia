import pytest
from fastapi.testclient import TestClient

import auth
import db
import main
from conftest import registrar, texto
from sqlalchemy import select


def test_sin_sesion_api_401_y_paginas_redirigen(anonimo):
    assert anonimo.get("/api/accesos").status_code == 401
    assert anonimo.get("/api/reporte").status_code == 401
    for pagina in ("/garita", "/reporte", "/residentes", "/usuarios", "/residente"):
        r = anonimo.get(pagina)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), pagina
    assert anonimo.get("/login").status_code == 200
    assert anonimo.get("/salud").json() == {"ok": True}


def test_login_incorrecto_mensaje_generico_y_bloqueo_por_intentos(anonimo, admin):
    r1 = anonimo.post("/api/login", json={"usuario": "admin", "clave": "mala"})
    r2 = anonimo.post("/api/login", json={"usuario": "noexiste", "clave": "mala"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json()  # no revela si el usuario existe
    for _ in range(5):
        anonimo.post("/api/login", json={"usuario": "admin", "clave": "mala"})
    # bloqueado incluso con la clave correcta
    assert anonimo.post("/api/login", json={"usuario": "admin", "clave": "clave-segura-1"}).status_code == 429


def test_guardia_solo_ve_la_garita(guardia):
    assert guardia.get("/garita").status_code == 200
    guardia.follow_redirects = False
    for pagina in ("/reporte", "/residentes", "/usuarios", "/residente"):
        r = guardia.get(pagina)
        assert r.status_code == 303 and r.headers["location"] == "/garita", pagina
    for metodo, url in (("get", "/api/reporte"), ("get", "/api/reporte.xlsx"), ("get", "/api/residentes"),
                        ("get", "/api/usuarios"), ("get", "/api/accesos?todos=1")):
        assert getattr(guardia, metodo)(url).status_code == 403, url
    assert guardia.post("/api/residentes", json={"telefono": "0991111111", "casa": "Mz 1 Villa 1"}).status_code == 403
    assert guardia.post("/api/casas/pago", json={"casa": "Mz 1 Villa 1", "al_dia": False}).status_code == 403
    assert guardia.post("/api/procesar-texto", json={"texto": "viene Ana"}).status_code == 403
    assert guardia.post("/api/usuarios", json={"usuario": "x1x", "nombre": "X", "rol": "ADMIN", "clave": "12345678"}).status_code == 403


def test_admin_accede_a_todo(admin):
    for pagina in ("/garita", "/reporte", "/residentes", "/usuarios", "/residente"):
        assert admin.get(pagina).status_code == 200, pagina
    assert admin.get("/api/reporte").status_code == 200
    assert admin.get("/api/accesos?todos=1").status_code == 200


def test_guardia_recibe_solo_campos_minimos(admin, guardia, wsp):
    registrar(admin)
    texto(admin, "593988391178", "Hoy viene Juan Pérez")
    reg_admin = admin.get("/api/accesos").json()[0]
    reg_guardia = guardia.get("/api/accesos").json()[0]
    assert "telefono" in reg_admin and "transcripcion" in reg_admin
    for campo in ("telefono", "transcripcion", "respondido_por", "origen", "hora_salida"):
        assert campo not in reg_guardia
    assert reg_guardia["nombre_visitante"] == "Juan Pérez"


def test_cambio_de_clave_obligatorio(anonimo, base_limpia):
    auth.crear_usuario("nuevo", "Nuevo", "GUARDIA", "temporal-123", debe_cambiar=True)
    r = anonimo.post("/api/login", json={"usuario": "nuevo", "clave": "temporal-123"})
    assert r.json()["destino"] == "/clave" and r.json()["debe_cambiar_clave"] is True
    r = anonimo.get("/api/accesos")
    assert r.status_code == 403 and r.json()["detail"] == "CAMBIAR_CLAVE"
    assert anonimo.get("/garita").headers["location"] == "/clave"
    assert anonimo.post("/api/cambiar-clave", json={"clave_actual": "x", "clave_nueva": "otra-clave-9"}).status_code == 400
    assert anonimo.post("/api/cambiar-clave", json={"clave_actual": "temporal-123", "clave_nueva": "corta"}).status_code == 400
    assert anonimo.post("/api/cambiar-clave", json={"clave_actual": "temporal-123", "clave_nueva": "otra-clave-9"}).status_code == 200
    assert anonimo.get("/api/accesos").status_code == 200
    # la clave nueva sirve, la vieja no
    otro = TestClient(main.app)
    assert otro.post("/api/login", json={"usuario": "nuevo", "clave": "temporal-123"}).status_code == 401
    assert otro.post("/api/login", json={"usuario": "nuevo", "clave": "otra-clave-9"}).status_code == 200


def test_salir_invalida_la_sesion(guardia):
    assert guardia.get("/api/yo").json()["rol"] == "GUARDIA"
    token = guardia.cookies.get(auth.COOKIE)
    assert guardia.post("/api/salir").status_code == 200
    copia = TestClient(main.app)
    copia.cookies.set(auth.COOKIE, token)
    assert copia.get("/api/yo").status_code == 401  # el token viejo ya no vale


def test_la_sesion_guardada_no_es_el_token(guardia):
    token = guardia.cookies.get(auth.COOKIE)
    with db.engine.connect() as conn:
        guardados = [r[0] for r in conn.execute(select(db.sesiones.c.token_hash))]
    assert token not in guardados and all(len(h) == 64 for h in guardados)  # solo se guarda el hash


def test_gestion_de_usuarios(admin, guardia):
    # el guardia que ya estaba logueado sigue activo hasta que lo desactiven
    r = admin.post("/api/usuarios", json={"usuario": "Maria.G", "nombre": "María", "rol": "GUARDIA", "clave": "temporal-123"})
    assert r.status_code == 200 and r.json()["usuario"] == "maria.g"
    assert admin.post("/api/usuarios", json={"usuario": "maria.g", "nombre": "X", "rol": "GUARDIA", "clave": "temporal-123"}).status_code == 409
    assert admin.post("/api/usuarios", json={"usuario": "a b", "nombre": "X", "rol": "GUARDIA", "clave": "temporal-123"}).status_code == 400
    assert admin.post("/api/usuarios", json={"usuario": "otro1", "nombre": "X", "rol": "SUPER", "clave": "temporal-123"}).status_code == 400
    lista = admin.get("/api/usuarios").json()
    assert {u["usuario"] for u in lista} == {"admin", "guardia1", "maria.g"}
    assert all("clave_hash" not in u for u in lista)  # el hash nunca sale de la API
    # desactivar al guardia cierra su sesión de inmediato
    gid = next(u["id"] for u in lista if u["usuario"] == "guardia1")
    assert admin.patch(f"/api/usuarios/{gid}", json={"activo": False}).status_code == 200
    assert guardia.get("/api/yo").status_code == 401
    # restablecer clave obliga a cambiarla
    assert admin.post(f"/api/usuarios/{gid}/clave", json={"clave": "reinicio-123"}).status_code == 200


def test_no_se_puede_dejar_el_sistema_sin_administrador(admin):
    yo = admin.get("/api/usuarios").json()[0]["id"]
    assert admin.patch(f"/api/usuarios/{yo}", json={"activo": False}).status_code == 409
    assert admin.patch(f"/api/usuarios/{yo}", json={"rol": "GUARDIA"}).status_code == 409
    assert admin.get("/api/yo").json()["rol"] == "ADMIN"


def test_websocket_requiere_sesion_y_filtra_por_rol(admin, guardia, anonimo, wsp):
    registrar(admin)
    with pytest.raises(Exception):
        with anonimo.websocket_connect("/ws/bitacora"):
            pass
    with guardia.websocket_connect("/ws/bitacora") as ws_g, admin.websocket_connect("/ws/bitacora") as ws_a:
        texto(admin, "593988391178", "Hoy viene Juan Pérez")
        msg_g, msg_a = ws_g.receive_json(), ws_a.receive_json()
    assert "telefono" not in msg_g["registro"] and "transcripcion" not in msg_g["registro"]
    assert msg_a["registro"]["telefono"] == "593988391178"
