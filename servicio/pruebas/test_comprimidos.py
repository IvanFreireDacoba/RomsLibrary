"""
Pruebas de comprimidos.py (Switch guardado como .nsz/.xcz, servido como
.nsp/.xci). nsz se sustituye por pruebas/nsz_falso.py: aquí se prueba la
lógica (elección de formato, verificación, LRU, candado, Range), no nsz.

    cd servicio && ROMSLIBRARY_DATOS=$(mktemp -d) python -m pytest -q pruebas
(en jarjar se lanzan dentro de la imagen del servicio, ver la cabecera de
comprimidos.py).
"""

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
_DATOS = Path(tempfile.mkdtemp(prefix="rl-pruebas-"))
os.environ["ROMSLIBRARY_DATOS"] = str(_DATOS)
os.environ["ROMSLIBRARY_NSZ"] = str(AQUI / "nsz_falso.py")
os.environ["FALSO_NSZ_LOG"] = str(_DATOS / "nsz.log")
(_DATOS / "keys").mkdir(parents=True, exist_ok=True)
(_DATOS / "keys" / "prod.keys").write_text("falsas\n")
sys.path.insert(0, str(AQUI.parent / "app"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402,F401  (inicializa la BD y las tablas)
import ajustes  # noqa: E402
import comprimidos as C  # noqa: E402
import config  # noqa: E402
import federacion  # noqa: E402

SW = config.ROOT / "switch"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _llamadas() -> list[str]:
    p = Path(os.environ["FALSO_NSZ_LOG"])
    return p.read_text().splitlines() if p.exists() else []


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    import shutil
    for d in (SW, C.CACHE):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    Path(os.environ["FALSO_NSZ_LOG"]).unlink(missing_ok=True)
    with C._conectar() as con:
        con.execute("DELETE FROM comprimidos")
        con.execute("DELETE FROM comprimidos_fallos")
        con.execute("DELETE FROM hashes")
    C._trabajos.clear()
    C._en_uso.clear()
    C._recien.clear()
    ajustes.guardar("cache_switch_gb", "15")
    monkeypatch.setattr(C, "MARGEN_DISCO", 0)
    monkeypatch.delenv("FALSO_NSZ_ROMPER", raising=False)
    monkeypatch.delenv("FALSO_NSZ_PAUSA", raising=False)
    yield


def _grupo(nombre="Juego", ficheros=None) -> Path:
    """Grupo de Switch ya finalizado («Juego.nsp/»), con ficheros viejos (en reposo)."""
    g = SW / f"{nombre}.nsp"
    g.mkdir()
    ficheros = ficheros or {f"{nombre}.nsp": os.urandom(3000) + b"A" * 50000, "Update (1.1).nsp": b"U" * 7000}
    roles = []
    for n, datos in ficheros.items():
        (g / n).write_bytes(datos)
        os.utime(g / n, (time.time() - 1000, time.time() - 1000))
        roles.append({"nombre": C.nombre_logico(n), "rol": "base" if n.startswith(nombre) else "update"})
    (g / "grupo.json").write_text(json.dumps({"tipo": "switch_base_dlc", "ficheros": roles}))
    return g


# ---------------------------------------------------------------- formato

def test_nombres_logicos():
    assert C.nombre_logico("A.nsz") == "A.nsp"
    assert C.nombre_logico("sub/A.XCZ") == "sub/A.xci"
    assert C.nombre_logico("A.nsp") == "A.nsp"
    assert C.nombre_comprimido("A.xci") == "A.xcz"
    assert C.nombre_comprimido("grupo.json") == "grupo.json"


def test_eleccion_de_formato_y_modo(monkeypatch):
    assert C.COMPRIMIDO_DE == {".nsp": ".nsz", ".xci": ".xcz"}
    assert C._modo_nsz() == ["-B"]
    monkeypatch.setattr(C, "MODO", "solido")
    assert C._modo_nsz() == ["-S"]


def test_pendientes_solo_grupos_finalizados_y_en_reposo():
    g = _grupo()
    (SW / "Subiendo").mkdir()                                  # grupo sin finalizar (sin extensión)
    (SW / "Subiendo" / "X.nsp").write_bytes(b"x")
    (SW / "Suelto.nsp").write_bytes(b"x")                      # fichero suelto: no se toca
    (g / "Reciente.nsp").write_bytes(b"r")                     # recién llegado: aún no
    tareas = [(t, p.name) for t, p in C.pendientes()]
    assert tareas == [("comprimir", "Juego.nsp"), ("comprimir", "Update (1.1).nsp")]


# ---------------------------------------------------------------- verificación

def test_comprimir_verifica_y_sustituye():
    g = _grupo()
    original = (g / "Juego.nsp").read_bytes()
    sha_grupo_antes = federacion._sha_juego(g)
    cifras = C.comprimir(g / "Juego.nsp")
    assert not (g / "Juego.nsp").exists() and (g / "Juego.nsz").is_file()
    assert cifras["tamano"] == len(original) and cifras["tamano_comp"] < len(original)
    assert C.info(g / "Juego.nsz")["sha"] == _sha(original)
    # Lo verificado pasa a la caché, idéntico al original.
    assert (C.CACHE / f"{_sha(original)}.nsp").read_bytes() == original
    # La identidad del juego (SHA del grupo con nombres lógicos) no cambia.
    assert federacion._sha_juego(g) == sha_grupo_antes
    assert [n for n, _ in C.ficheros_logicos(g)] == ["Juego.nsp", "Update (1.1).nsp", "grupo.json"]
    assert C.tamano_logico(g / "Juego.nsz") == len(original)


def test_si_no_sale_identico_se_queda_el_original(monkeypatch):
    g = _grupo()
    monkeypatch.setenv("FALSO_NSZ_ROMPER", "1")
    with pytest.raises(C.Fallo, match="NO sale idéntico"):
        C.comprimir(g / "Juego.nsp")
    assert (g / "Juego.nsp").is_file() and not (g / "Juego.nsz").exists()
    assert list(C.CACHE.iterdir()) == []
    assert list(C.TRABAJO.iterdir()) == []                    # sin restos en /datos/tmp


def test_vuelta_anota_el_fallo_y_no_reintenta(monkeypatch):
    g = _grupo(ficheros={"Juego.nsp": b"J" * 1000})
    monkeypatch.setenv("FALSO_NSZ_ROMPER", "1")
    C._vuelta()
    n = len(_llamadas())
    assert C._fallo(g / "Juego.nsp")
    C._vuelta()
    assert len(_llamadas()) == n                               # no se vuelve a intentar


def test_subido_ya_comprimido_se_verifica():
    g = _grupo(ficheros={"Juego.nsp": b"J" * 1000})
    C.comprimir(g / "Juego.nsp")
    with C._conectar() as con:
        con.execute("DELETE FROM comprimidos")                 # como si hubiese llegado ya comprimido
    with pytest.raises(C.SinVerificar):
        federacion._sha_juego(g)
    assert [t for t, _ in C.pendientes()] == ["verificar"]
    C._vuelta()
    assert C.info(g / "Juego.nsz")["sha"] == _sha(b"J" * 1000)
    assert "-V" in " ".join(_llamadas())


def test_hash_del_grupo_sigue_valiendo_tras_comprimir():
    g = _grupo()
    sha = federacion._sha_juego(g)
    tam, mtime = federacion._firma(g)
    with C._conectar() as con:
        con.execute("INSERT INTO hashes (ruta, tamano, mtime, sha) VALUES (?, ?, ?, ?)", (f"switch/{g.name}", tam, mtime, sha))
    C._vuelta()
    assert federacion.sha_conocido("switch", g.name) == sha     # sin volver a calcularlo


# ---------------------------------------------------------------- caché LRU

def _en_cache(nombre: str, tam: int, hace: float) -> Path:
    p = C.CACHE / nombre
    p.write_bytes(b"x" * tam)
    os.utime(p, (time.time() - hace, time.time() - hace))
    return p


def test_lru_expulsa_lo_mas_antiguo_y_nunca_lo_que_se_sirve():
    ajustes.guardar("cache_switch_gb", str(2500 / 1024**3))     # 2500 bytes
    viejo = _en_cache("a" * 64 + ".nsp", 1000, 300)
    medio = _en_cache("b" * 64 + ".nsp", 1000, 200)
    nuevo = _en_cache("c" * 64 + ".xci", 1000, 100)
    C._en_uso[viejo.name] = 1                                  # se está descargando
    fuera = C.expulsar()
    assert fuera == [medio.name]
    assert viejo.exists() and nuevo.exists()
    otro = _en_cache("d" * 64 + ".nsp", 1000, 50)
    C._soltar(viejo.name)                                      # termina: cuenta como recién descargado
    assert C._en_uso == {}
    assert viejo.exists() and otro.exists() and not nuevo.exists()


def test_limite_por_defecto_15_gb():
    with C._conectar() as con:
        con.execute("DELETE FROM ajustes WHERE clave = 'cache_switch_gb'")
    assert C.limite_bytes() == 15 * 1024**3


# ---------------------------------------------------------------- candado y Range

def _comprimido_sin_cache(datos: bytes) -> Path:
    g = _grupo(ficheros={"Juego.nsp": datos})
    C.comprimir(g / "Juego.nsp")
    for p in C.CACHE.iterdir():
        p.unlink()
    return g / "Juego.nsz"


def test_candado_una_sola_descompresion(monkeypatch):
    comprimido = _comprimido_sin_cache(os.urandom(20000))
    antes = sum("-D" in l for l in _llamadas())
    monkeypatch.setenv("FALSO_NSZ_PAUSA", "1")
    trabajos = []
    hilos = [threading.Thread(target=lambda: trabajos.append(C.preparar(comprimido))) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len({id(t) for t in trabajos}) == 1
    trabajos[0].hecho.wait(20)
    assert trabajos[0].error is None
    assert sum("-D" in l for l in _llamadas()) == antes + 1
    assert C.preparar(comprimido) is None                      # ya está en la caché


def _cliente():
    from fastapi import FastAPI
    app = FastAPI()

    @app.get("/f")
    def f(espera: float = 0):
        return C.respuesta(app.state.comprimido, "Juego.nsp", espera)
    return app, TestClient(app)


def test_503_mientras_se_prepara_y_luego_range(monkeypatch):
    datos = os.urandom(100000)
    comprimido = _comprimido_sin_cache(datos)
    app, cli = _cliente()
    app.state.comprimido = comprimido
    monkeypatch.setenv("FALSO_NSZ_PAUSA", "1")
    r = cli.get("/f")
    assert r.status_code == 503 and r.headers["retry-after"] == str(C.REINTENTAR_EN)
    r = cli.get("/f", params={"espera": 20})
    assert r.status_code == 200 and r.content == datos
    assert 'filename="Juego.nsp"' in r.headers["content-disposition"]
    # Reanudación a mitad (lo que hace la app tras un corte).
    r = cli.get("/f", headers={"Range": "bytes=40000-"})
    assert r.status_code == 206 and r.content == datos[40000:]
    assert r.headers["content-range"] == f"bytes 40000-{len(datos) - 1}/{len(datos)}"
    r = cli.get("/f", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == datos[10:20]
    assert C._en_uso == {}                                     # todas las descargas soltaron su fichero


def test_cache_corrupta_no_se_sirve():
    datos = os.urandom(5000)
    comprimido = _comprimido_sin_cache(datos)
    meta = C.info(comprimido)
    (C.CACHE / f"{meta['sha']}.nsp").write_bytes(b"corto")      # tamaño distinto: no vale
    assert C._listo(comprimido) is None


# ---------------------------------------------------------------- API de la app

def test_api_app_lista_y_sirve_nombres_logicos():
    datos = os.urandom(30000)
    g = _grupo(ficheros={"Juego.nsp": datos, "Update (1.1).nsp": b"U" * 500})
    C.comprimir(g / "Juego.nsp")
    cli = TestClient(main.app)
    clave = {"X-App-Token": ajustes.leer("clave_app")}
    r = cli.get("/APIs/RomLibraryApp", params={"accion": "grupo_ficheros", "system": "switch", "grupo": g.name},
                headers=clave)
    assert r.status_code == 200, r.text
    assert r.json() == [{"nombre": "Juego.nsp", "tamano": len(datos), "rol": "base"},
                        {"nombre": "Update (1.1).nsp", "tamano": 500, "rol": "update"}]
    r = cli.get("/APIs/RomLibraryApp", params={"accion": "grupo_fichero", "system": "switch", "grupo": g.name,
                                               "fichero": "Juego.nsp"}, headers={**clave, "Range": "bytes=100-"})
    assert r.status_code == 206 and r.content == datos[100:]
    galeria = main.listar_juegos_galeria(main.PETICION_INTERNA, main.TOKEN_SISTEMA, system="switch", q=None)
    assert galeria[0]["sizeBytes"] == len(datos) + 500 + (g / "grupo.json").stat().st_size


def test_lo_recien_preparado_no_se_expulsa_antes_de_descargarse():
    """Caché más pequeña que el juego: se prepara, la consola vuelve tras el
    503 y lo encuentra (antes se expulsaba y se descomprimía otra vez)."""
    datos = os.urandom(50000)
    comprimido = _comprimido_sin_cache(datos)
    ajustes.guardar("cache_switch_gb", str(1000 / 1024**3))     # 1000 bytes
    trabajo = C.preparar(comprimido)
    trabajo.hecho.wait(20)
    assert C.expulsar() == [] and C._listo(comprimido)
    app, cli = _cliente()
    app.state.comprimido = comprimido
    r = cli.get("/f")
    assert r.status_code == 200 and r.content == datos
    assert C._listo(comprimido) is None                        # descargado: ya se puede expulsar (y se expulsa)
    assert sum("-D" in l for l in _llamadas()) == 2            # comprobar al comprimir + esta, ninguna más


def test_metadatos_nunca_esperan_a_descomprimir(monkeypatch):
    """Arranque de la app con un Switch comprimido SIN caché y la
    descompresión lenta: hola, catálogo, versiones y la lista de ficheros
    del grupo responden al momento (solo la descarga del fichero espera)."""
    datos = os.urandom(30000)
    g = _grupo(ficheros={"Juego.nsp": datos})
    C.comprimir(g / "Juego.nsp")
    for f in C.CACHE.iterdir():
        f.unlink()
    monkeypatch.setenv("FALSO_NSZ_PAUSA", "5")
    cli = TestClient(main.app)
    clave = {"X-App-Token": ajustes.leer("clave_app")}
    for params in ({"accion": "hola"}, {"accion": "sistemas"}, {"accion": "juegos"},
                   {"accion": "juegos", "libreria": "todas"}, {"accion": "versiones"},
                   {"accion": "grupo_ficheros", "system": "switch", "grupo": g.name}):
        t = time.monotonic()
        r = cli.get("/APIs/RomLibraryApp", params=params, headers=clave)
        assert r.status_code == 200, (params, r.text)
        assert time.monotonic() - t < 2, params
    r = cli.get("/APIs/RomLibraryApp", params={"accion": "hola"}, headers=clave)
    assert r.json()["ok"] is True and r.json()["acceso"] == "completo"
    r = cli.get("/APIs/RomLibraryApp", params={"accion": "hola"})
    assert r.json()["acceso"] in ("publico", "clave")


def test_catalogo_gzip_solo_si_se_pide():
    for i in range(40):
        (SW / f"Juego {i:02d} con un nombre bastante largo.nsp").write_bytes(b"x")
    cli = TestClient(main.app)
    clave = {"X-App-Token": ajustes.leer("clave_app")}
    r = cli.get("/APIs/RomLibraryApp", params={"accion": "juegos"}, headers={**clave, "Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip" and len(r.json()) == 40
    r = cli.get("/APIs/RomLibraryApp", params={"accion": "juegos"}, headers={**clave, "Accept-Encoding": "identity"})
    assert "content-encoding" not in r.headers and len(r.json()) == 40
