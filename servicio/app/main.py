"""
Servidor de librería de ROMs personal (jarjar). Sirve únicamente por Tailscale
(bind explícito a la IP tailscale0, nunca 0.0.0.0) para las 2 Retroid del
usuario. Estructura de carpetas idéntica a la que espera ES-DE en el
dispositivo: ROMs/<sistema>/juego.ext -- así el indexado es un espejo directo.
"""

import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from starlette.background import BackgroundTask

import accesos
import ajustes
import config

def extension_real_de_imagen(cabecera: bytes) -> Optional[str]:
    """Determina la extensión mirando los bytes de verdad, NUNCA el nombre de
    fichero que declara el cliente -- ese nombre es fácil de tener mal (bug
    real encontrado: una carátula JPEG real guardada como "....png" porque
    algo por el camino confió en el nombre declarado en vez del contenido).
    WEBP se sigue detectando aquí (hace falta para saber cuándo convertir en
    guardar_caratula), pero nunca es el resultado final guardado en disco --
    decisión del usuario: solo se admiten PNG/JPG."""
    if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if cabecera.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
        return ".webp"
    return None


def guardar_caratula(contenido: bytes, carpeta_caratulas: Path, stem: str) -> str:
    """Guarda una carátula ya descargada por completo en memoria (las
    carátulas pesan como mucho unos pocos MB, nunca como un ROM -- bufferar
    entero es seguro y hace falta para poder decodificarla con Pillow).
    WEBP se convierte SIEMPRE a PNG antes de guardar (decisión del usuario:
    WEBP ya no se admite nunca como resultado final) -- PNG y no JPEG para no
    arriesgarse a una segunda pasada con pérdida encima de un WEBP que ya
    podía ser con pérdida él mismo, y porque las carátulas (colores planos,
    texto, logos) son precisamente el caso donde PNG comprime bien sin
    perder nitidez. Lanza ValueError si el contenido no es una imagen
    reconocible."""
    ext = extension_real_de_imagen(contenido[:16])
    if ext is None:
        raise ValueError("Formato de imagen no reconocible.")
    carpeta_caratulas.mkdir(parents=True, exist_ok=True)
    if ext == ".webp":
        imagen = Image.open(io.BytesIO(contenido))
        imagen.load()  # RIFF/WEBP es perezoso -- fuerza la decodificación antes de que el BytesIO salga de alcance.
        destino = carpeta_caratulas / (stem + ".png")
        imagen.save(destino, format="PNG")
    else:
        destino = carpeta_caratulas / (stem + ext)
        destino.write_bytes(contenido)
    return destino.name


ROOT = config.ROOT
COVERS_ROOT = config.COVERS_ROOT
APK_PATH = config.APK_PATH
EXTENSIONES_CARATULA_PERMITIDAS = {".jpg", ".jpeg", ".png", ".webp"}

# Extensiones reales aceptadas por sistema (no la convención artificial
# ".<sistema>" usada antes). La mayoría van por núcleos de RetroArch, que
# aceptan el ROM en crudo o comprimido en .zip; PS1/PS2 siempre en .zip;
# Switch usa sus formatos reales (NSP/XCI), ".switch" no existe de verdad.
EXTENSIONES_POR_SISTEMA = {
    "gb": {".gb", ".zip"},
    "gbc": {".gbc", ".zip"},
    "gba": {".gba", ".zip"},
    "n64": {".n64", ".z64", ".v64", ".zip"},
    "gc": {".iso", ".gcm", ".rvz", ".zip"},
    # Wii (USB Loader GX): .wbfs o .iso. Un .wbfs de más de 4 GB va partido
    # (.wbfs + .wbf1, .wbf2...) como juego de varios ficheros.
    "wii": {".wbfs", ".wbf1", ".wbf2", ".wbf3", ".iso"},
    "switch": {".nsp", ".xci"},
    "psx": {".bin", ".cue", ".chd", ".zip"},
    "ps2": {".iso", ".chd", ".zip"},
    "psp": {".iso", ".cso", ".zip"},
    # Juegos de RPG Maker (XP/VX/VXAce/MV/MZ, vía JoiPlay en el dispositivo,
    # no ES-DE): no son una ROM, son una carpeta completa de juego
    # (Game.exe + Data/ + Graphics/ + Audio/...). El staff sube un .zip de
    # esa carpeta ya comprimida; se descomprime aquí mismo -- ver subir_juego.
    "rpgmaker": {".zip"},
}

# Extensiones "de disco" (cargables directamente por un core) -- son las que
# entran en el .m3u multidisco. Los .bin de PS1 NO entran aquí a propósito:
# son pistas referenciadas POR un .cue, nunca se cargan sueltas.
EXTENSIONES_DISCO = {".cue", ".iso", ".chd"}

# Roles válidos para un grupo de Switch (base + updates/DLC opcionales).
# A diferencia del multidisco (orden por extensión+alfabético), aquí hace
# falta saber explícitamente qué es cada fichero -- un update y un DLC
# pueden ser el mismo .nsp sin forma de distinguirlos por nombre/extensión.
ROLES_SWITCH_VALIDOS = {"base", "update", "dlc"}

# Partes de un .wbfs partido (FAT32 no admite ficheros de 4 GB o más).
PARTES_WBFS = {".wbf1", ".wbf2", ".wbf3"}


@asynccontextmanager
async def _ciclo_vida(_app):
    """Al arrancar: hashes de los juegos en segundo plano (para no mostrar
    duplicados entre librerías), alta periódica en el índice si la
    librería es pública y comprobación de las fuentes externas."""
    import asyncio
    import externos
    import federacion
    import verificacion
    federacion.arrancar_hilo_hashes()
    verificacion.arrancar_hilo()
    tarea_externos = asyncio.create_task(externos.bucle())

    async def alta_periodica():
        await asyncio.sleep(15)
        while True:
            try:
                resultado = await federacion.darse_de_alta()
                if not resultado.get("ok"):
                    print(f"[federacion] sin alta en el índice: {resultado.get('motivo')}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[federacion] alta en el índice fallida: {e}", flush=True)
            await asyncio.sleep(6 * 3600)

    tarea = asyncio.create_task(alta_periodica())
    yield
    tarea.cancel()
    tarea_externos.cancel()


