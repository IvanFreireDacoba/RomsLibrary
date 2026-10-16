"""
Ficha de cada juego, lo que no sale del fichero:

- título: el nombre común de las versiones en distintos idiomas de un mismo
  juego (por defecto, el nombre del fichero sin extensión). La app junta las
  de un mismo título en una sola entrada e instala la española; si no hay,
  la inglesa (ver Juego/MainActivity en la app). Cada idioma es un fichero
  distinto, así que lleva su propia cadena de versiones (versiones.py).
- idiomas: los que indica quien sube el juego. Si no se han indicado, se
  deducen del nombre oficial del volcado (No-Intro/Redump: "(Spain)",
  "(En,Fr,De,Es,It)"...) o de etiquetas del nombre ("(ES)", "[EN]"...).
- archivo corrupto: lo marca el administrador o, si no ha dicho nada, sale
  solo de la verificación (volcado "dudoso", ver verificacion.py). El
  administrador puede quitar la marca automática ("está bien"). Su marca es
  de UNA versión (corrupto_sha): al subir otra deja de aplicarse. Lo que el
  administrador confirma como corrupto no se sirve (ver federacion.py).

- visibilidad: cada juego es PRIVADO (solo lo ve quien tiene acceso a la
  librería: su clave o una consola aceptada, ver accesos.py) o COMPARTIDO
  (lo ve cualquiera si la librería es pública, y se federa). Se elige al
  subirlo, siempre: por defecto privado, y compartir exige declarar la BASE
  (por qué se puede compartir). Un juego sin ficha es privado. Si el
  contenido es un juego publicado según No-Intro/Redump, el gestor lo avisa
  y pide confirmación expresa (no se guarda quién confirma: la decisión y su
  responsabilidad son de quien comparte). Nunca se bloquea.

Se guarda por ruta ("<sistema>/<nombre>"); renombrar y borrar la mueven o la
quitan (versiones.renombrado / gestor).
"""

import re
import sqlite3
import time
from pathlib import Path

import config

# Por qué se puede compartir un juego (lo declara quien lo comparte).
BASES = {
    "homebrew": "Homebrew o juego propio",
    "libre": "Licencia libre",
    "permiso": "Permiso del titular de los derechos",
    "fangame": "Fan game original (sin código de juegos comerciales)",
    "autor": "Enlace a la web de su autor",
}
# Las que necesitan detalle: qué licencia, o quién dio el permiso y cómo.
BASES_CON_DETALLE = {"libre", "permiso"}

IDIOMAS = {"es": "Español", "en": "Inglés", "ja": "Japonés", "fr": "Francés", "de": "Alemán", "it": "Italiano",
           "pt": "Portugués"}
_REGIONES = {"spain": "es", "mexico": "es", "latin america": "es", "argentina": "es", "usa": "en", "uk": "en",
             "australia": "en", "canada": "en", "japan": "ja", "france": "fr", "germany": "de", "italy": "it",
             "portugal": "pt", "brazil": "pt"}
