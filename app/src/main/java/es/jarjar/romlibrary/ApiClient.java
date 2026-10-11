package es.jarjar.romlibrary;

import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.net.URLEncoder;
import java.util.Map;
import java.util.UUID;

import org.json.JSONObject;

/**
 * Cliente HTTP mínimo contra el servidor RomLibrary. Usa HttpURLConnection
 * (no java.net.http.HttpClient: esa API no existe en el runtime real de
 * Android en ningún nivel de API, aunque compile contra el JDK de
 * escritorio).
 */
public final class ApiClient {

    private ApiClient() {
    }

    /**
     * La clave de acceso va en cabecera, nunca en la URL (las URLs acaban en
     * los logs). Las librerías públicas no la necesitan: vacía = sin clave.
     */
    static void ponerClave(HttpURLConnection con, String token) {
        if (token != null && !token.isEmpty()) {
            con.setRequestProperty("X-App-Token", token);
        }
    }

    public static String getTexto(String baseUrl, String token, String ruta) throws IOException {
        return getTexto(baseUrl, token, ruta, 8000);
    }

    public static String getTexto(String baseUrl, String token, String ruta, int timeoutMs) throws IOException {
        URL url = new URL(baseUrl + ruta);
        HttpURLConnection con = (HttpURLConnection) url.openConnection();
        ponerClave(con, token);
        con.setConnectTimeout(timeoutMs);
        con.setReadTimeout(timeoutMs);
        try {
            int codigo = con.getResponseCode();
            if (codigo != 200) {
                throw new IOException(detalleError(con, codigo));
            }
            try (InputStream in = con.getInputStream()) {
                ByteArrayOutputStream buffer = new ByteArrayOutputStream();
                byte[] trozo = new byte[8192];
                int leidos;
                while ((leidos = in.read(trozo)) != -1) {
                    buffer.write(trozo, 0, leidos);
                }
                return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
            }
        } finally {
            con.disconnect();
        }
    }

    /** Callback de progreso de descarga; total puede ser -1 si el servidor no manda Content-Length. */
    public interface ProgresoDescarga {
        void onProgreso(long descargado, long total);
    }

    /** Descarga un fichero por streaming directo a un OutputStream ya abierto (no lo cierra). */
    public static void descargarAFichero(String baseUrl, String token, String ruta, OutputStream destino) throws IOException {
        descargarAFichero(baseUrl, token, ruta, destino, null);
    }

    /** Igual que la anterior pero notificando bytes descargados/total en cada trozo leído. */
    public static void descargarAFichero(String baseUrl, String token, String ruta, OutputStream destino,
                                          ProgresoDescarga progreso) throws IOException {
        URL url = new URL(baseUrl + ruta);
        HttpURLConnection con = (HttpURLConnection) url.openConnection();
        ponerClave(con, token);
        con.setConnectTimeout(8000);
        con.setReadTimeout(30000);
        try {
            int codigo = con.getResponseCode();
            if (codigo != 200) {
                throw new IOException("HTTP " + codigo);
            }
            long total = con.getContentLengthLong();
            // Buffer grande + BufferedOutputStream: escribir a un Uri de SAF
            // (ContentResolver.openOutputStream) tiene mucho más coste por
            // llamada que un File normal -- con trozos de 16 KB sin buffer de
            // escritura, la descarga real acababa limitada por ese coste, no
            // por la red (confirmado: un curl directo desde el propio
            // dispositivo ya bajaba a la velocidad esperada, la app no).
            BufferedOutputStream salida = new BufferedOutputStream(destino, 65536);
            long acumulado = 0;
            try (InputStream in = con.getInputStream()) {
                byte[] trozo = new byte[65536];
                int leidos;
                while ((leidos = in.read(trozo)) != -1) {
                    salida.write(trozo, 0, leidos);
                    acumulado += leidos;
                    if (progreso != null) {
                        progreso.onProgreso(acumulado, total);
                    }
                }
                salida.flush();
            }
            // BUG REAL ENCONTRADO (This Gym of Mine: solo llegaron 4221 de
            // 10880 ficheros reales, un 61% perdido, sin ningún error --
            // partida a medias jugable-hasta-que-revienta): si la conexión se
            // corta a medias, in.read() simplemente devuelve -1 antes de
            // tiempo y el bucle sale igual que si hubiera terminado bien, sin
            // ninguna excepción. Para un fichero suelto eso deja un ROM
            // truncado; para un grupo, deja que ZipInputStream simplemente dé
            // por acabado el zip en el punto de corte -- sin fallar tampoco.
            // Comparar bytes reales contra el Content-Length real del
            // servidor (ya lo teníamos disponible para la barra de progreso)
            // detecta esto de forma genérica, para cualquier descarga.
            if (total > 0 && acumulado != total) {
                throw new IOException("Descarga incompleta: se esperaban " + total + " bytes, se recibieron " + acumulado);
            }
            // Sin Content-Length la comprobación de arriba no salta: un 200
            // con cuerpo vacío dejaba un ROM de 0 bytes "instalado" (Zelda
            // Minish Cap). Ningún ROM ni guardado real pesa 0 bytes.
            if (acumulado == 0) {
                throw new IOException("Descarga vacía: el servidor no envió ningún byte");
            }
        } finally {
            con.disconnect();
        }
    }