# Sin /docs ni /openapi.json: el servicio está expuesto a internet y no
# hace falta publicar el mapa completo de rutas.
app = FastAPI(title="RomsLibrary", lifespan=_ciclo_vida, docs_url=None, redoc_url=None, openapi_url=None)
ajustes.inicializar()
# Una carpeta por sistema soportado: en una instalación nueva no existe
# ninguna y la primera subida fallaba con "Crea la carpeta antes de subir".
for _sistema in EXTENSIONES_POR_SISTEMA:
    (ROOT / _sistema).mkdir(parents=True, exist_ok=True)


class _PeticionInterna:
    """Las rutas nuevas (API de la app, gestor) reutilizan las funciones de
    /api/* llamándolas directamente con esta "petición" y TOKEN_SISTEMA:
    un secreto aleatorio que se genera en cada arranque y nunca sale del
    proceso, así que solo el propio código puede usarlo."""
    headers: dict = {}


PETICION_INTERNA = _PeticionInterna()
TOKEN_SISTEMA = secrets.token_urlsafe(32)


def exigir_token(request: Request, token: str | None = Query(default=None)) -> None:
    """API interna antigua (/api/*): solo mientras el PHP de sund.es siga en
    medio. Sin ROMSLIBRARY_TOKEN_INTERNO configurado, está desactivada."""
    cabecera = request.headers.get("x-auth-token") or ""
    token = token or ""
    esperado = config.TOKEN_INTERNO
    if secrets.compare_digest(token, TOKEN_SISTEMA):
        return
    valido = bool(esperado) and (secrets.compare_digest(token, esperado) or secrets.compare_digest(cabecera, esperado))
    if not valido:
        raise HTTPException(status_code=401, detail="Token inválido.")


def nombre_seguro(valor: str) -> str:
    """Rechaza cualquier intento de path traversal o separador de ruta."""
    if not valor or valor in (".", "..") or "/" in valor or "\\" in valor or "\x00" in valor:
        raise HTTPException(status_code=400, detail="Nombre inválido.")
    return valor


def buscar_caratula(sistema: str, nombre_rom: str) -> Optional[str]:
    """Busca una carátula ya subida a mano para este ROM (mismo nombre base,
    cualquier extensión de imagen permitida). Nunca se descarga de bases de
    datos externas -- solo se sirve lo que el usuario subió."""
    stem = Path(nombre_rom).stem
    carpeta = COVERS_ROOT / sistema
    if not carpeta.is_dir():
        return None
    for ext in EXTENSIONES_CARATULA_PERMITIDAS:
        candidato = carpeta / (stem + ext)
        if candidato.is_file():
            return candidato.name
    return None


@app.get("/api/systems")
def listar_sistemas(request: Request, token: str | None = Query(default=None)):
    exigir_token(request, token)
    if not ROOT.is_dir():
        return JSONResponse([])
    sistemas = []
    for carpeta in sorted(ROOT.iterdir()):
        if carpeta.is_dir():
            # Un juego multi-fichero (PS1 cue+bin, multidisco...) vive en su
            # propia subcarpeta y cuenta como 1 solo juego, no 1 por fichero.
            n_juegos = sum(1 for f in carpeta.iterdir() if f.is_file() or f.is_dir())
            sistemas.append({"sistema": carpeta.name, "juegos": n_juegos})
    return sistemas


@app.get("/api/systems/{sistema}")
def listar_juegos(sistema: str, request: Request, token: str | None = Query(default=None)):
    exigir_token(request, token)
    sistema = nombre_seguro(sistema)
    carpeta = (ROOT / sistema).resolve()
    if not str(carpeta).startswith(str(ROOT.resolve())) or not carpeta.is_dir():
        raise HTTPException(status_code=404, detail="Sistema no encontrado.")
    juegos = []
    for f in sorted(carpeta.iterdir()):
        if f.is_file():
            juegos.append({"nombre": f.name, "tamano": f.stat().st_size})
    return juegos


def _carpeta_grupo(sistema: str, grupo: str) -> Path:
    sistema = nombre_seguro(sistema)
    grupo = nombre_seguro(grupo)
    carpeta = (ROOT / sistema / grupo).resolve()
    if not str(carpeta).startswith(str(ROOT.resolve())) or not carpeta.is_dir():
        raise HTTPException(status_code=404, detail="Grupo no encontrado.")
    return carpeta


@app.get("/api/group/files")
def ficheros_de_grupo(
    request: Request,
    token: str | None = Query(default=None),
    system: str = Query(...),
    grupo: str = Query(...),
):
    """Lista de ficheros de un grupo (disco PS1/PS2, Switch base+updates/DLC)
    para que la app los descargue UNO A UNO, en vez de pedir el grupo entero
    como zip: generar al vuelo un zip de 6,5 GB (Switch) tardaba ~30 s antes
    de mandar el primer byte, la app cortaba por timeout y reintentaba, y
    cada reintento generaba OTRO zip de 6,5 GB en disco. Fichero a fichero
    la descarga empieza al momento y cada uno se puede reanudar (Range)."""
    exigir_token(request, token)
    carpeta = _carpeta_grupo(system, grupo)
    roles = {}
    ruta_json = carpeta / "grupo.json"
    if ruta_json.is_file():
        try:
            roles = {f["nombre"]: f.get("rol") for f in json.loads(ruta_json.read_text(encoding="utf-8")).get("ficheros", [])}
        except (json.JSONDecodeError, OSError, KeyError):
            roles = {}
    return [
        {"nombre": p.relative_to(carpeta).as_posix(), "tamano": p.stat().st_size, "rol": roles.get(p.name)}
        for p in sorted(carpeta.rglob("*"))
        if p.is_file() and p.name != "grupo.json"
    ]


@app.get("/api/group/file")
def fichero_de_grupo(
    request: Request,
    token: str | None = Query(default=None),
    system: str = Query(...),
    grupo: str = Query(...),
    fichero: str = Query(...),
):
    """Un fichero concreto de un grupo (ruta relativa, puede llevar
    subcarpetas). FileResponse soporta Range: la app reanuda tras un corte."""
    exigir_token(request, token)
    carpeta = _carpeta_grupo(system, grupo)
    for parte in fichero.split("/"):
        nombre_seguro(parte)
    ruta = (carpeta / fichero).resolve()
    if not str(ruta).startswith(str(carpeta) + os.sep) or not ruta.is_file():
        raise HTTPException(status_code=404, detail="Fichero no encontrado en el grupo.")
    tipo, _ = mimetypes.guess_type(str(ruta))
    return FileResponse(ruta, media_type=tipo or "application/octet-stream", filename=ruta.name)


