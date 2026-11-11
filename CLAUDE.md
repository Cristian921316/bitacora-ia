# MVP Bitácora Residencial por Voz (FastAPI + WebSockets + SQLite)

Construye una aplicación monolítica ultraligera en Python con FastAPI que incluya:
1. **Base de Datos SQLite (`bitacora.db`)**: Con una tabla `accesos` (`id`, `casa`, `nombre_visitante`, `tipo`, `placa`, `observaciones`, `estado`: 'PRE_AUTORIZADO'/'ADENTRO'/'SALIO', `autorizado_por`, `fecha_hora`). Al arrancar, inserta 3 registros de prueba.
2. **Endpoint de IA (`POST /api/procesar-voz`)**: Recibe un archivo de audio (`.webm`/`.wav`), lo transcribe con Whisper (`whisper-1`) y extrae un JSON estructurado con Pydantic permitiendo múltiples visitas en un solo audio (campos: `casa`, `nombre_visitante`, `tipo` [VISITA, DELIVERY, TAXI_UBER, SERVICIO_TECNICO], `placa`, `observaciones`). Guarda en SQLite y hace broadcast por WebSocket.
3. **Gestión de Estados (`POST /api/cambiar-estado/{id}`)**: Permite cambiar el estado de una visita (de PRE_AUTORIZADO a ADENTRO o SALIO) y notifica por WebSocket.
4. **WebSocket (`/ws/bitacora`)**: Canal en tiempo real para empujar actualizaciones a los clientes conectados.
5. **Frontend Integrado (HTML + Tailwind CSS + Vanilla JS)**:
   - `/residente`: Pantalla móvil amigable con un botón de grabación de micrófono (*Push-to-Talk*) para mandar notas de voz y ver qué pre-autorizó.
   - `/garita`: Monitor de control de acceso en dos columnas ("Pre-autorizados por llegar" y "Adentro del Conjunto"), con un botón de un clic para dar ingreso/salida y reproducción de un beep por Web Audio API al recibir eventos en tiempo real.
---

# Estado actual del proyecto

Piloto funcional: visitas por voz o texto (web y WhatsApp), garita en tiempo real, visitas sin aviso con autorización
del residente por WhatsApp, reportes en Excel, login con 2 roles y estado de pago por casa.

## Estructura
| Archivo | Para qué |
|---|---|
| `main.py` | Rutas: visitas, residentes, estado de pago, WhatsApp (Evolution API), pantallas |
| `parametros.py` | Parámetros que el admin cambia en `/configuracion` (vigencia, control de salidas, tiempo de rechazo) y cálculo de «Vencida» |
| `importar.py` | Importar residentes desde Excel/CSV (revisar y confirmar) y plantilla descargable |
| `util.py` | Utilidades (normalizar celulares) |
| `config.py` | Ajustes desde variables de entorno / `.env` |
| `db.py` | Base de datos portable (SQLAlchemy Core): tablas, helpers y `init_db()` |
| `alembic/` | Migraciones del esquema (`0001` base del piloto, `0002` usuarios/pagos, `0003` configuración, `0004` residentes con id/cédula/celular opcional, `0005` cédula sin unicidad y casa de hasta 200 caracteres) |
| `auth.py` | Login, sesiones en BD, roles ADMIN / GUARDIA, gestión de usuarios |
| `realtime.py` | WebSocket; al guardia se le envían solo los campos mínimos |
| `reportes.py` | Reportes (solo ADMIN): pantalla, CSV y Excel |
| `static/` | Pantallas HTML (Tailwind CDN + JS); `static/assets/nav.js` = menú según el rol |
| `scripts/migrar_a_postgres.py` | Copia los datos de SQLite a PostgreSQL |
| `tests/` | 75 pruebas automáticas (`pytest`, SQLite) |
| `docker-compose.yml` | Evolution API (WhatsApp no oficial) + Postgres + Redis propios de Evolution |

## Cómo correrlo
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # completar OPENAI_API_KEY, ADMIN_USUARIO y ADMIN_PASSWORD (clave temporal)
uvicorn main:app --host 0.0.0.0 --port 8000
```
Entrar a `http://localhost:8000` con el administrador inicial: debe cambiar la clave en su primer ingreso. Luego, en
**Usuarios**, crear las cuentas de los guardias. `ADMIN_USUARIO/ADMIN_PASSWORD` solo se usan si no existe ningún usuario;
después se pueden borrar del `.env`.

