package es.jarjar.romlibrary;

import android.content.Context;
import android.os.Build;
import android.os.Environment;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Sincroniza lo que GameNative tiene instalado con el sistema <b>steam</b> de ES-DE.
 *
 * <p>GameNative ({@code app.gamenative}) guarda su biblioteca en una Room DB privada
 * ilegible sin root, pero instala los juegos en la SD pública en
 * {@code <sd>/GameNative/Steam/steamapps/common/<installdir>/} (una carpeta por juego).
 * Esa es la fuente de verdad de lo instalado. ES-DE muestra cada juego como un fichero
 * {@code <Nombre>.steam} cuyo CONTENIDO es el appid de Steam, y lo lanza por appid.
 *
 * <p>Al abrir la app (con {@code MANAGE_EXTERNAL_STORAGE}, el mismo permiso de las
 * carátulas) esta clase reconcilia, SIN privilegios, el sistema {@code steam}:
 * <ol>
 *   <li>Lista {@code common/} (filtra runtimes: «Steamworks Common Redistributables»,
 *       «Proton*», «Steam Linux Runtime*»…); cada carpeta completa = un instalado.</li>
 *   <li>Mapea installdir→appid: (1) {@code steam_appid.txt} en la carpeta; (2) los
 *       {@code .steam} ya existentes en ES-DE (nombre→appid); (3) caché en disco privado;
 *       (4) API pública de Steam {@code storesearch}. NUNCA inventa un appid.</li>
 *   <li>Crea los {@code .steam} que falten (contenido=appid) + su entrada en
 *       {@code gamelists/steam/gamelist.xml} (conservando el bloque entero —playcount/
 *       playtime/lastplayed y la metadata que ponga el scraper— casando por appid);
 *       BORRA del frontend ({@code .steam}/{@code .steamg} + entrada + media) lo que ya
 *       no esté instalado. No toca covers de juegos que siguen instalados.</li>
 *   <li>Si hubo cambios, deja la bandera de reinicio de ES-DE ({@link RefrescoEsDe}).</li>
 * </ol>
 *
 * <p>Todo best-effort: si GameNative o ES-DE no están, o algo falla, no hace nada y no
 * lanza. Solo toca el sistema {@code steam} y la bandera. Reproducible en cualquier
 * consola (lo hace la app en runtime; para la Nova se hacía antes a mano vía adb).
 */
final class SincroniaGameNative {

    private static final String TAG = "RomLibrary";

    /** Carpetas de {@code common/} que NO son juegos (runtimes de Steam/Proton). */
    private static final String[] RUNTIME_EXACTO = {
            "steamworks common redistributables",
            "steamworks shared",
    };
    private static final String[] RUNTIME_PREFIJO = {
            "proton", "steam linux runtime", "steamvr", "steam controller configs",
    };

