"""
Configuración del servicio RomsLibrary, toda por variables de entorno (ver
docker-compose.yml y README). Todos los datos viven bajo un único
directorio (un volumen en Docker):

    /datos/ROMs/<sistema>/...       juegos
    /datos/covers/<sistema>/...     carátulas subidas a mano
    /datos/anteriores/<sha>         última versión limpia de los juegos actualizados (versiones.py)
    /datos/apk/                     APK de la app (opcional, ver ajustes de APK)
    /datos/tmp/                     temporales grandes (subidas, zips)
    /datos/romslibrary.db           SQLite: ajustes, federación, hashes, versiones
"""

import os
import tempfile
from pathlib import Path


def _bool(nombre: str, defecto: bool) -> bool:
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in ("1", "true", "si", "sí", "yes", "on")


DATOS = Path(os.environ.get("ROMSLIBRARY_DATOS", "/datos"))
ROOT = DATOS / "ROMs"
COVERS_ROOT = DATOS / "covers"
APK_DIR = DATOS / "apk"
TMP = DATOS / "tmp"
DB_PATH = DATOS / "romslibrary.db"

# APK de la app: si este servicio no publica la suya (lo normal), la pide al
# servicio de origen (sund.es mantiene siempre la oficial y pública).
APK_PATH = APK_DIR / "RomsLibrary.apk"
APK_VERSION = APK_DIR / "version.json"
# App para consolas Linux, Steam Deck y Windows (cliente/ del monorepo): un
# paquete por plataforma y version.json con su versión y SHA-256 (lo escribe
# cliente/empaquetado/publicar.sh). Solo lo publica el servicio de origen.
CLIENTE_DIR = DATOS / "cliente"
# App para Wii (Homebrew Channel): boot.dol, meta.xml, icon.png y el zip de instalación.
WII_DIR = DATOS / "wii"
APK_ORIGEN = os.environ.get("ROMSLIBRARY_APK_ORIGEN", "https://sund.es").rstrip("/")

# Gestor web: lo protege Authentik delante (proxy inverso con forward-auth).
# El proxy añade el usuario autenticado en CABECERA_USUARIO y un secreto
# compartido en X-RomsLibrary-Proxy: sin ese secreto no se confía en la
# cabecera de usuario (si no, cualquiera que llegase al puerto del servicio
# sin pasar por el proxy podría hacerse pasar por administrador).
SECRETO_PROXY = os.environ.get("ROMSLIBRARY_SECRETO_PROXY", "")
CABECERA_USUARIO = os.environ.get("ROMSLIBRARY_CABECERA_USUARIO", "X-authentik-username")

# API interna antigua (/api/*), solo para la transición de sund.es mientras
# el PHP de Contabo siga en medio. Vacío = desactivada.
TOKEN_INTERNO = os.environ.get("ROMSLIBRARY_TOKEN_INTERNO", "")

# Federación de librerías públicas. El índice es un servicio APARTE (indice.py)
# que mantiene la lista de librerías públicas; el oficial está en sund.es.
INDICE_URL = os.environ.get("ROMSLIBRARY_INDICE_URL", "https://sund.es").rstrip("/")

# Ajustes iniciales (luego se cambian desde el gestor y viven en la BD).
NOMBRE_INICIAL = os.environ.get("ROMSLIBRARY_NOMBRE", "Mi RomsLibrary")
URL_PUBLICA_INICIAL = os.environ.get("ROMSLIBRARY_URL_PUBLICA", "").rstrip("/")
CLAVE_APP_INICIAL = os.environ.get("ROMSLIBRARY_CLAVE_APP", "")
# Toda librería nace PRIVADA; hacerla pública es una decisión explícita
# desde el gestor (con su aviso legal). Esto solo cambia el valor inicial.
PUBLICO_INICIAL = _bool("ROMSLIBRARY_PUBLICO", False)

# Uso de disco: categorías extra a mostrar en la barra del gestor, con el
# formato "Películas=/media/peliculas,Series=/media/series".
DISCO_EXTRA = [
    (parte.split("=", 1)[0].strip(), Path(parte.split("=", 1)[1].strip()))
    for parte in os.environ.get("ROMSLIBRARY_DISCO_EXTRA", "").split(",")
    if "=" in parte
]

for _carpeta in (ROOT, COVERS_ROOT, APK_DIR, TMP):
    _carpeta.mkdir(parents=True, exist_ok=True)

# Subidas y zips de varios GB: nunca en /tmp (puede ser tmpfs en RAM; en
# jarjar lo era y las subidas de Switch fallaban al llenarse).
tempfile.tempdir = str(TMP)
