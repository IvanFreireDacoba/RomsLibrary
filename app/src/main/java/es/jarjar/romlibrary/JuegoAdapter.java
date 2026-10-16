package es.jarjar.romlibrary;

import android.app.Activity;
import android.view.KeyEvent;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.ImageView;
import android.widget.ListView;
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
        ProgressBar barraProgreso = fila.findViewById(R.id.barraProgreso);

        nombre.setText(juego.nombreFichero);
        String tamano = (juego.tamanoBytes / 1024 / 1024 >= 1)
                ? (juego.tamanoBytes / 1024 / 1024) + " MB"
                : (juego.tamanoBytes / 1024) + " KB";
        String etiquetaRoles = juego.etiquetaRolesSwitch();
        detalle.setText((juego.instaladoLocal ? "✓ EN EL DISPOSITIVO · " : "") + tamano
                + (etiquetaRoles.isEmpty() ? "" : " · " + etiquetaRoles)
                + (juego.origen != null ? " · de " + juego.libreria + (juego.confianza >= 70 ? " ✔" : "") : "")
                + (juego.fuenteExterna != null ? " · enlace del autor" : "")
                + juego.etiquetaIdiomaYEstado());

        botonInstalar.setText(juego.instaladoLocal ? "Desinstalar" : "Instalar");
        botonInstalar.setOnClickListener(v -> listener.onAccionInstalar(juego));
        botonInstalar.setOnLongClickListener(v -> {
            listener.onElegirIdioma(juego);
            return true;
        });
        botonInstalar.setOnKeyListener((v, keyCode, event) -> navegar1D((ListView) parent, position, keyCode, event));
        // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
        final int posLog = position;
        botonInstalar.setOnFocusChangeListener((v, tieneFoco) -> FocusDebugLogger.log("LISTA fila " + posLog + " (" + juego.nombreFichero + ") foco=" + tieneFoco));
        boolean ocupado = juego.descargando || juego.enCola;
        botonInstalar.setVisibility(ocupado ? View.GONE : View.VISIBLE);
        barraProgreso.setVisibility(ocupado ? View.VISIBLE : View.GONE);
        if (juego.bytesTotal > 0) {
            barraProgreso.setIndeterminate(false);
            barraProgreso.setProgress((int) Math.min(1000, (1000L * juego.bytesDescargados) / juego.bytesTotal));
        } else {
            barraProgreso.setIndeterminate(true);
        }

        ImageLoader.cargarEn(caratula, juego.base(baseUrl), juego.clave(token), juego.coverUrl);

        return fila;
    }

    /**
     * Misma técnica que {@link JuegoGridAdapter#navegar2D}: la navegación por
     * D-pad nativa de ListView entre botones de filas recicladas no es
     * fiable en este dispositivo (confirmado con log real de foco), así que
     * se calcula el vecino a mano y se le pide foco directamente. Al salir
     * por arriba de la primera fila, vuelve al botón de alternar vista de la
     * barra de filtros en vez de dejar el comportamiento por defecto.
     */
    private boolean navegar1D(ListView lista, int position, int keyCode, KeyEvent event) {
        if (event.getAction() != KeyEvent.ACTION_DOWN) {
            return false;
        }
        int destino;
        if (keyCode == KeyEvent.KEYCODE_DPAD_DOWN) {
            destino = position + 1;
        } else if (keyCode == KeyEvent.KEYCODE_DPAD_UP) {
            destino = position - 1;
        } else {
            return false;
        }
        // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
        FocusDebugLogger.log("navegar1D: desde=" + position + " destino=" + destino
                + " primeraVisible=" + lista.getFirstVisiblePosition() + " hijosVisibles=" + lista.getChildCount());
        if (destino < 0) {
            View volver = activity.findViewById(R.id.botonAlternarVista);
            boolean ok = volver != null && volver.requestFocus();
            FocusDebugLogger.log("navegar1D: salir arriba, requestFocus=" + ok);
            return ok;
        }
        if (destino >= getCount()) {
            return false;
        }
        int indiceEnPantalla = destino - lista.getFirstVisiblePosition();
        if (indiceEnPantalla < 0 || indiceEnPantalla >= lista.getChildCount()) {
            // BUG REAL ENCONTRADO (log real: 28 casos, la misma pulsación
            // repitiéndose varias veces sin efecto): la fila destino aún no
            // tiene View real (reciclaje de ListView), así que requestFocus()
            // no tiene nada a lo que aplicarse -- de ahí que hiciera falta
            // repetir la pulsación hasta que el autoscroll manual (stick
            // derecho) la trajera a pantalla por su cuenta. Se salta primero
            // por posición (setSelection, sin animar -> layout inmediato) y
            // se pide el foco en el frame siguiente, cuando la fila ya está
            // realmente inflada.
            FocusDebugLogger.log("navegar1D: destino fuera de pantalla, indiceEnPantalla=" + indiceEnPantalla + " -> saltando por posición");
            moverFocoTrasSaltoDePosicion(lista, destino);
            return true;
        }
        View filaDestino = lista.getChildAt(indiceEnPantalla);
        View botonDestino = filaDestino.findViewById(R.id.botonInstalar);
        boolean ok = botonDestino != null && botonDestino.getVisibility() == View.VISIBLE && botonDestino.requestFocus();
        FocusDebugLogger.log("navegar1D: botonEncontrado=" + (botonDestino != null)
                + " visible=" + (botonDestino != null && botonDestino.getVisibility() == View.VISIBLE) + " requestFocus=" + ok);
        if (ok) {
            centrarEnViewport(lista, destino, filaDestino.getHeight());
        }
        return ok;
    }

    /**
     * Salto inmediato (sin animar) a la posición para forzar layout ya
     * mismo, y en el siguiente frame -- cuando la fila ya está enlazada de
     * verdad -- pedir el foco sobre su botón y centrar con scroll suave.
     */
    private void moverFocoTrasSaltoDePosicion(ListView lista, int destino) {
        lista.setSelection(destino);
        lista.post(() -> {
            int indice = destino - lista.getFirstVisiblePosition();
            if (indice < 0 || indice >= lista.getChildCount()) {
                FocusDebugLogger.log("navegar1D (post salto): fila " + destino + " sigue sin View tras setSelection");
                return;
            }
            View fila = lista.getChildAt(indice);
            View boton = fila.findViewById(R.id.botonInstalar);
            boolean ok = boton != null && boton.getVisibility() == View.VISIBLE && boton.requestFocus();
            FocusDebugLogger.log("navegar1D (post salto): fila " + destino + " requestFocus=" + ok);
            if (ok) {
                centrarEnViewport(lista, destino, fila.getHeight());
            }
        });
    }

    /** Autoscroll tipo "carrusel": mantiene la fila con foco centrada verticalmente en el viewport. */
    private void centrarEnViewport(ListView lista, int posicion, int alturaFila) {
        if (alturaFila <= 0) {
            return;
        }
        int offset = Math.max(0, (lista.getHeight() - alturaFila) / 2);
        lista.smoothScrollToPositionFromTop(posicion, offset, 150);
    }
}
