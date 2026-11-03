package es.jarjar.romlibrary;

import android.content.ContentResolver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.UriPermission;
import android.net.Uri;

import androidx.documentfile.provider.DocumentFile;

/**
 * Persistencia de la carpeta ROMs elegida por SAF. El URI en SharedPreferences
 * por sí solo no basta -- Android puede haber revocado el permiso persistente
 * (raíz movida/desmontada, límite de concesiones, etc.), así que toda lectura
 * se valida contra {@link ContentResolver#getPersistedUriPermissions()} antes
 * de confiar en el URI guardado.
 */
final class CarpetaRomsUtil {

    private static final int FLAGS = Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION;

    private CarpetaRomsUtil() {
    }

    /** Una entrada de una carpeta SAF, con lo que hace falta para saber si un juego está instalado. */
    static final class Hijo {
        final String id;
        final boolean carpeta;
        final long tamano;

        Hijo(String id, boolean carpeta, long tamano) {
            this.id = id;
            this.carpeta = carpeta;
            this.tamano = tamano;
        }
    }

    /**
     * Hijos de una carpeta del árbol SAF (docId null = la raíz), por nombre, con
     * UNA sola consulta. DocumentFile hace una consulta por cada getName()/
     * length() de cada fichero: con carpetas de cientos de ROMs, marcar los
     * instalados tardaba decenas de segundos.
     */
    static java.util.Map<String, Hijo> hijos(Context c, Uri arbol, String docId) {
        java.util.Map<String, Hijo> res = new java.util.HashMap<>();
        String id = docId != null ? docId : android.provider.DocumentsContract.getTreeDocumentId(arbol);
        Uri uri = android.provider.DocumentsContract.buildChildDocumentsUriUsingTree(arbol, id);
        String[] columnas = {android.provider.DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                android.provider.DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                android.provider.DocumentsContract.Document.COLUMN_MIME_TYPE,
                android.provider.DocumentsContract.Document.COLUMN_SIZE};
        try (android.database.Cursor cur = c.getContentResolver().query(uri, columnas, null, null, null)) {
            while (cur != null && cur.moveToNext()) {
                String nombre = cur.getString(1);
                if (nombre != null) {
                    res.put(nombre, new Hijo(cur.getString(0),
                            android.provider.DocumentsContract.Document.MIME_TYPE_DIR.equals(cur.getString(2)),
                            cur.isNull(3) ? 0 : cur.getLong(3)));
                }
            }
        } catch (Exception e) {
            android.util.Log.w("RomLibrary", "No se pudo listar la carpeta de ROMs", e);
        }
        return res;
    }

    /** Devuelve el URI guardado SOLO si su permiso persistente sigue siendo válido; si no, null. */
    static Uri obtenerUriValido(Context context, SharedPreferences prefs) {
        String guardado = prefs.getString(MainActivity.CLAVE_ARBOL_ROMS, null);
        if (guardado == null) {
            return null;
        }
        Uri uri = Uri.parse(guardado);
        for (UriPermission permiso : context.getContentResolver().getPersistedUriPermissions()) {
            if (permiso.getUri().equals(uri) && permiso.isReadPermission() && permiso.isWritePermission()) {
                return uri;
            }
        }
        return null;
    }

    /** Nombre visible de la carpeta ya elegida, o null si no hay ninguna válida. */
    static String nombreVisible(Context context, SharedPreferences prefs) {
        Uri uri = obtenerUriValido(context, prefs);
        if (uri == null) {
            return null;
        }
        DocumentFile raiz = DocumentFile.fromTreeUri(context, uri);
        return raiz != null ? raiz.getName() : null;
    }

    /**
     * Sustituye la carpeta guardada: libera el permiso anterior (evita acumular
     * concesiones contra el límite de Android) y toma+guarda el nuevo de forma persistente.
     */
    static void guardarNuevaCarpeta(Context context, SharedPreferences prefs, Uri nuevoUri) {
        ContentResolver resolver = context.getContentResolver();
        String anterior = prefs.getString(MainActivity.CLAVE_ARBOL_ROMS, null);
        if (anterior != null) {
            try {
                resolver.releasePersistableUriPermission(Uri.parse(anterior), FLAGS);
            } catch (Exception ignored) {
                // Ya podía estar revocado por el sistema; no es un error real.
            }
        }
        resolver.takePersistableUriPermission(nuevoUri, FLAGS);
        prefs.edit().putString(MainActivity.CLAVE_ARBOL_ROMS, nuevoUri.toString()).apply();
    }
}
