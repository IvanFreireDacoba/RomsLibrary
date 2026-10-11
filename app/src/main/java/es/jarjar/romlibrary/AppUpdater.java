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

    public static void comprobar(Activity activity, String servidor, String clave) {
        boolean sinServidor = servidor == null || servidor.isEmpty();
        String baseUrl = sinServidor ? Cuenta.ORIGEN_ACTUALIZACIONES : servidor;
        String token = sinServidor ? "" : clave;
        new Thread(() -> {
            try {
                String json = ApiClient.getTexto(baseUrl, token, "/APIs/RomLibraryApp?accion=version");
                JSONObject info = new JSONObject(json);
                long versionRemota = info.getLong("versionCode");
                String sha1Esperado = info.getString("apkSha1");
                String rutaApk = info.getString("apkPath");

                long versionLocal = obtenerVersionLocal(activity);
                if (versionRemota <= versionLocal) {
                    return;
                }

                File destino = new File(activity.getCacheDir(), "actualizacion.apk");
                try (FileOutputStream out = new FileOutputStream(destino)) {
                    ApiClient.descargarAFichero(baseUrl, token, rutaApk, out);
                }

                String sha1Real = calcularSha1(destino);
                if (!sha1Real.equalsIgnoreCase(sha1Esperado)) {
                    activity.runOnUiThread(() -> Toast.makeText(activity,
                            "Actualización descartada: el hash no coincide", Toast.LENGTH_LONG).show());
                    destino.delete();
                    return;
                }

                activity.runOnUiThread(() -> lanzarInstalador(activity, destino, baseUrl, token, rutaApk));
            } catch (Exception e) {
                // Fallo silencioso: la app sigue funcionando con la versión actual.
            }
        }).start();
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