@app.get("/api/download/{sistema}/{fichero}")
def descargar_juego(sistema: str, fichero: str, request: Request, token: str | None = Query(default=None)):
    exigir_token(request, token)
    sistema = nombre_seguro(sistema)
    fichero = nombre_seguro(fichero)
    ruta = (ROOT / sistema / fichero).resolve()
    if not str(ruta).startswith(str(ROOT.resolve())) or not ruta.exists():
        raise HTTPException(status_code=404, detail="Fichero no encontrado.")
    if ruta.is_dir():
        # Juego multi-fichero (PS1 cue+bin, multidisco con .m3u...): se
        # comprime al vuelo a un temporal en disco (nunca en memoria -- estos
        # sets pueden pesar varios GB en PS2/PSP) y se limpia al terminar de
        # enviarlo. Sin compresión real (STORED): son datos ya binarios
        # densos, comprimir solo gastaría CPU sin ahorrar espacio de verdad.
        # Si un cliente corta la descarga, Starlette no ejecuta la
        # BackgroundTask que borra el zip y se quedaba huérfano (llegaron a
        # acumularse 4 de 6,5 GB). Limpieza de los de más de 3 h.
        for viejo in Path(tempfile.gettempdir()).glob("tmp*.zip"):
            try:
                if time.time() - viejo.stat().st_mtime > 3 * 3600:
                    viejo.unlink()
            except OSError:
                pass
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp.close()
        # grupo.json (si existe, solo grupos de Switch) va SIEMPRE primero en
        # el zip -- el cliente Android lee el zip en streaming (ZipInputStream,
        # una sola pasada, nunca lo bufferea: los ROMs de un grupo pueden pesar
        # varios GB) y necesita conocer el rol de cada fichero ANTES de
        # extraerlo para renombrarlo [BASE]/[UPDATE]/[DLC]. Orden alfabetico
        # normal no lo garantiza (un nombre de ROM en mayuscula ordena antes
        # que "grupo.json" en ASCII).
        ficheros_grupo = sorted(
            (p for p in ruta.rglob("*") if p.is_file()),
            key=lambda p: (p.name != "grupo.json", p.name),
        )
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED) as zf:
            for p in ficheros_grupo:
                zf.write(p, arcname=str(p.relative_to(ruta)))
        return FileResponse(
            tmp.name, media_type="application/zip", filename=f"{fichero}.zip",
            background=BackgroundTask(os.unlink, tmp.name),
        )
    tipo, _ = mimetypes.guess_type(str(ruta))
    return FileResponse(ruta, media_type=tipo or "application/octet-stream", filename=fichero)


@app.get("/apk")
def descargar_apk():
    # Sin token a proposito: la app en si no es dato sensible, y ya esta
    # protegida por estar solo accesible dentro de la Tailnet -- asi la URL
    # es corta y facil de teclear/escanear (2026-09-19, no volver a revertir
    # esto sin motivo real).
    if not APK_PATH.is_file():
        raise HTTPException(status_code=404, detail="APK todavía no publicada.")
    return FileResponse(APK_PATH, media_type="application/vnd.android.package-archive", filename=APK_PATH.name)


_cache_sha1_apk: dict = {}


def info_apk() -> dict | None:
    """Versión de la APK publicada en este servicio, leída de version.json
    (lo escribe el paso de publicación a partir del output-metadata.json de
    Gradle). None si este servicio no publica APK propia."""
    if not APK_PATH.is_file() or not config.APK_VERSION.is_file():
        return None
    datos = json.loads(config.APK_VERSION.read_text(encoding="utf-8"))
    firma = (APK_PATH.stat().st_mtime, APK_PATH.stat().st_size)
    if _cache_sha1_apk.get("firma") != firma:
        _cache_sha1_apk.update(firma=firma, sha1=hashlib.sha1(APK_PATH.read_bytes()).hexdigest())
    return {"versionCode": int(datos["versionCode"]), "versionName": str(datos["versionName"]),
            "apkSha1": _cache_sha1_apk["sha1"]}


@app.get("/api/version")
def version_apk(request: Request, token: str | None = Query(default=None)):
    exigir_token(request, token)
    info = info_apk()
    if info is None:
        raise HTTPException(status_code=404, detail="APK todavía no publicada.")
    return {**info, "apkPath": "/apk"}


@app.get("/api/games")
def listar_juegos_galeria(
    request: Request,
    token: str | None = Query(default=None),
    system: str | None = Query(default=None),
    q: str | None = Query(default=None),
):
    """Listado plano para la galería (web de staff + app): {system, filename,
    sizeBytes, coverUrl}, con filtro opcional por sistema y/o texto."""
    exigir_token(request, token)
    if system is not None:
        system = nombre_seguro(system)
    resultado = []
    if not ROOT.is_dir():
        return resultado
    carpetas = [ROOT / system] if system else sorted(ROOT.iterdir())
    for carpeta in carpetas:
        if not carpeta.is_dir():
            continue
        for f in sorted(carpeta.iterdir()):
            if not (f.is_file() or f.is_dir()):
                continue
            if q and q.lower() not in f.name.lower():
                continue
            caratula = buscar_caratula(carpeta.name, f.name)
            es_grupo = f.is_dir()
            tamano = (
                sum(p.stat().st_size for p in f.rglob("*") if p.is_file())
                if es_grupo else f.stat().st_size
            )
            tipo_grupo = None
            roles = None
            if es_grupo:
                ruta_grupo_json = f / "grupo.json"
                if ruta_grupo_json.is_file():
                    try:
                        datos_grupo = json.loads(ruta_grupo_json.read_text(encoding="utf-8"))
                        tipo_grupo = datos_grupo.get("tipo")
                        ficheros_grupo = datos_grupo.get("ficheros", [])
                        roles = {
                            "base": sum(1 for x in ficheros_grupo if x.get("rol") == "base"),
                            "update": sum(1 for x in ficheros_grupo if x.get("rol") == "update"),
                            "dlc": sum(1 for x in ficheros_grupo if x.get("rol") == "dlc"),
                        }
                    except (json.JSONDecodeError, OSError):
                        pass
            resultado.append({
                "system": carpeta.name,
                "filename": f.name,
                "sizeBytes": tamano,
                "coverUrl": f"/api/cover/{carpeta.name}/{caratula}" if caratula else None,
                "esGrupo": es_grupo,
                "tipoGrupo": tipo_grupo,
                "roles": roles,
            })
    return resultado


