"""Segundo celular por residente (esposa, hija, etc.) con un alias para reconocerlo.

- residentes.telefono2: segundo número de WhatsApp de la MISMA persona (único; que no se repita con ningún celular principal
  se valida en la aplicación, porque la base no puede comparar dos columnas).
- residentes.alias2: cómo la persona quiere llamar a ese número (por ejemplo «esposa», «hija»). Solo lo ve el administrador.

Los datos existentes no cambian: las dos columnas nacen vacías.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # sqlite_autoincrement: al recrear la tabla en SQLite no se pierde el AUTOINCREMENT de id
    with op.batch_alter_table("residentes", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.add_column(sa.Column("telefono2", sa.String(20)))
        batch.add_column(sa.Column("alias2", sa.String(100)))
        batch.create_unique_constraint("uq_residentes_telefono2", ["telefono2"])


def downgrade() -> None:
    with op.batch_alter_table("residentes", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_constraint("uq_residentes_telefono2", type_="unique")
        batch.drop_column("alias2")
        batch.drop_column("telefono2")