_PALABRAS = {"es": "es", "esp": "es", "spa": "es", "español": "es", "espanol": "es", "spanish": "es", "castellano": "es",
             "en": "en", "eng": "en", "english": "en", "ingles": "en", "inglés": "en",
             "ja": "ja", "jp": "ja", "jap": "ja", "japanese": "ja", "fr": "fr", "french": "fr", "de": "de",
             "german": "de", "it": "it", "italian": "it", "pt": "pt", "br": "pt", "portuguese": "pt"}


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def inicializar() -> None:
    with _conectar() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS fichas (
                ruta TEXT PRIMARY KEY, titulo TEXT, idiomas TEXT, corrupto INTEGER, motivo TEXT NOT NULL DEFAULT '',
                usuario TEXT NOT NULL DEFAULT '', fecha REAL
            )""")
        columnas = {c["name"] for c in con.execute("PRAGMA table_info(fichas)")}
        if "compartido" not in columnas:
            # Todo lo que ya había queda PRIVADO hasta que su dueño lo revise.
            con.execute("ALTER TABLE fichas ADD COLUMN compartido INTEGER NOT NULL DEFAULT 0")
            con.execute("ALTER TABLE fichas ADD COLUMN base TEXT")
            con.execute("ALTER TABLE fichas ADD COLUMN base_detalle TEXT NOT NULL DEFAULT ''")
            con.execute("ALTER TABLE fichas ADD COLUMN compartido_fecha REAL")
        if "aviso" not in columnas:
            con.execute("ALTER TABLE fichas ADD COLUMN aviso TEXT NOT NULL DEFAULT ''")
        for sobra in ("compartido_por", "aviso_confirmado"):     # no se guarda quién comparte ni quién confirma
            if sobra in columnas:
                con.execute(f"ALTER TABLE fichas DROP COLUMN {sobra}")
        if "corrupto_sha" not in columnas:
            # Las marcas que ya había son de la versión que hay ahora.
            con.execute("ALTER TABLE fichas ADD COLUMN corrupto_sha TEXT")
            if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'hashes'").fetchone():
                con.execute("UPDATE fichas SET corrupto_sha = (SELECT sha FROM hashes h WHERE h.ruta = fichas.ruta) "
                            "WHERE corrupto IS NOT NULL")


def limpiar_idiomas(valores) -> list[str]:
    if isinstance(valores, str):
        valores = valores.split(",")
    return [i for i in IDIOMAS if i in {str(v).strip().lower() for v in (valores or [])}]


def limpiar_titulo(titulo) -> str:
    return " ".join(str(titulo or "").split())[:120]


def visibilidad(valor, base, detalle) -> tuple[bool, str | None, str]:
    """Valida la elección de visibilidad (obligatoria al subir y al editar)."""
    if valor not in ("privado", "compartido"):
        raise ValueError("Elige si el juego es solo para ti o compartido.")
    if valor == "privado":
        return False, None, ""
    if base not in BASES:
        raise ValueError("Para compartir un juego tienes que indicar por qué se puede compartir.")
    detalle = " ".join(str(detalle or "").split())[:200]
    if base in BASES_CON_DETALLE and not detalle:
        raise ValueError("Indica " + ("la licencia." if base == "libre" else "quién dio el permiso y cómo."))
    return True, base, detalle


def guardar(sistema: str, nombre: str, usuario: str, **campos) -> None:
    """Campos: titulo, idiomas (lista), corrupto (True/False/None = automático), motivo,
    sha (la versión a la que se refiere la marca de corrupto) y compartido
    (tupla ya validada por visibilidad())."""
    ruta = f"{sistema}/{nombre}"
    with _conectar() as con:
        con.execute("INSERT OR IGNORE INTO fichas (ruta) VALUES (?)", (ruta,))
        if "titulo" in campos:
            con.execute("UPDATE fichas SET titulo = ? WHERE ruta = ?", (limpiar_titulo(campos["titulo"]) or None, ruta))
        if "idiomas" in campos:
            con.execute("UPDATE fichas SET idiomas = ? WHERE ruta = ?",
                        (",".join(limpiar_idiomas(campos["idiomas"])) or None, ruta))
        if "corrupto" in campos:
            c = campos["corrupto"]
            con.execute("UPDATE fichas SET corrupto = ?, motivo = ?, corrupto_sha = ? WHERE ruta = ?",
                        (None if c is None else int(bool(c)), str(campos.get("motivo") or "")[:300],
                         None if c is None else campos.get("sha"), ruta))
        if "compartido" in campos:
            compartido, base, detalle = campos["compartido"]
            aviso = str(campos.get("aviso") or "")[:500] if compartido else ""
            con.execute("UPDATE fichas SET compartido = ?, base = ?, base_detalle = ?, compartido_fecha = ?, aviso = ? "
                        "WHERE ruta = ?", (int(compartido), base, detalle, time.time() if compartido else None, aviso, ruta))
        con.execute("UPDATE fichas SET usuario = ?, fecha = ? WHERE ruta = ?", (usuario, time.time(), ruta))


def renombrar(sistema: str, viejo: str, nuevo: str) -> None:
    with _conectar() as con:
        con.execute("UPDATE OR REPLACE fichas SET ruta = ? WHERE ruta = ?", (f"{sistema}/{nuevo}", f"{sistema}/{viejo}"))


def borrar(sistema: str, nombre: str) -> None:
    with _conectar() as con:
        con.execute("DELETE FROM fichas WHERE ruta = ?", (f"{sistema}/{nombre}",))


def todas() -> dict[str, dict]:
    with _conectar() as con:
        return {f["ruta"]: dict(f) for f in con.execute("SELECT * FROM fichas").fetchall()}


def _marca(ficha: dict | None, sha: str | None) -> int | None:
    """1/0 que puso el administrador a ESTA versión; None si no dijo nada de ella."""
    if not ficha or ficha.get("corrupto") is None:
        return None
    return ficha["corrupto"] if ficha.get("corrupto_sha") in (None, sha) else None


def _ficha(sistema: str, nombre: str) -> dict | None:
    with _conectar() as con:
        f = con.execute("SELECT * FROM fichas WHERE ruta = ?", (f"{sistema}/{nombre}",)).fetchone()
    return dict(f) if f else None


def confirmado_corrupto(sistema: str, nombre: str, sha: str | None) -> bool:
    return _marca(_ficha(sistema, nombre), sha) == 1


def confirmado_bueno(sistema: str, nombre: str, sha: str | None) -> bool:
    return _marca(_ficha(sistema, nombre), sha) == 0


def titulo_por_defecto(nombre: str) -> str:
    return Path(nombre).stem if Path(nombre).suffix else nombre


def deducir_idiomas(nombre_volcado: str | None, nombre_fichero: str) -> list[str]:
    encontrados: set[str] = set()
    for parte in re.findall(r"\(([^)]*)\)", nombre_volcado or ""):
        trozos = [t.strip() for t in parte.split(",")]
        if all(re.fullmatch(r"[A-Z][a-z](-[A-Za-z]+)?", t) for t in trozos):
            encontrados |= {t[:2].lower() for t in trozos}         # lista de idiomas: (En,Fr,De,Es,It)
    if not encontrados:
        for parte in re.findall(r"\(([^)]*)\)", nombre_volcado or ""):
            for region in (r.strip().lower() for r in parte.split(",")):
                if region in _REGIONES:
                    encontrados.add(_REGIONES[region])
                elif region == "europe":
                    encontrados.add("en")
    if not encontrados:
        for parte in re.findall(r"[\[(]([^\])]*)[\])]", nombre_fichero):
            for palabra in re.split(r"[\s,+/-]+", parte.lower()):
                if palabra in _PALABRAS:
                    encontrados.add(_PALABRAS[palabra])
    return [i for i in IDIOMAS if i in encontrados]


def completar(j: dict, ficha: dict | None, verif: dict | None) -> None:
    """Añade titulo, idiomas, corrupto y motivoCorrupto a un juego del listado."""
    ficha = ficha or {}
    j["titulo"] = ficha.get("titulo") or titulo_por_defecto(j["filename"])
    indicados = limpiar_idiomas(ficha.get("idiomas"))
    j["idiomas"] = indicados or deducir_idiomas(verif.get("nombre") if verif else None, j["filename"])
    j["idiomasDeducidos"] = not indicados and bool(j["idiomas"])     # solo para el gestor
    marca = _marca(ficha, j.get("sha"))
    if marca is None and verif and verif.get("estado") == "dudoso":
        j["corrupto"], j["motivoCorrupto"] = True, "Volcado dudoso: " + (verif.get("detalle") or "")
        j["corruptoAutomatico"] = True
    else:
        j["corrupto"] = marca == 1
        j["motivoCorrupto"] = (ficha.get("motivo") or "Marcado por el administrador.") if marca == 1 else ""
        j["corruptoAutomatico"] = False
    j["corruptoDescartado"] = marca == 0
    j["compartido"] = bool(ficha.get("compartido"))
    j["base"] = ficha.get("base") if j["compartido"] else None
    j["baseDetalle"] = (ficha.get("base_detalle") or "") if j["compartido"] else ""
    j["avisoCompartir"] = (ficha.get("aviso") or "") if j["compartido"] else ""                 # para el gestor                         # el administrador dijo que está bien