@app.get("/api/cover/{sistema}/{fichero}")
def servir_caratula(sistema: str, fichero: str, request: Request, token: str | None = Query(default=None)):
    exigir_token(request, token)
    sistema = nombre_seguro(sistema)
    fichero = nombre_seguro(fichero)
    ruta = (COVERS_ROOT / sistema / fichero).resolve()
    if not str(ruta).startswith(str(COVERS_ROOT.resolve())) or not ruta.is_file():
        raise HTTPException(status_code=404, detail="Carátula no encontrada.")
    tipo, _ = mimetypes.guess_type(str(ruta))
    return FileResponse(ruta, media_type=tipo or "application/octet-stream")


# Extensiones del fichero que lanza ES-DE dentro de un grupo de disco.
EXTENSIONES_LANZABLES = {".m3u", ".cue", ".iso", ".chd", ".nsp", ".xci", ".wbfs"}
SISTEMAS_DISCO = {"psx", "ps2"}


def carpeta_como_fichero(carpeta: Path) -> Path:
    """Renombra un grupo de PS1/PS2 "<juego>/" a "<juego>.<ext>/", donde
    <juego>.<ext> es el fichero que hay que lanzar (el .m3u si es multidisco,
    si no el .cue/.iso/.chd). ES-DE trata una carpeta cuyo nombre acaba en
    una extensión del sistema y que contiene un fichero con ESE MISMO nombre
    como un único juego ("directories interpreted as files"): sale una sola
    entrada en la vista de PSX, sin carpeta ni pistas sueltas. La clave de
    guardados y de carátula (nombre sin extensión) no cambia.
    Si ya tiene extensión o no se encuentra el lanzable, se deja igual."""
    if carpeta.suffix.lower() in EXTENSIONES_LANZABLES:
        return carpeta
    nombre = carpeta.name
    lanzable = None
    for ext in (".m3u", ".cue", ".iso", ".chd", ".nsp", ".xci", ".wbfs"):
        if (carpeta / f"{nombre}{ext}").is_file():
            lanzable = nombre + ext
            break
    if lanzable is None:
        return carpeta
    destino = carpeta.with_name(lanzable)
    if destino.exists():
        raise HTTPException(status_code=409, detail=f"Ya existe «{lanzable}» en este sistema.")
    carpeta.rename(destino)
    return destino


def existe_juego(carpeta_sistema: Path, nombre_juego: str) -> bool:
    """Un grupo puede estar guardado como "<juego>" o ya como "<juego>.cue"/.m3u..."""
    return any((carpeta_sistema / f"{nombre_juego}{ext}").exists() for ext in ("", *EXTENSIONES_LANZABLES))


def reparar_cues(carpeta: Path) -> list[str]:
    """Comprueba que cada .cue apunta a .bin que existen DE VERDAD (las
    líneas FILE "..."). Pasó con 22 .cue de la librería: los .bin se habían
    renombrado al subir y el .cue seguía apuntando al nombre original, así
    que el juego no arrancaba en ningún core. Si la corrección es inequívoca
    (un solo FILE -> el .bin con el mismo nombre que el .cue, o el único .bin
    de la carpeta; varias pistas -> los "(Track N)" en orden) se reescribe el
    .cue; si no, se devuelve la lista de problemas para rechazar la subida."""
    problemas = []

    def num_pista(nombre: str) -> Optional[int]:
        r = re.search(r"Track\s*0*(\d+)", nombre)
        return int(r.group(1)) if r else None

    bins = sorted(b.name for b in carpeta.glob("*.bin"))
    for cue in sorted(carpeta.glob("*.cue")):
        texto = cue.read_text(errors="replace")
        refs = re.findall(r'FILE\s+"([^"]+)"', texto)
        if all((carpeta / r).is_file() for r in refs):
            continue
        if len(refs) == 1:
            mismo = cue.with_suffix(".bin").name
            destino = [mismo] if mismo in bins else (bins if len(bins) == 1 else None)
        else:
            pistas = sorted((b for b in bins if num_pista(b) is not None), key=num_pista)
            destino = pistas if len(pistas) == len(refs) else None
        if not destino:
            problemas.append(f"{cue.name} apunta a {refs}, que no existen")
            continue
        for viejo, nuevo in zip(refs, destino):
            texto = texto.replace(f'"{viejo}"', f'"{nuevo}"', 1)
        cue.write_text(texto)
    return problemas


def zip_disco_a_grupo(zip_path: Path) -> Optional[Path]:
    """Convierte un .zip de PS1 con cue+bin dentro en un grupo (carpeta) --
    RetroArch NO puede cargar un cue+bin comprimido: con un core sin soporte
    de archivos (PCSX ReARMed, Beetle PSX) extrae SOLO el primer fichero
    cargable (el .cue) sin sus .bin, y el juego no arranca. Devuelve la
    carpeta creada, o None si el zip no lleva ningún .cue (p.ej. un .chd o
    .pbp suelto, que RetroArch sí extrae bien y se deja como está).
    - Se aplana cualquier subcarpeta interna.
    - Un solo .cue: se renombra a <juego>.cue (y su .sbi con él, los cores
      buscan el .sbi con el mismo nombre que el .cue). El .bin NO se toca:
      el .cue lo referencia por nombre. Así el guardado de RetroArch se llama
      <juego>.srm, igual que la clave que usa la app.
    - Varios .cue (multidisco): se genera <juego>.m3u con los discos."""
    nombre_juego = zip_path.stem
    carpeta_sistema = zip_path.parent
    destino = carpeta_sistema / nombre_juego
    with zipfile.ZipFile(zip_path) as zf:
        miembros = [m for m in zf.infolist() if not m.is_dir()]
        if not any(m.filename.lower().endswith(".cue") for m in miembros):
            return None
        if existe_juego(carpeta_sistema, nombre_juego):
            raise HTTPException(status_code=409, detail=f"Ya existe un juego «{nombre_juego}».")
        temporal = carpeta_sistema / f".conversion_{nombre_juego}"
        shutil.rmtree(temporal, ignore_errors=True)
        temporal.mkdir()
        try:
            for m in miembros:
                nombre_plano = Path(m.filename).name
                nombre_seguro(nombre_plano)
                salida = temporal / nombre_plano
                if salida.exists():
                    raise HTTPException(status_code=400, detail=f"Nombre repetido dentro del zip: {nombre_plano}")
                with zf.open(m) as origen, open(salida, "wb") as f:
                    shutil.copyfileobj(origen, f, 1024 * 1024)
            cues = sorted(p for p in temporal.iterdir() if p.suffix.lower() == ".cue")
            if len(cues) == 1:
                cue = cues[0]
                if cue.stem != nombre_juego:
                    sbi = cue.with_suffix(".sbi")
                    if sbi.is_file():
                        sbi.rename(temporal / f"{nombre_juego}.sbi")
                    cue.rename(temporal / f"{nombre_juego}.cue")
            else:
                (temporal / f"{nombre_juego}.m3u").write_text("\n".join(c.name for c in cues) + "\n", encoding="utf-8")
            problemas = reparar_cues(temporal)
            if problemas:
                raise HTTPException(status_code=400, detail="Juego con .cue roto: " + "; ".join(problemas))
            temporal.rename(destino)
            destino = carpeta_como_fichero(destino)
        except BaseException:
            shutil.rmtree(temporal, ignore_errors=True)
            raise
    zip_path.unlink()
    return destino


