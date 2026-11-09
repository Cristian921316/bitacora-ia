"""Vigencia configurable de las pre-autorizaciones y alerta de los que siguen adentro desde ayer."""
from datetime import datetime, timedelta

from sqlalchemy import insert

import db
import parametros
from conftest import registrar, texto

CASA = "Mz 6 Villa 23"


def hace(horas=0, minutos=0):
    return (datetime.now() - timedelta(hours=horas, minutes=minutos)).isoformat(timespec="seconds")


def crear(estado, fecha_hora, nombre="Visita", **extra):
    with db.engine.begin() as conn:
        return conn.execute(insert(db.accesos).values(
            casa=CASA, nombre_visitante=nombre, tipo="VISITA", estado=estado, fecha_hora=fecha_hora, **extra
        )).inserted_primary_key[0]


def test_valor_por_defecto_y_cambio_por_el_administrador(admin, guardia):
    assert parametros.valor("vigencia_horas") == 12
    ps = {p["clave"]: p for p in admin.get("/api/parametros").json()}
    assert ps["vigencia_horas"]["valor"] == 12 and ps["vigencia_horas"]["actualizado_por"] is None
    r = admin.put("/api/parametros/vigencia_horas", json={"valor": 24})
    assert r.status_code == 200 and r.json()["valor"] == 24 and r.json()["actualizado_por"] == "admin"
    assert parametros.valor("vigencia_horas") == 24
    # cualquier usuario con sesión puede leer lo que la garita necesita; solo el admin cambia
    cfg = guardia.get("/api/config").json()
    assert cfg["vigencia_horas"] == 24 and cfg["ahora_servidor"]
    assert guardia.get("/api/parametros").status_code == 403
    assert guardia.put("/api/parametros/vigencia_horas", json={"valor": 1}).status_code == 403


def test_validacion_de_parametros(admin):
    for malo in (0, -5, 169, 100000):
        assert admin.put("/api/parametros/vigencia_horas", json={"valor": malo}).status_code == 400, malo
    assert admin.put("/api/parametros/vigencia_horas", json={"valor": "abc"}).status_code == 422
    assert admin.put("/api/parametros/vigencia_horas", json={"valor": 12.5}).status_code == 422
    assert admin.put("/api/parametros/inexistente", json={"valor": 5}).status_code == 404
    assert parametros.valor("vigencia_horas") == 12   # nada se guardó


def test_la_garita_solo_lista_lo_vigente(admin, guardia):
    admin.put("/api/parametros/control_salidas", json={"valor": 1})   # para ver también a los que están adentro
    vigente = crear("PRE_AUTORIZADO", hace(horas=11), "Vigente")
    vencida = crear("PRE_AUTORIZADO", hace(horas=13), "Vencida")
    pend_vigente = crear("PENDIENTE", hace(minutos=20), "Espera")
    pend_vencida = crear("PENDIENTE", hace(horas=13), "Espera vieja")
    adentro_viejo = crear("ADENTRO", hace(horas=40), "Adentro antiguo", hora_ingreso=hace(horas=39))
    salio = crear("SALIO", hace(horas=1), "Ya salió")
    ids = {a["id"] for a in guardia.get("/api/accesos").json()}
    assert ids == {vigente, pend_vigente, adentro_viejo}      # la vencida y la que salió no cargan; el que sigue adentro sí
    # al ampliar la vigencia reaparecen, sin tocar los datos
    admin.put("/api/parametros/vigencia_horas", json={"valor": 14})
    assert {a["id"] for a in guardia.get("/api/accesos").json()} == {vigente, vencida, pend_vigente, pend_vencida, adentro_viejo}
    admin.put("/api/parametros/vigencia_horas", json={"valor": 1})
    assert {a["id"] for a in guardia.get("/api/accesos").json()} == {pend_vigente, adentro_viejo}


