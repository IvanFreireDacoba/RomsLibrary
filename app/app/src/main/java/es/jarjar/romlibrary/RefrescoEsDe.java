package es.jarjar.romlibrary;

import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Environment;
import android.util.Log;

import java.io.File;

/**
 * Bandera para que ES-DE relea su biblioteca tras instalar/borrar juegos, SIN
 * privilegios en la app.
 *
 * La app NO puede reiniciar ES-DE ella misma (force-stop de otra app = root o
 * Shizuku, ambos descartados en este proyecto). Lo hace un vigilante del lado
 * del dispositivo (Termux/Tasker, {@code ~/.termux/tasker/esde_refresh.sh},
 * sobre el canal adb local permanente 127.0.0.1:5555) que, SOLO si existe la
 * bandera, hace el force-stop + relanzar de ES-DE y la borra. La app únicamente
 * DEJA la bandera, con una escritura normal de fichero.
 *
 * RUTA EXACTA (fija, la que vigila el dispositivo):
 *   {@code /sdcard/RomsLibrary/reiniciar_esde.flag}  (fichero vacío)
 * Es {@link Environment#getExternalStorageDirectory()} + {@code /RomsLibrary/};
 * la carpeta se crea si no existe. Es una ruta FIJA del almacenamiento interno,
 * no depende de dónde esté la carpeta de ROMs (la microSD cambia de UUID). La
 * escritura necesita «Acceso a todos los archivos» (MANAGE_EXTERNAL_STORAGE, el
 * mismo permiso que ya usan las carátulas de ES-DE), que en la Retroid está
 * concedido.
 *
 * No depende ya del antiguo botón manual «Run script as root»: el vigilante
 * consume la bandera solo (ver docs/gamenative-esde-retroid.md).
 */
final class RefrescoEsDe {

    private static final String CARPETA = "RomsLibrary";
    static final String NOMBRE_BANDERA = "reiniciar_esde.flag";
    private static final String PREF_AUTO = "reiniciar_esde_auto";

    private RefrescoEsDe() {
    }

    static boolean esDeInstalado(Context c) {
        PackageManager pm = c.getPackageManager();
        return pm.getLaunchIntentForPackage("org.es_de.frontend") != null
                || pm.getLaunchIntentForPackage("org.es_de.frontend.galaxy") != null;
    }

    /** ¿Dejar la bandera automáticamente al terminar la cola? (ajuste, por defecto sí). */
    static boolean automatico(Context c) {
        return c.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE).getBoolean(PREF_AUTO, true);
    }

    static void setAutomatico(Context c, boolean valor) {
        c.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE).edit().putBoolean(PREF_AUTO, valor).apply();
    }

    /**
     * Deja la bandera al terminar la cola (o la sync). Respeta el ajuste y solo
     * actúa si ES-DE está instalado. Best-effort, nunca lanza.
     * @return la ruta donde quedó la bandera, o null si no se dejó.
     */
    static String marcarAutomatico(Context c) {
        if (!automatico(c) || !esDeInstalado(c)) {
            return null;
        }
        return escribir();
    }

    /** Deja la bandera por orden explícita del usuario (menú), ignorando el ajuste. */
    static String marcarForzado() {
        return escribir();
    }

    private static String escribir() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && !Environment.isExternalStorageManager()) {
                Log.w("RomLibrary", "Sin «Acceso a todos los archivos»: no se puede dejar la bandera de ES-DE");
                return null;
            }
            File dir = new File(Environment.getExternalStorageDirectory(), CARPETA);   // /sdcard/RomsLibrary
            if (!dir.isDirectory() && !dir.mkdirs()) {
                Log.w("RomLibrary", "No se pudo crear " + dir);
                return null;
            }
            File bandera = new File(dir, NOMBRE_BANDERA);                              // /sdcard/RomsLibrary/reiniciar_esde.flag
            // Recrear para que cambie el mtime aunque ya existiera (por si el
            // vigilante aún no la había consumido): así siempre la detecta.
            //noinspection ResultOfMethodCallIgnored
            bandera.delete();
            //noinspection ResultOfMethodCallIgnored
            bandera.createNewFile();
            Log.i("RomLibrary", "Bandera de reinicio de ES-DE: " + bandera.getAbsolutePath());
            return bandera.getAbsolutePath();
        } catch (Exception e) {
            Log.w("RomLibrary", "No se pudo dejar la bandera de ES-DE", e);
            return null;
        }
    }
}
