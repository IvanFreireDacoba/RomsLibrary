package es.jarjar.romlibrary;

import android.app.Activity;
import android.view.KeyEvent;
import android.view.View;

/**
 * Mapeo de mando compartido por todas las pantallas: BUTTON_A confirma
 * (equivalente a pulsar el elemento con foco), BUTTON_B vuelve atrás. El
 * D-pad ya mueve la selección de los ListView de forma nativa en Android,
 * no hace falta nada extra para eso.
 */
public abstract class BaseActivity extends Activity {

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (event.getAction() == KeyEvent.ACTION_DOWN) {
            int codigo = event.getKeyCode();
            if (codigo == KeyEvent.KEYCODE_BUTTON_B) {
                onBackPressed();
                return true;
            }
            if (codigo == KeyEvent.KEYCODE_BUTTON_A) {
                View foco = getCurrentFocus();
                if (foco != null) {
                    foco.performClick();
                    return true;
                }
            }
        }
        return super.dispatchKeyEvent(event);
    }
}
