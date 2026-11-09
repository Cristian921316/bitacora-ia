"""Regresión de todo lo que ya funcionaba antes de la reorganización."""
import openpyxl
import io

from conftest import registrar, texto, webhook

CASA = "Mz 6 Villa 23"


def test_residentes_normaliza_telefono_y_valida(admin):
    assert registrar(admin, "0988391178")["telefono"] == "593988391178"
    assert admin.post("/api/residentes", json={"telefono": "123", "casa": CASA}).status_code == 400
    assert admin.post("/api/residentes", json={"telefono": "0988391178", "casa": "  "}).status_code == 400
    registrar(admin, "0988391178", "Mz 7   Villa 1", "Cambió")        # actualiza, no duplica
    lista = admin.get("/api/residentes").json()
    assert len(lista) == 1 and lista[0]["casa"] == "Mz 7 Villa 1"
    rid = lista[0]["id"]
    assert admin.delete(f"/api/residentes/{rid}").status_code == 200
    assert admin.delete(f"/api/residentes/{rid}").status_code == 404


def test_anuncio_por_texto_y_audio_por_whatsapp(admin, guardia, wsp):
    registrar(admin)
    assert texto(admin, "593988391178", "Hoy viene Juan Pérez en taxi, placa ABC123")["registros"] == 1
    assert webhook(admin, "593988391178", {"audioMessage": {}, "base64": "AAAA"})["registros"] == 1
    visitas = admin.get("/api/accesos").json()
    assert {v["casa"] for v in visitas} == {CASA}                       # la casa sale del residente, no de la IA
    assert {v["origen"] for v in visitas} == {"whatsapp"}
    assert "Pre-autorizado" in wsp[0][1]
    # número no registrado, mensaje sin visita, tipo no soportado, token
    texto(admin, "593900000000", "viene Ana")
    assert "no está registrado" in wsp[-1][1]
    texto(admin, "593988391178", "hola, gracias")
    assert "No entendí" in wsp[-1][1]
    webhook(admin, "593988391178", {"imageMessage": {}})
    assert "nota de voz" in wsp[-1][1]
    assert admin.post("/api/whatsapp/webhook?token=mala", json={}).status_code == 401


def test_visita_sin_aviso_autorizada_y_rechazada(admin, guardia, wsp):
    registrar(admin, "0988391178")
    registrar(admin, "0977777777", CASA, "Esposa")
    r = guardia.post("/api/visita-sin-aviso", json={"mz": "6", "villa": "23", "nombre_visitante": "Juan", "tipo": "TAXI_UBER", "placa": "ABC123"})
    assert r.status_code == 200 and r.json()["residentes_avisados"] == 2
    assert "telefono" not in r.json()["registro"]                       # el guardia no ve teléfonos
    vid = r.json()["registro"]["id"]
    assert sum("Visita sin aviso" in t for _, t in wsp) == 2
    assert guardia.get("/api/accesos").json()[0]["estado"] == "PENDIENTE"
    texto(admin, "593988391178", "1")
    assert guardia.get("/api/accesos").json()[0]["estado"] == "PRE_AUTORIZADO"
    texto(admin, "593977777777", "2")                                   # ya respondida
    assert "ya fue respondida" in wsp[-1][1]
    # rechazo
    r2 = guardia.post("/api/visita-sin-aviso", json={"mz": "6", "villa": "23", "nombre_visitante": "Desconocido"})
    rid = r2.json()["registro"]["id"]
    texto(admin, "593977777777", "no")
    assert next(v for v in guardia.get("/api/accesos").json() if v["id"] == rid)["estado"] == "RECHAZADO"
    assert guardia.post(f"/api/cambiar-estado/{rid}", json={"estado": "ADENTRO"}).status_code == 409
    # ingreso de la autorizada
    assert guardia.post(f"/api/cambiar-estado/{vid}", json={"estado": "ADENTRO"}).status_code == 200
    assert any("Ingresó tu visita" in t for _, t in wsp)


def test_varias_pendientes_exigen_referencia(admin, guardia, wsp):
    registrar(admin)
    ids = [guardia.post("/api/visita-sin-aviso", json={"mz": "6", "villa": "23", "nombre_visitante": n}).json()["registro"]["id"] for n in ("Ana", "Beto")]
    texto(admin, "593988391178", "1")
    assert "Tienes 2 solicitudes" in wsp[-1][1]
    texto(admin, "593988391178", "2 999")
    assert "No encuentro" in wsp[-1][1]
    texto(admin, "593988391178", f"1 {ids[0]}")
    texto(admin, "593988391178", f"2 {ids[1]}")
    estados = {v["id"]: v["estado"] for v in admin.get("/api/accesos?todos=1").json()}
    assert estados[ids[0]] == "PRE_AUTORIZADO" and estados[ids[1]] == "RECHAZADO"


