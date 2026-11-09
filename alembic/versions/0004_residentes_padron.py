"""Residentes con id propio, cédula y celular opcional (permite importar personas sin celular).

SQLite no puede cambiar la llave primaria de una tabla, así que se crea la nueva, se copian los datos y se
reemplaza la anterior. Los datos existentes se conservan.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "residentes_nueva",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("telefono", sa.String(20)),
        sa.Column("cedula", sa.String(20)),
        sa.Column("nombre", sa.Text),
        sa.Column("casa", sa.Text, nullable=False),
        sa.UniqueConstraint("telefono", name="uq_residentes_telefono"),
        sa.UniqueConstraint("cedula", name="uq_residentes_cedula"),
        sqlite_autoincrement=True,
    )
    op.execute("INSERT INTO residentes_nueva (telefono, nombre, casa) SELECT telefono, nombre, casa FROM residentes")
    op.drop_table("residentes")
    op.rename_table("residentes_nueva", "residentes")


def downgrade() -> None:
    op.create_table(
        "residentes_vieja",
        sa.Column("telefono", sa.String(20), primary_key=True),
        sa.Column("casa", sa.Text, nullable=False),
        sa.Column("nombre", sa.Text),
    )
    op.execute("INSERT INTO residentes_vieja (telefono, nombre, casa) "
               "SELECT telefono, nombre, casa FROM residentes WHERE telefono IS NOT NULL")
    op.drop_table("residentes")
    op.rename_table("residentes_vieja", "residentes")
