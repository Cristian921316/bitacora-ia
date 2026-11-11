"""Importación de residentes desde Excel/CSV."""
import io

import openpyxl
from sqlalchemy import func, select

import db
from conftest import registrar, texto

ENC = ["Cédula", "Nombre", "Mz", "Villa", "Estado de pago", "Celular"]
CASA = "Mz 6 Villa 23"


def xlsx(filas, encabezado=ENC, previas=0):
    wb = openpyxl.Workbook()
    ws = wb.active
    for _ in range(previas):
        ws.append(["Listado de residentes", None, None])
    ws.append(encabezado)
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def subir(cliente, contenido, nombre="residentes.xlsx", aplicar=False):
    return cliente.post(f"/api/residentes/importar?aplicar={'true' if aplicar else 'false'}",
                        files={"archivo": (nombre, contenido, "application/octet-stream")})


def contar(tabla):
    with db.engine.connect() as c:
        return c.execute(select(func.count()).select_from(tabla)).scalar()


def lista(admin):
    return admin.get("/api/residentes").json()


FILAS = [
    ["0912345678", "Juan Pérez", "6", "23", "Al día", "0988391178"],
    ["0923456789", "María Gómez", "6", "24", "Impago", "0977777777"],
    ["0934567890", "Pedro Ruiz", "2", "5", "Pagado", "0966666666"],
]


# ---------- lo básico ----------
def test_revisar_no_guarda_y_confirmar_si(admin):
    r = subir(admin, xlsx(FILAS))
    assert r.status_code == 200 and r.json()["aplicado"] is False
    res = r.json()["resumen"]
    assert (res["filas_validas"], res["residentes_nuevos"], res["casas_nuevas"]) == (3, 3, 3)
    assert (res["casas_al_dia"], res["casas_impagas"]) == (2, 1)
    assert contar(db.residentes) == 0 and contar(db.casas) == 0       # revisar no deja nada

    r = subir(admin, xlsx(FILAS), aplicar=True)
    assert r.json()["aplicado"] is True and contar(db.residentes) == 3
    por_nombre = {x["nombre"]: x for x in lista(admin)}
    assert por_nombre["Juan Pérez"]["cedula"] == "0912345678" and por_nombre["Juan Pérez"]["telefono"] == "593988391178"
    assert por_nombre["Juan Pérez"]["casa"] == CASA and por_nombre["Juan Pérez"]["al_dia"] is True
    assert por_nombre["María Gómez"]["al_dia"] is False and por_nombre["María Gómez"]["pago_actualizado_por"] == "admin"
    assert por_nombre["Pedro Ruiz"]["al_dia"] is True


# ---------- reglas nuevas: qué ya no se valida ----------
def test_celular_opcional_y_sin_formato_se_carga_sin_celular_y_avisa(admin):
    filas = [
        ["", "Ana Uno", "1", "1", "Al día", ""],                         # sin celular: se carga, sin aviso
        ["", "Beto Dos", "1", "2", "Al día", "N/A"],                     # texto: sin celular + aviso
        ["", "Carla Tres", "1", "3", "Al día", "123"],                   # muy corto: sin celular + aviso
        ["", "Dora Cuatro", "1", "4", "Al día", "1" * 23],               # muy largo: sin celular + aviso
        ["", "Elena Cinco", "1", "5", "Al día", "0988391178"],           # bueno
    ]
    r = subir(admin, xlsx(filas), aplicar=True).json()
    assert r["resumen"]["filas_validas"] == 5 and r["resumen"]["filas_con_error"] == 0
    assert r["resumen"]["sin_celular"] == 4
    assert len(r["avisos"]) == 3 and all("no es utilizable" in a["motivo"] for a in r["avisos"])
    assert {x["nombre"]: x["telefono"] for x in lista(admin)} == {
        "Ana Uno": None, "Beto Dos": None, "Carla Tres": None, "Dora Cuatro": None, "Elena Cinco": "593988391178"}


def test_cedula_repetida_o_rara_se_acepta_sin_validar(admin):
    filas = [["0912345678", "Juan Pérez", "6", "23", "Al día", "0988391178"],
             ["0912345678", "María Gómez", "6", "24", "Al día", "0977777777"],      # misma cédula: se acepta
             ["ABC-12 34 xyz", "Pedro Ruiz", "2", "5", "Al día", ""]]               # formato raro: se acepta
    r = subir(admin, xlsx(filas), aplicar=True).json()
    assert r["resumen"]["filas_validas"] == 3 and r["errores_total"] == 0
    assert sorted(x["cedula"] for x in lista(admin)) == ["0912345678", "0912345678", "ABC1234xyz"]