    /** Abre el destino de una descarga: desde cero (anadir=false) o para continuar al final (anadir=true). */
    public interface AbridorSalida {
        OutputStream abrir(boolean anadir) throws IOException;
    }

    /** Error que no se arregla reintentando (404, token inválido...): corta los reintentos. */
    private static final class ErrorDefinitivo extends IOException {
        ErrorDefinitivo(String mensaje) {
            super(mensaje);
        }
    }

    private static final int MAX_INTENTOS_DESCARGA = 8;

    /**
     * Descarga de ROMs a prueba de cortes: si la conexión se cae (wifi que
     * parpadea, consola que entra en reposo, servidor reiniciado...) se
     * reintenta hasta MAX_INTENTOS_DESCARGA veces con espera creciente
     * (2s, 4s, 8s... hasta 30s, ~3,5 min en total) y se CONTINÚA desde el
     * último byte recibido con "Range: bytes=N-" (jarjar y el proxy de
     * sund.es responden 206). Si el servidor no acepta el Range (200), se
     * empieza de cero truncando el destino. Antes, cualquier corte abortaba
     * la descarga entera -- con discos de PS1 de 700 MB eso era casi seguro.
     */
    public static void descargarConReintentos(String baseUrl, String token, String ruta, AbridorSalida abridor,
                                              ProgresoDescarga progreso) throws IOException {
        long descargado = 0;
        long total = -1;
        IOException ultimoError = null;
        for (int intento = 1; intento <= MAX_INTENTOS_DESCARGA; intento++) {
            if (intento > 1) {
                try {
                    Thread.sleep(Math.min(30000L, 2000L << (intento - 2)));
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    throw new IOException("Descarga interrumpida", ie);
                }
            }
            URL url = new URL(baseUrl + ruta);
            HttpURLConnection con = (HttpURLConnection) url.openConnection();
            ponerClave(con, token);
            con.setConnectTimeout(10000);
            con.setReadTimeout(30000);
            if (descargado > 0) {
                con.setRequestProperty("Range", "bytes=" + descargado + "-");
            }
            try {
                int codigo = con.getResponseCode();
                boolean continuar;
                if (codigo == 206 && descargado > 0) {
                    continuar = true;
                    String rango = con.getHeaderField("Content-Range"); // "bytes 100-199/511958"
                    int barra = rango != null ? rango.lastIndexOf('/') : -1;
                    if (barra >= 0 && !rango.endsWith("*")) {
                        total = Long.parseLong(rango.substring(barra + 1).trim());
                    }
                } else if (codigo == 200) {
                    continuar = false;
                    descargado = 0;
                    total = con.getContentLengthLong();
                } else if (codigo == 416 && total > 0 && descargado == total) {
                    return; // ya estaba completo: el corte fue justo al final.
                } else if (codigo >= 400 && codigo < 500 && codigo != 408 && codigo != 429) {
                    throw new ErrorDefinitivo("HTTP " + codigo);
                } else {
                    throw new IOException("HTTP " + codigo);
                }
                try (OutputStream bruto = abridor.abrir(continuar);
                     BufferedOutputStream salida = new BufferedOutputStream(bruto, 65536);
                     InputStream in = con.getInputStream()) {
                    byte[] trozo = new byte[65536];
                    int leidos;
                    while ((leidos = in.read(trozo)) != -1) {
                        salida.write(trozo, 0, leidos);
                        descargado += leidos;
                        if (progreso != null) {
                            progreso.onProgreso(descargado, total);
                        }
                    }
                    salida.flush();
                }
                // Mismo control que descargarAFichero: un corte puede hacer
                // que read() devuelva -1 antes de tiempo sin lanzar nada.
                if (total > 0 && descargado != total) {
                    throw new IOException("Descarga incompleta: " + descargado + " de " + total + " bytes");
                }
                if (descargado == 0) {
                    throw new IOException("Descarga vacía: el servidor no envió ningún byte");
                }
                return;
            } catch (ErrorDefinitivo e) {
                throw e;
            } catch (IOException e) {
                ultimoError = e;
            } finally {
                con.disconnect();
            }
        }
        throw new IOException("Falló tras " + MAX_INTENTOS_DESCARGA + " intentos: "
                + (ultimoError != null ? ultimoError.getMessage() : "error desconocido"), ultimoError);
    }

