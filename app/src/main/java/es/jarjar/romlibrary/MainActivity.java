package es.jarjar.romlibrary;

import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.Menu;
import android.view.MenuItem;
import android.view.MotionEvent;
import android.view.View;
import android.widget.AbsListView;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.GridView;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.documentfile.provider.DocumentFile;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;


/**
 * Pantalla única: o bien pide la carpeta ROMs (si todavía no hay una válida),
 * o bien muestra directamente la Biblioteca_ (galería con todos los juegos de
 * todos los sistemas, igual que en la web) -- sin ningún paso intermedio de
 * "elegir sistema".
 *
 * La app SOLO habla con el dominio público de SunD (nunca con jarjar
 * directo) a través de /APIs/RomLibraryApp -- ese endpoint PHP en Contabo es
 * quien llama a jarjar por Tailscale server-to-server. Así Tailscale queda
 * limitado a Contabo<->jarjar y la app funciona desde cualquier conexión a
 * internet normal, sin Tailscale en el propio dispositivo.
 */
public class MainActivity extends BaseActivity {

    static final String PREFS = "romlibrary_prefs";
    static final String CLAVE_ARBOL_ROMS = "arbol_roms_uri";

    private static final String RUTA_API = Cuenta.RUTA_API;

    private static final int PETICION_ELEGIR_CARPETA = 1001;

    private SharedPreferences prefs;
    // Servidor configurado (Cuenta: URL + clave) e identidad de esta consola en él.
    private Cuenta cuenta;
    private String baseUrl;
    private String token;

    private View grupoElegirCarpeta;
    private View grupoBiblioteca;
    private TextView textoEstado;
    private ListView listaJuegos;
    private GridView cuadriculaJuegos;
    private Spinner spinnerFiltroSistema;
    private Spinner spinnerFiltroInstalado;
    // Librería: la personal (la del servidor configurado) o también las de
    // otras librerías públicas, si el servidor es público.
    private static final String[] LIBRERIAS = {"personal", "todas", "otras"};
    private Spinner spinnerFiltroLibreria;
    private EditText campoFiltroNombre;
    private Button botonAlternarVista;
    private boolean vistaCuadricula = false;

    private List<Juego> juegos = new ArrayList<>();
    private JuegoAdapter adapter;
    private JuegoGridAdapter adapterGrid;
    private List<String> sistemasDisponibles = new ArrayList<>();

    // Cola de descargas: como mucho una descarga real a la vez (el ancho de
    // banda hacia jarjar es limitado y variable -- ver investigación de
    // velocidad), pero el usuario puede ir marcando más juegos mientras
    // espera en vez de quedarse bloqueado en la interfaz.
    private final java.util.ArrayDeque<Juego> colaDescargas = new java.util.ArrayDeque<>();
    private boolean descargaEnCurso = false;


    // ---------- Scroll suave con el stick derecho del mando ----------
    // Independiente de la navegación de foco por D-pad (esa mueve el foco
    // entre botones de instalar/desinstalar); esto es scroll de cámara.
    private static final float DEADZONE_STICK = 0.2f;
    private static final int INTERVALO_SCROLL_MS = 16;
    private static final float PIXELS_MAX_POR_TICK = 24f;
    private final Handler manejadorScroll = new Handler(Looper.getMainLooper());

    // ---------- Debounce del buscador por nombre ----------
    private static final long RETRASO_BUSQUEDA_MS = 350;
    private final Handler manejadorBusqueda = new Handler(Looper.getMainLooper());
    private final Runnable busquedaDebounced = this::cargarJuegos;
    private float ejeScrollActual = 0f;
    private boolean scrollEnMarcha = false;
    private final Runnable tickScroll = new Runnable() {
        @Override
        public void run() {
            if (Math.abs(ejeScrollActual) < DEADZONE_STICK) {
                scrollEnMarcha = false;
                liberarListaTrasScroll();
                return;
            }
            AbsListView listaActiva = vistaCuadricula ? cuadriculaJuegos : listaJuegos;
            int distancia = (int) (ejeScrollActual * PIXELS_MAX_POR_TICK);
            listaActiva.smoothScrollBy(distancia, INTERVALO_SCROLL_MS);
            manejadorScroll.postDelayed(this, INTERVALO_SCROLL_MS);
        }
    };