def test_cedula_de_mas_de_20_caracteres_se_carga_sin_cedula_con_aviso(admin):
    r = subir(admin, xlsx([["9" * 30, "Juan Pérez", "6", "23", "Al día", ""]]), aplicar=True).json()
    assert r["resumen"]["filas_validas"] == 1 and any("demasiado larga" in a["motivo"] for a in r["avisos"])
    assert lista(admin)[0]["cedula"] is None


def test_mz_y_villa_hasta_50_caracteres(admin):
    largo = "A" * 50
    r = subir(admin, xlsx([["", "Juan", largo, largo, "Al día", ""]]), aplicar=True).json()
    assert r["resumen"]["filas_validas"] == 1
    assert lista(admin)[0]["casa"] == f"Mz {largo} Villa {largo}"
    r = subir(admin, xlsx([["", "Pedro", "A" * 51, "1", "Al día", ""]])).json()
    assert r["resumen"]["filas_con_error"] == 1 and "50 caracteres" in r["errores"][0]["motivo"]


def test_estado_de_pago_vacio_es_sin_cambio_y_casa_nueva_queda_al_dia(admin):
    registrar(admin, "0988391178", CASA, "Juan Pérez")
    admin.post("/api/casas/pago", json={"casa": CASA, "al_dia": False})
    filas = [["", "Juan Pérez", "6", "23", "", "0988391178"],     # estado vacío en una casa impaga: sigue impaga
             ["", "Ana Nueva", "7", "7", "", ""]]                 # casa nueva con estado vacío: queda al día
    r = subir(admin, xlsx(filas), aplicar=True).json()
    assert r["resumen"]["cambios_de_pago"] == 0
    estados = {x["casa"]: x["al_dia"] for x in lista(admin)}
    assert estados[CASA] is False and estados["Mz 7 Villa 7"] is True


def test_filas_con_error_solo_las_realmente_invalidas(admin):
    filas = [
        ["0912345678", "", "6", "23", "Al día", "0988391178"],                 # fila 2: sin nombre  -> ERROR
        ["0923456789", "Ana", "6", "24", "quizás", "0977777777"],              # fila 3: estado ilegible -> ERROR
        ["0934567890", "Beto", "6", "25", "Al día", "123"],                    # fila 4: celular malo -> válida con aviso
        ["0945678901", "Carla", "6", "26", "Al día", ""],                      # fila 5: sin celular -> válida
        ["0956789012", "Dora", "6", "27", "Al día", "0966666666"],             # fila 6: válida
        ["0956789012", "Dora Dos", "6", "28", "Al día", "0955555555"],         # fila 7: cédula repetida -> válida
        ["0989012345", "Gaby", "", "31", "Al día", "0944444444"],              # fila 8: sin manzana -> ERROR
    ]
    r = subir(admin, xlsx(filas), aplicar=True).json()
    assert r["resumen"]["filas_validas"] == 4 and r["errores_total"] == 3
    motivos = {e["fila"]: e["motivo"] for e in r["errores"]}
    assert set(motivos) == {2, 3, 8}
    assert "nombre" in motivos[2] and "no reconocido" in motivos[3] and "manzana" in motivos[8]
    assert {x["nombre"] for x in lista(admin)} == {"Beto", "Carla", "Dora", "Dora Dos"}


def test_columnas_obligatorias(admin):
    sin_pago = xlsx([["0912345678", "Juan", "6", "23", "0988391178"]], ["Cédula", "Nombre", "Mz", "Villa", "Celular"])
    r = subir(admin, sin_pago)
    assert r.status_code == 400 and "Estado de pago" in r.json()["detail"]
    sin_nombre = xlsx([["6", "23", "Al día"]], ["Mz", "Villa", "Estado de pago"])
    assert subir(admin, sin_nombre).status_code == 400
    # sin las columnas Cédula ni Celular sí se puede importar
    minimo = xlsx([["Juan", "6", "23", "Al día"]], ["Nombre", "Mz", "Villa", "Estado de pago"])
    r = subir(admin, minimo, aplicar=True)
    assert r.status_code == 200 and r.json()["resumen"]["filas_validas"] == 1 and r.json()["resumen"]["sin_celular"] == 1
    assert subir(admin, xlsx([["a", "b"]], ["Foo", "Bar"])).status_code == 400
    assert subir(admin, xlsx([], ENC)).status_code == 400


