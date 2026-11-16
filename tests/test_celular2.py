"""Segundo celular por residente (esposa, hija…) con alias: los dos celulares son la MISMA persona de la MISMA casa."""
import openpyxl
import io

from conftest import registrar, texto
from test_importar import lista, subir

CASA = "Mz 6 Villa 23"
ENC2 = ["Cédula", "Nombre", "Mz", "Villa", "Estado de pago", "Celular", "Celular 2", "Alias del celular 2"]


def xlsx2(filas, encabezado=ENC2):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(encabezado)
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


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


# ---------- importador ----------
def test_importar_celular2_y_alias_y_volver_a_subir_no_duplica(admin):
    archivo = xlsx2([["", "Juan Pérez", "6", "23", "Al día", "0988391178", "0991112223", "esposa"]])
    r = subir(admin, archivo, aplicar=True)
    assert r.status_code == 200 and r.json()["resumen"]["residentes_nuevos"] == 1
    x = lista(admin)
    assert len(x) == 1 and x[0]["telefono2"] == "593991112223" and x[0]["alias2"] == "esposa"
    again = subir(admin, archivo, aplicar=True).json()
    assert again["resumen"]["residentes_nuevos"] == 0 and len(lista(admin)) == 1


def test_importar_sin_las_columnas_nuevas_sigue_funcionando(admin):
    enc = ["Cédula", "Nombre", "Mz", "Villa", "Estado de pago", "Celular"]
    r = subir(admin, xlsx2([["", "Ana", "1", "1", "Al día", "0966666666"]], encabezado=enc), aplicar=True)
    assert r.status_code == 200 and lista(admin)[0]["telefono2"] is None


def test_importar_celular2_ajeno_se_omite_y_se_avisa(admin):
    registrar(admin, "0977777777", "Mz 9 Villa 9", "Otra Persona")
    r = subir(admin, xlsx2([["", "Juan Pérez", "6", "23", "Al día", "0988391178", "0977777777", "esposa"]]), aplicar=True).json()
    assert any("celular 2" in a["motivo"] and "ya lo tiene" in a["motivo"] for a in r["avisos"])
    juan = next(x for x in lista(admin) if x["nombre"] == "Juan Pérez")
    assert juan["telefono2"] is None and juan["alias2"] is None


def test_importar_celular_principal_que_es_el_celular2_de_otro_no_se_pisa(admin):
    alta(admin, telefono2="0991112223")
    r = subir(admin, xlsx2([["", "Nueva Persona", "7", "7", "Al día", "0991112223", "", ""]]), aplicar=True).json()
    assert any("ya lo tiene" in a["motivo"] for a in r["avisos"])
    assert next(x for x in lista(admin) if x["nombre"] == "Nueva Persona")["telefono"] is None


def test_importar_no_cambia_un_celular2_ya_existente(admin):
    alta(admin, telefono2="0991112223", alias2="esposa")
    r = subir(admin, xlsx2([["", "Juan Pérez", "6", "23", "Al día", "0988391178", "0984445556", "hija"]]), aplicar=True).json()
    assert any("ya tiene otro celular 2" in a["motivo"] for a in r["avisos"])
    x = lista(admin)[0]
    assert x["telefono2"] == "593991112223" and x["alias2"] == "esposa"
