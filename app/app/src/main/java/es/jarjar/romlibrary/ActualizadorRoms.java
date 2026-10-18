package es.jarjar.romlibrary;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;

import androidx.documentfile.provider.DocumentFile;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Actualiza los ROMs instalados cuando la librería tiene una versión más
 * nueva (ver versiones.py en el servicio). La identidad es el SHA-256, nunca
 * el nombre: la librería manda la lista de versiones SUPERADAS (sha + tamaño)
 * de cada juego y su versión actual. Aquí:
 *
 *   1. Solo se calcula el SHA de los ficheros de la consola cuyo tamaño
 *      coincide con el de alguna versión superada de su sistema (calcular el
 *      de todo, con ISOs de varios GB, sería muy lento). Cada SHA se guarda
 *      en caché por fichero + tamaño + fecha.
 *   2. Si un fichero es una versión superada: se suben antes sus partidas, se
 *      baja la versión actual a un temporal, se comprueba su SHA y se pone en
 *      su sitio CON EL MISMO NOMBRE que tenía en la consola. Así el emulador
 *      sigue encontrando sus partidas (van por el nombre del ROM).
 *
 * Una consola con la v1 y otra con la v2 pasan las dos a la actual. Solo
 * juegos de un fichero (los de varios, aún no). Nunca deja un juego a medias:
 * el original solo se borra cuando la versión nueva está entera y verificada.
 */
final class ActualizadorRoms {

    private ActualizadorRoms() {
    }

    private static final String PREFS_HASHES = "hashes_roms";

    /** Una versión superada de un juego de la librería. */
    private static final class Obsoleta {
        String sistema, sha, filenameActual, shaActual;
        String origenUrl;       // la versión actual está en otra librería (de confianza): se baja de allí
        String fuenteExterna;   // o en la web de su autor (se prueba primero)
        long tamano;
        int n, nActual;
    }

    private static final class Tarea {
        DocumentFile carpeta, fichero;
        Obsoleta o;
    }

    interface Aviso {
        void mostrar(String texto);
    }

    /** Bloqueante (hilo en segundo plano). Devuelve cuántos juegos ha actualizado. */
    static int comprobar(Context ctx, String baseUrl, String token, Uri arbolUri, Aviso aviso) {
        if (arbolUri == null) {
            return 0;
        }
        try {
            List<Obsoleta> obsoletas = leerObsoletas(baseUrl, token);
            if (obsoletas.isEmpty()) {
                return 0;
            }
            DocumentFile raiz = DocumentFile.fromTreeUri(ctx, arbolUri);
            if (raiz == null) {
                return 0;
            }
            List<Tarea> tareas = buscar(ctx, raiz, obsoletas);
            int hechas = 0;
            for (Tarea t : tareas) {
                String nombre = t.fichero.getName();
                aviso.mostrar("Actualizando " + nombre + " a la versión " + t.o.nActual + "…");
                if (actualizar(ctx, baseUrl, token, t)) {
                    hechas++;
                    aviso.mostrar("Actualizado: " + nombre + " (v" + t.o.n + " → v" + t.o.nActual + ")");
                }
            }
            return hechas;
        } catch (Exception e) {
            GuardadosSync.log("actualizador de ROMs: " + e);
            return 0;
        }
    }

