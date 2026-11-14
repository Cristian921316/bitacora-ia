# Comandos del servidor (chuleta rápida)

Todo se ejecuta **en el servidor**, dentro de `~/bitacora-ia`, salvo donde diga "en tu Mac".
No incluye claves, IP ni dominio (el repo es público).

```bash
ssh bitacora@IP_DEL_SERVIDOR        # entrar (en tu Mac). La llave pide su contraseña
cd ~/bitacora-ia                    # carpeta del proyecto
alias dc='docker compose'           # opcional: el .env ya trae COMPOSE_FILE, así que `docker compose` basta
```

## 1. Revisión rápida de salud
```bash
docker compose ps                                     # caddy y app deben decir "Up" (app "healthy")
curl -s https://TU_DOMINIO/salud                      # debe responder {"ok":true}
./scripts/whatsapp.sh estado                          # estado del canal en Whapi
```

## 2. Memoria, CPU y disco
```bash
free -h                                               # mira la columna "available" y la fila Swap
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"
sudo dmesg | grep -i -E "out of memory|killed process" | tail   # vacío = el sistema nunca mató nada por memoria
df -h /                                               # espacio en disco
uptime
```

## 3. Logs
```bash
docker compose logs --tail 50 app                     # últimas 50 líneas de la aplicación
docker compose logs -f --tail 0 app                   # EN VIVO (Ctrl+C sale). Escríbele al bot y mira qué aparece
docker compose logs --tail 30 caddy                   # HTTPS / certificados / errores de entrada
```
Qué buscar al escribirle al bot desde otro celular:
- En `app` debe aparecer `POST /api/whatsapp/webhook?token=*** ... 200`. Si NO aparece: Whapi no está llamando (revisa el canal y la URL del webhook en su panel).
- Si aparece 401: el token de la URL no coincide con `WEBHOOK_TOKEN` del `.env`. 405/404: la ruta o el método no llegan a la app.
- `[wsp] no se pudo responder...` = la app recibió el mensaje pero Whapi rechazó el envío (token `WHAPI_TOKEN` malo, canal desconectado o límite del plan).
- El token del webhook se oculta en estos registros (`token=***`).

## 4. WhatsApp (Whapi.Cloud)
```bash
./scripts/whatsapp.sh estado                          # estado del canal
./scripts/whatsapp.sh url                             # URL que se pega en Whapi como webhook (modo body, evento messages/post)
./scripts/whatsapp.sh webhook                         # la pone en el canal por API (alternativa a pegarla en el panel)
./scripts/whatsapp.sh enviar 593988391178 "prueba"    # mensaje de prueba (Ecuador: 0988391178 -> 593988391178, sin +)
```
El QR y la vinculación del número se hacen en el panel de Whapi (tu canal → conectar).

Si "se queda sin responder":
1. `./scripts/whatsapp.sh estado` (¿el canal sigue autenticado?).
2. Escríbele y mira `docker compose logs -f --tail 0 app`: ¿llega el webhook?
3. Si el canal pide QR otra vez: revincula en el panel. Causas típicas (según Whapi): número nuevo sin calentar, celular sin abrir WhatsApp en 14 días, dispositivo quitado por error.
4. Abre WhatsApp en el celular del bot cada 10–12 días y envía un par de mensajes.

## 5. Actualizar la aplicación (despliegue)
```bash
./scripts/respaldo.sh                                 # 1) respaldo de la base ANTES de cambiar nada
git fetch && git checkout RAMA && git pull            # 2) traer cambios (o solo `git pull` si ya está en la rama correcta)
docker compose up -d --build                          # 3) reconstruir y levantar
docker compose ps && docker compose logs --tail 30 app    # 4) verificar
./scripts/whatsapp.sh estado                          # 5) el canal sigue conectado
git log --oneline -1; git branch --show-current       # qué versión corre (para decirme el commit)
```

## 6. Respaldos
```bash
./scripts/respaldo.sh                                 # respaldo consistente (usa la API de SQLite, no cp)
ls -lh backups/ | tail                                # ver respaldos
./scripts/restaurar.sh                                # restaurar (guarda antes el estado actual). Léelo antes de usarlo
```

## 7. Datos que me sirven para diagnosticar (pega esto cuando algo falle)
```bash
git log --oneline -1; git branch --show-current
docker compose ps
./scripts/whatsapp.sh estado
free -h
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"
docker compose logs --since 2h app | grep -iE "webhook|wsp|error|Traceback" | tail -30
```
Y dime: **hora exacta** del fallo, **qué número** escribió, **desde qué celular**, y si el celular del bot tenía internet.

## 8. En tu Mac (entorno local)
```bash
pkill -f "uvicorn main:app"                           # bajar el servidor local
lsof -i :8000                                         # confirmar que no queda nada en el puerto 8000
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 # subirlo de nuevo (reinícialo tras cambios de backend)
.venv/bin/python -m pytest -q                         # pruebas
ssh-add --apple-use-keychain ~/.ssh/id_ed25519        # cargar la llave SSH (pide la contraseña una vez)
```

## 9. Contenedores
| Contenedor | Función |
|---|---|
| caddy | Entrada de internet (80/443), HTTPS automático |
| app | La aplicación (FastAPI + SQLite en el volumen `app_data`) y el webhook de WhatsApp |

WhatsApp ya no tiene contenedores propios: la app habla directo con Whapi.Cloud. Nunca borrar los volúmenes `app_data` y `caddy_data`.
