package es.jarjar.romlibrary;

import android.app.Activity;
import android.view.KeyEvent;
import android.view.View;
import android.widget.AbsListView;
import android.widget.AdapterView;

/**
 * Mapeo de mando compartido por todas las pantallas: BUTTON_A confirma
 * (equivalente a pulsar el elemento con foco; mantenido, pulsación larga),
 * BUTTON_B vuelve atrás. En una lista o cuadrícula el foco es de la propia
 * lista y A actúa sobre la fila seleccionada.
 */
public abstract class BaseActivity extends Activity {

    /** A pulsado y todavía sin soltar; y si ya se ha disparado su pulsación larga. */
    private boolean aPulsado, aLargo;

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        int codigo = event.getKeyCode();
        if (event.getAction() == KeyEvent.ACTION_DOWN && codigo == KeyEvent.KEYCODE_BUTTON_B) {
            onBackPressed();
            return true;
        }
        View foco = getCurrentFocus();
        if (codigo == KeyEvent.KEYCODE_BUTTON_A && foco != null) {
            // Se pulsa al SOLTAR: antes se pulsaba en cada ACTION_DOWN, y
            // mantener A repetía la pulsación (instalar y desinstalar seguido)
            // y nunca llegaba a la pulsación larga (elegir idioma).
            if (event.getAction() == KeyEvent.ACTION_DOWN) {
                if (event.getRepeatCount() == 0) {
                    aPulsado = true;
                    aLargo = false;
                } else if (aPulsado && !aLargo) {
                    aLargo = true;
                    pulsar(foco, true);
                }
            } else if (event.getAction() == KeyEvent.ACTION_UP) {
                if (aPulsado && !aLargo) {
                    pulsar(foco, false);
                }
                aPulsado = false;
            }
            return true;
        }
        return super.dispatchKeyEvent(event);
    }

    private static void pulsar(View foco, boolean largo) {
        if (foco instanceof AbsListView) {
            AbsListView lista = (AbsListView) foco;
            int pos = lista.getSelectedItemPosition();
            View fila = lista.getSelectedView();
            if (pos == AdapterView.INVALID_POSITION || fila == null) {
                return;
            }
            long id = lista.getItemIdAtPosition(pos);
            if (!largo) {
                lista.performItemClick(fila, pos, id);
            } else if (lista.getOnItemLongClickListener() != null) {
                lista.getOnItemLongClickListener().onItemLongClick(lista, fila, pos, id);
            }
        } else if (largo) {
            foco.performLongClick();
        } else {
            foco.performClick();
        }
    }
}
