package es.jarjar.romlibrary;

import android.app.Activity;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.util.List;

final class JuegoAdapter extends ArrayAdapter<Juego> {

    private final Activity activity;
    private final String baseUrl;
    private final String token;
    private final AccionInstalarListener listener;

    JuegoAdapter(Activity activity, List<Juego> juegos, String baseUrl, String token, AccionInstalarListener listener) {
        super(activity, 0, juegos);
        this.activity = activity;
        this.baseUrl = baseUrl;
        this.token = token;
        this.listener = listener;
    }

    @Override
    public View getView(int position, View convertView, ViewGroup parent) {
        View fila = convertView;
        if (fila == null) {
            fila = LayoutInflater.from(activity).inflate(R.layout.row_juego, parent, false);
        }
        Juego juego = getItem(position);

        ImageView caratula = fila.findViewById(R.id.imagenCaratula);
        TextView nombre = fila.findViewById(R.id.textoNombreJuego);
        TextView detalle = fila.findViewById(R.id.textoDetalleJuego);
        Button botonInstalar = fila.findViewById(R.id.botonInstalar);

        nombre.setText(juego.nombreFichero);
        String tamano = (juego.tamanoBytes / 1024 / 1024 >= 1)
                ? (juego.tamanoBytes / 1024 / 1024) + " MB"
                : (juego.tamanoBytes / 1024) + " KB";
        String etiquetaRoles = juego.etiquetaRolesSwitch();
        detalle.setText(android.text.TextUtils.concat((juego.instaladoLocal ? "✓ EN EL DISPOSITIVO · " : "") + tamano
                + (etiquetaRoles.isEmpty() ? "" : " · " + etiquetaRoles)
                + (juego.origen != null ? " · de " + juego.libreria + (juego.confianza >= 70 ? " ✔" : "") : "")
                + (juego.fuenteExterna != null ? " · enlace del autor" : ""),
                juego.etiquetaIdiomaYEstado()));

        botonInstalar.setText(juego.instaladoLocal ? "Desinstalar" : "Instalar");
        botonInstalar.setOnClickListener(v -> listener.onAccionInstalar(juego));
        botonInstalar.setOnLongClickListener(v -> {
            listener.onElegirIdioma(juego);
            return true;
        });
        botonInstalar.setVisibility(juego.descargando || juego.enCola ? View.GONE : View.VISIBLE);
        pintarProgreso(fila, juego);
        fila.setTag(juego);

        ImageLoader.cargarEn(caratula, juego.base(baseUrl), juego.clave(token), juego.coverUrl);

        return fila;
    }

    /**
     * Barra de progreso de una fila ya pintada. MainActivity la llama
     * directamente en las filas visibles mientras se descarga, en vez de
     * notifyDataSetChanged() varias veces por segundo: eso rehacía todas
     * las filas, cortaba los toques a medias y movía la selección.
     */
    static void pintarProgreso(View fila, Juego juego) {
        ProgressBar barra = fila.findViewById(R.id.barraProgreso);
        barra.setVisibility(juego.descargando || juego.enCola ? View.VISIBLE : View.GONE);
        if (juego.bytesTotal > 0) {
            barra.setIndeterminate(false);
            barra.setProgress((int) Math.min(1000, (1000L * juego.bytesDescargados) / juego.bytesTotal));
        } else {
            barra.setIndeterminate(true);
        }
    }
}
