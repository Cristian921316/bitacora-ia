"""Copia TODOS los datos de la base SQLite actual a PostgreSQL.

Uso (el día de la migración, con la app detenida):
    DATABASE_URL=postgresql://usuario:clave@servidor:5432/bitacora \\
        .venv/bin/python scripts/migrar_a_postgres.py --origen sqlite:///bitacora.db

- DATABASE_URL es el DESTINO (PostgreSQL). Debe estar vacío: el script crea el esquema con Alembic.
- El ORIGEN debe estar al día (arranca la app una vez para que aplique las migraciones).
- No copia las sesiones abiertas: los usuarios vuelven a ingresar una vez.
- Al terminar compara los conteos tabla por tabla y deja los contadores de ID listos.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, func, insert, inspect, select, text  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402

# Orden que respeta las relaciones entre tablas
TABLAS = ["usuarios", "casas", "configuracion", "residentes", "accesos", "movimientos", "intentos_bloqueados"]
CON_ID_AUTOINCREMENTAL = ["usuarios", "residentes", "accesos", "movimientos", "intentos_bloqueados"]
LOTE = 1000


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--origen", required=True, help="URL de la base SQLite de origen, p. ej. sqlite:///bitacora.db")
    args = ap.parse_args()

    if not config.DATABASE_URL.startswith("postgresql"):
        print("ERROR: DATABASE_URL (el destino) debe apuntar a PostgreSQL.")
        return 1
    origen = create_engine(args.origen)
    if "alembic_version" not in inspect(origen).get_table_names():
        print("ERROR: la base de origen no está al día. Arranca la app una vez para que aplique las migraciones.")
        return 1

    print("Creando el esquema en PostgreSQL (Alembic)…")
    db.init_db()
    destino = db.engine

    with destino.connect() as d:
        ocupadas = [t for t in TABLAS if d.execute(select(func.count()).select_from(db.metadata.tables[t])).scalar()]
    if ocupadas:
        print(f"ERROR: el destino ya tiene datos en {ocupadas}. Usa una base vacía.")
        return 1

    with origen.connect() as o, destino.begin() as d:
        for nombre in TABLAS:
            tabla = db.metadata.tables[nombre]
            filas = [dict(r._mapping) for r in o.execute(select(tabla)).all()]
            for i in range(0, len(filas), LOTE):
                d.execute(insert(tabla), filas[i:i + LOTE])
            print(f"  {nombre:22} {len(filas):>6} filas copiadas")
        for nombre in CON_ID_AUTOINCREMENTAL:  # que el próximo ID continúe después del último copiado
            d.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{nombre}', 'id'), COALESCE((SELECT MAX(id) FROM {nombre}), 0) + 1, false)"))

    print("Verificando conteos…")
    ok = True
    with origen.connect() as o, destino.connect() as d:
        for nombre in TABLAS:
            tabla = db.metadata.tables[nombre]
            a = o.execute(select(func.count()).select_from(tabla)).scalar()
            b = d.execute(select(func.count()).select_from(tabla)).scalar()
            marca = "OK " if a == b else "DIFERENTE"
            ok &= a == b
            print(f"  {marca} {nombre:22} origen={a} destino={b}")
    print("LISTO: migración completa." if ok else "ATENCIÓN: hay diferencias, revisa antes de usar el destino.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
