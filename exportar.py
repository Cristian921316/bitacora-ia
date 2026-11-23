"""Exportar la tabla de residentes a Excel (solo ADMIN). Sustituye al importador: la lista se edita en la pantalla Residentes."""
import io
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import select

from auth import solo_admin
from db import casas, clave_casa, engine, fila, residentes

router = APIRouter()

COLUMNAS = [("Cédula", 16), ("Nombre", 30), ("Casa", 18), ("Estado de pago", 16),
            ("Celular", 16), ("Celular 2", 16), ("Alias del celular 2", 22)]


@router.get("/api/residentes/exportar.xlsx")
def exportar_residentes(_: dict = Depends(solo_admin)):
    with engine.connect() as conn:
        rs = [fila(x) for x in conn.execute(select(residentes)).all()]
        pagos = {c.clave: c for c in conn.execute(select(casas)).all()}
    rs.sort(key=lambda r: ((r["casa"] or "").lower(), (r["nombre"] or "").lower()))
    wb = Workbook()
    ws = wb.active
    ws.title = "Residentes"
    ws.append([nombre for nombre, _ in COLUMNAS])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1E293B")
    for r in rs:
        pago = pagos.get(clave_casa(r["casa"]))
        ws.append([r["cedula"], r["nombre"], r["casa"], "Al día" if pago is None or pago.al_dia else "Impago",
                   r["telefono"], r["telefono2"], r["alias2"]])
        for celda in ws[ws.max_row]:
            if isinstance(celda.value, str):
                celda.data_type = "s"  # texto literal: ceros iniciales y nada que Excel pueda tomar como fórmula
    for i, (_, ancho) in enumerate(COLUMNAS):
        ws.column_dimensions[chr(65 + i)].width = ancho
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    return Response(content=buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="residentes_{datetime.now():%Y-%m-%d}.xlsx"'})
