"""En el piloto no se controlan las salidas: la garita solo muestra pre-autorizadas y pendientes de autorizar."""
from datetime import datetime, timedelta

from sqlalchemy import insert

import db
from conftest import registrar, texto

CASA = "Mz 6 Villa 23"


def hace(horas):
    return (datetime.now() - timedelta(hours=horas)).isoformat(timespec="seconds")


def test_por_defecto_la_garita_no_muestra_a_los_que_ya_ingresaron(admin, guardia, wsp):
    registrar(admin)
    texto(admin, "593988391178", "Hoy viene Juan Pérez")
    vid = guardia.get("/api/accesos").json()[0]["id"]
    assert guardia.get("/api/config").json()["control_salidas"] is False
    assert guardia.post(f"/api/cambiar-estado/{vid}", json={"estado": "ADENTRO"}).status_code == 200
    assert guardia.get("/api/accesos").json() == []                      # al ingresar sale de la pantalla
    assert any("Ingresó tu visita" in t for _, t in wsp)                 # el residente sí recibe su aviso
    mov = admin.get("/api/reporte?vista=movimientos&estado=INGRESO").json()["filas"]
    assert len(mov) == 1 and mov[0]["usuario"] == "guardia1"             # y el ingreso queda registrado
    assert admin.get("/api/accesos?todos=1").json()[0]["estado"] == "ADENTRO"


def test_con_control_de_salidas_vuelve_la_columna_adentro(admin, guardia, wsp):
    registrar(admin)
    texto(admin, "593988391178", "Hoy viene Juan Pérez")
    vid = guardia.get("/api/accesos").json()[0]["id"]
    guardia.post(f"/api/cambiar-estado/{vid}", json={"estado": "ADENTRO"})
    assert guardia.get("/api/accesos").json() == []
    assert admin.put("/api/parametros/control_salidas", json={"valor": 1}).status_code == 200
    assert guardia.get("/api/config").json()["control_salidas"] is True
    assert [a["estado"] for a in guardia.get("/api/accesos").json()] == ["ADENTRO"]
    assert guardia.post(f"/api/cambiar-estado/{vid}", json={"estado": "SALIO"}).status_code == 200


def test_el_interruptor_solo_acepta_0_o_1(admin):
    assert admin.put("/api/parametros/control_salidas", json={"valor": 2}).status_code == 400
    assert admin.put("/api/parametros/control_salidas", json={"valor": -1}).status_code == 400
    ps = {p["clave"]: p for p in admin.get("/api/parametros").json()}
    assert ps["control_salidas"]["tipo"] == "switch" and ps["control_salidas"]["valor"] == 0


def test_el_reporte_dice_ingreso_y_no_adentro_si_no_se_controla_la_salida(admin):
    with db.engine.begin() as conn:
        conn.execute(insert(db.accesos).values(casa=CASA, nombre_visitante="Ana", tipo="VISITA", estado="ADENTRO",
                                               fecha_hora=hace(1), hora_ingreso=hace(1)))
    assert admin.get("/api/reporte").json()["filas"][0]["estado"] == "INGRESÓ"
    assert admin.get("/api/reporte?estado=ADENTRO").json()["total"] == 1       # el filtro sigue funcionando
    admin.put("/api/parametros/control_salidas", json={"valor": 1})
    assert admin.get("/api/reporte").json()["filas"][0]["estado"] == "ADENTRO"