@app.post("/api/upload")
async def subir_juego(
    request: Request,
    token: str | None = Query(default=None),
    system: str = Form(...),
    filename: str = Form(...),
    rom: UploadFile = File(...),
    cover: Optional[UploadFile] = File(default=None),
):
    exigir_token(request, token)
    system = nombre_seguro(system)
    filename = nombre_seguro(filename)
    carpeta_sistema = (ROOT / system).resolve()
    if not str(carpeta_sistema).startswith(str(ROOT.resolve())) or not carpeta_sistema.is_dir():
        raise HTTPException(status_code=404, detail="Sistema no encontrado. Crea la carpeta antes de subir.")

    # Autoritativo aquí -- la validación en la web y en la app son solo
    # ayuda de UX, esta es la que de verdad decide.
    extensiones_validas = EXTENSIONES_POR_SISTEMA.get(system)
    if not extensiones_validas:
        raise HTTPException(status_code=400, detail=f"Sistema '{system}' sin extensiones configuradas para subida.")
    if not any(filename.lower().endswith(ext) for ext in extensiones_validas):
        raise HTTPException(
            status_code=400,
            detail=f"Extensión inválida para el sistema '{system}': se acepta {sorted(extensiones_validas)}.",
        )

    if system == "wii" and Path(filename).suffix.lower() in PARTES_WBFS:
        raise HTTPException(status_code=400, detail="Un .wbf1/.wbf2... es una parte de un .wbfs: súbelo como juego "
                                                    "de varios ficheros junto con su .wbfs.")

    es_rpgmaker = system == "rpgmaker"

    if es_rpgmaker:
        # El .zip subido es solo el transporte -- el "juego" de verdad es la
        # carpeta ya descomprimida (Game.exe + Data/ + Graphics/ + Audio/...),
        # para que en el dispositivo JoiPlay pueda apuntar directamente a
        # ella (no hay "ROM" ni emulador de por medio, ver /api/download que
        # ya zipea cualquier carpeta al vuelo sin cambios).
        nombre_juego = Path(filename).stem
        carpeta_juego = carpeta_sistema / nombre_juego
        if carpeta_juego.exists():
            raise HTTPException(status_code=409, detail="Ya existe un juego con ese nombre en ese sistema.")
        zip_temporal = carpeta_sistema / f".subida_{nombre_juego}.zip.tmp"
        with open(zip_temporal, "wb") as salida:
            while trozo := await rom.read(1024 * 1024):
                salida.write(trozo)
        try:
            with zipfile.ZipFile(zip_temporal) as zf:
                raiz_resuelta = carpeta_juego.resolve()
                for miembro in zf.namelist():
                    # zip-slip: ningún miembro puede acabar fuera de carpeta_juego.
                    if not str((carpeta_juego / miembro).resolve()).startswith(str(raiz_resuelta)):
                        raise HTTPException(status_code=400, detail="Zip con ruta insegura (zip-slip).")
                carpeta_juego.mkdir(parents=True)
                zf.extractall(carpeta_juego)
        except zipfile.BadZipFile:
            shutil.rmtree(carpeta_juego, ignore_errors=True)
            raise HTTPException(status_code=400, detail="El fichero subido no es un .zip válido.")
        finally:
            zip_temporal.unlink(missing_ok=True)
        destino_rom = carpeta_juego  # solo para el cleanup-si-falla-la-carátula de abajo
    else:
        destino_rom = carpeta_sistema / filename
        if destino_rom.exists():
            raise HTTPException(status_code=409, detail="Ya existe un fichero con ese nombre en ese sistema.")
        with open(destino_rom, "wb") as salida:
            while trozo := await rom.read(1024 * 1024):
                salida.write(trozo)

    caratula_guardada = None
    if cover is not None and cover.filename:
        contenido_cover = await cover.read()
        try:
            caratula_guardada = guardar_caratula(contenido_cover, COVERS_ROOT / system, Path(filename).stem)
        except ValueError:
            if es_rpgmaker:
                shutil.rmtree(destino_rom, ignore_errors=True)
            else:
                destino_rom.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail="La carátula no es una imagen jpg/png/webp reconocible (se mira el contenido real, no el nombre del fichero).",
            )

    # PS1 subido como .zip con cue+bin: se convierte en grupo (ver
    # zip_disco_a_grupo -- RetroArch no puede cargarlo comprimido).
    if system == "psx" and not es_rpgmaker and destino_rom.suffix.lower() == ".zip":
        carpeta_grupo = zip_disco_a_grupo(destino_rom)
        if carpeta_grupo is not None:
            destino_rom = carpeta_grupo

    nombre_resultado = destino_rom.name  # carpeta del juego (rpgmaker/grupo) o fichero (resto)
    return {"ok": True, "system": system, "filename": nombre_resultado, "cover": caratula_guardada}



# Juego de varios ficheros (PS1 cue+bin, multidisco...) -- en su propia
# subcarpeta ROOT/<sistema>/<nombre_juego>/, cuenta como UN juego en
# /api/games, se descarga como zip atómico. Se sube en 3 pasos (crear ->
# añadir cada fichero -> finalizar) EN VEZ de un único multipart gigante:
# un solo POST con varias partes del mismo campo "ficheros" obligaría a
# construir el cuerpo entero a mano en PHP y cargarlo en memoria -- ROMs de
# PS2/PSP pueden pesar varios GB, así que cada fichero se sube por separado
# con el mismo patrón de streaming ya usado en /api/upload (CURLFile).

