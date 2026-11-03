"""
Juegos de Switch guardados comprimidos: .nsp -> .nsz y .xci -> .xcz (con
nsz, https://github.com/nicoboss/nsz), servidos SIEMPRE descomprimidos.

- ALMACENAMIENTO: dentro de cada grupo de Switch («<juego>.nsp/», ver
  main.finalizar_grupo) los .nsp/.xci se comprimen en segundo plano (hilo
  «comprimir»). El original solo se borra cuando lo comprimido, al
  descomprimirlo, sale BIT A BIT idéntico (mismo SHA-256 y tamaño). Lo que
  llega ya comprimido (.nsz/.xcz) se guarda tal cual, tras comprobar que
  descomprime bien (nsz verifica los hashes de los NCA). Los juegos de
  Switch de UN solo fichero (no grupo) no se comprimen.
- NOMBRE LÓGICO: hacia fuera nada cambia. Un «Juego.nsz» guardado se ve,
  se lista y se descarga como «Juego.nsp» (lo que instalan la consola y
  Eden), con el tamaño y el SHA-256 del .nsp descomprimido. grupo.json
  guarda siempre los nombres lógicos. La identidad del juego (versiones.py,
  marcas de corrupto, federación) sigue siendo la del contenido
  descomprimido: comprimir no cambia el SHA del juego.
- METADATOS (tabla «comprimidos»): por cada fichero comprimido, su SHA y su
  tamaño, y los del contenido descomprimido. Se encuentra por (tamaño,
  mtime) del comprimido (renombrar no los cambia) y, si no, por su SHA.
- CACHÉ (/datos/cache/switch/<sha>.nsp|.xci): al descargar se descomprime
  una vez y se sirve el fichero de la caché (con Range: se puede reanudar).
  LRU con límite (ajuste «cache_switch_gb», 15 GB por defecto): se expulsa
  lo que más tiempo lleva sin descargarse, NUNCA lo que se está sirviendo.
  Un candado por fichero evita descomprimir dos veces lo mismo; todo se
  escribe en /datos/tmp y se mueve a su sitio con rename (atómico).
- ESPERA: si no está en la caché, la petición espera un rato y, si aún no
  está, responde 503 con Retry-After (la app y el cliente de PC reintentan
  y reanudan solos; la descompresión sigue en segundo plano).

Claves: nsz necesita prod.keys. Se le pasan con --keys desde
ROMSLIBRARY_SWITCH_CLAVES (por defecto /datos/keys/prod.keys, fuera de la
imagen y del repositorio). Sin claves no se comprime nada y no se aceptan
.nsz/.xcz: la librería funciona como siempre.

Línea de órdenes (dentro del contenedor):
    python comprimidos.py estado          ficheros, ratios, tiempos y caché
    python comprimidos.py limite <GB>     cambia el límite de la caché
    python comprimidos.py importar <fichero> <nombre del juego>
        juego de Switch (solo base) desde un fichero ya en /datos, por el mismo
        camino que una subida del gestor; el fichero de origen NO se borra
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

import ajustes
import config

SISTEMA = "switch"
COMPRIMIDO_DE = {".nsp": ".nsz", ".xci": ".xcz"}
LOGICO_DE = {v: k for k, v in COMPRIMIDO_DE.items()}

CLAVES = Path(os.environ.get("ROMSLIBRARY_SWITCH_CLAVES", str(config.DATOS / "keys" / "prod.keys")))
NSZ = os.environ.get("ROMSLIBRARY_NSZ", "nsz")
# jarjar tiene 12 núcleos y más servicios: 4 hilos para zstd, con nice/ionice.
HILOS = max(1, int(os.environ.get("ROMSLIBRARY_SWITCH_HILOS", "4")))
NIVEL = int(os.environ.get("ROMSLIBRARY_SWITCH_NIVEL", "18"))
# Compresión POR BLOQUES (ver la justificación en _modo_nsz).
MODO = os.environ.get("ROMSLIBRARY_SWITCH_MODO", "bloques")
LIMITE_DEFECTO_GB = float(os.environ.get("ROMSLIBRARY_CACHE_SWITCH_GB", "15"))
# Un fichero recién subido (o copiado) no se toca hasta que lleva este rato quieto.
REPOSO = 120
# Cuánto puede esperar una petición a que se descomprima antes de dar 503.
ESPERA_APP = 25            # la app corta a los 30 s sin respuesta
ESPERA_FEDERACION = 240    # las otras librerías esperan hasta 300 s
REINTENTAR_EN = 15         # Retry-After del 503
MARGEN_DISCO = 2 * 1024**3

CACHE = config.DATOS / "cache" / SISTEMA
# Carpeta de trabajo propia (se vacía al arrancar): nunca otra de /datos/tmp.
TRABAJO = config.TMP / "comprimidos-trabajo"


def _modo_nsz() -> list[str]:
    """Por bloques (-B, 1 MB) por defecto. Medido en jarjar (2026-11-03) con
    Pokémon Legends Arceus (.nsp de 6,46 GB, nivel 18, 4 hilos):
      sólida:      2,97 GB (45,9 %), comprimir 263 s, descomprimir 29 s
      por bloques: 2,95 GB (45,7 %), comprimir 193 s, descomprimir 27 s
    Aquí la sólida no gana ni en ratio, y por bloques comprime en paralelo de
    verdad (más rápido al importar) y descomprime igual o más rápido (lo que
    espera la primera descarga). Además es el modo por defecto de nsz para
    .xcz, así que lo que llega ya comprimido suele venir igual."""
    return ["-S"] if MODO == "solido" else ["-B"]


class SinVerificar(Exception):
    """Un comprimido cuyo contenido descomprimido aún no se conoce."""


class Fallo(Exception):
    pass


# =====================================================================
# Nombres lógicos
# =====================================================================

def es_comprimido(ruta: Path | str) -> bool:
    return Path(ruta).suffix.lower() in LOGICO_DE


def nombre_logico(nombre: str) -> str:
    """«Juego.nsz» -> «Juego.nsp»; cualquier otro nombre queda igual. Vale
    también para rutas relativas («sub/Juego.xcz»)."""
    p = Path(nombre)
    ext = LOGICO_DE.get(p.suffix.lower())
    return nombre[: -len(p.suffix)] + ext if ext else nombre


def nombre_comprimido(nombre: str) -> str:
    p = Path(nombre)
    ext = COMPRIMIDO_DE.get(p.suffix.lower())
    return nombre[: -len(p.suffix)] + ext if ext else nombre


def fisico(carpeta: Path, nombre: str) -> Path | None:
    """El fichero que hay de verdad en disco para un nombre lógico: el
    mismo si existe, si no su versión comprimida."""
    p = carpeta / nombre
    if p.is_file():
        return p
    alternativo = carpeta / nombre_comprimido(nombre)
    if alternativo != p and alternativo.is_file():
        return alternativo
    return None


def ficheros_logicos(carpeta: Path) -> list[tuple[str, Path]]:
    """[(ruta relativa lógica, fichero en disco)] de un grupo, ordenado. Si
    están a la vez el original y el comprimido (un instante, al terminar de
    comprimir), cuenta solo el original."""
    res: dict[str, Path] = {}
    for p in sorted(carpeta.rglob("*")):
        if not p.is_file():
            continue
        logico = nombre_logico(p.relative_to(carpeta).as_posix())
        if logico in res and es_comprimido(p):
            continue
        res[logico] = p
    return sorted(res.items())


def tamano_logico(p: Path) -> int:
    if es_comprimido(p):
        meta = info(p)
        if meta:
            return meta["tamano"]
    return p.stat().st_size


def tamano_juego(carpeta: Path) -> int:
    return sum(tamano_logico(p) for _, p in ficheros_logicos(carpeta))


def sha_logico(p: Path) -> str:
    """SHA-256 del contenido descomprimido. SinVerificar si aún no se conoce."""
    meta = info(p)
    if meta is None:
        raise SinVerificar(str(p))
    return meta["sha"]


# =====================================================================
# Metadatos
# =====================================================================

def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def inicializar() -> None:
    with _conectar() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS comprimidos (
                sha_comp TEXT PRIMARY KEY, tamano_comp INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                sha TEXT NOT NULL, tamano INTEGER NOT NULL, origen TEXT NOT NULL,
                seg_comprimir REAL, seg_descomprimir REAL, fecha REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS comprimidos_firma ON comprimidos (tamano_comp, mtime_ns);
            CREATE TABLE IF NOT EXISTS comprimidos_fallos (
                ruta TEXT PRIMARY KEY, tamano INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                error TEXT NOT NULL, fecha REAL NOT NULL
            );
            """
        )
    CACHE.mkdir(parents=True, exist_ok=True)
    TRABAJO.mkdir(parents=True, exist_ok=True)


