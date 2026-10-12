"""
API de la app Android (RomsLibrary): /APIs/RomLibraryApp?accion=...

Es la MISMA interfaz que usa la APK desde siempre (antes la servía un PHP
en sund.es por delante de jarjar), así que una única APK sirve para
cualquier servicio RomsLibrary: solo cambia la URL y la clave de acceso.

- "version" y "apk" son públicas (la app no es un dato sensible).
- Librería PÚBLICA (ajuste "publico"): el resto también es libre, sin clave
  (los guardados siguen protegidos por android_id + identificador).
- Librería privada: todo lo demás exige la clave de acceso del servicio
  (ajuste "clave_app", se ve y se cambia en el gestor), en ?token= o en la
  cabecera X-App-Token.
- Si este servicio no publica APK propia, sirve la oficial del servicio de
  origen (ROMSLIBRARY_APK_ORIGEN, sund.es): la app es igual para todos.
"""

import secrets
import time

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import ajustes
import config
import consolas
import federacion
import main as nucleo

router = APIRouter()

RUTA = "/APIs/RomLibraryApp"
ACCIONES_PUBLICAS = {"version", "apk"}


def _clave_valida(request: Request) -> bool:
    token = request.query_params.get("token") or request.headers.get("x-app-token") or ""
    esperado = ajustes.leer("clave_app")
    return bool(esperado) and bool(token) and secrets.compare_digest(token, esperado)


def _acceso_permitido(request: Request, accion: str) -> bool:
    if accion in ACCIONES_PUBLICAS or _clave_valida(request):
        return True
    return ajustes.publico()


_registros_por_ip: dict[str, list[float]] = {}


def _registro_permitido(request: Request) -> bool:
    """Consolas NUEVAS por IP y día (config.REGISTROS_POR_IP_DIA). La IP la
    pone el proxy (nginx/Caddy) en X-Real-IP; el servicio no se expone solo."""
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    ahora = time.time()
    marcas = [t for t in _registros_por_ip.get(ip, []) if ahora - t < 86400]
    if len(marcas) >= config.REGISTROS_POR_IP_DIA:
        _registros_por_ip[ip] = marcas
        return False
    _registros_por_ip[ip] = marcas + [ahora]
    if len(_registros_por_ip) > 10000:
        for clave in [k for k, v in _registros_por_ip.items() if all(ahora - t >= 86400 for t in v)]:
            del _registros_por_ip[clave]
    return True


def _param(origen, nombre: str) -> str:
    valor = origen.get(nombre)
    if valor is None or str(valor) == "":
        raise HTTPException(status_code=400, detail=f"Falta el parámetro «{nombre}».")
    return str(valor)


def _url_caratula(juego: dict) -> str | None:
    """Las carátulas se piden siempre a esta misma API (la app nunca habla
    con /api/* ni con otras librerías directamente). Para juegos de otra
    librería pública lleva también el origen."""
    if not juego.get("coverUrl"):
        return None
    nombre = juego["coverUrl"].rsplit("/", 1)[-1]
    url = RUTA + "?" + str(httpx.QueryParams({"accion": "caratula", "system": juego["system"], "filename": nombre}))
    if juego.get("origen"):
        url += "&" + str(httpx.QueryParams({"origen": juego["origen"]}))
    return url


