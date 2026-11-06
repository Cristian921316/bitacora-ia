# MVP Bitácora Residencial por Voz (FastAPI + WebSockets + SQLite)

Construye una aplicación monolítica ultraligera en Python con FastAPI que incluya:
1. **Base de Datos SQLite (`bitacora.db`)**: Con una tabla `accesos` (`id`, `casa`, `nombre_visitante`, `tipo`, `placa`, `observaciones`, `estado`: 'PRE_AUTORIZADO'/'ADENTRO'/'SALIO', `autorizado_por`, `fecha_hora`). Al arrancar, inserta 3 registros de prueba.
2. **Endpoint de IA (`POST /api/procesar-voz`)**: Recibe un archivo de audio (`.webm`/`.wav`), lo transcribe con Whisper (`whisper-1`) y extrae un JSON estructurado con Pydantic permitiendo múltiples visitas en un solo audio (campos: `casa`, `nombre_visitante`, `tipo` [VISITA, DELIVERY, TAXI_UBER, SERVICIO_TECNICO], `placa`, `observaciones`). Guarda en SQLite y hace broadcast por WebSocket.
3. **Gestión de Estados (`POST /api/cambiar-estado/{id}`)**: Permite cambiar el estado de una visita (de PRE_AUTORIZADO a ADENTRO o SALIO) y notifica por WebSocket.
4. **WebSocket (`/ws/bitacora`)**: Canal en tiempo real para empujar actualizaciones a los clientes conectados.
5. **Frontend Integrado (HTML + Tailwind CSS + Vanilla JS)**:
   - `/residente`: Pantalla móvil amigable con un botón de grabación de micrófono (*Push-to-Talk*) para mandar notas de voz y ver qué pre-autorizó.
   - `/garita`: Monitor de control de acceso en dos columnas ("Pre-autorizados por llegar" y "Adentro del Conjunto"), con un botón de un clic para dar ingreso/salida y reproducción de un beep por Web Audio API al recibir eventos en tiempo real.