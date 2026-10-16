package es.jarjar.romlibrary;

import android.content.Context;
import android.content.SharedPreferences;
import android.provider.Settings;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.File;
import java.io.IOException;
import java.io.OutputStream;
import java.util.Map;

/**
 * Servicio de PARTIDAS (sund.es/partidas): guardados, identidad de la consola
 * y sincronización entre consolas, aparte de la librería de la que vengan los
 * juegos. Así las partidas no dependen de que una librería sea pública o
 * privada, y las librerías nunca ven el ANDROID_ID.
 *
 * El ANDROID_ID es la contraseña de la consola: solo viaja aquí (POST por
 * HTTPS) para abrir una sesión, y el servidor guarda solo su HMAC. Todo lo
 * demás va con el token de la sesión en la cabecera Authorization. Si
 * caduca (401), se abre otra sola.
 *
 * Bloqueante: llamar siempre desde un hilo de fondo.
 */
final class Partidas {

    static final String URL_POR_DEFECTO = "https://sund.es";
    private static final String PREFIJO = "/partidas/v1";

    private static final String PREF_URL = "partidas_url";
    private static final String PREF_TOKEN = "partidas_token";
    private static final String PREF_IDENTIFICADOR = "partidas_identificador";
    private static final String PREF_NOMBRE = "partidas_nombre";
    // Identidad de versiones anteriores (una por librería): solo para recuperar el nombre.
    private static final String PREF_NOMBRE_ANTIGUO = "consola_nombre@";

    private Partidas() {
    }

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE);
    }

    static String url(Context c) {
        return prefs(c).getString(PREF_URL, URL_POR_DEFECTO);
    }

    /** XXXXX-XXXXX de esta consola, o null si aún no está registrada. */
    static String identificador(Context c) {
        return prefs(c).getString(PREF_IDENTIFICADOR, null);
    }

    static String nombre(Context c) {
        return prefs(c).getString(PREF_NOMBRE, null);
    }

    static boolean registrada(Context c) {
        return identificador(c) != null;
    }

    private static String secreto(Context c) {
        return Settings.Secure.getString(c.getContentResolver(), Settings.Secure.ANDROID_ID);
    }

    /**
     * Abre sesión. Con nombre, registra la consola si es nueva (si ya existía,
     * el nombre no cambia: para eso, renombrar). Sin nombre, solo entra:
     * ErrorHttp 404 si la consola no está registrada.
     */
    private static void abrirSesion(Context c, String nombre) throws IOException {
        try {
            JSONObject datos = new JSONObject().put("secreto", secreto(c));
            if (nombre != null) {
                datos.put("nombre", nombre);
            }
            JSONObject r = new JSONObject(ApiClient.postJson(url(c), null, PREFIJO + "/sesion", datos));
            prefs(c).edit()
                    .putString(PREF_TOKEN, r.getString("token"))
                    .putString(PREF_IDENTIFICADOR, r.getString("identificador"))
                    .putString(PREF_NOMBRE, r.getString("nombre"))
                    .apply();
        } catch (JSONException e) {
            throw new IOException("Respuesta inesperada del servicio de partidas.");
        }
    }

    /** Registra esta consola (o entra si ya lo estaba) y le pone ese nombre. */
    static void registrar(Context c, String nombre) throws IOException {
        abrirSesion(c, nombre);
        if (!nombre.equals(nombre(c))) {
            renombrar(c, nombre);
        }
    }

    /**
     * Al arrancar sin identidad guardada: la consola puede estar ya registrada
     * (reinstalación, o venir de una versión que guardaba las partidas en la
     * librería, que se migraron aquí). Si no, se registra con el nombre que
     * tuviera. Devuelve si ha quedado registrada.
     */
    static boolean recuperar(Context c) {
        try {
            abrirSesion(c, null);
            return true;
        } catch (ApiClient.ErrorHttp e) {
            if (e.codigo != 404) {
                return false;
            }
        } catch (IOException e) {
            return false;
        }
        for (Map.Entry<String, ?> p : prefs(c).getAll().entrySet()) {
            if (p.getKey().startsWith(PREF_NOMBRE_ANTIGUO) && p.getValue() instanceof String) {
                try {
                    abrirSesion(c, (String) p.getValue());
                    return true;
                } catch (IOException e) {
                    return false;
                }
            }
        }
        return false;
    }

    static void renombrar(Context c, String nombre) throws IOException {
        try {
            JSONObject r = new JSONObject(postJson(c, "/consola/renombrar", new JSONObject().put("nombre", nombre)));
            prefs(c).edit().putString(PREF_NOMBRE, r.getString("nombre")).apply();
        } catch (JSONException e) {
            throw new IOException("Respuesta inesperada del servicio de partidas.");
        }
    }

    // ---------- Peticiones con sesión (se renueva sola si caduca) ----------

    private interface Peticion<T> {
        T hacer(String sesion) throws IOException;
    }

    private static <T> T conSesion(Context c, Peticion<T> peticion) throws IOException {
        String token = prefs(c).getString(PREF_TOKEN, null);
        if (token != null) {
            try {
                return peticion.hacer(ApiClient.SESION + token);
            } catch (ApiClient.ErrorHttp e) {
                if (e.codigo != 401) {
                    throw e;
                }
            }
        }
        abrirSesion(c, null);
        return peticion.hacer(ApiClient.SESION + prefs(c).getString(PREF_TOKEN, ""));
    }

    static String get(Context c, String ruta, int timeoutMs) throws IOException {
        return conSesion(c, s -> ApiClient.getTexto(url(c), s, PREFIJO + ruta, timeoutMs));
    }

    static String postJson(Context c, String ruta, JSONObject datos) throws IOException {
        return conSesion(c, s -> ApiClient.postJson(url(c), s, PREFIJO + ruta, datos));
    }

    static void subir(Context c, Map<String, String> campos, String nombreFichero, File local) throws IOException {
        conSesion(c, s -> {
            ApiClient.subirFichero(url(c), s, PREFIJO + "/partida", campos, nombreFichero, local);
            return null;
        });
    }

    static void bajar(Context c, String sistema, String clave, String nombreFichero, OutputStream destino)
            throws IOException {
        String ruta = PREFIJO + "/partida?sistema=" + android.net.Uri.encode(sistema) + "&clave="
                + android.net.Uri.encode(clave) + "&nombre_fichero=" + android.net.Uri.encode(nombreFichero);
        conSesion(c, s -> {
            ApiClient.descargarAFichero(url(c), s, ruta, destino);
            return null;
        });
    }
}
