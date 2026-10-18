package es.jarjar.romlibrary;

import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.Drawable;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;

import androidx.documentfile.provider.DocumentFile;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Carátulas de ES-DE para lo que el scraper no sabe encontrar:
 *
 * - androidapps, androidgames y emulators: ES-DE crea un "<Nombre>.app" por
 *   cada app, con el nombre del paquete dentro. La carátula es el icono de
 *   la propia app (el que se ve en el lanzador de Android).
 * - steam: cada "<Juego>.steam" lleva dentro el id de Steam; la carátula es
 *   la vertical oficial de la tienda (library_600x900).
 *
 * Solo rellena lo que falta: nunca pisa una carátula que ya exista (scrapeada
 * o puesta a mano). Se ejecuta en segundo plano al abrir la app.
 */
final class EsDeIconos {

    private static final String[] SISTEMAS_APPS = {"androidapps", "androidgames", "emulators"};
    private static final String[] EXTENSIONES = {".png", ".jpg", ".jpeg", ".webp", ".gif"};
    private static final int LADO_ICONO = 512;

    private EsDeIconos() {
    }

    /** Devuelve cuántas carátulas ha creado. Bloqueante: llamar desde un hilo de fondo. */
    static int completar(Context context, Uri arbolRoms) {
        if (arbolRoms == null || Build.VERSION.SDK_INT < Build.VERSION_CODES.R || !Environment.isExternalStorageManager()) {
            return 0;
        }
        DocumentFile roms = DocumentFile.fromTreeUri(context, arbolRoms);
        if (roms == null || !roms.isDirectory()) {
            return 0;
        }
        int creadas = 0;
        for (String sistema : SISTEMAS_APPS) {
            DocumentFile carpeta = roms.findFile(sistema);
            if (carpeta == null || !carpeta.isDirectory()) {
                continue;
            }
            File covers = carpetaMedia(sistema, "covers");
            for (DocumentFile f : carpeta.listFiles()) {
                String nombre = f.getName();
                if (nombre == null || !nombre.toLowerCase().endsWith(".app")) {
                    continue;
                }
                String stem = nombre.substring(0, nombre.length() - 4);
                if (tieneCaratula(covers, stem)) {
                    continue;
                }
                String paquete = leerTexto(context, f.getUri());
                if (paquete != null && guardarIcono(context, paquete, new File(covers, stem + ".png"))) {
                    creadas++;
                }
            }
        }
        DocumentFile steam = roms.findFile("steam");
        if (steam != null && steam.isDirectory()) {
            File covers = carpetaMedia("steam", "covers");
            for (DocumentFile f : steam.listFiles()) {
                String nombre = f.getName();
                if (nombre == null || !nombre.toLowerCase().endsWith(".steam")) {
                    continue;
                }
                String stem = nombre.substring(0, nombre.length() - 6);
                String id = leerTexto(context, f.getUri());
                if (tieneCaratula(covers, stem) || id == null || !id.matches("\\d{1,10}")) {
                    continue;
                }
                if (descargarCaratulaSteam(id, new File(covers, stem + ".jpg"))) {
                    creadas++;
                }
            }
        }
        return creadas;
    }

    /**
     * Carpeta de media que ES-DE lee de verdad: el ajuste MediaDirectory de
     * es_settings.xml si tiene valor; si no, /storage/emulated/0/ES-DE/downloaded_media.
     */
    static File carpetaMedia(String sistema, String tipo) {
        File raizEsDe = new File(Environment.getExternalStorageDirectory(), "ES-DE");
        File media = new File(raizEsDe, "downloaded_media");
        try {
            String xml = new String(java.nio.file.Files.readAllBytes(
                    new File(raizEsDe, "settings/es_settings.xml").toPath()), StandardCharsets.UTF_8);
            java.util.regex.Matcher m = java.util.regex.Pattern
                    .compile("<string name=\"MediaDirectory\" value=\"([^\"]*)\"").matcher(xml);
            if (m.find() && !m.group(1).trim().isEmpty()) {
                media = new File(m.group(1).trim());
            }
        } catch (Exception ignored) {
            // sin es_settings.xml legible: la ubicación por defecto
        }
        return new File(media, sistema + "/" + tipo);
    }

    private static boolean tieneCaratula(File covers, String stem) {
        for (String ext : EXTENSIONES) {
            if (new File(covers, stem + ext).isFile()) {
                return true;
            }
        }
        return false;
    }

    private static String leerTexto(Context context, Uri uri) {
        try (InputStream in = context.getContentResolver().openInputStream(uri)) {
            if (in == null) {
                return null;
            }
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] trozo = new byte[256];
            int n;
            while ((n = in.read(trozo)) != -1 && buffer.size() < 4096) {
                buffer.write(trozo, 0, n);
            }
            String texto = buffer.toString(StandardCharsets.UTF_8.name()).trim();
            return texto.isEmpty() ? null : texto;
        } catch (Exception e) {
            return null;
        }
    }

    private static boolean guardarIcono(Context context, String paquete, File destino) {
        try {
            Drawable icono = context.getPackageManager().getApplicationIcon(paquete);
            Bitmap bitmap = Bitmap.createBitmap(LADO_ICONO, LADO_ICONO, Bitmap.Config.ARGB_8888);
            Canvas lienzo = new Canvas(bitmap);
            icono.setBounds(0, 0, LADO_ICONO, LADO_ICONO);
            icono.draw(lienzo);
            return escribir(destino, out -> bitmap.compress(Bitmap.CompressFormat.PNG, 100, out));
        } catch (PackageManager.NameNotFoundException e) {
            return false; // app desinstalada: ES-DE quitará su .app
        }
    }

    private static boolean descargarCaratulaSteam(String id, File destino) {
        String base = "https://cdn.cloudflare.steamstatic.com/steam/apps/" + id + "/";
        for (String imagen : new String[]{"library_600x900_2x.jpg", "library_600x900.jpg", "header.jpg"}) {
            try {
                HttpURLConnection con = (HttpURLConnection) new URL(base + imagen).openConnection();
                con.setConnectTimeout(10_000);
                con.setReadTimeout(20_000);
                if (con.getResponseCode() != 200) {
                    con.disconnect();
                    continue;
                }
                try (InputStream in = con.getInputStream()) {
                    if (escribir(destino, out -> {
                        byte[] trozo = new byte[64 * 1024];
                        int n;
                        while ((n = in.read(trozo)) != -1) {
                            out.write(trozo, 0, n);
                        }
                    })) {
                        return true;
                    }
                } finally {
                    con.disconnect();
                }
            } catch (Exception ignored) {
                // sin red o imagen inexistente: se prueba la siguiente
            }
        }
        return false;
    }

    interface Escritor {
        void escribir(OutputStream out) throws Exception;
    }

    /** Escribe a un temporal y renombra: ES-DE nunca ve una imagen a medias. */
    private static boolean escribir(File destino, Escritor escritor) {
        File carpeta = destino.getParentFile();
        if (carpeta != null && !carpeta.isDirectory() && !carpeta.mkdirs()) {
            return false;
        }
        File temporal = new File(carpeta, destino.getName() + ".tmp");
        try (OutputStream out = new FileOutputStream(temporal)) {
            escritor.escribir(out);
        } catch (Exception e) {
            temporal.delete();
            return false;
        }
        return temporal.renameTo(destino);
    }
}