Pruebas: `pip install -r requirements-dev.txt && pytest`.

## Roles
- **ADMIN**: menú completo (Garita, Reporte, Residentes, Usuarios, Configuración, Pantalla residente). Puede hacer excepciones de ingreso.
- **GUARDIA**: solo `/garita`, sin menú. No ve teléfonos, textos de audios, reportes ni estado de pagos.
- Sesión por cookie `HttpOnly` guardada en BD (hash del token); 5 intentos fallidos bloquean 5 min; claves PBKDF2-SHA256.
- Con HTTPS poner `COOKIE_SECURE=1`.

## Estado de pago
- Se guarda **por casa** (tabla `casas`; una casa puede tener varios celulares). Botón Al día / Impago en **Residentes**.
- Con `BLOQUEO_POR_MORA=1` (por defecto) una casa impaga no puede: anunciar visitas por WhatsApp, registrar visitas sin aviso
  ni recibir ingresos. Solo un ADMIN puede forzar un ingreso. Los mensajes al guardia/residente son neutros (no dicen "pago").
- Cada intento bloqueado queda en el reporte, vista **Intentos bloqueados**.
- Pendiente: subir el corte de pagos desde un archivo; historial de quién cambió el estado de cada casa.

## Control de salidas (apagado en el piloto)
- Parámetro `control_salidas` (Configuración, por defecto **No**): la garita solo muestra *Esperando autorización* y *Pre-autorizados
  por llegar*. Al dar ingreso la visita sale de la pantalla; el ingreso queda registrado y el residente recibe su aviso.
- Sin control de salidas, el reporte muestra el estado como «INGRESÓ» (no se sabe si ya se fue). Encendiéndolo vuelven la columna
  «Adentro del Conjunto», el botón de salida y la alerta roja de quienes siguen adentro desde ayer.

## Importar y editar residentes
- Residentes → **Importar residentes (Excel)**. Columnas: Cédula, Nombre, Mz, Villa, Estado de pago y Celular.
  **Obligatorias: Nombre, Mz, Villa y Estado de pago** (una fila puede traer el estado vacío = «sin cambio»; una casa nueva queda al día).
  **Cédula y Celular son opcionales y no se validan**: la cédula puede repetirse; un celular inservible (texto, muy corto o largo)
  o repetido por otra persona se carga **sin celular** y deja un aviso. Mz y Villa admiten hasta 50 caracteres. Plantilla descargable.
- Solo es error de fila: falta el nombre, la manzana o la villa, o un estado de pago ilegible (leerlo mal podría bloquear a una casa
  que sí pagó). Dos pasos: **revisar** (no guarda nada) y **confirmar**. Las filas con error se omiten y se explican.
- Cómo reconoce a una persona al volver a subir: por **celular con el mismo nombre**; si no hay celular, por **nombre + casa**
  (si ya estaba sin celular y ahora lo trae, se le completa). La cédula NO identifica a nadie. Nunca se pisa a otra persona:
  si el celular ya lo tiene alguien con otro nombre (pareja que comparte teléfono), la nueva persona se carga sin celular.
- Solo agrega o actualiza: las casas que no aparecen en el archivo no se tocan. Estado de pago por casa: si hay estados distintos
  en una casa, queda IMPAGA. Excel quita ceros iniciales: las cédulas de 9 dígitos se completan y los celulares 9XXXXXXXX pasan a 593 9XXXXXXXX.
- **Edición en línea** en Residentes: clic en nombre, cédula, celular o casa; **Enter** guarda, **Esc** cancela, **Tab** pasa al siguiente
  dato de la fila; la celda parpadea en verde (guardado) o rojo (error con el motivo). La casa se edita con dos casillas (Mz y Villa).
  API: `PATCH /api/residentes/{id}` (cambios parciales; el celular se valida y no puede repetirse; vaciarlo lo quita).
- El alta manual también admite celular vacío (se completa luego con un clic).
- Pendiente: unificar `06` con `6` y `12A` con `12-A` (hoy son casas distintas).

## Vigencia de las visitas
- Parámetro `vigencia_horas` (por defecto 12, editable en **Configuración**): una visita PRE_AUTORIZADA o PENDIENTE sin ingresar
  deja de mostrarse en la garita y ya no se puede ingresar pasado ese tiempo desde su registro. Se **calcula al consultar**
  (no hay estado guardado ni tarea programada): cambiar el parámetro aplica a todo al instante. En el reporte sale como «Vencida».
