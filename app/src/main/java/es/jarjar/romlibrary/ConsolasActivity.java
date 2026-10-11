package es.jarjar.romlibrary;

import android.app.AlertDialog;
import android.graphics.Typeface;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Map;

/**
 * "Sincronizar consolas" y "Consolas bloqueadas" (según el extra MODO).
 *
 * Vincular dos consolas hace que compartan UNA sola carpeta de guardados en
 * el servidor (se fusionan y gana siempre el guardado más reciente). Para
 * evitar que alguien vincule una consola ajena por error y le pise las
 * partidas, la otra consola tiene que ACEPTAR la solicitud. Bloquear
 * desvincula e impide volver a pedir la sincronización hasta desbloquear.
 *
 * Interfaz construida por código (pocas vistas, todas navegables con el
 * D-pad): cada acción va al servidor en segundo plano y se vuelve a pintar
 * todo con el estado real que devuelve.
 */
public class ConsolasActivity extends BaseActivity {

    static final String EXTRA_MODO = "modo";
    static final String MODO_BLOQUEADAS = "bloqueadas";

    private static final int VERDE = 0xFF7BFFB0;
    private static final int TEXTO = 0xFFD7E4D7;
    private static final int APAGADO = 0xFF6F8F6F;

    private Cuenta cuenta;
    private boolean modoBloqueadas;
    private LinearLayout contenido;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        modoBloqueadas = MODO_BLOQUEADAS.equals(getIntent().getStringExtra(EXTRA_MODO));
        setTitle(modoBloqueadas ? "Consolas bloqueadas" : "Sincronizar consolas");
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(0xFF10151A);
        contenido = new LinearLayout(this);
        contenido.setOrientation(LinearLayout.VERTICAL);
        int p = dp(16);
        contenido.setPadding(p, p, p, p);
        scroll.addView(contenido);
        setContentView(scroll);
    }

    @Override
    protected void onResume() {
        super.onResume();
        cuenta = Cuenta.cargar(this);
        if (!cuenta.registrada()) {
            Toast.makeText(this, "Configura primero el servidor y el nombre de la consola", Toast.LENGTH_LONG).show();
            finish();
            return;
        }
        recargar();
    }

    // ---------- Servidor ----------

    private void recargar() {
        contenido.removeAllViews();
        contenido.addView(texto("Cargando...", APAGADO, 14));
        new Thread(() -> {
            try {
                String json = ApiClient.getTexto(cuenta.url, cuenta.clave,
                        Cuenta.RUTA_API + "?accion=consola_estado" + cuenta.consultaIdentidad(), 15000);
                JSONObject estado = new JSONObject(json);
                runOnUiThread(() -> pintar(estado));
            } catch (Exception e) {
                runOnUiThread(() -> {
                    contenido.removeAllViews();
                    contenido.addView(texto("Error: " + e.getMessage(), 0xFFFF8A8A, 14));
                    contenido.addView(boton("Reintentar", v -> recargar()));
                });
            }
        }).start();
    }

    /** Acción sobre el servidor con un campo extra; al terminar, se recarga todo. */
    private void accion(String op, String campo, String valor, String mensajeOk) {
        accion(op, campo, valor, null, null, mensajeOk);
    }

    private void accion(String op, String campo, String valor, String campo2, String valor2, String mensajeOk) {
        new Thread(() -> {
            try {
                Map<String, String> campos = cuenta.identidad();
                if (campo != null) {
                    campos.put(campo, valor);
                }
                if (campo2 != null) {
                    campos.put(campo2, valor2);
                }
                ApiClient.postFormulario(cuenta.url, cuenta.clave,
                        Cuenta.RUTA_API + "?accion=consola_accion&op=" + op, campos);
                runOnUiThread(() -> {
                    Toast.makeText(this, mensajeOk, Toast.LENGTH_LONG).show();
                    recargar();
                });
            } catch (Exception e) {
                runOnUiThread(() -> Toast.makeText(this, "Error: " + e.getMessage(), Toast.LENGTH_LONG).show());
            }
        }).start();
    }

    // ---------- Pintado ----------

    private void pintar(JSONObject estado) {
        contenido.removeAllViews();
        try {
            JSONObject yo = estado.getJSONObject("yo");
            contenido.addView(titulo("Esta consola"));
            contenido.addView(texto(yo.getString("nombre"), TEXTO, 18));
            TextView ident = texto(yo.getString("identificador"), VERDE, 26);
            ident.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
            contenido.addView(ident);
            contenido.addView(texto("Comparte este identificador con la otra consola para sincronizar las partidas.",
                    APAGADO, 12));

            if (modoBloqueadas) {
                pintarLista(estado.getJSONArray("bloqueadas"), "No has bloqueado ninguna consola.",
                        "Consolas bloqueadas", (nombre, id) -> new View[]{
                                boton("Desbloquear", v -> accion("desbloquear", "otra", id, "Desbloqueada " + nombre))});
                return;
            }

            contenido.addView(boton("Cambiar nombre", v -> pedirNombre(yo.optString("nombre"))));

            contenido.addView(titulo("Añadir consola"));
            EditText campo = new EditText(this);
            campo.setHint("Identificador: 12345-67890");
            campo.setInputType(InputType.TYPE_CLASS_PHONE);
            campo.setTextColor(TEXTO);
            campo.setHintTextColor(APAGADO);
            contenido.addView(campo);
            contenido.addView(boton("Sincronizar", v -> {
                String otra = normalizarIdentificador(campo.getText().toString());
                if (otra == null) {
                    Toast.makeText(this, "El identificador tiene 10 cifras: 12345-67890", Toast.LENGTH_LONG).show();
                    return;
                }
                accion("solicitar", "otra", otra,
                        "Solicitud enviada: la otra consola tiene que aceptarla al abrir RomsLibrary");
            }));

            JSONArray recibidas = estado.getJSONArray("solicitudes_recibidas");
            if (recibidas.length() > 0) {
                contenido.addView(titulo("Quieren sincronizarse con esta consola"));
                for (int i = 0; i < recibidas.length(); i++) {
                    JSONObject s = recibidas.getJSONObject(i);
                    String nombre = s.getString("nombre");
                    String sol = String.valueOf(s.getInt("solicitud"));
                    contenido.addView(fila(nombre, s.getString("identificador"),
                            boton("Aceptar", v -> confirmar("¿Sincronizar con " + nombre + "?",
                                    "Las dos consolas compartirán las mismas partidas (se queda siempre la más reciente).",
                                    () -> accion("responder", "solicitud", sol, "aceptar", "true", "Sincronizada con " + nombre))),
                            boton("Rechazar", v -> accion("responder", "solicitud", sol, "aceptar", "false", "Rechazada"))));
                }
            }

            JSONArray enviadas = estado.getJSONArray("solicitudes_enviadas");
            if (enviadas.length() > 0) {
                contenido.addView(titulo("Esperando a que acepten"));
                for (int i = 0; i < enviadas.length(); i++) {
                    JSONObject s = enviadas.getJSONObject(i);
                    String sol = String.valueOf(s.getInt("solicitud"));
                    contenido.addView(fila(s.getString("nombre"), s.getString("identificador"),
                            boton("Cancelar", v -> accion("cancelar", "solicitud", sol, "Solicitud cancelada"))));
                }
            }

            pintarLista(estado.getJSONArray("sincronizadas"), "Ninguna: esta consola guarda sus partidas sola.",
                    "Consolas sincronizadas", (nombre, id) -> new View[]{
                            boton("Bloquear", v -> confirmar("¿Bloquear " + nombre + "?",
                                    "Se desincroniza y no podrá volver a pedir sincronizarse hasta que la desbloquees.",
                                    () -> accion("bloquear", "otra", id, "Bloqueada " + nombre))),
                            boton("Desincronizar", v -> confirmar("¿Desincronizar " + nombre + "?",
                                    "Dejará de compartir partidas con esta consola (se lleva una copia de las actuales).",
                                    () -> accion("desincronizar", "otra", id, "Desincronizada " + nombre)))});
        } catch (Exception e) {
            contenido.addView(texto("Respuesta inesperada del servidor: " + e.getMessage(), 0xFFFF8A8A, 14));
        }
    }

    private interface BotonesFila {
        View[] crear(String nombre, String identificador);
    }

    private void pintarLista(JSONArray lista, String vacia, String tituloSeccion, BotonesFila botones) throws Exception {
        contenido.addView(titulo(tituloSeccion));
        if (lista.length() == 0) {
            contenido.addView(texto(vacia, APAGADO, 14));
            return;
        }
        for (int i = 0; i < lista.length(); i++) {
            JSONObject c = lista.getJSONObject(i);
            String nombre = c.getString("nombre");
            String id = c.getString("identificador");
            contenido.addView(fila(nombre, id, botones.crear(nombre, id)));
        }
    }

    private void pedirNombre(String actual) {
        EditText campo = new EditText(this);
        campo.setText(actual);
        campo.setSelection(campo.getText().length());
        new AlertDialog.Builder(this)
                .setTitle("Nombre de esta consola")
                .setView(campo)
                .setPositiveButton("Guardar", (d, w) -> {
                    String nombre = campo.getText().toString().trim();
                    if (nombre.isEmpty()) {
                        return;
                    }
                    Cuenta.guardarNombre(this, cuenta.url, nombre);
                    accion("renombrar", "nombre", nombre, "Nombre cambiado");
                })
                .setNegativeButton("Cancelar", null)
                .show();
    }

    private void confirmar(String titulo, String mensaje, Runnable siAcepta) {
        new AlertDialog.Builder(this)
                .setTitle(titulo)
                .setMessage(mensaje)
                .setPositiveButton("Sí", (d, w) -> siAcepta.run())
                .setNegativeButton("No", null)
                .show();
    }

    /** "1234567890", "12345 67890" o "12345-67890" -> "12345-67890"; null si no son 10 cifras. */
    static String normalizarIdentificador(String texto) {
        String cifras = texto == null ? "" : texto.replaceAll("[^0-9]", "");
        return cifras.length() == 10 ? cifras.substring(0, 5) + "-" + cifras.substring(5) : null;
    }

    // ---------- Piezas de interfaz ----------

    private View fila(String nombre, String identificador, View... botones) {
        LinearLayout fila = new LinearLayout(this);
        fila.setOrientation(LinearLayout.HORIZONTAL);
        fila.setGravity(Gravity.CENTER_VERTICAL);
        fila.setPadding(0, dp(6), 0, dp(6));
        LinearLayout datos = new LinearLayout(this);
        datos.setOrientation(LinearLayout.VERTICAL);
        datos.addView(texto(nombre, TEXTO, 15));
        TextView ident = texto(identificador, VERDE, 14);
        ident.setTypeface(Typeface.MONOSPACE);
        datos.addView(ident);
        fila.addView(datos, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));
        for (View b : botones) {
            fila.addView(b);
        }
        return fila;
    }

    private TextView titulo(String t) {
        TextView v = texto("> " + t, VERDE, 16);
        v.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        v.setPadding(0, dp(20), 0, dp(6));
        return v;
    }

    private TextView texto(String t, int color, int sp) {
        TextView v = new TextView(this);
        v.setText(t);
        v.setTextColor(color);
        v.setTextSize(sp);
        return v;
    }

    private Button boton(String t, View.OnClickListener accion) {
        Button b = new Button(this);
        b.setText(t);
        b.setAllCaps(false);
        b.setOnClickListener(accion);
        return b;
    }

    private int dp(int valor) {
        return Math.round(valor * getResources().getDisplayMetrics().density);
    }
}
