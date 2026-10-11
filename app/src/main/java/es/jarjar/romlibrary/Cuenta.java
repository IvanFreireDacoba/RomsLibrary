package es.jarjar.romlibrary;

import android.content.Context;
import android.content.SharedPreferences;
import android.provider.Settings;

import org.json.JSONObject;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Servidor al que se conecta la app (URL + clave de acceso) y la identidad
 * de esta consola en él: el identificador público XXXXX-XXXXX y el nombre
 * que le puso el usuario. La APK es la misma para todo el mundo; cada uno
 * apunta a su propio servicio de librería.
 *
 * El ANDROID_ID es la credencial que, junto al identificador, autoriza a
 * tocar los guardados: nunca se enseña en pantalla. La identidad se guarda
 * por servidor: la misma consola tiene un identificador distinto en cada uno.
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
    private static final String PREF_IDENTIFICADOR = "consola_identificador@";
    private static final String PREF_NOMBRE = "consola_nombre@";

    final String url;
    final String clave;
    final String androidId;
    final String identificador; // null si esta consola aún no se ha registrado en este servidor
    final String nombre;

    private Cuenta(String url, String clave, String androidId, String identificador, String nombre) {
        this.url = url;
        this.clave = clave;
        this.androidId = androidId;
        this.identificador = identificador;
        this.nombre = nombre;
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE);
    }

    static Cuenta cargar(Context context) {
        SharedPreferences p = prefs(context);
        String url = p.getString(PREF_URL, URL_POR_DEFECTO);
        String androidId = Settings.Secure.getString(context.getContentResolver(), Settings.Secure.ANDROID_ID);
        return new Cuenta(url, p.getString(PREF_CLAVE, ""), androidId,
                p.getString(PREF_IDENTIFICADOR + url, null), p.getString(PREF_NOMBRE + url, null));
    }

    boolean registrada() {
        return identificador != null;
    }

    /** Campos de identidad que exigen las acciones v2 (android_id + identificador). */
    Map<String, String> identidad() {
        Map<String, String> campos = new LinkedHashMap<>();
        campos.put("android_id", androidId);
        campos.put("identificador", identificador);
        return campos;
    }

    String consultaIdentidad() {
        return "&android_id=" + android.net.Uri.encode(androidId) + "&identificador=" + android.net.Uri.encode(identificador);
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
     * Configura el servidor y registra esta consola en él (idempotente: si ya
     * estaba registrada devuelve su identificador de siempre y su nombre).
     * Bloqueante: llamar desde un hilo de fondo.
     */
    static Cuenta configurar(Context context, String url, String clave, String nombre) throws IOException {
        url = normalizarUrl(url);
        if (url.isEmpty()) {
            throw new IOException("Falta la dirección del servidor.");
        }
        String androidId = Settings.Secure.getString(context.getContentResolver(), Settings.Secure.ANDROID_ID);
        Map<String, String> campos = new LinkedHashMap<>();
        campos.put("android_id", androidId);
        campos.put("nombre", nombre == null ? "" : nombre);
        String respuesta = ApiClient.postFormulario(url, clave, RUTA_API + "?accion=consola_registrar", campos);
        try {
            JSONObject o = new JSONObject(respuesta);
            String identificador = o.getString("identificador");
            String nombreReal = o.getString("nombre");
            prefs(context).edit()
                    .putString(PREF_URL, url)
                    .putString(PREF_CLAVE, clave)
                    .putString(PREF_IDENTIFICADOR + url, identificador)
                    .putString(PREF_NOMBRE + url, nombreReal)
                    .apply();
            return new Cuenta(url, clave, androidId, identificador, nombreReal);
        } catch (org.json.JSONException e) {
            throw new IOException("Respuesta inesperada del servidor.");
        }
    }

    /** Tras renombrar en el servidor, se actualiza la copia local. */
    static void guardarNombre(Context context, String url, String nombre) {
        prefs(context).edit().putString(PREF_NOMBRE + url, nombre).apply();
    }
}