- Quien sigue ADENTRO desde un día anterior se muestra en rojo, arriba y con un contador «desde ayer»; nunca se oculta.
- La garita usa la hora del servidor (`/api/config`) para los «hace N min» y las vigencias, no la del navegador.
- Limitación conocida: «mañana viene X» se vence a las 12 h porque la fecha de la visita no se extrae del mensaje.

## Despliegue (VPS con Docker)
- Guía paso a paso: `docs/DESPLIEGUE.md`. Archivos: `Dockerfile`, `docker-compose.prod.yml`, `deploy/Caddyfile`,
  `.env.prod.example`, `requirements.lock` (versiones exactas probadas; las directas coinciden con las que funcionaron con
  OpenAI real, p. ej. `openai==2.48.0`).
- Stack: Caddy (HTTPS automático) + app (FastAPI + SQLite en el volumen `/data`) + Evolution API (+ su Postgres y Redis).
  Solo se publican 80/443; Evolution queda en `127.0.0.1` y el webhook de WhatsApp es solo interno (Caddy lo bloquea).
- Scripts (en el servidor): `scripts/whatsapp.sh` (crear/qr/estado/salir), `scripts/respaldo.sh`, `scripts/restaurar.sh`.
  Ninguno borra archivos; la restauración usa la API de respaldo de SQLite y guarda antes el estado actual.
- La app corre con `--proxy-headers` (IP real detrás de Caddy, necesaria para el bloqueo de login por IP), un solo proceso,
  usuario sin privilegios y `TZ=America/Guayaquil`.
- Pendiente: compilar Tailwind en vez de usar el CDN; respaldo automático (el script existe, falta activar el cron).

## Base de datos y migración a una más robusta
- Hoy SQLite (`bitacora.db`, modo WAL). Para cambiar de motor basta `DATABASE_URL=postgresql://usuario:clave@servidor:5432/bitacora`:
  el código usa SQLAlchemy Core y el esquema lo crea Alembic (`init_db()` corre en cada arranque).
- Migrar los datos (app detenida, destino vacío):
  `DATABASE_URL=postgresql://... .venv/bin/python scripts/migrar_a_postgres.py --origen sqlite:///bitacora.db`
  Crea el esquema, copia las tablas, deja los contadores de ID y compara los conteos. Probado con una copia de la base real.
- Cambios de esquema futuros: `alembic revision -m "descripcion"` y escribir la migración en `alembic/versions/`.
- Las fechas son texto ISO local del servidor; en Postgres se pueden pasar a `timestamp` con una migración.
- Respaldos del piloto: carpeta `backups/` (ignorada por git). **Aún no hay respaldo automático.**

## Decisiones tomadas
- Extracción con `gpt-4o-mini` (structured outputs, configurable con `EXTRACTION_MODEL`); transcripción con `whisper-1`.
- Estados de una visita: PENDIENTE (sin aviso, espera al residente), PRE_AUTORIZADO, ADENTRO, SALIO, RECHAZADO.
- La garita carga solo lo abierto (pendientes, por llegar, adentro y rechazos de los últimos 30 min); el historial vive en el reporte.
- `/residente` (voz/texto por web) quedó como pantalla de demostración solo para ADMIN: los residentes usan WhatsApp.
- El micrófono del navegador solo funciona en `localhost` o HTTPS.
- El `.env` y la base nunca se suben al repo.

## Visita sin aviso (garita)
- Es una ayuda para casos fortuitos (el 90 % lo anuncia el residente): hay que saber a QUIÉN se visita. Búsqueda por Mz y Villa tolerante
  (1 = 01, 12A = 12-A). Una casa → sus residentes; sin casa que coincida NO se puede registrar (400); casa no al día → panel rojo
  «El residente de … no está al día en sus pagos. Debe comunicarse con administración» (nunca la visita), sin nombres, envío bloqueado (409
  y queda en «Intentos bloqueados»). «Registrar igual» solo si la casa existe pero nadie tiene celular válido. Sin conexión: no deja registrar.
- La visita se guarda con la casa del residente elegido tal como está guardada; `resolver_solicitud` compara la casa sin distinguir mayúsculas.

