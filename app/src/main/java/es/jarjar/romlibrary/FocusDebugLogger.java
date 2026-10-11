package es.jarjar.romlibrary;

import android.os.Environment;

import java.io.File;
import java.io.FileWriter;
import java.text.SimpleDateFormat;
import java.util.Locale;

/**
 * SOLO DEPURACIÓN TEMPORAL -- diagnóstico del D-pad sin depender de ADB
 * (MANAGE_EXTERNAL_STORAGE ya da acceso de escritura aquí sin depuración
 * inalámbrica). Quitar todas las llamadas a esto en cuanto se resuelva el
 * bug de foco; no debe quedar en producción.
 */
final class FocusDebugLogger {

    private static final File FICHERO = new File(Environment.getExternalStorageDirectory(), "RomLibraryDebug/focus_log.txt");

    private FocusDebugLogger() {
    }

    static void log(String mensaje) {
        try {
            File carpeta = FICHERO.getParentFile();
            if (carpeta != null && !carpeta.isDirectory()) {
                carpeta.mkdirs();
            }
            String hora = new SimpleDateFormat("HH:mm:ss.SSS", Locale.ROOT).format(new java.util.Date());
            try (FileWriter fw = new FileWriter(FICHERO, true)) {
                fw.write(hora + "  " + mensaje + "\n");
            }
        } catch (Exception ignored) {
        }
    }
}
