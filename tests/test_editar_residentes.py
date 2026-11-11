"""Editar residentes (nombre, cédula, celular y casa) y alta manual con celular opcional."""
from conftest import registrar

CASA = "Mz 6 Villa 23"


def uno(admin, **filtro):
    return next(x for x in admin.get("/api/residentes").json() if all(x[k] == v for k, v in filtro.items()))


def patch(admin, rid, **cambios):
    return admin.patch(f"/api/residentes/{rid}", json=cambios)


def test_editar_un_campo_no_toca_los_demas(admin):
    registrar(admin, "0988391178", CASA, "Juan Pérez")
    rid = uno(admin, telefono="593988391178")["id"]
    r = patch(admin, rid, cedula="0912345678")
    assert r.status_code == 200
    x = r.json()
    assert (x["cedula"], x["nombre"], x["telefono"], x["casa"]) == ("0912345678", "Juan Pérez", "593988391178", CASA)
    assert x["al_dia"] is True
    r = patch(admin, rid, nombre="  Juan Andrés Pérez  ")
    assert r.json()["nombre"] == "Juan Andrés Pérez" and r.json()["cedula"] == "0912345678"


def test_el_celular_se_normaliza_y_se_valida(admin):
    registrar(admin, "0988391178", CASA, "Juan")
    rid = uno(admin, telefono="593988391178")["id"]
    assert patch(admin, rid, telefono="0955 555 555").json()["telefono"] == "593955555555"
    assert patch(admin, rid, telefono="955555555").json()["telefono"] == "593955555555"
    for malo in ("123", "N/A", "9" * 20):
        r = patch(admin, rid, telefono=malo)
        assert r.status_code == 400 and "Celular inválido" in r.json()["detail"], malo
    assert uno(admin, id=rid)["telefono"] == "593955555555"          # lo inválido no cambió nada


def test_vaciar_el_celular_lo_quita_y_se_puede_volver_a_poner(admin):
    registrar(admin, "0988391178", CASA, "Juan")
    rid = uno(admin, telefono="593988391178")["id"]
    assert patch(admin, rid, telefono="").json()["telefono"] is None
    assert patch(admin, rid, telefono="0988391178").json()["telefono"] == "593988391178"


def test_no_se_puede_usar_el_celular_de_otra_persona(admin):
    registrar(admin, "0988391178", CASA, "Juan Pérez")
    registrar(admin, "0977777777", "Mz 2 Villa 5", "María Gómez")
    rid = uno(admin, telefono="593977777777")["id"]
    r = patch(admin, rid, telefono="0988391178")
    assert r.status_code == 409 and "Juan Pérez" in r.json()["detail"] and CASA in r.json()["detail"]
    assert uno(admin, id=rid)["telefono"] == "593977777777"


def test_la_cedula_puede_repetirse_y_vaciarse(admin):
    registrar(admin, "0988391178", CASA, "Juan")
    registrar(admin, "0977777777", "Mz 2 Villa 5", "María")
    a, b = uno(admin, nombre="Juan")["id"], uno(admin, nombre="María")["id"]
    assert patch(admin, a, cedula="0912345678").status_code == 200
    assert patch(admin, b, cedula="0912345678").status_code == 200          # repetida: se acepta
    assert patch(admin, b, cedula="").json()["cedula"] is None
    assert patch(admin, a, cedula="9" * 21).status_code == 400


def test_cambiar_la_casa_crea_la_casa_y_muestra_su_estado_de_pago(admin):
    registrar(admin, "0988391178", CASA, "Juan")
    admin.post("/api/casas/pago", json={"casa": "Mz 8 Villa 8", "al_dia": False})      # la casa destino está impaga
    rid = uno(admin, telefono="593988391178")["id"]
    assert uno(admin, id=rid)["al_dia"] is True
    x = patch(admin, rid, casa="Mz   8  Villa 8").json()                                # espacios de más: se normalizan
    assert x["casa"] == "Mz 8 Villa 8" and x["al_dia"] is False                         # hereda el estado de la casa nueva
    nueva = patch(admin, rid, casa="Mz 40 Villa 1").json()                               # casa que no existía: nace al día
    assert nueva["casa"] == "Mz 40 Villa 1" and nueva["al_dia"] is True
    assert patch(admin, rid, casa="   ").status_code == 400
    assert patch(admin, rid, casa="X" * 200).status_code == 400


def test_nombre_vacio_se_guarda_como_sin_nombre(admin):
    registrar(admin, "0988391178", CASA, "Juan")
    rid = uno(admin, telefono="593988391178")["id"]
    assert patch(admin, rid, nombre="   ").json()["nombre"] is None
    assert patch(admin, rid, nombre="N" * 201).status_code == 400


def test_errores_y_permisos(admin, guardia, anonimo):
    registrar(admin, "0988391178", CASA, "Juan")
    rid = uno(admin, telefono="593988391178")["id"]
    assert admin.patch("/api/residentes/999999", json={"nombre": "X"}).status_code == 404
    assert admin.patch(f"/api/residentes/{rid}", json={}).status_code == 400            # nada que cambiar
    assert guardia.patch(f"/api/residentes/{rid}", json={"nombre": "X"}).status_code == 403
    assert anonimo.patch(f"/api/residentes/{rid}", json={"nombre": "X"}).status_code == 401
    assert uno(admin, id=rid)["nombre"] == "Juan"


def test_un_celular_editado_sirve_de_inmediato_por_whatsapp(admin, wsp):
    from conftest import texto
    admin.post("/api/residentes", json={"casa": CASA, "nombre": "Sin celular"})        # alta sin celular
    rid = uno(admin, nombre="Sin celular")["id"]
    assert texto(admin, "593988391178", "Hoy viene Juan Pérez").get("ignorado") == "residente no registrado"
    patch(admin, rid, telefono="0988391178")
    assert texto(admin, "593988391178", "Hoy viene Juan Pérez")["registros"] == 1


# ---------- alta manual ----------
def test_alta_manual_con_celular_opcional_y_cedula(admin):
    r = admin.post("/api/residentes", json={"casa": CASA, "nombre": "Ana", "cedula": " 0912 345678 "})
    assert r.status_code == 200 and r.json()["telefono"] is None and r.json()["cedula"] == "0912345678"
    assert admin.post("/api/residentes", json={"casa": CASA, "nombre": "ana"}).status_code == 409     # misma persona y casa
    assert admin.post("/api/residentes", json={"casa": CASA, "nombre": "Ana", "telefono": "123"}).status_code == 400
    assert admin.post("/api/residentes", json={"casa": CASA, "nombre": "Beto", "telefono": "0988391178"}).status_code == 200
    assert admin.post("/api/residentes", json={"casa": "X" * 200, "nombre": "Z"}).status_code == 400
    assert admin.post("/api/residentes", json={"casa": "  ", "nombre": "Z"}).status_code == 400
    assert len(admin.get("/api/residentes").json()) == 2


def test_el_formulario_de_visita_sin_aviso_admite_50_caracteres(admin, guardia):
    ok = "7" * 50
    admin.post("/api/residentes", json={"casa": f"Mz {ok} Villa {ok}", "nombre": "Vecino Largo"})
    assert guardia.post("/api/visita-sin-aviso", json={"mz": ok, "villa": ok, "nombre_visitante": "Pedro"}).status_code == 200
    assert guardia.post("/api/visita-sin-aviso", json={"mz": "7" * 51, "villa": "1", "nombre_visitante": "Pedro"}).status_code == 400