    /**
     * BUG REAL ENCONTRADO (dato del usuario: tras usar el stick para
     * scrollear, los botones Instalar/Desinstalar dejan de responder tanto
     * al tacto como al D-pad hasta reiniciar la app): smoothScrollBy() deja
     * al AbsListView (ListView/GridView) en un estado interno de scroll/fling
     * que normalmente cierra un dedo real al soltar (ACTION_UP) -- como el
     * scroll por stick nunca pasa por un toque real, ese estado se queda a
     * medias, y mientras dura, onInterceptTouchEvent() se cree que sigue en
     * mitad de un gesto de scroll y no deja que el toque (ni, indirectamente,
     * el foco de D-pad) llegue a los hijos. Se fuerza el cierre limpio con un
     * ACTION_CANCEL sintético, exactamente lo que haría un dedo real al
     * soltar el stick.
     */
    private void liberarListaTrasScroll() {
        AbsListView listaActiva = vistaCuadricula ? cuadriculaJuegos : listaJuegos;
        long ahora = android.os.SystemClock.uptimeMillis();
        MotionEvent cancelacion = MotionEvent.obtain(ahora, ahora, MotionEvent.ACTION_CANCEL, 0f, 0f, 0);
        listaActiva.onTouchEvent(cancelacion);
        cancelacion.recycle();
        // Log real (ver focus_log.txt de hoy): mientras el stick scrollea, el
        // foco oscila varias veces por segundo entre el propio contenedor y
        // la fila que en ese instante ocupa la posición en pantalla que tenía
        // el foco antes de reciclar la vista -- efecto secundario conocido de
        // AbsListView + hijos focusables + smoothScrollBy. Si la última
        // oscilación deja el foco en el CONTENEDOR (no en ningún botón), el
        // D-pad ya no tiene a quién mandarle las flechas -- navegar1D/2D solo
        // están enganchados a los botones, no al contenedor. Se redirige al
        // primer botón visible en ese caso.
        boolean focoEnContenedor = listaActiva.isFocused();
        if (focoEnContenedor) {
            enfocarPrimeraFilaOCelda();
        }
        // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
        FocusDebugLogger.log("liberarListaTrasScroll: ACTION_CANCEL enviado a "
                + (vistaCuadricula ? "cuadriculaJuegos" : "listaJuegos") + " focoEnContenedor=" + focoEnContenedor);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        // Android 13+: sin este permiso la notificación de progreso de las
        // descargas en segundo plano no se ve (la descarga sigue igual).
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 77);
        }
        cuenta = Cuenta.cargar(this);
        baseUrl = cuenta.url;
        token = cuenta.clave;

        grupoElegirCarpeta = findViewById(R.id.grupoElegirCarpeta);
        grupoBiblioteca = findViewById(R.id.grupoBiblioteca);
        textoEstado = findViewById(R.id.textoEstado);
        listaJuegos = findViewById(R.id.listaJuegos);
        cuadriculaJuegos = findViewById(R.id.cuadriculaJuegos);
        spinnerFiltroSistema = findViewById(R.id.spinnerFiltroSistema);
        spinnerFiltroInstalado = findViewById(R.id.spinnerFiltroInstalado);
        spinnerFiltroLibreria = findViewById(R.id.spinnerFiltroLibreria);
        ArrayAdapter<String> adapterLibreria = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item,
                new String[]{"Personal", "Todas", "Otras"});
        adapterLibreria.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinnerFiltroLibreria.setAdapter(adapterLibreria);
        campoFiltroNombre = findViewById(R.id.campoFiltroNombre);
        botonAlternarVista = findViewById(R.id.botonAlternarVista);

        ArrayAdapter<String> adapterFiltroInstalado = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item,
                new String[]{"Todos", "Instalados", "No instalados"});
        adapterFiltroInstalado.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinnerFiltroInstalado.setAdapter(adapterFiltroInstalado);

        // SOLO DEPURACIÓN TEMPORAL -- diagnóstico real del D-pad sin ADB (ver
        // FocusDebugLogger). Quitar todo esto en cuanto se resuelva el bug.
        spinnerFiltroSistema.setOnFocusChangeListener((v, tieneFoco) -> FocusDebugLogger.log("spinnerFiltroSistema foco=" + tieneFoco));
        campoFiltroNombre.setOnFocusChangeListener((v, tieneFoco) -> FocusDebugLogger.log("campoFiltroNombre foco=" + tieneFoco));
        botonAlternarVista.setOnFocusChangeListener((v, tieneFoco) -> FocusDebugLogger.log("botonAlternarVista foco=" + tieneFoco));
        listaJuegos.setOnFocusChangeListener((v, tieneFoco) -> FocusDebugLogger.log("listaJuegos (contenedor) foco=" + tieneFoco));
        cuadriculaJuegos.setOnFocusChangeListener((v, tieneFoco) -> FocusDebugLogger.log("cuadriculaJuegos (contenedor) foco=" + tieneFoco));

        // Deja que el foco de D-pad/mando entre en los botones Instalar/Desinstalar
        // de cada fila (si no, el comportamiento por defecto de ListView impide
        // que el foco aterrice en el botón en vez de en la fila entera). GridView
        // no tiene este método -- no es un descuido, AbsListView (la superclase
        // común) no lo declara, solo ListView lo añade -- comprobado con el
        // compilador tras un intento fallido de llamarlo también en GridView.
        listaJuegos.setItemsCanFocus(true);

        findViewById(R.id.botonElegirCarpetaInicial).setOnClickListener(v -> lanzarSelectorCarpeta());

        // El mantener pulsado no era fiable en el dispositivo del usuario -- el
        // instalar/desinstalar es un botón explícito siempre visible en la
        // propia tarjeta (ver JuegoAdapter/JuegoGridAdapter). La app NUNCA
        // borra ROMs de jarjar -- eso es exclusivo de /staff/romlibrary en la
        // web -- así que un toque normal en el resto de la fila/celda no hace
        // nada.

        botonAlternarVista.setOnClickListener(v -> {
            vistaCuadricula = !vistaCuadricula;
            aplicarVistaSeleccionada();
        });
        // Punto de entrada explícito por D-pad desde CUALQUIERA de los 3
        // controles de la fila de filtros hacia la lista/cuadrícula: el
        // buscador de foco geométrico por defecto de Android no siempre
        // encuentra el primer botón de una fila reciclada (mismo problema de
        // fondo que ya resolvió navegar2D() en la cuadrícula, aquí para el
        // salto inicial "de arriba hacia abajo").
        View.OnKeyListener bajarALaListaConDpad = (v, keyCode, event) -> {
            // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
            FocusDebugLogger.log("bajarALaListaConDpad: origen=" + v.getClass().getSimpleName()
                    + " keyCode=" + keyCode + " action=" + event.getAction());
            if (event.getAction() == KeyEvent.ACTION_DOWN && keyCode == KeyEvent.KEYCODE_DPAD_DOWN) {
                return enfocarPrimeraFilaOCelda();
            }
            return false;
        };
        spinnerFiltroSistema.setOnKeyListener(bajarALaListaConDpad);
        spinnerFiltroInstalado.setOnKeyListener(bajarALaListaConDpad);
        spinnerFiltroLibreria.setOnKeyListener(bajarALaListaConDpad);
        spinnerFiltroLibreria.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            private boolean primeraVez = true;

            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                if (primeraVez) {
                    primeraVez = false; // la selección inicial la dispara el propio Spinner al montarse
                    return;
                }
                cargarJuegos();
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });
        campoFiltroNombre.setOnKeyListener(bajarALaListaConDpad);
        botonAlternarVista.setOnKeyListener(bajarALaListaConDpad);

        spinnerFiltroInstalado.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                // Filtro puramente local (instaladoLocal ya está calculado):
                // no hace falta volver a pedir la lista al servidor.
                aplicarFiltrosYActualizarAdaptadores();
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });

        spinnerFiltroSistema.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                cargarJuegos();
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });
        // BUG REAL ENCONTRADO (dato del usuario: el teclado se cierra en cada
        // pulsación): cargarJuegos() hace una petición de red y, al terminar,
        // reemplaza los adaptadores y fuerza el foco dentro de la lista
        // (enfocarPrimeraFilaOCelda) para que el D-pad pueda entrar -- si eso
        // pasa en CADA tecla, le quita el foco al EditText de golpe y el
        // teclado se cierra. Ahora se espera una pausa real de escritura
        // (debounce) antes de refrescar, y al refrescar NO se toca el foco si
        // sigue siendo el propio campo de búsqueda el que lo tiene.
        campoFiltroNombre.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence s, int start, int count, int after) {
            }

            @Override
            public void onTextChanged(CharSequence s, int start, int before, int count) {
            }

            @Override
            public void afterTextChanged(Editable s) {
                manejadorBusqueda.removeCallbacks(busquedaDebounced);
                manejadorBusqueda.postDelayed(busquedaDebounced, RETRASO_BUSQUEDA_MS);
            }
        });
        campoFiltroNombre.setOnEditorActionListener((v, actionId, event) -> {
            boolean esEnter = event != null && event.getKeyCode() == KeyEvent.KEYCODE_ENTER
                    && event.getAction() == KeyEvent.ACTION_DOWN;
            if (actionId == android.view.inputmethod.EditorInfo.IME_ACTION_SEARCH
                    || actionId == android.view.inputmethod.EditorInfo.IME_ACTION_DONE
                    || esEnter) {
                manejadorBusqueda.removeCallbacks(busquedaDebounced);
                cargarJuegos();
                return true;
            }
            return false;
        });


        AppUpdater.comprobar(this, baseUrl, token);
        comprobarConectividadYAvisarSiHaceFalta();
        cargarSistemasDisponibles();
        actualizarEstadoInicial();
        comprobarPermisoAlmacenamientoCompleto();
        actualizarSubtitulo();
        if (!cuenta.registrada()) {
            mostrarConfiguracionServidor(true);
        }
    }

    // ---------- Servidor e identidad de la consola ----------

    /** Nombre e identificador de esta consola siempre visibles bajo el título. */
    private void actualizarSubtitulo() {
        if (getActionBar() != null) {
            getActionBar().setSubtitle(cuenta.registrada()
                    ? cuenta.nombre + " · " + cuenta.identificador
                    : "Consola sin configurar");
        }
    }

    /**
     * Servidor (URL + clave de acceso) y nombre de la consola. La primera vez
     * registra la consola y le asigna su identificador XXXXX-XXXXX; después
     * sirve para cambiar de servidor (cada servidor tiene su identificador).
     */
    private void mostrarConfiguracionServidor(boolean primeraVez) {
        LinearLayout formulario = new LinearLayout(this);
        formulario.setOrientation(LinearLayout.VERTICAL);
        int p = Math.round(20 * getResources().getDisplayMetrics().density);
        formulario.setPadding(p, p / 2, p, 0);
        EditText campoUrl = new EditText(this);
        campoUrl.setHint("Servidor, p.ej. https://sund.es");
        campoUrl.setText(cuenta.url);
        campoUrl.setInputType(android.text.InputType.TYPE_TEXT_VARIATION_URI);
        // La clave solo la piden las librerías PRIVADAS (se ve en su gestor web).
        EditText campoClave = new EditText(this);
        campoClave.setHint("Clave de acceso (solo librerías privadas)");
        campoClave.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        campoClave.setText(cuenta.clave);
        EditText campoNombre = new EditText(this);
        campoNombre.setHint("Nombre de esta consola, p.ej. Retroid de Efe");
        if (cuenta.nombre != null) {
            campoNombre.setText(cuenta.nombre);
        }
        TextView ayuda = new TextView(this);
        ayuda.setText("El nombre y el identificador de la consola los verán las consolas con las que sincronices partidas.");
        formulario.addView(campoUrl);
        formulario.addView(campoClave);
        formulario.addView(campoNombre);
        formulario.addView(ayuda);
        AlertDialog dialogo = new AlertDialog.Builder(this)
                .setTitle(primeraVez ? "Configura RomsLibrary" : "Servidor y consola")
                .setView(formulario)
                .setCancelable(!primeraVez)
                .setPositiveButton("Guardar", null)
                .setNegativeButton(primeraVez ? "Más tarde" : "Cancelar", null)
                .create();
        dialogo.setOnShowListener(d -> dialogo.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            String url = campoUrl.getText().toString();
            String clave = campoClave.getText().toString().trim();
            String nombre = campoNombre.getText().toString().trim();
            if (Cuenta.normalizarUrl(url).isEmpty()) {
                Toast.makeText(this, "Escribe la dirección del servidor, p.ej. https://sund.es", Toast.LENGTH_SHORT).show();
                return;
            }
            if (nombre.isEmpty()) {
                Toast.makeText(this, "Ponle un nombre a la consola", Toast.LENGTH_SHORT).show();
                return;
            }
            v.setEnabled(false);
            new Thread(() -> {
                try {
                    Cuenta nueva = Cuenta.configurar(getApplicationContext(), url, clave, nombre);
                    if (cuenta.registrada() && cuenta.url.equals(nueva.url) && !nombre.equals(nueva.nombre)) {
                        // Ya registrada en este servidor: registrar no cambia el nombre, renombrar sí.
                        java.util.Map<String, String> campos = nueva.identidad();
                        campos.put("nombre", nombre);
                        ApiClient.postFormulario(nueva.url, nueva.clave, RUTA_API + "?accion=consola_accion&op=renombrar", campos);
                        Cuenta.guardarNombre(getApplicationContext(), nueva.url, nombre);
                        nueva = Cuenta.cargar(getApplicationContext());
                    }
                    Cuenta lista = nueva;
                    runOnUiThread(() -> {
                        cuenta = lista;
                        baseUrl = lista.url;
                        token = lista.clave;
                        actualizarSubtitulo();
                        dialogo.dismiss();
                        Toast.makeText(this, "Consola " + lista.nombre + " · " + lista.identificador, Toast.LENGTH_LONG).show();
                        cargarSistemasDisponibles();
                        cargarJuegos();
                    });
                } catch (Exception e) {
                    runOnUiThread(() -> {
                        v.setEnabled(true);
                        Toast.makeText(this, "No se pudo conectar: " + e.getMessage(), Toast.LENGTH_LONG).show();
                    });
                }
            }).start();
        }));
        dialogo.show();
    }

    /**
     * Al volver a la app: si otra consola ha pedido sincronizarse con esta,
     * se pregunta aquí (nunca se acepta sola: podría pisar nuestras partidas).
     */
    private boolean preguntandoSolicitud = false;

    private void comprobarSolicitudesPendientes() {
        if (!cuenta.registrada() || preguntandoSolicitud) {
            return;
        }
        Cuenta c = cuenta;
        new Thread(() -> {
            try {
                org.json.JSONObject estado = new org.json.JSONObject(ApiClient.getTexto(c.url, c.clave,
                        RUTA_API + "?accion=consola_estado" + c.consultaIdentidad(), 10000));
                org.json.JSONArray recibidas = estado.getJSONArray("solicitudes_recibidas");
                if (recibidas.length() == 0) {
                    return;
                }
                org.json.JSONObject s = recibidas.getJSONObject(0);
                String nombre = s.getString("nombre");
                String ident = s.getString("identificador");
                String sol = String.valueOf(s.getInt("solicitud"));
                runOnUiThread(() -> {
                    if (preguntandoSolicitud || isFinishing()) {
                        return;
                    }
                    preguntandoSolicitud = true;
                    new AlertDialog.Builder(this)
                            .setTitle("Solicitud de sincronización")
                            .setMessage(nombre + " (" + ident + ") quiere sincronizar sus partidas con esta consola.\n\n"
                                    + "Si aceptas, las dos consolas compartirán las mismas partidas (se queda siempre la más reciente).")
                            .setPositiveButton("Aceptar", (d, w) -> responderSolicitud(c, sol, true, nombre))
                            .setNegativeButton("Rechazar", (d, w) -> responderSolicitud(c, sol, false, nombre))
                            .setNeutralButton("Luego", null)
                            .setOnDismissListener(d -> preguntandoSolicitud = false)
                            .show();
                });
            } catch (Exception ignored) {
                // Sin conexión o servidor antiguo: se volverá a mirar al volver a la app.
            }
        }).start();
    }

    private void responderSolicitud(Cuenta c, String solicitud, boolean aceptar, String nombre) {
        new Thread(() -> {
            try {
                java.util.Map<String, String> campos = c.identidad();
                campos.put("solicitud", solicitud);
                campos.put("aceptar", aceptar ? "true" : "false");
                ApiClient.postFormulario(c.url, c.clave, RUTA_API + "?accion=consola_accion&op=responder", campos);
                runOnUiThread(() -> Toast.makeText(this, aceptar ? "Sincronizada con " + nombre : "Solicitud rechazada",
                        Toast.LENGTH_LONG).show());
                if (aceptar) {
                    ultimaSyncGuardados = -INTERVALO_MIN_SYNC_GUARDADOS_MS; // bajar ya las partidas compartidas
                    runOnUiThread(this::sincronizarGuardadosEnSegundoPlano);
                }
            } catch (Exception e) {
                runOnUiThread(() -> Toast.makeText(this, "Error: " + e.getMessage(), Toast.LENGTH_LONG).show());
            }
        }).start();
    }

    @Override
    protected void onDestroy() {
        manejadorBusqueda.removeCallbacks(busquedaDebounced);
        super.onDestroy();
    }

    // ---------- Permiso de almacenamiento completo (para escribir en ES-DE/) ----------

    /**
     * MANAGE_EXTERNAL_STORAGE no es un permiso runtime normal (no hay diálogo
     * de sistema tipo "Permitir/Denegar") -- solo se puede conceder desde la
     * pantalla de Ajustes dedicada a esa app, así que hay que llevar ahí al
     * usuario con un Intent explícito. Autorizado por el usuario para poder
     * copiar carátulas dentro de ES-DE/downloaded_media/, una carpeta hermana
     * de ROMs/ que el permiso SAF de "Elegir carpeta ROMs" nunca cubre.
     */
    private void comprobarPermisoAlmacenamientoCompleto() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R || Environment.isExternalStorageManager()) {
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("Acceso a todos los archivos")
                .setMessage("Para dejar la carátula ya lista dentro de la carpeta de ES-DE (ES-DE/downloaded_media/), "
                        + "esta app necesita el permiso \"Acceso a todos los archivos\" de Android.")
                .setPositiveButton("Ir a Ajustes", (d, w) -> {
                    try {
                        Intent intent = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                                Uri.parse("package:" + getPackageName()));
                        startActivity(intent);
                    } catch (Exception e) {
                        startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
                    }
                })
                .setNegativeButton("Luego", null)
                .show();
    }

    /**
     * Carpeta de carátulas que ES-DE lee DE VERDAD. ES-DE no guarda su
     * downloaded_media "junto a las ROMs" (suposición anterior, incorrecta:
     * con las ROMs en la SD, la app copiaba la carátula a
     * <SD>/ES-DE/downloaded_media, una carpeta que ES-DE nunca mira, y en
     * pantalla salía la carátula scrapeada vieja de la carpeta buena). La
     * regla real: el ajuste MediaDirectory de es_settings.xml si tiene valor;
     * si está vacío (lo normal), <carpeta de ES-DE>/downloaded_media, y la
     * carpeta de ES-DE en Android es /storage/emulated/0/ES-DE.
     */
    private File carpetaCoversESDE(String sistema) {
        return EsDeIconos.carpetaMedia(sistema, "covers");
    }

    /**
     * Nombre base con el que ES-DE busca la media de un juego. Para un
     * fichero, sin extensión ("Pokémon Quetzal.gba" -> "Pokémon Quetzal").
     * Para una CARPETA, el nombre completo: los grupos de PS1/PS2 se llaman
     * "MediEvil.cue"/"Final Fantasy VII.m3u" para que ES-DE los muestre como
     * un solo juego ("directories interpreted as files"), y en ese caso la
     * documentación de ES-DE dice que la media incluye esa "extensión":
     * covers/MediEvil.cue.jpg. Con covers/MediEvil.jpg no salía nada.
     */
    private String nombreMediaESDE(Juego juego) {
        return juego.esGrupo ? juego.nombreFichero : stemSinExtension(juego.nombreFichero);
    }

    /**
     * Si ES-DE ya tenía media scrapeada para este juego con el nombre de
     * antes ("MediEvil.png" en 3dboxes/, miximages/, videos/...), se renombra
     * al nombre que busca ahora ("MediEvil.cue.png") en TODAS las carpetas de
     * media del sistema, para no perder el scrapeo. No pisa nada existente.
     */
    private void migrarMediaAnteriorESDE(File carpetaSistemaMedia, Juego juego, String nombreMedia) {
        String anterior = stemSinExtension(juego.nombreFichero);
        if (carpetaSistemaMedia == null || anterior.equals(nombreMedia)) {
            return;
        }
        File[] tipos = carpetaSistemaMedia.listFiles(File::isDirectory);
        if (tipos == null) {
            return;
        }
        for (File tipo : tipos) {
            File[] viejos = tipo.listFiles((dir, nombre) -> nombre.startsWith(anterior + ".")
                    && nombre.indexOf('.', anterior.length() + 1) == -1);
            if (viejos == null) {
                continue;
            }
            for (File viejo : viejos) {
                String ext = viejo.getName().substring(anterior.length());
                File nuevo = new File(tipo, nombreMedia + ext);
                if (!nuevo.exists() && viejo.renameTo(nuevo)) {
                    android.media.MediaScannerConnection.scanFile(this,
                            new String[]{viejo.getAbsolutePath(), nuevo.getAbsolutePath()}, null, null);
                }
            }
        }
    }

    private String stemSinExtension(String nombreFichero) {
        int punto = nombreFichero.lastIndexOf('.');
        return punto > 0 ? nombreFichero.substring(0, punto) : nombreFichero;
    }

    /**
     * Copia la MISMA carátula que ya se ve en la UI a
     * ES-DE/downloaded_media/<sistema>/covers/<rom-sin-extension>.<ext> --
     * best-effort: si falta el permiso o algo falla, el juego se instala
     * igual, solo que ES-DE no tendrá la carátula lista de antemano.
     *
     * IMPORTANTE: {@code alTerminar} se llama SIEMPRE (éxito, fallo o
     * ni-siquiera-lo-intenta) y es el único sitio desde el que se debe
     * disparar el refresco de ES-DE -- si se disparara en paralelo en vez de
     * encadenado aquí, cabría la carrera "se refresca ES-DE mientras la
     * carátula todavía se está escribiendo".
     */
    private void copiarCaratulaAESDE(Juego juego, Runnable alTerminar) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R || !Environment.isExternalStorageManager()
                || juego.coverUrl == null) {
            // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
            FocusDebugLogger.log("copiarCaratulaAESDE: NO se intenta (sdk=" + Build.VERSION.SDK_INT
                    + " manageStorage=" + (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && Environment.isExternalStorageManager())
                    + " coverUrl=" + juego.coverUrl + ")");
            alTerminar.run();
            return;
        }
        new Thread(() -> {
            File temporal = null;
            try {
                File carpetaCovers = carpetaCoversESDE(juego.sistema);
                boolean carpetaOk = carpetaCovers.isDirectory() || carpetaCovers.mkdirs();
                // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
                FocusDebugLogger.log("copiarCaratulaAESDE: carpetaCovers=" + carpetaCovers.getAbsolutePath() + " ok=" + carpetaOk);
                if (!carpetaOk) {
                    return;
                }
                // Se descarga primero a un fichero temporal SIN asumir la
                // extensión -- se detecta por los bytes reales (magic number)
                // al terminar. Bug real encontrado: una carátula subida como
                // JPEG real pero nombrada "....png" en el servidor rompía la
                // detección de ES-DE porque confiábamos en el nombre en vez
                // del contenido; esto ya está arreglado también en el
                // servidor (jarjar ahora sniffea la extensión al subir), pero
                // esta comprobación se queda aquí como defensa adicional.
                temporal = File.createTempFile("caratula_esde", null, getCacheDir());
                try (FileOutputStream out = new FileOutputStream(temporal)) {
                    ApiClient.descargarAFichero(baseUrl, token, juego.coverUrl, out);
                }
                String extension = extensionRealDeImagen(temporal);
                // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
                FocusDebugLogger.log("copiarCaratulaAESDE: temporal=" + temporal.length() + " bytes extension=" + extension);
                if (extension == null) {
                    return;
                }
                String nombreMedia = nombreMediaESDE(juego);
                migrarMediaAnteriorESDE(carpetaCovers.getParentFile(), juego, nombreMedia);
                File destino = new File(carpetaCovers, nombreMedia + extension);
                borrarCaratulasConOtraExtension(carpetaCovers, nombreMedia, extension);
                // BUG REAL ENCONTRADO (ficheros de carátula ausentes en TODOS
                // los casos frescos probados, tras el fix de volumen): File.
                // renameTo() usa rename(2), que solo funciona DENTRO del mismo
                // sistema de ficheros. El temporal vive siempre en la caché
                // interna de la app (getCacheDir()); en cuanto carpetaCovers
                // pasó a apuntar a la tarjeta SD (fix de volumen anterior),
                // CUALQUIER renameTo() cruzando de interno a SD falla siempre
                // -- silenciosamente, sin excepción -- así que la carátula
                // nunca llegaba a ningún sitio. Se sustituye por una copia
                // real de bytes (funciona cruzando sistemas de ficheros) y
                // luego se borra el temporal.
                if (!copiarFichero(temporal, destino)) {
                    FocusDebugLogger.log("copiarCaratulaAESDE: copia a " + destino.getAbsolutePath() + " FALLÓ");
                    return;
                }
                FocusDebugLogger.log("copiarCaratulaAESDE: copiada a " + destino.getAbsolutePath());
                temporal.delete();
                temporal = null; // ya se copió y se borró, no volver a borrar en el finally
                // Un fichero creado con java.io.File (MANAGE_EXTERNAL_STORAGE)
                // no entra automáticamente en el índice MediaStore de Android
                // -- si ES-DE consulta media por ahí en vez de listar la
                // carpeta a pelo, el fichero puede quedar "invisible" para
                // siempre (ni un reinicio completo lo arregla, porque no es
                // un problema de proceso/caché de ES-DE sino del índice del
                // sistema). Forzar el escaneo cubre ese caso sin coste real.
                android.media.MediaScannerConnection.scanFile(this,
                        new String[]{destino.getAbsolutePath()}, null, null);
            } catch (Exception e) {
                // Best-effort -- el refresco de ES-DE se intenta igualmente.
                // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
                FocusDebugLogger.log("copiarCaratulaAESDE: excepción " + e);
            } finally {
                if (temporal != null) {
                    temporal.delete();
                }
                alTerminar.run();
            }
        }).start();
    }

    /** Copia bytes de un fichero a otro -- a diferencia de File.renameTo(), funciona cruzando sistemas de ficheros (interno -> SD). */
    private boolean copiarFichero(File origen, File destino) {
        try (java.io.FileInputStream in = new java.io.FileInputStream(origen);
             FileOutputStream out = new FileOutputStream(destino)) {
            byte[] buffer = new byte[65536];
            int leidos;
            while ((leidos = in.read(buffer)) != -1) {
                out.write(buffer, 0, leidos);
            }
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /** Lee los primeros bytes de verdad para saber el formato real, NUNCA se confía en un nombre/extensión declarados. */
    private String extensionRealDeImagen(File fichero) {
        // Bug real encontrado (Ape Escape, WEBP): InputStream.read(byte[])
        // NO garantiza rellenar el array entero de una sola llamada -- con un
        // solo read() de 12 bytes, WEBP (que necesita mirar hasta el byte 11)
        // podía comparar contra posiciones sin leer todavía (0 por defecto),
        // fallando el reconocimiento aunque el fichero fuera un WEBP real.
        // Hace falta leer en bucle hasta rellenar el buffer o llegar a EOF.
        byte[] cabecera = new byte[12];
        int totalLeidos = 0;
        try (java.io.FileInputStream in = new java.io.FileInputStream(fichero)) {
            while (totalLeidos < cabecera.length) {
                int leidos = in.read(cabecera, totalLeidos, cabecera.length - totalLeidos);
                if (leidos == -1) {
                    break;
                }
                totalLeidos += leidos;
            }
        } catch (Exception e) {
            return null;
        }
        if (totalLeidos >= 4 && cabecera[0] == (byte) 0x89 && cabecera[1] == 0x50 && cabecera[2] == 0x4E && cabecera[3] == 0x47) {
            return ".png";
        }
        if (totalLeidos >= 3 && (cabecera[0] & 0xFF) == 0xFF && (cabecera[1] & 0xFF) == 0xD8 && (cabecera[2] & 0xFF) == 0xFF) {
            return ".jpg";
        }
        if (totalLeidos >= 12 && cabecera[0] == 'R' && cabecera[1] == 'I' && cabecera[2] == 'F' && cabecera[3] == 'F'
                && cabecera[8] == 'W' && cabecera[9] == 'E' && cabecera[10] == 'B' && cabecera[11] == 'P') {
            return ".webp";
        }
        return null;
    }

    /** Evita dejar dos carátulas del mismo juego con extensiones distintas si el formato real cambió entre instalaciones. */
    private void borrarCaratulasConOtraExtension(File carpetaCovers, String stem, String extensionValida) {
        File[] candidatos = carpetaCovers.listFiles((dir, nombre) -> nombre.startsWith(stem + ".") && !nombre.endsWith(extensionValida));
        if (candidatos != null) {
            String[] rutas = new String[candidatos.length];
            for (int i = 0; i < candidatos.length; i++) {
                rutas[i] = candidatos[i].getAbsolutePath();
                candidatos[i].delete();
            }
            android.media.MediaScannerConnection.scanFile(this, rutas, null, null);
        }
    }

    /** Simétrico a copiarCaratulaAESDE: al desinstalar la copia local, quita también la carátula que puso esta app. */
    private void borrarCaratulaDeESDE(Juego juego) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R || !Environment.isExternalStorageManager()) {
            return;
        }
        new Thread(() -> {
            File carpetaCovers = carpetaCoversESDE(juego.sistema);
            String stem = nombreMediaESDE(juego);
            File[] candidatos = carpetaCovers.listFiles((dir, nombre) -> nombre.startsWith(stem + "."));
            if (candidatos != null) {
                String[] rutas = new String[candidatos.length];
                for (int i = 0; i < candidatos.length; i++) {
                    rutas[i] = candidatos[i].getAbsolutePath();
                    candidatos[i].delete();
                }
                // Igual que al copiar: sacar también del índice MediaStore la
                // entrada ya borrada, si es que llegó a entrar.
                android.media.MediaScannerConnection.scanFile(this, rutas, null, null);
            }
        }).start();
    }

    private void actualizarEstadoInicial() {
        Uri carpeta = CarpetaRomsUtil.obtenerUriValido(this, prefs);
        if (carpeta == null) {
            grupoElegirCarpeta.setVisibility(View.VISIBLE);
            grupoBiblioteca.setVisibility(View.GONE);
        } else {
            grupoElegirCarpeta.setVisibility(View.GONE);
            grupoBiblioteca.setVisibility(View.VISIBLE);
            cargarJuegos();
        }
    }

    private void aplicarVistaSeleccionada() {
        botonAlternarVista.setText(vistaCuadricula ? "Lista" : "Cuadrícula");
        listaJuegos.setVisibility(vistaCuadricula ? View.GONE : View.VISIBLE);
        cuadriculaJuegos.setVisibility(vistaCuadricula ? View.VISIBLE : View.GONE);
    }

    /**
     * Pone el foco de D-pad directamente en el botón Instalar/Desinstalar de
     * la primera fila/celda visible de la vista activa. Devuelve false (deja
     * el comportamiento por defecto) si todavía no hay ninguna fila real --
     * ej. lista vacía o adaptador aún sin cargar.
     */
    private boolean enfocarPrimeraFilaOCelda() {
        AbsListView listaActiva = vistaCuadricula ? cuadriculaJuegos : listaJuegos;
        int hijos = listaActiva.getChildCount();
        // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
        FocusDebugLogger.log("enfocarPrimeraFilaOCelda: vistaCuadricula=" + vistaCuadricula
                + " hijosVisibles=" + hijos + " isInTouchMode=" + listaActiva.isInTouchMode());
        if (hijos == 0) {
            return false;
        }
        View primeraFila = listaActiva.getChildAt(0);
        View botonInstalar = primeraFila.findViewById(R.id.botonInstalar);
        boolean ok = botonInstalar != null && botonInstalar.requestFocus();
        FocusDebugLogger.log("enfocarPrimeraFilaOCelda: botonInstalarEncontrado=" + (botonInstalar != null) + " requestFocus=" + ok);
        return ok;
    }

    private void lanzarSelectorCarpeta() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        startActivityForResult(intent, PETICION_ELEGIR_CARPETA);
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        getMenuInflater().inflate(R.menu.menu_biblioteca, menu);
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        int id = item.getItemId();
        if (id == R.id.menuCambiarCarpeta) {
            lanzarSelectorCarpeta();
            return true;
        } else if (id == R.id.menuRefrescar) {
            cargarJuegos();
            return true;
        } else if (id == R.id.menuReiniciarEsDe) {
            reiniciarEsDeManual();
            return true;
        } else if (id == R.id.menuSincronizarConsolas || id == R.id.menuConsolasBloqueadas) {
            if (!cuenta.registrada()) {
                mostrarConfiguracionServidor(false);
                return true;
            }
            Intent intent = new Intent(this, ConsolasActivity.class);
            if (id == R.id.menuConsolasBloqueadas) {
                intent.putExtra(ConsolasActivity.EXTRA_MODO, ConsolasActivity.MODO_BLOQUEADAS);
            }
            startActivity(intent);
            return true;
        } else if (id == R.id.menuServidor) {
            mostrarConfiguracionServidor(false);
            return true;
        }
        return super.onOptionsItemSelected(item);
    }

    @Override
    public boolean dispatchGenericMotionEvent(MotionEvent event) {
        if ((event.getSource() & InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK
                && event.getAction() == MotionEvent.ACTION_MOVE
                && grupoBiblioteca.getVisibility() == View.VISIBLE) {
            // BUG REAL ENCONTRADO: este handler consumía SIEMPRE el evento
            // entero (return true) mirando solo los ejes del stick derecho.
            // Android manda TODOS los ejes de un mando en un único MotionEvent
            // por dispositivo -- si la cruceta física y/o el stick izquierdo
            // de esta Retroid llegan como ejes (AXIS_HAT_X/Y, AXIS_X/Y) en vez
            // de como KeyEvent KEYCODE_DPAD_* reales, se estaban descartando
            // aquí sin que nada los tradujera a navegación: de ahí que
            // navegar1D/navegar2D (que solo escuchan KeyEvent real) nunca
            // llegaran a dispararse ni con D-pad ni con el stick izquierdo,
            // en ninguna ronda anterior, independientemente de cualquier
            // arreglo de foco.
            boolean dpadPorEjes = procesarEjesComoDpad(event);
            float y = ejeVerticalStickDerecho(event);
            ejeScrollActual = Math.abs(y) >= DEADZONE_STICK ? y : 0f;
            if (ejeScrollActual != 0f && !scrollEnMarcha) {
                scrollEnMarcha = true;
                // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
                FocusDebugLogger.log("stick: empieza scroll (eje=" + ejeScrollActual + ")");
                manejadorScroll.post(tickScroll);
            }
            return true;
        }
        return super.dispatchGenericMotionEvent(event);
    }

    // ---------- D-pad físico y/o stick izquierdo cuando llegan como ejes analógicos ----------
    private static final float DEADZONE_DPAD_EJES = 0.5f;
    private boolean dpadDisparadoArriba, dpadDisparadoAbajo, dpadDisparadoIzquierda, dpadDisparadoDerecha;

    /**
     * A diferencia del stick derecho (scroll continuo mientras se mantiene),
     * esto debe disparar UNA vez por "empujón" -- como una pulsación real de
     * D-pad -- y no volver a disparar hasta que el eje vuelva a la zona
     * muerta. Cubre a la vez la cruceta física (si llega como AXIS_HAT_X/Y en
     * vez de KeyEvent) y el stick izquierdo (AXIS_X/Y): el que tenga más
     * magnitud en cada instante manda.
     */
    private boolean procesarEjesComoDpad(MotionEvent event) {
        float hatX = event.getAxisValue(MotionEvent.AXIS_HAT_X);
        float hatY = event.getAxisValue(MotionEvent.AXIS_HAT_Y);
        float x = event.getAxisValue(MotionEvent.AXIS_X);
        float y = event.getAxisValue(MotionEvent.AXIS_Y);
        float horizontal = Math.abs(hatX) > Math.abs(x) ? hatX : x;
        float vertical = Math.abs(hatY) > Math.abs(y) ? hatY : y;

        boolean algo = false;
        if (vertical <= -DEADZONE_DPAD_EJES) {
            if (!dpadDisparadoArriba) {
                dpadDisparadoArriba = true;
                enviarTeclaDpadAlFoco(KeyEvent.KEYCODE_DPAD_UP);
            }
            algo = true;
        } else if (vertical >= DEADZONE_DPAD_EJES) {
            if (!dpadDisparadoAbajo) {
                dpadDisparadoAbajo = true;
                enviarTeclaDpadAlFoco(KeyEvent.KEYCODE_DPAD_DOWN);
            }
            algo = true;
        } else {
            dpadDisparadoArriba = false;
            dpadDisparadoAbajo = false;
        }
        if (horizontal <= -DEADZONE_DPAD_EJES) {
            if (!dpadDisparadoIzquierda) {
                dpadDisparadoIzquierda = true;
                enviarTeclaDpadAlFoco(KeyEvent.KEYCODE_DPAD_LEFT);
            }
            algo = true;
        } else if (horizontal >= DEADZONE_DPAD_EJES) {
            if (!dpadDisparadoDerecha) {
                dpadDisparadoDerecha = true;
                enviarTeclaDpadAlFoco(KeyEvent.KEYCODE_DPAD_RIGHT);
            }
            algo = true;
        } else {
            dpadDisparadoIzquierda = false;
            dpadDisparadoDerecha = false;
        }
        return algo;
    }

    /**
     * Convierte el "empujón" de eje en un KeyEvent sintético real y lo manda
     * directo a la vista con foco actual -- exactamente el mismo camino
     * (dispatchKeyEvent -> OnKeyListener) que seguiría una pulsación de
     * hardware real, así que reutiliza navegar1D/navegar2D tal cual sin
     * duplicar su lógica.
     */
    private void enviarTeclaDpadAlFoco(int keyCode) {
        View foco = getCurrentFocus();
        // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
        FocusDebugLogger.log("procesarEjesComoDpad: keyCode=" + keyCode
                + " focoActual=" + (foco == null ? "null" : foco.getClass().getSimpleName()));
        if (foco == null) {
            return;
        }
        long ahora = android.os.SystemClock.uptimeMillis();
        boolean consumido = foco.dispatchKeyEvent(new KeyEvent(ahora, ahora, KeyEvent.ACTION_DOWN, keyCode, 0));
        foco.dispatchKeyEvent(new KeyEvent(ahora, ahora, KeyEvent.ACTION_UP, keyCode, 0));
        // SOLO DEPURACIÓN TEMPORAL -- ver FocusDebugLogger, quitar tras el diagnóstico.
        FocusDebugLogger.log("procesarEjesComoDpad: consumido=" + consumido);
    }

    /**
     * El eje vertical del stick derecho varía según cómo lo reporte el driver
     * del mando (distintos mandos Bluetooth/USB en Android lo mapean como
     * AXIS_RZ, AXIS_Z o incluso AXIS_RY) -- no se ha podido comprobar contra
     * el hardware real de la Retroid, así que se leen los tres candidatos y
     * se usa el que tenga mayor magnitud en ese instante (los ejes que el
     * mando no usa de verdad deberían quedarse a 0).
     */
    private float ejeVerticalStickDerecho(MotionEvent event) {
        float rz = event.getAxisValue(MotionEvent.AXIS_RZ);
        float z = event.getAxisValue(MotionEvent.AXIS_Z);
        float ry = event.getAxisValue(MotionEvent.AXIS_RY);
        float resultado = rz;
        if (Math.abs(z) > Math.abs(resultado)) {
            resultado = z;
        }
        if (Math.abs(ry) > Math.abs(resultado)) {
            resultado = ry;
        }
        return resultado;
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == PETICION_ELEGIR_CARPETA && resultCode == RESULT_OK && data != null) {
            Uri arbol = data.getData();
            if (arbol != null) {
                CarpetaRomsUtil.guardarNuevaCarpeta(this, prefs, arbol);
                Toast.makeText(this, "Carpeta ROMs guardada", Toast.LENGTH_SHORT).show();
                actualizarEstadoInicial();
            }
        }
    }

    // ---------- Conectividad ----------

    private boolean sinServidor() {
        return baseUrl == null || baseUrl.isEmpty();
    }

    private void comprobarConectividadYAvisarSiHaceFalta() {
        if (sinServidor()) {
            return;
        }
        new Thread(() -> {
            try {
                ApiClient.getTexto(baseUrl, token, RUTA_API + "?accion=version", 5000);
            } catch (Exception e) {
                android.util.Log.e("RomLibrary", "Fallo al contactar con " + baseUrl, e);
                String detalle = e.getClass().getSimpleName() + (e.getMessage() != null ? ": " + e.getMessage() : "");
                runOnUiThread(() -> Toast.makeText(this,
                        "No se pudo contactar con el servidor (" + detalle + ")", Toast.LENGTH_LONG).show());
            }
        }).start();
    }

    // ---------- Carga de sistemas y galería ----------

    private void cargarSistemasDisponibles() {
        if (sinServidor()) {
            return;
        }
        new Thread(() -> {
            try {
                String json = ApiClient.getTexto(baseUrl, token, RUTA_API + "?accion=sistemas");
                JSONArray arr = new JSONArray(json);
                List<String> nombres = new ArrayList<>();
                for (int i = 0; i < arr.length(); i++) {
                    nombres.add(arr.getJSONObject(i).getString("sistema"));
                }
                runOnUiThread(() -> {
                    sistemasDisponibles = nombres;
                    List<String> opcionesSpinner = new ArrayList<>();
                    opcionesSpinner.add("Todas las consolas");
                    opcionesSpinner.addAll(nombres);
                    ArrayAdapter<String> spinnerAdapter = new ArrayAdapter<>(
                            this, android.R.layout.simple_spinner_item, opcionesSpinner);
                    spinnerAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
                    spinnerFiltroSistema.setAdapter(spinnerAdapter);
                });
            } catch (Exception ignored) {
            }
        }).start();
    }

    private void cargarJuegos() {
        if (sinServidor()) {
            textoEstado.setText("Elige un servidor en «Servidor y consola» (p.ej. https://sund.es).");
            return;
        }
        String sistemaFiltro = null;
        if (spinnerFiltroSistema.getSelectedItemPosition() > 0 && spinnerFiltroSistema.getSelectedItem() != null) {
            sistemaFiltro = spinnerFiltroSistema.getSelectedItem().toString();
        }
        String texto = campoFiltroNombre.getText().toString().trim();

        List<String> params = new ArrayList<>();
        params.add("accion=juegos");
        params.add("libreria=" + LIBRERIAS[Math.max(0, spinnerFiltroLibreria.getSelectedItemPosition())]);
        if (sistemaFiltro != null) {
            params.add("system=" + Uri.encode(sistemaFiltro));
        }
        if (!texto.isEmpty()) {
            params.add("q=" + Uri.encode(texto));
        }
        String ruta = RUTA_API + "?" + String.join("&", params);

        textoEstado.setText("Cargando...");
        new Thread(() -> {
            try {
                List<Juego> lista = parsearJuegos(ApiClient.getTexto(baseUrl, token, ruta));
                marcarInstaladosLocalmente(lista);
                runOnUiThread(() -> {
                    juegos = lista;
                    aplicarFiltrosYActualizarAdaptadores();
                });
            } catch (Exception e) {
                runOnUiThread(() -> textoEstado.setText("Error: " + e.getMessage()));
            }
        }).start();
    }

    /**
     * Filtro "Instalados"/"No instalados" (junto al de sistema): puramente
     * local, instaladoLocal ya viene calculado por marcarInstaladosLocalmente,
     * así que no hace falta ninguna petición de red para aplicarlo.
     */
    private List<Juego> aplicarFiltroInstalado(List<Juego> lista) {
        int seleccion = spinnerFiltroInstalado.getSelectedItemPosition();
        if (seleccion <= 0) {
            return lista;
        }
        boolean quiereInstalados = seleccion == 1;
        List<Juego> resultado = new ArrayList<>();
        for (Juego juego : lista) {
            if (juego.instaladoLocal == quiereInstalados) {
                resultado.add(juego);
            }
        }
        return resultado;
    }

    /** Reconstruye los adaptadores a partir de `juegos` + el filtro de instalado actual. */
    private void aplicarFiltrosYActualizarAdaptadores() {
        List<Juego> visibles = aplicarFiltroInstalado(juegos);
        textoEstado.setText(visibles.isEmpty() ? "Sin juegos" : visibles.size() + " juego(s)");
        adapter = new JuegoAdapter(this, visibles, baseUrl, token, this::onAccionInstalarDesinstalar);
        adapterGrid = new JuegoGridAdapter(this, visibles, baseUrl, token, this::onAccionInstalarDesinstalar);
        listaJuegos.setAdapter(adapter);
        cuadriculaJuegos.setAdapter(adapterGrid);
        aplicarVistaSeleccionada();
        // El foco inicial por defecto de Android aterriza en el primer
        // focusable de arriba (el spinner de filtro), no dentro de la lista
        // -- sin esto, el mando se queda atrapado en la fila de filtros y
        // nunca "baja" a los botones de instalar/desinstalar. Se fuerza
        // explícito en cuanto la vista activa ya tiene filas/celdas reales.
        // PERO no si quien tiene el foco ahora mismo es el propio buscador
        // -- si no, cada refresco (incluido el debounced tras escribir)
        // le robaría el foco al EditText y cerraría el teclado de golpe.
        (vistaCuadricula ? cuadriculaJuegos : listaJuegos).post(() -> {
            if (getCurrentFocus() != campoFiltroNombre) {
                enfocarPrimeraFilaOCelda();
            }
        });
    }

    private static List<Juego> parsearJuegos(String json) throws Exception {
        JSONArray arr = new JSONArray(json);
        List<Juego> lista = new ArrayList<>();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.getJSONObject(i);
            JSONObject roles = o.isNull("roles") ? null : o.optJSONObject("roles");
            Juego juego = new Juego(
                    o.getString("system"),
                    o.getString("filename"),
                    o.getLong("sizeBytes"),
                    o.isNull("coverUrl") ? null : o.getString("coverUrl"),
                    o.optBoolean("esGrupo", false),
                    o.isNull("tipoGrupo") ? null : o.optString("tipoGrupo", null),
                    roles == null ? 0 : roles.optInt("base", 0),
                    roles == null ? 0 : roles.optInt("update", 0),
                    roles == null ? 0 : roles.optInt("dlc", 0));
            juego.origen = o.isNull("origen") || !o.has("origen") ? null : o.getString("origen");
            juego.libreria = o.optString("libreria", "");
            lista.add(juego);
        }
        return lista;
    }

    // ---------- Guardados en segundo plano ----------

    /**
     * Sube los guardados de TODOS los juegos instalados (no solo al
     * desinstalar -- ver GuardadosSync). Se pide la lista completa sin
     * filtros, independiente de lo que esté mostrando la UI. Estático para
     * que el límite de frecuencia sobreviva a la recreación de la Activity
     * (rotación, volver de ES-DE...).
     */
    private static final long INTERVALO_MIN_SYNC_GUARDADOS_MS = 2 * 60 * 1000;
    private static long ultimaSyncGuardados = -INTERVALO_MIN_SYNC_GUARDADOS_MS;
    private static final AtomicBoolean syncGuardadosEnCurso = new AtomicBoolean(false);

    @Override
    protected void onResume() {
        super.onResume();
        cuenta = Cuenta.cargar(this); // por si se renombró en "Sincronizar consolas"
        actualizarSubtitulo();
        sincronizarGuardadosEnSegundoPlano();
        comprobarSolicitudesPendientes();
        completarIconosEsDeEnSegundoPlano();
    }

    private static final AtomicBoolean iconosEsDeEnCurso = new AtomicBoolean(false);

    /** Carátulas de apps, emuladores y Steam que falten en ES-DE (ver EsDeIconos). */
    private void completarIconosEsDeEnSegundoPlano() {
        Uri arbol = CarpetaRomsUtil.obtenerUriValido(this, prefs);
        if (arbol == null || !iconosEsDeEnCurso.compareAndSet(false, true)) {
            return;
        }
        Context contextoApp = getApplicationContext();
        new Thread(() -> {
            try {
                int creadas = EsDeIconos.completar(contextoApp, arbol);
                if (creadas > 0) {
                    runOnUiThread(() -> Toast.makeText(this, "ES-DE: " + creadas
                            + (creadas == 1 ? " carátula nueva" : " carátulas nuevas") + " de apps/Steam", Toast.LENGTH_SHORT).show());
                }
            } finally {
                iconosEsDeEnCurso.set(false);
            }
        }, "iconos-esde").start();
    }

    private void sincronizarGuardadosEnSegundoPlano() {
        long ahora = SystemClock.elapsedRealtime();
        if (ahora - ultimaSyncGuardados < INTERVALO_MIN_SYNC_GUARDADOS_MS || !syncGuardadosEnCurso.compareAndSet(false, true)) {
            return;
        }
        ultimaSyncGuardados = ahora;
        Context contextoApp = getApplicationContext();
        new Thread(() -> {
            try {
                List<Juego> todos = parsearJuegos(ApiClient.getTexto(baseUrl, token, RUTA_API + "?accion=juegos&libreria=todas"));
                marcarInstaladosLocalmente(todos);
                List<Juego> instalados = new ArrayList<>();
                for (Juego juego : todos) {
                    if (juego.instaladoLocal) {
                        instalados.add(juego);
                    }
                }
                GuardadosSync.sincronizarInstalados(contextoApp, Cuenta.cargar(contextoApp), instalados);
            } catch (Exception e) {
                GuardadosSync.log("ERROR en la sincronización en segundo plano: " + e);
            } finally {
                syncGuardadosEnCurso.set(false);
            }
        }).start();
    }

    private void marcarInstaladosLocalmente(List<Juego> lista) {
        Uri arbolUri = CarpetaRomsUtil.obtenerUriValido(this, prefs);
        DocumentFile raiz = arbolUri != null ? DocumentFile.fromTreeUri(this, arbolUri) : null;
        Map<String, DocumentFile> carpetasPorSistema = new HashMap<>();
        for (Juego juego : lista) {
            if ("rpgmaker".equals(juego.sistema)) {
                // Siempre en almacenamiento interno, independiente de que
                // haya (o no) una carpeta ROMs SAF elegida.
                juego.instaladoLocal = carpetaRpgmakerInterno(juego.nombreFichero).isDirectory();
                continue;
            }
            if (raiz == null) {
                continue;
            }
            DocumentFile carpetaSistema = carpetasPorSistema.computeIfAbsent(juego.sistema, raiz::findFile);
            DocumentFile local = carpetaSistema != null ? carpetaSistema.findFile(juego.nombreFichero) : null;
            // Un fichero de 0 bytes es una descarga que falló a medias (pasó
            // con Zelda Minish Cap): cuenta como NO instalado, para que el
            // botón ofrezca volver a descargarlo (descargar() ya borra el
            // existente antes de crear el nuevo).
            juego.instaladoLocal = local != null && (local.isDirectory() || local.length() > 0);
        }
    }

    private void refrescarAdaptadores() {
        if (adapter != null) {
            adapter.notifyDataSetChanged();
        }
        if (adapterGrid != null) {
            adapterGrid.notifyDataSetChanged();
        }
    }

    // ---------- Botón instalar/desinstalar de la tarjeta ----------

    private void onAccionInstalarDesinstalar(Juego juego) {
        if (juego.instaladoLocal) {
            eliminarCopiaLocal(juego);
        } else {
            solicitarDescarga(juego);
        }
    }

    /**
     * Punto de entrada real para instalar: si no hay ninguna descarga en
     * curso, empieza ya; si la hay, se encola y le toca cuando le toque. Así
     * el usuario puede seguir marcando juegos sin esperar a que termine el
     * actual, pero nunca hay más de una descarga real compitiendo por el
     * mismo ancho de banda limitado hacia jarjar.
     */
    private void solicitarDescarga(Juego juego) {
        if (juego.descargando || juego.enCola) {
            return;
        }
        if (descargaEnCurso) {
            juego.enCola = true;
            colaDescargas.add(juego);
            refrescarAdaptadores();
            Toast.makeText(this, "En cola (" + colaDescargas.size() + "): " + juego.nombreFichero, Toast.LENGTH_SHORT).show();
        } else {
            descargar(juego);
        }
    }

    /** Llamado al terminar cada descarga (éxito o error): arranca la siguiente en cola, si hay. */
    private void procesarSiguienteEnCola() {
        descargaEnCurso = false;
        Juego siguiente = colaDescargas.poll();
        if (siguiente != null) {
            siguiente.enCola = false;
            descargar(siguiente);
        } else {
            // Cola vacía: ya no hace falta mantener el proceso vivo en segundo plano.
            DescargaService.detener(this);
        }
    }

    private void eliminarCopiaLocal(Juego juego) {
        if ("rpgmaker".equals(juego.sistema)) {
            File carpetaJuego = carpetaRpgmakerInterno(juego.nombreFichero);
            if (!carpetaJuego.isDirectory()) {
                return;
            }
            // Sin GuardadosSync (JoiPlay tiene su propio formato, fuera de
            // alcance) ni carátula de ES-DE que borrar -- mismas exclusiones
            // que en la instalación.
            new Thread(() -> {
                boolean borrado = borrarRecursivo(carpetaJuego);
                runOnUiThread(() -> {
                    if (borrado) {
                        juego.instaladoLocal = false;
                        refrescarAdaptadores();
                        Toast.makeText(this, "Copia local eliminada", Toast.LENGTH_SHORT).show();
                    }
                });
            }).start();
            return;
        }
        Uri arbolUri = CarpetaRomsUtil.obtenerUriValido(this, prefs);
        if (arbolUri == null) {
            return;
        }
        DocumentFile raiz = DocumentFile.fromTreeUri(this, arbolUri);
        DocumentFile carpetaSistema = raiz == null ? null : raiz.findFile(juego.sistema);
        DocumentFile fichero = carpetaSistema == null ? null : carpetaSistema.findFile(juego.nombreFichero);
        if (fichero == null) {
            return;
        }
        // Subir el guardado es ahora una llamada de red (best-effort, nunca
        // bloquea el borrado del ROM) -- ya no puede ir en el hilo principal.
        new Thread(() -> {
            GuardadosSync.subirAntesDeDesinstalar(this, cuenta, juego);
            boolean borrado = fichero.delete();
            runOnUiThread(() -> {
                if (borrado) {
                    juego.instaladoLocal = false;
                    refrescarAdaptadores();
                    borrarCaratulaDeESDE(juego);
                    Toast.makeText(this, "Copia local eliminada", Toast.LENGTH_SHORT).show();
                }
            });
        }).start();
    }

    // ---------- Descarga ----------

    private void descargar(Juego juego) {
        // Los juegos de RPG Maker van SIEMPRE en almacenamiento interno,
        // independientemente de qué carpeta ROMs SAF haya elegido el usuario
        // para el resto de sistemas -- JoiPlay y su RTP viven en rutas fijas
        // de almacenamiento interno público, y ya vimos con ES-DE que la SD
        // trae problemas de permisos que aquí no queremos arrastrar.
        if ("rpgmaker".equals(juego.sistema)) {
            descargarRpgmakerInterno(juego);
            return;
        }
        Uri arbolUri = CarpetaRomsUtil.obtenerUriValido(this, prefs);
        if (arbolUri == null) {
            Toast.makeText(this, "Primero elige la carpeta ROMs", Toast.LENGTH_LONG).show();
            return;
        }
        textoEstado.setText("Descargando " + juego.nombreFichero + "...");
        descargaEnCurso = true;
        DescargaService.iniciar(this, "Descargando " + juego.nombreFichero);
        juego.descargando = true;
        juego.bytesDescargados = 0;
        juego.bytesTotal = -1;
        refrescarAdaptadores();
        new Thread(() -> {
            // Fuera del try para poder borrarlo en el catch si la descarga se
            // corta a medias (ver ApiClient.descargarAFichero: ahora compara
            // bytes reales contra Content-Length y lanza si no coincide) --
            // sin esto, un fichero suelto truncado se quedaba instalado igual
            // que uno bueno, exactamente el mismo bug que dejaba a medias los
            // juegos de grupo antes de este fix.
            DocumentFile destinoParaLimpiarSiFalla = null;
            try {
                DocumentFile raiz = DocumentFile.fromTreeUri(this, arbolUri);
                if (raiz == null) {
                    throw new IllegalStateException("No se pudo abrir la carpeta ROMs guardada");
                }
                DocumentFile carpetaSistema = raiz.findFile(juego.sistema);
                if (carpetaSistema == null) {
                    carpetaSistema = raiz.createDirectory(juego.sistema);
                }
                if (carpetaSistema == null) {
                    throw new IllegalStateException("No se pudo crear la carpeta del sistema");
                }
                DocumentFile existente = carpetaSistema.findFile(juego.nombreFichero);
                if (existente != null) {
                    existente.delete();
                }
                if (juego.esGrupo) {
                    descargarGrupoPorFicheros(carpetaSistema, juego);
                } else {
                    DocumentFile destino = carpetaSistema.createFile("application/octet-stream", juego.nombreFichero);
                    if (destino == null) {
                        throw new IllegalStateException("No se pudo crear el fichero destino");
                    }
                    destinoParaLimpiarSiFalla = destino;
                    // "wa" = continuar al final tras un corte, "wt" = empezar de cero (ver descargarConReintentos).
                    ApiClient.descargarConReintentos(baseUrl, token,
                            RUTA_API + "?accion=descargar&system=" + juego.sistema + "&filename=" + Uri.encode(juego.nombreFichero) + origenDe(juego),
                            anadir -> getContentResolver().openOutputStream(destino.getUri(), anadir ? "wa" : "wt"),
                            crearCallbackProgreso(juego));
                }
                juego.instaladoLocal = true;
                if ("rpgmaker".equals(juego.sistema)) {
                    // Se juega con JoiPlay, no con ES-DE -- sin guardados
                    // (formato propio de JoiPlay, fuera de alcance) ni
                    // carátula/refresco de ES-DE en esta primera versión.
                    runOnUiThread(() -> {
                        juego.descargando = false;
                        textoEstado.setText("Descargado: " + juego.nombreFichero
                                + " -- añádelo en JoiPlay apuntando a Game.exe dentro de su carpeta.");
                        Toast.makeText(this, "Descargado", Toast.LENGTH_SHORT).show();
                        procesarSiguienteEnCola();
                        refrescarAdaptadores();
                    });
                } else {
                    // Best-effort, nunca puede tumbar una instalación que ya ha
                    // terminado con éxito (ver GuardadosSync: compara fechas en
                    // ambas direcciones antes de tocar nada).
                    GuardadosSync.restaurarTrasInstalar(this, cuenta, juego);
                    // Eden no tiene forma de recibir updates/DLC automáticamente
                    // (los identifica por Title ID embebido en el propio fichero,
                    // y la instalación es SIEMPRE manual vía su propio menú
                    // "Install DLC"/"Install Content to NAND" -- investigado a
                    // fondo, no hay Intent/API de Android para automatizarlo).
                    // Solo se avisa UNA vez aquí, justo tras la descarga; los
                    // ficheros ya quedan renombrados [UPDATE]/[DLC] (el base, sin prefijo) en
                    // descargarGrupoPorFicheros() para que sean fáciles de
                    // distinguir en el selector de ficheros nativo de Eden.
                    boolean avisoEdenPendiente = "switch_base_dlc".equals(juego.tipoGrupo)
                            && (juego.rolesUpdate > 0 || juego.rolesDlc > 0);
                    String mensajeInstalado = avisoEdenPendiente
                            ? "Descargado: " + juego.nombreFichero
                                    + " -- abre Eden y usa \"Install DLC\"/\"Install Content to NAND\" una vez"
                                    + " para instalar los ficheros [UPDATE]/[DLC] (Title ID, no por nombre)."
                            : "Descargado: " + juego.nombreFichero;
                    copiarCaratulaAESDE(juego, () -> runOnUiThread(() -> {
                        juego.descargando = false;
                        textoEstado.setText(mensajeInstalado);
                        Toast.makeText(this, avisoEdenPendiente ? "Descargado -- revisa el aviso de Eden" : "Descargado",
                                avisoEdenPendiente ? Toast.LENGTH_LONG : Toast.LENGTH_SHORT).show();
                        procesarSiguienteEnCola();
                        refrescarAdaptadores();
                        intentarRefrescarESDE(juego);
                    }));
                }
            } catch (Exception e) {
                if (destinoParaLimpiarSiFalla != null) {
                    destinoParaLimpiarSiFalla.delete();
                }
                runOnUiThread(() -> {
                    juego.descargando = false;
                    textoEstado.setText("Error al descargar: " + e.getMessage());
                    procesarSiguienteEnCola();
                    refrescarAdaptadores();
                });
            }
        }).start();
    }

    /**
     * Throttla las notificaciones de progreso a ~8/s: sin esto, cada trozo de
     * 16 KB dispararía un notifyDataSetChanged en el hilo de UI, cientos de
     * veces por segundo en ficheros grandes (PS2/PSP).
     */
    private ApiClient.ProgresoDescarga crearCallbackProgreso(Juego juego) {
        return new ApiClient.ProgresoDescarga() {
            long ultimoPost = 0;
            long ultimaNotificacion = 0;

            @Override
            public void onProgreso(long descargado, long total) {
                long ahora = System.currentTimeMillis();
                // Notificación del servicio en primer plano: como mucho 1/s
                // (el sistema descarta/limita actualizaciones más rápidas).
                if (ahora - ultimaNotificacion >= 1000 || descargado == total) {
                    ultimaNotificacion = ahora;
                    int permil = total > 0 ? (int) (descargado * 1000 / total) : -1;
                    DescargaService.actualizar(getApplicationContext(),
                            juego.nombreFichero + (total > 0 ? " · " + (permil / 10) + "%" : ""), permil);
                }
                if (ahora - ultimoPost < 120 && descargado != total) {
                    return;
                }
                ultimoPost = ahora;
                juego.bytesDescargados = descargado;
                juego.bytesTotal = total;
                runOnUiThread(MainActivity.this::refrescarAdaptadores);
            }
        };
    }

    /**
     * Juego de varios ficheros (PS1/PS2 cue+bin o multidisco, Switch
     * base+updates/DLC): se descarga FICHERO A FICHERO dentro de su carpeta
     * ("Juego.cue/", "Juego.nsp/"), cada uno con reintentos y reanudación.
     * Antes se pedía el grupo entero como un zip que jarjar generaba al vuelo:
     * con Switch (6,5 GB) tardaba ~30 s en mandar el primer byte, la app
     * cortaba por timeout y reintentaba, y cada reintento generaba otro zip
     * de 6,5 GB en el servidor -- la descarga no empezaba nunca. Si CUALQUIER
     * fichero falla, se borra la carpeta entera: nunca queda a medias.
     */
    /** Juegos de otra librería pública: nuestro servidor los pide a la de origen. */
    private static String origenDe(Juego juego) {
        return juego.origen == null ? "" : "&origen=" + Uri.encode(juego.origen);
    }

    private void descargarGrupoPorFicheros(DocumentFile carpetaSistema, Juego juego) throws Exception {
        String consulta = "&system=" + juego.sistema + "&grupo=" + Uri.encode(juego.nombreFichero) + origenDe(juego);
        JSONArray ficheros = new JSONArray(ApiClient.getTexto(baseUrl, token,
                RUTA_API + "?accion=grupo_ficheros" + consulta, 15000));
        long total = 0;
        for (int i = 0; i < ficheros.length(); i++) {
            total += ficheros.getJSONObject(i).getLong("tamano");
        }
        DocumentFile carpetaJuego = carpetaSistema.createDirectory(juego.nombreFichero);
        if (carpetaJuego == null) {
            throw new IllegalStateException("No se pudo crear la carpeta del juego");
        }
        Map<String, DocumentFile> cacheSubcarpetas = new HashMap<>();
        ApiClient.ProgresoDescarga progresoJuego = crearCallbackProgreso(juego);
        final long totalJuego = total;
        long acumulado = 0;
        try {
            for (int i = 0; i < ficheros.length(); i++) {
                JSONObject f = ficheros.getJSONObject(i);
                String ruta = f.getString("nombre");
                String rol = f.isNull("rol") ? "" : f.getString("rol");
                int ultimaBarra = ruta.lastIndexOf('/');
                String rutaCarpeta = ultimaBarra < 0 ? "" : ruta.substring(0, ultimaBarra);
                String nombreFichero = ultimaBarra < 0 ? ruta : ruta.substring(ultimaBarra + 1);
                // Prefijo visible [UPDATE]/[DLC] para distinguirlos de un
                // vistazo en el selector de ficheros de Eden al instalarlos a
                // mano. El BASE no lleva prefijo: se llama igual que la
                // carpeta ("Juego.nsp/Juego.nsp") para que ES-DE muestre el
                // grupo como un único juego y lance justo ese fichero.
                if (rutaCarpeta.isEmpty() && "update".equals(rol)) {
                    nombreFichero = "[UPDATE] " + nombreFichero;
                } else if (rutaCarpeta.isEmpty() && "dlc".equals(rol)) {
                    nombreFichero = "[DLC] " + nombreFichero;
                }
                DocumentFile carpetaDestino = resolverCarpetaDestino(carpetaJuego, rutaCarpeta, cacheSubcarpetas);
                DocumentFile destino = carpetaDestino.createFile("application/octet-stream", nombreFichero);
                if (destino == null) {
                    throw new IllegalStateException("No se pudo crear " + ruta);
                }
                final long yaDescargado = acumulado;
                ApiClient.descargarConReintentos(baseUrl, token,
                        RUTA_API + "?accion=grupo_fichero" + consulta + "&fichero=" + Uri.encode(ruta),
                        anadir -> getContentResolver().openOutputStream(destino.getUri(), anadir ? "wa" : "wt"),
                        (descargado, totalFichero) -> progresoJuego.onProgreso(yaDescargado + descargado, totalJuego));
                acumulado += f.getLong("tamano");
            }
        } catch (Exception e) {
            carpetaJuego.delete();
            throw e;
        }
    }

    /**
     * Resuelve (creando lo que haga falta) la subcarpeta SAF correspondiente
     * a una ruta relativa tipo "Graphics/Characters" dentro de la carpeta del
     * juego -- recursivo con memoización, para no repetir trabajo cuando
     * varios ficheros comparten la misma carpeta intermedia.
     */
    private DocumentFile resolverCarpetaDestino(DocumentFile raizJuego, String rutaCarpeta, Map<String, DocumentFile> cache) {
        if (rutaCarpeta.isEmpty()) {
            return raizJuego;
        }
        DocumentFile cacheada = cache.get(rutaCarpeta);
        if (cacheada != null) {
            return cacheada;
        }
        int ultimaBarra = rutaCarpeta.lastIndexOf('/');
        DocumentFile padre = ultimaBarra < 0
                ? raizJuego
                : resolverCarpetaDestino(raizJuego, rutaCarpeta.substring(0, ultimaBarra), cache);
        String nombreCarpeta = ultimaBarra < 0 ? rutaCarpeta : rutaCarpeta.substring(ultimaBarra + 1);
        DocumentFile existente = padre.findFile(nombreCarpeta);
        DocumentFile carpeta = (existente != null && existente.isDirectory()) ? existente : padre.createDirectory(nombreCarpeta);
        if (carpeta == null) {
            throw new IllegalStateException("No se pudo crear la subcarpeta " + rutaCarpeta);
        }
        cache.put(rutaCarpeta, carpeta);
        return carpeta;
    }

    // ---------- RPG Maker: siempre en almacenamiento interno (java.io.File, no SAF) ----------

    /**
     * Carpeta raíz fija de almacenamiento interno para RPG Maker -- NUNCA la
     * carpeta ROMs SAF que el usuario haya elegido para el resto de sistemas.
     * JoiPlay y su RTP viven en rutas fijas de almacenamiento interno público
     * (ver RTP en /storage/emulated/0/JoiPlay/RTP/...), y la SD ya nos dio
     * problemas de permisos/rendimiento reales con ES-DE -- para RPG Maker no
     * merece la pena arrastrar el mismo riesgo. Se usa java.io.File normal
     * (MANAGE_EXTERNAL_STORAGE), el mismo patrón que ya usamos para el RTP y
     * para las carátulas de ES-DE antes del fix de volumen -- no hace falta
     * SAF en absoluto para almacenamiento interno público.
     */
    private File carpetaRpgmakerInterno(String nombreJuego) {
        return new File(Environment.getExternalStorageDirectory(), "ROMs/rpgmaker/" + nombreJuego);
    }

    /** Borra recursivamente -- File.delete() normal solo borra ficheros/carpetas ya vacías. */
    private boolean borrarRecursivo(File f) {
        if (f.isDirectory()) {
            File[] hijos = f.listFiles();
            if (hijos != null) {
                for (File hijo : hijos) {
                    borrarRecursivo(hijo);
                }
            }
        }
        return f.delete();
    }

    private void descargarRpgmakerInterno(Juego juego) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && !Environment.isExternalStorageManager()) {
            Toast.makeText(this, "Activa \"Acceso a todos los archivos\" para instalar juegos de RPG Maker",
                    Toast.LENGTH_LONG).show();
            return;
        }
        textoEstado.setText("Descargando " + juego.nombreFichero + "...");
        descargaEnCurso = true;
        DescargaService.iniciar(this, "Descargando " + juego.nombreFichero);
        juego.descargando = true;
        juego.bytesDescargados = 0;
        juego.bytesTotal = -1;
        refrescarAdaptadores();
        new Thread(() -> {
            File carpetaJuego = carpetaRpgmakerInterno(juego.nombreFichero);
            try {
                File raizSistema = new File(Environment.getExternalStorageDirectory(), "ROMs/rpgmaker");
                if (!raizSistema.isDirectory() && !raizSistema.mkdirs()) {
                    throw new IllegalStateException("No se pudo crear ROMs/rpgmaker en almacenamiento interno");
                }
                // Reinstalar sobre una copia ya existente (a medias o completa)
                // empieza limpio siempre -- mismo criterio que el borrado
                // previo del SAF para el resto de sistemas.
                if (carpetaJuego.exists()) {
                    borrarRecursivo(carpetaJuego);
                }
                if (!carpetaJuego.mkdirs()) {
                    throw new IllegalStateException("No se pudo crear la carpeta del juego");
                }
                File zipTemporal = File.createTempFile("juego_rpgmaker", ".zip", getCacheDir());
                try {
                    ApiClient.descargarConReintentos(baseUrl, token,
                            RUTA_API + "?accion=descargar&system=" + juego.sistema + "&filename=" + Uri.encode(juego.nombreFichero) + origenDe(juego),
                            anadir -> new FileOutputStream(zipTemporal, anadir),
                            crearCallbackProgreso(juego));
                    // Con java.io.File normal, a diferencia de SAF/DocumentFile,
                    // File.mkdirs() ya crea cualquier profundidad de subcarpetas
                    // de una vez -- no hace falta el caché recursivo tipo
                    // resolverCarpetaDestino() que sí hacía falta para SAF.
                    try (ZipInputStream zis = new ZipInputStream(new java.io.FileInputStream(zipTemporal))) {
                        ZipEntry entrada;
                        byte[] buffer = new byte[65536];
                        while ((entrada = zis.getNextEntry()) != null) {
                            if (entrada.isDirectory()) {
                                continue;
                            }
                            File destinoEntrada = new File(carpetaJuego, entrada.getName());
                            File carpetaPadre = destinoEntrada.getParentFile();
                            if (carpetaPadre != null && !carpetaPadre.isDirectory() && !carpetaPadre.mkdirs()) {
                                throw new IllegalStateException("No se pudo crear " + carpetaPadre);
                            }
                            try (OutputStream out = new FileOutputStream(destinoEntrada)) {
                                int leidos;
                                while ((leidos = zis.read(buffer)) != -1) {
                                    out.write(buffer, 0, leidos);
                                }
                            }
                            zis.closeEntry();
                        }
                    }
                } finally {
                    zipTemporal.delete();
                }
                juego.instaladoLocal = true;
                // Se juega con JoiPlay, no con ES-DE -- sin guardados (formato
                // propio de JoiPlay, fuera de alcance) ni carátula/refresco de
                // ES-DE en esta primera versión (igual que la rama rpgmaker
                // de descargar() para el resto de sistemas).
                runOnUiThread(() -> {
                    juego.descargando = false;
                    textoEstado.setText("Descargado: " + juego.nombreFichero
                            + " -- añádelo en JoiPlay apuntando a Game.exe dentro de su carpeta.");
                    Toast.makeText(this, "Descargado", Toast.LENGTH_SHORT).show();
                    procesarSiguienteEnCola();
                    refrescarAdaptadores();
                });
            } catch (Exception e) {
                borrarRecursivo(carpetaJuego);
                runOnUiThread(() -> {
                    juego.descargando = false;
                    textoEstado.setText("Error al descargar: " + e.getMessage());
                    procesarSiguienteEnCola();
                    refrescarAdaptadores();
                });
            }
        }).start();
    }

    // ---------- Refresco de ES-DE tras instalar ----------

    /**
     * Tras instalar un juego, ES-DE tiene que rescanear para que aparezca:
     * se pide a Tasker el cierre forzoso + relanzamiento de ES-DE (ver
     * TaskerBridge; un simple startActivity no basta si ES-DE ya estaba en
     * marcha). De forma OPORTUNISTA y silenciosa: el puente de Tasker solo
     * funciona con la VPN conectada, que lo normal es que no lo esté justo
     * tras instalar, así que avisar en cada descarga sería ruido; siempre
     * queda "Reiniciar ES-DE" en el menú. La descarga NUNCA se marca como
     * fallida por esto. (Shizuku se descartó del todo en este proyecto.)
     */
    private void intentarRefrescarESDE(Juego juego) {
        if (getPackageManager().getLaunchIntentForPackage("org.es_de.frontend") == null
                && getPackageManager().getLaunchIntentForPackage("org.es_de.frontend.galaxy") == null) {
            Toast.makeText(this, "Descarga completada.", Toast.LENGTH_SHORT).show();
            return;
        }
        if (TaskerBridge.solicitarRefrescoEsDe(this) == TaskerBridge.Resultado.SOLICITADO) {
            Toast.makeText(this, "Descarga completada. Actualizando ES-DE vía Tasker…", Toast.LENGTH_SHORT).show();
        } else {
            Toast.makeText(this, "Descargado. Usa \"Reiniciar ES-DE\" en el menú si no aparece.", Toast.LENGTH_LONG).show();
        }
    }

    /**
     * Acción manual del menú -- pide a Tasker (vía broadcast, ver
     * TaskerBridge) que ejecute el force-stop + relanzamiento real de ES-DE
     * a través del puente adb que Tasker levanta bajo demanda. Sustituye al
     * antiguo "solo traer al frente" (que no garantizaba un rescan si ES-DE
     * ya estaba en segundo plano) ahora que existe una vía real sin Shizuku.
     * Como el broadcast es asíncrono y Tasker no devuelve confirmación,
     * el mensaje deja claro que se ha PEDIDO, no que ya esté hecho -- y si
     * el puente no puede estar disponible (Tasker no instalado o VPN
     * desconectada) se dice con honestidad en vez de fallar en silencio.
     */
    private void reiniciarEsDeManual() {
        TaskerBridge.Resultado resultado = TaskerBridge.solicitarRefrescoEsDe(this);
        switch (resultado) {
            case SOLICITADO:
                Toast.makeText(this, "Pedido a Tasker refrescar ES-DE (puede tardar unos segundos).",
                        Toast.LENGTH_LONG).show();
                break;
            case TASKER_NO_INSTALADO:
                Toast.makeText(this, "Tasker no está instalado -- no se puede refrescar ES-DE de esta forma.",
                        Toast.LENGTH_LONG).show();
                break;
            case VPN_NO_CONECTADA:
            default:
                Toast.makeText(this, "La VPN de Tailscale no está conectada -- conéctala primero para que "
                        + "el puente esté disponible, luego vuelve a intentarlo.", Toast.LENGTH_LONG).show();
                break;
        }
    }
}
