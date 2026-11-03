package es.jarjar.romlibrary;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.util.LruCache;
import android.widget.ImageView;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

/**
 * Carátulas, la ÚLTIMA fase del arranque (1: conexión, 2: tabla de juegos,
 * 3: carátulas), perezosas y poco a poco:
 * - Pocas descargas a la vez (2) y la cola es LIFO: lo último que se ha
 *   pedido es lo que está en pantalla, así que va primero. Una petición
 *   cuya fila ya se ha reciclado (salió de la pantalla) se salta.
 * - Caché en memoria (por bytes) y en disco (getCacheDir()/caratulas, 7
 *   días, 64 MB como mucho), así al abrir la app no se vuelven a pedir.
 * - Se decodifican reducidas (lado mayor ~ 512 px) para no gastar memoria.
 * Antes había un hilo por carátula sin límite: al arrancar salían decenas de
 * peticiones de golpe que competían con la tabla de juegos.
 */
public final class ImageLoader {

    private static final int DESCARGAS_A_LA_VEZ = 2;
    private static final int LADO_MAXIMO = 512;
    private static final long CADUCA_DISCO_MS = 7L * 24 * 3600 * 1000;
    private static final long MAX_DISCO = 64L * 1024 * 1024;

    private static final LruCache<String, Bitmap> MEMORIA =
            new LruCache<String, Bitmap>((int) Math.min(Runtime.getRuntime().maxMemory() / 8, 48L * 1024 * 1024)) {
                @Override
                protected int sizeOf(String clave, Bitmap bitmap) {
                    return bitmap.getByteCount();
                }
            };

    private static final ThreadPoolExecutor DESCARGAS = new ThreadPoolExecutor(
            DESCARGAS_A_LA_VEZ, DESCARGAS_A_LA_VEZ, 30, TimeUnit.SECONDS, new LinkedBlockingDeque<Runnable>() {
        // LIFO: lo más reciente (lo visible) primero.
        @Override
        public boolean offer(Runnable r) {
            return offerFirst(r);
        }
    });

    static {
        DESCARGAS.allowCoreThreadTimeOut(true);
    }

    /** Vistas esperando cada URL (una sola descarga aunque la pidan varias). */
    private static final Map<String, Boolean> EN_CURSO = new HashMap<>();

    private static File carpetaDisco;

    /** Para medir la fase 3 del arranque en el log (primeras carátulas pintadas). */
    private static volatile long inicio;
    private static final java.util.concurrent.atomic.AtomicInteger PINTADAS = new java.util.concurrent.atomic.AtomicInteger();

    static void marcarInicio(long inicioArranque) {
        inicio = inicioArranque;
        PINTADAS.set(0);
    }

    private ImageLoader() {
    }

    /** Llamar una vez (onCreate): prepara la caché en disco y la recorta. */
    public static synchronized void iniciar(Context contexto) {
        if (carpetaDisco != null) {
            return;
        }
        carpetaDisco = new File(contexto.getCacheDir(), "caratulas");
        //noinspection ResultOfMethodCallIgnored
        carpetaDisco.mkdirs();
        File dir = carpetaDisco;
        new Thread(() -> recortarDisco(dir), "caratulas-recorte").start();
    }

