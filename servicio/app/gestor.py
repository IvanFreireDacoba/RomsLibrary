"""
Gestor web de la librería: /gestor/ (página) y /gestor/api/* (JSON).

Lo protege Authentik delante (proxy inverso con forward-auth): el proxy
solo deja pasar a usuarios autenticados y añade su nombre en
CABECERA_USUARIO y el secreto compartido en X-RomsLibrary-Proxy. Sin ese
secreto se rechaza todo: así no basta con llegar al puerto del servicio
saltándose el proxy para hacerse pasar por administrador.

La página usa rutas RELATIVAS ("api/juegos"), así funciona igual en
https://<servicio>/gestor/ que publicada bajo otro prefijo por el proxy
(en sund.es: /staff/romlibrary/).

Seguridad sobre juegos de OTRAS librerías públicas: aquí solo se puede
renombrar, cambiar la carátula o borrar juegos PROPIOS (las rutas solo
aceptan nombres locales, nunca un "origen"); de los ajenos solo descargar
o copiar a la librería personal.
"""

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import accesos
import ajustes
import config
import externos
import federacion
import fichas
import verificacion
import versiones
import main as nucleo

router = APIRouter(prefix="/gestor")
PAGINA = Path(__file__).parent / "web" / "gestor.html"


def usuario_gestor(request: Request) -> str:
    if not config.SECRETO_PROXY:
        raise HTTPException(status_code=503, detail="Gestor desactivado: falta ROMSLIBRARY_SECRETO_PROXY.")
    secreto = request.headers.get("x-romslibrary-proxy", "")
    usuario = request.headers.get(config.CABECERA_USUARIO.lower(), "")
    secreto_ok = secrets.compare_digest(secreto, config.SECRETO_PROXY)
    if not secreto_ok or not usuario:
        # Diagnóstico sin valores: qué faltó (proxy mal configurado vs. acceso directo).
        print(f"[gestor] acceso rechazado: secreto_proxy={'ok' if secreto_ok else ('mal' if secreto else 'ausente')}, "
              f"usuario={'presente' if usuario else 'ausente'} ({config.CABECERA_USUARIO})", flush=True)
        raise HTTPException(status_code=403, detail="Acceso solo a través del proxy autenticado (Authentik).")
    # CSRF: una acción que cambia datos (subir, renombrar, borrar, ajustes...)
    # solo se acepta si la página que la hace es el propio gestor. Los
    # navegadores siempre mandan Origin en estas peticiones.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origen = request.headers.get("origin")
        if origen:
            from urllib.parse import urlparse
            if urlparse(origen).netloc.lower() != request.headers.get("host", "").lower():
                print(f"[gestor] petición rechazada por Origin ajeno: {origen}", flush=True)
                raise HTTPException(status_code=403, detail="Petición desde otro sitio rechazada.")
    return usuario


Usuario = Depends(usuario_gestor)
I, T = nucleo.PETICION_INTERNA, nucleo.TOKEN_SISTEMA


def _ok(data=None):
    return {"success": True, "data": data} if data is not None else {"success": True}


def _error(e: HTTPException):
    return JSONResponse({"success": False, "message": e.detail}, status_code=e.status_code)


# ---------- Página ----------

@router.get("")
def sin_barra():
    return RedirectResponse("gestor/")


