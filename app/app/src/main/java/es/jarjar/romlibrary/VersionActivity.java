package es.jarjar.romlibrary;

import android.content.Intent;
import android.content.pm.PackageInfo;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * Menú ⋮ → «Versión»: versión instalada (versionName y versionCode), la del
 * servicio al que está conectada, y los textos legales que YA existen:
 * - Términos de uso, privacidad y aviso legal: la página pública
 *   https://sund.es/romslibrary/aviso-legal (no hay un documento de términos
 *   aparte: los términos son su apartado «2. Normas de uso»).
 * - Licencia MIT del proyecto (publico/LICENSE del monorepo, la del repo
 *   público) y las licencias de las librerías que van dentro del APK
 *   (AndroidX/Kotlin/Guava, Apache 2.0), en assets/licencias.
 * Todo son botones: cruceta (foco verde muy visible) y táctil; B vuelve.
 */
public class VersionActivity extends BaseActivity {

    static final String URL_LEGAL = "https://sund.es/romslibrary/aviso-legal";
    static final String URL_CODIGO = "https://github.com/IvanFreireDacoba/RomsLibrary";

    private TextView textoServicio;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setTitle("Versión");

        LinearLayout raiz = new LinearLayout(this);
        raiz.setOrientation(LinearLayout.VERTICAL);
        raiz.setPadding(dp(24), dp(16), dp(24), dp(24));

        TextView titulo = new TextView(this);
        titulo.setText("RomsLibrary");
        titulo.setTextSize(26);
        titulo.setTypeface(Typeface.DEFAULT_BOLD);
        titulo.setTextColor(Color.WHITE);
        raiz.addView(titulo);

        TextView version = new TextView(this);
        version.setText(textoVersion());
        version.setTextSize(18);
        version.setTextColor(Color.parseColor("#7bffb0"));
        version.setPadding(0, dp(4), 0, 0);
        raiz.addView(version);

        textoServicio = new TextView(this);
        textoServicio.setTextSize(14);
        textoServicio.setTextColor(Color.parseColor("#b0bac4"));
        textoServicio.setPadding(0, dp(4), 0, dp(12));
        raiz.addView(textoServicio);

        // ---- Ajuste del reinicio de ES-DE (ver RefrescoEsDe) ----
        if (RefrescoEsDe.esDeInstalado(this)) {
            seccion(raiz, "ES-DE");
            Switch interruptor = new Switch(this);
            interruptor.setText("Reiniciar ES-DE al terminar las descargas");
            interruptor.setTextSize(17);
            interruptor.setTextColor(Color.WHITE);
            interruptor.setPadding(dp(16), dp(10), dp(16), dp(10));
            interruptor.setBackgroundResource(R.drawable.boton_version_fondo);
            interruptor.setFocusable(true);
            interruptor.setChecked(RefrescoEsDe.automatico(this));
            interruptor.setOnCheckedChangeListener((b, marcado) -> RefrescoEsDe.setAutomatico(this, marcado));
            LinearLayout.LayoutParams lpSw = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
            lpSw.topMargin = dp(6);
            raiz.addView(interruptor, lpSw);
            TextView ayuda = new TextView(this);
            ayuda.setText("Al acabar la cola, deja una señal para que la consola reinicie ES-DE y vea los juegos "
                    + "nuevos. La reinicia un vigilante del dispositivo; la app no la reinicia sola.");
            ayuda.setTextSize(13);
            ayuda.setTextColor(Color.parseColor("#8fa0b0"));
            ayuda.setPadding(dp(4), dp(4), dp(4), 0);
            raiz.addView(ayuda);
        }

        seccion(raiz, "Condiciones y políticas");
        Button primero = boton(raiz, "Términos y condiciones de uso",
                v -> abrirWeb("Términos y condiciones de uso", URL_LEGAL, "2."));
        boton(raiz, "Política de privacidad",
                v -> abrirWeb("Política de privacidad", URL_LEGAL, "5."));
        boton(raiz, "Aviso legal (completo)",
                v -> abrirWeb("Aviso legal", URL_LEGAL, null));

