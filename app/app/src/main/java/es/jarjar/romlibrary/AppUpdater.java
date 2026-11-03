package es.jarjar.romlibrary;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.widget.Toast;

import androidx.core.content.FileProvider;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.security.MessageDigest;

/**
 * Autoactualización: consulta /api/version (calculado al vuelo en el
 * servidor a partir de la APK real ya publicada, nunca un número a mano),
 * compara contra la versión instalada, y si hay una más nueva la descarga
 * y verifica su SHA1 ANTES de lanzar el instalador -- nunca se instala nada
 * sin verificar el hash primero.
 */
public final class AppUpdater {

    private AppUpdater() {
    }

    /** Una sola comprobación a la vez, y como mucho cada INTERVALO_MS (también desde onResume). */
    private static final java.util.concurrent.atomic.AtomicBoolean EN_CURSO = new java.util.concurrent.atomic.AtomicBoolean(false);
    private static final long INTERVALO_MS = 30 * 60 * 1000;
    private static long ultimaOk = -INTERVALO_MS;
    /** Reintentos si la consulta falla (red lenta al arrancar, servidor ocupado...). */
    private static final long[] ESPERAS_MS = {0, 10_000, 30_000, 90_000};

    /**
     * Independiente de todo lo demás del arranque: su propio hilo, su propia
     * petición ligera (accion=version) y sus reintentos. Antes era un único
     * intento de 8 s al crear la Activity: si fallaba (el 2026-11-03 el
     * servidor tardaba >10 s en responder durante el arranque) no se volvía a
     * mirar hasta cerrar y abrir la app, y nunca salía el aviso de update.
     */
    public static void comprobar(Activity activity, String servidor, String clave) {
        long ahora = android.os.SystemClock.elapsedRealtime();
        if (ahora - ultimaOk < INTERVALO_MS || !EN_CURSO.compareAndSet(false, true)) {
            return;
        }
        // Siempre del origen oficial, conecte la consola a la librería que
        // conecte: una librería de terceros nunca decide qué APK se instala.
        String baseUrl = Cuenta.ORIGEN_ACTUALIZACIONES;
        String token = "";
        new Thread(() -> {
            try {
                for (long espera : ESPERAS_MS) {
                    if (espera > 0) {
                        Thread.sleep(espera);
                    }
                    if (activity.isFinishing()) {
                        return;
                    }
                    try {
                        comprobarUnaVez(activity, baseUrl, token);
                        ultimaOk = android.os.SystemClock.elapsedRealtime();
                        return;
                    } catch (java.io.IOException e) {
                        android.util.Log.w("RomLibrary", "Comprobación de actualización fallida, se reintenta", e);
                    }
                }
            } catch (Exception e) {
                // Fallo silencioso: la app sigue funcionando con la versión actual.
            } finally {
                EN_CURSO.set(false);
            }
        }, "comprobar-update").start();
    }

    private static void comprobarUnaVez(Activity activity, String baseUrl, String token) throws Exception {
        String json = ApiClient.getTexto(baseUrl, token, "/APIs/RomLibraryApp?accion=version", 15000);
        JSONObject info = new JSONObject(json);
        long versionRemota = info.getLong("versionCode");
        String sha1Esperado = info.getString("apkSha1");
        String rutaApk = info.getString("apkPath");

        long versionLocal = obtenerVersionLocal(activity);
        if (versionRemota <= versionLocal) {
            return;
        }

        File destino = new File(activity.getCacheDir(), "actualizacion.apk");
        // Ya bajada en un intento anterior (p.ej. se canceló el instalador): no se repite.
        if (!(destino.isFile() && calcularSha1(destino).equalsIgnoreCase(sha1Esperado))) {
            try (FileOutputStream out = new FileOutputStream(destino)) {
                ApiClient.descargarAFichero(baseUrl, token, rutaApk, out);
            }
        }

        String sha1Real = calcularSha1(destino);
        if (!sha1Real.equalsIgnoreCase(sha1Esperado)) {
            activity.runOnUiThread(() -> Toast.makeText(activity,
                    "Actualización descartada: el hash no coincide", Toast.LENGTH_LONG).show());
            destino.delete();
            return;
        }

        activity.runOnUiThread(() -> {
            if (!activity.isFinishing()) {
                lanzarInstalador(activity, destino, baseUrl, token, rutaApk);
            }
        });
    }

    private static long obtenerVersionLocal(Activity activity) throws PackageManager.NameNotFoundException {
        PackageInfo info = activity.getPackageManager().getPackageInfo(activity.getPackageName(), 0);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            return info.getLongVersionCode();
        }
        return info.versionCode;
    }

    private static String calcularSha1(File fichero) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-1");
        try (FileInputStream in = new FileInputStream(fichero)) {
            byte[] trozo = new byte[8192];
            int leidos;
            while ((leidos = in.read(trozo)) != -1) {
                digest.update(trozo, 0, leidos);
            }
        }
        StringBuilder sb = new StringBuilder();
        for (byte b : digest.digest()) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    private static void lanzarInstalador(Activity activity, File apk, String baseUrl, String token, String rutaApk) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                && !activity.getPackageManager().canRequestPackageInstalls()) {
            Toast.makeText(activity, "Activa \"instalar apps desconocidas\" para esta app y vuelve a intentarlo",
                    Toast.LENGTH_LONG).show();
            Intent ajustes = new Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + activity.getPackageName()));
            try {
                activity.startActivity(ajustes);
            } catch (Exception ignored) {
                abrirEnNavegador(activity, baseUrl, token, rutaApk);
            }
            return;
        }
        try {
            Uri uri = FileProvider.getUriForFile(activity, activity.getPackageName() + ".fileprovider", apk);
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(uri, "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            activity.startActivity(intent);
        } catch (Exception e) {
            abrirEnNavegador(activity, baseUrl, token, rutaApk);
        }
    }

    private static void abrirEnNavegador(Activity activity, String baseUrl, String token, String rutaApk) {
        try {
            Uri uri = Uri.parse(baseUrl + rutaApk); // "apk" es pública: sin clave
            activity.startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (Exception ignored) {
        }
    }
}
