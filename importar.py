"""Importación masiva de residentes desde Excel (.xlsx) o CSV. Solo ADMIN.

Columnas (en cualquier orden, con estos u otros nombres parecidos):
    Cédula · Nombre · Mz · Villa · Estado de pago · Celular
Obligatorias: Nombre, Mz, Villa y Celular (el celular es lo que une a la persona con WhatsApp).

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

SINONIMOS = {
    "cedula": {"cedula", "cedula de identidad", "ci", "identificacion", "documento", "cedula/ruc", "cedula ruc"},
    "nombre": {"nombre", "nombres", "residente", "nombre completo", "nombres y apellidos", "apellidos y nombres"},
    "mz": {"mz", "manzana", "mza"},
    "villa": {"villa", "lote", "casa", "solar"},
    "pago": {"estado de pago", "status de pago", "status", "estado", "pago", "situacion de pago", "situacion"},
    "celular": {"celular", "telefono", "movil", "whatsapp", "cel", "numero de celular", "celular whatsapp"},
}
OBLIGATORIAS = ("nombre", "mz", "villa", "celular")
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
    raise HTTPException(400, "No encontré los encabezados. La primera fila debe tener: Cédula, Nombre, Mz, Villa, "
                             "Estado de pago, Celular. Descarga la plantilla para verlo.")


# ---------- validación de cada fila ----------
def _quitar_prefijo(valor: str, prefijos: str) -> str:
    m = re.match(rf"^(?:{prefijos})\.?\s*(.+)$", valor, re.I)
    return (m.group(1) if m else valor).strip()


def _validar(d: dict) -> dict:
    """Devuelve los datos limpios, o {'error': 'motivo'}."""
    nombre = d.get("nombre", "").strip()
    mz = _quitar_prefijo(d.get("mz", ""), "mz|mza|manzana")
    villa = _quitar_prefijo(d.get("villa", ""), "villa|lote|casa|solar")
    if not nombre:
        return {"error": "Falta el nombre"}
    if not mz or not villa:
        return {"error": "Falta la manzana o la villa"}
    if len(mz) > 10 or len(villa) > 10 or len(nombre) > 120:
        return {"error": "Manzana, villa o nombre demasiado largos"}
    cedula = d.get("cedula", "").replace(" ", "").replace("-", "")
    if cedula:
        if re.fullmatch(r"\d{9}", cedula):   # Excel quita el cero inicial de las cédulas
            cedula = "0" + cedula
        if not re.fullmatch(r"[A-Za-z0-9]{5,20}", cedula):
            return {"error": f"Cédula no válida ({d.get('cedula')})"}
    if not d.get("celular", "").strip():
        return {"error": "Falta el celular"}
    celular = normalizar_telefono(d["celular"])
    if not 11 <= len(celular) <= 15:
        return {"error": f"Celular no válido ({d['celular']}). Use el formato 0988391178"}
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
            "casa": f"Mz {mz} Villa {villa}"}


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
        por_ced = {r["cedula"]: r for r in existentes if r["cedula"]}
        por_tel = {r["telefono"]: r for r in existentes if r["telefono"]}
        por_nombre_casa = {(_norm(r["nombre"]), clave_casa(r["casa"])): r for r in existentes if r["nombre"]}
        pagos = {c.clave: bool(c.al_dia) for c in conn.execute(select(casas)).all()}
        ced_vistas: Dict[str, int] = {}
        tel_vistos: Dict[str, int] = {}
        pago_casa: Dict[str, dict] = {}
        validas = 0

        for d in datos:
            n = d["fila"]
            v = _validar(d)
            if "error" in v:
                errores.append({"fila": n, "motivo": v["error"]})
                continue
            if v["cedula"] and v["cedula"] in ced_vistas:
                errores.append({"fila": n, "motivo": f"La cédula {v['cedula']} está repetida (ya aparece en la fila {ced_vistas[v['cedula']]})"})
                continue
            if v["celular"] and v["celular"] in tel_vistos:
                errores.append({"fila": n, "motivo": f"El celular {v['celular']} está repetido (ya aparece en la fila {tel_vistos[v['celular']]})"})
                continue

            # ¿ya existe? Por cédula, luego por celular, y si no trae ninguno, por nombre + casa
            actual = None
            if v["cedula"] and v["cedula"] in por_ced:
                actual = por_ced[v["cedula"]]
            elif v["celular"] and v["celular"] in por_tel:
                actual = por_tel[v["celular"]]
            elif not v["cedula"] and not v["celular"]:
                actual = por_nombre_casa.get((_norm(v["nombre"]), clave_casa(v["casa"])))

            # el mismo celular con otra cédula = otra persona, no se puede fusionar
            if actual is not None and v["cedula"] and actual["cedula"] and actual["cedula"] != v["cedula"]:
                errores.append({"fila": n, "motivo": f"El celular ya pertenece a otro residente con otra cédula ({actual['nombre'] or 'sin nombre'}, {actual['casa']})"})
                continue

            # conflictos con OTRA persona ya registrada
            otro_tel = por_tel.get(v["celular"]) if v["celular"] else None
            otra_ced = por_ced.get(v["cedula"]) if v["cedula"] else None
            if (otro_tel is not None and otro_tel is not actual) or (otra_ced is not None and otra_ced is not actual):
                dueno = (otro_tel or otra_ced)
                errores.append({"fila": n, "motivo": f"El celular o la cédula ya pertenecen a otro residente ({dueno['nombre'] or 'sin nombre'}, {dueno['casa']})"})
                continue

            validas += 1
            if v["cedula"]:
                ced_vistas[v["cedula"]] = n
            if v["celular"]:
                tel_vistos[v["celular"]] = n

            if actual is None:
                nuevo_id = conn.execute(insert(residentes).values(
                    telefono=v["celular"], cedula=v["cedula"], nombre=v["nombre"], casa=v["casa"])).inserted_primary_key[0]
                actual = {"id": nuevo_id, "telefono": v["celular"], "cedula": v["cedula"], "nombre": v["nombre"], "casa": v["casa"]}
                nuevos += 1
            else:
                if clave_casa(actual["casa"]) != clave_casa(v["casa"]):
                    avisos.append({"fila": n, "motivo": f"{v['nombre']} cambia de casa: {actual['casa']} → {v['casa']}"})
                if v["celular"] and actual["telefono"] and actual["telefono"] != v["celular"]:
                    avisos.append({"fila": n, "motivo": f"{v['nombre']} cambia de celular: +{actual['telefono']} → +{v['celular']}"})
                cambios = {"nombre": v["nombre"], "casa": v["casa"]}
                if v["cedula"]:
                    cambios["cedula"] = v["cedula"]
                if v["celular"]:
                    cambios["telefono"] = v["celular"]
                conn.execute(update(residentes).where(residentes.c.id == actual["id"]).values(**cambios))
                for k in ("cedula", "telefono"):   # mantiene los índices al día
                    if actual.get(k) and actual[k] != cambios.get(k, actual[k]):
                        (por_ced if k == "cedula" else por_tel).pop(actual[k], None)
                actual.update(cambios)
                actualizados += 1
            if actual["cedula"]:
                por_ced[actual["cedula"]] = actual
            if actual["telefono"]:
                por_tel[actual["telefono"]] = actual
            por_nombre_casa[(_norm(actual["nombre"]), clave_casa(actual["casa"]))] = actual

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
            "residentes_nuevos": nuevos, "residentes_actualizados": actualizados,
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
    ws.append(["0923456789", "María Gómez (ejemplo)", "6", "24", "Impago", "0977777777"])
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
        "Columnas obligatorias: Nombre, Mz, Villa y Celular. Recomendadas: Cédula y Estado de pago.",
        "Estado de pago: «Al día» o «Impago» (también se entiende Sí/No, Pagado, Pendiente, Moroso).",
        "Celular: el WhatsApp con el que la persona escribirá al sistema. Formato 0988391178 (también sirve 593988391178).",
        "Si una casa tiene varias personas con estados distintos, la casa queda como IMPAGA.",
        "Volver a subir el archivo ACTUALIZA a las personas (se reconocen por cédula o celular); no las duplica.",
        "Las casas que no aparecen en el archivo no se modifican.",
    ):
        ayuda.append([linea])
    ayuda.column_dimensions["A"].width = 110
    buf = io.BytesIO()
    wb.save(buf)
    return Response(content=buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="plantilla_residentes.xlsx"'})
