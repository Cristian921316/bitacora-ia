import config
from conftest import registrar, texto, webhook

CASA = "Mz 6 Villa 23"


def estado_pago(admin, tel):
    return next(r for r in admin.get("/api/residentes").json() if r["telefono"] == tel)


def test_el_pago_es_por_casa_no_por_telefono(admin):
    registrar(admin, "0988391178", CASA, "Marido")
    registrar(admin, "0977777777", CASA, "Esposa")
    registrar(admin, "0966666666", "Mz 2 Villa 5", "Otro")
    assert all(r["al_dia"] for r in admin.get("/api/residentes").json())  # nacen al día
    assert admin.post("/api/casas/pago", json={"casa": "mz 6   villa 23", "al_dia": False}).status_code == 200
    assert estado_pago(admin, "593988391178")["al_dia"] is False
    assert estado_pago(admin, "593977777777")["al_dia"] is False   # misma casa
    assert estado_pago(admin, "593966666666")["al_dia"] is True    # otra casa no cambia
    r = estado_pago(admin, "593988391178")
    assert r["pago_actualizado_por"] == "admin" and r["pago_actualizado"]
    admin.post("/api/casas/pago", json={"casa": CASA, "al_dia": True})
    assert estado_pago(admin, "593977777777")["al_dia"] is True


def test_whatsapp_de_casa_impaga_se_bloquea_con_mensaje_neutro(admin, guardia, wsp):
    registrar(admin)
    admin.post("/api/casas/pago", json={"casa": CASA, "al_dia": False})
    r = texto(admin, "593988391178", "Hoy viene Juan Pérez")
    assert r.get("bloqueado") is True
    assert admin.get("/api/accesos").json() == []              # no se creó la visita
    respuesta = wsp[-1][1].lower()
    assert "administración" in respuesta
    assert "pago" not in respuesta and "mora" not in respuesta and "deuda" not in respuesta  # no revela el motivo
    # también con nota de voz
    assert webhook(admin, "593988391178", {"audioMessage": {}, "base64": "AAAA"}).get("bloqueado") is True
    bloqueos = admin.get("/api/reporte?vista=bloqueos").json()
    assert bloqueos["total"] == 2 and bloqueos["filas"][0]["motivo"] == "IMPAGO"
    # al ponerse al día vuelve a funcionar
    admin.post("/api/casas/pago", json={"casa": CASA, "al_dia": True})
    assert texto(admin, "593988391178", "Hoy viene Juan Pérez")["registros"] == 1


def test_guardia_no_puede_registrar_visita_sin_aviso_en_casa_impaga(admin, guardia, wsp):
    registrar(admin)
    admin.post("/api/casas/pago", json={"casa": CASA, "al_dia": False})
    r = guardia.post("/api/visita-sin-aviso", json={"mz": "6", "villa": "23", "nombre_visitante": "Ana"})
    assert r.status_code == 409 and "no está al día" in r.json()["detail"] and "Debe comunicarse con administración" in r.json()["detail"]
    c = guardia.get("/api/casa/residentes", params={"mz": "6", "villa": "23"}).json()   # la pantalla lo avisa antes de enviar
    assert c["bloqueada"] is True and c["residentes"] == [] and "no está al día" in c["mensaje"]
    assert "593988391178" not in str(c) and "Juan" not in str(c)
    assert wsp == []                                            # al residente no se le molesta
    b = admin.get("/api/reporte?vista=bloqueos").json()["filas"][0]
    assert b["usuario"] == "guardia1" and b["origen"] == "garita" and "Ana" in b["detalle"]


def test_ingreso_bloqueado_si_la_casa_cae_en_mora_despues_de_pre_autorizar(admin, guardia, wsp):
    registrar(admin)
    texto(admin, "593988391178", "Hoy viene Juan Pérez")
    vid = admin.get("/api/accesos").json()[0]["id"]
    admin.post("/api/casas/pago", json={"casa": CASA, "al_dia": False})
    r = guardia.post(f"/api/cambiar-estado/{vid}", json={"estado": "ADENTRO"})
    assert r.status_code == 409 and "administración" in r.json()["detail"].lower()
    assert admin.get("/api/accesos").json()[0]["estado"] == "PRE_AUTORIZADO"
    # el administrador sí puede hacer una excepción, y queda registrado quién fue
    assert admin.post(f"/api/cambiar-estado/{vid}", json={"estado": "ADENTRO"}).status_code == 200
    mov = admin.get("/api/reporte?vista=movimientos&estado=INGRESO").json()["filas"][0]
    assert mov["usuario"] == "admin" and mov["origen"] == "admin"


def test_el_bloqueo_se_puede_desactivar(admin, guardia, wsp, monkeypatch):
    registrar(admin)
    admin.post("/api/casas/pago", json={"casa": CASA, "al_dia": False})
    monkeypatch.setattr(config, "BLOQUEO_POR_MORA", False)
    assert texto(admin, "593988391178", "Hoy viene Juan Pérez")["registros"] == 1


def test_casa_inexistente_no_se_puede_registrar(guardia, wsp):
    r = guardia.post("/api/visita-sin-aviso", json={"mz": "9", "villa": "99", "nombre_visitante": "Nadie"})
    assert r.status_code == 400 and "No existe la casa" in r.json()["detail"] and wsp == []
    c = guardia.get("/api/casa/residentes", params={"mz": "9", "villa": "99"}).json()
    assert c["existe"] is False and c["residentes"] == [] and c["sugerencias"] == []
