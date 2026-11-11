"""Visita sin aviso: el guardia confirma quién vive en la casa y elige a quién se le pregunta."""
from sqlalchemy import insert

import db
from conftest import registrar

CASA = "Mz 6 Villa 23"


def consulta(cliente, mz, villa):
    return cliente.get("/api/casa/residentes", params={"mz": mz, "villa": villa})


def sin_aviso(guardia, ids=None, **extra):
    cuerpo = {"mz": "6", "villa": "23", "nombre_visitante": "Pedro Visita", **extra}
    if ids is not None:
        cuerpo["residente_ids"] = ids
    return guardia.post("/api/visita-sin-aviso", json=cuerpo)


def preparar(admin):
    registrar(admin, "0988391178", CASA, "Juan Pérez")
    registrar(admin, "0977777777", CASA, "María Gómez")
    return {r["nombre"]: r["id"] for r in admin.get("/api/residentes").json()}


# ---------- consultar la casa ----------
def test_muestra_quien_vive_en_la_casa_solo_con_nombres(admin, guardia):
    preparar(admin)
    r = consulta(guardia, "6", "23")
    assert r.status_code == 200
    d = r.json()
    assert d["casa"] == CASA and d["sugerencias"] == []
    assert [x["nombre"] for x in d["residentes"]] == ["Juan Pérez", "María Gómez"]
    assert all(x["avisable"] is True for x in d["residentes"])
    for x in d["residentes"]:                       # el guardia NO recibe celulares, cédulas ni pagos
        assert set(x) == {"id", "nombre", "avisable"}
    assert "telefono" not in r.text and "593988391178" not in r.text and "al_dia" not in r.text


def test_quien_no_tiene_celular_aparece_como_no_avisable(admin, guardia):
    admin.post("/api/residentes", json={"casa": CASA, "nombre": "Sin Celular"})
    registrar(admin, "0988391178", CASA, "Con Celular")
    d = consulta(guardia, "6", "23").json()
    assert {x["nombre"]: x["avisable"] for x in d["residentes"]} == {"Con Celular": True, "Sin Celular": False}


def test_busqueda_tolerante_en_mayusculas_y_espacios(admin, guardia):
    preparar(admin)
    for mz, villa in (("6", "23"), (" 6 ", "23 "), ("6", " 23")):
        assert len(consulta(guardia, mz, villa).json()["residentes"]) == 2


def test_una_casa_equivalente_se_muestra_directa_aunque_se_escriba_distinto(admin, guardia):
    registrar(admin, "0911111111", "Mz 06 Villa 5", "Ana Cero")
    registrar(admin, "0922222222", "Mz 6 Villa 12-A", "Beto Guion")
    registrar(admin, "0933333333", "Mz 6 Villa 7B", "Carla Pegada")
    for mz, villa, casa, nombre in (("6", "5", "Mz 06 Villa 5", "Ana Cero"),      # 6 encuentra 06
                                    ("06", "05", "Mz 06 Villa 5", "Ana Cero"),
                                    ("6", "12A", "Mz 6 Villa 12-A", "Beto Guion"),
                                    ("6", "12 a", "Mz 6 Villa 12-A", "Beto Guion"),
                                    ("6", "7-b", "Mz 6 Villa 7B", "Carla Pegada")):
        d = consulta(guardia, mz, villa).json()
        assert d["casa"] == casa and [x["nombre"] for x in d["residentes"]] == [nombre] and d["sugerencias"] == []
    d = consulta(guardia, "9", "99").json()                      # no existe nada parecido
    assert d["residentes"] == [] and d["sugerencias"] == []


def test_si_hay_varias_casas_equivalentes_se_listan_para_elegir(admin, guardia):
    registrar(admin, "0911111111", "Mz 1 Villa 3", "Sin Cero")
    registrar(admin, "0922222222", "Mz 01 Villa 03", "Con Cero")
    d = consulta(guardia, "1", "3").json()
    assert d["residentes"] == [] and sorted(s["casa"] for s in d["sugerencias"]) == ["Mz 01 Villa 03", "Mz 1 Villa 3"]
    d = consulta(guardia, "1", "3").json()
    d = guardia.get("/api/casa/residentes", params={"mz": "01", "villa": "03", "exacta": 1}).json()   # al tocar una
    assert d["casa"] == "Mz 01 Villa 03" and [x["nombre"] for x in d["residentes"]] == ["Con Cero"]


def test_la_visita_se_guarda_con_la_casa_del_residente_elegido(admin, guardia, wsp):
    registrar(admin, "0911111111", "Mz 06 Villa 5", "Ana Cero")
    ids = {r["nombre"]: r["id"] for r in admin.get("/api/residentes").json()}
    r = guardia.post("/api/visita-sin-aviso", json={"mz": "6", "villa": "5", "nombre_visitante": "Pedro",
                                                    "tipo": "VISITA", "residente_ids": [ids["Ana Cero"]]})
    assert r.status_code == 200 and r.json()["registro"]["casa"] == "Mz 06 Villa 5"


