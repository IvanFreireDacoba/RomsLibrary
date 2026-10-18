package es.jarjar.romlibrary;

/** Botón explícito de Instalar/Desinstalar por tarjeta -- compartido por la vista de lista y la de cuadrícula. */
interface AccionInstalarListener {
    void onAccionInstalar(Juego juego);

    /** Pulsación larga: elegir otro idioma del mismo juego (si lo hay). */
    void onElegirIdioma(Juego juego);
}