@app.post("/api/group/create")
def crear_grupo(
    request: Request,
    token: str | None = Query(default=None),
    system: str = Form(...),
    nombre_juego: str = Form(...),
):
    exigir_token(request, token)
    system = nombre_seguro(system)
    nombre_seguro(nombre_juego)
    carpeta_sistema = (ROOT / system).resolve()
    if not str(carpeta_sistema).startswith(str(ROOT.resolve())) or not carpeta_sistema.is_dir():
        raise HTTPException(status_code=404, detail="Sistema no encontrado. Crea la carpeta antes de subir.")
    carpeta_juego = carpeta_sistema / nombre_juego
    if existe_juego(carpeta_sistema, nombre_juego):
        raise HTTPException(status_code=409, detail="Ya existe un juego con ese nombre en ese sistema.")
    carpeta_juego.mkdir(parents=True)
    return {"ok": True}


@app.post("/api/group/add_file")
async def anadir_fichero_grupo(
    request: Request,
    token: str | None = Query(default=None),
    system: str = Form(...),
    nombre_juego: str = Form(...),
    filename: str = Form(...),
    fichero: UploadFile = File(...),
    rol: Optional[str] = Form(default=None),
):
    exigir_token(request, token)
    system = nombre_seguro(system)
    nombre_seguro(nombre_juego)
    filename = nombre_seguro(filename)
    extensiones_validas = EXTENSIONES_POR_SISTEMA.get(system)
    if not extensiones_validas or not any(filename.lower().endswith(ext) for ext in extensiones_validas):
        raise HTTPException(
            status_code=400,
            detail=f"Extensión inválida para '{system}': se acepta {sorted(extensiones_validas or [])}.",
        )
    if system == "switch" and rol not in ROLES_SWITCH_VALIDOS:
        raise HTTPException(
            status_code=400,
            detail=f"rol inválido para switch: se acepta {sorted(ROLES_SWITCH_VALIDOS)}.",
        )
    carpeta_juego = (ROOT / system / nombre_juego).resolve()
    if not str(carpeta_juego).startswith(str(ROOT.resolve())) or not carpeta_juego.is_dir():
        raise HTTPException(status_code=404, detail="Grupo no encontrado -- crea el grupo primero.")
    destino = carpeta_juego / filename
    with open(destino, "wb") as salida:
        while trozo := await fichero.read(1024 * 1024):
            salida.write(trozo)
    if system == "switch":
        _registrar_rol_grupo(carpeta_juego, filename, rol)
    return {"ok": True}


def _ordenar_wbfs_partido(carpeta_juego: Path, nombre_juego: str) -> None:
    """Un juego de Wii de varios ficheros solo puede ser un .wbfs partido: un
    .wbfs y sus partes .wbf1, .wbf2... seguidas. Se renombran a
    «<juego>.wbfs», «<juego>.wbf1»... (los cargadores buscan las partes con el
    mismo nombre que el .wbfs)."""
    ficheros = [p for p in carpeta_juego.iterdir() if p.is_file()]
    wbfs = [p for p in ficheros if p.suffix.lower() == ".wbfs"]
    partes = sorted((p for p in ficheros if p.suffix.lower() in PARTES_WBFS), key=lambda p: p.suffix.lower())
    error = None
    if len(wbfs) != 1 or len(wbfs) + len(partes) != len(ficheros):
        error = "Un juego de Wii de varios ficheros tiene que ser un .wbfs y sus partes (.wbf1, .wbf2...)."
    elif [p.suffix.lower() for p in partes] != [f".wbf{i}" for i in range(1, len(partes) + 1)]:
        error = "Faltan partes del .wbfs: tienen que ir seguidas (.wbf1, .wbf2...)."
    elif not partes:
        error = "Un solo .wbfs no es un juego de varios ficheros: súbelo como fichero suelto."
    if error:
        shutil.rmtree(carpeta_juego, ignore_errors=True)
        raise HTTPException(status_code=400, detail=error)
    for p in [*wbfs, *partes]:
        p.rename(carpeta_juego / f"{nombre_juego}{p.suffix.lower()}.renombrando")
    for p in carpeta_juego.iterdir():
        p.rename(carpeta_juego / p.name.removesuffix(".renombrando"))


