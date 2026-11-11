"""Importación masiva de residentes desde Excel (.xlsx) o CSV. Solo ADMIN.

Columnas (en cualquier orden, con estos u otros nombres parecidos):
    Cédula · Nombre · Mz · Villa · Estado de pago · Celular
Obligatorias: Nombre, Mz, Villa y Estado de pago (la columna; una fila puede traerlo vacío = "sin cambio").
La Cédula y el Celular son opcionales y NO se validan: lo que no sirva se carga sin ese dato y queda un AVISO
(la persona se puede completar después, editándola en la pantalla de Residentes o subiendo el archivo otra vez).

Se trabaja en dos pasos: primero se REVISA el archivo (no guarda nada y muestra qué pasaría) y luego se
confirma. Solo agrega o actualiza: las casas que no aparecen en el archivo no se tocan.
"""
import csv
import io
import re
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import insert, select, update

from auth import solo_admin
from db import ahora_iso, asegurar_casa, casas, clave_casa, engine, fila, residentes
from reportes import _norm
from util import normalizar_telefono

router = APIRouter()

MAX_BYTES = 5 * 1024 * 1024
MAX_FILAS = 5000
MAX_MOSTRADOS = 200
MAX_MZ_VILLA = 50      # caracteres de la manzana y de la villa
MAX_NOMBRE = 200
MAX_CEDULA = 20
MAX_CELULAR = 20

SINONIMOS = {
    "cedula": {"cedula", "cedula de identidad", "ci", "identificacion", "documento", "cedula/ruc", "cedula ruc"},
    "nombre": {"nombre", "nombres", "residente", "nombre completo", "nombres y apellidos", "apellidos y nombres"},
    "mz": {"mz", "manzana", "mza"},
    "villa": {"villa", "lote", "casa", "solar"},
    "pago": {"estado de pago", "status de pago", "status", "estado", "pago", "situacion de pago", "situacion"},
    "celular": {"celular", "telefono", "movil", "whatsapp", "cel", "numero de celular", "celular whatsapp"},
}
OBLIGATORIAS = ("nombre", "mz", "villa", "pago")
NOMBRES_COLUMNA = {"cedula": "Cédula", "nombre": "Nombre", "mz": "Mz", "villa": "Villa",
                   "pago": "Estado de pago", "celular": "Celular"}

AL_DIA = {"si", "s", "1", "ok", "pagado", "pagada", "pago", "al dia", "aldia", "cancelado", "cancelada", "vigente"}
IMPAGO = {"no", "n", "0", "impago", "impaga", "pendiente", "moroso", "morosa", "mora", "deuda", "debe", "vencido", "atrasado"}


