package es.jarjar.romlibrary;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;

import androidx.documentfile.provider.DocumentFile;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.InputStream;
import java.security.MessageDigest;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Actualiza los ROMs instalados cuando la librería tiene una versión más
 * nueva (ver versiones.py / federacion.versiones_para_app en el servicio).
 * Hay dos cruces, por orden de prioridad:
 *
 *   A) POR SHA (identidad exacta): la librería manda las versiones SUPERADAS
 *      (sha + tamaño) de cada juego y su versión actual. Solo se calcula el SHA
 *      de los ficheros de la consola cuyo tamaño coincide con el de alguna
 *      versión superada (hacerlo de todo, con ISOs de varios GB, sería lento).
 *
 *   B) POR NOMBRE (convención): para los ficheros que el SHA no casa —versiones
 *      viejas que la librería nunca registró, p.ej. un «Pokémon Team Rocket»
 *      antiguo—, se cruza por NOMBRE normalizado (sin tildes, sin (región)/[tag],
 *      sin extensión). Si coincide con un juego de la librería y su contenido es
 *      distinto al actual, se pasa a la versión de la librería. El fichero nuevo
 *      conserva el BASENAME de la consola con la extensión de la librería
 *      (p.ej. «Team Rocket.gba» → «Team Rocket.zip»): las partidas de RetroArch
 *      van por el basename, así que se siguen encontrando.
 *
 * En ambos casos: se suben antes las partidas, se baja la versión actual a un
 * temporal y se comprueba su SHA; el original solo se borra cuando la nueva
 * está entera y verificada. Solo juegos de un fichero (los de varios, aún no).
 */
final class ActualizadorRoms {

    private ActualizadorRoms() {
    }

    private static final String PREFS_HASHES = "hashes_roms";

    /** Una versión superada de un juego de la librería (cruce por SHA). */
    private static final class Obsoleta {
        String sistema, sha, filenameActual, shaActual;
        String origenUrl;       // la versión actual está en otra librería (de confianza): se baja de allí
        String fuenteExterna;   // o en la web de su autor (se prueba primero)
        long tamano;
        int n, nActual;
    }

    /** La versión actual de un juego, indexada por nombre normalizado (cruce por nombre). */
    private static final class PorNombre {
        String filename, sha, origenUrl, fuenteExterna;
        long tamano;
    }

    private static final class Datos {
        List<Obsoleta> obsoletas = new ArrayList<>();
        // sistema -> nombre normalizado -> versión actual (null = nombre ambiguo, no tocar)
        Map<String, Map<String, PorNombre>> porNombre = new HashMap<>();
    }

