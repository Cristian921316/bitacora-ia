"""Reportes de la bitácora (solo ADMIN): pantalla, CSV y Excel."""
import csv
import io
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select

import parametros
from auth import solo_admin
from db import accesos, engine, fila, intentos_bloqueados, movimientos

router = APIRouter(dependencies=[Depends(solo_admin)])


# ---------- búsqueda (misma lógica que la garita: entiende "mz 6 villa 23", "mz6 v23", "6 23") ----------
def _norm(s) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def _parse_casa(t: str) -> dict:
    out = {"mz": None, "vi": None}

    def set_mz(m):
        out["mz"] = m.group(1)
        return " "

    def set_vi(m):
        out["vi"] = m.group(1)
        return " "

    resto = re.sub(r"\b(?:mz|mza|manzana)\.?(?:\s+|(?=\d))([a-z0-9]+)", set_mz, t, count=1)
    resto = re.sub(r"\b(?:villa|casa|lote)\.?\s*([a-z0-9]+)", set_vi, resto, count=1)
    if out["vi"] is None:
        resto = re.sub(r"\bv\.?\s*(\d+)\b", set_vi, resto, count=1)
    resto = resto.strip()
    nums = re.match(r"^(\d+)\s+(\d+)$", resto)
    if out["mz"] is None and out["vi"] is None and nums:
        out["mz"], out["vi"], resto = nums.group(1), nums.group(2), ""
    out["resto"] = resto
    return out


def _filtrar_texto(filas: list, q: str) -> list:
    q = _norm(q).strip()
    if not q:
        return filas
    c = _parse_casa(q)
    tokens = c["resto"].split()
    salida = []
    for f in filas:
        casa_n = _norm(f.get("casa"))
        rc = _parse_casa(casa_n)
        rmz, rvi = rc["mz"], rc["vi"]
        if rmz is None and rvi is None and re.fullmatch(r"[a-z0-9]+", casa_n or ""):
            rvi = casa_n
        if c["mz"] is not None and rmz != c["mz"]:
            continue
        if c["vi"] is not None and rvi != c["vi"]:
            continue
        blob = _norm(" ".join(str(v or "") for v in f.values()))
        if all(t in blob for t in tokens):
            salida.append(f)
    return salida


# ---------- consulta ----------
def _rango(desde: Optional[str], hasta: Optional[str]):
    hoy = datetime.now().date()
    try:
        d = date.fromisoformat(desde) if desde else hoy
        h = date.fromisoformat(hasta) if hasta else hoy
    except ValueError:
        raise HTTPException(400, "Fecha inválida (use AAAA-MM-DD)")
    # rango por texto ISO: funciona igual en SQLite y PostgreSQL
    return f"{d.isoformat()}T00:00:00", f"{(h + timedelta(days=1)).isoformat()}T00:00:00"


def _minutos(ini: Optional[str], fin: Optional[str]) -> Optional[int]:
    try:
        return round((datetime.fromisoformat(fin) - datetime.fromisoformat(ini)).total_seconds() / 60)
    except Exception:
        return None


def consultar_reporte(vista: str, desde: Optional[str], hasta: Optional[str],
                      estado: Optional[str], q: Optional[str]) -> List[dict]:
    ini, fin = _rango(desde, hasta)
    m, a, ib = movimientos, accesos, intentos_bloqueados
    limite = parametros.limite_vigencia()
    if vista == "visitas":
        stmt = select(a).where(a.c.fecha_hora >= ini, a.c.fecha_hora < fin)
        if estado:
            e = estado.upper()
            if e == "VENCIDA":   # sin ingresar y fuera de vigencia (se calcula, no se guarda)
                stmt = stmt.where(a.c.estado.in_(parametros.ESTADOS_CON_VIGENCIA), a.c.fecha_hora < limite)
            elif e in parametros.ESTADOS_CON_VIGENCIA:
                stmt = stmt.where(a.c.estado == e, a.c.fecha_hora >= limite)
            else:
                stmt = stmt.where(a.c.estado == e)
        stmt = stmt.order_by(a.c.id.desc())
    elif vista == "movimientos":
        stmt = (select(m.c.id, m.c.fecha_hora, m.c.evento, a.c.casa, a.c.nombre_visitante, a.c.tipo, a.c.placa,
                       a.c.observaciones, a.c.autorizado_por, m.c.origen, m.c.usuario, m.c.acceso_id)
                .select_from(m.join(a, a.c.id == m.c.acceso_id))
                .where(m.c.fecha_hora >= ini, m.c.fecha_hora < fin))
        if estado:
            stmt = stmt.where(m.c.evento == estado.upper())
        stmt = stmt.order_by(m.c.id.desc())
    elif vista == "bloqueos":
        stmt = select(ib).where(ib.c.fecha_hora >= ini, ib.c.fecha_hora < fin).order_by(ib.c.id.desc())
    else:
        raise HTTPException(400, "vista debe ser 'visitas', 'movimientos' o 'bloqueos'")
    with engine.connect() as conn:
        filas = [fila(r) for r in conn.execute(stmt).all()]
    if vista == "visitas":
        for f in filas:
            f["estado"] = parametros.estado_efectivo(f, limite)
            if f["estado"] == "ADENTRO" and not parametros.valor("control_salidas"):
                f["estado"] = "INGRESÓ"   # sin control de salidas no sabemos si ya se fue
            f["permanencia_min"] = _minutos(f.get("hora_ingreso"), f.get("hora_salida"))
    return _filtrar_texto(filas, q or "")


