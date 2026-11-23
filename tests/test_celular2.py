"""Segundo celular por residente (esposa, hija…) con alias: los dos celulares son la MISMA persona de la MISMA casa."""
from conftest import registrar, texto

CASA = "Mz 6 Villa 23"


def lista(admin):
    return admin.get("/api/residentes").json()


def alta(admin, **extra):
    cuerpo = {"telefono": "0988391178", "casa": CASA, "nombre": "Juan Pérez", **extra}
    return admin.post("/api/residentes", json=cuerpo)


# ---------- alta y edición ----------
def test_alta_con_celular2_y_alias(admin):
    r = alta(admin, telefono2="0991112223", alias2="  esposa ")
    assert r.status_code == 200
    assert r.json()["telefono2"] == "593991112223" and r.json()["alias2"] == "esposa"
    x = lista(admin)
    assert len(x) == 1 and x[0]["telefono2"] == "593991112223" and x[0]["alias2"] == "esposa"


def test_alias_sin_celular2_se_descarta(admin):
    r = alta(admin, alias2="esposa")
    assert r.status_code == 200 and r.json()["alias2"] is None


def test_celular2_no_puede_ser_igual_al_principal_ni_invalido(admin):
    assert alta(admin, telefono2="0988391178").status_code == 400
    assert alta(admin, telefono2="123").status_code == 400
    assert lista(admin) == []


def test_un_celular_no_puede_estar_en_dos_personas_ni_en_las_dos_columnas(admin):
    alta(admin, telefono2="0991112223")
    otra = {"casa": "Mz 7 Villa 1", "nombre": "Otra"}
    assert admin.post("/api/residentes", json={**otra, "telefono": "0991112223"}).status_code == 409      # es el celular 2 de Juan
    assert admin.post("/api/residentes", json={**otra, "telefono": "0977777777", "telefono2": "0991112223"}).status_code == 409
    assert admin.post("/api/residentes", json={**otra, "telefono": "0977777777", "telefono2": "0988391178"}).status_code == 409
    assert len(lista(admin)) == 1


def test_editar_celular2_y_alias_en_linea(admin):
    rid = alta(admin).json() and lista(admin)[0]["id"]
    assert admin.patch(f"/api/residentes/{rid}", json={"alias2": "hija"}).status_code == 400   # alias sin celular 2
    r = admin.patch(f"/api/residentes/{rid}", json={"telefono2": "0991112223"})
    assert r.status_code == 200 and r.json()["telefono2"] == "593991112223" and r.json()["alias2"] is None
    r = admin.patch(f"/api/residentes/{rid}", json={"alias2": "hija"})
    assert r.status_code == 200 and r.json()["alias2"] == "hija"
    assert admin.patch(f"/api/residentes/{rid}", json={"telefono2": "0988391178"}).status_code == 400   # = principal
    r = admin.patch(f"/api/residentes/{rid}", json={"telefono2": ""})          # vaciar el celular 2 quita también el alias
    assert r.status_code == 200 and r.json()["telefono2"] is None and r.json()["alias2"] is None


def test_editar_no_pisa_el_celular_de_otra_persona(admin):
    alta(admin, telefono2="0991112223")
    registrar(admin, "0977777777", "Mz 7 Villa 1", "Otra")
    otra = next(x for x in lista(admin) if x["nombre"] == "Otra")["id"]
    assert admin.patch(f"/api/residentes/{otra}", json={"telefono": "0991112223"}).status_code == 409
    assert admin.patch(f"/api/residentes/{otra}", json={"telefono2": "0988391178"}).status_code == 409
    assert admin.patch(f"/api/residentes/{otra}", json={"telefono2": "0991112223"}).status_code == 409


# ---------- WhatsApp: los dos celulares identifican a la misma persona ----------
def test_el_celular2_puede_anunciar_visitas_y_solo_a_el_se_le_responde(admin, guardia, wsp):
    alta(admin, telefono2="0991112223", alias2="esposa")
    r = texto(admin, "593991112223", "Hoy viene Juan Pérez en taxi, placa ABC123")
    assert r["registros"] == 1
    assert admin.get("/api/accesos").json()[0]["casa"] == CASA            # la casa sale de la fila de la persona
    assert [n for n, _ in wsp] == ["593991112223"]                        # confirmación solo al número que escribió


def test_casa_en_mora_bloquea_los_dos_celulares(admin, wsp):
    alta(admin, telefono2="0991112223")
    admin.post("/api/casas/pago", json={"casa": CASA, "al_dia": False})
    assert texto(admin, "593988391178", "viene Pedro").get("bloqueado") is True
    assert texto(admin, "593991112223", "viene Pedro").get("bloqueado") is True


# ---------- visita sin aviso ----------
def test_visita_sin_aviso_llega_a_los_dos_celulares_y_el_guardia_ve_un_solo_nombre(admin, guardia, wsp):
    alta(admin, telefono2="0991112223", alias2="esposa")
    d = guardia.get("/api/casa/residentes", params={"mz": "6", "villa": "23"}).json()
    assert [x["nombre"] for x in d["residentes"]] == ["Juan Pérez"] and d["residentes"][0]["avisable"] is True
    assert "esposa" not in guardia.get("/api/casa/residentes", params={"mz": "6", "villa": "23"}).text   # el alias es solo del admin
    assert "593991112223" not in guardia.get("/api/casa/residentes", params={"mz": "6", "villa": "23"}).text

    r = guardia.post("/api/visita-sin-aviso", json={"mz": "6", "villa": "23", "nombre_visitante": "Pedro"})
    assert r.status_code == 200
    assert r.json()["residentes_avisados"] == 1 and r.json()["preguntado_a"] == ["Juan Pérez"]    # una persona
    assert sorted(n for n, t in wsp if "Visita sin aviso" in t) == ["593988391178", "593991112223"]   # dos celulares


def test_cualquiera_de_los_dos_responde_y_gana_el_primero(admin, guardia, wsp):
    alta(admin, telefono2="0991112223")
    vid = guardia.post("/api/visita-sin-aviso", json={"mz": "6", "villa": "23", "nombre_visitante": "Pedro"}).json()["registro"]["id"]
    texto(admin, "593991112223", "1")                                     # responde la esposa
    assert next(v for v in guardia.get("/api/accesos").json() if v["id"] == vid)["estado"] == "PRE_AUTORIZADO"
    texto(admin, "593988391178", "2")                                     # el titular llega tarde
    assert "ya fue respondida" in wsp[-1][1]


def test_persona_con_solo_celular2_es_avisable(admin, guardia, wsp):
    r = admin.post("/api/residentes", json={"casa": CASA, "nombre": "Solo dos", "telefono2": "0991112223"})
    assert r.status_code == 200
    d = guardia.get("/api/casa/residentes", params={"mz": "6", "villa": "23"}).json()
    assert d["residentes"][0]["avisable"] is True
    guardia.post("/api/visita-sin-aviso", json={"mz": "6", "villa": "23", "nombre_visitante": "Pedro"})
    assert [n for n, t in wsp if "Visita sin aviso" in t] == ["593991112223"]


def test_aviso_de_ingreso_llega_a_los_dos_celulares(admin, guardia, wsp):
    alta(admin, telefono2="0991112223")
    vid = guardia.post("/api/visita-sin-aviso", json={"mz": "6", "villa": "23", "nombre_visitante": "Pedro"}).json()["registro"]["id"]
    wsp.clear()
    guardia.post(f"/api/cambiar-estado/{vid}", json={"estado": "ADENTRO"})
    assert sorted(n for n, t in wsp if "visita" in t.lower()) == ["593988391178", "593991112223"]


# ---------- botón Llamar (garita) y exportación ----------
def test_llamar_redirige_a_wame_y_el_celular_no_viaja_en_la_lista(admin, guardia):
    alta(admin, telefono2="0991112223")
    d = guardia.get("/api/casa/residentes", params={"mz": "6", "villa": "23"}).json()
    r0 = d["residentes"][0]
    assert r0["avisable"] is True and "5939" not in str(d)
    r = guardia.get(f"/api/residentes/{r0['id']}/llamar", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://wa.me/593988391178"


def test_llamar_sin_celular_mora_y_sin_sesion(admin, guardia, anonimo):
    admin.post("/api/residentes", json={"casa": "Mz 1 Villa 1", "nombre": "Sin Cel"})
    sin = next(x for x in lista(admin) if x["nombre"] == "Sin Cel")["id"]
    assert guardia.get(f"/api/residentes/{sin}/llamar", follow_redirects=False).status_code == 404
    alta(admin)
    juan = next(x for x in lista(admin) if x["nombre"] == "Juan Pérez")["id"]
    assert anonimo.get(f"/api/residentes/{juan}/llamar", follow_redirects=False).status_code == 401
    admin.post("/api/casas/pago", json={"casa": CASA, "al_dia": False})
    assert guardia.get(f"/api/residentes/{juan}/llamar", follow_redirects=False).status_code == 403


def test_exportar_residentes_excel_solo_admin(admin, guardia):
    import io, openpyxl
    alta(admin, telefono2="0991112223", alias2="esposa")
    r = admin.get("/api/residentes/exportar.xlsx")
    assert r.status_code == 200
    filas = list(openpyxl.load_workbook(io.BytesIO(r.content)).active.values)
    assert filas[0][:4] == ("Cédula", "Nombre", "Casa", "Estado de pago")
    assert filas[1][1:] == ("Juan Pérez", CASA, "Al día", "593988391178", "593991112223", "esposa")
    assert guardia.get("/api/residentes/exportar.xlsx").status_code == 403
