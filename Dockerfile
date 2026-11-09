FROM python:3.12-slim

# Hora de Ecuador: las visitas y el reporte se guardan con la hora local del servidor
ENV TZ=America/Guayaquil \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements.lock* ./
# Con requirements.lock las versiones quedan fijas y cada despliegue es idéntico
RUN pip install --no-cache-dir -r "$( [ -f requirements.lock ] && echo requirements.lock || echo requirements.txt )"

COPY . .

# La app no corre como root. /data guarda la base de datos (se monta como volumen)
RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown -R app:app /data
USER app
ENV DATABASE_URL=sqlite:////data/bitacora.db

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/salud', timeout=4)"

# Un solo proceso: los avisos en vivo viven en memoria. --proxy-headers para ver la IP real detrás de Caddy
# (necesario para que el bloqueo por intentos de login se aplique por IP y no a todos a la vez)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