@router.api_route(RUTA, methods=["GET", "POST"])
async def api_app(request: Request):
    q = request.query_params
    accion = q.get("accion", "juegos")
    if not _acceso_permitido(request, accion):
        mensaje = ("Clave de acceso incorrecta." if ajustes.publico()
                   else "Esta librería es privada: necesita su clave de acceso.")
        return JSONResponse({"success": False, "message": mensaje}, status_code=401)
    I, T = nucleo.PETICION_INTERNA, nucleo.TOKEN_SISTEMA

    if request.method == "GET":
        if accion == "sistemas":
            return nucleo.listar_sistemas(I, T)
        if accion == "juegos":
            juegos = await federacion.listar_para_app(
                sistema=q.get("system"), texto=q.get("q"), libreria=q.get("libreria", "personal"))
            for juego in juegos:
                juego["coverUrl"] = _url_caratula(juego)
            return juegos
        if accion == "caratula":
            if q.get("origen"):
                return await federacion.caratula_remota(q["origen"], _param(q, "system"), _param(q, "filename"))
            return nucleo.servir_caratula(_param(q, "system"), _param(q, "filename"), I, T)
        if accion == "descargar":
            if q.get("origen"):
                return await federacion.descarga_remota(request, q["origen"], "descargar",
                                                        {"system": _param(q, "system"), "filename": _param(q, "filename")})
            return nucleo.descargar_juego(_param(q, "system"), _param(q, "filename"), I, T)
        if accion == "grupo_ficheros":
            if q.get("origen"):
                return await federacion.json_remoto(q["origen"], "grupo_ficheros",
                                                    {"system": _param(q, "system"), "grupo": _param(q, "grupo")})
            return nucleo.ficheros_de_grupo(I, T, system=_param(q, "system"), grupo=_param(q, "grupo"))
        if accion == "grupo_fichero":
            if q.get("origen"):
                return await federacion.descarga_remota(request, q["origen"], "grupo_fichero", {
                    "system": _param(q, "system"), "grupo": _param(q, "grupo"), "fichero": _param(q, "fichero")})
            return nucleo.fichero_de_grupo(I, T, system=_param(q, "system"), grupo=_param(q, "grupo"),
                                           fichero=_param(q, "fichero"))
        if accion == "version":
            return await _version()
        if accion == "apk":
            return await _apk()
        # Consolas registradas (android_id + identificador).
        if accion == "consola_estado":
            return nucleo.consola_estado(I, T, android_id=_param(q, "android_id"),
                                         identificador=_param(q, "identificador"))
        if accion == "guardado2_indice":
            return nucleo.guardados_indice(I, T, android_id=_param(q, "android_id"),
                                           identificador=_param(q, "identificador"))
        if accion == "guardado2_bajar":
            return nucleo.guardado_bajar_v2(I, T, android_id=_param(q, "android_id"),
                                            identificador=_param(q, "identificador"), sistema=_param(q, "sistema"),
                                            clave=_param(q, "clave"), nombre_fichero=_param(q, "nombre_fichero"))
        raise HTTPException(status_code=404, detail="Acción desconocida.")

    # El formulario se recibe entero antes de poder mirar nada: lo que declare
    # más de lo permitido se rechaza sin leerlo (solo se suben partidas).
    if int(request.headers.get("content-length") or 0) > config.GUARDADO_MAX_BYTES + 1024 * 1024:
        return JSONResponse({"success": False, "message": "Partida demasiado grande."}, status_code=413)
    form = await request.form()
    if accion == "consola_registrar":
        android_id = _param(form, "android_id")
        if not consolas.existe(android_id) and not _registro_permitido(request):
            return JSONResponse({"success": False, "message": "Demasiadas consolas nuevas desde esta conexión. Prueba mañana."},
                                status_code=429)
        return nucleo.consola_registrar(I, T, android_id=android_id, nombre=form.get("nombre", ""))
    if accion == "consola_accion":
        return nucleo.consola_accion(
            _param(q, "op"), I, T, android_id=_param(form, "android_id"),
            identificador=_param(form, "identificador"), nombre=str(form.get("nombre", "")),
            otra=str(form.get("otra", "")), solicitud=int(form.get("solicitud") or 0),
            aceptar=str(form.get("aceptar", "")).lower() in ("1", "true", "on", "si", "sí"))
    if accion == "guardado2_subir":
        return await nucleo.guardado_subir_v2(
            I, T, android_id=_param(form, "android_id"), identificador=_param(form, "identificador"),
            sistema=_param(form, "sistema"), clave=_param(form, "clave"),
            nombre_fichero=_param(form, "nombre_fichero"),
            mtime_dispositivo=float(_param(form, "mtime_dispositivo")), fichero=_fichero(form))
    raise HTTPException(status_code=404, detail="Acción desconocida.")


def _fichero(form):
    fichero = form.get("fichero")
    if fichero is None or isinstance(fichero, str):
        raise HTTPException(status_code=400, detail="No se ha recibido el fichero.")
    return fichero


# ---------- APK ----------

async def _version():
    info = nucleo.info_apk()
    if info is not None:
        return {**info, "apkPath": f"{RUTA}?accion=apk"}
    if not _origen_apk_util():
        raise HTTPException(status_code=404, detail="APK no disponible.")
    async with httpx.AsyncClient(timeout=10) as cliente:
        r = await cliente.get(f"{config.APK_ORIGEN}{RUTA}", params={"accion": "version"})
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="No se pudo consultar la versión oficial de la app.")
    return {**r.json(), "apkPath": f"{RUTA}?accion=apk"}


async def _apk():
    if nucleo.APK_PATH.is_file():
        return FileResponse(nucleo.APK_PATH, media_type="application/vnd.android.package-archive",
                            filename="RomsLibrary.apk")
    if not _origen_apk_util():
        raise HTTPException(status_code=404, detail="APK no disponible.")
    cliente = httpx.AsyncClient(timeout=httpx.Timeout(30, read=120))
    respuesta = await cliente.send(cliente.build_request("GET", f"{config.APK_ORIGEN}{RUTA}", params={"accion": "apk"}),
                                   stream=True)
    if respuesta.status_code != 200:
        await respuesta.aclose()
        await cliente.aclose()
        raise HTTPException(status_code=502, detail="No se pudo descargar la app oficial.")

    async def trozos():
        try:
            async for trozo in respuesta.aiter_bytes(65536):
                yield trozo
        finally:
            await respuesta.aclose()
            await cliente.aclose()

    cabeceras = {"Content-Disposition": 'attachment; filename="RomsLibrary.apk"'}
    if "content-length" in respuesta.headers:
        cabeceras["Content-Length"] = respuesta.headers["content-length"]
    return StreamingResponse(trozos(), media_type="application/vnd.android.package-archive", headers=cabeceras)


def _origen_apk_util() -> bool:
    """No tiene sentido pedírsela a uno mismo (el servicio de origen sin APK publicada)."""
    propia = ajustes.leer("url_publica").rstrip("/")
    return bool(config.APK_ORIGEN) and config.APK_ORIGEN != propia
