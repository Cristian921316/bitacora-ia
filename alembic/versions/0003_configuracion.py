"""Parámetros de operación configurables (vigencia de pre-autorizaciones, etc.).

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "configuracion",
        sa.Column("clave", sa.String(50), primary_key=True),
        sa.Column("valor", sa.String(200), nullable=False),
        sa.Column("actualizado_por", sa.String(50)),
        sa.Column("actualizado_en", sa.String(19)),
    )


def downgrade() -> None:
    op.drop_table("configuracion")
