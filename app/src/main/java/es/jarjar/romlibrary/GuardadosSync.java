package es.jarjar.romlibrary;

import android.content.Context;
import android.net.Uri;
import android.os.Environment;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.FileWriter;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

/**
 * Sincronización de guardados (partidas y save states) con el servidor.
 *
 * Los guardados no son de la consola sino de su grupo de sincronización
 * (ver "Sincronizar consolas"): todas las consolas de un grupo comparten la
 * misma carpeta en el servidor. Cada guardado es una "entrada" (un fichero
 * o una carpeta entera que viaja como zip) identificada por sistema + clave
 * (nombre del ROM sin extensión, o "_compartido" si el emulador no separa
 * por juego) + nombre. En cada sincronización, para cada entrada gana
 * SIEMPRE la copia más reciente: si la local es más nueva se sube, si la del
 * servidor es más nueva se baja. Solo se tocan los juegos instalados.
 *
 * Al bajar se restaura también la fecha de modificación original: si no,
 * el fichero recién bajado parecería "más nuevo" que el del servidor y se
 * volvería a subir en la siguiente apertura, en bucle entre consolas.
 *
 * Rutas reales de cada emulador, comprobadas en las Retroid por SSH:
 * - RetroArch: RetroArch/{saves,states}/<core>/<rom>.srm|.state*
 *   (+ la memory card 2 compartida de PCSX ReARMed: pcsx-card2.mcd).
 * - ARMSX2 (PS2): memcards/mcd00X.ps2 (compartidas) y sstates/ (states).
 * - Eden (Switch): nand/user/save (no separa por juego; Eden no tiene states).
 * - Dolphin (GameCube): GC/ (memory cards) y StateSaves/.
 * - PPSSPP (PSP vía RetroArch): PSP/SAVEDATA (carpeta).
 * - Kirin (RPG Maker): Kirin/Saved Games/<título> (título según game-library.json).
 *
 * Best-effort: un fallo aquí nunca impide instalar ni desinstalar un juego,
 * pero ya no se traga en silencio: todo queda en
 * RomLibraryDebug/guardados_log.txt.
 */
final class GuardadosSync {

    private static final String COMPARTIDO = "_compartido";
    private static final long TOLERANCIA_MS = 2000; // los zips guardan la hora con 2 s de precisión
    private static final File RAIZ = Environment.getExternalStorageDirectory();
    private static final File RETROARCH = new File(RAIZ, "RetroArch");
    private static final File DATOS_APPS = new File(RAIZ, "Android/data");
    private static final File FICHERO_LOG = new File(RAIZ, "RomLibraryDebug/guardados_log.txt");

    private GuardadosSync() {
    }

    /** Un guardado concreto: fichero suelto o carpeta (viaja como zip). */
    static final class Entrada {
        final String sistema;
        final String clave;
        final String nombre;
        final File local;
        final boolean carpeta;

        Entrada(String sistema, String clave, String nombre, File local, boolean carpeta) {
            this.sistema = sistema;
            this.clave = clave;
            this.nombre = nombre;
            this.local = local;
            this.carpeta = carpeta;
        }

        String id() {
            return sistema + "/" + clave + "/" + nombre;
        }

        /** Fecha del guardado local: la del fichero, o la del más reciente de la carpeta. 0 = no hay. */
        long mtimeLocal() {
            if (carpeta) {
                return mtimeMasReciente(local);
            }
            return local.isFile() ? local.lastModified() : 0;
        }
    }

    // ---------- Puntos de entrada ----------

    /** Sincroniza (subir y bajar, gana lo más reciente) todos los juegos instalados. Bloqueante. */
    static void sincronizarInstalados(Context context, Cuenta cuenta, List<Juego> instalados) {
        if (!cuenta.registrada()) {
            return;
        }
        try {
            Map<String, Double> remotos = indiceRemoto(cuenta);
            Map<String, Entrada> entradas = new LinkedHashMap<>();
            for (Juego juego : instalados) {
                for (Entrada e : entradasDe(juego, remotos)) {
                    entradas.put(e.id(), e); // las compartidas de un sistema solo una vez
                }
            }
            int subidos = 0;
            int bajados = 0;
            for (Entrada e : entradas.values()) {
                try {
                    int r = sincronizarEntrada(context, cuenta, e, remotos.get(e.id()));
                    if (r > 0) {
                        subidos++;
                    } else if (r < 0) {
                        bajados++;
                    }
                } catch (Exception ex) {
                    log("ERROR sincronizando " + e.id() + ": " + ex);
                }
            }
            if (subidos + bajados > 0) {
                log("sincronización: " + subidos + " subidos, " + bajados + " bajados");
            }
        } catch (Exception e) {
            log("ERROR en la sincronización: " + e);
        }
    }

