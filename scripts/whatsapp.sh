#!/usr/bin/env bash
# Utilidades de la línea de WhatsApp (Whapi.Cloud). Se ejecuta EN el servidor, dentro de la carpeta del proyecto.
#   ./scripts/whatsapp.sh estado              estado del canal (debe estar autenticado/conectado)
#   ./scripts/whatsapp.sh url                 muestra la URL que se pega en Whapi como webhook
#   ./scripts/whatsapp.sh webhook             pone esa URL en el canal por API (alternativa a pegarla en el panel)
#   ./scripts/whatsapp.sh enviar NUMERO "txt" envía un mensaje de prueba (NUMERO con código de país, sin +)
# El QR y la vinculación se hacen en el panel de Whapi (dashboard.whapi.cloud > tu canal).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "No encuentro el archivo .env en $(pwd)"; exit 1; }
set -a; . ./.env; set +a
: "${WHAPI_TOKEN:?Falta WHAPI_TOKEN en el .env}"
BASE="${WHAPI_URL:-https://gate.whapi.cloud}"
URL_WEBHOOK="https://${DOMINIO:?Falta DOMINIO en el .env}/api/whatsapp/webhook?token=${WEBHOOK_TOKEN:?Falta WEBHOOK_TOKEN en el .env}"

api() { curl -fsS -m 20 -H "Authorization: Bearer ${WHAPI_TOKEN}" -H 'content-type: application/json' "$@"; }

case "${1:-}" in
  estado)
    api "$BASE/health" | python3 -c "
import sys, json
d = json.load(sys.stdin)
st = d.get('status') or {}
print(st.get('text') or json.dumps(d, ensure_ascii=False))"
    ;;
  url)
    echo "$URL_WEBHOOK"
    echo "(Modo: body · Evento: messages / post. No compartas esta URL: el token es el secreto.)"
    ;;
  webhook)
    api -X PATCH "$BASE/settings" -d "{\"webhooks\":[{\"url\":\"${URL_WEBHOOK}\",\"mode\":\"body\",\"events\":[{\"type\":\"messages\",\"method\":\"post\"}]}]}" > /dev/null \
      && echo "Webhook configurado en el canal."
    ;;
  enviar)
    [ -n "${2:-}" ] && [ -n "${3:-}" ] || { echo 'Uso: ./scripts/whatsapp.sh enviar 593988391178 "texto"'; exit 1; }
    api -X POST "$BASE/messages/text" -d "$(python3 -c "import json,sys; print(json.dumps({'to': sys.argv[1], 'body': sys.argv[2]}))" "$2" "$3")" > /dev/null \
      && echo "Mensaje enviado."
    ;;
  *)
    sed -n '2,7p' "$0"
    exit 1
    ;;
esac
