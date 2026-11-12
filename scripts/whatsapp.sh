#!/usr/bin/env bash
# Administra la línea de WhatsApp (Evolution API) del servidor. Se ejecuta EN el servidor, dentro de la carpeta del proyecto.
#   ./scripts/whatsapp.sh crear    crea la línea y conecta su webhook con la app (se hace una sola vez)
#   ./scripts/whatsapp.sh qr       muestra el QR para vincular el número (WhatsApp > Dispositivos vinculados)
#   ./scripts/whatsapp.sh estado   dice si el número está conectado
#   ./scripts/whatsapp.sh ajustes  reduce el tráfico de la línea (ignora grupos y estados, sin historial): más estable
#   ./scripts/whatsapp.sh salir    desvincula el número (para cambiarlo por otro)
# Para ver el QR en la terminal conviene:  sudo apt install qrencode
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "No encuentro el archivo .env en $(pwd)"; exit 1; }
set -a; . ./.env; set +a
BASE="http://127.0.0.1:${EVOLUTION_HOST_PORT:-8080}"
INST="${EVOLUTION_INSTANCE:-bitacora}"

api() { curl -fsS -H "apikey: ${EVOLUTION_APIKEY}" -H 'content-type: application/json' "$@"; }
campo() { python3 -c "import sys,json; d=json.load(sys.stdin); print(d$1)" 2>/dev/null || true; }

case "${1:-}" in
  crear)
    api -X POST "$BASE/instance/create" -d "{
      \"instanceName\": \"$INST\",
      \"integration\": \"WHATSAPP-BAILEYS\",
      \"qrcode\": false,
      \"webhook\": {
        \"url\": \"http://app:8000/api/whatsapp/webhook?token=${WEBHOOK_TOKEN}\",
        \"byEvents\": false,
        \"base64\": true,
        \"events\": [\"MESSAGES_UPSERT\"]
      },
      \"groupsIgnore\": true,
      \"alwaysOnline\": false,
      \"readMessages\": false,
      \"readStatus\": false,
      \"syncFullHistory\": false
    }" > /dev/null
    echo "Línea '$INST' creada. Ahora ejecuta:  ./scripts/whatsapp.sh qr"
    ;;
  qr)
    for intento in $(seq 1 8); do        # el QR vence en ~30 s: se renueva solo
      resp="$(api "$BASE/instance/connect/$INST")"
      if [ "$(api "$BASE/instance/connectionState/$INST" | campo "['instance']['state']")" = "open" ]; then
        echo "✅ El número ya está conectado."; exit 0
      fi
      codigo="$(printf '%s' "$resp" | campo "['code']")"
      [ -n "$codigo" ] || { echo "Todavía no hay QR. Reintenta en unos segundos."; sleep 5; continue; }
      clear 2>/dev/null || true
      echo "En el celular: WhatsApp > Ajustes > Dispositivos vinculados > Vincular un dispositivo."
      echo "(Intento $intento de 8: el QR se renueva solo. Ctrl+C para salir.)"; echo
      if command -v qrencode >/dev/null 2>&1; then
        qrencode -t ANSIUTF8 "$codigo"
      else
        printf '%s' "$resp" | campo "['base64']" | sed 's/^data:image\/png;base64,//' | base64 --decode > qr.png
        echo "No hay 'qrencode' instalado: guardé el QR en $(pwd)/qr.png (ábrelo con: scp usuario@servidor:$(pwd)/qr.png .)"
      fi
      sleep 25
    done
    echo "Se agotaron los intentos. Ejecuta de nuevo:  ./scripts/whatsapp.sh qr"
    ;;
  estado)
    api "$BASE/instance/connectionState/$INST" | campo "['instance']['state']"
    ;;
  ajustes)
    api -X POST "$BASE/settings/set/$INST" -d '{
      "rejectCall": false, "groupsIgnore": true, "alwaysOnline": false,
      "readMessages": false, "readStatus": false, "syncFullHistory": false
    }' > /dev/null && echo "Ajustes aplicados (sin grupos, sin estados, sin historial, no siempre en línea)."
    ;;
  salir)
    api -X DELETE "$BASE/instance/logout/$INST" > /dev/null && echo "Número desvinculado."
    ;;
  *)
    sed -n '2,9p' "$0"
    exit 1
    ;;
esac
