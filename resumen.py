"""Resumen de actividad por WhatsApp para los administradores (hoy, ayer, semana). Solo cifras, sin nombres ni casas."""
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select

from db import accesos, intentos_bloqueados
from reportes import _norm

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
TIPOS = {"VISITA": "visita", "DELIVERY": "delivery", "TAXI_UBER": "taxi/uber", "SERVICIO_TECNICO": "técnico"}
AYUDA_ADMIN = ("📊 Para ver el resumen escribe o di:\n• *reporte de hoy*\n• *reporte de ayer*\n• *reporte de la semana*")

_DISPARADOR = re.compile(r"\b(reporte|resumen|informe|balance|cuantas|cuantos|estadistica|estadisticas)\b")


def detectar_periodo(texto: str) -> Optional[str]:
    """'hoy' | 'ayer' | 'semana' si el mensaje pide un resumen; None si no (así 'hoy viene Juan' sigue siendo un anuncio)."""
    t = re.sub(r"[^\w\s]", " ", _norm(texto)).strip()
    t = re.sub(r"\s+", " ", t)
    if not t:
        return None
    if t in ("hoy", "ayer", "semana"):
        return t
    if not _DISPARADOR.search(t):
        return None
    if re.search(r"\bayer\b", t):
        return "ayer"
    if re.search(r"\b(semana|semanal|7 dias|siete dias)\b", t):
        return "semana"
    return "hoy"


def _rango(periodo: str, ahora: datetime):
    hoy0 = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    if periodo == "ayer":
        return hoy0 - timedelta(days=1), hoy0, "Ayer"
    if periodo == "semana":
        return hoy0 - timedelta(days=6), ahora, "Últimos 7 días"
    return hoy0, ahora, "Hoy"


def armar_resumen(conn, periodo: str, ahora: Optional[datetime] = None) -> str:
    ahora = ahora or datetime.now()
    desde, hasta, titulo = _rango(periodo, ahora)
    d, h = desde.isoformat(timespec="seconds"), hasta.isoformat(timespec="seconds")
    en_rango = (accesos.c.fecha_hora >= d, accesos.c.fecha_hora < h) if periodo == "ayer" else (accesos.c.fecha_hora >= d, accesos.c.fecha_hora <= h)

    def contar(*cond):
        return conn.execute(select(func.count()).select_from(accesos).where(*en_rango, *cond)).scalar() or 0

    total = contar()
    if periodo == "hoy":
        cuando = f"Hoy, {ahora.day} {MESES[ahora.month - 1]}, hasta las {ahora:%H:%M}"
    elif periodo == "ayer":
        a = desde
        cuando = f"Ayer, {a.day} {MESES[a.month - 1]} (día completo)"
    else:
        cuando = f"Últimos 7 días ({desde.day} {MESES[desde.month - 1]} al {ahora.day} {MESES[ahora.month - 1]}), hasta las {ahora:%H:%M}"
    if total == 0:
        return f"📊 {cuando}\nNo hay visitas registradas."

    ingresaron = contar(accesos.c.estado.in_(("ADENTRO", "SALIO")))
    rechazadas = contar(accesos.c.estado == "RECHAZADO")
    sin_ingresar = contar(accesos.c.estado.in_(("PRE_AUTORIZADO", "PENDIENTE")))
    sa_total = contar(accesos.c.origen == "garita")
    sa_ok = contar(accesos.c.origen == "garita", accesos.c.estado.in_(("PRE_AUTORIZADO", "ADENTRO", "SALIO")))
    sa_no = contar(accesos.c.origen == "garita", accesos.c.estado == "RECHAZADO")
    sa_esp = contar(accesos.c.origen == "garita", accesos.c.estado == "PENDIENTE")
    bloqueos = conn.execute(select(func.count()).select_from(intentos_bloqueados).where(
        intentos_bloqueados.c.fecha_hora >= d,
        (intentos_bloqueados.c.fecha_hora < h) if periodo == "ayer" else (intentos_bloqueados.c.fecha_hora <= h))).scalar() or 0
    por_tipo = conn.execute(select(accesos.c.tipo, func.count()).where(*en_rango).group_by(accesos.c.tipo)
                            .order_by(func.count().desc())).all()
    tipos = ", ".join(f"{TIPOS.get(t, t.lower())} {n}" for t, n in por_tipo)

    lineas = [f"📊 {cuando}", f"• Visitas: {total} · Ingresaron: {ingresaron}",
              f"• Rechazadas: {rechazadas} · Sin ingresar: {sin_ingresar}"]
    if sa_total:
        extra = f" ({sa_ok} autorizadas, {sa_no} rechazadas, {sa_esp} sin respuesta)"
        lineas.append(f"• Sin aviso en garita: {sa_total}{extra}")
    if bloqueos:
        lineas.append(f"• Bloqueos por casa no al día: {bloqueos}")
    lineas.append(f"• Por tipo: {tipos}")
    return "\n".join(lineas)
