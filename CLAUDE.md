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

El MVP está **completo y probado** (flujo de voz real con Whisper + extracción, WebSocket, `/residente` y `/garita`).

## Estructura
- `main.py`: backend FastAPI (SQLite, WebSocket `/ws/bitacora`, `/api/procesar-voz`, `/api/cambiar-estado/{id}`, `/api/accesos`, rutas `/`, `/residente`, `/garita`).
- `static/residente.html` y `static/garita.html`: frontend (Tailwind por CDN + JS vanilla).
- `requirements.txt`, `.env.example`, `.gitignore`.
- `bitacora.db` se crea sola al arrancar (no está en el repo) y se siembra con 3 registros si la tabla está vacía.

## Cómo correrlo
macOS/Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # y pegar OPENAI_API_KEY (clave secreta sk-..., NO el Tracking ID)
uvicorn main:app --host 0.0.0.0 --port 8000
```
Windows: `.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000`.
Abrir `http://localhost:8000/residente` y `http://localhost:8000/garita`.

## Decisiones tomadas
- Extracción de visitas con `gpt-4o-mini` vía structured outputs (Pydantic); configurable con la variable `EXTRACTION_MODEL` en `.env`. Transcripción con `whisper-1` (language="es").
- Transiciones de estado permitidas: PRE_AUTORIZADO → ADENTRO o SALIO, y ADENTRO → SALIO. Cualquier otra devuelve 409.
- El micrófono del navegador solo funciona en `localhost` o HTTPS (no por IP de red en HTTP).
- El beep de la garita requiere pulsar antes "Activar sonido" (política de autoplay del navegador).
- El `.env` nunca se sube al repo (contiene la clave de OpenAI).

## Pendientes / ideas
- Safari graba audio en MP4 pero el frontend lo envía con nombre `.webm`; detectar el formato real (`recorder.mimeType`) y ajustar la extensión. Usar Chrome mientras tanto.
- HTTPS local (certificado autofirmado) o túnel para probar el micrófono desde el celular.
- Autenticación: hoy cualquiera puede abrir `/garita` y cambiar estados.
