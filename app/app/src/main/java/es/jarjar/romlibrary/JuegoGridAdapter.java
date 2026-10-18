package es.jarjar.romlibrary;

import android.app.Activity;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.TextView;

import java.util.List;

/** Misma información que {@link JuegoAdapter}, en celdas cuadradas para la vista de cuadrícula. */
final class JuegoGridAdapter extends ArrayAdapter<Juego> {

    private final Activity activity;
    private final String baseUrl;
    private final String token;
    private final AccionInstalarListener listener;

    JuegoGridAdapter(Activity activity, List<Juego> juegos, String baseUrl, String token, AccionInstalarListener listener) {
        super(activity, 0, juegos);
        this.activity = activity;
        this.baseUrl = baseUrl;
        this.token = token;
        this.listener = listener;
    }

    @Override
    public View getView(int position, View convertView, ViewGroup parent) {
        View celda = convertView;
        if (celda == null) {
            celda = LayoutInflater.from(activity).inflate(R.layout.grid_juego, parent, false);
        }
        Juego juego = getItem(position);

        ImageView caratula = celda.findViewById(R.id.imagenCaratula);
        TextView nombre = celda.findViewById(R.id.textoNombreJuego);
        TextView detalle = celda.findViewById(R.id.textoDetalleJuego);
        Button botonInstalar = celda.findViewById(R.id.botonInstalar);

        nombre.setText(juego.nombreFichero);
        String tamano = (juego.tamanoBytes / 1024 / 1024 >= 1)
                ? (juego.tamanoBytes / 1024 / 1024) + " MB"
                : (juego.tamanoBytes / 1024) + " KB";
        String etiquetaRoles = juego.etiquetaRolesSwitch();
        detalle.setText(android.text.TextUtils.concat(juego.sistema + " · " + tamano + (etiquetaRoles.isEmpty() ? "" : " · " + etiquetaRoles)
                + (juego.origen != null ? " · de " + juego.libreria + (juego.confianza >= 70 ? " ✔" : "") : "")
                + (juego.fuenteExterna != null ? " · enlace del autor" : ""),
                juego.etiquetaIdiomaYEstado()));

        botonInstalar.setText(juego.instaladoLocal ? "Desinstalar" : juego.descargable ? "Instalar" : "No disponible");
        botonInstalar.setEnabled(juego.instaladoLocal || juego.descargable);
        botonInstalar.setOnClickListener(v -> listener.onAccionInstalar(juego));
        botonInstalar.setOnLongClickListener(v -> {
            listener.onElegirIdioma(juego);
            return true;
        });
        botonInstalar.setVisibility(juego.descargando || juego.enCola ? View.GONE : View.VISIBLE);
        JuegoAdapter.pintarProgreso(celda, juego);
        celda.setTag(juego);

        ImageLoader.cargarEn(caratula, juego.base(baseUrl), juego.clave(token), juego.coverUrl);

        return celda;
    }
}
