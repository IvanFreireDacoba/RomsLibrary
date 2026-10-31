package es.jarjar.romlibrary;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.Socket;
import java.net.URL;
import java.net.UnknownHostException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SNIHostName;
import javax.net.ssl.SNIServerName;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * DNS resiliente para sund.es.
 *
 * En los dispositivos con Tailscale y la opción "Usar DNS de Tailscale"
 * activada, el resolutor del sistema (100.100.100.100) no contesta y sund.es
 * no resuelve: la app (librería, federación y, sobre todo, las partidas) deja
 * de conectar aunque la red funcione. La app no puede cambiar ese ajuste del
 * dispositivo, así que resuelve sund.es por su cuenta.
 *
 * Funcionamiento: para sund.es y sus subdominios se intenta PRIMERO el
 * resolutor del sistema (comportamiento idéntico cuando el DNS va bien). Si
 * falla, se resuelve por DNS-over-HTTPS contra IPs literales (Cloudflare
 * 1.1.1.1 y Google 8.8.8.8, que no necesitan DNS para contactarlas) y, como
 * último recurso, con una IP pública fija conocida. Con la IP ya resuelta se
 * conecta por IP pero manteniendo la cabecera Host, el SNI y la validación del
 * certificado en el host REAL, de modo que TLS sigue siendo correcto (mismo
 * certificado, misma comprobación de nombre) y nginx enruta bien. Lo único que
 * cambia frente a la ruta normal es a qué dirección se abre el socket.
 *
 * El resto de hosts (CDN de Steam, fuentes externas de los autores, etc.) no
 * se tocan: ruta normal, para no secuestrar el DNS de terceros.
 */
final class ResolucionDns {

    private ResolucionDns() {
    }

    /**
     * IP pública de sund.es (Contabo) como ÚLTIMO recurso si ni el sistema ni
     * el DoH responden. PUEDE CAMBIAR: es solo una red de seguridad; la vía
     * principal de respaldo es el DoH, que siempre da la IP actual.
     */
    private static final String IP_RESPALDO = "161.97.115.1";

    // Cache positiva de la IP resuelta por DoH, para no repetir la consulta en
    // cada petición de una misma ráfaga (p. ej. abrir la librería lanza varias).
    private static final long CACHE_MS = 5 * 60 * 1000L;
    private static volatile String ipCacheHost;
    private static volatile String ipCacheValor;
    private static volatile long ipCacheExpira;

    /**
     * Abre una conexión al spec dado. Para sund.es, con resolución resiliente;
     * para cualquier otro host, exactamente como antes.
     */
    static HttpURLConnection abrir(String spec) throws IOException {
        URL url = new URL(spec);
        String host = url.getHost();
        if (!esHostPropio(host) || sistemaResuelve(host)) {
            return (HttpURLConnection) url.openConnection();   // ruta normal, sin cambios
        }
        // El sistema no resuelve sund.es (DNS roto): resolver por nuestra cuenta.
        String ip = resolverPorDoH(host);
        if (ip == null) {
            // Ni DoH ni respaldo: que falle como siempre (UnknownHostException).
            return (HttpURLConnection) url.openConnection();
        }
        return conexionPorIp(url, host, ip);
    }

    /** Solo sund.es y subdominios: no secuestramos el DNS de hosts ajenos. */
    static boolean esHostPropio(String host) {
        if (host == null) {
            return false;
        }
        host = host.toLowerCase();
        return host.equals("sund.es") || host.endsWith(".sund.es");
    }

    private static boolean sistemaResuelve(String host) {
        try {
            InetAddress.getByName(host);
            return true;
        } catch (UnknownHostException e) {
            return false;
        }
    }

    private static String resolverPorDoH(String host) {
        long ahora = System.currentTimeMillis();
        String cacheHost = ipCacheHost;
        String cacheValor = ipCacheValor;
        if (host.equals(cacheHost) && cacheValor != null && ahora < ipCacheExpira) {
            return cacheValor;
        }
        // IPs LITERALES: no necesitan DNS para contactarlas, y su certificado
        // incluye la IP en el SAN, así que la validación TLS estándar es correcta.
        String[] servidores = {
            "https://1.1.1.1/dns-query?type=A&name=",   // Cloudflare
            "https://8.8.8.8/resolve?type=A&name="      // Google
        };
        for (String base : servidores) {
            try {
                String ip = consultaDoH(base + host);
                if (ip != null) {
                    guardarCache(host, ip, ahora);
                    return ip;
                }
            } catch (Exception ignorado) {
                // probar el siguiente servidor DoH
            }
        }
        // Último recurso: IP fija conocida (puede quedar obsoleta).
        guardarCache(host, IP_RESPALDO, ahora);
        return IP_RESPALDO;
    }

    private static void guardarCache(String host, String ip, long ahora) {
        ipCacheHost = host;
        ipCacheValor = ip;
        ipCacheExpira = ahora + CACHE_MS;
    }