@router.get("/")
def pagina(usuario: str = Usuario):
    return FileResponse(PAGINA, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"})


# ---------- Estado y ajustes ----------

@router.get("/api/estado")
def estado(usuario: str = Usuario):
    a = ajustes.todos()
    return _ok({
        "usuario": usuario,
        "nombre": a.get("nombre", ""),
        "url_publica": a.get("url_publica", ""),
        "publico": a.get("publico") == "1",
        "clave_app": a.get("clave_app", ""),
        "federada": bool(a.get("fed_id")),
        "publica_aceptada": a.get("publica_aceptada", ""),
        "indice_url": config.INDICE_URL,
        "app": nucleo.info_apk(),
    })


@router.post("/api/ajustes")
async def guardar_ajustes(request: Request, usuario: str = Usuario):
    datos = await request.json()
    publico_antes = ajustes.publico()
    if "nombre" in datos:
        nombre = " ".join(str(datos["nombre"]).split())[:60]
        if not nombre:
            return _error(HTTPException(400, "El nombre no puede estar vacío."))
        ajustes.guardar("nombre", nombre)
    if "url_publica" in datos:
        url = str(datos["url_publica"]).strip().rstrip("/")
        if url and not url.startswith(("https://", "http://")):
            return _error(HTTPException(400, "La URL pública tiene que empezar por https://"))
        ajustes.guardar("url_publica", url)
    if datos.get("regenerar_clave_app"):
        ajustes.guardar("clave_app", secrets.token_urlsafe(24))
    if "publico" in datos:
        if datos["publico"] and not publico_antes:
            # Pasar a pública es publicar el catálogo para cualquiera: solo
            # tras aceptar el aviso legal (el gestor lo enseña). A privada se
            # puede volver siempre, sin condiciones.
            if not datos.get("acepto_aviso_publica"):
                return _error(HTTPException(400, "Para hacer pública la librería hay que aceptar el aviso legal."))
            ajustes.guardar("publica_aceptada", time.strftime("%Y-%m-%d %H:%M:%S"))      # sin quién: solo cuándo
        ajustes.guardar("publico", "1" if datos["publico"] else "0")
    aviso = None
    try:
        if ajustes.publico():
            resultado = await federacion.darse_de_alta()
            if not resultado.get("ok"):
                aviso = resultado.get("motivo")
        elif publico_antes:
            await federacion.darse_de_baja()
    except HTTPException as e:
        aviso = e.detail
    return {"success": True, "aviso": aviso}


# ---------- Juegos ----------

@router.get("/api/sistemas")
def sistemas(usuario: str = Usuario):
    return _ok(nucleo.listar_sistemas(I, T))


@router.get("/api/juegos")
async def juegos(request: Request, usuario: str = Usuario):
    q = request.query_params
    if q.get("accion") == "descargar":
        try:
            if q.get("origen"):
                return await federacion.descarga_directa(q["origen"], q["sistema"], q["nombre"])
            return nucleo.descargar_juego(q["sistema"], q["nombre"], I, T)
        except KeyError:
            return _error(HTTPException(400, "Faltan parámetros."))
    libreria = q.get("libreria", "todas")
    if libreria not in ("todas", "personal", "otras"):
        libreria = "todas"
    lista = await federacion.juegos_listado(q.get("sistema") or None, q.get("q") or None, libreria, servir=False)
    return _ok(lista)


@router.get("/api/caratula")
async def caratula(request: Request, usuario: str = Usuario):
    q = request.query_params
    if q.get("origen"):
        return await federacion.caratula_remota(q["origen"], q["sistema"], q["nombre"])
    respuesta = nucleo.servir_caratula(q["sistema"], q["nombre"], I, T)
    respuesta.headers["Cache-Control"] = "private, max-age=3600"
    return respuesta


@router.post("/api/juegos")
async def subir_o_editar(request: Request, usuario: str = Usuario):
    form = await request.form()
    modo = str(form.get("modo", ""))
    sistema = str(form.get("sistema", "")).strip()
    try:
        if modo == "renombrar":
            nucleo.renombrar_juego(I, T, system=sistema, filename=str(form.get("nombre", "")),
                                   nuevo_nombre=str(form.get("nuevo_nombre", "")))
            return _ok()
        if modo == "caratula":
            await nucleo.cambiar_caratula(I, T, system=sistema, filename=str(form.get("nombre", "")),
                                          cover=_fichero(form, "cover"))
            return _ok()
        if modo == "ficha":
            return _ok(await _guardar_ficha(form, sistema, usuario))
        titulo = _titulo_de_otro_idioma(form, sistema) if modo != "actualizar" else ""
        # Juego nuevo: hay que elegir SIEMPRE si es solo para ti o compartido (y por qué).
        compartido = _visibilidad(form, enlace=modo == "enlace") if modo != "actualizar" else None
        if modo == "enlace":
            # Juego nuevo solo con un enlace (+ nombre, carátula e idiomas): ver externos.py.
            r = await externos.anadir_juego(sistema, str(form.get("nombre", "")), str(form.get("url", "")),
                                            str(form.get("pagina", "")), usuario,
                                            caratula=_fichero(form, "cover", obligatorio=False),
                                            idiomas=_idiomas(form), titulo=titulo)
            return _ok({**r, **await _compartir(sistema, r["filename"], usuario, compartido, form)})
        if modo == "grupo":
            await _subir_grupo(form, sistema)
            return _ok(await _compartir(sistema, _nombre_grupo_final(sistema, str(form.get("nombre_juego", "")).strip()),
                                        usuario, compartido, form, titulo=titulo, idiomas=_idiomas(form)))
        if modo == "actualizar":
            return _ok(await versiones.actualizar(sistema, str(form.get("nombre", "")), _fichero(form, "rom"), usuario,
                                                  sin_cabecera_ok=str(form.get("sin_cabecera_ok")) == "1"))
        rom = _fichero(form, "rom")
        r = await nucleo.subir_juego(I, T, system=sistema, filename=str(form.get("nombre", "")).strip(), rom=rom,
                                     cover=_fichero(form, "cover", obligatorio=False))
        return _ok(await _compartir(sistema, r["filename"], usuario, compartido, form, titulo=titulo,
                                    idiomas=_idiomas(form)))
    except HTTPException as e:
        return _error(e)


def _aviso_publicado(sistema: str, nombre: str) -> str | None:
    """Bloqueante (hilo aparte): ¿es un juego publicado según su contenido?"""
    ruta = config.ROOT / sistema / nombre
    sha = federacion.sha_conocido(sistema, nombre)
    if not sha and ruta.exists():
        tamano, mtime = federacion._firma(ruta)          # recién subido: el hilo de hashes aún no ha pasado
        sha = federacion._sha_juego(ruta)
        with federacion._conectar() as con:
            con.execute("INSERT OR REPLACE INTO hashes (ruta, tamano, mtime, sha) VALUES (?, ?, ?, ?)",
                        (f"{sistema}/{nombre}", tamano, mtime, sha))
    try:
        return verificacion.identificar_publicado(sistema, nombre, sha)
    except Exception as e:  # noqa: BLE001 -- el aviso nunca impide guardar
        print(f"[gestor] aviso de {sistema}/{nombre}: {e}", flush=True)
        return None


async def _compartir(sistema: str, nombre: str, usuario: str, compartido: tuple, form, **campos) -> dict:
    """Guarda la ficha con su visibilidad. Si se va a compartir un juego que su
    contenido identifica como publicado (No-Intro/Redump), AVISA: se queda
    «Solo para mí» hasta que el administrador confirme expresamente
    (confirmo_aviso=1), y la confirmación queda anotada. Nunca se bloquea."""
    sistema = nucleo.nombre_seguro(sistema)
    previa = fichas.todas().get(f"{sistema}/{nombre}") or {}
    if compartido and compartido[0] and previa.get("compartido") \
            and (previa.get("base"), previa.get("base_detalle") or "") == (compartido[1], compartido[2]):
        compartido = None             # ya compartido igual: ni se vuelve a preguntar ni se reescribe lo anotado
    aviso = await asyncio.to_thread(_aviso_publicado, sistema, nombre) if compartido and compartido[0] else None
    if aviso and str(form.get("confirmo_aviso")) != "1":
        fichas.guardar(sistema, nombre, usuario, compartido=(False, None, ""), **campos)
        return {"necesitaConfirmacion": True, "aviso": aviso, "sistema": sistema, "nombre": nombre,
                "visibilidad": "compartido", "base": compartido[1], "base_detalle": compartido[2]}
    if compartido is not None:
        campos["compartido"] = compartido
        campos["aviso"] = aviso or ""
    fichas.guardar(sistema, nombre, usuario, **campos)
    return {}


def _visibilidad(form, enlace: bool = False) -> tuple:
    """«Solo para mí» o «Compartido» con su base (fichas.BASES). «Enlace del
    autor» solo vale para los juegos que se descargan de la web de su autor."""
    try:
        valor = fichas.visibilidad(form.get("visibilidad"), form.get("base"), form.get("base_detalle"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if valor[1] == "autor" and not enlace:
        raise HTTPException(400, "«Enlace a la web de su autor» solo vale para juegos que se descargan de la web de su autor.")
    return valor


def _titulo_de_otro_idioma(form, sistema: str) -> str:
    """«Es otro idioma de...» al subir: solo el título de un juego que ya esté
    subido en esa consola (si no, no hay con qué juntarlo). Vacío = juego nuevo."""
    pedido = fichas.limpiar_titulo(form.get("titulo", ""))
    if not pedido:
        return ""
    existentes = {j["titulo"] for j in federacion.juegos_propios(nucleo.nombre_seguro(sistema), servir=False)}
    for titulo in existentes:
        if titulo.lower() == pedido.lower():
            return titulo
    raise HTTPException(400, f"«{pedido}» no es ningún juego subido en esta consola: elige uno de la lista o déjalo vacío.")


def _idiomas(form) -> list[str]:
    return fichas.limpiar_idiomas(str(form.get("idiomas", "")))


def _nombre_grupo_final(sistema: str, nombre_juego: str) -> str:
    """La carpeta que acaba siendo el juego ("Juego.cue", "Juego.m3u", "Juego.nsp"...)."""
    carpeta = config.ROOT / nucleo.nombre_seguro(sistema)
    candidatos = [p.name for p in carpeta.iterdir() if p.is_dir() and Path(p.name).stem == nombre_juego] \
        if carpeta.is_dir() else []
    return candidatos[0] if candidatos else nombre_juego


async def _guardar_ficha(form, sistema: str, usuario: str) -> dict:
    """Título (para juntar idiomas), idiomas y marca de archivo corrupto de un juego propio."""
    nombre = nucleo.nombre_seguro(str(form.get("nombre", "")))
    if not (config.ROOT / nucleo.nombre_seguro(sistema) / nombre).exists():
        raise HTTPException(404, "Juego no encontrado.")
    campos = {}
    if "titulo" in form:
        campos["titulo"] = str(form.get("titulo", ""))
    if "idiomas" in form:
        campos["idiomas"] = _idiomas(form)
    if "visibilidad" in form:
        campos["compartido"] = _visibilidad(form, enlace=bool(externos.por_ruta().get(f"{nucleo.nombre_seguro(sistema)}/{nombre}")))
    if "corrupto" in form:
        valor = str(form.get("corrupto"))
        campos["corrupto"] = {"1": True, "0": False}.get(valor)       # "auto" -> lo decide la verificación
        campos["motivo"] = str(form.get("motivo", ""))
        campos["sha"] = federacion.sha_conocido(nucleo.nombre_seguro(sistema), nombre)   # la marca es de esta versión
    compartido = campos.pop("compartido", None)
    return {"ok": True, **await _compartir(sistema, nombre, usuario, compartido, form, **campos)}


async def _subir_grupo(form, sistema: str):
    """Juego de varios ficheros: crear grupo -> añadir cada fichero (con su
    rol si es Switch) -> finalizar. Si algo falla, se borra el grupo."""
    nombre_juego = str(form.get("nombre_juego", "")).strip()
    roles = json.loads(str(form.get("roles_json") or "{}"))
    ficheros = [f for f in form.getlist("ficheros[]") if not isinstance(f, str)]
    if not nombre_juego or not ficheros:
        raise HTTPException(400, "Falta el nombre del juego o los ficheros.")
    nucleo.crear_grupo(I, T, system=sistema, nombre_juego=nombre_juego)
    try:
        for f in ficheros:
            await nucleo.anadir_fichero_grupo(I, T, system=sistema, nombre_juego=nombre_juego, filename=f.filename,
                                              fichero=f, rol=roles.get(f.filename))
        await nucleo.finalizar_grupo(I, T, system=sistema, nombre_juego=nombre_juego,
                                     es_multidisco=str(form.get("es_multidisco")) == "1",
                                     cover=_fichero(form, "cover", obligatorio=False))
    except Exception:
        nucleo.borrar_grupo_incompleto(sistema, nombre_juego, I, T)
        raise
    return _ok()


# ---------- Accesos de consolas (ver accesos.py) ----------

@router.get("/api/accesos")
def lista_accesos(usuario: str = Usuario):
    return _ok(accesos.listar())


@router.post("/api/accesos")
async def decidir_acceso(request: Request, usuario: str = Usuario):
    datos = await request.json()
    try:
        accesos.decidir(int(datos.get("id") or 0), str(datos.get("accion", "")), datos.get("nombre"))
    except accesos.Error as e:
        return _error(HTTPException(400, str(e)))
    return _ok(accesos.listar())


@router.get("/api/versiones")
def historial_versiones(request: Request, usuario: str = Usuario):
    q = request.query_params
    return _ok(versiones.historial(q.get("sistema", ""), q.get("nombre", "")))


@router.post("/api/versiones")
async def etiqueta_version(request: Request, usuario: str = Usuario):
    """Pone/cambia la etiqueta vX.Y.Z (editable) de una versión del juego."""
    d = await request.json()
    try:
        return _ok(versiones.fijar_etiqueta(str(d.get("sistema", "")), str(d.get("nombre", "")),
                                            int(d.get("n") or 0), str(d.get("etiqueta", ""))))
    except HTTPException as e:
        return _error(e)
    except (TypeError, ValueError):
        return _error(HTTPException(400, "Falta la versión a etiquetar."))


@router.get("/api/externo")
def info_externo(request: Request, usuario: str = Usuario):
    q = request.query_params
    return _ok(externos.info(q.get("sistema", ""), q.get("nombre", "")))


@router.post("/api/externo")
async def externo(request: Request, usuario: str = Usuario):
    """Fuentes externas (ver externos.py): guardar o quitar el enlace de un
    juego (los juegos nuevos desde un enlace van por POST /api/juegos, modo "enlace")."""
    d = await request.json()
    sistema, nombre = str(d.get("sistema", "")), str(d.get("nombre", ""))
    try:
        if d.get("accion") == "quitar":
            externos.quitar(sistema, nombre)
            return _ok()
        return _ok(await externos.guardar_enlace(sistema, nombre, str(d.get("url", "")), str(d.get("pagina", "")), usuario,
                                                 sin_cabecera_ok=bool(d.get("sin_cabecera_ok"))))
    except HTTPException as e:
        return _error(e)


@router.delete("/api/juegos")
async def borrar(request: Request, usuario: str = Usuario):
    datos = await request.json()
    try:
        nucleo.borrar_juego(I, T, system=str(datos.get("sistema", "")), filename=str(datos.get("nombre", "")))
        fichas.borrar(str(datos.get("sistema", "")), str(datos.get("nombre", "")))
        return _ok()
    except HTTPException as e:
        return _error(e)


def _fichero(form, campo: str, obligatorio: bool = True):
    f = form.get(campo)
    if f is None or isinstance(f, str) or not getattr(f, "filename", ""):
        if obligatorio:
            raise HTTPException(400, "No se ha recibido el fichero.")
        return None
    return f


# ---------- Copiar desde otra librería ----------

@router.post("/api/copiar")
async def copiar(request: Request, usuario: str = Usuario):
    datos = await request.json()
    try:
        return await federacion.iniciar_copia(str(datos.get("origen", "")), str(datos.get("sistema", "")),
                                              str(datos.get("fichero", "")), str(datos.get("nuevo_nombre", "")))
    except HTTPException as e:
        return _error(e)


@router.get("/api/copias")
def copias(usuario: str = Usuario):
    return _ok(federacion.copias_en_curso())


# ---------- Disco ----------

@router.get("/api/disco")
def disco(usuario: str = Usuario):
    return _ok(nucleo.uso_disco_datos())


@router.get("/api/salud")
def salud_gestor(usuario: Optional[str] = Usuario):
    return _ok({"usuario": usuario})