def test_con_dos_formatos_de_la_misma_casa_se_ofrecen_ambos(admin, guardia):
    registrar(admin, "0911111111", "Mz 6 Villa 5", "Ana")
    registrar(admin, "0922222222", "Mz 06 Villa 5", "Beto")
    d = consulta(guardia, "6", "5").json()
    assert d["residentes"] == [] and sorted(s["casa"] for s in d["sugerencias"]) == ["Mz 06 Villa 5", "Mz 6 Villa 5"]


def test_validaciones_y_permisos(admin, guardia, anonimo):
    assert consulta(guardia, " ", "23").status_code == 400
    assert consulta(guardia, "6", "").status_code == 400
    assert consulta(guardia, "7" * 51, "1").status_code == 400
    assert consulta(anonimo, "6", "23").status_code == 401
    assert consulta(admin, "6", "23").status_code == 200


def test_una_casa_impaga_avisa_al_guardia_sin_nombres_y_se_bloquea_al_enviar(admin, guardia, wsp):
    ids = preparar(admin)
    admin.post("/api/casas/pago", json={"casa": CASA, "al_dia": False})
    d = consulta(guardia, "6", "23")
    j = d.json()
    assert j["bloqueada"] is True and j["residentes"] == [] and "no está al día" in j["mensaje"]
    assert "impag" not in d.text.lower() and "Juan" not in d.text          # sin deudas ni nombres
    r = sin_aviso(guardia, [ids["Juan Pérez"]])
    assert r.status_code == 409 and "no está al día" in r.json()["detail"]
    assert wsp == []


# ---------- preguntar solo a quien se elige ----------
def test_se_pregunta_solo_a_la_persona_elegida(admin, guardia, wsp):
    ids = preparar(admin)
    r = sin_aviso(guardia, [ids["Juan Pérez"]])
    assert r.status_code == 200
    d = r.json()
    assert d["preguntado_a"] == ["Juan Pérez"] and d["residentes_avisados"] == 1
    avisos = [(n, t) for n, t in wsp if "Visita sin aviso" in t]
    assert [n for n, _ in avisos] == ["593988391178"]                    # María no recibió nada
    assert "se preguntó a Juan Pérez" in d["registro"]["autorizado_por"]


def test_si_no_se_elige_a_nadie_en_la_peticion_se_pregunta_a_toda_la_casa(admin, guardia, wsp):
    preparar(admin)
    r = sin_aviso(guardia)                                              # sin residente_ids: compatible con lo anterior
    assert r.json()["residentes_avisados"] == 2
    assert sorted(n for n, t in wsp if "Visita sin aviso" in t) == ["593977777777", "593988391178"]


def test_lista_vacia_registra_sin_avisar_a_nadie(admin, guardia, wsp):
    preparar(admin)
    r = sin_aviso(guardia, [])
    assert r.status_code == 200 and r.json()["preguntado_a"] == [] and r.json()["residentes_avisados"] == 0
    assert wsp == []
    assert r.json()["registro"]["autorizado_por"] == "Sin aviso (registrado en garita)"
    assert guardia.get("/api/accesos").json()[0]["estado"] == "PENDIENTE"       # igual queda registrada


def test_no_se_puede_preguntar_a_alguien_de_otra_casa(admin, guardia, wsp):
    preparar(admin)
    registrar(admin, "0966666666", "Mz 2 Villa 5", "Otro Vecino")
    otro = next(x["id"] for x in admin.get("/api/residentes").json() if x["nombre"] == "Otro Vecino")
    r = sin_aviso(guardia, [otro])
    assert r.status_code == 400 and "no pertenecen" in r.json()["detail"]
    assert wsp == []
    assert guardia.get("/api/accesos").json() == []                      # no se registró nada


def test_elegir_a_alguien_sin_celular_no_avisa_a_nadie(admin, guardia, wsp):
    admin.post("/api/residentes", json={"casa": CASA, "nombre": "Sin Celular"})
    sin = next(x["id"] for x in admin.get("/api/residentes").json() if x["nombre"] == "Sin Celular")
    r = sin_aviso(guardia, [sin])
    assert r.status_code == 200 and r.json()["preguntado_a"] == [] and wsp == []


def test_la_respuesta_del_residente_elegido_funciona_igual(admin, guardia, wsp):
    from conftest import texto
    ids = preparar(admin)
    vid = sin_aviso(guardia, [ids["María Gómez"]]).json()["registro"]["id"]
    texto(admin, "593977777777", "1")
    assert next(a for a in guardia.get("/api/accesos").json() if a["id"] == vid)["estado"] == "PRE_AUTORIZADO"
    # Juan no fue consultado, pero igual podría responder por su casa mientras haya una solicitud pendiente
    vid2 = sin_aviso(guardia, [ids["María Gómez"]], nombre_visitante="Otra Visita").json()["registro"]["id"]
    texto(admin, "593988391178", "2")
    assert next(a for a in guardia.get("/api/accesos").json() if a["id"] == vid2)["estado"] == "RECHAZADO"
