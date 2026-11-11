"""Capa de datos. Usa SQLAlchemy Core: el mismo código corre en SQLite (hoy) y en PostgreSQL (mañana).

El esquema vive en las migraciones de Alembic (carpeta alembic/). Las tablas de abajo describen ese
esquema para armar las consultas; cualquier cambio de columnas va con una migración nueva.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, CheckConstraint, Column, ForeignKey, Index, Integer, MetaData, String, Table, Text,
    create_engine, event, inspect, insert, select, true,
)

import config

metadata = MetaData()

# Fechas y horas se guardan como texto ISO "2026-11-07T10:15:03" (hora local del servidor). Ordenan bien
# como texto y funcionan igual en cualquier motor; en Postgres se pueden convertir a timestamp después.

accesos = Table(
    "accesos", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("casa", Text, nullable=False),
    Column("nombre_visitante", Text, nullable=False),
    Column("tipo", Text, nullable=False),
    Column("placa", Text),
    Column("observaciones", Text),
    Column("estado", Text, nullable=False, server_default="PRE_AUTORIZADO"),
    Column("autorizado_por", Text),
    Column("fecha_hora", Text, nullable=False),
    Column("transcripcion", Text),
    Column("origen", Text),
    Column("telefono", Text),
    Column("hora_ingreso", Text),
    Column("hora_salida", Text),
    Column("hora_respuesta", Text),
    Column("respondido_por", Text),
    CheckConstraint("estado IN ('PRE_AUTORIZADO','ADENTRO','SALIO','PENDIENTE','RECHAZADO')", name="ck_accesos_estado"),
    Index("idx_accesos_fecha", "fecha_hora"),
    sqlite_autoincrement=True,
)

# Historial: solo se agregan filas, nunca se editan ni se borran.
movimientos = Table(
    "movimientos", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("acceso_id", Integer, nullable=False),
    Column("evento", Text, nullable=False),
    Column("fecha_hora", Text, nullable=False),
    Column("origen", Text),
    Column("usuario", Text),  # quién lo hizo (usuario del sistema), si aplica
    CheckConstraint("evento IN ('REGISTRADO','INGRESO','SALIDA','SOLICITUD','AUTORIZADO','RECHAZADO')", name="ck_movimientos_evento"),
    Index("idx_mov_fecha", "fecha_hora"),
    sqlite_autoincrement=True,
)

# Padrón de residentes. El celular (WhatsApp) es opcional: hay personas importadas que aún no tienen uno registrado.
residentes = Table(
    "residentes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("telefono", String(20), unique=True),
    Column("cedula", String(20)),          # puede repetirse: la lista de residentes no siempre es limpia
    Column("nombre", Text),
    Column("casa", Text, nullable=False),
    Index("ix_residentes_cedula", "cedula"),
    sqlite_autoincrement=True,
)

# Estado de pago POR CASA (una casa puede tener varios celulares).
casas = Table(
    "casas", metadata,
    Column("clave", String(200), primary_key=True),  # casa en minúsculas y sin espacios repetidos
    Column("casa", String(200), nullable=False),
    Column("al_dia", Boolean, nullable=False, server_default=true()),
    Column("actualizado_por", String(50)),
    Column("actualizado_en", String(19)),
)

usuarios = Table(
    "usuarios", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("usuario", String(50), nullable=False, unique=True),
    Column("nombre", String(100), nullable=False),
    Column("rol", String(10), nullable=False),
    Column("clave_hash", String(255), nullable=False),
    Column("activo", Boolean, nullable=False, server_default=true()),
    Column("debe_cambiar_clave", Boolean, nullable=False, server_default="0"),
    Column("creado", String(19), nullable=False),
    CheckConstraint("rol IN ('ADMIN','GUARDIA')", name="ck_usuarios_rol"),
    sqlite_autoincrement=True,
)

sesiones = Table(
    "sesiones", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("usuario_id", Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
    Column("creada", String(19), nullable=False),
    Column("expira", String(19), nullable=False),
)

# Visitas que no se pudieron autorizar por mora: queda constancia para el administrador.
intentos_bloqueados = Table(
    "intentos_bloqueados", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("fecha_hora", String(19), nullable=False),
    Column("casa", Text, nullable=False),
    Column("origen", String(20)),
    Column("telefono", String(20)),
    Column("usuario", String(50)),
    Column("detalle", Text),
    Column("motivo", String(20), nullable=False),
    sqlite_autoincrement=True,
)

# Parámetros que el administrador cambia desde la pantalla de configuración (ver parametros.py).
configuracion = Table(
    "configuracion", metadata,
    Column("clave", String(50), primary_key=True),
    Column("valor", String(200), nullable=False),
    Column("actualizado_por", String(50)),
    Column("actualizado_en", String(19)),
)

_es_sqlite = config.DATABASE_URL.startswith("sqlite")
engine = create_engine(
    config.DATABASE_URL,
    pool_pre_ping=True,
    **({"connect_args": {"check_same_thread": False}} if _es_sqlite else {}),
)

if _es_sqlite:
    @event.listens_for(engine, "connect")
    def _pragmas_sqlite(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")   # lecturas y escrituras no se bloquean entre sí
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


# ---------- utilidades ----------
def ahora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def fila(row) -> Optional[dict]:
    return dict(row._mapping) if row is not None else None


def clave_casa(casa: str) -> str:
    return " ".join((casa or "").lower().split())


def asegurar_casa(conn, casa: str) -> None:
    """Registra la casa (al día por defecto) si todavía no existe."""
    clave = clave_casa(casa)
    if not clave:
        return
    if conn.execute(select(casas.c.clave).where(casas.c.clave == clave)).first() is None:
        conn.execute(insert(casas).values(clave=clave, casa=" ".join(casa.split()), al_dia=True))


def casa_al_dia(conn, casa: str) -> bool:
    """True si la casa está al día o si no la conocemos (no se bloquea lo que no se puede verificar)."""
    r = conn.execute(select(casas.c.al_dia).where(casas.c.clave == clave_casa(casa))).first()
    return True if r is None else bool(r[0])


def registrar_bloqueo(conn, casa: str, origen: str, detalle: str, telefono: Optional[str] = None,
                      usuario: Optional[str] = None, motivo: str = "IMPAGO") -> None:
    conn.execute(insert(intentos_bloqueados).values(
        fecha_hora=ahora_iso(), casa=casa, origen=origen, telefono=telefono, usuario=usuario,
        detalle=(detalle or "")[:300], motivo=motivo))


# ---------- migraciones ----------
def _alembic_cfg():
    from alembic.config import Config
    cfg = Config(str(config.BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(config.BASE_DIR / "alembic"))
    return cfg


def init_db() -> None:
    """Deja la base al día con el último esquema. Es seguro ejecutarla en cada arranque."""
    from alembic import command
    cfg = _alembic_cfg()
    tablas = set(inspect(engine).get_table_names())
    if "accesos" in tablas and "alembic_version" not in tablas:
        # Base creada antes de usar Alembic (la del piloto): ya tiene el esquema de la migración 0001
        command.stamp(cfg, "0001")
    command.upgrade(cfg, "head")