    /** Subcarpetas de media de ES-DE donde puede haber ficheros de un juego borrado. */
    private static final String[] TIPOS_MEDIA = {
            "covers", "marquees", "screenshots", "titlescreens", "miximages",
            "physicalmedia", "3dboxes", "backcovers", "fanart", "videos", "manuals",
    };
    private static final String[] EXT_MEDIA = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".pdf"};

    private static final String CACHE_APPID = "gamenative_appid.cache";

    private SincroniaGameNative() {
    }

    /**
     * Reconcilia el sistema {@code steam} de ES-DE con lo instalado en GameNative.
     * Bloqueante: llamar desde un hilo de fondo. Devuelve cuántos juegos se añadieron o
     * quitaron (0 si no había nada que hacer, GameNative/ES-DE no están, o falta permiso).
     */
    static int sincronizar(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R || !Environment.isExternalStorageManager()) {
            return 0;
        }
        try {
            File common = descubrirCommonGameNative();
            if (common == null) {
                return 0; // GameNative no está instalado / sin juegos en la SD
            }
            File esDeRoot = new File(Environment.getExternalStorageDirectory(), "ES-DE");
            if (!esDeRoot.isDirectory()) {
                return 0; // ES-DE no está
            }
            File steamRoms = descubrirRomsSteam(esDeRoot);
            File gamelist = new File(esDeRoot, "gamelists/steam/gamelist.xml");

            // 1) Instalado en GameNative: installdir de cada carpeta completa (sin runtimes).
            List<String> instalados = listarInstalados(common);
            if (instalados.isEmpty()) {
                Log.i(TAG, "SincroniaGameNative: GameNative sin juegos completos instalados");
            }

            // 2) Lo que ES-DE ya tiene: fichero->appid (de .steam/.steamg existentes).
            Map<String, String> ficheroAAppid = new LinkedHashMap<>();
            Map<String, String> appidAFichero = new LinkedHashMap<>(); // appid -> fichero .steam preferido
            Map<String, String> normAAppid = new LinkedHashMap<>();     // nombre normalizado -> appid
            escanearExistentes(steamRoms, ficheroAAppid, appidAFichero, normAAppid);

            // 3) Resolver appid de cada instalado y construir el conjunto deseado (por appid).
            Properties cache = cargarCache(context);
            Map<String, Juego> deseados = new LinkedHashMap<>();
            List<String> sinAppid = new ArrayList<>();
            boolean cacheModificada = false;
            for (String installdir : instalados) {
                String[] res = resolverAppid(new File(common, installdir), installdir, normAAppid, cache);
                if (res == null || res[0] == null) {
                    sinAppid.add(installdir);
                    continue;
                }
                String appid = res[0];
                String nombreCanonico = res[1];
                if (nombreCanonico != null && !appid.equals(cache.getProperty(installdir, "").split("\\|", 2)[0])) {
                    cache.setProperty(installdir, appid + "|" + nombreCanonico);
                    cacheModificada = true;
                } else if (cache.getProperty(installdir) == null) {
                    cache.setProperty(installdir, appid + "|" + (nombreCanonico == null ? "" : nombreCanonico));
                    cacheModificada = true;
                }
                if (deseados.containsKey(appid)) {
                    continue; // dos carpetas con el mismo appid: una basta
                }
                String ficheroExistente = appidAFichero.get(appid);
                String stem, nombre;
                if (ficheroExistente != null) {
                    stem = sinExtension(ficheroExistente); // conserva el nombre (y por tanto la cover) del usuario
                    nombre = nombreCanonico != null ? nombreCanonico : stem;
                } else {
                    stem = sanitizarNombreFichero(installdir); // installdir ya viene seguro para FAT
                    nombre = nombreCanonico != null ? nombreCanonico : installdir;
                }
                deseados.put(appid, new Juego(appid, stem, nombre, ficheroExistente));
            }
            if (cacheModificada) {
                guardarCache(context, cache);
            }
            if (!sinAppid.isEmpty()) {
                Log.w(TAG, "SincroniaGameNative: sin appid (no se exportan): " + sinAppid);
            }

            // 4) Aplicar: crear los que falten, borrar los que sobren, reescribir gamelist.
            int anadidos = 0, borrados = 0;

            // 4a) Borrar ficheros .steam/.steamg que no correspondan a un instalado (o .steamg sueltos).
            File[] ficheros = steamRoms.isDirectory() ? steamRoms.listFiles() : null;
            if (ficheros != null) {
                for (File f : ficheros) {
                    if (!f.isFile()) {
                        continue;
                    }
                    String n = f.getName();
                    boolean esSteam = n.toLowerCase(Locale.ROOT).endsWith(".steam");
                    boolean esSteamG = n.toLowerCase(Locale.ROOT).endsWith(".steamg");
                    if (!esSteam && !esSteamG) {
                        continue; // Games/, systeminfo.txt, *.png del sistema: no se tocan
                    }
                    String appid = ficheroAAppid.get(n);
                    boolean instalado = appid != null && deseados.containsKey(appid);
                    boolean esElCanonico = esSteam && instalado
                            && (sinExtension(n)).equals(deseados.get(appid).stem);
                    if (esElCanonico) {
                        continue; // es el fichero que conservamos para este juego
                    }
                    // Fichero a eliminar: juego desinstalado, duplicado, o .steamg (extensión muerta).
                    if (f.delete()) {
                        if (!instalado) {
                            borrarMedia(esDeRoot, sinExtension(n));
                            borrados++;
                        }
                        Log.i(TAG, "SincroniaGameNative: eliminado " + n + (instalado ? " (duplicado)" : " (desinstalado)"));
                    }
                }
            }

            // 4b) Crear los .steam que falten (contenido = appid).
            for (Juego j : deseados.values()) {
                if (j.ficheroExistente != null) {
                    continue;
                }
                if (!steamRoms.isDirectory() && !steamRoms.mkdirs()) {
                    Log.w(TAG, "SincroniaGameNative: no se pudo crear " + steamRoms);
                    break;
                }
                File destino = new File(steamRoms, j.stem + ".steam");
                if (destino.exists()) {
                    // Colisión de nombre con otro juego: desambiguar con el appid.
                    destino = new File(steamRoms, j.stem + " (" + j.appid + ").steam");
                    j.stem = j.stem + " (" + j.appid + ")";
                }
                if (escribirTexto(destino, j.appid)) {
                    anadidos++;
                    Log.i(TAG, "SincroniaGameNative: añadido " + destino.getName() + " -> appid " + j.appid);
                }
            }

            // 4c) Reescribir el gamelist conservando los bloques existentes (y su metadata).
            boolean gamelistCambio = reescribirGamelist(gamelist, deseados, ficheroAAppid);

            boolean huboCambios = anadidos > 0 || borrados > 0 || gamelistCambio;
            if (huboCambios) {
                String ruta = RefrescoEsDe.marcarAutomatico(context);
                Log.i(TAG, "SincroniaGameNative: " + anadidos + " añadidos, " + borrados
                        + " borrados, gamelist " + (gamelistCambio ? "actualizado" : "sin cambios")
                        + (ruta != null ? "; bandera de reinicio dejada" : ""));
            }
            return anadidos + borrados;
        } catch (Throwable t) {
            Log.w(TAG, "SincroniaGameNative: fallo (ignorado)", t);
            return 0;
        }
    }

    // ----------------------------------------------------------------- descubrimiento de rutas

    /** Raíces de almacenamiento montadas donde pueden vivir GameNative o las ROMs. */
    private static List<File> raicesAlmacenamiento() {
        List<File> raices = new ArrayList<>();
        raices.add(Environment.getExternalStorageDirectory()); // /storage/emulated/0 normalmente
        File storage = new File("/storage");
        File[] hijos = storage.listFiles();
        if (hijos != null) {
            for (File h : hijos) {
                String n = h.getName();
                if ("emulated".equals(n) || "self".equals(n) || "container".equals(n)) {
                    continue;
                }
                if (h.isDirectory()) {
                    raices.add(h); // microSD/USB: /storage/6564-3634, etc.
                }
            }
        }
        return raices;
    }

    /** Busca {@code GameNative/Steam/steamapps/common} bajo cualquier almacenamiento. */
    private static File descubrirCommonGameNative() {
        for (File raiz : raicesAlmacenamiento()) {
            File c = new File(raiz, "GameNative/Steam/steamapps/common");
            if (c.isDirectory()) {
                return c;
            }
        }
        return null;
    }

    /**
     * Carpeta {@code <ROMDirectory>/steam} de ES-DE. El root sale del ajuste
     * {@code ROMDirectory} de {@code es_settings.xml}; si no está, se prueban las ROMs
     * en cada almacenamiento y, como último recurso, {@code <ES-DE>/ROMs/steam}.
     */
    private static File descubrirRomsSteam(File esDeRoot) {
        String romDir = leerAjuste(esDeRoot, "ROMDirectory");
        if (romDir != null && !romDir.trim().isEmpty()) {
            romDir = romDir.trim()
                    .replace("%ESPATH%", esDeRoot.getAbsolutePath())
                    .replace("%ROMPATH%", esDeRoot.getAbsolutePath());
            return new File(romDir, "steam");
        }
        for (File raiz : raicesAlmacenamiento()) {
            File s = new File(raiz, "ROMs/steam");
            if (s.isDirectory()) {
                return s;
            }
        }
        return new File(esDeRoot, "ROMs/steam");
    }

    /** Lee el valor de un ajuste {@code <string name="X" value="Y"/>} de es_settings.xml. */
    private static String leerAjuste(File esDeRoot, String clave) {
        try {
            String xml = new String(java.nio.file.Files.readAllBytes(
                    new File(esDeRoot, "settings/es_settings.xml").toPath()), StandardCharsets.UTF_8);
            Matcher m = Pattern.compile(
                    "<string name=\"" + Pattern.quote(clave) + "\" value=\"([^\"]*)\"").matcher(xml);
            if (m.find()) {
                return m.group(1);
            }
        } catch (Exception ignored) {
            // sin es_settings.xml legible
        }
        return null;
    }

    // ----------------------------------------------------------------- lectura de estado

    /** installdir de cada carpeta completa de {@code common/}, sin runtimes. */
    private static List<String> listarInstalados(File common) {
        List<String> res = new ArrayList<>();
        File[] carpetas = common.listFiles();
        if (carpetas == null) {
            return res;
        }
        // Solo exigir .download_complete si GameNative lo usa (alguna carpeta lo tiene).
        boolean usaMarca = false;
        for (File c : carpetas) {
            if (c.isDirectory() && new File(c, ".download_complete").exists()) {
                usaMarca = true;
                break;
            }
        }
        for (File c : carpetas) {
            if (!c.isDirectory()) {
                continue;
            }
            String nombre = c.getName();
            if (nombre.startsWith(".") || esRuntime(nombre)) {
                continue;
            }
            if (usaMarca && !new File(c, ".download_complete").exists()) {
                Log.i(TAG, "SincroniaGameNative: instalación incompleta, se ignora: " + nombre);
                continue;
            }
            res.add(nombre);
        }
        return res;
    }

    private static boolean esRuntime(String nombre) {
        String n = nombre.toLowerCase(Locale.ROOT);
        for (String ex : RUNTIME_EXACTO) {
            if (n.equals(ex)) {
                return true;
            }
        }
        for (String pre : RUNTIME_PREFIJO) {
            if (n.startsWith(pre)) {
                return true;
            }
        }
        return false;
    }

    /** Rellena los mapas fichero->appid, appid->fichero (.steam preferido) y nombreNorm->appid. */
    private static void escanearExistentes(File steamRoms, Map<String, String> ficheroAAppid,
                                           Map<String, String> appidAFichero, Map<String, String> normAAppid) {
        File[] ficheros = steamRoms.isDirectory() ? steamRoms.listFiles() : null;
        if (ficheros == null) {
            return;
        }
        for (File f : ficheros) {
            if (!f.isFile()) {
                continue;
            }
            String n = f.getName();
            String bajo = n.toLowerCase(Locale.ROOT);
            boolean esSteam = bajo.endsWith(".steam");
            boolean esSteamG = bajo.endsWith(".steamg");
            if (!esSteam && !esSteamG) {
                continue;
            }
            String appid = leerTexto(f);
            if (appid == null || !appid.matches("\\d{1,10}")) {
                continue;
            }
            ficheroAAppid.put(n, appid);
            String stem = sinExtension(n);
            normAAppid.put(normalizar(stem), appid);
            // Canónico SOLO un .steam (extensión válida): un juego que solo exista como
            // .steamg (extensión muerta) se trata como nuevo y se recrea como .steam.
            if (esSteam && !appidAFichero.containsKey(appid)) {
                appidAFichero.put(appid, n);
            }
        }
    }

    // ----------------------------------------------------------------- mapeo installdir -> appid

    /** @return {appid, nombreCanonico(null si no vino de la API)} o null si no se pudo resolver. */
    private static String[] resolverAppid(File carpeta, String installdir,
                                          Map<String, String> normAAppid, Properties cache) {
        // (1) steam_appid.txt dentro de la carpeta del juego.
        String porFichero = leerTexto(new File(carpeta, "steam_appid.txt"));
        if (porFichero != null && porFichero.matches("\\d{1,10}")) {
            return new String[]{porFichero, null};
        }
        // (2) un .steam ya existente con el mismo nombre normalizado.
        String porNombre = normAAppid.get(normalizar(installdir));
        if (porNombre != null) {
            return new String[]{porNombre, null};
        }
        // (3) caché en disco privado de la app.
        String cacheado = cache.getProperty(installdir);
        if (cacheado != null) {
            String[] partes = cacheado.split("\\|", 2);
            if (partes[0].matches("\\d{1,10}")) {
                return new String[]{partes[0], partes.length > 1 && !partes[1].isEmpty() ? partes[1] : null};
            }
        }
        // (4) API pública de Steam (storesearch). Se cachea en el llamador.
        return buscarEnSteam(installdir);
    }

    /** {@code storesearch} de Steam: devuelve {appid, nombre} del primer resultado, o null. */
    private static String[] buscarEnSteam(String installdir) {
        try {
            String termino = installdir.replace('_', ' ').replaceAll("\\s+", " ").trim();
            String url = "https://store.steampowered.com/api/storesearch/?term="
                    + URLEncoder.encode(termino, "UTF-8") + "&cc=us&l=en";
            HttpURLConnection con = ResolucionDns.abrir(url);
            con.setConnectTimeout(10_000);
            con.setReadTimeout(20_000);
            if (con.getResponseCode() != 200) {
                con.disconnect();
                return null;
            }
            String cuerpo;
            try (InputStream in = con.getInputStream()) {
                cuerpo = leerTodo(in);
            } finally {
                con.disconnect();
            }
            JSONObject raiz = new JSONObject(cuerpo);
            JSONArray items = raiz.optJSONArray("items");
            if (items == null || items.length() == 0) {
                return null;
            }
            JSONObject primero = items.getJSONObject(0);
            long id = primero.optLong("id", 0);
            if (id <= 0) {
                return null;
            }
            String nombre = primero.optString("name", null);
            return new String[]{Long.toString(id), (nombre == null || nombre.isEmpty()) ? null : nombre};
        } catch (Exception e) {
            Log.w(TAG, "SincroniaGameNative: storesearch falló para " + installdir, e);
            return null;
        }
    }

    // ----------------------------------------------------------------- gamelist

    private static final Pattern BLOQUE_GAME = Pattern.compile("<game>.*?</game>", Pattern.DOTALL);
    private static final Pattern PATH_GAME = Pattern.compile("<path>\\s*\\.?/?([^<]*)</path>");

    /**
     * Reescribe gamelist.xml conservando el bloque entero de los juegos que siguen
     * (metadata incluida; se corrige solo el {@code <path>} si cambió el fichero),
     * quitando los desinstalados y añadiendo un bloque mínimo para los nuevos.
     * @return true si el contenido cambió.
     */
    private static boolean reescribirGamelist(File gamelist, Map<String, Juego> deseados,
                                              Map<String, String> ficheroAAppid) {
        String viejo = leerTexto(gamelist);
        if (viejo == null) {
            viejo = "";
        }
        StringBuilder bloques = new StringBuilder();
        java.util.Set<String> yaPuestos = new java.util.HashSet<>();
        Matcher m = BLOQUE_GAME.matcher(viejo);
        while (m.find()) {
            String bloque = m.group();
            Matcher pm = PATH_GAME.matcher(bloque);
            if (!pm.find()) {
                continue;
            }
            String fichero = desescaparXml(pm.group(1).trim());
            String appid = ficheroAAppid.get(fichero);
            if (appid == null || !deseados.containsKey(appid) || yaPuestos.contains(appid)) {
                continue; // desinstalado, huérfano o duplicado -> se descarta
            }
            Juego j = deseados.get(appid);
            String ficheroDeseado = j.stem + ".steam";
            if (!ficheroDeseado.equals(fichero)) {
                bloque = PATH_GAME.matcher(bloque).replaceFirst(
                        Matcher.quoteReplacement("<path>./" + escaparXml(ficheroDeseado) + "</path>"));
            }
            bloques.append(bloque);
            if (!bloque.endsWith("\n")) {
                bloques.append("\n");
            }
            yaPuestos.add(appid);
        }
        // Nuevos: bloque mínimo (EsDeIconos pondrá la carátula; el scraper, la metadata).
        for (Juego j : deseados.values()) {
            if (yaPuestos.contains(j.appid)) {
                continue;
            }
            bloques.append("\t<game>\n\t\t<path>./").append(escaparXml(j.stem)).append(".steam</path>\n")
                    .append("\t\t<name>").append(escaparXml(j.nombre)).append("</name>\n\t</game>\n");
            yaPuestos.add(j.appid);
        }
        String nuevo = "<?xml version=\"1.0\"?>\n<gameList>\n" + bloques + "</gameList>\n";
        if (normalizarFin(nuevo).equals(normalizarFin(viejo))) {
            return false;
        }
        File dir = gamelist.getParentFile();
        if (dir != null && !dir.isDirectory() && !dir.mkdirs()) {
            return false;
        }
        return escribirTexto(gamelist, nuevo);
    }

    // ----------------------------------------------------------------- media

    private static void borrarMedia(File esDeRoot, String stem) {
        for (String tipo : TIPOS_MEDIA) {
            File carpeta = EsDeIconos.carpetaMedia("steam", tipo);
            for (String ext : EXT_MEDIA) {
                File f = new File(carpeta, stem + ext);
                if (f.isFile() && f.delete()) {
                    Log.i(TAG, "SincroniaGameNative: media borrada " + f.getName());
                }
            }
        }
    }

    // ----------------------------------------------------------------- caché appid

    private static Properties cargarCache(Context context) {
        Properties p = new Properties();
        File f = new File(context.getFilesDir(), CACHE_APPID);
        if (f.isFile()) {
            try (InputStream in = new java.io.FileInputStream(f)) {
                p.load(in);
            } catch (Exception ignored) {
            }
        }
        return p;
    }

    private static void guardarCache(Context context, Properties p) {
        File f = new File(context.getFilesDir(), CACHE_APPID);
        try (OutputStream out = new FileOutputStream(f)) {
            p.store(out, "installdir=appid|nombre (SincroniaGameNative)");
        } catch (Exception e) {
            Log.w(TAG, "SincroniaGameNative: no se pudo guardar la caché de appid", e);
        }
    }

    // ----------------------------------------------------------------- utilidades

    private static final class Juego {
        final String appid;
        String stem;
        final String nombre;
        final String ficheroExistente; // nombre del .steam ya en disco, o null si es nuevo

        Juego(String appid, String stem, String nombre, String ficheroExistente) {
            this.appid = appid;
            this.stem = stem;
            this.nombre = nombre;
            this.ficheroExistente = ficheroExistente;
        }
    }

    private static String sinExtension(String nombre) {
        int p = nombre.lastIndexOf('.');
        return p > 0 ? nombre.substring(0, p) : nombre;
    }

    /** Normaliza para casar nombres: minúsculas, sin tildes, ':'→'_', espacios colapsados. */
    private static String normalizar(String s) {
        String base = java.text.Normalizer.normalize(s, java.text.Normalizer.Form.NFD)
                .replaceAll("\\p{M}+", "");
        return base.toLowerCase(Locale.ROOT).replace(':', '_').replaceAll("\\s+", " ").trim();
    }

    /** Nombre de fichero seguro para FAT/ES-DE (los installdir ya suelen venirlo). */
    private static String sanitizarNombreFichero(String nombre) {
        String s = nombre.replace(':', '_').replaceAll("[\\\\/*?\"<>|]", "_").replaceAll("\\s+", " ").trim();
        while (s.endsWith(".") || s.endsWith(" ")) {
            s = s.substring(0, s.length() - 1);
        }
        return s.isEmpty() ? "juego" : s;
    }

    private static String escaparXml(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\"", "&quot;").replace("'", "&apos;");
    }

    private static String desescaparXml(String s) {
        return s.replace("&apos;", "'").replace("&quot;", "\"").replace("&gt;", ">")
                .replace("&lt;", "<").replace("&amp;", "&");
    }

    private static String normalizarFin(String s) {
        return s.replace("\r\n", "\n").trim();
    }

    private static String leerTexto(File f) {
        if (f == null || !f.isFile()) {
            return null;
        }
        try (InputStream in = new java.io.FileInputStream(f)) {
            String t = leerTodo(in).trim();
            return t.isEmpty() ? null : t;
        } catch (Exception e) {
            return null;
        }
    }

    private static String leerTodo(InputStream in) throws Exception {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] trozo = new byte[64 * 1024];
        int n;
        while ((n = in.read(trozo)) != -1) {
            buffer.write(trozo, 0, n);
            if (buffer.size() > 8 * 1024 * 1024) {
                break; // salvaguarda: un gamelist/appid nunca es tan grande
            }
        }
        return buffer.toString(StandardCharsets.UTF_8.name());
    }

    /** Escribe a un temporal y renombra: ES-DE nunca ve un fichero a medias. */
    private static boolean escribirTexto(File destino, String contenido) {
        File carpeta = destino.getParentFile();
        if (carpeta != null && !carpeta.isDirectory() && !carpeta.mkdirs()) {
            return false;
        }
        File temporal = new File(carpeta, destino.getName() + ".tmp");
        try (OutputStream out = new FileOutputStream(temporal)) {
            out.write(contenido.getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            temporal.delete();
            return false;
        }
        if (destino.exists() && !destino.delete()) {
            temporal.delete();
            return false;
        }
        return temporal.renameTo(destino);
    }
}
