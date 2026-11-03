package es.jarjar.romlibrary;

import android.graphics.Color;
import android.os.Bundle;
import android.text.TextUtils;
import android.view.KeyEvent;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * Un documento del apartado «Versión»: una página web (el aviso legal de
 * sund.es, opcionalmente desplazada a un apartado) o textos de assets/
 * (licencias). Cruceta arriba/abajo desplaza, L1/R1 o izquierda/derecha
 * pasan página, B vuelve (BaseActivity).
 */
public class DocumentoActivity extends BaseActivity {

    static final String EXTRA_TITULO = "titulo";
    static final String EXTRA_URL = "url";
    static final String EXTRA_SECCION = "seccion";
    static final String EXTRA_ASSETS = "assets";

    private WebView web;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setTitle(getIntent().getStringExtra(EXTRA_TITULO));
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#14181c"));
        web.setFocusable(true);
        setContentView(web);
        web.requestFocus();

        String url = getIntent().getStringExtra(EXTRA_URL);
        String[] assets = getIntent().getStringArrayExtra(EXTRA_ASSETS);
        if (url != null) {
            cargarWeb(url, getIntent().getStringExtra(EXTRA_SECCION));
        } else if (assets != null) {
            StringBuilder sb = new StringBuilder();
            for (String a : assets) {
                if (sb.length() > 0) {
                    sb.append("\n\n------------------------------------------------------------\n\n");
                }
                sb.append(leerAsset(a));
            }
            mostrarHtml("<pre>" + TextUtils.htmlEncode(sb.toString()) + "</pre>");
        }
    }

    private void cargarWeb(String url, String seccion) {
        // Solo para desplazar al apartado pedido; la página es la nuestra de sund.es.
        web.getSettings().setJavaScriptEnabled(seccion != null);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView vista, String direccion) {
                if (seccion != null && direccion.startsWith(url)) {
                    // -140: la cabecera de sund.es es fija y taparía el título del apartado.
                    vista.evaluateJavascript("(function(){var hs=document.querySelectorAll('h2');"
                            + "for(var i=0;i<hs.length;i++){if(hs[i].textContent.trim().indexOf('" + seccion + "')===0){"
                            + "window.scrollTo(0,hs[i].getBoundingClientRect().top+window.pageYOffset-140);return;}}})()", null);
                }
            }

            @Override
            public void onReceivedError(WebView vista, WebResourceRequest peticion, WebResourceError error) {
                if (peticion.isForMainFrame()) {
                    mostrarHtml("<p>No se ha podido cargar la página (sin conexión).</p><p>Puedes leerla en:<br><b>"
                            + TextUtils.htmlEncode(url) + "</b></p>");
                }
            }
        });
        web.loadUrl(url);
    }

    private void mostrarHtml(String cuerpo) {
        String html = "<html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
                + "<style>body{background:#14181c;color:#e6eaee;font-family:sans-serif;padding:12px;}"
                + "pre{white-space:pre-wrap;word-wrap:break-word;font-size:14px;line-height:1.4;}"
                + "b{color:#7bffb0;}</style></head><body>" + cuerpo + "</body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    private String leerAsset(String ruta) {
        try (InputStream in = getAssets().open(ruta)) {
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] trozo = new byte[8192];
            int n;
            while ((n = in.read(trozo)) != -1) {
                buf.write(trozo, 0, n);
            }
            return new String(buf.toByteArray(), StandardCharsets.UTF_8);
        } catch (Exception e) {
            return "(no disponible: " + ruta + ")";
        }
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (event.getAction() == KeyEvent.ACTION_DOWN) {
            int paso = Math.round(120 * getResources().getDisplayMetrics().density);
            switch (event.getKeyCode()) {
                case KeyEvent.KEYCODE_DPAD_DOWN:
                    web.scrollBy(0, paso);
                    return true;
                case KeyEvent.KEYCODE_DPAD_UP:
                    web.scrollBy(0, -paso);
                    return true;
                case KeyEvent.KEYCODE_DPAD_RIGHT:
                case KeyEvent.KEYCODE_BUTTON_R1:
                case KeyEvent.KEYCODE_PAGE_DOWN:
                    web.pageDown(false);
                    return true;
                case KeyEvent.KEYCODE_DPAD_LEFT:
                case KeyEvent.KEYCODE_BUTTON_L1:
                case KeyEvent.KEYCODE_PAGE_UP:
                    web.pageUp(false);
                    return true;
                default:
                    break;
            }
        }
        return super.dispatchKeyEvent(event);
    }

    @Override
    protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }
}