    /**
     * Mensaje de error legible: el "detail" (jarjar/FastAPI) o "message"
     * (PHP) del cuerpo JSON si lo hay, p.ej. "No hay ninguna consola con ese
     * identificador"; si no, "HTTP <código>".
     */
    static String detalleError(HttpURLConnection con, int codigo) {
        try (InputStream es = con.getErrorStream()) {
            if (es != null) {
                JSONObject o = new JSONObject(leerTodo(es));
                if (o.has("detail")) {
                    return o.getString("detail");
                }
                if (o.has("message")) {
                    return o.getString("message");
                }
            }
        } catch (Exception ignored) {
            // cuerpo no JSON: se queda el código
        }
        return "HTTP " + codigo;
    }

    /** POST de formulario (application/x-www-form-urlencoded) que devuelve el cuerpo de la respuesta. */
    public static String postFormulario(String baseUrl, String token, String ruta, Map<String, String> campos)
            throws IOException {
        URL url = new URL(baseUrl + ruta);
        StringBuilder cuerpo = new StringBuilder();
        for (Map.Entry<String, String> c : campos.entrySet()) {
            if (cuerpo.length() > 0) {
                cuerpo.append('&');
            }
            cuerpo.append(URLEncoder.encode(c.getKey(), "UTF-8")).append('=')
                    .append(URLEncoder.encode(c.getValue(), "UTF-8"));
        }
        HttpURLConnection con = (HttpURLConnection) url.openConnection();
        ponerClave(con, token);
        con.setRequestMethod("POST");
        con.setDoOutput(true);
        con.setConnectTimeout(8000);
        con.setReadTimeout(30000);
        con.setRequestProperty("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8");
        try {
            try (OutputStream out = con.getOutputStream()) {
                out.write(cuerpo.toString().getBytes(StandardCharsets.UTF_8));
            }
            int codigo = con.getResponseCode();
            if (codigo < 200 || codigo >= 300) {
                throw new IOException(detalleError(con, codigo));
            }
            try (InputStream in = con.getInputStream()) {
                return leerTodo(in);
            }
        } finally {
            con.disconnect();
        }
    }

    /**
     * Sube un fichero (multipart, streaming directo desde disco: las memory
     * cards de PS2 y los zips de guardados de Switch/GameCube pesan varios MB)
     * junto con los campos de texto indicados. Va a un java.io.File real
     * (MANAGE_EXTERNAL_STORAGE), no a un Uri de SAF como los ROMs.
     */
    public static void subirFichero(String baseUrl, String token, String ruta, Map<String, String> campos,
                                    String nombreFichero, File local) throws IOException {
        String boundary = "----RomLibrary" + UUID.randomUUID();
        URL url = new URL(baseUrl + ruta);
        HttpURLConnection con = (HttpURLConnection) url.openConnection();
        ponerClave(con, token);
        con.setRequestMethod("POST");
        con.setDoOutput(true);
        con.setChunkedStreamingMode(1 << 16);
        con.setConnectTimeout(8000);
        con.setReadTimeout(120000);
        con.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        try {
            try (OutputStream out = con.getOutputStream()) {
                for (Map.Entry<String, String> c : campos.entrySet()) {
                    escribirCampoTexto(out, boundary, c.getKey(), c.getValue());
                }
                escribirCabeceraFichero(out, boundary, "fichero", nombreFichero);
                try (InputStream in = new FileInputStream(local)) {
                    copiar(in, out);
                }
                out.write("\r\n".getBytes(StandardCharsets.UTF_8));
                out.write(("--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
            }
            int codigo = con.getResponseCode();
            if (codigo < 200 || codigo >= 300) {
                throw new IOException(detalleError(con, codigo));
            }
        } finally {
            con.disconnect();
        }
    }

    private static void escribirCampoTexto(OutputStream out, String boundary, String nombre, String valor) throws IOException {
        String parte = "--" + boundary + "\r\n"
                + "Content-Disposition: form-data; name=\"" + nombre + "\"\r\n\r\n"
                + valor + "\r\n";
        out.write(parte.getBytes(StandardCharsets.UTF_8));
    }

    private static void escribirCabeceraFichero(OutputStream out, String boundary, String campo, String nombreFichero) throws IOException {
        String cabecera = "--" + boundary + "\r\n"
                + "Content-Disposition: form-data; name=\"" + campo + "\"; filename=\"" + nombreFichero + "\"\r\n"
                + "Content-Type: application/octet-stream\r\n\r\n";
        out.write(cabecera.getBytes(StandardCharsets.UTF_8));
    }

    private static void copiar(InputStream in, OutputStream out) throws IOException {
        byte[] trozo = new byte[65536];
        int leidos;
        while ((leidos = in.read(trozo)) != -1) {
            out.write(trozo, 0, leidos);
        }
    }

    private static String leerTodo(InputStream in) throws IOException {
        if (in == null) {
            return "";
        }
        try (InputStream cerrar = in) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            copiar(cerrar, buffer);
            return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
        }
    }
}
