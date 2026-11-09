"""Usuarios con roles, sesiones, estado de pago por casa e intentos bloqueados.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("usuario", sa.String(50), nullable=False, unique=True),
        sa.Column("nombre", sa.String(100), nullable=False),
        sa.Column("rol", sa.String(10), nullable=False),
        sa.Column("clave_hash", sa.String(255), nullable=False),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("debe_cambiar_clave", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("creado", sa.String(19), nullable=False),
        sa.CheckConstraint("rol IN ('ADMIN','GUARDIA')", name="ck_usuarios_rol"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "sesiones",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("usuario_id", sa.Integer, sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("creada", sa.String(19), nullable=False),
        sa.Column("expira", sa.String(19), nullable=False),
    )
    op.create_table(
        "casas",
        sa.Column("clave", sa.String(100), primary_key=True),
        sa.Column("casa", sa.String(100), nullable=False),
        sa.Column("al_dia", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("actualizado_por", sa.String(50)),
        sa.Column("actualizado_en", sa.String(19)),
    )
    op.create_table(
        "intentos_bloqueados",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("fecha_hora", sa.String(19), nullable=False),
        sa.Column("casa", sa.Text, nullable=False),
        sa.Column("origen", sa.String(20)),
        sa.Column("telefono", sa.String(20)),
        sa.Column("usuario", sa.String(50)),
        sa.Column("detalle", sa.Text),
        sa.Column("motivo", sa.String(20), nullable=False),
        sqlite_autoincrement=True,
    )
    with op.batch_alter_table("movimientos") as batch:
        batch.add_column(sa.Column("usuario", sa.Text))

    # Las casas que ya tienen residentes registrados arrancan "al día"
    bind = op.get_bind()
    casas_existentes = bind.execute(sa.text("SELECT DISTINCT casa FROM residentes")).fetchall()
    vistas = set()
    for (casa,) in casas_existentes:
        clave = " ".join((casa or "").lower().split())
        if clave and clave not in vistas:
            vistas.add(clave)
            bind.execute(
                sa.text("INSERT INTO casas (clave, casa, al_dia) VALUES (:clave, :casa, :al_dia)"),
                {"clave": clave, "casa": " ".join(casa.split()), "al_dia": True},
            )


def downgrade() -> None:
    with op.batch_alter_table("movimientos") as batch:
        batch.drop_column("usuario")
    op.drop_table("intentos_bloqueados")
    op.drop_table("casas")
    op.drop_table("sesiones")
    op.drop_table("usuarios")