# ---------- volver a subir, completar y no pisar ----------
def test_volver_a_subir_el_mismo_archivo_no_duplica(admin):
    subir(admin, xlsx(FILAS), aplicar=True)
    r = subir(admin, xlsx(FILAS), aplicar=True).json()
    assert r["resumen"]["residentes_nuevos"] == 0 and r["resumen"]["residentes_actualizados"] == 3
    assert contar(db.residentes) == 3


def test_cambio_de_estado_de_pago_al_volver_a_subir(admin):
    subir(admin, xlsx(FILAS), aplicar=True)
    cambiado = [["0912345678", "Juan Pérez", "6", "23", "Impago", "0988391178"]] + FILAS[1:]
    r = subir(admin, xlsx(cambiado), aplicar=True).json()
    assert r["resumen"]["residentes_nuevos"] == 0
    assert {"casa": CASA, "de": "Al día", "a": "Impago"} in r["cambios_de_pago"]
    assert next(x for x in lista(admin) if x["nombre"] == "Juan Pérez")["al_dia"] is False


def test_la_misma_persona_que_cambia_de_casa_se_actualiza(admin):
    subir(admin, xlsx(FILAS), aplicar=True)
    r = subir(admin, xlsx([["", "Juan Pérez", "7", "1", "Al día", "0988391178"]]), aplicar=True).json()
    assert r["resumen"]["residentes_nuevos"] == 0 and r["resumen"]["residentes_actualizados"] == 1
    assert any("cambia de casa" in a["motivo"] for a in r["avisos"])
    assert contar(db.residentes) == 3
    assert next(x for x in lista(admin) if x["nombre"] == "Juan Pérez")["casa"] == "Mz 7 Villa 1"


def test_completar_el_celular_de_quien_se_cargo_sin_celular(admin):
    r = subir(admin, xlsx([["", "Ana Torres", "5", "3", "Al día", ""]]), aplicar=True).json()
    assert r["resumen"]["sin_celular"] == 1
    r = subir(admin, xlsx([["", "Ana Torres", "5", "3", "Al día", "0955555555"]]), aplicar=True).json()
    assert r["resumen"]["residentes_nuevos"] == 0 and r["resumen"]["residentes_actualizados"] == 1
    assert r["resumen"]["sin_celular"] == 0
    assert contar(db.residentes) == 1 and lista(admin)[0]["telefono"] == "593955555555"


def test_mismo_nombre_y_casa_con_otro_celular_se_agrega_como_persona_aparte(admin):
    subir(admin, xlsx([["", "Ana Torres", "5", "3", "Al día", "0955555555"]]), aplicar=True)
    r = subir(admin, xlsx([["", "Ana Torres", "5", "3", "Al día", "0944444444"]]), aplicar=True).json()
    assert r["resumen"]["residentes_nuevos"] == 1 and any("persona aparte" in a["motivo"] for a in r["avisos"])
    assert sorted(x["telefono"] for x in lista(admin)) == ["593944444444", "593955555555"]


def test_dos_personas_con_el_mismo_celular_nunca_se_pisan(admin):
    # una pareja que comparte un teléfono, en la misma casa y en el mismo archivo
    filas = [["", "Juan Pérez", "6", "23", "Al día", "0988391178"],
             ["", "María Gómez", "6", "23", "Al día", "0988391178"]]
    r = subir(admin, xlsx(filas), aplicar=True).json()
    assert r["resumen"]["residentes_nuevos"] == 2 and r["resumen"]["sin_celular"] == 1
    assert any("ya lo tiene" in a["motivo"] for a in r["avisos"])
    por_nombre = {x["nombre"]: x["telefono"] for x in lista(admin)}
    assert por_nombre == {"Juan Pérez": "593988391178", "María Gómez": None}      # la primera conserva su celular
    # y en una carga posterior, alguien con otro nombre y otra casa tampoco pisa a quien ya lo tiene
    subir(admin, xlsx([["", "Pedro Otro", "9", "9", "Al día", "0988391178"]]), aplicar=True)
    assert next(x for x in lista(admin) if x["nombre"] == "Juan Pérez")["telefono"] == "593988391178"
    assert contar(db.residentes) == 3


def test_las_casas_que_no_estan_en_el_archivo_no_se_tocan(admin):
    registrar(admin, "0911111111", "Mz 9 Villa 9", "Otro")
    admin.post("/api/casas/pago", json={"casa": "Mz 9 Villa 9", "al_dia": False})
    subir(admin, xlsx(FILAS), aplicar=True)
    otro = next(x for x in lista(admin) if x["casa"] == "Mz 9 Villa 9")
    assert otro["al_dia"] is False


