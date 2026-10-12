package es.jarjar.romlibrary;

import android.app.Activity;
import android.view.KeyEvent;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.GridView;
import android.widget.ImageView;
import android.widget.ProgressBar;
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
        ProgressBar barraProgreso = celda.findViewById(R.id.barraProgreso);

        nombre.setText(juego.nombreFichero);
        String tamano = (juego.tamanoBytes / 1024 / 1024 >= 1)
                ? (juego.tamanoBytes / 1024 / 1024) + " MB"
                : (juego.tamanoBytes / 1024) + " KB";
        String etiquetaRoles = juego.etiquetaRolesSwitch();
        detalle.setText(juego.sistema + " · " + tamano + (etiquetaRoles.isEmpty() ? "" : " · " + etiquetaRoles)
                + (juego.origen != null ? " · de " + juego.libreria : ""));

        botonInstalar.setText(juego.instaladoLocal ? "Desinstalar" : "Instalar");
        botonInstalar.setOnClickListener(v -> listener.onAccionInstalar(juego));
        botonInstalar.setOnKeyListener((v, keyCode, event) -> navegar2D((GridView) parent, position, keyCode, event));
        // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
        final int posLog = position;
        botonInstalar.setOnFocusChangeListener((v, tieneFoco) -> FocusDebugLogger.log("GRID celda " + posLog + " (" + juego.nombreFichero + ") foco=" + tieneFoco));
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

        return celda;
    }

    /**
     * GridView no hace navegación 2D fiable por D-pad de forma nativa entre
     * botones dentro de sus celdas (problema conocido de las AdapterView con
     * reciclaje de vistas, no solo de esta app). Se calcula el vecino a mano
     * por aritmética de posición/columnas y se le pide foco directamente --
     * solo cubre el caso (habitual aquí) de que el vecino ya esté visible en
     * pantalla; si no lo está, se deja el comportamiento por defecto.
     */
    private boolean navegar2D(GridView grid, int position, int keyCode, KeyEvent event) {
        if (event.getAction() != KeyEvent.ACTION_DOWN) {
            return false;
        }
        int columnas = grid.getNumColumns();
        if (columnas <= 0) {
            return false;
        }
        int destino;
        switch (keyCode) {
            case KeyEvent.KEYCODE_DPAD_LEFT:
                destino = position - 1;
                break;
            case KeyEvent.KEYCODE_DPAD_RIGHT:
                destino = position + 1;
                break;
            case KeyEvent.KEYCODE_DPAD_UP:
                destino = position - columnas;
                break;
            case KeyEvent.KEYCODE_DPAD_DOWN:
                destino = position + columnas;
                break;
            default:
                return false;
        }
        // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
        FocusDebugLogger.log("navegar2D: desde=" + position + " destino=" + destino + " columnas=" + columnas
                + " primeraVisible=" + grid.getFirstVisiblePosition() + " hijosVisibles=" + grid.getChildCount());
        if (destino < 0) {
            if (keyCode == KeyEvent.KEYCODE_DPAD_UP) {
                // Sale por arriba de la primera fila de celdas: vuelve a la
                // barra de filtros en vez de dejar el comportamiento nativo
                // (poco fiable en este dispositivo, confirmado con log real).
                View volver = activity.findViewById(R.id.botonAlternarVista);
                boolean ok = volver != null && volver.requestFocus();
                FocusDebugLogger.log("navegar2D: salir arriba, requestFocus=" + ok);
                return ok;
            }
            return false;
        }
        if (destino >= getCount()) {
            return false;
        }
        boolean esHorizontal = keyCode == KeyEvent.KEYCODE_DPAD_LEFT || keyCode == KeyEvent.KEYCODE_DPAD_RIGHT;
        if (esHorizontal && destino / columnas != position / columnas) {
            return false; // no saltar de fila al llegar al borde izq/dcha
        }
        int indiceEnPantalla = destino - grid.getFirstVisiblePosition();
        if (indiceEnPantalla < 0 || indiceEnPantalla >= grid.getChildCount()) {
            // BUG REAL ENCONTRADO (log real: 28 casos entre lista y
            // cuadrícula, la misma pulsación repitiéndose sin efecto hasta
            // que un scroll manual traía la fila a pantalla): la celda
            // destino aún no tiene View real (reciclaje de GridView), así
            // que requestFocus() no tenía nada a lo que aplicarse. Se salta
            // primero por posición (setSelection, sin animar -> layout
            // inmediato) y se pide el foco en el frame siguiente, cuando la
            // celda ya está realmente inflada.
            FocusDebugLogger.log("navegar2D: destino fuera de pantalla, indiceEnPantalla=" + indiceEnPantalla + " -> saltando por posición");
            moverFocoTrasSaltoDePosicion(grid, destino);
            return true;
        }
        View celdaDestino = grid.getChildAt(indiceEnPantalla);
        View botonDestino = celdaDestino.findViewById(R.id.botonInstalar);
        boolean ok = botonDestino != null && botonDestino.getVisibility() == View.VISIBLE && botonDestino.requestFocus();
        FocusDebugLogger.log("navegar2D: botonEncontrado=" + (botonDestino != null)
                + " visible=" + (botonDestino != null && botonDestino.getVisibility() == View.VISIBLE) + " requestFocus=" + ok);
        if (ok) {
            centrarEnViewport(grid, destino, celdaDestino.getHeight());
        }
        return ok;
    }

    /**
     * Salto inmediato (sin animar) a la posición para forzar layout ya
     * mismo, y en el siguiente frame -- cuando la celda ya está enlazada de
     * verdad -- pedir el foco sobre su botón y centrar con scroll suave.
     */
    private void moverFocoTrasSaltoDePosicion(GridView grid, int destino) {
        grid.setSelection(destino);
        grid.post(() -> {
            int indice = destino - grid.getFirstVisiblePosition();
            if (indice < 0 || indice >= grid.getChildCount()) {
                FocusDebugLogger.log("navegar2D (post salto): celda " + destino + " sigue sin View tras setSelection");
                return;
            }
            View celda = grid.getChildAt(indice);
            View boton = celda.findViewById(R.id.botonInstalar);
            boolean ok = boton != null && boton.getVisibility() == View.VISIBLE && boton.requestFocus();
            FocusDebugLogger.log("navegar2D (post salto): celda " + destino + " requestFocus=" + ok);
            if (ok) {
                centrarEnViewport(grid, destino, celda.getHeight());
            }
        });
    }

    /** Autoscroll tipo "carrusel": mantiene la fila de celdas con foco centrada verticalmente en el viewport. */
    private void centrarEnViewport(GridView grid, int posicion, int alturaCelda) {
        if (alturaCelda <= 0) {
            return;
        }
        int offset = Math.max(0, (grid.getHeight() - alturaCelda) / 2);
        grid.smoothScrollToPositionFromTop(posicion, offset, 150);
    }
}
