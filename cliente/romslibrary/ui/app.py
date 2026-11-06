"""
Pantalla principal: la lista de juegos de la librería, como la app de Android
pero para mando.

  A instalar / desinstalar      Y elegir idioma      X buscar
  L / R cambiar de consola      Select filtro (instalados / no instalados / todos)
  Start menú (partidas, ajustes, actualizaciones)    B salir

El trabajo de red va en hilos; la pantalla solo lee su estado.

Arranque por fases (ninguna espera a la siguiente):
  1. «hola»: petición ligera que el servidor contesta al momento. Con ella la
     consola ya se ve CONECTADA (indicador de la cabecera).
  2. La tabla de juegos (solo metadatos, con gzip), que se pinta al llegar.
  3. Las carátulas, perezosas: solo las de las filas en pantalla, las últimas
     pedidas primero, dos a la vez, reducidas y con caché en disco.
La comprobación de versión nueva de la app va en su propio hilo, con
reintentos: no depende de que funcione nada más.

Pruebas sin consola (ver empaquetado/probar.sh): RL_TAMANO=640x480 abre una
ventana de ese tamaño, RL_GUION ejecuta acciones guionizadas
(«esperar 2; abajo; captura lista; menu; salir») y RL_CAPTURAS es la carpeta
donde se guardan las capturas.
"""

import os
import queue
import sys
import threading
import time
from pathlib import Path

import pygame

from .. import (VERSION, acceso, actualizar_roms, api, autoactualizar, catalogo, guardados, instalador, legal, partidas,
               plataforma)
from ..ajustes import Ajustes, carpeta_datos
from . import tema
from .dialogos import Confirmar, Lista, Mensaje, SelectorCarpeta, Teclado, TextoLargo
from .entrada import Entrada

LIBRERIAS = [("personal", "Mi librería"), ("otras", "Otras librerías"), ("todas", "Todas")]
FILTROS = ["Todos", "Instalados", "No instalados"]
INICIO = time.monotonic()
_RECURSOS = Path(__file__).resolve().parent.parent / "recursos"
PORTADA_MAX = 320                  # px de alto como mucho en memoria (consolas con 1 GB de RAM)


def _fase(texto: str) -> None:
    print(f"[fases] {time.monotonic() - INICIO:6.2f}s {texto}", flush=True)


def _preparar_sdl() -> None:
    """Antes de pygame.init(). Mandos: el mapeado de SDL_GameControllerDB que
    va en el paquete (el SDL_GAMECONTROLLERCONFIG de PortMaster, si lo hay,
    manda sobre él). Vídeo: sin X11 ni Wayland (Knulli, muOS) se usa KMS/DRM."""
    db = _RECURSOS / "gamecontrollerdb.txt"
    if db.is_file() and not os.environ.get("SDL_GAMECONTROLLERCONFIG_FILE"):
        os.environ["SDL_GAMECONTROLLERCONFIG_FILE"] = str(db)
    if sys.platform.startswith("linux") and not os.environ.get("SDL_VIDEODRIVER") \
            and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
        os.environ["_RL_KMSDRM_AUTO"] = "1"


def _abrir_pantalla(modo, flags):
    try:
        return pygame.display.set_mode(modo, flags)
    except pygame.error:
        if not os.environ.pop("_RL_KMSDRM_AUTO", None):
            raise
        # kmsdrm no ha ido: lo que SDL encuentre por sí mismo.
        os.environ.pop("SDL_VIDEODRIVER", None)
        pygame.display.quit()
        pygame.display.init()
        return pygame.display.set_mode(modo, flags)