def test_si_una_casa_trae_estados_distintos_queda_impaga(admin):
    filas = [["0912345678", "Juan Pérez", "6", "23", "Al día", "0988391178"],
             ["0923456789", "Ana Pérez", "6", "23", "Impago", "0977777777"]]
    r = subir(admin, xlsx(filas), aplicar=True).json()
    assert r["resumen"]["casas_impagas"] == 1 and any("IMPAGO" in a["motivo"] for a in r["avisos"])
    assert all(x["al_dia"] is False for x in lista(admin))


# ---------- formatos y seguridad ----------
def test_quirks_de_excel_cedula_y_celular_sin_cero_inicial(admin):
    # Excel guarda 0912345678 como número y le quita el cero; Mz y Villa también llegan como números
    filas = [[912345678, "Juan Pérez", 6, 23, "Al día", 988391178]]
    r = subir(admin, xlsx(filas), aplicar=True).json()
    assert r["resumen"]["filas_validas"] == 1
    x = lista(admin)[0]
    assert (x["cedula"], x["telefono"], x["casa"]) == ("0912345678", "593988391178", CASA)


def test_encabezados_con_otros_nombres_y_fila_de_titulo_previa(admin):
    enc = ["CI", "Residente", "Manzana", "Lote", "Status", "WhatsApp"]
    r = subir(admin, xlsx([["0912345678", "Juan Pérez", "Mz 6", "Villa 23", "SI", "0988391178"]], enc, previas=2), aplicar=True)
    assert r.status_code == 200 and r.json()["resumen"]["filas_validas"] == 1
    assert lista(admin)[0]["casa"] == CASA                  # quita los prefijos "Mz" y "Villa"


def test_csv_con_punto_y_coma_y_codificacion_latin1(admin):
    texto_csv = "Cédula;Nombre;Mz;Villa;Estado de pago;Celular\n0912345678;José Núñez;6;23;Al día;0988391178\n"
    r = subir(admin, texto_csv.encode("latin-1"), nombre="residentes.csv", aplicar=True)
    assert r.status_code == 200 and r.json()["resumen"]["filas_validas"] == 1
    assert lista(admin)[0]["nombre"] == "José Núñez"


def test_archivos_invalidos(admin):
    assert subir(admin, b"hola", nombre="residentes.pdf").status_code == 400
    assert subir(admin, b"", nombre="residentes.xlsx").status_code == 400
    assert subir(admin, b"no soy un excel", nombre="residentes.xlsx").status_code == 400
    assert subir(admin, b"x" * (5 * 1024 * 1024 + 1), nombre="grande.csv").status_code == 413


def test_solo_el_administrador_importa(guardia, anonimo):
    assert subir(guardia, xlsx(FILAS)).status_code == 403
    assert guardia.get("/api/residentes/plantilla.xlsx").status_code == 403
    assert anonimo.post("/api/residentes/importar", files={"archivo": ("a.xlsx", b"x")}).status_code == 401


def test_plantilla_descargable_y_se_puede_volver_a_subir(admin):
    r = admin.get("/api/residentes/plantilla.xlsx")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert [c.value for c in wb["Residentes"][1]] == ENC and "Instrucciones" in wb.sheetnames
    again = subir(admin, r.content)                          # la plantilla tal cual es un archivo válido
    assert again.status_code == 200 and again.json()["resumen"]["filas_validas"] == 2


# ---------- efecto sobre el resto del sistema ----------
def test_tras_importar_una_casa_impaga_queda_bloqueada(admin, wsp):
    subir(admin, xlsx(FILAS), aplicar=True)
    r = texto(admin, "593977777777", "Hoy viene Juan Pérez")                    # María Gómez: Mz 6 Villa 24, impaga
    assert r.get("bloqueado") is True
    assert texto(admin, "593988391178", "Hoy viene Juan Pérez")["registros"] == 1   # Juan: al día


def test_residente_sin_celular_se_lista_pero_no_recibe_avisos(admin, wsp):
    subir(admin, xlsx([["", "Sin cel", "3", "3", "Al día", ""]]), aplicar=True)
    x = lista(admin)[0]
    assert x["telefono"] is None
    from main import telefonos_de_casa
    assert telefonos_de_casa("Mz 3 Villa 3") == []
    assert admin.delete(f"/api/residentes/{x['id']}").status_code == 200
