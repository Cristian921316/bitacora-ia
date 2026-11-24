"""Estado LLAMADA: auditoría de las llamadas que el guardia hace al residente desde la garita (sin registrar la visita).

- accesos.estado y movimientos.evento admiten el valor nuevo «LLAMADA». Los datos existentes no cambian.
  Las filas LLAMADA no aparecen en la garita (solo en el reporte del administrador).

Revision ID: 0007
Revises: 0006
"""
import re

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

ESTADOS_NUEVOS = "estado IN ('PRE_AUTORIZADO','ADENTRO','SALIO','PENDIENTE','RECHAZADO','LLAMADA')"
ESTADOS_ANTES = "estado IN ('PRE_AUTORIZADO','ADENTRO','SALIO','PENDIENTE','RECHAZADO')"
EVENTOS_NUEVOS = "evento IN ('REGISTRADO','INGRESO','SALIDA','SOLICITUD','AUTORIZADO','RECHAZADO','LLAMADA')"
EVENTOS_ANTES = "evento IN ('REGISTRADO','INGRESO','SALIDA','SOLICITUD','AUTORIZADO','RECHAZADO')"


def _cambiar(tabla: str, nombre: str, antes: str, despues: str) -> None:
    """Cambia la condición CHECK. En SQLite la regla de la base real está escrita dentro de la columna y sin nombre, así que
    no se puede borrar por nombre: se reconstruye la tabla a partir de su propio CREATE TABLE con la condición cambiada."""
    conn = op.get_bind()
    if conn.dialect.name != "sqlite":
        op.drop_constraint(nombre, tabla, type_="check")
        op.create_check_constraint(nombre, tabla, despues)
        return
    sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tabla,)).scalar()
    if antes not in sql:
        raise RuntimeError(f"No encuentro la regla esperada en la tabla {tabla}; no se cambió nada.")
    indices = [r[0] for r in conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (tabla,))]
    seq = conn.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name=?", (tabla,)).scalar()
    nueva = re.sub(r'^CREATE TABLE\s+"?\w+"?', f"CREATE TABLE {tabla}_nueva", sql.replace(antes, despues), count=1)   # SQLite guarda el nombre con o sin comillas
    conn.exec_driver_sql(nueva)
    conn.exec_driver_sql(f"INSERT INTO {tabla}_nueva SELECT * FROM {tabla}")
    conn.exec_driver_sql(f"DROP TABLE {tabla}")              # sus índices se van con ella
    conn.exec_driver_sql(f"ALTER TABLE {tabla}_nueva RENAME TO {tabla}")
    for ddl in indices:
        conn.exec_driver_sql(ddl)
    if seq is not None:                                      # que los id nuevos sigan donde iban
        conn.exec_driver_sql("UPDATE sqlite_sequence SET seq=? WHERE name=?", (seq, tabla))


def upgrade() -> None:
    _cambiar("accesos", "ck_accesos_estado", ESTADOS_ANTES, ESTADOS_NUEVOS)
    _cambiar("movimientos", "ck_movimientos_evento", EVENTOS_ANTES, EVENTOS_NUEVOS)


def downgrade() -> None:
    # Ojo: falla si ya existen filas LLAMADA; bórralas antes de volver atrás.
    _cambiar("movimientos", "ck_movimientos_evento", EVENTOS_NUEVOS, EVENTOS_ANTES)
    _cambiar("accesos", "ck_accesos_estado", ESTADOS_NUEVOS, ESTADOS_ANTES)
