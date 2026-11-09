"""Parámetros de operación que el administrador puede cambiar sin tocar código ni reiniciar.

Para agregar uno nuevo: descríbelo en PARAMETROS (valor por defecto, mínimo, máximo, texto) y úsalo con valor(nombre).
Si un parámetro nunca se editó, vale su valor por defecto.
"""
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy import insert, select, update

from db import ahora_iso, configuracion, engine

PARAMETROS: Dict[str, dict] = {
    "vigencia_horas": {
        "defecto": 12, "min": 1, "max": 168, "unidad": "horas",
        "titulo": "Vigencia de las pre-autorizaciones",
        "ayuda": ("Una visita pre-autorizada (o una visita sin aviso que sigue esperando respuesta) deja de mostrarse en la "
                  "garita y ya no se puede ingresar cuando pasan estas horas desde que se registró. "
                  "Queda en el reporte como «Vencida»."),
    },
    "control_salidas": {
        "defecto": 0, "min": 0, "max": 1, "unidad": "", "tipo": "switch",
        "titulo": "Controlar las salidas en la garita",
        "ayuda": ("Apagado: la garita solo muestra las pre-autorizadas y las que esperan autorización; al dar ingreso la visita "
                  "sale de la pantalla y no se pide registrar la salida. Encendido: aparece la columna «Adentro del Conjunto» "
                  "con el botón de salida y la alerta de quienes siguen adentro desde ayer."),
    },
    "rechazo_visible_minutos": {
        "defecto": 30, "min": 1, "max": 240, "unidad": "minutos",
        "titulo": "Tiempo que se muestra un rechazo en la garita",
        "ayuda": "Cuando un residente NO autoriza una visita sin aviso, la garita la muestra en rojo durante este tiempo.",
    },
}


def valor(nombre: str) -> int:
    """Valor actual del parámetro (el configurado, o el que trae por defecto)."""
    with engine.connect() as conn:
        v = conn.execute(select(configuracion.c.valor).where(configuracion.c.clave == nombre)).scalar()
    try:
        return int(v) if v is not None else PARAMETROS[nombre]["defecto"]
    except (TypeError, ValueError):
        return PARAMETROS[nombre]["defecto"]


def listar() -> list:
    with engine.connect() as conn:
        guardados = {r.clave: r for r in conn.execute(select(configuracion)).all()}
    salida = []
    for nombre, p in PARAMETROS.items():
        g = guardados.get(nombre)
        try:
            actual = int(g.valor) if g is not None else p["defecto"]
        except (TypeError, ValueError):
            actual = p["defecto"]
        salida.append({
            "clave": nombre, "valor": actual, "defecto": p["defecto"], "min": p["min"], "max": p["max"],
            "unidad": p["unidad"], "tipo": p.get("tipo", "numero"), "titulo": p["titulo"], "ayuda": p["ayuda"],
            "actualizado_por": g.actualizado_por if g is not None else None,
            "actualizado_en": g.actualizado_en if g is not None else None,
        })
    return salida


def guardar(nombre: str, nuevo: int, usuario: str) -> int:
    """Guarda el parámetro. Lanza ValueError con un mensaje claro si no es válido."""
    p: Optional[dict] = PARAMETROS.get(nombre)
    if p is None:
        raise KeyError(nombre)
    if isinstance(nuevo, bool) or not isinstance(nuevo, int):
        raise ValueError("El valor debe ser un número entero")
    if not p["min"] <= nuevo <= p["max"]:
        raise ValueError(f"El valor debe estar entre {p['min']} y {p['max']} {p['unidad']}")
    datos = dict(valor=str(nuevo), actualizado_por=usuario, actualizado_en=ahora_iso())
    with engine.begin() as conn:
        if conn.execute(select(configuracion.c.clave).where(configuracion.c.clave == nombre)).first():
            conn.execute(update(configuracion).where(configuracion.c.clave == nombre).values(**datos))
        else:
            conn.execute(insert(configuracion).values(clave=nombre, **datos))
    return nuevo


# ---------- vigencia de las visitas ----------
ESTADOS_CON_VIGENCIA = ("PRE_AUTORIZADO", "PENDIENTE")


def limite_vigencia() -> str:
    """Fecha ISO a partir de la cual una visita registrada ya está vencida (ahora menos la vigencia)."""
    return (datetime.now() - timedelta(hours=valor("vigencia_horas"))).isoformat(timespec="seconds")


def estado_efectivo(reg: dict, limite: str) -> str:
    """El estado que se debe mostrar: una visita sin ingresar que superó su vigencia es «VENCIDA»."""
    if reg["estado"] in ESTADOS_CON_VIGENCIA and reg["fecha_hora"] < limite:
        return "VENCIDA"
    return reg["estado"]