# ---------- lectura del archivo ----------
def _texto(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "si" if v else "no"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return " ".join(str(v).split())


def _clave_encabezado(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9/ ]+", " ", _norm(t)).split())


def _leer(nombre: Optional[str], contenido: bytes) -> List[List[str]]:
    n = (nombre or "").lower()
    if n.endswith((".xlsx", ".xlsm")):
        try:
            wb = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
            ws = wb.active
            filas, vacias = [], 0
            for row in ws.iter_rows(values_only=True):
                celdas = [_texto(c) for c in row]
                vacias = vacias + 1 if not any(celdas) else 0
                filas.append(celdas)
                if vacias > 50 or len(filas) > MAX_FILAS + 60:   # hojas con formato hasta la fila 1.000.000
                    break
            return filas
        except Exception:
            raise HTTPException(400, "No se pudo leer el archivo Excel. Guárdalo como .xlsx e inténtalo de nuevo.")
    if n.endswith(".csv"):
        try:
            txt = contenido.decode("utf-8-sig")
        except UnicodeDecodeError:
            txt = contenido.decode("latin-1")
        try:
            dialecto = csv.Sniffer().sniff(txt[:4096], delimiters=",;\t")
        except csv.Error:
            dialecto = csv.excel
        return [[_texto(c) for c in row] for row in csv.reader(io.StringIO(txt), dialecto)]
    raise HTTPException(400, "Formato no soportado: sube un archivo .xlsx o .csv")


def _extraer(filas: List[List[str]]) -> List[dict]:
    """Ubica la fila de encabezados y devuelve una lista de dicts con la columna canónica como clave."""
    inverso = {s: canon for canon, ss in SINONIMOS.items() for s in ss}
    for i, row in enumerate(filas[:15]):
        mapa = {}
        for j, celda in enumerate(row):
            canon = inverso.get(_clave_encabezado(celda))
            if canon and canon not in mapa.values():
                mapa[j] = canon
        if len(mapa) >= 3:
            faltan = [NOMBRES_COLUMNA[c] for c in OBLIGATORIAS if c not in mapa.values()]
            if faltan:
                raise HTTPException(400, f"Faltan columnas obligatorias: {', '.join(faltan)}. "
                                         f"Encontré: {', '.join(NOMBRES_COLUMNA[c] for c in mapa.values())}.")
            datos = []
            for k, row in enumerate(filas[i + 1:], start=i + 2):   # k = número de fila en el archivo (1 = primera)
                if not any(row):
                    continue
                d = {canon: (row[j] if j < len(row) else "") for j, canon in mapa.items()}
                d["fila"] = k
                datos.append(d)
            if len(datos) > MAX_FILAS:
                raise HTTPException(400, f"El archivo tiene más de {MAX_FILAS} filas. Divídelo en partes.")
            if not datos:
                raise HTTPException(400, "El archivo no tiene filas de datos debajo de los encabezados.")
            return datos
    raise HTTPException(400, "No encontré los encabezados. La primera fila debe tener: Nombre, Mz, Villa, "
                             "Estado de pago (y, si los tienes, Cédula y Celular). Descarga la plantilla para verlo.")


def _nom(nombre) -> str:
    """Nombre comparable: sin tildes, en minúsculas y con los espacios normalizados."""
    return " ".join(_norm(nombre).split())


# ---------- validación de cada fila ----------
def _quitar_prefijo(valor: str, prefijos: str) -> str:
    m = re.match(rf"^(?:{prefijos})\.?\s*(.+)$", valor, re.I)
    return (m.group(1) if m else valor).strip()


def _validar(d: dict) -> dict:
    """Devuelve los datos limpios más una lista de avisos, o {'error': 'motivo'}.
    Solo es error lo que impide registrar a la persona: falta el nombre, la manzana o la villa, o un estado de pago
    ilegible (leerlo mal podría bloquear a una casa que sí pagó). La cédula y el celular nunca bloquean una fila."""
    avisos: List[str] = []
    nombre = d.get("nombre", "").strip()
    mz = _quitar_prefijo(d.get("mz", ""), "mz|mza|manzana")
    villa = _quitar_prefijo(d.get("villa", ""), "villa|lote|casa|solar")
    if not nombre:
        return {"error": "Falta el nombre"}
    if not mz or not villa:
        return {"error": "Falta la manzana o la villa"}
    if len(mz) > MAX_MZ_VILLA or len(villa) > MAX_MZ_VILLA or len(nombre) > MAX_NOMBRE:
        return {"error": f"Manzana o villa de más de {MAX_MZ_VILLA} caracteres, o nombre de más de {MAX_NOMBRE}"}

    # cédula: sin validar. Solo se limpian espacios y guiones y se completa el cero que Excel quita
    cedula = d.get("cedula", "").replace(" ", "").replace("-", "")
    if re.fullmatch(r"\d{9}", cedula):
        cedula = "0" + cedula
    if len(cedula) > MAX_CEDULA:
        avisos.append(f"La cédula «{cedula[:30]}» es demasiado larga: se cargó sin cédula")
        cedula = ""

    # celular: opcional. Si no se puede usar, se carga a la persona sin celular y se avisa
    celular = None
    crudo = d.get("celular", "").strip()
    if crudo:
        normal = normalizar_telefono(crudo)
        if 11 <= len(normal) <= 15:
            celular = normal
        else:
            avisos.append(f"El celular «{crudo[:30]}» no es utilizable: se cargó sin celular")

    pago = None
    p = _norm(d.get("pago", "")).strip()
    if p:
        if p in AL_DIA:
            pago = True
        elif p in IMPAGO:
            pago = False
        else:
            return {"error": f"Estado de pago no reconocido ({d['pago']}). Use «Al día» o «Impago»"}
    return {"nombre": nombre, "cedula": cedula or None, "celular": celular, "pago": pago,
            "casa": f"Mz {mz} Villa {villa}", "avisos": avisos}


# ---------- proceso (sirve para revisar y para aplicar) ----------
def _procesar(datos: List[dict], usuario: str, aplicar: bool) -> dict:
    errores: List[dict] = []
    avisos: List[dict] = []
    nuevos = actualizados = casas_nuevas = 0
    cambios_pago: List[dict] = []

    conn = engine.connect()
    trans = conn.begin()
    try:
        existentes = [fila(r) for r in conn.execute(select(residentes)).all()]
        por_tel = {r["telefono"]: r for r in existentes if r["telefono"]}
        por_nombre_casa = {(_nom(r["nombre"]), clave_casa(r["casa"])): r for r in existentes if r["nombre"]}
        pagos = {c.clave: bool(c.al_dia) for c in conn.execute(select(casas)).all()}
        pago_casa: Dict[str, dict] = {}
        tocados: Dict[int, dict] = {}     # personas de este archivo (para contar las que quedan sin celular)
        validas = 0

        for d in datos:
            n = d["fila"]
            v = _validar(d)
            if "error" in v:
                errores.append({"fila": n, "motivo": v["error"]})
                continue
            for a in v["avisos"]:
                avisos.append({"fila": n, "motivo": a})

            # ¿ya existe? El celular identifica a la persona; si no hay celular se usa nombre + casa.
            # La cédula NO identifica a nadie (puede repetirse).
            nombre_n, casa_n = _nom(v["nombre"]), clave_casa(v["casa"])
            actual = None
            if v["celular"] and v["celular"] in por_tel:
                dueno = por_tel[v["celular"]]
                if _nom(dueno["nombre"]) == nombre_n:
                    actual = dueno            # misma persona (aunque haya cambiado de casa)
                else:                          # otro nombre: puede ser alguien que comparte el celular. Nunca se pisa a la anterior
                    avisos.append({"fila": n, "motivo": f"El celular +{v['celular']} ya lo tiene {dueno['nombre'] or 'otra persona'} "
                                                       f"({dueno['casa']}): {v['nombre']} se cargó sin celular"})
                    v["celular"] = None
            if actual is None:
                candidato = por_nombre_casa.get((nombre_n, casa_n))
                if candidato is not None:
                    if not v["celular"] or not candidato["telefono"] or candidato["telefono"] == v["celular"]:
                        actual = candidato     # misma persona: se le completa o confirma el celular
                    else:
                        avisos.append({"fila": n, "motivo": f"{v['nombre']} ya existe en {v['casa']} con otro celular "
                                                           f"(+{candidato['telefono']}): se agregó como persona aparte"})

            validas += 1
            if actual is None:
                nuevo_id = conn.execute(insert(residentes).values(
                    telefono=v["celular"], cedula=v["cedula"], nombre=v["nombre"], casa=v["casa"])).inserted_primary_key[0]
                actual = {"id": nuevo_id, "telefono": v["celular"], "cedula": v["cedula"], "nombre": v["nombre"], "casa": v["casa"]}
                nuevos += 1
            else:
                if clave_casa(actual["casa"]) != casa_n:
                    avisos.append({"fila": n, "motivo": f"{v['nombre']} cambia de casa: {actual['casa']} → {v['casa']}"})
                cambios = {"nombre": v["nombre"], "casa": v["casa"]}
                if v["cedula"]:
                    cambios["cedula"] = v["cedula"]
                if v["celular"] and not actual["telefono"]:
                    cambios["telefono"] = v["celular"]
                conn.execute(update(residentes).where(residentes.c.id == actual["id"]).values(**cambios))
                actual.update(cambios)
                actualizados += 1
            if actual["telefono"]:
                por_tel[actual["telefono"]] = actual
            por_nombre_casa[(_nom(actual["nombre"]), clave_casa(actual["casa"]))] = actual
            tocados[actual["id"]] = actual

            # estado de pago: se junta por casa
            clave = clave_casa(v["casa"])
            g = pago_casa.setdefault(clave, {"casa": v["casa"], "valores": set(), "filas": []})
            g["filas"].append(n)
            if v["pago"] is not None:
                g["valores"].add(v["pago"])

        # estado de pago por casa: si en alguna fila dice impago, la casa queda impaga
        al_dia_final = impago_final = 0
        ahora = ahora_iso()
        for clave, g in pago_casa.items():
            if clave not in pagos:
                asegurar_casa(conn, g["casa"])
                pagos[clave] = True
                casas_nuevas += 1
            if len(g["valores"]) == 2:
                avisos.append({"fila": g["filas"][0], "motivo": f"{g['casa']}: las filas {', '.join(map(str, g['filas']))} "
                                                                 "traen estados distintos; se dejó como IMPAGO"})
            final = False if False in g["valores"] else (True if True in g["valores"] else None)
            if final is not None and final != pagos[clave]:
                conn.execute(update(casas).where(casas.c.clave == clave).values(
                    al_dia=final, actualizado_por=usuario, actualizado_en=ahora))
                cambios_pago.append({"casa": g["casa"], "de": "Al día" if pagos[clave] else "Impago",
                                     "a": "Al día" if final else "Impago"})
                pagos[clave] = final
            if pagos[clave]:
                al_dia_final += 1
            else:
                impago_final += 1

        sin_celular = sum(1 for r in tocados.values() if not r["telefono"])
        if aplicar:
            trans.commit()
        else:
            trans.rollback()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()

    return {
        "aplicado": aplicar,
        "resumen": {
            "filas_leidas": len(datos), "filas_validas": validas, "filas_con_error": len(errores),
            "residentes_nuevos": nuevos, "residentes_actualizados": actualizados, "sin_celular": sin_celular,
            "casas_en_archivo": len(pago_casa), "casas_nuevas": casas_nuevas,
            "casas_al_dia": al_dia_final, "casas_impagas": impago_final, "cambios_de_pago": len(cambios_pago),
        },
        "cambios_de_pago": cambios_pago[:MAX_MOSTRADOS],
        "errores": errores[:MAX_MOSTRADOS],
        "avisos": avisos[:MAX_MOSTRADOS],
        "errores_total": len(errores),
    }


# ---------- endpoints ----------
@router.post("/api/residentes/importar")
async def importar_residentes(archivo: UploadFile = File(...), aplicar: bool = Query(False),
                              u: dict = Depends(solo_admin)):
    """aplicar=false: solo revisa y muestra qué pasaría. aplicar=true: guarda las filas válidas."""
    contenido = await archivo.read()
    if not contenido:
        raise HTTPException(400, "El archivo está vacío")
    if len(contenido) > MAX_BYTES:
        raise HTTPException(413, "El archivo supera los 5 MB")
    datos = _extraer(_leer(archivo.filename, contenido))
    return _procesar(datos, u["usuario"], aplicar)


@router.get("/api/residentes/plantilla.xlsx")
def plantilla(_: dict = Depends(solo_admin)):
    wb = Workbook()
    ws = wb.active
    ws.title = "Residentes"
    ws.append(["Cédula", "Nombre", "Mz", "Villa", "Estado de pago", "Celular"])
    ws.append(["0912345678", "Juan Pérez (ejemplo)", "6", "23", "Al día", "0988391178"])
    ws.append(["", "María Gómez (ejemplo)", "6", "24", "Impago", ""])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1E293B")
    for col, ancho in zip("ABCDEF", (16, 30, 8, 8, 16, 16)):
        ws.column_dimensions[col].width = ancho
    for fila_ in ws.iter_rows(min_row=2, max_row=500):   # texto, para que Excel no quite el cero inicial
        fila_[0].number_format = "@"
        fila_[5].number_format = "@"
    ws.freeze_panes = "A2"
    ayuda = wb.create_sheet("Instrucciones")
    for linea in (
        "Una fila por persona. Borra las filas de ejemplo antes de subir el archivo.",
        "Obligatorias: las columnas Nombre, Mz, Villa y Estado de pago. Mz y Villa admiten hasta 50 caracteres.",
        "Estado de pago: «Al día» o «Impago» (también se entiende Sí/No, Pagado, Pendiente, Moroso). Vacío = sin cambio.",
        "Cédula y Celular son opcionales y no se validan. Si un celular no sirve, la persona se carga sin celular y se te avisa.",
        "Celular: el WhatsApp con el que la persona escribirá al sistema. Formato 0988391178 (también sirve 593988391178).",
        "Sin celular la persona queda registrada, pero no podrá usar WhatsApp hasta que se lo agregues (se edita en la pantalla Residentes).",
        "Si una casa tiene varias personas con estados distintos, la casa queda como IMPAGA.",
        "Volver a subir el archivo no duplica: se reconoce a la persona por celular (con el mismo nombre) o por nombre + casa, y se le completan los datos.",
        "Si dos personas distintas traen el mismo celular, el celular se queda con la primera y la otra se carga sin celular (se te avisa).",
        "Las casas que no aparecen en el archivo no se modifican.",
    ):
        ayuda.append([linea])
    ayuda.column_dimensions["A"].width = 120
    buf = io.BytesIO()
    wb.save(buf)
    return Response(content=buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="plantilla_residentes.xlsx"'})
