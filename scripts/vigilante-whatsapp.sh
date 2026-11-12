#!/usr/bin/env bash
# Vigilante de la línea de WhatsApp. Se ejecuta EN el servidor cada pocos minutos (cron).
# Reinicia SOLO el contenedor evolution-api (no toca la base ni la app) si:
#   - el estado de la instancia ya no es "open", o
#   - en los últimos minutos aparecieron errores de stream 503 / "stream errored out" (Evolution queda "open" pero sordo).
# No reinicia más de una vez cada 10 minutos. Registro: logs/vigilante-whatsapp.log
# Instalar:  crontab -e   y agregar:   */3 * * * * /home/bitacora/bitacora-ia/scripts/vigilante-whatsapp.sh
set -uo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || exit 1
set -a; . ./.env; set +a
BASE="http://127.0.0.1:${EVOLUTION_HOST_PORT:-8080}"
INST="${EVOLUTION_INSTANCE:-bitacora}"
VENTANA="${VIGILANTE_VENTANA:-4m}"
ENFRIAMIENTO=600
mkdir -p logs
LOG=logs/vigilante-whatsapp.log
MARCA=logs/.vigilante-ultimo-reinicio
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

estado="$(curl -fsS -m 10 -H "apikey: ${EVOLUTION_APIKEY}" "$BASE/instance/connectionState/$INST" 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['instance']['state'])" 2>/dev/null || echo "sin_respuesta")"
errores="$(docker compose -f docker-compose.prod.yml logs --since "$VENTANA" evolution-api 2>/dev/null \
  | grep -cE 'stream errored out|"code":"503"' || true)"

motivo=""
[ "$estado" != "open" ] && motivo="estado=$estado"
[ "${errores:-0}" -gt 0 ] && motivo="${motivo:+$motivo, }errores_stream=$errores"
[ -z "$motivo" ] && exit 0

ahora=$(date +%s); ultimo=$(cat "$MARCA" 2>/dev/null || echo 0)
if [ $((ahora - ultimo)) -lt $ENFRIAMIENTO ]; then
  log "Problema ($motivo) pero ya se reinició hace menos de 10 min; espero."; exit 0
fi
log "Reiniciando evolution-api ($motivo)"
echo "$ahora" > "$MARCA"
docker compose -f docker-compose.prod.yml restart evolution-api >> "$LOG" 2>&1
