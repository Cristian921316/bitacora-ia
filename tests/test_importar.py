"""Importación de residentes desde Excel/CSV."""
import io

import openpyxl
from sqlalchemy import func, select

import db
from conftest import registrar, texto

ENC = ["Cédula", "Nombre", "Mz", "Villa", "Estado de pago", "Celular"]


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


FILAS = [
    ["0912345678", "Juan Pérez", "6", "23", "Al día", "0988391178"],
    ["0923456789", "María Gómez", "6", "24", "Impago", "0977777777"],
    ["0934567890", "Pedro Ruiz", "2", "5", "Pagado", "0966666666"],
]


def test_revisar_no_guarda_y_confirmar_si(admin):
    r = subir(admin, xlsx(FILAS))
    assert r.status_code == 200 and r.json()["aplicado"] is False
    res = r.json()["resumen"]
    assert (res["filas_validas"], res["residentes_nuevos"], res["casas_nuevas"]) == (3, 3, 3)
    assert (res["casas_al_dia"], res["casas_impagas"]) == (2, 1)
    assert contar(db.residentes) == 0 and contar(db.casas) == 0       # revisar no deja nada

    r = subir(admin, xlsx(FILAS), aplicar=True)
    assert r.json()["aplicado"] is True and contar(db.residentes) == 3
    lista = {x["nombre"]: x for x in admin.get("/api/residentes").json()}
    assert lista["Juan Pérez"]["cedula"] == "0912345678" and lista["Juan Pérez"]["telefono"] == "593988391178"
    assert lista["Juan Pérez"]["casa"] == "Mz 6 Villa 23" and lista["Juan Pérez"]["al_dia"] is True
    assert lista["María Gómez"]["al_dia"] is False and lista["María Gómez"]["pago_actualizado_por"] == "admin"
    assert lista["Pedro Ruiz"]["al_dia"] is True


def test_volver_a_subir_actualiza_sin_duplicar(admin):
    subir(admin, xlsx(FILAS), aplicar=True)
    cambiado = [["0912345678", "Juan A. Pérez", "7", "1", "Impago", "0988391178"]] + FILAS[1:]
    r = subir(admin, xlsx(cambiado), aplicar=True).json()
    assert r["resumen"]["residentes_nuevos"] == 0 and r["resumen"]["residentes_actualizados"] == 3
    assert contar(db.residentes) == 3
    juan = next(x for x in admin.get("/api/residentes").json() if x["cedula"] == "0912345678")
    assert juan["nombre"] == "Juan A. Pérez" and juan["casa"] == "Mz 7 Villa 1" and juan["al_dia"] is False
    assert any("cambia de casa" in a["motivo"] for a in r["avisos"])
    assert {"casa": "Mz 7 Villa 1", "de": "Al día", "a": "Impago"} in r["cambios_de_pago"] or r["resumen"]["casas_nuevas"] == 1


def test_cambio_de_celular_por_cedula(admin):
    subir(admin, xlsx(FILAS), aplicar=True)
    nuevo = [["0912345678", "Juan Pérez", "6", "23", "Al día", "0955555555"]]
    r = subir(admin, xlsx(nuevo), aplicar=True).json()
    assert any("cambia de celular" in a["motivo"] for a in r["avisos"])
    assert contar(db.residentes) == 3
    juan = next(x for x in admin.get("/api/residentes").json() if x["cedula"] == "0912345678")
    assert juan["telefono"] == "593955555555"


def test_las_casas_que_no_estan_en_el_archivo_no_se_tocan(admin):
    registrar(admin, "0911111111", "Mz 9 Villa 9", "Otro")
    admin.post("/api/casas/pago", json={"casa": "Mz 9 Villa 9", "al_dia": False})
    subir(admin, xlsx(FILAS), aplicar=True)
    otro = next(x for x in admin.get("/api/residentes").json() if x["casa"] == "Mz 9 Villa 9")
    assert otro["al_dia"] is False


def test_si_una_casa_trae_estados_distintos_queda_impaga(admin):
    filas = [["0912345678", "Juan Pérez", "6", "23", "Al día", "0988391178"],
             ["0923456789", "Ana Pérez", "6", "23", "Impago", "0977777777"]]
    r = subir(admin, xlsx(filas), aplicar=True).json()
    assert r["resumen"]["casas_impagas"] == 1 and any("IMPAGO" in a["motivo"] for a in r["avisos"])
    assert all(x["al_dia"] is False for x in admin.get("/api/residentes").json())


def test_quirks_de_excel_cedula_y_celular_sin_cero_inicial(admin):
    # Excel guarda 0912345678 como número y le quita el cero; Mz y Villa también llegan como números
    filas = [[912345678, "Juan Pérez", 6, 23, "Al día", 988391178]]
    r = subir(admin, xlsx(filas), aplicar=True).json()
    assert r["resumen"]["filas_validas"] == 1
    x = admin.get("/api/residentes").json()[0]
    assert (x["cedula"], x["telefono"], x["casa"]) == ("0912345678", "593988391178", "Mz 6 Villa 23")


def test_encabezados_con_otros_nombres_y_fila_de_titulo_previa(admin):
    enc = ["CI", "Residente", "Manzana", "Lote", "Status", "WhatsApp"]
    r = subir(admin, xlsx([["0912345678", "Juan Pérez", "Mz 6", "Villa 23", "SI", "0988391178"]], enc, previas=2), aplicar=True)
    assert r.status_code == 200 and r.json()["resumen"]["filas_validas"] == 1
    assert admin.get("/api/residentes").json()[0]["casa"] == "Mz 6 Villa 23"      # quita los prefijos "Mz" y "Villa"


def test_filas_con_error_se_omiten_y_se_explican(admin):
    with db.engine.begin() as conn:      # un residente ya registrado, con su cédula
        conn.execute(db.residentes.insert().values(telefono="593900000001", cedula="0900000099", nombre="Existente", casa="Mz 1 Villa 1"))
    filas = [
        ["0912345678", "", "6", "23", "Al día", "0988391178"],                        # fila 2: sin nombre
        ["0923456789", "Ana", "6", "24", "quizás", "0977777777"],                     # fila 3: estado raro
        ["0934567890", "Beto", "6", "25", "Al día", "123"],                           # fila 4: celular malo
        ["0945678901", "Carla", "6", "26", "Al día", ""],                             # fila 5: sin celular
        ["0956789012", "Dora", "6", "27", "Al día", "0966666666"],                    # fila 6: válida
        ["0956789012", "Dora 2", "6", "28", "Al día", "0955555555"],                  # fila 7: cédula repetida
        ["0967890123", "Elena", "6", "29", "Al día", "0966666666"],                   # fila 8: celular repetido
        ["0978901234", "Fabi", "6", "30", "Al día", "0900000001"],                    # fila 9: celular de otro residente
        ["0989012345", "Gaby", "", "31", "Al día", "0944444444"],                     # fila 10: sin manzana
    ]
    r = subir(admin, xlsx(filas), aplicar=True).json()
    assert r["resumen"]["filas_validas"] == 1 and r["errores_total"] == 8
    motivos = {e["fila"]: e["motivo"] for e in r["errores"]}
    assert "nombre" in motivos[2] and "no reconocido" in motivos[3] and "no válido" in motivos[4]
    assert "Falta el celular" in motivos[5] and "repetida" in motivos[7] and "repetido" in motivos[8]
    assert "otro residente" in motivos[9] and "manzana" in motivos[10]
    nombres = {x["nombre"] for x in admin.get("/api/residentes").json()}
    assert nombres == {"Existente", "Dora"}                                         # solo la fila válida se guardó


def test_columnas_obligatorias_faltantes(admin):
    sin_celular = xlsx([["0912345678", "Juan", "6", "23", "Al día"]], ["Cédula", "Nombre", "Mz", "Villa", "Estado de pago"])
    r = subir(admin, sin_celular)
    assert r.status_code == 400 and "Celular" in r.json()["detail"]
    sin_nada = xlsx([["a", "b"]], ["Foo", "Bar"])
    assert subir(admin, sin_nada).status_code == 400
    vacio = xlsx([], ENC)
    assert subir(admin, vacio).status_code == 400


def test_csv_con_punto_y_coma_y_codificacion_latin1(admin):
    texto_csv = "Cédula;Nombre;Mz;Villa;Estado de pago;Celular\n0912345678;José Núñez;6;23;Al día;0988391178\n"
    r = subir(admin, texto_csv.encode("latin-1"), nombre="residentes.csv", aplicar=True)
    assert r.status_code == 200 and r.json()["resumen"]["filas_validas"] == 1
    assert admin.get("/api/residentes").json()[0]["nombre"] == "José Núñez"


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


def test_tras_importar_una_casa_impaga_queda_bloqueada(admin, wsp):
    subir(admin, xlsx(FILAS), aplicar=True)
    r = texto(admin, "593977777777", "Hoy viene Juan Pérez")                    # María Gómez: Mz 6 Villa 24, impaga
    assert r.get("bloqueado") is True
    assert texto(admin, "593988391178", "Hoy viene Juan Pérez")["registros"] == 1   # Juan: al día


def test_residente_sin_celular_se_lista_pero_no_recibe_avisos(admin, wsp):
    with db.engine.begin() as conn:
        conn.execute(db.residentes.insert().values(nombre="Sin cel", casa="Mz 3 Villa 3", cedula="0900000009"))
    x = admin.get("/api/residentes").json()[0]
    assert x["telefono"] is None and x["cedula"] == "0900000009"
    from main import telefonos_de_casa
    assert telefonos_de_casa("Mz 3 Villa 3") == []
    assert admin.delete(f"/api/residentes/{x['id']}").status_code == 200