    private static final class Tarea {
        DocumentFile carpeta, fichero;
        Obsoleta o;
        String nombreDestino;   // nombre final del fichero (basename consola + extensión librería)
        boolean porNombre;      // true = cruzado por nombre (versión vieja desconocida)
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
            Datos datos = leerDatos(baseUrl, token);
            if (datos.obsoletas.isEmpty() && datos.porNombre.isEmpty()) {
                return 0;
            }
            DocumentFile raiz = DocumentFile.fromTreeUri(ctx, arbolUri);
            if (raiz == null) {
                return 0;
            }
            List<Tarea> tareas = buscar(ctx, raiz, datos);
            int hechas = 0;
            for (Tarea t : tareas) {
                String nombre = t.fichero.getName();
                aviso.mostrar("Actualizando " + nombre + "…");
                if (actualizar(ctx, baseUrl, token, t)) {
                    hechas++;
                    aviso.mostrar("Actualizado: " + nombre
                            + (t.porNombre ? " (a la versión de la librería)" : " (v" + t.o.n + " → v" + t.o.nActual + ")"));
                }
            }
            return hechas;
        } catch (Exception e) {
            GuardadosSync.log("actualizador de ROMs: " + e);
            return 0;
        }
    }

    /**
     * Hiper-ligero: cuántos ROMs INSTALADOS tienen una versión más nueva en la
     * librería, SIN descargar nada (solo las listas y el SHA de los ficheros
     * candidatos, con la caché de hashes). Lo usa el worker de fondo para
     * decidir si avisar (ver ComprobadorActualizaciones).
     */
    static int pendientes(Context ctx, String baseUrl, String token, Uri arbolUri) {
        if (arbolUri == null) {
            return 0;
        }
        try {
            Datos datos = leerDatos(baseUrl, token);
            if (datos.obsoletas.isEmpty() && datos.porNombre.isEmpty()) {
                return 0;
            }
            DocumentFile raiz = DocumentFile.fromTreeUri(ctx, arbolUri);
            if (raiz == null) {
                return 0;
            }
            return buscar(ctx, raiz, datos).size();
        } catch (Exception e) {
            GuardadosSync.log("comprobador de actualizaciones (fondo): " + e);
            return 0;
        }
    }

    private static Datos leerDatos(String baseUrl, String token) throws Exception {
        JSONObject raiz = new JSONObject(
                ApiClient.getTexto(baseUrl, token, Cuenta.RUTA_API + "?accion=versiones", 15000));
        Datos datos = new Datos();
        JSONArray arr = raiz.optJSONArray("obsoletas");
        for (int i = 0; arr != null && i < arr.length(); i++) {
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
            o.origenUrl = urlValida(a, "origenUrl");
            o.fuenteExterna = urlValida(a, "fuenteExterna");
            // Nombres que acaban en disco: los mismos filtros que al instalar.
            if (NombresSeguros.componente(o.sistema) && NombresSeguros.componente(o.filenameActual)) {
                datos.obsoletas.add(o);
            }
        }
        // porNombre: se agrupa por sistema + nombre normalizado. Si un nombre sale
        // más de una vez (ambiguo) se guarda null y no se toca; se prefieren los
        // juegos propios (origenUrl nulo) frente a los de otra librería.
        Map<String, Map<String, List<PorNombre>>> tmp = new HashMap<>();
        JSONArray pn = raiz.optJSONArray("porNombre");
        for (int i = 0; pn != null && i < pn.length(); i++) {
            JSONObject j = pn.getJSONObject(i);
            String sistema = j.getString("sistema");
            String filename = j.getString("nombre");
            if (!NombresSeguros.componente(sistema) || !NombresSeguros.componente(filename)) {
                continue;
            }
            PorNombre e = new PorNombre();
            e.filename = filename;
            e.sha = j.getString("sha").toLowerCase();
            e.tamano = j.getLong("tamano");
            e.origenUrl = urlValida(j, "origenUrl");
            e.fuenteExterna = urlValida(j, "fuenteExterna");
            tmp.computeIfAbsent(sistema, k -> new HashMap<>())
                    .computeIfAbsent(normalizar(filename), k -> new ArrayList<>()).add(e);
        }
        for (Map.Entry<String, Map<String, List<PorNombre>>> s : tmp.entrySet()) {
            Map<String, PorNombre> resuelto = new HashMap<>();
            for (Map.Entry<String, List<PorNombre>> n : s.getValue().entrySet()) {
                List<PorNombre> todos = n.getValue();
                List<PorNombre> propios = new ArrayList<>();
                for (PorNombre e : todos) {
                    if (e.origenUrl == null) {
                        propios.add(e);
                    }
                }
                List<PorNombre> elegidos = propios.isEmpty() ? todos : propios;
                resuelto.put(n.getKey(), elegidos.size() == 1 ? elegidos.get(0) : null);
            }
            datos.porNombre.put(s.getKey(), resuelto);
        }
        return datos;
    }

    private static String urlValida(JSONObject j, String campo) {
        String v = j.isNull(campo) ? "" : j.optString(campo, "");
        return v.startsWith("https://") && !v.contains("@") ? v : null;
    }

    private static List<Tarea> buscar(Context ctx, DocumentFile raiz, Datos datos) {
        // Cruce por SHA, por sistema y tamaño.
        Map<String, Map<Long, List<Obsoleta>>> porSistema = new HashMap<>();
        for (Obsoleta o : datos.obsoletas) {
            porSistema.computeIfAbsent(o.sistema, k -> new HashMap<>())
                    .computeIfAbsent(o.tamano, k -> new ArrayList<>()).add(o);
        }
        Set<String> sistemas = new HashSet<>(porSistema.keySet());
        sistemas.addAll(datos.porNombre.keySet());

        List<Tarea> tareas = new ArrayList<>();
        for (String sistema : sistemas) {
            DocumentFile carpeta = raiz.findFile(sistema);
            if (carpeta == null || !carpeta.isDirectory()) {
                continue;
            }
            Map<Long, List<Obsoleta>> porTamano = porSistema.get(sistema);
            Map<String, PorNombre> porNombre = datos.porNombre.get(sistema);
            Set<String> yaTratados = new HashSet<>();     // uris cogidas por SHA
            DocumentFile[] ficheros = carpeta.listFiles();

            // A) por SHA
            if (porTamano != null) {
                for (DocumentFile f : ficheros) {
                    if (!esRom(f)) {
                        continue;
                    }
                    List<Obsoleta> candidatas = porTamano.get(f.length());
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
                            t.nombreDestino = f.getName();   // misma versión seguida: mismo nombre
                            tareas.add(t);
                            yaTratados.add(f.getUri().toString());
                            break;
                        }
                    }
                }
            }

            // B) por NOMBRE (solo los que el SHA no cogió)
            if (porNombre != null) {
                for (DocumentFile f : ficheros) {
                    if (!esRom(f) || yaTratados.contains(f.getUri().toString())) {
                        continue;
                    }
                    PorNombre e = porNombre.get(normalizar(f.getName()));
                    if (e == null) {                          // sin coincidencia o nombre ambiguo
                        continue;
                    }
                    // Si el tamaño coincide con el de la versión actual lo damos por
                    // actual (no hasheamos: evita recorrer ISOs enteras en cada arranque).
                    // El cruce por nombre actúa cuando el contenido difiere en tamaño,
                    // que es el caso de una versión vieja distinta.
                    if (f.length() == e.tamano) {
                        continue;
                    }
                    String destino = baseName(f.getName()) + extension(e.filename);
                    // No pisar otro fichero que ya exista con ese nombre.
                    if (!destino.equals(f.getName()) && carpeta.findFile(destino) != null) {
                        continue;
                    }
                    Obsoleta o = new Obsoleta();
                    o.sistema = sistema;
                    o.filenameActual = e.filename;
                    o.shaActual = e.sha;
                    o.tamano = f.length();
                    o.origenUrl = e.origenUrl;
                    o.fuenteExterna = e.fuenteExterna;
                    Tarea t = new Tarea();
                    t.carpeta = carpeta;
                    t.fichero = f;
                    t.o = o;
                    t.nombreDestino = destino;
                    t.porNombre = true;
                    tareas.add(t);
                }
            }
        }
        return tareas;
    }

    private static boolean esRom(DocumentFile f) {
        String n = f.getName();
        return f.isFile() && n != null && !n.endsWith(".actualizando") && !n.endsWith(".anterior");
    }

    private static boolean actualizar(Context ctx, String baseUrl, String token, Tarea t) {
        String nombre = t.fichero.getName();
        String destino = t.nombreDestino != null ? t.nombreDestino : nombre;
        DocumentFile temporal = null;
        try {
            // 1. Partidas a salvo en el servidor antes de tocar nada. Van por el
            //    basename del ROM, que conservamos, así que siguen asociadas.
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

            // 3. Cambio: el original se aparta, el nuevo toma su nombre destino y
            //    solo entonces se borra el original (si algo falla, se deshace).
            String apartado = nombre + ".anterior";
            if (!t.fichero.renameTo(apartado)) {
                throw new IllegalStateException("no se pudo apartar el original");
            }
            if (!temporal.renameTo(destino)) {
                t.fichero.renameTo(nombre);
                throw new IllegalStateException("no se pudo renombrar la versión nueva");
            }
            temporal = null;
            t.fichero.delete();
            DocumentFile nuevo = t.carpeta.findFile(destino);
            if (nuevo != null) {
                guardarCache(ctx, nuevo, sha);
            }
            GuardadosSync.log("ROM actualizado: " + t.o.sistema + "/" + nombre
                    + (t.porNombre ? " -> " + destino + " (por nombre)" : " v" + t.o.n + " -> v" + t.o.nActual));
            return true;
        } catch (Exception e) {
            GuardadosSync.log("ERROR actualizando " + t.o.sistema + "/" + nombre + ": " + e);
            if (temporal != null) {
                temporal.delete();
            }
            return false;
        }
    }

    // ---------- nombres ----------

    /** Nombre normalizado para el cruce por nombre: sin extensión, sin tildes,
     * sin (región)/[tag], separadores colapsados, en minúsculas. */
    private static String normalizar(String fichero) {
        String b = baseName(fichero);
        b = Normalizer.normalize(b, Normalizer.Form.NFKD).replaceAll("\\p{M}+", "");
        b = b.replaceAll("[\\(\\[].*?[\\)\\]]", " ");
        b = b.replaceAll("[\\s_\\-:!.]+", " ").trim().toLowerCase();
        return b;
    }

    private static String baseName(String fichero) {
        int p = fichero.lastIndexOf('.');
        return p > 0 ? fichero.substring(0, p) : fichero;
    }

    private static String extension(String fichero) {
        int p = fichero.lastIndexOf('.');
        return p > 0 ? fichero.substring(p) : "";
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