    /** Antes de borrar un juego: sube sus guardados si los locales son más nuevos. */
    static void subirAntesDeDesinstalar(Context context, Cuenta cuenta, Juego juego) {
        sincronizarInstalados(context, cuenta, java.util.Collections.singletonList(juego));
    }

    /** Recién instalado un juego: baja sus guardados del servidor (si los hay). */
    static void restaurarTrasInstalar(Context context, Cuenta cuenta, Juego juego) {
        sincronizarInstalados(context, cuenta, java.util.Collections.singletonList(juego));
    }

    // ---------- Comparación y transferencia ----------

    /** +1 si se ha subido, -1 si se ha bajado, 0 si ya estaban iguales. */
    private static int sincronizarEntrada(Context context, Cuenta cuenta, Entrada e, Double mtimeRemotoSeg)
            throws Exception {
        long local = e.mtimeLocal();
        long remoto = mtimeRemotoSeg == null ? 0 : Math.round(mtimeRemotoSeg * 1000);
        if (local > 0 && local > remoto + TOLERANCIA_MS) {
            subir(context, cuenta, e, local);
            log("subido " + e.id());
            return 1;
        }
        if (remoto > 0 && remoto > local + TOLERANCIA_MS) {
            bajar(context, cuenta, e, remoto);
            log("bajado " + e.id());
            return -1;
        }
        return 0;
    }

