"""
Índice de librerías públicas de RomsLibrary: un servicio APARTE de
cualquier librería (en sund.es, su propio contenedor y su propia base de
datos). Solo guarda nombre, URL y credenciales de las librerías públicas;
nunca juegos, catálogos ni descargas.

    uvicorn indice:app        (mismo contenedor que el servicio, otro comando)

Alta (prueba de que controlas la URL que declaras): la librería genera un
código de un solo uso y lo manda junto a su URL; el índice pide
GET <url>/federacion/ping y solo da de alta si esa URL devuelve el mismo
código. Devuelve un id + secreto (aquí solo se guarda el hash del secreto).

Entre librerías nunca viaja ese secreto: para leer el catálogo de otra se
pide aquí un TICKET (10 min, solo para esa librería de destino) y la de
destino lo valida aquí.

Retirada de una librería (petición de los titulares de derechos o de su
propio dueño): desde la línea de órdenes del contenedor,

    python indice.py listar
    python indice.py retirar <id|url> "motivo"
    python indice.py readmitir <id|url>

Una librería retirada deja de verse y no puede volver a darse de alta sola.
Las que no contactan con el índice en DIAS_INACTIVA dejan de listarse (se
dan de alta cada 6 h mientras siguen públicas).

CONFIANZA (0-100) de cada librería: la decide SOLO el administrador del
índice. La librería de referencia (REFERENCIA, sund.es) vale siempre 100 y
manda en el linaje de versiones (ver linaje.py); las demás aportan linaje
solo desde linaje.UMBRAL. Cada 6 h el índice lee los catálogos y calcula una
SUGERENCIA de reputación (nunca se aplica sola), comparando con la referencia:
juegos con el mismo SHA que su versión actual, juegos más al día que ella
(su cadena la continúa), juegos anticuados y contradicciones de historial.

    python indice.py reputacion                  # sugerencias y motivos
    python indice.py confianza <id|url> <0-99>   # fijar la confianza
"""

import hashlib
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path

import asyncio
import contextlib
import json

import httpx
from fastapi import FastAPI, HTTPException, Request

import linaje
import redsegura

DATOS = Path(os.environ.get("ROMSLIBRARY_DATOS", "/datos"))
DB_PATH = DATOS / "indice.db"
DURACION_TICKET = 600
DIAS_INACTIVA = 7
# Solo para pruebas en red local: admite URLs privadas/http. En producción,
# NUNCA (el índice haría peticiones a la red interna).
URLS_PRIVADAS = os.environ.get("ROMSLIBRARY_FEDERACION_URLS_PRIVADAS", "").lower() in ("1", "true", "si", "sí")
# La librería de referencia: confianza 100 siempre, manda en el linaje.
REFERENCIA = os.environ.get("ROMSLIBRARY_INDICE_REFERENCIA", "https://sund.es").rstrip("/")
CADA_REPUTACION = 6 * 3600


@contextlib.asynccontextmanager
async def _ciclo(_app):
    tarea = asyncio.create_task(_bucle_reputacion())
    yield
    tarea.cancel()


app = FastAPI(title="Índice RomsLibrary", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_ciclo)


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con


