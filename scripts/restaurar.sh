#!/usr/bin/env bash
# Restaura la base de datos desde un respaldo. NO borra archivos: copia el respaldo sobre la base con la API de SQLite.
# Antes de restaurar guarda una copia del estado ACTUAL (pre_restauracion_*.db) por si te arrepientes.
#   ./scripts/restaurar.sh                     lista los respaldos disponibles
#   ./scripts/restaurar.sh <nombre.db>         restaura ese respaldo (pide confirmación)
#   ./scripts/restaurar.sh <nombre.db> --si    restaura sin preguntar
set -euo pipefail
cd "$(dirname "$0")/.."
ARCHIVO="${1:-}"

if [ -z "$ARCHIVO" ]; then
  echo "Respaldos disponibles (el nombre lleva la fecha y hora):"
  docker compose run --rm --no-deps -T app ls -1 /data/backups
  echo; echo "Uso: ./scripts/restaurar.sh <nombre.db>"
  exit 0
fi
case "$ARCHIVO" in *[!A-Za-z0-9._-]*) echo "Nombre de archivo no válido."; exit 1;; esac

if [ "${2:-}" != "--si" ]; then
  read -r -p "Se reemplazará la base actual por '$ARCHIVO' (antes se guarda una copia del estado actual). Escribe SI para continuar: " r
  [ "$r" = "SI" ] || { echo "Cancelado."; exit 1; }
fi

docker compose stop app
docker compose run --rm --no-deps -T app python - "$ARCHIVO" <<'PY'
import os, sqlite3, sys
from datetime import datetime

origen = f"/data/backups/{os.path.basename(sys.argv[1])}"
actual = "/data/bitacora.db"
if not os.path.exists(origen):
    sys.exit(f"No existe el respaldo {origen}")

# 1) el respaldo debe estar sano antes de tocar nada
src = sqlite3.connect(origen)
if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
    sys.exit("El respaldo está dañado: no se restauró nada.")

# 2) copia de seguridad del estado actual
if os.path.exists(actual):
    seguridad = f"/data/backups/pre_restauracion_{datetime.now():%Y%m%d_%H%M%S}.db"
    a, b = sqlite3.connect(actual), sqlite3.connect(seguridad)
    a.backup(b)
    a.close(); b.close()
    print("Copia del estado actual guardada en:", seguridad)

# 3) restaurar
dst = sqlite3.connect(actual)
src.backup(dst)
visitas = dst.execute("SELECT COUNT(*) FROM accesos").fetchone()[0]
dst.close(); src.close()
print(f"Base restaurada desde {os.path.basename(origen)} ({visitas} visitas).")
PY
docker compose start app
echo "App arrancando. Revisa con: docker compose ps"
