package es.jarjar.romlibrary;

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