        seccion(raiz, "Licencias");
        boton(raiz, "Licencia de RomsLibrary (MIT)",
                v -> abrirTexto("Licencia de RomsLibrary", "licencias/LICENSE-RomsLibrary.txt"));
        boton(raiz, "Licencias de terceros",
                v -> abrirTexto("Licencias de terceros", "licencias/TERCEROS.txt", "licencias/Apache-2.0.txt"));
        boton(raiz, "Código fuente (GitHub)",
                v -> abrirWeb("Código fuente", URL_CODIGO, null));

        TextView contacto = new TextView(this);
        contacto.setText("Contacto: support@sund.es");
        contacto.setTextSize(14);
        contacto.setTextColor(Color.parseColor("#b0bac4"));
        contacto.setPadding(0, dp(16), 0, 0);
        raiz.addView(contacto);

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.parseColor("#14181c"));
        scroll.addView(raiz);
        setContentView(scroll);
        primero.requestFocus();
        consultarServicio();
    }

    private String textoVersion() {
        try {
            PackageInfo info = getPackageManager().getPackageInfo(getPackageName(), 0);
            long codigo = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P ? info.getLongVersionCode() : info.versionCode;
            return "Versión " + info.versionName + " (versionCode " + codigo + ")";
        } catch (Exception e) {
            return "Versión desconocida";
        }
    }

    /** Versión del servicio conectado, con la petición ligera «hola» (si es anterior, no la tiene). */
    private void consultarServicio() {
        Cuenta cuenta = Cuenta.cargar(this);
        if (cuenta.url == null || cuenta.url.isEmpty()) {
            textoServicio.setText("Sin servidor configurado");
            return;
        }
        textoServicio.setText("Servidor: " + cuenta.url);
        new Thread(() -> {
            String texto;
            try {
                JSONObject o = new JSONObject(ApiClient.getTexto(cuenta.url, cuenta.clave, Cuenta.RUTA_API + "?accion=hola", 8000));
                texto = "Servidor: " + cuenta.url + " · librería «" + o.optString("libreria", "?") + "» · servicio "
                        + o.optString("servicio", "?");
            } catch (ApiClient.ErrorHttp e) {
                texto = "Servidor: " + cuenta.url + " · servicio anterior a la 1.2.0";
            } catch (Exception e) {
                texto = "Servidor: " + cuenta.url + " · sin conexión";
            }
            String fin = texto;
            runOnUiThread(() -> textoServicio.setText(fin));
        }).start();
    }

    private void abrirWeb(String titulo, String url, String seccion) {
        startActivity(new Intent(this, DocumentoActivity.class)
                .putExtra(DocumentoActivity.EXTRA_TITULO, titulo)
                .putExtra(DocumentoActivity.EXTRA_URL, url)
                .putExtra(DocumentoActivity.EXTRA_SECCION, seccion));
    }

    private void abrirTexto(String titulo, String... assets) {
        startActivity(new Intent(this, DocumentoActivity.class)
                .putExtra(DocumentoActivity.EXTRA_TITULO, titulo)
                .putExtra(DocumentoActivity.EXTRA_ASSETS, assets));
    }

    private void seccion(LinearLayout raiz, String texto) {
        TextView t = new TextView(this);
        t.setText(texto);
        t.setTextSize(15);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextColor(Color.parseColor("#8fa0b0"));
        t.setPadding(0, dp(14), 0, dp(4));
        raiz.addView(t);
    }

    private Button boton(LinearLayout raiz, String texto, View.OnClickListener accion) {
        Button b = new Button(this);
        b.setText(texto);
        b.setAllCaps(false);
        b.setTextSize(17);
        b.setGravity(Gravity.CENTER_VERTICAL | Gravity.START);
        b.setPadding(dp(16), dp(10), dp(16), dp(10));
        b.setBackgroundResource(R.drawable.boton_version_fondo);
        b.setTextColor(getResources().getColorStateList(R.color.boton_buscar_texto, getTheme()));
        b.setFocusable(true);
        b.setOnClickListener(accion);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(6);
        raiz.addView(b, lp);
        return b;
    }

    private int dp(int valor) {
        return Math.round(valor * getResources().getDisplayMetrics().density);
    }
}