def disponible() -> bool:
    """¿Se puede comprimir/descomprimir aquí? (hacen falta las claves)."""
    return CLAVES.is_file() and os.access(CLAVES, os.R_OK)


def info(p: Path) -> dict | None:
    """{sha, tamano, ...} del contenido descomprimido de `p`, si se conoce.
    Rápido: solo busca por (tamaño, mtime) del comprimido."""
    try:
        st = p.stat()
    except OSError:
        return None
    with _conectar() as con:
        fila = con.execute("SELECT * FROM comprimidos WHERE tamano_comp = ? AND mtime_ns = ?",
                           (st.st_size, st.st_mtime_ns)).fetchone()
    return dict(fila) if fila else None


def _identificar_por_sha(p: Path) -> dict | None:
    """Lento (lee el comprimido entero): un comprimido conocido cuyo mtime
    ha cambiado (copiado, restaurado de una copia de seguridad...)."""
    sha_comp, tam = _sha(p)
    with _conectar() as con:
        fila = con.execute("SELECT * FROM comprimidos WHERE sha_comp = ?", (sha_comp,)).fetchone()
        if fila is None or fila["tamano_comp"] != tam:
            return None
        con.execute("UPDATE comprimidos SET mtime_ns = ? WHERE sha_comp = ?", (p.stat().st_mtime_ns, sha_comp))
    return info(p)


