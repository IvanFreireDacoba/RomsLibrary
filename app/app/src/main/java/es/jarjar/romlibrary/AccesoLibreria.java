package es.jarjar.romlibrary;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

import java.io.IOException;
import java.security.SecureRandom;

/**
 * «Solicitar acceso» a la librería conectada: en vez de repartir la clave de
 * la librería, la app genera un token propio de esta consola y lo manda con
 * el nombre y el identificador de la consola. El administrador la acepta o
 * la rechaza en su gestor; aceptada, ese token es la clave de esta consola
 * (y solo de ella: se puede revocar sin tocar a las demás). Ver accesos.py
 * en el servicio.
 *
 * Bloqueante: llamar desde un hilo de fondo.
 */
final class AccesoLibreria {

    private static final String PREF_PENDIENTE = "acceso_pendiente@";

    private AccesoLibreria() {
    }

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE);
    }

    static boolean pendiente(Context c, String url) {
        return prefs(c).getString(PREF_PENDIENTE + url, null) != null;
    }

    static void solicitar(Context c, String url, String nombre) throws IOException {
        url = Cuenta.normalizarUrl(url);
        byte[] bytes = new byte[24];
        new SecureRandom().nextBytes(bytes);
        StringBuilder token = new StringBuilder();
        for (byte b : bytes) {
            token.append(String.format("%02x", b));
        }
        try {
            JSONObject datos = new JSONObject().put("token", token.toString()).put("nombre", nombre)
                    .put("consola", Partidas.identificador(c) == null ? "" : Partidas.identificador(c));
            ApiClient.postJson(url, null, Cuenta.RUTA_API + "?accion=solicitar_acceso", datos);
        } catch (org.json.JSONException e) {
            throw new IOException(e.getMessage());
        }
        prefs(c).edit().putString(PREF_PENDIENTE + url, token.toString()).apply();
        Cuenta.guardar(c, url, Cuenta.cargar(c).url.equals(url) ? Cuenta.cargar(c).clave : "");
    }

    /**
     * Si hay una solicitud pendiente para la librería actual, mira cómo va.
     * Devuelve "aceptada" (y deja el token como clave), "rechazada"
     * (rechazada, revocada o caducada: se olvida) o null (sigue pendiente o
     * no hay solicitud).
     */
    static String comprobar(Context c) {
        Cuenta cuenta = Cuenta.cargar(c);
        String token = prefs(c).getString(PREF_PENDIENTE + cuenta.url, null);
        if (token == null) {
            return null;
        }
        try {
            String estado = new JSONObject(ApiClient.getTexto(cuenta.url, token,
                    Cuenta.RUTA_API + "?accion=acceso_estado", 10000)).optString("estado");
            if (estado.equals("aceptada")) {
                Cuenta.guardar(c, cuenta.url, token);
            } else if (estado.equals("pendiente")) {
                return null;
            }
            prefs(c).edit().remove(PREF_PENDIENTE + cuenta.url).apply();
            return estado.equals("aceptada") ? "aceptada" : "rechazada";
        } catch (Exception e) {
            return null;                                      // sin conexión: se mira la próxima vez
        }
    }
}