    /** Asocia la petición a la vista mediante una etiqueta para no pintar una carátula
     * equivocada si la fila se recicló antes de que terminase la descarga. */
    public static void cargarEn(ImageView vista, String baseUrl, String token, String coverUrl) {
        // La clave es la URL completa: dos librerías pueden tener la misma ruta relativa.
        // coverUrl puede ser ya absoluta (otra librería pública).
        String completa = coverUrl == null ? null
                : (coverUrl.startsWith("http://") || coverUrl.startsWith("https://")) ? coverUrl : baseUrl + coverUrl;
        vista.setTag(completa);
        vista.setImageDrawable(null);
        if (completa == null) {
            return;
        }
        Bitmap enMemoria = MEMORIA.get(completa);
        if (enMemoria != null) {
            vista.setImageBitmap(enMemoria);
            return;
        }
        DESCARGAS.execute(() -> {
            // La fila ya muestra otro juego (se ha desplazado la lista): no hace falta.
            if (!completa.equals(vista.getTag())) {
                return;
            }
            synchronized (EN_CURSO) {
                if (EN_CURSO.containsKey(completa)) {
                    // Otra tarea ya la trae: se reintenta pintar al acabar (desde memoria).
                    vista.postDelayed(() -> {
                        Bitmap b = MEMORIA.get(completa);
                        if (b != null && completa.equals(vista.getTag())) {
                            vista.setImageBitmap(b);
                        }
                    }, 1500);
                    return;
                }
                EN_CURSO.put(completa, Boolean.TRUE);
            }
            try {
                Bitmap bitmap = obtener(completa, token);
                if (bitmap == null) {
                    return;
                }
                MEMORIA.put(completa, bitmap);
                vista.post(() -> {
                    if (completa.equals(vista.getTag())) {
                        vista.setImageBitmap(bitmap);
                        int n = PINTADAS.incrementAndGet();
                        if (inicio > 0 && (n == 1 || n == 10)) {
                            android.util.Log.i("RomLibrary", "arranque fase 3 (carátulas): " + n + " pintada(s) a los "
                                    + (android.os.SystemClock.elapsedRealtime() - inicio) + " ms del arranque");
                        }
                    }
                });
            } finally {
                synchronized (EN_CURSO) {
                    EN_CURSO.remove(completa);
                }
            }
        });
    }

    private static Bitmap obtener(String url, String token) {
        File fichero = carpetaDisco == null ? null : new File(carpetaDisco, clave(url));
        boolean vigente = fichero != null && fichero.isFile()
                && System.currentTimeMillis() - fichero.lastModified() < CADUCA_DISCO_MS;
        if (vigente) {
            Bitmap b = decodificar(fichero);
            if (b != null) {
                return b;
            }
        }
        try {
            byte[] datos = descargar(url, token);
            if (fichero != null) {
                File tmp = new File(fichero.getPath() + ".tmp");
                try (FileOutputStream out = new FileOutputStream(tmp)) {
                    out.write(datos);
                }
                if (!tmp.renameTo(fichero)) {
                    //noinspection ResultOfMethodCallIgnored
                    tmp.delete();
                }
            }
            return decodificar(datos);
        } catch (Exception e) {
            // Sin red: mejor la carátula vieja que ninguna.
            return fichero != null && fichero.isFile() ? decodificar(fichero) : null;
        }
    }

    private static byte[] descargar(String url, String token) throws Exception {
        HttpURLConnection con = ResolucionDns.abrir(url);
        ApiClient.ponerClave(con, token);
        con.setConnectTimeout(8000);
        con.setReadTimeout(15000);
        try (InputStream in = con.getInputStream()) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] trozo = new byte[8192];
            int leidos;
            while ((leidos = in.read(trozo)) != -1) {
                buffer.write(trozo, 0, leidos);
            }
            return buffer.toByteArray();
        } finally {
            con.disconnect();
        }
    }

    private static Bitmap decodificar(File f) {
        try {
            return decodificar(Files.readAllBytes(f.toPath()));
        } catch (Exception e) {
            return null;
        }
    }

    private static Bitmap decodificar(byte[] datos) {
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(datos, 0, datos.length, o);
        int muestra = 1;
        while (Math.max(o.outWidth, o.outHeight) / (muestra * 2) >= LADO_MAXIMO) {
            muestra *= 2;
        }
        BitmapFactory.Options d = new BitmapFactory.Options();
        d.inSampleSize = muestra;
        return BitmapFactory.decodeByteArray(datos, 0, datos.length, d);
    }

    private static String clave(String url) {
        try {
            byte[] h = MessageDigest.getInstance("SHA-1").digest(url.getBytes("UTF-8"));
            StringBuilder sb = new StringBuilder();
            for (byte b : h) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (Exception e) {
            return Integer.toHexString(url.hashCode());
        }
    }

    private static void recortarDisco(File dir) {
        File[] ficheros = dir.listFiles();
        if (ficheros == null) {
            return;
        }
        Arrays.sort(ficheros, Comparator.comparingLong(File::lastModified).reversed());
        long total = 0;
        for (File f : ficheros) {
            total += f.length();
            if (total > MAX_DISCO || f.getName().endsWith(".tmp")) {
                //noinspection ResultOfMethodCallIgnored
                f.delete();
            }
        }
    }
}