    private static List<Obsoleta> leerObsoletas(String baseUrl, String token) throws Exception {
        JSONArray arr = new JSONObject(ApiClient.getTexto(baseUrl, token, Cuenta.RUTA_API + "?accion=versiones", 15000))
                .getJSONArray("obsoletas");
        List<Obsoleta> lista = new ArrayList<>();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject j = arr.getJSONObject(i);
            JSONObject a = j.getJSONObject("actual");
            Obsoleta o = new Obsoleta();
            o.sistema = j.getString("sistema");
            o.sha = j.getString("sha").toLowerCase();
            o.tamano = j.getLong("tamano");
            o.n = j.getInt("n");
            o.filenameActual = a.getString("filename");
            o.shaActual = a.getString("sha").toLowerCase();
            o.nActual = a.getInt("n");
            String origen = a.isNull("origenUrl") ? "" : a.optString("origenUrl", "");
            o.origenUrl = origen.startsWith("https://") && !origen.contains("@") ? origen : null;
            String externa = a.isNull("fuenteExterna") ? "" : a.optString("fuenteExterna", "");
            o.fuenteExterna = externa.startsWith("https://") && !externa.contains("@") ? externa : null;
            // Nombres que acaban en disco: los mismos filtros que al instalar.
            if (NombresSeguros.componente(o.sistema) && NombresSeguros.componente(o.filenameActual)) {
                lista.add(o);
            }
        }
        return lista;
    }

    private static List<Tarea> buscar(Context ctx, DocumentFile raiz, List<Obsoleta> obsoletas) {
        Map<String, Map<Long, List<Obsoleta>>> porSistema = new HashMap<>();
        for (Obsoleta o : obsoletas) {
            porSistema.computeIfAbsent(o.sistema, k -> new HashMap<>())
                    .computeIfAbsent(o.tamano, k -> new ArrayList<>()).add(o);
        }
        List<Tarea> tareas = new ArrayList<>();
        for (Map.Entry<String, Map<Long, List<Obsoleta>>> s : porSistema.entrySet()) {
            DocumentFile carpeta = raiz.findFile(s.getKey());
            if (carpeta == null || !carpeta.isDirectory()) {
                continue;
            }
            for (DocumentFile f : carpeta.listFiles()) {
                if (!f.isFile() || f.getName() == null || f.getName().endsWith(".actualizando")) {
                    continue;
                }
                List<Obsoleta> candidatas = s.getValue().get(f.length());
                if (candidatas == null) {
                    continue;
                }
                String sha = shaConCache(ctx, f);
                for (Obsoleta o : candidatas) {
                    if (o.sha.equals(sha)) {
                        Tarea t = new Tarea();
                        t.carpeta = carpeta;
                        t.fichero = f;
                        t.o = o;
                        tareas.add(t);
                        break;
                    }
                }
            }
        }
        return tareas;
    }

    private static boolean actualizar(Context ctx, String baseUrl, String token, Tarea t) {
        String nombre = t.fichero.getName();
        DocumentFile temporal = null;
        try {
            // 1. Partidas a salvo en el servidor antes de tocar nada.
            Juego juego = new Juego(t.o.sistema, nombre, t.fichero.length(), null, false, null, 0, 0, 0);
            GuardadosSync.subirAntesDeDesinstalar(ctx, juego);

            // 2. Versión actual a un temporal y comprobada.
            temporal = t.carpeta.createFile("application/octet-stream", nombre + ".actualizando");
            if (temporal == null) {
                throw new IllegalStateException("no se pudo crear el temporal");
            }
            final Uri uriTemporal = temporal.getUri();
            String sha = "";
            if (t.o.fuenteExterna != null) {
                // Primero la web del autor; si falla o no coincide, la librería.
                try {
                    ApiClient.descargarConReintentos("", null, t.o.fuenteExterna,
                            anadir -> ctx.getContentResolver().openOutputStream(uriTemporal, anadir ? "wa" : "wt"),
                            (hecho, total) -> { });
                    sha = sha(ctx, uriTemporal);
                } catch (Exception e) {
                    GuardadosSync.log("enlace del autor no disponible: " + e);
                }
            }
            if (!t.o.shaActual.equals(sha)) {
                // De otra librería: directo de ella y sin nuestra clave.
                boolean otra = t.o.origenUrl != null;
                ApiClient.descargarConReintentos(otra ? t.o.origenUrl : baseUrl, otra ? null : token,
                        Cuenta.RUTA_API + "?accion=descargar&system=" + Uri.encode(t.o.sistema)
                                + "&filename=" + Uri.encode(t.o.filenameActual),
                        anadir -> ctx.getContentResolver().openOutputStream(uriTemporal, anadir ? "wa" : "wt"),
                        (hecho, total) -> { });
                sha = sha(ctx, uriTemporal);
            }
            if (!t.o.shaActual.equals(sha)) {
                throw new IllegalStateException("la descarga no coincide con la versión publicada");
            }

            // 3. Cambio: el original se aparta, el nuevo toma su nombre y solo
            //    entonces se borra el original (si algo falla, se deshace).
            String apartado = nombre + ".anterior";
            if (!t.fichero.renameTo(apartado)) {
                throw new IllegalStateException("no se pudo apartar el original");
            }
            if (!temporal.renameTo(nombre)) {
                t.fichero.renameTo(nombre);
                throw new IllegalStateException("no se pudo renombrar la versión nueva");
            }
            temporal = null;
            t.fichero.delete();
            DocumentFile nuevo = t.carpeta.findFile(nombre);
            if (nuevo != null) {
                guardarCache(ctx, nuevo, sha);
            }
            GuardadosSync.log("ROM actualizado: " + t.o.sistema + "/" + nombre + " v" + t.o.n + " -> v" + t.o.nActual);
            return true;
        } catch (Exception e) {
            GuardadosSync.log("ERROR actualizando " + t.o.sistema + "/" + nombre + ": " + e);
            if (temporal != null) {
                temporal.delete();
            }
            return false;
        }
    }

    // ---------- SHA-256 ----------

    private static String shaConCache(Context ctx, DocumentFile f) {
        SharedPreferences p = ctx.getSharedPreferences(PREFS_HASHES, Context.MODE_PRIVATE);
        String clave = f.getUri().toString();
        String firma = f.length() + "|" + f.lastModified() + "|";
        String guardado = p.getString(clave, "");
        if (guardado.startsWith(firma)) {
            return guardado.substring(firma.length());
        }
        try {
            String sha = sha(ctx, f.getUri());
            guardarCache(ctx, f, sha);
            return sha;
        } catch (Exception e) {
            return "";
        }
    }

    private static void guardarCache(Context ctx, DocumentFile f, String sha) {
        ctx.getSharedPreferences(PREFS_HASHES, Context.MODE_PRIVATE).edit()
                .putString(f.getUri().toString(), f.length() + "|" + f.lastModified() + "|" + sha).apply();
    }

    static String sha(Context ctx, Uri uri) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        try (InputStream in = ctx.getContentResolver().openInputStream(uri)) {
            if (in == null) {
                throw new IllegalStateException("no se pudo leer " + uri);
            }
            byte[] trozo = new byte[1024 * 1024];
            int n;
            while ((n = in.read(trozo)) != -1) {
                md.update(trozo, 0, n);
            }
        }
        StringBuilder hex = new StringBuilder();
        for (byte b : md.digest()) {
            hex.append(String.format("%02x", b));
        }
        return hex.toString();
    }
}
