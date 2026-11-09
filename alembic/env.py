from alembic import context
from sqlalchemy import create_engine

import config
from db import metadata

target_metadata = metadata


def run_migrations_online() -> None:
    connectable = create_engine(config.DATABASE_URL)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite no puede alterar tablas directamente
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


run_migrations_online()
