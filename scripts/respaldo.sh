#!/usr/bin/env bash
# Copia de seguridad consistente de la base de datos (segura aunque la app esté en uso).
#   ./scripts/respaldo.sh [dias_a_conservar]     (por defecto 14)
# Los respaldos quedan en el volumen de la app: /data/backups. OJO: eso NO protege si se pierde el servidor;
# copia también la carpeta a otro lugar (ver docs/DESPLIEGUE.md, sección Respaldos).
set -euo pipefail
cd "$(dirname "$0")/.."
DIAS="${1:-14}"
docker compose exec -T app python - "$DIAS" <<'PY'
import glob, os, sqlite3, sys, time
from datetime import datetime

os.makedirs("/data/backups", exist_ok=True)
destino = f"/data/backups/bitacora_{datetime.now():%Y%m%d_%H%M%S}.db"
origen = sqlite3.connect("/data/bitacora.db")
copia = sqlite3.connect(destino)
origen.backup(copia)          # copia consistente incluso con la app escribiendo
copia.close(); origen.close()

c = sqlite3.connect(destino)
assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "el respaldo salió dañado"
visitas = c.execute("SELECT COUNT(*) FROM accesos").fetchone()[0]
residentes = c.execute("SELECT COUNT(*) FROM residentes").fetchone()[0]
c.close()
print(f"Respaldo listo: {destino} ({visitas} visitas, {residentes} residentes, {os.path.getsize(destino) // 1024} KB)")

limite = time.time() - int(sys.argv[1]) * 86400
for f in glob.glob("/data/backups/bitacora_*.db"):
    if os.path.getmtime(f) < limite:
        os.remove(f)
        print("Borrado por antiguo:", os.path.basename(f))
PY