## Resumen por WhatsApp para administradores
- `ADMIN_TELEFONOS` (.env, coma, ej. `0991234567,0987654321`) = celulares que pueden escribir o enviar nota de voz al número de la bitácora:
  «reporte de hoy / de ayer / de la semana» (o solo «hoy», «ayer», «semana»). Responde solo cifras (visitas, ingresaron, rechazadas,
  sin ingresar, sin aviso en garita, bloqueos, por tipo), sin nombres ni casas. Código: `resumen.py` + intercepción en el webhook.
- Exige palabra de pedido (reporte/resumen/informe/cuántas…) para que «hoy viene Juan» siga siendo un anuncio; un admin que también es
  residente puede seguir anunciando visitas. Un número que no está en la lista nunca recibe el resumen.
- En producción: agregar `ADMIN_TELEFONOS` al `.env` del servidor y `docker compose up -d`.

## Estado de ramas y despliegue (actualizado 2026-11-11)
- `main` (GitHub, repo PÚBLICO) incluye el piloto, el paquete de despliegue y el PR #3 (importador relajado, edición en línea, migración 0005,
  visita sin aviso con casa/mora/búsqueda tolerante, resumen por WhatsApp para admins). 97 pruebas pasan. `serverRun.txt` NUNCA se sube.
- **PRODUCCIÓN YA DESPLEGADA con el PR #3**, con dominio propio (subdominio `bitacora.` de un dominio en Hostinger, registro A → IP del droplet;
  el dominio concreto va solo en el `.env` del servidor, no en el repo público). WhatsApp re-vinculado con QR nuevo y funcionando;
  `ADMIN_TELEFONOS` configurado y probado. El dominio `sslip.io` se dejó porque algunos equipos lo bloqueaban.
- Para futuras actualizaciones: `./scripts/respaldo.sh` → `git pull` → `docker compose up -d --build` → `docker compose ps` y logs →
  `./scripts/whatsapp.sh estado`. Cambiar de dominio = editar `DOMINIO` en el `.env` + `docker compose up -d` (los usuarios deben volver a entrar).
  Reversa: `git checkout <commit_anterior>` + `docker compose up -d --build`; si hubo migración, restaurar el respaldo previo.
- Producción: droplet DigitalOcean NYC (2 vCPU / 2 GB, se recomienda 4 GB), SQLite en el volumen `app_data`, Evolution + su Postgres/Redis.
- Sin hacer aún: cargar la lista real en producción («Revisar archivo» y confirmar, con respaldo antes), respaldo nocturno (cron),
  subir a 4 GB, endurecer SSH, borrar ADMIN_PASSWORD del .env, monitor externo de /salud, snapshots del proveedor.

## Entorno local y precauciones
- La base local (`bitacora.db`) tiene la lista REAL (784 residentes, 467 con celular) y WhatsApp local vinculado a un número propio:
  NO enviar solicitudes a casas reales; probar con una casa propia (p. ej. Mz 99 Villa 99 con un segundo teléfono).
  Idea sin decidir: lista blanca de números en local.
- Reiniciar el servidor local tras cambios de backend (uvicorn no recarga solo): una pantalla nueva con backend viejo parece «no encuentra».
- La base usa WAL: copiarla con `sqlite3 bitacora.db ".backup 'destino'"`, NO con `cp` (sale desactualizada). Los scripts de producción ya usan la API correcta.
- La lista real mezcla ceros iniciales en Mz/Villa (~40 % de las casas): la búsqueda es tolerante (06 = 6, 12A = 12-A = 12 a).
- Pruebas: `pytest` en SQLite (rápidas). Postgres solo cuando el usuario lo pida.

## Pendientes / ideas
- **Búsqueda por nombre del residente** en visita sin aviso: pospuesta (la lista sería enorme). Diseño hablado: mínimo 3 letras,
  máximo 8 resultados, solo nombres, historial de búsquedas, casa visible junto al nombre.
- Despliegue continuo en 3 etapas (script con reversión, CI en GitHub, CD con aprobación): propuesto, sin elegir.
- Migrar a PostgreSQL administrado DESPUÉS del piloto (solo cuando se pida; scripts y pruebas ya existen).
- Permisos automáticos para técnicos, accesos excepcionales con auditoría, botón de emergencia, subir el corte de pagos,
  modo silencioso y topes de WhatsApp, API oficial de Meta, varios conjuntos, extraer la fecha de la visita («mañana»),
  compilar Tailwind (hoy CDN), respaldo automático activo.
- Safari graba audio en MP4 pero el frontend lo envía como `.webm` (usar Chrome en la pantalla de demostración).
