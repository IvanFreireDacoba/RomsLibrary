package es.jarjar.romlibrary;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.widget.ImageView;

import java.io.ByteArrayOutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.LinkedHashMap;
import java.util.Map;

/** Carga de carátulas con caché en memoria (LRU simple) para no re-descargar en cada scroll. */
public final class ImageLoader {

    private static final int MAX_EN_CACHE = 60;

    private static final Map<String, Bitmap> CACHE = new LinkedHashMap<String, Bitmap>(16, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Bitmap> eldest) {
            return size() > MAX_EN_CACHE;
        }
    };

    private ImageLoader() {
    }

    /** Asocia la petición a la vista mediante una etiqueta para no pintar una carátula
     * equivocada si la fila se recicló antes de que terminase la descarga. */
    public static void cargarEn(ImageView vista, String baseUrl, String token, String coverUrl) {
        // La clave es la URL completa: dos librerías pueden tener la misma ruta relativa.
        String completa = coverUrl == null ? null : baseUrl + coverUrl;
        vista.setTag(completa);
        vista.setImageDrawable(null);
        if (completa == null) {
            return;
        }
        synchronized (CACHE) {
            Bitmap enCache = CACHE.get(completa);
            if (enCache != null) {
                vista.setImageBitmap(enCache);
                return;
            }
        }
        new Thread(() -> {
            try {
                URL url = new URL(completa);
                HttpURLConnection con = (HttpURLConnection) url.openConnection();
                ApiClient.ponerClave(con, token);
                con.setConnectTimeout(8000);
                con.setReadTimeout(15000);
                ByteArrayOutputStream buffer = new ByteArrayOutputStream();
                try {
                    byte[] trozo = new byte[8192];
                    int leidos;
                    java.io.InputStream in = con.getInputStream();
                    while ((leidos = in.read(trozo)) != -1) {
                        buffer.write(trozo, 0, leidos);
                    }
                } finally {
                    con.disconnect();
                }
                byte[] datos = buffer.toByteArray();
                Bitmap bitmap = BitmapFactory.decodeByteArray(datos, 0, datos.length);
                if (bitmap == null) {
                    return;
                }
                synchronized (CACHE) {
                    CACHE.put(completa, bitmap);
                }
                vista.post(() -> {
                    if (completa.equals(vista.getTag())) {
                        vista.setImageBitmap(bitmap);
                    }
                });
            } catch (Exception ignored) {
            }
        }).start();
    }
}