def _guardar_meta(sha_comp: str, tamano_comp: int, mtime_ns: int, sha: str, tamano: int, origen: str,
                  seg_comprimir: float | None, seg_descomprimir: float | None) -> None:
    with _conectar() as con:
        con.execute("INSERT OR REPLACE INTO comprimidos (sha_comp, tamano_comp, mtime_ns, sha, tamano, origen, "
                    "seg_comprimir, seg_descomprimir, fecha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (sha_comp, tamano_comp, mtime_ns, sha, tamano, origen, seg_comprimir, seg_descomprimir,
                     time.time()))


def _fallo(p: Path) -> str | None:
    """Error ya anotado para ESTE fichero (mismo tamaño y mtime): no se reintenta en bucle."""
    try:
        st = p.stat()
    except OSError:
        return None
    with _conectar() as con:
        fila = con.execute("SELECT * FROM comprimidos_fallos WHERE ruta = ?", (_rel(p),)).fetchone()
    if fila and fila["tamano"] == st.st_size and fila["mtime_ns"] == st.st_mtime_ns:
        return fila["error"]
    return None


def _anotar_fallo(p: Path, error: str) -> None:
    try:
        st = p.stat()
    except OSError:
        return
    with _conectar() as con:
        con.execute("INSERT OR REPLACE INTO comprimidos_fallos (ruta, tamano, mtime_ns, error, fecha) "
                    "VALUES (?, ?, ?, ?, ?)", (_rel(p), st.st_size, st.st_mtime_ns, error[:500], time.time()))


def _rel(p: Path) -> str:
    try:
        return p.relative_to(config.ROOT).as_posix()
    except ValueError:
        return str(p)


# =====================================================================
# nsz y utilidades
# =====================================================================

