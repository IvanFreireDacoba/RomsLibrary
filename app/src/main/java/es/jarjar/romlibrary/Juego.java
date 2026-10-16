package es.jarjar.romlibrary;

import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.style.ForegroundColorSpan;
import android.text.style.StyleSpan;

import java.util.ArrayList;
import java.util.List;

final class Juego {
    final String sistema;
    final String nombreFichero;
    final long tamanoBytes;
    String coverUrl; // ruta relativa a base(): "/APIs/RomLibraryApp?accion=caratula&..."
    final boolean esGrupo; // juego de varios ficheros (PS1 cue+bin, multidisco; o Switch base+DLC/updates) en su propia subcarpeta en jarjar.
    final String tipoGrupo; // null, o "switch_base_dlc" -- ver grupo.json en el backend. Null para multidisco (sigue usando .m3u, sin roles).
    final int rolesBase;
    final int rolesUpdate;
    final int rolesDlc;
    // Juegos de otra librería pública: id del servicio de origen (null =
    // librería propia), su nombre (se enseña) y su URL. Se descargan
    // DIRECTAMENTE de ella, por su API pública y sin clave: nuestro servidor
    // nunca hace de intermediario con juegos ajenos.
    String origen;
    String origenUrl;
    String libreria;
    String sha;            // SHA-256 de la versión que ofrece la librería (null si aún no lo ha calculado)
    // Juego que su autor publica en su web (traducciones...): se descarga de
    // ahí (cuenta para sus estadísticas) y, si falla o no coincide el SHA, de la librería.
    String fuenteExterna;
    String shaEnlace;      // SHA de lo que da ese enlace (el del juego, o el del .zip en RPG Maker)
    // Versiones del mismo juego en distintos idiomas: comparten título y la
    // librería las manda por separado (cada una con sus propias versiones).
    // En la lista se ve una sola entrada (ver MainActivity.agruparIdiomas).
    String titulo;
    List<String> idiomas = new ArrayList<>();
    List<Juego> variantes = new ArrayList<>(); // todas las del mismo título, esta incluida
    // Marcado como archivo corrupto en la librería (a mano o por la verificación): se avisa antes de instalar.
    boolean corrupto;
    // Solo lo sospecha la verificación (naranja) o lo ha comprobado el administrador (rojo).
    boolean corruptoAutomatico;
    String motivoCorrupto;
    int confianza;         // de la librería de origen, según el índice (70+ = de confianza)
    boolean instaladoLocal;
    boolean descargando; // estado de UI puro (no viene del servidor): sustituye el botón por una barra de progreso.
    boolean enCola; // esperando su turno en la cola de descargas (ver MainActivity.solicitarDescarga).
    volatile long bytesDescargados;
    volatile long bytesTotal = -1; // -1 = desconocido todavía: la barra se muestra indeterminada hasta el primer progreso real.

    Juego(String sistema, String nombreFichero, long tamanoBytes, String coverUrl, boolean esGrupo,
          String tipoGrupo, int rolesBase, int rolesUpdate, int rolesDlc) {
        this.sistema = sistema;
        this.nombreFichero = nombreFichero;
        this.tamanoBytes = tamanoBytes;
        this.coverUrl = coverUrl;
        this.esGrupo = esGrupo;
        this.tipoGrupo = tipoGrupo;
        this.rolesBase = rolesBase;
        this.rolesUpdate = rolesUpdate;
        this.rolesDlc = rolesDlc;
    }

    /** Servidor del que se descarga este juego: el suyo de origen o el nuestro. */
    String base(String nuestro) {
        return origenUrl != null ? origenUrl : nuestro;
    }

    /** Nuestra clave de acceso solo viaja a nuestro servidor, nunca a otras librerías. */
    String clave(String nuestra) {
        return origenUrl != null ? null : nuestra;
    }

    /** "ES", "EN FR"... ("" si la librería no lo sabe). */
    String etiquetaIdiomas() {
        return String.join(" ", idiomas).toUpperCase();
    }

    /** Texto extra para las tarjetas: idioma, otros idiomas disponibles y aviso de archivo corrupto. */
    CharSequence etiquetaIdiomaYEstado() {
        SpannableStringBuilder sb = new SpannableStringBuilder();
        String idioma = etiquetaIdiomas();
        if (!idioma.isEmpty()) {
            sb.append(" · ").append(idioma);
        }
        if (variantes.size() > 1) {
            sb.append(" (+").append(String.valueOf(variantes.size() - 1)).append(variantes.size() == 2 ? " idioma" : " idiomas").append(")");
        }
        if (corrupto) {
            sb.append(" · ");
            int inicio = sb.length();
            sb.append("⚠ ARCHIVO CORRUPTO");
            sb.setSpan(new ForegroundColorSpan(colorCorrupto()), inicio, sb.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            sb.setSpan(new StyleSpan(android.graphics.Typeface.BOLD), inicio, sb.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
        }
        return sb;
    }

    /** Naranja si solo lo sospecha la verificación automática; rojo si está comprobado. */
    int colorCorrupto() {
        return corruptoAutomatico ? 0xFFFF9F2E : 0xFFFF4D4D;
    }

    /** Orden de preferencia: sin marcar como corrupto primero; luego español, inglés, otro idioma conocido y desconocido. */
    int preferencia() {
        int idioma = idiomas.contains("es") ? 0 : idiomas.contains("en") ? 1 : idiomas.isEmpty() ? 3 : 2;
        return (corrupto ? 10 : 0) + idioma;
    }

    /** Etiqueta corta tipo "Base+2 Updates+1 DLC" para las tarjetas de la galería, o "" si no aplica. */
    String etiquetaRolesSwitch() {
        if (!"switch_base_dlc".equals(tipoGrupo)) {
            return "";
        }
        StringBuilder sb = new StringBuilder();
        if (rolesBase > 0) {
            sb.append("Base");
        }
        if (rolesUpdate > 0) {
            if (sb.length() > 0) sb.append("+");
            sb.append(rolesUpdate).append(rolesUpdate == 1 ? " Update" : " Updates");
        }
        if (rolesDlc > 0) {
            if (sb.length() > 0) sb.append("+");
            sb.append(rolesDlc).append(" DLC");
        }
        return sb.toString();
    }
}
