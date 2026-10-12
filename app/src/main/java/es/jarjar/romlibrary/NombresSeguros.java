package es.jarjar.romlibrary;

/**
 * Los nombres que llegan del servidor (sistema, fichero, rutas dentro de un
 * grupo, partidas) acaban como nombres de fichero en la consola. Un servidor
 * malicioso podría mandar "../" para escribir fuera de su carpeta (la app tiene
 * acceso a todos los archivos), así que solo se aceptan nombres simples.
 */
final class NombresSeguros {

    private NombresSeguros() {
    }

    /** Un único componente de ruta: sin barras, sin "." ni "..", sin caracteres de control. */
    static boolean componente(String nombre) {
        if (nombre == null || nombre.isEmpty() || nombre.length() > 255
                || nombre.equals(".") || nombre.equals("..")) {
            return false;
        }
        for (int i = 0; i < nombre.length(); i++) {
            char c = nombre.charAt(i);
            if (c == '/' || c == '\\' || c < 0x20 || c == 0x7f) {
                return false;
            }
        }
        return true;
    }

    /** Ruta relativa "a/b/c": cada componente tiene que ser válido (vacía también vale). */
    static boolean rutaRelativa(String ruta) {
        if (ruta == null) {
            return false;
        }
        if (ruta.isEmpty()) {
            return true;
        }
        for (String parte : ruta.split("/", -1)) {
            if (!componente(parte)) {
                return false;
            }
        }
        return true;
    }
}