def inicializar() -> None:
    DATOS.mkdir(parents=True, exist_ok=True)
    with _conectar() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS servicios (
                id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, nombre TEXT NOT NULL,
                secreto_hash TEXT NOT NULL, publico INTEGER NOT NULL DEFAULT 1,
                alta REAL NOT NULL, ultimo_contacto REAL NOT NULL,
                retirada REAL, motivo_retirada TEXT
            );
            CREATE TABLE IF NOT EXISTS tickets (
                ticket TEXT PRIMARY KEY, origen_id TEXT NOT NULL, destino_id TEXT NOT NULL, caduca REAL NOT NULL
            );
            """
        )
        columnas = {f["name"] for f in con.execute("PRAGMA table_info(servicios)")}
        for nombre, tipo in (("confianza", "INTEGER NOT NULL DEFAULT 0"), ("sugerencia", "INTEGER"),
                             ("reputacion", "TEXT"), ("reputacion_fecha", "REAL")):
            if nombre not in columnas:
                con.execute(f"ALTER TABLE servicios ADD COLUMN {nombre} {tipo}")


def _hash_secreto(secreto: str) -> str:
    return hashlib.sha256(secreto.encode()).hexdigest()


def _visible_sql() -> tuple[str, float]:
    """Condición SQL de "se ve en el índice": pública, no retirada y viva."""
    return "publico = 1 AND retirada IS NULL AND ultimo_contacto > ?", time.time() - DIAS_INACTIVA * 86400


def _autenticar(con: sqlite3.Connection, ident: str, secreto: str) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM servicios WHERE id = ?", (ident or "",)).fetchone()
    if fila is None or not secrets.compare_digest(fila["secreto_hash"], _hash_secreto(secreto or "")):
        raise HTTPException(status_code=403, detail="Librería no reconocida por el índice.")
    if fila["retirada"] is not None:
        raise HTTPException(status_code=403, detail=f"Librería retirada del índice: {fila['motivo_retirada']}")
    con.execute("UPDATE servicios SET ultimo_contacto = ? WHERE id = ?", (time.time(), fila["id"]))
    return fila


@app.get("/api/health")  # el HEALTHCHECK de la imagen (compartida con el servicio)
def salud():
    return {"ok": True}


@app.post("/federacion/indice/alta")
async def alta(request: Request):
    """Registra (o vuelve a registrar, con secreto nuevo) una URL verificada."""
    datos = await request.json()
    url = str(datos.get("url", "")).rstrip("/")
    nombre = " ".join(str(datos.get("nombre", "")).split())[:60] or url
    codigo = str(datos.get("codigo", ""))
    if not url.startswith(("https://", "http://")) or not codigo:
        raise HTTPException(status_code=400, detail="Falta la URL pública o el código de verificación.")
    if not URLS_PRIVADAS and not redsegura.url_publica(url):
        raise HTTPException(status_code=400, detail="La URL pública tiene que ser https y resolver a una IP pública.")
    with _conectar() as con:
        fila = con.execute("SELECT * FROM servicios WHERE url = ?", (url,)).fetchone()
    if fila is not None and fila["retirada"] is not None:
        raise HTTPException(status_code=403, detail=f"Librería retirada del índice: {fila['motivo_retirada']}")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as cliente:
            r = await cliente.get(f"{url}/federacion/ping")
        devuelto = r.json().get("alta_pendiente") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError):
        devuelto = None
    if not devuelto or not secrets.compare_digest(str(devuelto), codigo):
        raise HTTPException(status_code=403, detail=f"No se pudo verificar que {url} es tuya "
                                                    "(¿la URL pública es correcta y accesible desde internet?).")
    secreto = secrets.token_urlsafe(32)
    ahora = time.time()
    ident = fila["id"] if fila else secrets.token_hex(8)
    with _conectar() as con:
        con.execute(
            "INSERT INTO servicios (id, url, nombre, secreto_hash, publico, alta, ultimo_contacto) "
            "VALUES (?, ?, ?, ?, 1, ?, ?) ON CONFLICT(url) DO UPDATE SET nombre = excluded.nombre, "
            "secreto_hash = excluded.secreto_hash, publico = 1, ultimo_contacto = excluded.ultimo_contacto",
            (ident, url, nombre, _hash_secreto(secreto), ahora, ahora))
    return {"id": ident, "secreto": secreto}


@app.post("/federacion/indice/baja")
async def baja(request: Request):
    """La librería ha pasado a privada: deja de verse al momento."""
    datos = await request.json()
    with _conectar() as con:
        fila = _autenticar(con, datos.get("id"), datos.get("secreto"))
        con.execute("UPDATE servicios SET publico = 0 WHERE id = ?", (fila["id"],))
        con.execute("DELETE FROM tickets WHERE origen_id = ? OR destino_id = ?", (fila["id"], fila["id"]))
    return {"ok": True}


@app.post("/federacion/indice/servicios")
async def servicios(request: Request):
    datos = await request.json()
    with _conectar() as con:
        yo = _autenticar(con, datos.get("id"), datos.get("secreto"))
        if not yo["publico"]:
            raise HTTPException(status_code=403, detail="Solo las librerías públicas ven a las demás.")
        condicion, limite = _visible_sql()
        filas = con.execute(f"SELECT id, nombre, url, confianza FROM servicios WHERE {condicion} AND id != ? ORDER BY nombre",
                            (limite, yo["id"])).fetchall()
    return [{**dict(f), "confianza": _confianza(f)} for f in filas]


def _confianza(fila) -> int:
    return 100 if fila["url"] == REFERENCIA else max(0, min(99, fila["confianza"] or 0))


@app.post("/federacion/indice/ticket")
async def ticket(request: Request):
    datos = await request.json()
    with _conectar() as con:
        yo = _autenticar(con, datos.get("id"), datos.get("secreto"))
        condicion, limite = _visible_sql()
        destino = con.execute(f"SELECT id FROM servicios WHERE id = ? AND {condicion}",
                              (datos.get("destino", ""), limite)).fetchone()
        if not yo["publico"] or destino is None:
            raise HTTPException(status_code=403, detail="Las dos librerías tienen que ser públicas.")
        nuevo = secrets.token_urlsafe(32)
        con.execute("DELETE FROM tickets WHERE caduca < ?", (time.time(),))
        con.execute("INSERT INTO tickets (ticket, origen_id, destino_id, caduca) VALUES (?, ?, ?, ?)",
                    (nuevo, yo["id"], destino["id"], time.time() + DURACION_TICKET))
    return {"ticket": nuevo, "caduca": time.time() + DURACION_TICKET}


@app.post("/federacion/indice/verificar")
async def verificar(request: Request):
    datos = await request.json()
    with _conectar() as con:
        yo = _autenticar(con, datos.get("id"), datos.get("secreto"))
        propio = con.execute("SELECT caduca FROM tickets WHERE ticket = ? AND origen_id = 'indice' AND destino_id = ? "
                             "AND caduca > ?", (datos.get("ticket", ""), yo["id"], time.time())).fetchone()
        if propio is not None:
            # El propio índice leyendo catálogos para la reputación.
            return {"valido": True, "origen": {"id": "indice", "nombre": "Índice"}, "caduca": propio["caduca"]}
        fila = con.execute(
            "SELECT t.*, s.nombre FROM tickets t JOIN servicios s ON s.id = t.origen_id "
            "WHERE t.ticket = ? AND t.destino_id = ? AND t.caduca > ? AND s.publico = 1 AND s.retirada IS NULL",
            (datos.get("ticket", ""), yo["id"], time.time())).fetchone()
    if fila is None:
        return {"valido": False}
    return {"valido": True, "origen": {"id": fila["origen_id"], "nombre": fila["nombre"]}, "caduca": fila["caduca"]}


inicializar()


# ---------- Reputación (sugerida; la confianza la pone el administrador) ----------

async def _catalogo(cliente: httpx.AsyncClient, servicio) -> list[dict] | None:
    billete = secrets.token_urlsafe(32)
    with _conectar() as con:
        con.execute("INSERT INTO tickets (ticket, origen_id, destino_id, caduca) VALUES (?, 'indice', ?, ?)",
                    (billete, servicio["id"], time.time() + DURACION_TICKET))
    try:
        r = await cliente.get(f"{servicio['url']}/federacion/catalogo", headers={"X-RomsLibrary-Ticket": billete})
        datos = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        datos = None
    if not isinstance(datos, list):
        return None
    juegos = []
    for j in datos[:20000]:
        if not isinstance(j, dict) or not isinstance(j.get("sha"), str) or len(j["sha"]) != 64:
            continue
        ant = [p[0] for p in j.get("anteriores") or [] if isinstance(p, list) and p and isinstance(p[0], str) and len(p[0]) == 64]
        juegos.append(ant + [j["sha"]])
    return juegos


def _puntuar(ref: list[list[str]], cadenas: list[list[str]]) -> tuple[int, dict]:
    lin = linaje.Linaje()
    for c in ref:
        lin.anadir(c, "referencia")
    actuales = {c[-1] for c in ref}
    viejas = {s for c in ref for s in c[:-1]}
    d = {"juegos": len(cadenas), "iguales": 0, "mas_al_dia": 0, "anticuados": 0, "conflictos": 0, "otros": 0}
    for c in cadenas:
        antes = len(lin.conflictos)
        lin.anadir(c, "candidata")
        if len(lin.conflictos) > antes:
            d["conflictos"] += 1
        elif c[-1] in actuales:
            d["iguales"] += 1
        elif c[-1] in viejas:
            d["anticuados"] += 1
        elif any(s in actuales or s in viejas for s in c[:-1]):
            d["mas_al_dia"] += 1                     # continúa la cadena de la referencia
        else:
            d["otros"] += 1                          # juegos que la referencia no tiene: ni suma ni resta
    buenos = d["iguales"] + d["mas_al_dia"]
    comparables = buenos + d["anticuados"]
    if not comparables:
        return 0, d
    # Proporción de juegos al día respecto a la referencia, que solo pesa del
    # todo con 20 juegos en común; cada contradicción de historial resta 25.
    # Tope 95: el 100 es solo de la referencia.
    nota = 100 * buenos / comparables * min(1, buenos / 20) - 25 * d["conflictos"]
    return max(0, min(95, round(nota))), d


async def calcular_reputacion() -> None:
    condicion, limite = _visible_sql()
    with _conectar() as con:
        filas = con.execute(f"SELECT * FROM servicios WHERE {condicion}", (limite,)).fetchall()
    ref = next((f for f in filas if f["url"] == REFERENCIA), None)
    if ref is None:
        return
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=60)) as cliente:
        cat_ref = await _catalogo(cliente, ref)
        if cat_ref is None:
            return
        for f in filas:
            if f["id"] == ref["id"]:
                continue
            cat = await _catalogo(cliente, f)
            if cat is None:
                continue
            nota, detalle = _puntuar(cat_ref, cat)
            with _conectar() as con:
                con.execute("UPDATE servicios SET sugerencia = ?, reputacion = ?, reputacion_fecha = ? WHERE id = ?",
                            (nota, json.dumps(detalle), time.time(), f["id"]))


async def _bucle_reputacion():
    await asyncio.sleep(60)
    while True:
        try:
            await calcular_reputacion()
        except Exception as e:  # noqa: BLE001 -- el bucle no puede morir
            print(f"[indice] reputación: {e}", flush=True)
        await asyncio.sleep(CADA_REPUTACION)


# ---------- Línea de órdenes (retiradas y confianza) ----------

def _cli(argumentos: list[str]) -> int:
    orden = argumentos[0] if argumentos else ""
    with _conectar() as con:
        if orden == "listar":
            for f in con.execute("SELECT * FROM servicios ORDER BY alta"):
                estado = (f"RETIRADA ({f['motivo_retirada']})" if f["retirada"] is not None
                          else "pública" if f["publico"] else "privada")
                visto = time.strftime("%Y-%m-%d %H:%M", time.localtime(f["ultimo_contacto"]))
                print(f"{f['id']}  {f['url']}  «{f['nombre']}»  {estado}  confianza {_confianza(f)}  último contacto {visto}")
            return 0
        if orden == "reputacion":
            if len(argumentos) > 1 and argumentos[1] == "ahora":
                asyncio.run(calcular_reputacion())
            for f in con.execute("SELECT * FROM servicios ORDER BY confianza DESC, nombre"):
                cuando = time.strftime("%Y-%m-%d %H:%M", time.localtime(f["reputacion_fecha"])) if f["reputacion_fecha"] else "nunca"
                print(f"{f['id']}  «{f['nombre']}»  confianza {_confianza(f)}  sugerencia {f['sugerencia'] if f['sugerencia'] is not None else '-'}"
                      f"  ({cuando})  {f['reputacion'] or ''}")
            return 0
        if orden == "confianza" and len(argumentos) >= 3:
            fila = con.execute("SELECT * FROM servicios WHERE id = ? OR url = ?",
                               (argumentos[1], argumentos[1].rstrip("/"))).fetchone()
            if fila is None:
                print("No existe esa librería en el índice.")
                return 1
            if fila["url"] == REFERENCIA:
                print("La librería de referencia tiene siempre confianza 100.")
                return 1
            valor = max(0, min(99, int(argumentos[2])))
            con.execute("UPDATE servicios SET confianza = ? WHERE id = ?", (valor, fila["id"]))
            print(f"confianza de «{fila['nombre']}»: {valor}")
            return 0
        if orden in ("retirar", "readmitir") and len(argumentos) >= 2:
            fila = con.execute("SELECT id FROM servicios WHERE id = ? OR url = ?",
                               (argumentos[1], argumentos[1].rstrip("/"))).fetchone()
            if fila is None:
                print("No existe esa librería en el índice.")
                return 1
            if orden == "retirar":
                motivo = " ".join(argumentos[2:]) or "retirada por el administrador del índice"
                con.execute("UPDATE servicios SET retirada = ?, motivo_retirada = ? WHERE id = ?",
                            (time.time(), motivo[:200], fila["id"]))
                con.execute("DELETE FROM tickets WHERE origen_id = ? OR destino_id = ?", (fila["id"], fila["id"]))
            else:
                con.execute("UPDATE servicios SET retirada = NULL, motivo_retirada = NULL WHERE id = ?", (fila["id"],))
            print(f"{orden}: {fila['id']} hecho.")
            return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