def test_no_se_puede_ingresar_una_visita_vencida(admin, guardia):
    vencida = crear("PRE_AUTORIZADO", hace(horas=13), "Vencida")
    for cliente in (guardia, admin):                     # tampoco el administrador: debe registrarse de nuevo
        r = cliente.post(f"/api/cambiar-estado/{vencida}", json={"estado": "ADENTRO"})
        assert r.status_code == 409 and "venció" in r.json()["detail"] and "12 horas" in r.json()["detail"]
    pendiente = crear("PENDIENTE", hace(horas=13), "Espera vieja")
    assert guardia.post(f"/api/cambiar-estado/{pendiente}", json={"estado": "ADENTRO"}).status_code == 409
    # una vigente sí entra, y el que ya está adentro puede salir aunque sea antiguo
    ok = crear("PRE_AUTORIZADO", hace(horas=2), "Vigente")
    assert guardia.post(f"/api/cambiar-estado/{ok}", json={"estado": "ADENTRO"}).status_code == 200
    viejo = crear("ADENTRO", hace(horas=40), "Antiguo", hora_ingreso=hace(horas=39))
    assert guardia.post(f"/api/cambiar-estado/{viejo}", json={"estado": "SALIO"}).status_code == 200


def test_el_residente_no_puede_autorizar_una_solicitud_vencida(admin, wsp):
    registrar(admin)
    crear("PENDIENTE", hace(horas=13), "Espera vieja")
    texto(admin, "593988391178", "1")
    assert admin.get("/api/accesos?todos=1").json()[0]["estado"] == "VENCIDA"   # no pasó a PRE_AUTORIZADO
    assert not any("Autorizaste" in t for _, t in wsp)


def test_reporte_marca_como_vencida(admin):
    crear("PRE_AUTORIZADO", hace(horas=13), "Vencida")
    crear("PRE_AUTORIZADO", hace(horas=1), "Vigente")
    crear("PENDIENTE", hace(horas=14), "Espera vieja")
    visitas = {v["nombre_visitante"]: v["estado"] for v in admin.get("/api/reporte").json()["filas"]}
    assert visitas == {"Vencida": "VENCIDA", "Vigente": "PRE_AUTORIZADO", "Espera vieja": "VENCIDA"}
    assert admin.get("/api/reporte?estado=VENCIDA").json()["total"] == 2
    assert [v["nombre_visitante"] for v in admin.get("/api/reporte?estado=PRE_AUTORIZADO").json()["filas"]] == ["Vigente"]
    assert admin.get("/api/reporte?estado=PENDIENTE").json()["total"] == 0
    todos = {v["nombre_visitante"]: v["estado"] for v in admin.get("/api/accesos?todos=1").json()}
    assert todos["Vencida"] == "VENCIDA"                     # la pantalla de demostración también lo ve así
    assert admin.get("/api/reporte.xlsx").status_code == 200


def test_el_guardia_recibe_la_hora_de_ingreso_para_la_alerta_de_ayer(admin, guardia):
    admin.put("/api/parametros/control_salidas", json={"valor": 1})
    ayer = hace(horas=30)
    crear("ADENTRO", ayer, "Rezagado", hora_ingreso=ayer)
    reg = guardia.get("/api/accesos").json()[0]
    assert reg["hora_ingreso"] == ayer
    assert "telefono" not in reg and "transcripcion" not in reg


def test_rechazo_visible_configurable(admin, guardia):
    crear("RECHAZADO", hace(minutos=40), "Rechazo viejo", hora_respuesta=hace(minutos=40), respondido_por="593988391178")
    assert guardia.get("/api/accesos").json() == []                 # por defecto 30 min
    admin.put("/api/parametros/rechazo_visible_minutos", json={"valor": 60})
    assert len(guardia.get("/api/accesos").json()) == 1


def test_pantalla_de_configuracion_solo_admin(admin, guardia, anonimo):
    assert admin.get("/configuracion").status_code == 200
    guardia.follow_redirects = False
    assert guardia.get("/configuracion").headers["location"] == "/garita"
    assert anonimo.get("/configuracion").headers["location"].startswith("/login")
