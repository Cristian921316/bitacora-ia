# Despliegue en un VPS

Esta guía lleva la app a un servidor propio (VPS) con HTTPS automático.

**Qué se probó y qué no.** Antes de escribirla se levantó el stack completo en un entorno limpio y se verificó: construcción de la
imagen, HTTPS y cabeceras de seguridad, cookie `Secure`, hora de Ecuador, app sin root, datos que sobreviven a reinicios,
respaldo y restauración. WhatsApp se conecta directo a Whapi.Cloud (desde la versión 2026-11-13). **No se pudo probar aquí**:
la preparación del servidor real (sección 2: firewall, SSH, Docker, swap) y la emisión del certificado con un dominio real.
Son pasos estándar de Ubuntu, pero revisa la salida de cada uno.

**Qué se instala en el servidor** (todo en contenedores Docker, con `docker compose`):

| Servicio | Para qué | ¿Visible desde internet? |
|---|---|---|
| `caddy` | HTTPS automático (Let's Encrypt) y puerta de entrada | Sí (80 y 443) |
| `app` | La aplicación (FastAPI + SQLite) | No, solo por Caddy |

WhatsApp no tiene contenedores propios: la app habla directo con Whapi.Cloud (servicio externo) y Whapi llama al webhook
`/api/whatsapp/webhook?token=...`, que por eso es público pero exige el secreto `WEBHOOK_TOKEN`.

**Servidor recomendado:** Ubuntu 24.04 LTS, 2 vCPU, 4 GB de RAM, 80 GB de disco. Región cercana a Ecuador (Miami, Atlanta o
Nueva York). Cuesta unos 20 a 24 USD al mes; confirma el precio en la página del proveedor. Activa los **respaldos del
proveedor** (suelen costar ~20 % más): son tu segunda red de seguridad.

---

## 1. Antes de empezar
- Una cuenta en el proveedor (la creas tú) y el servidor creado **con tu clave SSH**.
- Un **dominio** (o subdominio, ej. `bitacora.midominio.com`) con un registro **A** apuntando a la IP del servidor.
  Comprueba que ya resuelve: `dig +short bitacora.midominio.com` debe mostrar la IP. Hazlo **antes** de arrancar la app:
  si no, Let's Encrypt no podrá emitir el certificado.
- Tu clave de OpenAI y un celular/chip con WhatsApp para vincular (mejor un número dedicado: ver `CLAUDE.md`).

## 2. Preparar el servidor (una sola vez)
Conéctate como `root` (`ssh root@IP_DEL_SERVIDOR`) y ejecuta:

```bash
export DEBIAN_FRONTEND=noninteractive                # evita pantallas interactivas durante las actualizaciones
apt-get update && apt-get -y -o Dpkg::Options::="--force-confold" upgrade
timedatectl set-timezone America/Guayaquil          # horas de los registros y del cron en hora de Ecuador

# Usuario de trabajo (no uses root para el día a día). Sin contraseña: entra con la clave SSH y usa sudo sin clave
adduser --disabled-password --gecos "" bitacora
usermod -aG sudo bitacora
echo "bitacora ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-bitacora && chmod 440 /etc/sudoers.d/90-bitacora
mkdir -p /home/bitacora/.ssh && cp /root/.ssh/authorized_keys /home/bitacora/.ssh/authorized_keys
chown -R bitacora:bitacora /home/bitacora/.ssh && chmod 700 /home/bitacora/.ssh && chmod 600 /home/bitacora/.ssh/authorized_keys

# Firewall: solo SSH, web y HTTPS
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

# Memoria de respaldo (evita que un pico de uso tumbe la app)
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Docker (instalador oficial) y herramientas
curl -fsSL https://get.docker.com | sh
usermod -aG docker bitacora
apt install -y git qrencode fail2ban unattended-upgrades
```

Abre **otra terminal** y comprueba que entras con `ssh bitacora@IP_DEL_SERVIDOR`. Solo entonces, endurece SSH
(sin contraseñas ni acceso directo de root):

```bash
sudo tee /etc/ssh/sshd_config.d/99-endurecer.conf <<'EOF'
PasswordAuthentication no
PermitRootLogin no
EOF
sudo systemctl restart ssh
```

> Docker publica los puertos saltándose `ufw`. Por eso `docker-compose.prod.yml` solo publica 80 y 443 al público.
> No agregues más `ports:` sin pensarlo.

## 3. Traer el código y configurar
Ya como el usuario `bitacora`:

```bash
git clone https://github.com/Cristian921316/bitacora-ia.git
cd bitacora-ia
cp .env.prod.example .env
chmod 600 .env
```

Genera el secreto del webhook y pégalo en `.env` (el token de WhatsApp, `WHAPI_TOKEN`, lo da el panel de Whapi):

```bash
openssl rand -hex 16    # -> WEBHOOK_TOKEN
```

Edita `nano .env` y completa: `DOMINIO`, `OPENAI_API_KEY`, `ADMIN_USUARIO`, `ADMIN_PASSWORD` (clave temporal larga) y las tres
claves de arriba. Opcional: `ADMIN_TELEFONOS` (celulares de administradores, separados por coma) para pedir por WhatsApp «reporte de hoy / ayer / semana». Los valores faltantes hacen que `docker compose` falle con un mensaje claro en vez de arrancar mal.

## 4. Arrancar
```bash
docker compose up -d --build        # la primera vez tarda unos minutos
docker compose ps                   # todo debe estar "Up"; la app "healthy"
curl https://bitacora.midominio.com/salud     # debe responder {"ok":true}
```
Caddy obtiene el certificado solo. Si no responde: `docker compose logs caddy` (casi siempre es el DNS o el firewall).

## 5. Conectar WhatsApp (Whapi.Cloud)
1. En el panel de Whapi crea un canal y **vincula el número** escaneando el QR (en el celular: *WhatsApp → Ajustes →
   Dispositivos vinculados → Vincular un dispositivo*). Activa **Auto Download** en los ajustes del canal (los audios llegan con enlace).
2. Copia el **token del canal** en el `.env` del servidor: `WHAPI_TOKEN=...` (nunca en chats ni en git).
3. Obtén la URL del webhook y pégala en Whapi (Settings → Webhooks; modo **body**, evento **messages / post**):
```bash
./scripts/whatsapp.sh url           # imprime https://TU_DOMINIO/api/whatsapp/webhook?token=...
./scripts/whatsapp.sh webhook       # alternativa: la pone en el canal por API
```
4. Aplica el cambio y comprueba: `docker compose up -d --build`, `./scripts/whatsapp.sh estado`, y escríbele al número desde otro celular.

> Whapi no es la API oficial de Meta: se vincula como dispositivo (igual que WhatsApp Web) y el número puede desconectarse o
> bloquearse. Calienta el número (úsalo a mano los primeros días), no mandes mensajes masivos, y abre WhatsApp en el celular al
> menos cada 10–12 días (a los 14 sin abrirlo, WhatsApp desvincula los dispositivos).

## 6. Primer ingreso y puesta en marcha
1. Entra a `https://bitacora.midominio.com` con `ADMIN_USUARIO` / `ADMIN_PASSWORD`. Te obliga a cambiar la clave.
2. **Usuarios**: crea las cuentas de los guardias (rol *Guardia*).
3. **Residentes → Importar residentes**: sube el Excel (primero «Revisar archivo», luego confirma).
4. **Configuración**: revisa la vigencia (12 h) y el control de salidas (apagado).
5. Borra `ADMIN_PASSWORD` del `.env` y ejecuta `docker compose up -d` (ya no hace falta).
6. Prueba de punta a punta: un audio desde el celular de un residente, y míralo aparecer en la garita.

*(Opcional, no probado)* Si quieres llevar al servidor la base del piloto local, hazlo **antes** del paso 1:
`docker compose stop app && docker compose cp /ruta/bitacora.db app:/data/bitacora.db && docker compose run --rm --no-deps -u root app chown app:app /data/bitacora.db && docker compose start app`.
Lo recomendado es empezar limpio: la base local tiene datos de prueba.

## 7. Respaldos (no los omitas con datos reales)
```bash
./scripts/respaldo.sh               # copia consistente en el volumen de la app (conserva 14 días)
./scripts/restaurar.sh              # lista los respaldos
./scripts/restaurar.sh bitacora_20261108_023000.db      # restaura uno (pide confirmación)
```
`restaurar.sh` guarda antes una copia del estado actual (`pre_restauracion_*.db`) y no borra archivos.

**Automático cada noche**, con `crontab -e`:
```
30 2 * * * cd /home/bitacora/bitacora-ia && ./scripts/respaldo.sh 14 >> /home/bitacora/respaldo.log 2>&1
```
**Importante:** esos respaldos viven en el mismo servidor. Si el servidor se pierde, se pierden también. Por eso:
1. Activa los **respaldos/snapshots del proveedor**.
2. Copia los respaldos a otro lugar con regularidad. En el servidor: `docker compose cp app:/data/backups ./respaldos-copia`
   y luego, desde tu Mac: `scp -r bitacora@IP:bitacora-ia/respaldos-copia ./respaldos-servidor`
   (o usa `rclone` hacia un almacenamiento en la nube).
3. De vez en cuando **prueba restaurar** en una copia: un respaldo que nunca se probó no es una garantía.

## 8. Actualizar la aplicación
```bash
cd ~/bitacora-ia
./scripts/respaldo.sh                      # siempre, antes de actualizar
git pull
docker compose up -d --build               # las migraciones de la base se aplican solas al arrancar
docker compose ps && docker compose logs --tail 30 app
```
Para volver atrás: `git log --oneline`, `git checkout <commit_anterior>` y `docker compose up -d --build`
(si hubo una migración de la base, restaura también el respaldo previo).

## 9. Operación diaria
| Qué | Comando |
|---|---|
| Estado | `docker compose ps` |
| Registros de la app | `docker compose logs -f app` |
| Mensajes de WhatsApp que llegan | `docker compose logs -f app` (líneas `POST /api/whatsapp/webhook`) |
| Reiniciar la app | `docker compose restart app` |
| Espacio en disco | `df -h /` y `docker system df` |
| WhatsApp conectado | `./scripts/whatsapp.sh estado` |

Los registros rotan solos (máx. 30 MB por servicio). Configura un monitor externo gratuito (p. ej. UptimeRobot) sobre
`https://tu-dominio/salud` para enterarte si se cae.

## 10. Problemas frecuentes
- **No emite el certificado / el sitio no abre por HTTPS:** el DNS no apunta aún al servidor, o el firewall bloquea el 80/443.
  `dig +short tu-dominio`, `sudo ufw status`, `docker compose logs caddy`.
- **WhatsApp aparece desconectado:** `./scripts/whatsapp.sh estado`; si no está autenticado, vuelve a escanear el QR en el panel de Whapi.
- **Error 502:** la app está caída o arrancando: `docker compose ps` y `docker compose logs --tail 50 app`.
- **«Falta … en el archivo .env»:** completa esa variable en `.env` y vuelve a ejecutar `docker compose up -d`.
- **Un guardia no puede entrar:** *Usuarios* → restablecer clave. Si se bloqueó por intentos, espera 5 minutos.

## 11. Seguridad: lista de verificación
- [ ] SSH solo con clave; `root` sin acceso; `fail2ban` activo.
- [ ] `ufw` activo con solo 22, 80 y 443.
- [ ] `.env` con permisos `600`, fuera de git, y sin `ADMIN_PASSWORD` después del primer ingreso.
- [ ] Respaldos del proveedor activados y una copia fuera del servidor.
- [ ] Actualizaciones de seguridad automáticas (`unattended-upgrades`) y `apt upgrade` de vez en cuando.
- [ ] Un monitor externo sobre `/salud`.

## 12. Límites conocidos (y cómo se supera cada uno)
- **Una sola copia de la app:** los avisos en vivo viven en memoria. Se escala subiendo el tamaño del servidor.
  Para varias copias hará falta PostgreSQL y un canal compartido (Redis); el código ya está preparado para el cambio de base.
- **SQLite:** suficiente para el piloto. Migrar a PostgreSQL cuando se decida (`scripts/migrar_a_postgres.py`).
- **Estilos desde un CDN (Tailwind):** las pantallas cargan los estilos desde internet. Si ese servicio fallara se verían sin
  formato. Pendiente: compilar los estilos dentro de la app.
- **WhatsApp no oficial (Whapi):** riesgo de desconexión o bloqueo del número. El plan es pasar a la API oficial de Meta (p. ej. YCloud) cuando el piloto lo requiera.
