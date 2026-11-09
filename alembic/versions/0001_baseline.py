"""Esquema base del piloto: accesos, movimientos y residentes.

Las bases creadas antes de Alembic ya tienen este esquema y se marcan con `stamp` (ver db.init_db).

Revision ID: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accesos",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("casa", sa.Text, nullable=False),
        sa.Column("nombre_visitante", sa.Text, nullable=False),
        sa.Column("tipo", sa.Text, nullable=False),
        sa.Column("placa", sa.Text),
        sa.Column("observaciones", sa.Text),
        sa.Column("estado", sa.Text, nullable=False, server_default="PRE_AUTORIZADO"),
        sa.Column("autorizado_por", sa.Text),
        sa.Column("fecha_hora", sa.Text, nullable=False),
        sa.Column("transcripcion", sa.Text),
        sa.Column("origen", sa.Text),
        sa.Column("telefono", sa.Text),
        sa.Column("hora_ingreso", sa.Text),
        sa.Column("hora_salida", sa.Text),
        sa.Column("hora_respuesta", sa.Text),
        sa.Column("respondido_por", sa.Text),
        sa.CheckConstraint(
            "estado IN ('PRE_AUTORIZADO','ADENTRO','SALIO','PENDIENTE','RECHAZADO')", name="ck_accesos_estado"),
        sqlite_autoincrement=True,
    )
    op.create_index("idx_accesos_fecha", "accesos", ["fecha_hora"])

    op.create_table(
        "movimientos",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("acceso_id", sa.Integer, nullable=False),
        sa.Column("evento", sa.Text, nullable=False),
        sa.Column("fecha_hora", sa.Text, nullable=False),
        sa.Column("origen", sa.Text),
        sa.CheckConstraint(
            "evento IN ('REGISTRADO','INGRESO','SALIDA','SOLICITUD','AUTORIZADO','RECHAZADO')",
            name="ck_movimientos_evento"),
        sqlite_autoincrement=True,
    )
    op.create_index("idx_mov_fecha", "movimientos", ["fecha_hora"])

    op.create_table(
        "residentes",
        sa.Column("telefono", sa.String(20), primary_key=True),
        sa.Column("casa", sa.Text, nullable=False),
        sa.Column("nombre", sa.Text),
    )


def downgrade() -> None:
    op.drop_table("residentes")
    op.drop_table("movimientos")
    op.drop_table("accesos")