@router.get("/api/reporte")
def api_reporte(vista: str = "visitas", desde: Optional[str] = None, hasta: Optional[str] = None,
                estado: Optional[str] = None, q: Optional[str] = None, limite: int = 500):
    filas = consultar_reporte(vista, desde, hasta, estado, q)
    return {"vista": vista, "total": len(filas), "filas": filas[: max(1, min(limite, 5000))]}


COLUMNAS = {
    "visitas": [
        ("id", "ID"), ("fecha_hora", "Fecha y hora de registro"), ("casa", "Casa"),
        ("nombre_visitante", "Visitante"), ("tipo", "Tipo"), ("placa", "Placa"),
        ("observaciones", "Observaciones"), ("estado", "Estado actual"),
        ("hora_ingreso", "Hora de ingreso"), ("hora_salida", "Hora de salida"),
        ("permanencia_min", "Permanencia (min)"), ("autorizado_por", "Autorizado por"),
        ("hora_respuesta", "Hora de respuesta del residente"), ("respondido_por", "Respondió (teléfono)"),
        ("origen", "Origen"), ("telefono", "Teléfono"), ("transcripcion", "Petición (texto del audio)"),
    ],
    "movimientos": [
        ("id", "ID movimiento"), ("fecha_hora", "Fecha y hora"), ("evento", "Evento"),
        ("casa", "Casa"), ("nombre_visitante", "Visitante"), ("tipo", "Tipo"), ("placa", "Placa"),
        ("observaciones", "Observaciones"), ("autorizado_por", "Autorizado por"),
        ("origen", "Origen"), ("usuario", "Usuario"), ("acceso_id", "ID visita"),
    ],
    "bloqueos": [
        ("id", "ID"), ("fecha_hora", "Fecha y hora"), ("casa", "Casa"), ("origen", "Origen"),
        ("telefono", "Teléfono"), ("usuario", "Usuario"), ("detalle", "Detalle del intento"), ("motivo", "Motivo"),
    ],
}
HOJAS = {"visitas": "Visitas", "movimientos": "Movimientos", "bloqueos": "Bloqueos"}
COLUMNAS_FECHA = {"fecha_hora", "hora_ingreso", "hora_salida", "hora_respuesta"}
ANCHOS = {"casa": 18, "nombre_visitante": 26, "observaciones": 30, "transcripcion": 60,
          "autorizado_por": 28, "detalle": 50}


def _celda_segura(v) -> str:
    # Evita inyección de fórmulas al abrir el CSV en Excel (el texto viene de audios de terceros)
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/api/reporte.csv")
def api_reporte_csv(vista: str = "visitas", desde: Optional[str] = None, hasta: Optional[str] = None,
                    estado: Optional[str] = None, q: Optional[str] = None):
    filas = consultar_reporte(vista, desde, hasta, estado, q)
    cols = COLUMNAS[vista]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([titulo for _, titulo in cols])
    for f in filas:
        w.writerow([_celda_segura(f.get(k)) for k, _ in cols])
    nombre = f"bitacora_{vista}_{desde or 'hoy'}_{hasta or 'hoy'}.csv"
    return Response(
        content="﻿" + buf.getvalue(),  # BOM para que Excel respete tildes y ñ
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get("/api/reporte.xlsx")
def api_reporte_xlsx(vista: str = "visitas", desde: Optional[str] = None, hasta: Optional[str] = None,
                     estado: Optional[str] = None, q: Optional[str] = None):
    filas = consultar_reporte(vista, desde, hasta, estado, q)
    cols = COLUMNAS[vista]
    wb = Workbook()
    ws = wb.active
    ws.title = HOJAS[vista]
    ws.append([titulo for _, titulo in cols])
    for celda in ws[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1E293B")
        celda.alignment = Alignment(vertical="center")
    for f in filas:
        valores = []
        for k, _ in cols:
            v = f.get(k)
            if k in COLUMNAS_FECHA and v:
                try:
                    v = datetime.fromisoformat(v)  # fecha real de Excel: se puede ordenar y filtrar
                except ValueError:
                    pass
            valores.append(v)
        ws.append(valores)
        for celda in ws[ws.max_row]:
            if isinstance(celda.value, datetime):
                celda.number_format = "yyyy-mm-dd hh:mm:ss"
            elif isinstance(celda.value, str):
                celda.data_type = "s"  # texto literal: un "=..." venido de un audio no se ejecuta como fórmula
    for i, (k, _) in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = ANCHOS.get(k, 20 if k in COLUMNAS_FECHA else 16)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    nombre = f"bitacora_{vista}_{desde or 'hoy'}_{hasta or 'hoy'}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )
