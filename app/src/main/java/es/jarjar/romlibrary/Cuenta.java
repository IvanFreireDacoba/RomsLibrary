package es.jarjar.romlibrary;

import android.content.Context;
import android.content.SharedPreferences;

import java.io.IOException;

/**
 * Librería a la que se conecta la app: URL + clave de acceso (solo las
 * privadas la piden). La APK es la misma para todo el mundo; cada uno apunta
 * a la librería que quiera. La identidad de la consola y sus partidas no van
 * aquí sino al servicio de partidas (ver Partidas), igual para todas.
 */
final class Cuenta {

    // Por defecto, sund.es (librería pública: no pide clave). Se puede
    // cambiar a cualquier otra; las privadas piden su clave.
    static final String URL_POR_DEFECTO = "https://sund.es";
    // La APK oficial la publica siempre sund.es: de ahí se actualiza la app
    // mientras no haya servidor configurado.
    static final String ORIGEN_ACTUALIZACIONES = "https://sund.es";
    static final String RUTA_API = "/APIs/RomLibraryApp";

    private static final String PREF_URL = "servidor_url";
    private static final String PREF_CLAVE = "servidor_clave";

    final String url;
    final String clave;

    private Cuenta(String url, String clave) {
        this.url = url;
        this.clave = clave;
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE);
    }

    static Cuenta cargar(Context context) {
        SharedPreferences p = prefs(context);
        return new Cuenta(p.getString(PREF_URL, URL_POR_DEFECTO), p.getString(PREF_CLAVE, ""));
    }

    /** "https://sund.es/" -> "https://sund.es"; añade https:// si falta. */
    static String normalizarUrl(String url) {
        String u = url == null ? "" : url.trim();
        while (u.endsWith("/")) {
            u = u.substring(0, u.length() - 1);
        }
        if (!u.isEmpty() && !u.startsWith("http://") && !u.startsWith("https://")) {
            u = "https://" + u;
        }
        return u;
    }

    /**
     * Cambia de librería, comprobando antes que responde y que acepta la
     * clave. Bloqueante: llamar desde un hilo de fondo.
     */
    static Cuenta configurar(Context context, String url, String clave) throws IOException {
        url = normalizarUrl(url);
        if (url.isEmpty()) {
            throw new IOException("Falta la dirección del servidor.");
        }
        ApiClient.getTexto(url, clave, RUTA_API + "?accion=sistemas", 10000);
        prefs(context).edit().putString(PREF_URL, url).putString(PREF_CLAVE, clave).apply();
        return new Cuenta(url, clave);
    }
}
