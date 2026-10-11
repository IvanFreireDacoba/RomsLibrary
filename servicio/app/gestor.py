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

import json
import secrets
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import ajustes
import config
import federacion
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
        "es_indice": config.ES_INDICE,
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
                return await federacion.descarga_remota(request, q["origen"], "descargar",
                                                        {"system": q["sistema"], "filename": q["nombre"]})
            return nucleo.descargar_juego(q["sistema"], q["nombre"], I, T)
        except KeyError:
            return _error(HTTPException(400, "Faltan parámetros."))
    libreria = q.get("libreria", "todas")
    if libreria not in ("todas", "personal", "otras"):
        libreria = "todas"
    lista = await federacion.juegos_listado(q.get("sistema") or None, q.get("q") or None, libreria)
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
        if modo == "grupo":
            return await _subir_grupo(form, sistema)
        rom = _fichero(form, "rom")
        await nucleo.subir_juego(I, T, system=sistema, filename=str(form.get("nombre", "")).strip(), rom=rom,
                                 cover=_fichero(form, "cover", obligatorio=False))
        return _ok()
    except HTTPException as e:
        return _error(e)


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


@router.delete("/api/juegos")
async def borrar(request: Request, usuario: str = Usuario):
    datos = await request.json()
    try:
        nucleo.borrar_juego(I, T, system=str(datos.get("sistema", "")), filename=str(datos.get("nombre", "")))
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
