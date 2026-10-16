"""
API de la app Android (RomsLibrary): /APIs/RomLibraryApp?accion=...

Es la MISMA interfaz que usa la APK desde siempre (antes la servía un PHP
en sund.es por delante de jarjar), así que una única APK sirve para
cualquier servicio RomsLibrary: solo cambia la URL y la clave de acceso.

- "version" y "apk" son públicas (la app no es un dato sensible).
- Librería PÚBLICA (ajuste "publico"): el resto también es libre, sin clave.
- Las partidas y la identidad de las consolas NO van aquí: son del servicio
  de partidas (partidas.py), aparte de cualquier librería.
- Librería privada: todo lo demás exige la clave de acceso del servicio
  (ajuste "clave_app", se ve y se cambia en el gestor), en ?token= o en la
  cabecera X-App-Token.
- Si este servicio no publica APK propia, sirve la oficial del servicio de
  origen (ROMSLIBRARY_APK_ORIGEN, sund.es): la app es igual para todos.
"""

import json
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import ajustes
import config
import federacion
import main as nucleo

router = APIRouter()

RUTA = "/APIs/RomLibraryApp"
ACCIONES_PUBLICAS = {"version", "apk", "cliente_version", "cliente"}


def _clave_valida(request: Request) -> bool:
    token = request.query_params.get("token") or request.headers.get("x-app-token") or ""
    esperado = ajustes.leer("clave_app")
    return bool(esperado) and bool(token) and secrets.compare_digest(token, esperado)


def _acceso_permitido(request: Request, accion: str) -> bool:
    if accion in ACCIONES_PUBLICAS or _clave_valida(request):
        return True
    return ajustes.publico()


def _param(origen, nombre: str) -> str:
    valor = origen.get(nombre)
    if valor is None or str(valor) == "":
        raise HTTPException(status_code=400, detail=f"Falta el parámetro «{nombre}».")
    return str(valor)


def _url_caratula(juego: dict) -> str | None:
    """Carátula de un juego propio: ruta relativa a esta misma API. De otra
    librería pública: URL completa de SU API pública (como las descargas, la
    app la pide directamente allí; ver federacion.py)."""
    if not juego.get("coverUrl"):
        return None
    params = {"accion": "caratula", "system": juego["system"], "filename": juego["coverUrl"].rsplit("/", 1)[-1]}
    if juego.get("origenUrl"):
        return federacion.url_directa(juego["origenUrl"], params)
    return RUTA + "?" + str(httpx.QueryParams(params))


# Las apps anteriores a la 2.23.0 pedían los juegos de otras librerías a
# esta, que hacía de intermediaria. Ya no: se descargan de su origen.
def _sin_intermediario() -> JSONResponse:
    return JSONResponse({"success": False, "message": "Actualiza la app: los juegos de otras librerías "
                                                      "se descargan directamente de ellas."}, status_code=410)


@router.get(RUTA)
async def api_app(request: Request):
    q = request.query_params
    accion = q.get("accion", "juegos")
    if not _acceso_permitido(request, accion):
        mensaje = ("Clave de acceso incorrecta." if ajustes.publico()
                   else "Esta librería es privada: necesita su clave de acceso.")
        return JSONResponse({"success": False, "message": mensaje}, status_code=401)
    I, T = nucleo.PETICION_INTERNA, nucleo.TOKEN_SISTEMA

    if q.get("origen") and accion in ("caratula", "descargar", "grupo_ficheros", "grupo_fichero"):
        return _sin_intermediario()
    if accion == "sistemas":
        return nucleo.listar_sistemas(I, T)
    if accion == "juegos":
        juegos = await federacion.listar_para_app(
            sistema=q.get("system"), texto=q.get("q"), libreria=q.get("libreria", "personal"))
        for juego in juegos:
            juego["coverUrl"] = _url_caratula(juego)
        return juegos
    if accion == "caratula":
        return nucleo.servir_caratula(_param(q, "system"), _param(q, "filename"), I, T)
    if accion == "descargar":
        return federacion.servir_descarga(_param(q, "system"), _param(q, "filename"))
    if accion in ("grupo_ficheros", "grupo_fichero"):
        # Lo confirmado como corrupto no se sirve (410).
        federacion.fichero_limpio(nucleo.nombre_seguro(_param(q, "system")), nucleo.nombre_seguro(_param(q, "grupo")))
    if accion == "grupo_ficheros":
        return nucleo.ficheros_de_grupo(I, T, system=_param(q, "system"), grupo=_param(q, "grupo"))
    if accion == "grupo_fichero":
        return nucleo.fichero_de_grupo(I, T, system=_param(q, "system"), grupo=_param(q, "grupo"),
                                       fichero=_param(q, "fichero"))
    if accion == "versiones":
        # Versiones superadas de los juegos de esta librería y la actual de
        # cada uno: la app actualiza lo que tenga instalado (ver versiones.py).
        return {"obsoletas": await federacion.obsoletas_para_app()}
    if accion == "version":
        return await _version()
    if accion == "apk":
        return await _apk()
    if accion == "cliente_version":
        return _cliente_version()
    if accion == "cliente":
        return _cliente(_param(q, "paquete"))
    raise HTTPException(status_code=404, detail="Acción desconocida.")


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


# ---------- App para Linux / Steam Deck / Windows ----------

def _cliente_info() -> dict:
    try:
        return json.loads((config.CLIENTE_DIR / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="App no publicada en este servicio.") from None


def _cliente_version():
    info = _cliente_info()
    return {"version": info["version"],
            "paquetes": {k: {"sha256": v["sha256"], "tamano": v["tamano"]} for k, v in info["paquetes"].items()},
            "instalar": info.get("instalar", {})}


def _cliente(paquete: str):
    """Paquete de actualización (o de instalación, «-instalar») de una plataforma."""
    info = _cliente_info()
    base, instalar = paquete.removesuffix("-instalar"), paquete.endswith("-instalar")
    datos = (info.get("instalar", {}) if instalar else info["paquetes"]).get(base)
    if not datos:
        raise HTTPException(status_code=404, detail="Paquete desconocido.")
    ruta = config.CLIENTE_DIR / datos["fichero"]
    if not ruta.is_file() or "/" in datos["fichero"]:
        raise HTTPException(status_code=404, detail="Paquete no disponible.")
    return FileResponse(ruta, media_type="application/octet-stream", filename=datos["fichero"])


def _origen_apk_util() -> bool:
    """No tiene sentido pedírsela a uno mismo (el servicio de origen sin APK publicada)."""
    propia = ajustes.leer("url_publica").rstrip("/")
    return bool(config.APK_ORIGEN) and config.APK_ORIGEN != propia
