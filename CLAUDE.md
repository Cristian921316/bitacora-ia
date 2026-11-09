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
| `alembic/` | Migraciones del esquema (`0001` base del piloto, `0002` usuarios/pagos, `0003` configuración, `0004` residentes con id/cédula/celular opcional) |
| `auth.py` | Login, sesiones en BD, roles ADMIN / GUARDIA, gestión de usuarios |
| `realtime.py` | WebSocket; al guardia se le envían solo los campos mínimos |
| `reportes.py` | Reportes (solo ADMIN): pantalla, CSV y Excel |
| `static/` | Pantallas HTML (Tailwind CDN + JS); `static/assets/nav.js` = menú según el rol |
| `scripts/migrar_a_postgres.py` | Copia los datos de SQLite a PostgreSQL |
| `tests/` | 55 pruebas automáticas (`pytest`, SQLite) |
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

## Importar residentes
- Residentes → **Importar residentes (Excel)**. Columnas: Cédula, Nombre, Mz, Villa, Estado de pago y **Celular** (obligatorios:
  Nombre, Mz, Villa y Celular). Plantilla descargable. Acepta .xlsx y .csv (máx. 5 MB / 5000 filas).
- Dos pasos: primero **revisa** (no guarda nada y muestra nuevos, actualizados, cambios de pago, errores y avisos por fila) y luego **confirma**.
- Reconoce a la persona por cédula, luego por celular (no duplica al volver a subir). Solo agrega o actualiza: las casas que no
  aparecen en el archivo no se tocan. El estado de pago es por casa; si hay estados distintos en una casa, queda IMPAGA.
- Filas con error se omiten y se explican (celular repetido, cédula distinta para el mismo celular, estado no reconocido, etc.).
- Excel quita ceros iniciales: las cédulas de 9 dígitos se completan y los celulares 9XXXXXXXX pasan a 593 9XXXXXXXX.

## Vigencia de las visitas
- Parámetro `vigencia_horas` (por defecto 12, editable en **Configuración**): una visita PRE_AUTORIZADA o PENDIENTE sin ingresar
  deja de mostrarse en la garita y ya no se puede ingresar pasado ese tiempo desde su registro. Se **calcula al consultar**
  (no hay estado guardado ni tarea programada): cambiar el parámetro aplica a todo al instante. En el reporte sale como «Vencida».
- Quien sigue ADENTRO desde un día anterior se muestra en rojo, arriba y con un contador «desde ayer»; nunca se oculta.
- La garita usa la hora del servidor (`/api/config`) para los «hace N min» y las vigencias, no la del navegador.
- Limitación conocida: «mañana viene X» se vence a las 12 h porque la fecha de la visita no se extrae del mensaje.

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

## Pendientes / ideas
- **Producción**: HTTPS y dominio, zona horaria de Ecuador en el servidor, respaldo automático, monitoreo, contenedor de la app.
- Extraer la fecha de la visita («mañana», «el viernes») para que no se venza antes de llegar.
- Permisos automáticos para técnicos (el residente los pide; se validan contra el estado de pago; código que el guardia verifica).
- Accesos excepcionales con motivo y auditoría; botón de emergencia para el guardia.
- Subir el corte de pagos desde Excel; importar residentes desde Excel.
- WhatsApp: modo silencioso, pausa si responde un humano, tope de mensajes por hora; pasar a la API oficial de Meta.
- Varios conjuntos: `conjunto_id` en las tablas, líneas de WhatsApp y parámetros por conjunto, panel de super-admin.
- Safari graba audio en MP4 pero el frontend lo envía como `.webm` (usar Chrome en la pantalla de demostración).
