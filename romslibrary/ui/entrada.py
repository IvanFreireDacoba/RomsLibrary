"""
Mando, teclado y ratón traducidos a unas pocas acciones. Los mandos se leen
con la API GameController de SDL (en las consolas, PortMaster le pasa el
mapeado del aparato en SDL_GAMECONTROLLERCONFIG).

Acciones: arriba, abajo, izquierda, derecha, aceptar, atras, idioma (Y),
buscar (X), menu (Start), filtro (Select), pag_ant / pag_sig (L / R).
"""

import time

import pygame

try:
    from pygame._sdl2 import controller as _controller
except ImportError:            # pygame sin el módulo de mandos: solo teclado
    _controller = None

_TECLAS = {
    pygame.K_UP: "arriba", pygame.K_DOWN: "abajo", pygame.K_LEFT: "izquierda", pygame.K_RIGHT: "derecha",
    pygame.K_RETURN: "aceptar", pygame.K_KP_ENTER: "aceptar", pygame.K_SPACE: "aceptar",
    pygame.K_ESCAPE: "atras", pygame.K_BACKSPACE: "atras",
    pygame.K_y: "idioma", pygame.K_l: "idioma", pygame.K_x: "buscar", pygame.K_SLASH: "buscar",
    pygame.K_TAB: "menu", pygame.K_m: "menu", pygame.K_f: "filtro",
    pygame.K_PAGEUP: "pag_ant", pygame.K_PAGEDOWN: "pag_sig", pygame.K_q: "pag_ant", pygame.K_e: "pag_sig",
}


class Entrada:
    def __init__(self, intercambiar_ab: bool = False):
        self.intercambiar_ab = intercambiar_ab
        self.mandos = {}
        self._ejes = {}
        self._pulsada = None            # (acción de dirección, instante en que se repite)
        if _controller:
            _controller.init()
            for i in range(_controller.get_count()):
                self._abrir(i)

    def _abrir(self, indice: int) -> None:
        try:
            if _controller.is_controller(indice):
                c = _controller.Controller(indice)
                self.mandos[c.as_joystick().get_instance_id()] = c
        except pygame.error:
            pass

    def _botones(self) -> dict:
        a, b = ("atras", "aceptar") if self.intercambiar_ab else ("aceptar", "atras")
        return {
            pygame.CONTROLLER_BUTTON_A: a, pygame.CONTROLLER_BUTTON_B: b,
            pygame.CONTROLLER_BUTTON_Y: "idioma", pygame.CONTROLLER_BUTTON_X: "buscar",
            pygame.CONTROLLER_BUTTON_START: "menu", pygame.CONTROLLER_BUTTON_BACK: "filtro",
            pygame.CONTROLLER_BUTTON_LEFTSHOULDER: "pag_ant", pygame.CONTROLLER_BUTTON_RIGHTSHOULDER: "pag_sig",
            pygame.CONTROLLER_BUTTON_DPAD_UP: "arriba", pygame.CONTROLLER_BUTTON_DPAD_DOWN: "abajo",
            pygame.CONTROLLER_BUTTON_DPAD_LEFT: "izquierda", pygame.CONTROLLER_BUTTON_DPAD_RIGHT: "derecha",
        }

    def traducir(self, ev) -> list[str]:
        """Acciones que produce un evento (normalmente 0 o 1)."""
        if ev.type == pygame.KEYDOWN:
            return [_TECLAS[ev.key]] if ev.key in _TECLAS else []
        if _controller and ev.type == pygame.CONTROLLERDEVICEADDED:
            self._abrir(ev.device_index)
            return []
        if ev.type == pygame.CONTROLLERDEVICEREMOVED:
            self.mandos.pop(ev.instance_id, None)
            return []
        if ev.type == pygame.CONTROLLERBUTTONDOWN:
            accion = self._botones().get(ev.button)
            if accion in ("arriba", "abajo", "izquierda", "derecha"):
                self._pulsada = (accion, time.monotonic() + 0.35)
            return [accion] if accion else []
        if ev.type == pygame.CONTROLLERBUTTONUP:
            if self._pulsada and self._botones().get(ev.button) == self._pulsada[0]:
                self._pulsada = None
            return []
        if ev.type == pygame.CONTROLLERAXISMOTION and ev.axis in (pygame.CONTROLLER_AXIS_LEFTY,
                                                                  pygame.CONTROLLER_AXIS_LEFTX,
                                                                  pygame.CONTROLLER_AXIS_TRIGGERLEFT,
                                                                  pygame.CONTROLLER_AXIS_TRIGGERRIGHT):
            # Palanca y gatillos como botones: solo al cruzar el umbral.
            valor = ev.value / 32767
            antes = self._ejes.get((ev.instance_id, ev.axis), 0)
            ahora = 1 if valor > 0.6 else -1 if valor < -0.6 else 0
            self._ejes[(ev.instance_id, ev.axis)] = ahora
            if ahora == antes:
                return []
            if ahora == 0 and ev.axis in (pygame.CONTROLLER_AXIS_TRIGGERLEFT, pygame.CONTROLLER_AXIS_TRIGGERRIGHT):
                return []
            if ev.axis in (pygame.CONTROLLER_AXIS_LEFTY, pygame.CONTROLLER_AXIS_LEFTX):
                if ahora == 0:
                    self._pulsada = None
                    return []
                if ev.axis == pygame.CONTROLLER_AXIS_LEFTY:
                    accion = "abajo" if ahora > 0 else "arriba"
                else:
                    accion = "derecha" if ahora > 0 else "izquierda"
                self._pulsada = (accion, time.monotonic() + 0.35)
                return [accion]
            return ["pag_ant" if ev.axis == pygame.CONTROLLER_AXIS_TRIGGERLEFT else "pag_sig"]
        if ev.type == pygame.MOUSEWHEEL:
            return ["arriba"] * max(0, ev.y) + ["abajo"] * max(0, -ev.y)
        return []

    def repeticiones(self) -> list[str]:
        """Dirección mantenida en el mando: se repite (el teclado ya lo hace solo)."""
        if self._pulsada and time.monotonic() >= self._pulsada[1]:
            self._pulsada = (self._pulsada[0], time.monotonic() + 0.07)
            return [self._pulsada[0]]
        return []