def _sha(p: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(p, "rb") as f:
        while trozo := f.read(4 * 1024 * 1024):
            h.update(trozo)
            n += len(trozo)
    return h.hexdigest(), n


_HEX_LARGO = re.compile(r"\b[0-9a-fA-F]{32,}\b")


def _nsz(argumentos: list[str], carpeta: Path) -> None:
    """Ejecuta nsz con prioridad baja. La salida va a un log en la carpeta de
    trabajo; si falla, se lanza Fallo con el final del log (sin cadenas
    hexadecimales largas: nunca puede acabar una clave en los logs)."""
    log = carpeta / "nsz.log"
    orden = [NSZ, "--keys", str(CLAVES), *argumentos]
    if shutil.which("ionice"):
        orden = ["ionice", "-c", "2", "-n", "7", *orden]
    with open(log, "wb") as salida:
        r = subprocess.run(orden, stdout=salida, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                           cwd=carpeta, preexec_fn=lambda: os.nice(10), env={**os.environ, "HOME": str(carpeta)})
    if r.returncode != 0:
        cola = log.read_text(errors="replace").strip().splitlines()[-6:]
        raise Fallo(f"nsz terminó con código {r.returncode}: " + _HEX_LARGO.sub("…", " | ".join(cola))[:400])


def _unico(carpeta: Path, extension: str) -> Path:
    hallados = [p for p in carpeta.iterdir() if p.is_file() and p.suffix.lower() == extension]
    if len(hallados) != 1:
        raise Fallo(f"nsz no ha dejado un {extension} (hay {len(hallados)})")
    return hallados[0]


def _nueva_carpeta_trabajo() -> Path:
    carpeta = TRABAJO / uuid.uuid4().hex
    carpeta.mkdir(parents=True)
    return carpeta


def descomprimir_a(comprimido: Path, carpeta: Path) -> Path:
    """Descomprime en `carpeta` (de trabajo, vacía) y devuelve el fichero resultante."""
    salida = carpeta / "d"
    salida.mkdir()
    _nsz(["-D", "-t", str(HILOS), "-m", "1", "-o", str(salida), str(comprimido)], carpeta)
    return _unico(salida, LOGICO_DE[comprimido.suffix.lower()])


# =====================================================================
# Compresión al importar (hilo «comprimir»)
# =====================================================================

def comprimir(original: Path) -> dict:
    """Comprime un .nsp/.xci de un grupo. Solo si al descomprimirlo sale
    idéntico, el comprimido sustituye al original (que pasa a la caché: la
    primera descarga ya no espera). Devuelve las cifras de la operación."""
    ext = original.suffix.lower()
    st0 = original.stat()
    trabajo = _nueva_carpeta_trabajo()
    try:
        t0 = time.monotonic()
        sha, tamano = _sha(original)
        t_sha = time.monotonic()
        (trabajo / "c").mkdir()
        _nsz(["-C", "-K", *_modo_nsz(), "-l", str(NIVEL), "-t", str(HILOS), "-m", "1", "-o", str(trabajo / "c"),
              str(original)], trabajo)
        comprimido = _unico(trabajo / "c", COMPRIMIDO_DE[ext])
        t1 = time.monotonic()
        st = original.stat()
        if (st.st_size, st.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns):
            raise Fallo("el original ha cambiado mientras se comprimía")
        descomprimido = descomprimir_a(comprimido, trabajo)
        sha_d, tamano_d = _sha(descomprimido)
        t2 = time.monotonic()
        if (sha_d, tamano_d) != (sha, tamano):
            raise Fallo(f"al descomprimir NO sale idéntico (SHA {sha_d[:12]}… frente a {sha[:12]}…, "
                        f"{tamano_d} frente a {tamano} bytes): se deja el original")
        sha_comp, tamano_comp = _sha(comprimido)
        destino = original.with_name(nombre_comprimido(original.name))
        cifras = {"fichero": _rel(original), "tamano": tamano, "tamano_comp": tamano_comp,
                  "ratio": round(tamano_comp / tamano, 4) if tamano else None,
                  "seg_comprimir": round(t1 - t_sha, 1), "seg_descomprimir": round(t2 - t1, 1),
                  "seg_sha": round(t_sha - t0, 1)}
        _guardar_meta(sha_comp, tamano_comp, comprimido.stat().st_mtime_ns, sha, tamano, "comprimido aquí",
                      cifras["seg_comprimir"], cifras["seg_descomprimir"])
        os.replace(comprimido, destino)                 # mismo disco (/datos): atómico
        _adoptar(descomprimido, sha, ext)
        original.unlink()
        return cifras
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


def verificar_subido(comprimido: Path) -> dict:
    """Un .nsz/.xcz que ha llegado ya comprimido: se descomprime, nsz
    comprueba los hashes de los NCA (-V) y se apunta el SHA del resultado."""
    trabajo = _nueva_carpeta_trabajo()
    try:
        t0 = time.monotonic()
        descomprimido = descomprimir_a(comprimido, trabajo)
        _nsz(["-V", str(descomprimido)], trabajo)
        sha, tamano = _sha(descomprimido)
        t1 = time.monotonic()
        sha_comp, tamano_comp = _sha(comprimido)
        _guardar_meta(sha_comp, tamano_comp, comprimido.stat().st_mtime_ns, sha, tamano, "subido comprimido",
                      None, round(t1 - t0, 1))
        _adoptar(descomprimido, sha, LOGICO_DE[comprimido.suffix.lower()])
        return {"fichero": _rel(comprimido), "tamano": tamano, "tamano_comp": tamano_comp,
                "seg_descomprimir": round(t1 - t0, 1)}
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


def pendientes() -> list[tuple[str, Path]]:
    """[("comprimir"|"verificar", fichero)] dentro de los grupos de Switch ya finalizados."""
    carpeta = config.ROOT / SISTEMA
    res = []
    if not carpeta.is_dir():
        return res
    ahora = time.time()
    for grupo in sorted(carpeta.iterdir()):
        if grupo.name.startswith(".") or not grupo.is_dir() or grupo.suffix.lower() not in COMPRIMIDO_DE:
            continue
        for p in sorted(grupo.rglob("*")):
            if not p.is_file():
                continue
            try:
                quieto = ahora - p.stat().st_mtime > REPOSO
            except OSError:
                continue
            if p.suffix.lower() in COMPRIMIDO_DE and quieto:
                res.append(("comprimir", p))
            elif es_comprimido(p) and info(p) is None:
                res.append(("verificar", p))
    return res


_despertar = threading.Event()


def avisar() -> None:
    """Hay algo nuevo (grupo finalizado, copia terminada): mirar ya."""
    _despertar.set()


def _vuelta() -> None:
    for tarea, p in pendientes():
        if not p.exists() or _fallo(p):
            continue
        try:
            if tarea == "verificar":
                if _identificar_por_sha(p):
                    continue
                # Por el mismo candado que las descargas: nunca dos veces a la vez.
                trabajo = preparar(p)
                if trabajo is not None:
                    trabajo.hecho.wait()
                    if trabajo.error:
                        raise Fallo(trabajo.error)
                print(f"[comprimidos] verificado {_rel(p)}: {info(p)}", flush=True)
            else:
                antes = _hash_grupo_conocido(p)
                cifras = comprimir(p)
                _rehacer_hash_grupo(p, antes)
                print(f"[comprimidos] comprimido {cifras}", flush=True)
        except Exception as e:  # noqa: BLE001 -- se anota y se sigue con el resto
            _anotar_fallo(p, str(e))
            print(f"[comprimidos] {tarea} {_rel(p)}: {e}", flush=True)
    expulsar()


def arrancar_hilo() -> None:
    """Solo en el proceso del servicio (nunca desde la línea de órdenes: borraría
    el trabajo en curso del servicio)."""
    # Restos de trabajos interrumpidos (reinicio a mitad de comprimir o descomprimir).
    shutil.rmtree(TRABAJO, ignore_errors=True)
    TRABAJO.mkdir(parents=True, exist_ok=True)
    if not disponible():
        print(f"[comprimidos] sin claves de Switch ({CLAVES}): no se comprime nada", flush=True)

    def bucle():
        time.sleep(30)
        while True:
            try:
                if disponible():
                    _vuelta()
                else:
                    expulsar()
            except Exception as e:  # noqa: BLE001 -- el hilo no puede morir
                print(f"[comprimidos] error: {e}", flush=True)
            _despertar.wait(60)
            _despertar.clear()
    threading.Thread(target=bucle, name="comprimir", daemon=True).start()


def _grupo_de(p: Path) -> tuple[str, Path] | None:
    try:
        rel = p.relative_to(config.ROOT / SISTEMA)
    except ValueError:
        return None
    grupo = config.ROOT / SISTEMA / rel.parts[0]
    return f"{SISTEMA}/{rel.parts[0]}", grupo


def _hash_grupo_conocido(p: Path) -> str | None:
    import federacion
    g = _grupo_de(p)
    return federacion.sha_conocido(SISTEMA, g[1].name) if g else None


def _rehacer_hash_grupo(p: Path, sha: str | None) -> None:
    """El contenido lógico del grupo no ha cambiado: su SHA sigue valiendo
    con la firma (tamaño+mtime) nueva; así el hilo de hashes no lo relee."""
    import federacion
    g = _grupo_de(p)
    if not g or not sha or not g[1].is_dir():
        return
    tamano, mtime = federacion._firma(g[1], fresca=True)
    import metacache
    metacache.calcular(g[1])      # tamaño/firma de los listados, ya con el comprimido
    with _conectar() as con:
        con.execute("UPDATE hashes SET tamano = ?, mtime = ? WHERE ruta = ? AND sha = ?", (tamano, mtime, g[0], sha))


# =====================================================================
# Caché de descomprimidos (LRU)
# =====================================================================

_candado = threading.Lock()
_en_uso: dict[str, int] = {}                 # nombre en la caché -> descargas en curso
_trabajos: dict[str, "_Trabajo"] = {}        # comprimido (ruta) -> descompresión en marcha o fallida
_recien: dict[str, float] = {}               # nombre en la caché -> cuándo se preparó (aún sin descargar)
GRACIA = 600                                 # s que se respeta lo recién preparado
_hilos = ThreadPoolExecutor(max_workers=2, thread_name_prefix="descomprimir")


class _Trabajo:
    def __init__(self):
        self.hecho = threading.Event()
        self.error: str | None = None
        self.fin = 0.0


def limite_bytes() -> int:
    try:
        gb = float(ajustes.leer("cache_switch_gb", "") or LIMITE_DEFECTO_GB)
    except ValueError:
        gb = LIMITE_DEFECTO_GB
    return int(max(gb, 0) * 1024**3)


def _entradas() -> list[os.DirEntry]:
    try:
        return [e for e in os.scandir(CACHE) if e.is_file() and e.name.split(".")[-1] in ("nsp", "xci")]
    except FileNotFoundError:
        return []


def uso_cache() -> dict:
    entradas = _entradas()
    return {"bytes": sum(e.stat().st_size for e in entradas), "ficheros": len(entradas), "limite": limite_bytes(),
            "en_uso": sum(1 for v in _en_uso.values() if v)}


def expulsar(extra: int = 0) -> list[str]:
    """Deja la caché por debajo del límite (contando `extra` bytes que van a
    entrar), expulsando primero lo que hace más tiempo que no se descarga.
    Lo que se está sirviendo no se toca nunca, ni lo recién preparado que aún
    no ha empezado a descargarse (si no, con la caché llena, se expulsaba
    antes de que la consola volviese tras el 503 y se descomprimía otra vez)."""
    limite = limite_bytes()
    fuera = []
    with _candado:
        entradas = sorted(_entradas(), key=lambda e: e.stat().st_mtime)
        total = sum(e.stat().st_size for e in entradas) + extra
        for e in entradas:
            if total <= limite:
                break
            if _en_uso.get(e.name) or time.monotonic() - _recien.get(e.name, -GRACIA) < GRACIA:
                continue
            try:
                tamano = e.stat().st_size
                os.unlink(e.path)
            except OSError:
                continue
            total -= tamano
            fuera.append(e.name)
    for nombre in fuera:
        print(f"[comprimidos] caché: expulsado {nombre}", flush=True)
    return fuera


def _adoptar(descomprimido: Path, sha: str, ext: str) -> Path:
    """Mete en la caché un descomprimido ya verificado (rename: atómico)."""
    destino = CACHE / f"{sha}{ext}"
    expulsar(extra=descomprimido.stat().st_size)
    with _candado:
        os.replace(descomprimido, destino)
        os.utime(destino)
        _recien[destino.name] = time.monotonic()
    return destino


def _en_cache(sha: str, ext: str, tamano: int) -> Path | None:
    p = CACHE / f"{sha}{ext}"
    try:
        return p if p.stat().st_size == tamano else None
    except OSError:
        return None


def _hacer_sitio(necesario: int, exacto: bool = True) -> None:
    """Antes de descomprimir. Con un tamaño solo estimado (un .xcz aún sin
    verificar) no se vacía la caché por adelantado: se ajusta al meterlo
    (_adoptar, con el tamaño real); aquí solo se mira el disco."""
    if exacto:
        expulsar(extra=necesario)
    if shutil.disk_usage(config.DATOS).free < necesario + MARGEN_DISCO:
        raise Fallo("no hay espacio libre en disco para descomprimir el juego")


def _descomprimir(comprimido: Path, trabajo: _Trabajo) -> None:
    try:
        meta = info(comprimido) or _identificar_por_sha(comprimido)
        if meta is None:
            # Llegó comprimido y aún no se había verificado: se verifica ya.
            _hacer_sitio(comprimido.stat().st_size * 3, exacto=False)
            verificar_subido(comprimido)
        else:
            _hacer_sitio(meta["tamano"])
            carpeta = _nueva_carpeta_trabajo()
            try:
                t0 = time.monotonic()
                salida = descomprimir_a(comprimido, carpeta)
                sha, tamano = _sha(salida)
                if (sha, tamano) != (meta["sha"], meta["tamano"]):
                    raise Fallo("lo descomprimido no coincide con el SHA guardado")
                _adoptar(salida, sha, salida.suffix.lower())
                print(f"[comprimidos] descomprimido {_rel(comprimido)} en {time.monotonic() - t0:.1f} s", flush=True)
            finally:
                shutil.rmtree(carpeta, ignore_errors=True)
    except Exception as e:  # noqa: BLE001 -- lo ven las peticiones que esperan
        trabajo.error = str(e) or e.__class__.__name__
        print(f"[comprimidos] no se pudo descomprimir {_rel(comprimido)}: {trabajo.error}", flush=True)
    finally:
        trabajo.fin = time.monotonic()
        trabajo.hecho.set()


def _listo(comprimido: Path) -> Path | None:
    meta = info(comprimido)
    if meta is None:
        return None
    return _en_cache(meta["sha"], LOGICO_DE[comprimido.suffix.lower()], meta["tamano"])


def preparar(comprimido: Path) -> _Trabajo | None:
    """Lanza (una sola vez) la descompresión a la caché si hace falta. None
    si ya está lista."""
    if _listo(comprimido):
        return None
    if not disponible():
        raise HTTPException(503, "Esta librería no tiene las claves de Switch: no puede descomprimir este juego.")
    clave = str(comprimido)
    with _candado:
        trabajo = _trabajos.get(clave)
        # Un fallo se recuerda un minuto (no se reintenta en cada petición).
        if trabajo is None or (trabajo.hecho.is_set() and (not trabajo.error or time.monotonic() - trabajo.fin > 60)):
            trabajo = _Trabajo()
            _trabajos[clave] = trabajo
            _hilos.submit(_descomprimir, comprimido, trabajo)
    return trabajo


class _RespuestaCache(FileResponse):
    """FileResponse (con Range) que suelta su fichero de la caché al acabar,
    también si el cliente corta a medias (al contrario que BackgroundTask)."""

    def __init__(self, *args, clave: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._clave = clave

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            _soltar(self._clave)


def _soltar(nombre: str) -> None:
    with _candado:
        _en_uso[nombre] = _en_uso.get(nombre, 1) - 1
        if _en_uso[nombre] <= 0:
            _en_uso.pop(nombre, None)
            try:
                os.utime(CACHE / nombre)      # LRU: cuenta desde la última descarga
            except OSError:
                pass
    expulsar()


def _coger(comprimido: Path) -> Path | None:
    """Marca en uso el fichero de la caché (bajo el candado: así no se puede
    expulsar entre que se encuentra y se empieza a servir)."""
    with _candado:
        ruta = _listo(comprimido)
        if ruta is None:
            return None
        _en_uso[ruta.name] = _en_uso.get(ruta.name, 0) + 1
        _recien.pop(ruta.name, None)            # ya lo protege «en uso»
        os.utime(ruta)
        return ruta


def respuesta(comprimido: Path, nombre: str, espera: float) -> FileResponse:
    """Respuesta para descargar `comprimido` descomprimido, como `nombre`.
    Bloquea hasta `espera` segundos (llamar desde un hilo, nunca en el bucle
    de eventos); si no está listo para entonces, 503 + Retry-After."""
    trabajo = preparar(comprimido)
    if trabajo is not None:
        trabajo.hecho.wait(espera)
        if trabajo.hecho.is_set() and trabajo.error:
            raise HTTPException(500, f"No se pudo preparar el juego: {trabajo.error}")
    ruta = _coger(comprimido)
    if ruta is None:
        raise HTTPException(503, "Preparando el juego en el servidor (se está descomprimiendo); vuelve a intentarlo "
                                 "en unos segundos.", headers={"Retry-After": str(REINTENTAR_EN)})
    try:
        return _RespuestaCache(ruta, media_type="application/octet-stream", filename=nombre, clave=ruta.name)
    except BaseException:
        _soltar(ruta.name)
        raise


# =====================================================================
# Línea de órdenes
# =====================================================================

def _gb(n: int) -> str:
    return f"{n / 1024**3:.2f} GB"


def _importar(origen: Path, nombre_juego: str) -> int:
    import asyncio
    import main as nucleo
    if not origen.is_file() or origen.suffix.lower() not in (*COMPRIMIDO_DE, *LOGICO_DE):
        print(f"No es un .nsp/.xci/.nsz/.xcz: {origen}")
        return 1
    I, T = nucleo.PETICION_INTERNA, nucleo.TOKEN_SISTEMA
    nucleo.crear_grupo(I, T, system=SISTEMA, nombre_juego=nombre_juego)
    carpeta = config.ROOT / SISTEMA / nombre_juego
    try:
        # Lo mismo que hace anadir_fichero_grupo con lo subido, pero copiando del disco.
        temporal = carpeta / f".importando{origen.suffix.lower()}"
        shutil.copyfile(origen, temporal)
        temporal.rename(carpeta / origen.name)
        nucleo._registrar_rol_grupo(carpeta, origen.name, "base")
        asyncio.run(nucleo.finalizar_grupo(I, T, system=SISTEMA, nombre_juego=nombre_juego, es_multidisco=False,
                                           cover=None))
    except BaseException:
        shutil.rmtree(carpeta, ignore_errors=True)
        raise
    final = [p.name for p in (config.ROOT / SISTEMA).iterdir() if p.is_dir() and p.stem == nombre_juego]
    print(f"Importado como {final}: el servicio lo verifica o comprime en segundo plano.")
    return 0


def _cli(argv: list[str]) -> int:
    inicializar()
    if argv[:1] == ["limite"] and len(argv) == 2:
        valor = float(argv[1].replace(",", "."))
        if valor < 0:
            print("El límite no puede ser negativo.")
            return 1
        ajustes.guardar("cache_switch_gb", str(valor))
        print(f"Límite de la caché de Switch: {valor} GB (se aplica en menos de un minuto).")
        return 0
    if argv[:1] == ["importar"] and len(argv) == 3:
        return _importar(Path(argv[1]), argv[2])
    if argv[:1] in ([], ["estado"]):
        print(f"Claves: {'sí' if disponible() else 'NO'} ({CLAVES}) · modo {MODO}, nivel {NIVEL}, {HILOS} hilos")
        with _conectar() as con:
            filas = con.execute("SELECT * FROM comprimidos ORDER BY fecha").fetchall()
            fallos = con.execute("SELECT * FROM comprimidos_fallos ORDER BY fecha").fetchall()
        for f in filas:
            print(f"  {f['sha'][:12]}… {_gb(f['tamano'])} -> {_gb(f['tamano_comp'])} "
                  f"({100 * f['tamano_comp'] / f['tamano']:.1f} %), comprimir {f['seg_comprimir']} s, "
                  f"descomprimir {f['seg_descomprimir']} s, {f['origen']}")
        for f in fallos:
            print(f"  FALLO {f['ruta']}: {f['error']}")
        pend = pendientes()
        print(f"Pendientes: {len(pend)}" + "".join(f"\n  {t} {_rel(p)}" for t, p in pend))
        u = uso_cache()
        print(f"Caché: {_gb(u['bytes'])} de {_gb(u['limite'])} en {u['ficheros']} ficheros")
        for e in sorted(_entradas(), key=lambda e: e.stat().st_mtime):
            print(f"  {e.name}  {_gb(e.stat().st_size)}  última descarga "
                  f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e.stat().st_mtime))}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
