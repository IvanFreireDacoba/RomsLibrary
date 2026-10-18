package es.jarjar.romlibrary;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;

/**
 * Pide a Tasker (instalado y configurado por el propio usuario, con "Allow
 * External Access" activado en sus preferencias) que ejecute la Task
 * "ESDE_Refresh_OnDemand" -- que hace un force-stop + relanzamiento real de
 * ES-DE vía un puente adb local que Tasker levanta bajo demanda (ver
 * bridge_on.sh / esde_refresh.sh en ~/.termux/tasker/ del dispositivo).
 *
 * Esta app NUNCA ejecuta el force-stop ella misma -- eso exigiría privilegio
 * (Shizuku/root/AccessibilityService), justo lo que este mecanismo evita.
 * Solo dispara el broadcast que Tasker ya sabe interpretar.
 *
 * El puente depende de que la VPN de Tailscale esté conectada (así arranca
 * Tasker el ciclo completo). Esta clase comprueba eso desde el propio
 * ConnectivityManager (TRANSPORT_VPN) para poder avisar con honestidad en
 * vez de disparar un broadcast que Tasker no podría completar.
 */
final class TaskerBridge {

    private static final String PAQUETE_TASKER = "net.dinglisch.android.taskerm";
    private static final String ACCION_EJECUTAR_TASK = "net.dinglisch.android.tasker.ACTION_TASK";
    private static final String EXTRA_NOMBRE_TASK = "task_name";
    private static final String TASK_REFRESCO_ESDE = "ESDE_Refresh_OnDemand";

    enum Resultado {
        /** El broadcast se envió; Tasker se encarga del resto (async, sin confirmación de vuelta). */
        SOLICITADO,
        /** Tasker no está instalado en este dispositivo. */
        TASKER_NO_INSTALADO,
        /** La VPN de Tailscale no está conectada -- el puente adb no estaría disponible. */
        VPN_NO_CONECTADA,
    }

    private TaskerBridge() {
    }

    static boolean taskerInstalado(Context context) {
        try {
            context.getPackageManager().getPackageInfo(PAQUETE_TASKER, 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    static boolean vpnConectada(Context context) {
        ConnectivityManager cm = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) {
            return false;
        }
        Network red = cm.getActiveNetwork();
        if (red == null) {
            return false;
        }
        NetworkCapabilities cap = cm.getNetworkCapabilities(red);
        return cap != null && cap.hasTransport(NetworkCapabilities.TRANSPORT_VPN);
    }

    static Resultado solicitarRefrescoEsDe(Context context) {
        if (!taskerInstalado(context)) {
            return Resultado.TASKER_NO_INSTALADO;
        }
        if (!vpnConectada(context)) {
            return Resultado.VPN_NO_CONECTADA;
        }
        Intent intent = new Intent(ACCION_EJECUTAR_TASK);
        intent.putExtra(EXTRA_NOMBRE_TASK, TASK_REFRESCO_ESDE);
        context.sendBroadcast(intent);
        return Resultado.SOLICITADO;
    }
}