    private static Map<String, Double> indiceRemoto(Cuenta cuenta) throws Exception {
        String json = ApiClient.getTexto(cuenta.url, cuenta.clave,
                Cuenta.RUTA_API + "?accion=guardado2_indice" + cuenta.consultaIdentidad(), 20000);
        JSONArray arr = new JSONArray(json);
        Map<String, Double> remotos = new HashMap<>();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.getJSONObject(i);
            remotos.put(o.getString("sistema") + "/" + o.getString("clave") + "/" + o.getString("nombre_fichero"),
                    o.getDouble("mtime"));
        }
        return remotos;
    }

    private static void subir(Context context, Cuenta cuenta, Entrada e, long mtimeLocal) throws Exception {
        Map<String, String> campos = cuenta.identidad();
        campos.put("sistema", e.sistema);
        campos.put("clave", e.clave);
        campos.put("nombre_fichero", e.nombre);
        campos.put("mtime_dispositivo", String.valueOf(mtimeLocal / 1000.0));
        if (!e.carpeta) {
            ApiClient.subirFichero(cuenta.url, cuenta.clave, Cuenta.RUTA_API + "?accion=guardado2_subir", campos, e.nombre, e.local);
            return;
        }
        File zip = File.createTempFile("guardado", ".zip", context.getCacheDir());
        try {
            comprimirCarpeta(e.local, zip);
            ApiClient.subirFichero(cuenta.url, cuenta.clave, Cuenta.RUTA_API + "?accion=guardado2_subir", campos, e.nombre, zip);
        } finally {
            zip.delete();
        }
    }

    private static void bajar(Context context, Cuenta cuenta, Entrada e, long mtimeRemoto) throws Exception {
        File temporal = File.createTempFile("guardado", null, context.getCacheDir());
        try {
            try (OutputStream out = new FileOutputStream(temporal)) {
                ApiClient.descargarAFichero(cuenta.url, cuenta.clave,
                        Cuenta.RUTA_API + "?accion=guardado2_bajar" + cuenta.consultaIdentidad()
                                + "&sistema=" + Uri.encode(e.sistema) + "&clave=" + Uri.encode(e.clave)
                                + "&nombre_fichero=" + Uri.encode(e.nombre),
                        out);
            }
            if (e.carpeta) {
                descomprimirSobreCarpeta(temporal, e.local);
            } else {
                File padre = e.local.getParentFile();
                if (padre != null && !padre.isDirectory() && !padre.mkdirs()) {
                    throw new IOException("No se pudo crear " + padre);
                }
                copiarFichero(temporal, e.local);
                // Misma fecha que en el servidor (ver cabecera de la clase).
                e.local.setLastModified(mtimeRemoto);
            }
        } finally {
            temporal.delete();
        }
    }

    // ---------- Qué guardados tiene cada juego ----------

    /**
     * Entradas de un juego instalado: las que existen en la consola y las que
     * solo existen en el servidor (para poder bajarlas).
     */
    static List<Entrada> entradasDe(Juego juego, Map<String, Double> remotos) {
        List<Entrada> lista = new ArrayList<>();
        String s = juego.sistema;
        String stem = quitarExtension(juego.nombreFichero);
        switch (s) {
            case "switch":
                lista.add(new Entrada(s, COMPARTIDO, "nand_user_save.zip",
                        new File(DATOS_APPS, "dev.eden.eden_emulator/files/nand/user/save"), true));
                break;
            case "ps2": {
                File armsx2 = new File(DATOS_APPS, "come.nanodata.armsx2/files");
                for (String mc : new String[]{"mcd001.ps2", "mcd002.ps2"}) {
                    lista.add(new Entrada(s, COMPARTIDO, mc, new File(armsx2, "memcards/" + mc), false));
                }
                lista.add(new Entrada(s, COMPARTIDO, "armsx2_sstates.zip", new File(armsx2, "sstates"), true));
                break;
            }
            case "gc": {
                File dolphin = new File(DATOS_APPS, "org.dolphinemu.dolphinemu/files");
                lista.add(new Entrada(s, COMPARTIDO, "dolphin_gc.zip", new File(dolphin, "GC"), true));
                lista.add(new Entrada(s, COMPARTIDO, "dolphin_statesaves.zip", new File(dolphin, "StateSaves"), true));
                break;
            }
            case "rpgmaker": {
                File carpetaKirin = carpetaKirinDe(juego.nombreFichero);
                if (carpetaKirin != null) {
                    lista.add(new Entrada(s, juego.nombreFichero, "kirin_saved_games.zip", carpetaKirin, true));
                }
                break;
            }
            default:
                // RetroArch: GB, GBC, GBA, N64, PS1, PSP...
                lista.addAll(entradasRetroArch(s, stem, remotos));
                if ("psx".equals(s)) {
                    lista.add(new Entrada(s, COMPARTIDO, "saves__PCSX-ReARMed__pcsx-card2.mcd",
                            new File(RETROARCH, "saves/PCSX-ReARMed/pcsx-card2.mcd"), false));
                }
                if ("psp".equals(s)) {
                    lista.add(new Entrada(s, COMPARTIDO, "ppsspp_savedata.zip", carpetaPpsspp(), true));
                }
                break;
        }
        return lista;
    }

    /**
     * Ficheros de RetroArch del juego (<rom>.srm, <rom>.state, .state1,
     * .state.auto...) de cualquier core, más los que solo están en el
     * servidor. Nombre en el servidor: "<saves|states>__<core>__<fichero>",
     * así se sabe en qué carpeta va al bajarlo aunque ese core ni exista aún.
     * Se busca "<rom>." exacto: "Super Mario Advance" no se lleva los de
     * "Super Mario Advance 2".
     */
    private static List<Entrada> entradasRetroArch(String sistema, String stem, Map<String, Double> remotos) {
        List<Entrada> lista = new ArrayList<>();
        String prefijo = stem + ".";
        for (String tipo : new String[]{"saves", "states"}) {
            File[] cores = new File(RETROARCH, tipo).listFiles(File::isDirectory);
            if (cores == null) {
                continue;
            }
            for (File core : cores) {
                File[] ficheros = core.listFiles((dir, nombre) -> nombre.startsWith(prefijo));
                if (ficheros == null) {
                    continue;
                }
                for (File f : ficheros) {
                    lista.add(new Entrada(sistema, stem, tipo + "__" + core.getName() + "__" + f.getName(), f, false));
                }
            }
        }
        String prefijoRemoto = sistema + "/" + stem + "/";
        for (String id : remotos.keySet()) {
            if (!id.startsWith(prefijoRemoto)) {
                continue;
            }
            String nombre = id.substring(prefijoRemoto.length());
            String[] partes = nombre.split("__", 3);
            if (partes.length == 3 && ("saves".equals(partes[0]) || "states".equals(partes[0]))) {
                lista.add(new Entrada(sistema, stem, nombre,
                        new File(RETROARCH, partes[0] + "/" + partes[1] + "/" + partes[2]), false));
            }
        }
        return lista;
    }

    /** PPSSPP (core de RetroArch) guarda la tarjeta de memoria en PSP/SAVEDATA bajo saves/ o system/. */
    private static File carpetaPpsspp() {
        File enSaves = new File(RETROARCH, "saves/PPSSPP/PSP/SAVEDATA");
        File enSystem = new File(RETROARCH, "system/PPSSPP/PSP/SAVEDATA");
        return !enSaves.isDirectory() && enSystem.isDirectory() ? enSystem : enSaves;
    }

    /**
     * Kirin guarda las partidas en Kirin/Saved Games/<título>, y el título
     * sale de su game-library.json (el juego que apunta a la carpeta
     * ROMs/rpgmaker/<juego>/...). Si el juego aún no se ha añadido a Kirin,
     * no hay partidas que sincronizar.
     */
    private static File carpetaKirinDe(String nombreJuego) {
        File biblioteca = new File(RAIZ, "Kirin/game-library.json");
        if (!biblioteca.isFile()) {
            return null;
        }
        try {
            JSONArray juegos = new JSONArray(new String(Files.readAllBytes(biblioteca.toPath()), StandardCharsets.UTF_8));
            String marca = "/ROMs/rpgmaker/" + nombreJuego + "/";
            for (int i = 0; i < juegos.length(); i++) {
                JSONObject j = juegos.getJSONObject(i);
                if ((j.optString("path") + "/").contains(marca)) {
                    return new File(RAIZ, "Kirin/Saved Games/" + j.getString("title"));
                }
            }
        } catch (Exception e) {
            log("ERROR leyendo game-library.json de Kirin: " + e);
        }
        return null;
    }

    // ---------- Utilidades ----------

    private static String quitarExtension(String nombreFichero) {
        int punto = nombreFichero.lastIndexOf('.');
        return punto > 0 ? nombreFichero.substring(0, punto) : nombreFichero;
    }

    private static long mtimeMasReciente(File carpeta) {
        File[] hijos = carpeta.listFiles();
        if (hijos == null) {
            return 0;
        }
        long max = 0;
        for (File hijo : hijos) {
            max = Math.max(max, hijo.isDirectory() ? mtimeMasReciente(hijo) : hijo.lastModified());
        }
        return max;
    }

    private static void comprimirCarpeta(File carpeta, File zipDestino) throws IOException {
        try (ZipOutputStream zos = new ZipOutputStream(new FileOutputStream(zipDestino))) {
            comprimirRecursivo(carpeta, carpeta, zos);
        }
    }

    private static void comprimirRecursivo(File raiz, File actual, ZipOutputStream zos) throws IOException {
        File[] hijos = actual.listFiles();
        if (hijos == null) {
            return;
        }
        for (File hijo : hijos) {
            if (hijo.isDirectory()) {
                comprimirRecursivo(raiz, hijo, zos);
                continue;
            }
            ZipEntry entrada = new ZipEntry(raiz.toURI().relativize(hijo.toURI()).getPath());
            entrada.setTime(hijo.lastModified()); // se restaura al descomprimir (ver cabecera)
            zos.putNextEntry(entrada);
            try (InputStream in = new FileInputStream(hijo)) {
                byte[] buffer = new byte[65536];
                int leidos;
                while ((leidos = in.read(buffer)) != -1) {
                    zos.write(buffer, 0, leidos);
                }
            }
            zos.closeEntry();
        }
    }

    private static void descomprimirSobreCarpeta(File zip, File carpetaDestino) throws IOException {
        if (!carpetaDestino.isDirectory() && !carpetaDestino.mkdirs()) {
            throw new IOException("No se pudo crear " + carpetaDestino);
        }
        String raizCanonica = carpetaDestino.getCanonicalPath() + File.separator;
        try (ZipInputStream zis = new ZipInputStream(new FileInputStream(zip))) {
            ZipEntry entrada;
            while ((entrada = zis.getNextEntry()) != null) {
                File destino = new File(carpetaDestino, entrada.getName());
                if (!destino.getCanonicalPath().startsWith(raizCanonica)) {
                    continue; // defensa "zip slip"
                }
                if (entrada.isDirectory()) {
                    destino.mkdirs();
                    continue;
                }
                File padre = destino.getParentFile();
                if (padre != null) {
                    padre.mkdirs();
                }
                try (OutputStream out = new FileOutputStream(destino)) {
                    byte[] buffer = new byte[65536];
                    int leidos;
                    while ((leidos = zis.read(buffer)) != -1) {
                        out.write(buffer, 0, leidos);
                    }
                }
                if (entrada.getTime() > 0) {
                    destino.setLastModified(entrada.getTime());
                }
                zis.closeEntry();
            }
        }
    }

    private static void copiarFichero(File origen, File destino) throws IOException {
        try (InputStream in = new FileInputStream(origen); OutputStream out = new FileOutputStream(destino)) {
            byte[] buffer = new byte[65536];
            int leidos;
            while ((leidos = in.read(buffer)) != -1) {
                out.write(buffer, 0, leidos);
            }
        }
    }

    static void log(String mensaje) {
        try {
            File carpeta = FICHERO_LOG.getParentFile();
            if (carpeta != null && !carpeta.isDirectory()) {
                carpeta.mkdirs();
            }
            String hora = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.ROOT).format(new Date());
            try (FileWriter fw = new FileWriter(FICHERO_LOG, true)) {
                fw.write(hora + "  " + mensaje + "\n");
            }
        } catch (Exception ignored) {
        }
    }
}
