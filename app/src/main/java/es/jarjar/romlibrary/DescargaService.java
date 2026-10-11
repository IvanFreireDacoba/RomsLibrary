package es.jarjar.romlibrary;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

/**
 * Servicio en primer plano que mantiene viva la app mientras hay una
 * descarga de ROM en curso. Las descargas siguen corriendo en los hilos de
 * MainActivity; este servicio solo existe para que Android NO mate el
 * proceso cuando el usuario sale de la app (vuelve a ES-DE, apaga la
 * pantalla...): notificación con el progreso + WakeLock parcial (la CPU no
 * se duerme con la pantalla apagada) + WifiLock (el wifi no entra en ahorro
 * de energía a mitad de un disco de 700 MB).
 */
public class DescargaService extends Service {

    private static final String CANAL = "descargas";
    private static final int ID_NOTIFICACION = 4201;
    private static final String EXTRA_TEXTO = "texto";

    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;

    static void iniciar(Context context, String texto) {
        Intent intent = new Intent(context, DescargaService.class).putExtra(EXTRA_TEXTO, texto);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    static void detener(Context context) {
        context.stopService(new Intent(context, DescargaService.class));
    }

    /** Actualiza la notificación existente (permil: 0-1000, o -1 si el total aún no se conoce). */
    static void actualizar(Context context, String texto, int permil) {
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm != null) {
            nm.notify(ID_NOTIFICACION, construir(context, texto, permil));
        }
    }

    private static Notification construir(Context context, String texto, int permil) {
        crearCanal(context);
        Intent abrir = new Intent(context, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent alTocar = PendingIntent.getActivity(context, 0, abrir,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(context, CANAL)
                : new Notification.Builder(context);
        return b.setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle("RomsLibrary")
                .setContentText(texto)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(alTocar)
                .setProgress(1000, Math.max(permil, 0), permil < 0)
                .build();
    }

    private static void crearCanal(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm != null && nm.getNotificationChannel(CANAL) == null) {
            NotificationChannel canal = new NotificationChannel(CANAL, "Descargas", NotificationManager.IMPORTANCE_LOW);
            canal.setDescription("Progreso de las descargas de juegos");
            nm.createNotificationChannel(canal);
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String texto = intent != null && intent.getStringExtra(EXTRA_TEXTO) != null
                ? intent.getStringExtra(EXTRA_TEXTO) : "Descargando...";
        Notification notificacion = construir(this, texto, -1);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(ID_NOTIFICACION, notificacion, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(ID_NOTIFICACION, notificacion);
        }
        if (wakeLock == null) {
            PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "RomLibrary:descarga");
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire(6 * 60 * 60 * 1000L); // tope de seguridad: 6 h.
        }
        if (wifiLock == null) {
            WifiManager wm = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wm != null) {
                wifiLock = wm.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "RomLibrary:descarga");
                wifiLock.setReferenceCounted(false);
                wifiLock.acquire();
            }
        }
        // No se relanza solo si Android lo mata: las descargas viven en los
        // hilos de la app, sin ellas el servicio no tiene nada que hacer.
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        if (wifiLock != null && wifiLock.isHeld()) {
            wifiLock.release();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