def _registrar_rol_grupo(carpeta_juego: Path, filename: str, rol: str) -> None:
    """Va guardando el rol (base/update/dlc) de cada fichero de un grupo de
    Switch segun se sube, en grupo.json dentro de la propia carpeta -- se
    consulta despues en finalize() para la validacion final y lo lee
    /api/games para mostrar el desglose de roles en la app."""
    ruta_json = carpeta_juego / "grupo.json"
    datos = {"tipo": "switch_base_dlc", "ficheros": []}
    if ruta_json.is_file():
        try:
            datos = json.loads(ruta_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            datos = {"tipo": "switch_base_dlc", "ficheros": []}
    datos["ficheros"] = [f for f in datos.get("ficheros", []) if f.get("nombre") != filename]
    datos["ficheros"].append({"nombre": filename, "rol": rol})
    ruta_json.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


@app.post("/api/group/finalize")
async def finalizar_grupo(
    request: Request,
    token: str | None = Query(default=None),
    system: str = Form(...),
    nombre_juego: str = Form(...),
    es_multidisco: bool = Form(False),
    cover: Optional[UploadFile] = File(default=None),
):
    """Genera el .m3u (si es_multidisco) y guarda la carátula. Los discos se
    listan alfabéticamente -- funciona bien con la convención "Disc 1",
    "Disc 2"... pero con 10+ discos el orden alfabético no es el numérico
    real (limitación conocida, aceptable para el uso real de este catálogo)."""
    exigir_token(request, token)
    system = nombre_seguro(system)
    nombre_seguro(nombre_juego)
    carpeta_juego = (ROOT / system / nombre_juego).resolve()
    if not str(carpeta_juego).startswith(str(ROOT.resolve())) or not carpeta_juego.is_dir():
        raise HTTPException(status_code=404, detail="Grupo no encontrado.")

    if system == "switch":
        ruta_json = carpeta_juego / "grupo.json"
        if not ruta_json.is_file():
            shutil.rmtree(carpeta_juego, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Grupo de Switch sin ningún fichero con rol asignado.")
        try:
            datos_grupo = json.loads(ruta_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            shutil.rmtree(carpeta_juego, ignore_errors=True)
            raise HTTPException(status_code=400, detail="grupo.json corrupto, grupo descartado.")
        ficheros_reg = datos_grupo.get("ficheros", [])
        bases = [f for f in ficheros_reg if f.get("rol") == "base"]
        if len(bases) != 1:
            shutil.rmtree(carpeta_juego, ignore_errors=True)
            raise HTTPException(
                status_code=400,
                detail=f"Un grupo de Switch necesita exactamente 1 fichero base (hay {len(bases)}).",
            )
        nombres_registrados = {f["nombre"] for f in ficheros_reg}
        nombres_en_disco = {
            p.name for p in carpeta_juego.iterdir()
            if p.is_file() and p.suffix.lower() in EXTENSIONES_POR_SISTEMA["switch"]
        }
        if nombres_registrados != nombres_en_disco:
            shutil.rmtree(carpeta_juego, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Los ficheros subidos no coinciden con los roles registrados.")
        # El base se llama igual que el juego ("Mi juego.nsp") para que
        # la carpeta pueda llamarse "Mi juego.nsp/" y ES-DE la muestre
        # como UN solo juego que lanza el base (updates/DLC quedan dentro,
        # sin verse; se instalan en Eden desde su propio menú).
        base = bases[0]
        nombre_base = nombre_juego + Path(base["nombre"]).suffix.lower()
        if base["nombre"] != nombre_base:
            if (carpeta_juego / nombre_base).exists():
                shutil.rmtree(carpeta_juego, ignore_errors=True)
                raise HTTPException(status_code=400, detail=f"Un update/DLC ya se llama «{nombre_base}».")
            (carpeta_juego / base["nombre"]).rename(carpeta_juego / nombre_base)
            base["nombre"] = nombre_base
            ruta_json.write_text(json.dumps(datos_grupo, ensure_ascii=False, indent=2), encoding="utf-8")
    elif es_multidisco:
        discos = sorted(p.name for p in carpeta_juego.iterdir() if p.suffix.lower() in EXTENSIONES_DISCO)
        if len(discos) < 2:
            shutil.rmtree(carpeta_juego, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Multidisco necesita al menos 2 ficheros de disco (.cue/.iso/.chd).")
        (carpeta_juego / f"{nombre_juego}.m3u").write_text("\n".join(discos) + "\n", encoding="utf-8")

    if system == "wii":
        _ordenar_wbfs_partido(carpeta_juego, nombre_juego)
    if system in ("switch", "wii"):
        carpeta_juego = carpeta_como_fichero(carpeta_juego)
    if system in ("psx", "ps2"):
        problemas = reparar_cues(carpeta_juego)
        if problemas:
            shutil.rmtree(carpeta_juego, ignore_errors=True)
            raise HTTPException(status_code=400, detail="Juego con .cue roto: " + "; ".join(problemas))
        carpeta_juego = carpeta_como_fichero(carpeta_juego)

    caratula_guardada = None
    if cover is not None and cover.filename:
        contenido_cover = await cover.read()
        try:
            caratula_guardada = guardar_caratula(contenido_cover, COVERS_ROOT / system, nombre_juego)
        except ValueError:
            pass  # best-effort aquí, igual que antes: sin carátula el juego se guarda igual.

    return {"ok": True, "cover": caratula_guardada}


@app.delete("/api/group/{sistema}/{nombre_juego}")
def borrar_grupo_incompleto(
    sistema: str, nombre_juego: str, request: Request, token: str | None = Query(default=None),
):
    """Para poder limpiar un grupo si la subida falla a medio camino (algún
    add_file falla) -- evita dejar una carpeta a medias ocupando espacio."""
    exigir_token(request, token)
    sistema = nombre_seguro(sistema)
    nombre_seguro(nombre_juego)
    carpeta_juego = (ROOT / sistema / nombre_juego).resolve()
    if str(carpeta_juego).startswith(str(ROOT.resolve())) and carpeta_juego.is_dir():
        shutil.rmtree(carpeta_juego, ignore_errors=True)
    return {"ok": True}


@app.delete("/api/games")
def borrar_juego(
    request: Request,
    token: str | None = Query(default=None),
    system: str = Query(...),
    filename: str = Query(...),
):
    """Borra el ROM y su carátula de la librería central en jarjar de verdad
    -- operación destructiva, distinta de "desinstalar" en la app (que solo
    borra la copia local del dispositivo)."""
    exigir_token(request, token)
    system = nombre_seguro(system)
    filename = nombre_seguro(filename)
    ruta_rom = (ROOT / system / filename).resolve()
    if not str(ruta_rom).startswith(str(ROOT.resolve())) or not ruta_rom.exists():
        raise HTTPException(status_code=404, detail="Fichero no encontrado.")
    caratula = buscar_caratula(system, filename)
    if ruta_rom.is_dir():
        shutil.rmtree(ruta_rom)
    else:
        ruta_rom.unlink()
    if caratula:
        (COVERS_ROOT / system / caratula).unlink(missing_ok=True)
    return {"ok": True, "eliminado": filename, "caratula_eliminada": caratula}


def _caratulas_de(sistema: str, stem: str) -> list[Path]:
    carpeta = COVERS_ROOT / sistema
    return [carpeta / (stem + ext) for ext in EXTENSIONES_CARATULA_PERMITIDAS if (carpeta / (stem + ext)).is_file()]


@app.post("/api/games/rename")
def renombrar_juego(
    request: Request,
    token: str | None = Query(default=None),
    system: str = Form(...),
    filename: str = Form(...),
    nuevo_nombre: str = Form(...),
):
    """Renombra un juego de la librería SIN perder nada de lo asociado:
    - ROM suelto: el fichero (la extensión tiene que seguir siendo válida
      para el sistema).
    - Grupo: la carpeta, y además el fichero que lanza ES-DE dentro de ella
      si se llamaba igual que la carpeta (<nombre>.m3u, o el único
      .cue/.iso/.chd) -- RetroArch nombra el guardado según ESE fichero, y
      la app según la carpeta; si no se renombran juntos dejan de coincidir.
    - Carátula: mismo nombre base nuevo.
    Las partidas no están aquí sino en el servicio de partidas, aparte de
    cualquier librería. Se comprueban TODOS los conflictos antes de tocar nada."""
    exigir_token(request, token)
    system = nombre_seguro(system)
    filename = nombre_seguro(filename)
    nuevo_nombre = nombre_seguro(nuevo_nombre.strip())
    if nuevo_nombre == filename:
        return {"ok": True, "sin_cambios": True}

    origen = (ROOT / system / filename).resolve()
    destino = (ROOT / system / nuevo_nombre).resolve()
    raiz = str(ROOT.resolve())
    if not str(origen).startswith(raiz) or not str(destino).startswith(raiz) or not origen.exists():
        raise HTTPException(status_code=404, detail="Juego no encontrado.")
    if destino.exists():
        raise HTTPException(status_code=409, detail=f"Ya existe un juego llamado «{nuevo_nombre}».")
    es_grupo = origen.is_dir()
    ext_grupo = origen.suffix.lower() if es_grupo and origen.suffix.lower() in EXTENSIONES_LANZABLES else ""
    if ext_grupo and not nuevo_nombre.lower().endswith(ext_grupo):
        nuevo_nombre += origen.suffix  # "Juego A.cue" -> "Juego A Remaster" debe seguir siendo ".cue"
        destino = (ROOT / system / nuevo_nombre).resolve()
        if destino.exists():
            raise HTTPException(status_code=409, detail=f"Ya existe un juego llamado «{nuevo_nombre}».")
    if not es_grupo and Path(nuevo_nombre).suffix.lower() not in EXTENSIONES_POR_SISTEMA.get(system, set()):
        raise HTTPException(status_code=400, detail="La extensión del nuevo nombre no es válida para este sistema.")

    clave_vieja = Path(filename).stem
    clave_nueva = Path(nuevo_nombre).stem
    if not clave_nueva:
        raise HTTPException(status_code=400, detail="Nombre inválido.")

    # Fichero lanzable dentro del grupo que hay que renombrar a la par.
    lanzable = None
    if ext_grupo:
        lanzable = origen / filename  # carpeta "X.cue" -> lanza "X.cue"
    elif es_grupo:
        m3u = origen / f"{filename}.m3u"
        discos = [p for p in origen.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONES_DISCO]
        if m3u.is_file():
            lanzable = m3u
        elif len(discos) == 1 and discos[0].stem == filename:
            lanzable = discos[0]

    caratulas = _caratulas_de(system, clave_vieja)
    for c in caratulas:
        if (c.parent / (clave_nueva + c.suffix)).exists():
            raise HTTPException(status_code=409, detail="Ya existe una carátula con el nuevo nombre.")

    # --- A partir de aquí ya no hay conflictos posibles: se aplica todo ---
    origen.rename(destino)
    if lanzable is not None:
        nombre_lanzable = nuevo_nombre if ext_grupo else nuevo_nombre + lanzable.suffix
        (destino / lanzable.name).rename(destino / nombre_lanzable)
        ruta_json = destino / "grupo.json"
        if ruta_json.is_file():
            datos_grupo = json.loads(ruta_json.read_text(encoding="utf-8"))
            for f in datos_grupo.get("ficheros", []):
                if f.get("nombre") == lanzable.name:
                    f["nombre"] = nombre_lanzable
            ruta_json.write_text(json.dumps(datos_grupo, ensure_ascii=False, indent=2), encoding="utf-8")
        # Las partes de un .wbfs partido se llaman como él.
        for p in destino.iterdir():
            if p.suffix.lower() in PARTES_WBFS and p.stem == lanzable.stem:
                p.rename(destino / (Path(nombre_lanzable).stem + p.suffix.lower()))
    for c in caratulas:
        c.rename(c.parent / (clave_nueva + c.suffix))
    # El juego conserva su historial de versiones y, si es un solo fichero,
    # su SHA (renombrar no cambia el contenido: no hace falta recalcularlo).
    import versiones
    versiones.renombrado(system, filename, destino.name, mover_hash=destino.is_file())

    return {
        "ok": True,
        "nuevo_nombre": nuevo_nombre,
        "caratulas": len(caratulas),
    }


@app.post("/api/games/cover")
async def cambiar_caratula(
    request: Request,
    token: str | None = Query(default=None),
    system: str = Form(...),
    filename: str = Form(...),
    cover: UploadFile = File(...),
):
    """Sustituye la carátula de un juego ya existente (misma validación por
    bytes reales y conversión WEBP->PNG que al subir)."""
    exigir_token(request, token)
    system = nombre_seguro(system)
    filename = nombre_seguro(filename)
    ruta_rom = (ROOT / system / filename).resolve()
    if not str(ruta_rom).startswith(str(ROOT.resolve())) or not ruta_rom.exists():
        raise HTTPException(status_code=404, detail="Juego no encontrado.")
    contenido = await cover.read()
    if extension_real_de_imagen(contenido[:16]) is None:
        raise HTTPException(status_code=400, detail="La imagen no es PNG, JPG ni WEBP.")
    stem = Path(filename).stem
    # La anterior puede tener otra extensión (.jpg -> .png): se borra antes
    # para que buscar_caratula no siga encontrando la vieja.
    for anterior in _caratulas_de(system, stem):
        anterior.unlink()
    guardada = guardar_caratula(contenido, COVERS_ROOT / system, stem)
    return {"ok": True, "cover": guardada}


@app.get("/api/health")
def salud():
    return {"ok": True}


def _tamano_carpeta_bytes(carpeta: Path) -> int:
    if not carpeta.is_dir():
        return 0
    try:
        salida = subprocess.run(
            ["du", "-sb", str(carpeta)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return int(salida.stdout.split()[0])
    except (subprocess.SubprocessError, ValueError, IndexError):
        return 0


def uso_disco_datos() -> dict:
    """Disco donde viven los datos + tamaño de los ROMs y de las categorías
    extra configuradas (en sund.es: las películas y series de Jellyfin, que
    comparten disco con los ROMs)."""
    disco_total, disco_usado, disco_libre = shutil.disk_usage(ROOT)
    categorias = {"ROMs (RomsLibrary)": _tamano_carpeta_bytes(ROOT)}
    for nombre, ruta in config.DISCO_EXTRA:
        categorias[nombre] = _tamano_carpeta_bytes(ruta)
    return {"disco_total": disco_total, "disco_usado": disco_usado, "disco_libre": disco_libre,
            "categorias": categorias}


@app.get("/api/disk-usage")
def uso_disco(request: Request, token: str | None = Query(default=None)):
    exigir_token(request, token)
    return uso_disco_datos()


# ---------- Rutas nuevas: API de la app, federación y gestor web ----------
# Al final del módulo a propósito: importan este mismo módulo (main) para
# reutilizar sus funciones, que ya están todas definidas en este punto.
import api_app  # noqa: E402
import federacion  # noqa: E402
import gestor  # noqa: E402
import externos  # noqa: E402
import fichas  # noqa: E402
import verificacion  # noqa: E402
import versiones  # noqa: E402

federacion.inicializar()
versiones.inicializar()
externos.inicializar()
fichas.inicializar()
accesos.inicializar()
verificacion.inicializar()
app.include_router(api_app.router)
app.include_router(federacion.router)
app.include_router(gestor.router)
