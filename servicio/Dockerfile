# Servicio RomsLibrary: API de la app, gestor web, federación y almacenamiento.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ROMSLIBRARY_DATOS=/datos

# UID/GID configurables para que los ficheros del volumen sean del usuario
# del host (en jarjar, 1000).
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} romslibrary && useradd -u ${UID} -g ${GID} -m romslibrary \
    && mkdir -p /datos && chown romslibrary:romslibrary /datos

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .

USER romslibrary
VOLUME /datos
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health')"

# Sin log de accesos propio: lo lleva el proxy (y las URLs de la app llevan
# credenciales en la query, que no deben quedar en ningún log).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
