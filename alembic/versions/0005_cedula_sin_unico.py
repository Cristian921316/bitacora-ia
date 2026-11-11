"""La cédula ya no es única (la lista de residentes puede traerla repetida) y el texto de la casa admite 50 + 50 caracteres.

- residentes.cedula: se quita la restricción única y se deja un índice normal para buscar rápido.
- casas.clave / casas.casa: de 100 a 200 caracteres ("Mz <50> Villa <50>" puede pasar de 100).

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # sqlite_autoincrement: al recrear la tabla en SQLite no se pierde el AUTOINCREMENT de id
    with op.batch_alter_table("residentes", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_constraint("uq_residentes_cedula", type_="unique")
        batch.create_index("ix_residentes_cedula", ["cedula"])
    with op.batch_alter_table("casas") as batch:
        batch.alter_column("clave", existing_type=sa.String(100), type_=sa.String(200), existing_nullable=False)
        batch.alter_column("casa", existing_type=sa.String(100), type_=sa.String(200), existing_nullable=False)


def downgrade() -> None:
    # Ojo: volver a exigir cédula única falla si ya hay cédulas repetidas.
    with op.batch_alter_table("casas") as batch:
        batch.alter_column("casa", existing_type=sa.String(200), type_=sa.String(100), existing_nullable=False)
        batch.alter_column("clave", existing_type=sa.String(200), type_=sa.String(100), existing_nullable=False)
    with op.batch_alter_table("residentes", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_index("ix_residentes_cedula")
        batch.create_unique_constraint("uq_residentes_cedula", ["cedula"])