    /**
     * Una consulta DoH con respuesta JSON (API json de Cloudflare y Google).
     * Devuelve la primera IP de un registro A, o null. Se conecta a una IP
     * literal, así que NO necesita DNS.
     */
    private static String consultaDoH(String spec) throws IOException {
        HttpsURLConnection con = (HttpsURLConnection) new URL(spec).openConnection();
        con.setConnectTimeout(5000);
        con.setReadTimeout(5000);
        con.setRequestProperty("Accept", "application/dns-json");
        try {
            if (con.getResponseCode() != 200) {
                return null;
            }
            String cuerpo;
            try (InputStream in = con.getInputStream()) {
                ByteArrayOutputStream buffer = new ByteArrayOutputStream();
                byte[] trozo = new byte[4096];
                int n;
                while ((n = in.read(trozo)) != -1) {
                    buffer.write(trozo, 0, n);
                }
                cuerpo = new String(buffer.toByteArray(), StandardCharsets.UTF_8);
            }
            try {
                JSONArray respuestas = new JSONObject(cuerpo).optJSONArray("Answer");
                if (respuestas == null) {
                    return null;
                }
                for (int i = 0; i < respuestas.length(); i++) {
                    JSONObject r = respuestas.getJSONObject(i);
                    if (r.optInt("type") == 1) {   // 1 = registro A (IPv4)
                        String ip = r.optString("data", "");
                        if (!ip.isEmpty()) {
                            return ip;
                        }
                    }
                }
                return null;
            } catch (JSONException e) {
                return null;   // respuesta DoH no esperada
            }
        } finally {
            con.disconnect();
        }
    }

    /**
     * Conexión a la IP literal pero con Host, SNI y validación del certificado
     * fijados al host REAL (sund.es): nginx enruta por la cabecera Host, el
     * servidor devuelve el certificado correcto por SNI y se valida contra
     * sund.es. Así TLS sigue siendo seguro; lo único que cambia es la IP del
     * socket.
     */
    private static HttpURLConnection conexionPorIp(URL url, String host, String ip) throws IOException {
        URL urlIp = new URL(url.getProtocol(), ip, url.getPort(), url.getFile());
        HttpURLConnection con = (HttpURLConnection) urlIp.openConnection();
        con.setRequestProperty("Host", host);
        if (con instanceof HttpsURLConnection) {
            HttpsURLConnection sec = (HttpsURLConnection) con;
            sec.setSSLSocketFactory(new FabricaSniSocket(sec.getSSLSocketFactory(), host));
            final HostnameVerifier porDefecto = HttpsURLConnection.getDefaultHostnameVerifier();
            sec.setHostnameVerifier((hostname, session) -> porDefecto.verify(host, session));
        }
        return con;
    }

    /**
     * Fábrica de sockets TLS que fuerza el SNI al host real aunque el socket se
     * conecte a una IP literal. Delega todo lo demás en la fábrica por defecto,
     * de modo que la cadena de confianza y las suites se validan igual que
     * siempre.
     */
    private static final class FabricaSniSocket extends SSLSocketFactory {
        private final SSLSocketFactory base;
        private final String hostReal;

        FabricaSniSocket(SSLSocketFactory base, String hostReal) {
            this.base = base;
            this.hostReal = hostReal;
        }

        private Socket conSni(Socket s) {
            if (s instanceof SSLSocket) {
                SSLSocket ssl = (SSLSocket) s;
                SSLParameters p = ssl.getSSLParameters();
                List<SNIServerName> nombres = new ArrayList<>();
                nombres.add(new SNIHostName(hostReal));
                p.setServerNames(nombres);
                ssl.setSSLParameters(p);
            }
            return s;
        }

        // La que usa HttpsURLConnection: envuelve en TLS el socket ya conectado
        // a la IP. Pasamos el host REAL para el SNI/validación, no la IP.
        @Override
        public Socket createSocket(Socket s, String host, int port, boolean autoClose) throws IOException {
            return conSni(base.createSocket(s, hostReal, port, autoClose));
        }

        @Override
        public Socket createSocket(String host, int port) throws IOException {
            return conSni(base.createSocket(host, port));
        }

        @Override
        public Socket createSocket(String host, int port, InetAddress localHost, int localPort) throws IOException {
            return conSni(base.createSocket(host, port, localHost, localPort));
        }

        @Override
        public Socket createSocket(InetAddress host, int port) throws IOException {
            return conSni(base.createSocket(host, port));
        }

        @Override
        public Socket createSocket(InetAddress address, int port, InetAddress localAddress, int localPort) throws IOException {
            return conSni(base.createSocket(address, port, localAddress, localPort));
        }

        @Override
        public String[] getDefaultCipherSuites() {
            return base.getDefaultCipherSuites();
        }

        @Override
        public String[] getSupportedCipherSuites() {
            return base.getSupportedCipherSuites();
        }
    }
}