def test_ingresar_igual_sin_respuesta_y_transiciones(admin, guardia, wsp):
    registrar(admin)
    vid = guardia.post("/api/visita-sin-aviso", json={"mz": "6", "villa": "23", "nombre_visitante": "Carlos"}).json()["registro"]["id"]
    assert guardia.post(f"/api/cambiar-estado/{vid}", json={"estado": "ADENTRO"}).status_code == 200
    assert any("sin tu respuesta" in t for _, t in wsp)
    assert guardia.post(f"/api/cambiar-estado/{vid}", json={"estado": "ADENTRO"}).status_code == 409
    assert guardia.post(f"/api/cambiar-estado/{vid}", json={"estado": "SALIO"}).status_code == 200
    assert guardia.post(f"/api/cambiar-estado/{vid}", json={"estado": "XX"}).status_code == 400
    assert guardia.post("/api/cambiar-estado/99999", json={"estado": "ADENTRO"}).status_code == 404
    assert guardia.get("/api/accesos").json() == []                    # las que salieron ya no cargan en la garita
    assert [v["estado"] for v in admin.get("/api/accesos?todos=1").json()] == ["SALIO"]


def test_reportes_filtros_y_excel(admin, guardia, wsp):
    registrar(admin)
    registrar(admin, "0966666666", "Mz 2 Villa 5", "Otro")
    texto(admin, "593988391178", "Hoy viene Juan Pérez en taxi, placa ABC123")
    texto(admin, "593966666666", "Hoy viene Pedro Gómez")
    vid = admin.get("/api/accesos").json()[1]["id"]
    guardia.post(f"/api/cambiar-estado/{vid}", json={"estado": "ADENTRO"})
    r = admin.get("/api/reporte").json()
    assert r["total"] == 2
    assert admin.get("/api/reporte?q=mz6 v23").json()["total"] == 1
    assert admin.get("/api/reporte?q=perez").json()["total"] == 1     # sin distinguir acentos
    assert admin.get("/api/reporte?q=gomez").json()["total"] == 1
    assert admin.get("/api/reporte?estado=ADENTRO").json()["total"] == 1
    assert admin.get("/api/reporte?vista=movimientos&estado=INGRESO").json()["filas"][0]["usuario"] == "guardia1"
    assert admin.get("/api/reporte?desde=2000-01-01&hasta=2000-01-02").json()["total"] == 0
    assert admin.get("/api/reporte?desde=hoy").status_code == 400
    assert admin.get("/api/reporte?vista=otra").status_code == 400
    csv = admin.get("/api/reporte.csv")
    assert csv.status_code == 200 and csv.content.startswith(b"\xef\xbb\xbfID,")
    for vista in ("visitas", "movimientos", "bloqueos"):
        x = admin.get(f"/api/reporte.xlsx?vista={vista}")
        assert x.status_code == 200 and "spreadsheetml" in x.headers["content-type"]
        openpyxl.load_workbook(io.BytesIO(x.content))                  # es un .xlsx válido


def test_excel_no_ejecuta_formulas(admin, wsp, monkeypatch):
    import main
    registrar(admin)
    from db import accesos, engine, ahora_iso
    from sqlalchemy import insert
    with engine.begin() as conn:
        conn.execute(insert(accesos).values(casa=CASA, nombre_visitante='=HYPERLINK("http://x")', tipo="VISITA",
                                            estado="PRE_AUTORIZADO", fecha_hora=ahora_iso(), transcripcion="=1+1"))
    ws = openpyxl.load_workbook(io.BytesIO(admin.get("/api/reporte.xlsx").content)).active
    celdas = {c.value: c.data_type for fila in ws.iter_rows(min_row=2) for c in fila if isinstance(c.value, str)}
    assert celdas['=HYPERLINK("http://x")'] == "s" and celdas["=1+1"] == "s"
    assert admin.get("/api/reporte.csv").content.decode("utf-8-sig").count("'=HYPERLINK") == 1


def test_pantalla_residente_web_solo_admin_usa_voz_y_texto(admin, guardia):
    r = admin.post("/api/procesar-texto", json={"texto": "Esta tarde viene Pedro Gómez"})
    assert r.status_code == 200 and r.json()["registros"][0]["origen"] == "web"
    assert admin.post("/api/procesar-texto", json={"texto": "   "}).status_code == 400
    v = admin.post("/api/procesar-voz", files={"audio": ("nota.webm", b"datos", "audio/webm")})
    assert v.status_code == 200 and len(v.json()["registros"]) == 1


def test_validaciones_de_visita_sin_aviso(guardia):
    assert guardia.post("/api/visita-sin-aviso", json={"mz": " ", "villa": "1", "nombre_visitante": "X"}).status_code == 400
    assert guardia.post("/api/visita-sin-aviso", json={"mz": "1", "villa": "1", "nombre_visitante": "X" * 200}).status_code == 400
    assert guardia.post("/api/visita-sin-aviso", json={"mz": "1", "villa": "1", "nombre_visitante": "X", "tipo": "HACKER"}).status_code == 422