class App:
    def __init__(self, ventana: bool = False):
        self.ajustes = Ajustes()
        _preparar_sdl()
        pygame.init()
        if not pygame.display.get_init() and os.environ.pop("_RL_KMSDRM_AUTO", None):
            # Sin KMS/DRM usable: lo que SDL encuentre por sí mismo.
            os.environ.pop("SDL_VIDEODRIVER", None)
            pygame.display.init()
        pygame.key.set_repeat(300, 45)
        pygame.display.set_caption("RomsLibrary")
        try:
            _icono = _RECURSOS / "icono.png"
            if _icono.is_file():
                pygame.display.set_icon(pygame.image.load(str(_icono)))
        except Exception:  # noqa: BLE001 -- el icono es cosmético, que no tumbe el arranque
            pass
        info = pygame.display.Info()
        tamano_prueba = os.environ.get("RL_TAMANO")
        if tamano_prueba:
            self.pantalla = _abrir_pantalla(tuple(int(x) for x in tamano_prueba.lower().split("x")), 0)
        elif ventana or (self.ajustes.plataforma.id in ("windows", "linux", "esde", "emudeck")
                         and info.current_w > 1300):
            self.pantalla = _abrir_pantalla((1280, 800), pygame.RESIZABLE)
        else:
            self.pantalla = _abrir_pantalla((0, 0), pygame.FULLSCREEN)
        _fase(f"pantalla {self.pantalla.get_size()} con {pygame.display.get_driver()}")
        tema.configurar(self.pantalla.get_height())
        pygame.mouse.set_visible(ventana or sys.platform == "win32")
        self.entrada = Entrada(bool(self.ajustes["intercambiar_ab"]))
        self.dialogos: list = []
        self.avisos: list[tuple[str, float, tuple]] = []
        self.juegos: list[catalogo.Juego] = []          # todas las variantes
        self.visibles: list[catalogo.Juego] = []
        self.sel = 0
        self.sistema_idx = 0                            # 0 = todas las consolas
        self.libreria_idx = 0
        self.filtro_idx = 0
        self.busqueda = ""
        self.cargando = False
        self.recargar = False                           # se pidió otra carga mientras había una en curso
        self.estado = ""
        self.cola: queue.Queue = queue.Queue()
        self.cancelar_actual = False
        self.portadas: dict[str, pygame.Surface | None] = {}
        self.portadas_crudas: dict[str, pygame.Surface | None] = {}
        self.pedidas: set[str] = set()
        self.cola_portadas: queue.LifoQueue = queue.LifoQueue()   # lo último pedido (lo que se ve) primero
        self.vistas: dict[str, float] = {}               # portada -> última vez que estuvo en pantalla
        self.conexion = "conectando"                    # conectando | conectada | sin conexión
        self.info_servidor: dict = {}
        self.primera_portada = False
        self.guion = _Guion(self)
        self.cache_portadas = carpeta_datos() / "portadas"
        self.salir = False
        threading.Thread(target=self._trabajador_descargas, daemon=True).start()
        for _ in range(2):
            threading.Thread(target=self._trabajador_portadas, daemon=True).start()
        if not plataforma.reconocida(self.ajustes.plataforma) and not self.ajustes["roms_elegida"]:
            self.dialogos.append(SelectorCarpeta("¿Dónde están los ROMs de esta consola?", self.ajustes["roms"],
                                                 self._fijar_roms))
        threading.Thread(target=self._comprobar_app, daemon=True).start()
        threading.Thread(target=self._conectar, daemon=True).start()

    # ---------------- utilidades ----------------

    def aviso(self, texto: str, color=tema.TEXTO, segundos: float = 4) -> None:
        self.avisos.append((texto, time.monotonic() + segundos, color))
        self.avisos = self.avisos[-4:]

    def sistemas(self) -> list[str | None]:
        return [None] + self.ajustes.sistemas_visibles()

    def _en_hilo(self, funcion, *args) -> None:
        threading.Thread(target=funcion, args=args, daemon=True).start()

    # ---------------- arranque y catálogo ----------------

    def _conectar(self) -> None:
        """Fase 1: «hola». Al contestar, conectada y se pide la tabla (fase 2) y
        lo demás del arranque. Sin conexión se reintenta solo."""
        espera = 3
        while not self.salir:
            self.conexion = "conectando"
            try:
                self.info_servidor = api.hola(self.ajustes["url"], self.ajustes["clave"] or None)
                self.conexion = "conectada"
                _fase(f"conectada ({self.info_servidor.get('servicio', 'servidor antiguo')})")
                break
            except api.ErrorApi as e:
                self.conexion = "sin conexión"
                self.estado = f"Sin conexión: {e}. Reintentando…"
                time.sleep(espera)
                espera = min(espera * 2, 30)
        if self.salir:
            return
        self.cargar()
        self._al_empezar()

    def _comprobar_app(self) -> None:
        """Versión nueva de la app, por su cuenta: si sund.es no contesta se
        reintenta (antes, un fallo en el arranque la dejaba sin mirar)."""
        if not autoactualizar.empaquetada():
            return
        for espera in (0, 10, 30, 90, 300):
            time.sleep(espera)
            try:
                nueva = autoactualizar.comprobar(con_errores=True)
            except api.ErrorApi:
                continue
            if nueva:
                self.dialogos.append(Confirmar(
                    "Actualización disponible", f"Hay una versión nueva de RomsLibrary: {nueva['version']} "
                    f"(tienes la {VERSION}). Se descarga, se instala y la app se vuelve a abrir.",
                    "Actualizar ahora", lambda: self._en_hilo(self._actualizar_app, nueva), "Más tarde"))
            return

    def _al_empezar(self) -> None:
        """Alta en el servidor (para las partidas), partidas de lo instalado,
        ROMs con versión nueva y versión nueva de la app."""
        try:
            if not self.ajustes.identificador:
                partidas.recuperar(self.ajustes)
                self.aviso(f"Consola: {self.ajustes.nombre_consola} ({self.ajustes.identificador})", tema.ACENTO)
            self._avisar_solicitudes()
            r = acceso.comprobar(self.ajustes)
            if r:
                self.aviso("Acceso concedido: ya ves la librería entera" if r == "aceptada"
                           else "La librería no ha aceptado la solicitud de acceso", tema.ACENTO if r == "aceptada" else tema.AVISO)
                if r == "aceptada":
                    self.cargar()
        except (api.ErrorApi, KeyError, TypeError) as e:
            self.aviso(f"No se pudo registrar la consola: {e}", tema.AVISO)
        try:
            self.sincronizar_partidas(silencioso=True)
        except Exception as e:  # noqa: BLE001
            print(f"[arranque] partidas: {e}", flush=True)
        try:
            hechos = actualizar_roms.comprobar(self.ajustes, lambda t: self.aviso(t, tema.ACENTO))
        except Exception as e:  # noqa: BLE001 -- que no tumbe el resto del arranque
            hechos = 0
            print(f"[arranque] actualizar ROMs: {e}", flush=True)
        if hechos:
            self.cargar()

    def _actualizar_app(self, nueva: dict) -> None:
        self.estado = f"Descargando la versión {nueva['version']}…"
        try:
            autoactualizar.instalar(nueva, lambda h, t: setattr(self, "estado",
                                                               f"Descargando la versión {nueva['version']}… "
                                                               f"{h * 100 // t if t > 0 else 0}%"))
        except Exception as e:  # noqa: BLE001
            self.estado = ""
            self.aviso(f"No se pudo actualizar la app: {e}", tema.ERROR, 8)

    def cargar(self) -> None:
        if self.cargando:
            self.recargar = True
            return
        self.cargando = True
        self.estado = "Cargando…"
        self._en_hilo(self._cargar)

    def _cargar(self) -> None:
        try:
            sistema = self.sistemas()[self.sistema_idx]
            lista = catalogo.cargar(self.ajustes["url"], self.ajustes["clave"] or None, self.ajustes.sistemas_visibles(),
                                    LIBRERIAS[self.libreria_idx][0], sistema, self.busqueda or None)
            catalogo.marcar_instalados(self.ajustes, lista)
            # Las descargas en curso conservan su objeto (barra de progreso).
            activos = {(j.sistema, j.fichero, j.origen): j for j in self.juegos if j.progreso >= 0 or j.en_cola}
            lista = [activos.get((j.sistema, j.fichero, j.origen), j) for j in lista]
            self.juegos = lista
            self.estado = ""
            _fase(f"tabla de juegos ({len(lista)})")
        except api.ErrorApi as e:
            self.estado = f"Error: {e}"
        finally:
            self.cargando = False
            if self.recargar:                           # los filtros cambiaron durante la carga
                self.recargar = False
                self.cargar()
            else:
                self._refiltrar()

    def _refiltrar(self) -> None:
        agrupados = catalogo.agrupar_idiomas(self.juegos)
        if self.filtro_idx == 1:
            agrupados = [j for j in agrupados if any(v.instalado for v in j.variantes)]
        elif self.filtro_idx == 2:
            agrupados = [j for j in agrupados if not any(v.instalado for v in j.variantes)]
        actual = self.visibles[self.sel] if 0 <= self.sel < len(self.visibles) else None
        self.visibles = agrupados
        if actual in agrupados:
            self.sel = agrupados.index(actual)
        self.sel = max(0, min(self.sel, len(agrupados) - 1))

    # ---------------- portadas ----------------

    def _trabajador_portadas(self) -> None:
        while True:
            j = self.cola_portadas.get()
            if time.monotonic() - self.vistas.get(j.portada, 0) > 1.5:
                # Ya no está en pantalla (se ha bajado por la lista): se pedirá otra vez si vuelve.
                self.pedidas.discard(j.portada)
                continue
            ruta = instalador.descargar_portada(self.ajustes, j, self.cache_portadas)
            try:
                img = pygame.image.load(str(ruta)) if ruta else None
                if img and img.get_height() > PORTADA_MAX:
                    w, h = img.get_size()
                    img = pygame.transform.smoothscale(img, (max(1, w * PORTADA_MAX // h), PORTADA_MAX))
                self.portadas_crudas[j.portada] = img
                if img and not self.primera_portada:
                    self.primera_portada = True
                    _fase("primera carátula")
            except (pygame.error, OSError, ValueError):
                self.portadas_crudas[j.portada] = None

    def portada(self, j: catalogo.Juego, tamano: tuple[int, int]) -> pygame.Surface | None:
        if not j.portada:
            return None
        self.vistas[j.portada] = time.monotonic()
        clave = f"{j.portada}|{tamano}"
        if clave in self.portadas:
            return self.portadas[clave]
        if j.portada in self.portadas_crudas:
            cruda = self.portadas_crudas[j.portada]
            if cruda is None:
                self.portadas[clave] = None
                return None
            w, h = cruda.get_size()
            f = min(tamano[0] / w, tamano[1] / h)
            self.portadas[clave] = pygame.transform.smoothscale(cruda.convert_alpha(),
                                                                (max(1, int(w * f)), max(1, int(h * f))))
            return self.portadas[clave]
        if j.portada not in self.pedidas:
            self.pedidas.add(j.portada)
            self.cola_portadas.put(j)
        return None

    # ---------------- instalar / desinstalar ----------------

    def accion_principal(self, j: catalogo.Juego) -> None:
        if j.progreso >= 0 or j.en_cola:
            return
        if j.instalado:
            self.dialogos.append(Confirmar("Desinstalar", f"¿Quitar «{j.nombre_visible}» de esta consola? Antes se suben "
                                           "sus partidas al servidor.", "Desinstalar",
                                           lambda: self._en_hilo(self._desinstalar, j)))
        elif not j.descargable:
            self.dialogos.append(Mensaje("Archivo corrupto", f"La librería ha comprobado que «{j.nombre_visible}» es un archivo "
                                         f"corrupto y no lo sirve hasta que suba una versión buena.\n\n{j.motivo_corrupto}"))
        elif j.corrupto:
            texto = (f"La verificación automática de la librería sospecha que «{j.nombre_visible}» está corrupto (nadie lo "
                     "ha comprobado aún)" if j.corrupto_automatico else
                     f"La librería ha comprobado que «{j.nombre_visible}» es un archivo corrupto")
            self.dialogos.append(Confirmar("Posible archivo corrupto" if j.corrupto_automatico else "Archivo corrupto",
                                           f"{texto}: puede no arrancar o fallar en la partida.\n\n{j.motivo_corrupto}",
                                           "Instalar igualmente", lambda: self.encolar(j)))
        else:
            self.encolar(j)

    def encolar(self, j: catalogo.Juego) -> None:
        j.en_cola, j.error = True, ""
        self.cola.put(j)
        self._refiltrar()

    def _trabajador_descargas(self) -> None:
        while True:
            j = self.cola.get()
            j.en_cola, j.progreso = False, 0.0
            self.cancelar_actual = False
            try:
                def avance(h, t):
                    j.preparando = False
                    j.progreso = h / t if t > 0 else 0.0
                instalador.instalar(self.ajustes, j, avance, lambda: self.cancelar_actual or self.salir,
                                    preparando=lambda: setattr(j, "preparando", True))
                j.instalado = True
                guardados.sincronizar_juego(self.ajustes, j.sistema, j.fichero)
                self.aviso(f"Instalado: {j.nombre_visible}", tema.ACENTO)
            except Exception as e:  # noqa: BLE001
                j.error = str(e)
                self.aviso(f"Error con {j.nombre_visible}: {e}", tema.ERROR, 8)
            finally:
                j.progreso = -1.0
                j.preparando = False
                self._refiltrar()

    def _desinstalar(self, j: catalogo.Juego) -> None:
        try:
            guardados.sincronizar_juego(self.ajustes, j.sistema, j.fichero)
            instalador.desinstalar(self.ajustes, j)
            j.instalado = False
            self.aviso(f"Quitado de la consola: {j.nombre_visible}")
        except OSError as e:
            self.aviso(f"No se pudo borrar: {e}", tema.ERROR)
        self._refiltrar()

    def elegir_idioma(self, j: catalogo.Juego) -> None:
        if len(j.variantes) < 2:
            self.aviso("Este juego solo está en " + (j.etiqueta_idiomas() or "un idioma"))
            return
        opciones = []
        for v in j.variantes:
            idiomas = ", ".join(catalogo.IDIOMAS.get(i, i) for i in v.idiomas) or "Idioma sin indicar"
            opciones.append(f"{idiomas} — {v.nombre_visible}" + (" ✓" if v.instalado else "") + (" ⚠ corrupto" if v.corrupto else ""))
        self.dialogos.append(Lista("Elegir idioma", opciones, lambda i: self.accion_principal(j.variantes[i])))

    # ---------------- partidas ----------------

    def sincronizar_partidas(self, silencioso: bool = False) -> None:
        instalados = [(j.sistema, j.fichero) for j in self.juegos if j.instalado]
        if not instalados:
            catalogo_completo = []
            try:
                catalogo_completo = catalogo.cargar(self.ajustes["url"], self.ajustes["clave"] or None,
                                                    self.ajustes.sistemas_visibles(), "todas")
                catalogo.marcar_instalados(self.ajustes, catalogo_completo)
            except api.ErrorApi:
                pass
            instalados = [(j.sistema, j.fichero) for j in catalogo_completo if j.instalado]
        subidos, bajados = guardados.sincronizar(self.ajustes, instalados)
        if subidos or bajados or not silencioso:
            self.aviso(f"Partidas: {subidos} subidas, {bajados} bajadas", tema.ACENTO)

    # ---------------- menú y ajustes ----------------

    def abrir_menu(self) -> None:
        opciones = [("Sincronizar partidas ahora", lambda: self._en_hilo(self.sincronizar_partidas)),
                    ("Sincronizar con otras consolas", lambda: self._en_hilo(self._abrir_consolas)),
                    ("Buscar versiones nuevas de los ROMs", lambda: self._en_hilo(self._buscar_roms)),
                    ("Librería: " + LIBRERIAS[self.libreria_idx][1], self._cambiar_libreria),
                    ("Ajustes", self.abrir_ajustes),
                    (f"Versión ({VERSION}) y licencias", self._version),
                    ("Salir", self._pedir_salir)]
        self.dialogos.append(Lista("Menú", [o[0] for o in opciones], lambda i: opciones[i][1]()))

    # ---------------- vincular consolas (servicio de partidas) ----------------

    def _avisar_solicitudes(self) -> None:
        """Al arrancar: si otra consola ha pedido vincularse, se pregunta (nunca se acepta sola)."""
        try:
            recibidas = partidas.estado(self.ajustes)["solicitudes_recibidas"]
        except api.ErrorApi:
            return
        if recibidas:
            self._preguntar_solicitud(recibidas[0])

    def _preguntar_solicitud(self, s: dict) -> None:
        respuestas = [lambda: self._en_hilo(self._responder, s, True),
                      lambda: self._en_hilo(self._responder, s, False), lambda: None]
        self.dialogos.append(Lista(
            "Solicitud de sincronización", ["Aceptar", "Rechazar", "Luego"], lambda i: respuestas[i](),
            detalle=f"{s['nombre']} ({s['identificador']}) quiere sincronizar sus partidas con esta consola. "
                    "Si aceptas, las dos compartirán las mismas partidas (se queda la más reciente).", inicial=2))

    def _responder(self, s: dict, aceptar: bool) -> None:
        try:
            partidas.accion(self.ajustes, "responder", solicitud=s["solicitud"], aceptar=aceptar)
            self.aviso(f"Sincronizada con {s['nombre']}" if aceptar else "Solicitud rechazada", tema.ACENTO)
            if aceptar:
                self.sincronizar_partidas()
        except api.ErrorApi as e:
            self.aviso(f"Error: {e}", tema.ERROR)

    def _abrir_consolas(self) -> None:
        try:
            if not self.ajustes.identificador:
                partidas.recuperar(self.ajustes)
            e = partidas.estado(self.ajustes)
        except api.ErrorApi as ex:
            self.aviso(f"Error: {ex}", tema.ERROR)
            return
        opciones = [("Vincular con otra consola…", lambda: self.dialogos.append(
            Teclado("Identificador de la otra consola (12345-67890)", "", self._solicitar)))]
        for s in e["solicitudes_recibidas"]:
            opciones.append((f"Solicitud de {s['nombre']} ({s['identificador']})", lambda s=s: self._preguntar_solicitud(s)))
        for s in e["solicitudes_enviadas"]:
            opciones.append((f"Enviada a {s['nombre']}: cancelar", lambda s=s: self._en_hilo(
                self._hacer, "cancelar", {"solicitud": s["solicitud"]}, "Solicitud cancelada")))
        for c in e["sincronizadas"]:
            opciones.append((f"Sincronizada: {c['nombre']} ({c['identificador']}) — desvincular", lambda c=c: self.dialogos.append(
                Confirmar("Desvincular", f"¿Dejar de compartir partidas con {c['nombre']}? Se queda con una copia.",
                          "Desvincular", lambda: self._en_hilo(self._hacer, "desincronizar", {"otra": c["identificador"]},
                                                              f"Desvinculada {c['nombre']}")))))
        for c in e["bloqueadas"]:
            opciones.append((f"Bloqueada: {c['nombre']} — desbloquear", lambda c=c: self._en_hilo(
                self._hacer, "desbloquear", {"otra": c["identificador"]}, f"Desbloqueada {c['nombre']}")))
        self.dialogos.append(Lista(f"Esta consola: {self.ajustes.nombre_consola} · {self.ajustes.identificador}",
                                   [o[0] for o in opciones], lambda i: opciones[i][1]()))

    def _solicitar(self, ident: str) -> None:
        self._en_hilo(self._hacer, "solicitar", {"otra": ident.strip()},
                      "Solicitud enviada: si el identificador es correcto, la otra consola tendrá que aceptarla")

    def _hacer(self, op: str, datos: dict, mensaje: str) -> None:
        try:
            partidas.accion(self.ajustes, op, **datos)
            self.aviso(mensaje, tema.ACENTO)
        except api.ErrorApi as e:
            self.aviso(f"Error: {e}", tema.ERROR)

    def _buscar_roms(self) -> None:
        hechos = actualizar_roms.comprobar(self.ajustes, lambda t: self.aviso(t, tema.ACENTO))
        self.aviso(f"ROMs actualizados: {hechos}" if hechos else "Todos los ROMs instalados están al día")
        if hechos:
            self.cargar()

    def _cambiar_libreria(self) -> None:
        self.dialogos.append(Lista("Librería", [n for _, n in LIBRERIAS], self._fijar_libreria, inicial=self.libreria_idx))

    def _fijar_libreria(self, i: int) -> None:
        self.libreria_idx = i
        self.cargar()

    def _version(self) -> None:
        """Apartado «Versión»: versión, aviso legal (normas de uso y
        privacidad), licencia y licencias de terceros (ver legal.py)."""
        p = self.ajustes.plataforma
        servidor = self.info_servidor.get("servicio")
        detalle = (f"RomsLibrary {VERSION} · {plataforma.id_paquete()} · {p.nombre}\n"
                   f"Servidor: {self.ajustes['url']}" + (f" (servicio {servidor})" if servidor else "") + "\n"
                   f"Consola: {self.ajustes.nombre_consola or '—'} ({self.ajustes.identificador or 'sin registrar'})")
        opciones = [("Términos de uso y privacidad (aviso legal)", lambda: self._en_hilo(self._ver_aviso_legal)),
                    ("Licencia de RomsLibrary (MIT)", lambda: self.dialogos.append(
                        TextoLargo("Licencia de RomsLibrary", legal.licencia())))]
        terceros = legal.licencias_terceros()
        if terceros:
            opciones.append((f"Licencias de terceros ({len(terceros)})", lambda: self.dialogos.append(Lista(
                "Licencias de terceros", [t for t, _ in terceros],
                lambda i: self.dialogos.append(TextoLargo(terceros[i][0], terceros[i][1])), queda_abierta=True))))
        opciones.append(("Volver", lambda: True))
        self.dialogos.append(Lista("Versión", [o[0] for o in opciones], lambda i: opciones[i][1](), detalle=detalle,
                                   queda_abierta=True))

    def _ver_aviso_legal(self) -> None:
        self.aviso("Descargando el aviso legal…")
        self.dialogos.append(TextoLargo("Aviso legal y privacidad", legal.aviso_legal()))

    def abrir_ajustes(self) -> None:
        a = self.ajustes
        p = a.plataforma
        opciones = [
            (f"Servidor: {a['url']}", lambda: self.dialogos.append(Teclado("Servidor (https://…)", a["url"], self._fijar_url))),
            ("Clave de acceso: " + ("••••••" if a["clave"] else "(sin clave: solo los juegos compartidos)"),
             lambda: self.dialogos.append(Teclado("Clave de acceso", a["clave"], self._fijar_clave, oculto=True))),
            ("Solicitar acceso a esta librería" + (" (pendiente)" if acceso.pendiente(a) else ""),
             lambda: self._en_hilo(self._solicitar_acceso)),
            (f"Nombre de la consola: {a.nombre_consola or '—'}",
             lambda: self.dialogos.append(Teclado("Nombre de la consola", a.nombre_consola, self._fijar_nombre))),
            (f"Carpeta ROMs: {a['roms']}",
             lambda: self.dialogos.append(SelectorCarpeta("Carpeta ROMs", a["roms"], self._fijar_roms))),
            ("Carpeta de cada consola…", self._ajustes_carpetas),
            ("Mostrar todas las consolas: " + ("sí" if a["todas_las_consolas"] else f"no (solo las que mueve {p.nombre})"),
             self._alternar_todas),
            ("Intercambiar A y B: " + ("sí" if a["intercambiar_ab"] else "no"), self._alternar_ab),
        ]
        if p.partidas == "por_sistema":
            opciones.insert(4, (f"Carpeta de partidas: {a['partidas']}",
                                lambda: self.dialogos.append(SelectorCarpeta("Carpeta de partidas", a["partidas"],
                                                                             lambda r: a.__setitem__("partidas", r)))))
        self.dialogos.append(Lista("Ajustes", [o[0] for o in opciones], lambda i: opciones[i][1]()))

    def _reabrir_ajustes(self) -> None:
        self.dialogos = [d for d in self.dialogos if getattr(d, "titulo", "") != "Ajustes"]
        self.abrir_ajustes()

    def _fijar_url(self, url: str) -> None:
        url = url.strip().rstrip("/")
        if url and not url.startswith(("https://", "http://")):
            url = "https://" + url
        self.ajustes["url"] = url
        self.info_servidor = {}
        self._en_hilo(self._conectar)

    def _solicitar_acceso(self) -> None:
        try:
            if not self.ajustes.identificador:
                partidas.recuperar(self.ajustes)
            acceso.solicitar(self.ajustes)
            self.aviso("Solicitud enviada: cuando el administrador la acepte verás la librería entera", tema.ACENTO)
        except api.ErrorApi as e:
            self.aviso(f"No se pudo enviar: {e}", tema.ERROR)

    def _fijar_clave(self, clave: str) -> None:
        self.ajustes["clave"] = clave.strip()
        self.cargar()

    def _fijar_nombre(self, nombre: str) -> None:
        def hacer():
            try:
                if self.ajustes.identificador:
                    partidas.renombrar(self.ajustes, nombre)
                else:
                    partidas.registrar(self.ajustes, nombre)
                self.aviso(f"Nombre guardado: {self.ajustes.nombre_consola}", tema.ACENTO)
            except (api.ErrorApi, KeyError, TypeError) as e:
                self.aviso(f"No se pudo cambiar el nombre: {e}", tema.ERROR)
        self._en_hilo(hacer)

    def _fijar_roms(self, ruta: str) -> None:
        self.ajustes["roms"] = ruta
        self.ajustes["roms_elegida"] = True
        self.cargar()

    def _alternar_todas(self) -> None:
        self.ajustes["todas_las_consolas"] = not self.ajustes["todas_las_consolas"]
        self.sistema_idx = 0
        self.cargar()

    def _alternar_ab(self) -> None:
        self.ajustes["intercambiar_ab"] = not self.ajustes["intercambiar_ab"]
        self.entrada.intercambiar_ab = bool(self.ajustes["intercambiar_ab"])

    def _ajustes_carpetas(self) -> None:
        sistemas = self.ajustes.sistemas_visibles()
        opciones = [f"{plataforma.NOMBRES[s]}: {self.ajustes.carpeta_sistema(s).name}" for s in sistemas]

        def elegir(i):
            s = sistemas[i]

            def fijar(nombre):
                carpetas = dict(self.ajustes["carpetas"])
                nombre = nombre.strip()
                if nombre and catalogo.componente_seguro(nombre):
                    carpetas[s] = nombre
                else:
                    carpetas.pop(s, None)
                self.ajustes["carpetas"] = carpetas
                self.cargar()
            self.dialogos.append(Teclado(f"Carpeta de {plataforma.NOMBRES[s]} (vacío = la de siempre)",
                                         self.ajustes["carpetas"].get(s, ""), fijar))
        self.dialogos.append(Lista("Carpeta de cada consola", opciones, elegir,
                                   detalle=f"Dentro de {self.ajustes['roms']}"))

    def _pedir_salir(self) -> None:
        self.salir = True

    # ---------------- bucle ----------------

    def manejar(self, accion: str) -> None:
        if self.dialogos:
            d = self.dialogos[-1]
            if d.manejar(accion) and d in self.dialogos:
                self.dialogos.remove(d)
            return
        n = len(self.visibles)
        if accion in ("arriba", "abajo") and n:
            self.sel = (self.sel + (1 if accion == "abajo" else -1)) % n
        elif accion in ("izquierda", "derecha") and n:
            self.sel = max(0, min(n - 1, self.sel + (6 if accion == "derecha" else -6)))
        elif accion in ("pag_ant", "pag_sig"):
            self.sistema_idx = (self.sistema_idx + (1 if accion == "pag_sig" else -1)) % len(self.sistemas())
            self.sel = 0
            self.cargar()
        elif accion == "filtro":
            self.filtro_idx = (self.filtro_idx + 1) % len(FILTROS)
            self._refiltrar()
        elif accion == "aceptar" and n:
            self.accion_principal(self.visibles[self.sel])
        elif accion == "idioma" and n:
            self.elegir_idioma(self.visibles[self.sel])
        elif accion == "buscar":
            self.dialogos.append(Teclado("Buscar por nombre", self.busqueda, self._buscar))
        elif accion == "menu":
            self.abrir_menu()
        elif accion == "atras":
            if self.busqueda:
                self._buscar("")
            else:
                self.dialogos.append(Confirmar("Salir", "¿Cerrar RomsLibrary?", "Salir", self._pedir_salir))

    def _buscar(self, texto: str) -> None:
        self.busqueda = texto.strip()
        self.sel = 0
        self.cargar()

    def bucle(self) -> None:
        reloj = pygame.time.Clock()
        while not self.salir:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.salir = True
                elif ev.type == pygame.VIDEORESIZE:
                    tema.configurar(ev.h)
                    self.portadas.clear()
                elif ev.type == pygame.TEXTINPUT and self.dialogos and isinstance(self.dialogos[-1], Teclado) \
                        and ev.text not in ("\r", "\n"):
                    self.dialogos[-1].escribir(ev.text)
                    continue
                if ev.type == pygame.KEYDOWN and self.dialogos and isinstance(self.dialogos[-1], Teclado) \
                        and ev.unicode and ev.unicode.isprintable() and ev.key not in (pygame.K_RETURN,):
                    continue                            # lo escribe TEXTINPUT, no es una acción
                for accion in self.entrada.traducir(ev):
                    self.manejar(accion)
            for accion in self.entrada.repeticiones():
                self.manejar(accion)
            self.dibujar()
            self.guion.paso()
            pygame.display.flip()
            reloj.tick(30)
        self.cancelar_actual = True
        self.estado = "Subiendo partidas…"
        self.dibujar()
        pygame.display.flip()
        try:
            guardados.sincronizar(self.ajustes, [(j.sistema, j.fichero) for j in self.juegos if j.instalado])
        except Exception:  # noqa: BLE001
            pass
        pygame.quit()

    # ---------------- dibujo ----------------

    def dibujar(self) -> None:
        sup = self.pantalla
        sup.fill(tema.FONDO)
        w, h = sup.get_size()
        px = tema.px
        # Cabecera
        titulo = tema.texto(sup, "RomsLibrary", (px(16), px(10)), 30, tema.ACENTO, negrita=True)
        sistema = self.sistemas()[self.sistema_idx]
        chips = [("L/R", plataforma.NOMBRES.get(sistema, "Todas las consolas")),
                 ("Select", FILTROS[self.filtro_idx]), ("", LIBRERIAS[self.libreria_idx][1])]
        if self.busqueda:
            chips.append(("X", f"«{self.busqueda}»"))
        color = {"conectada": tema.ACENTO, "sin conexión": tema.AVISO}.get(self.conexion, tema.SUAVE)
        conexion = ("● " if self.conexion == "conectada" else "○ ") + self.conexion
        estado = self.estado or f"{len(self.visibles)} juego(s)"
        derecha = w - px(16) - tema.fuente(18).size(conexion)[0]
        x = titulo.right + px(20)
        for boton, valor in chips:
            texto = (boton + " " if boton else "") + valor
            if x + tema.fuente(20).size(texto)[0] > derecha - px(12):
                break                                   # pantalla estrecha: no cabe
            r = tema.texto(sup, texto, (x, px(16)), 20, tema.TEXTO)
            x = r.right + px(20)
        tema.texto(sup, conexion, (derecha, px(8)), 18, color)
        tema.texto(sup, estado, (w - px(16) - tema.fuente(16).size(estado)[0], px(30)), 16, tema.SUAVE,
                   ancho_max=w // 2)
        # Lista
        arriba, abajo = px(56), h - px(44)
        alto = px(96)
        visibles = max(1, (abajo - arriba) // alto)
        inicio = max(0, min(self.sel - visibles // 2, len(self.visibles) - visibles))
        for i in range(inicio, min(len(self.visibles), inicio + visibles)):
            self._fila(sup, self.visibles[i], pygame.Rect(px(10), arriba + (i - inicio) * alto, w - px(20), alto - px(6)),
                       i == self.sel)
        if not self.visibles and not self.cargando:
            tema.texto(sup, "Sin juegos" if not self.estado else self.estado, (w // 2, h // 2), 26, tema.SUAVE,
                       centrado=True)
        # Avisos
        ahora = time.monotonic()
        self.avisos = [a for a in self.avisos if a[1] > ahora]
        y = abajo - px(10)
        for texto, _, color in reversed(self.avisos):
            f = tema.fuente(20)
            ancho = min(w - px(40), f.size(texto)[0] + px(24))
            r = pygame.Rect(w - ancho - px(16), y - px(36), ancho, px(32))
            tema.caja(sup, r, (8, 10, 14), tema.BORDE, 8)
            tema.texto(sup, texto, (r.x + px(12), r.y + px(5)), 20, color, ancho_max=ancho - px(24))
            y -= px(40)
        if self.dialogos:
            self.dialogos[-1].dibujar(sup)
            tema.ayuda(sup, [("A", "Aceptar"), ("B", "Atrás")] + (
                [("Y", "Borrar"), ("X", "Espacio"), ("Start", "Hecho"), ("Select", "Mayús")]
                if isinstance(self.dialogos[-1], Teclado) else []))
        else:
            actual = self.visibles[self.sel] if self.visibles else None
            tema.ayuda(sup, [("A", "Desinstalar" if actual and actual.instalado else "Instalar" if not actual or actual.descargable
                                   else "No disponible"), ("Start", "Menú"),
                             ("X", "Buscar"), ("L/R", "Consola"), ("Y", "Idioma"), ("Select", "Filtro"),
                             ("B", "Salir")])     # en orden de importancia: en 640 px no caben todos

    def _fila(self, sup, j: catalogo.Juego, r: pygame.Rect, seleccionada: bool) -> None:
        px = tema.px
        tema.caja(sup, r, tema.SELECCION if seleccionada else tema.PANEL, tema.ACENTO if seleccionada else None, 10)
        lado = r.h - px(12)
        hueco = pygame.Rect(r.x + px(6), r.y + px(6), int(lado * 0.75), lado)
        pygame.draw.rect(sup, (32, 38, 46), hueco, border_radius=px(6))
        img = self.portada(j, hueco.size)
        if img:
            sup.blit(img, img.get_rect(center=hueco.center))
        x = hueco.right + px(14)
        ancho = r.right - x - px(14)
        tema.texto(sup, j.nombre_visible, (x, r.y + px(10)), 24, negrita=True, ancho_max=ancho)
        partes = [plataforma.NOMBRES.get(j.sistema, j.sistema), j.tamano_texto()]
        if j.idiomas:
            partes.append(j.etiqueta_idiomas())
        if len(j.variantes) > 1:
            partes.append(f"+{len(j.variantes) - 1} idioma(s)")
        if j.version and j.version > 1:
            partes.append(j.etiqueta or f"v{j.version}")
        if j.origen:
            partes.append("de " + j.libreria + (" ✔" if j.confianza >= 70 else ""))
        if j.fuente_externa:
            partes.append("enlace del autor")
        tema.texto(sup, " · ".join(partes), (x, r.y + px(42)), 19, tema.SUAVE, ancho_max=ancho)
        y3 = r.y + px(66)
        if getattr(j, "preparando", False):
            tema.texto(sup, "Preparando en el servidor (descomprimiendo)…", (x, y3), 18, tema.AVISO, ancho_max=ancho)
        elif j.progreso >= 0:
            tema.barra(sup, pygame.Rect(x, y3 + px(6), min(ancho, px(420)), px(10)), j.progreso)
            tema.texto(sup, f"{int(j.progreso * 100)}%", (x + min(ancho, px(420)) + px(10), y3), 18, tema.SUAVE)
        elif j.en_cola:
            tema.texto(sup, "En cola…", (x, y3), 18, tema.SUAVE)
        elif j.error:
            tema.texto(sup, "Error: " + j.error, (x, y3), 18, tema.ERROR, ancho_max=ancho)
        elif j.corrupto:
            tema.texto(sup, "⚠ ARCHIVO CORRUPTO", (x, y3), 18,
                       tema.CORRUPTO_AUTOMATICO if j.corrupto_automatico else tema.CORRUPTO, negrita=True)
        elif j.instalado:
            tema.texto(sup, "✓ En la consola", (x, y3), 18, tema.ACENTO)


def main() -> None:
    ventana = "--ventana" in sys.argv
    try:
        App(ventana).bucle()
    except Exception:
        # Sin consola en los portátiles: el fallo queda escrito para poder verlo.
        import traceback
        (carpeta_datos() / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


class _Guion:
    """Acciones guionizadas para probar sin mando (RL_GUION). Pasos separados
    por «;»: una acción (abajo, aceptar, menu...), «esperar N», «tabla»
    (espera a que llegue la tabla de juegos, como mucho 60 s), «captura
    nombre» (PNG en RL_CAPTURAS) y «salir»."""

    def __init__(self, app):
        self.app = app
        self.pasos = [p.strip() for p in os.environ.get("RL_GUION", "").split(";") if p.strip()]
        self.hasta = 0.0
        self.carpeta = Path(os.environ.get("RL_CAPTURAS") or ".")

    def paso(self) -> None:
        if not self.pasos or time.monotonic() < self.hasta:
            return
        orden, _, arg = self.pasos[0].partition(" ")
        if orden == "tabla":
            if (self.app.juegos or self.app.estado.startswith("Error")) and not self.app.cargando:
                self.pasos.pop(0)
            elif time.monotonic() - INICIO > 60:
                self.pasos.pop(0)
            return
        self.pasos.pop(0)
        if orden == "esperar":
            self.hasta = time.monotonic() + float(arg or 1)
        elif orden == "captura":
            self.carpeta.mkdir(parents=True, exist_ok=True)
            pygame.image.save(self.app.pantalla, str(self.carpeta / f"{arg or 'captura'}.png"))
            print(f"[guion] captura {arg}", flush=True)
        elif orden == "salir":
            self.app.salir = True
        else:
            self.app.manejar(orden)
            self.hasta = time.monotonic() + 0.3
