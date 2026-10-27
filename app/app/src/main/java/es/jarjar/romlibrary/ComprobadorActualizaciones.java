package es.jarjar.romlibrary;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;

import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import java.util.concurrent.TimeUnit;

/**
 * Aviso HIPER-LIGERO en segundo plano: una vez al día (WorkManager, con
 * restricciones de red y batería, respetando Doze) pregunta a la librería si
 * algún juego YA DESCARGADO en esta consola tiene una versión más nueva y, si
 * la hay, muestra UNA notificación local ("Hay juegos para actualizar"). No
 * descarga nada aquí: solo mira metadatos (accion=versiones) y el SHA de los
 * ficheros candidatos por tamaño, con la caché de hashes. La actualización de
 * verdad la hace la app al abrirse (ver ActualizadorRoms / MainActivity).
 */
public final class ComprobadorActualizaciones extends Worker {

    private static final String TRABAJO = "comprobar-actualizaciones";
    private static final String CANAL = "actualizaciones";
    private static final int ID_NOTIFICACION = 4321;

    public ComprobadorActualizaciones(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    /** Programa el chequeo diario (idempotente: no reinicia el que ya hubiera). */
    static void programar(Context ctx) {
        Constraints constraints = new Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .setRequiresBatteryNotLow(true)
                .build();
        PeriodicWorkRequest trabajo = new PeriodicWorkRequest.Builder(
                ComprobadorActualizaciones.class, 1, TimeUnit.DAYS)
                .setConstraints(constraints)
                .setInitialDelay(6, TimeUnit.HOURS)   // no pisar el chequeo de "al abrir"
                .build();
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
                TRABAJO, ExistingPeriodicWorkPolicy.KEEP, trabajo);
    }

    @NonNull
    @Override
    public Result doWork() {
        Context ctx = getApplicationContext();
        Cuenta cuenta = Cuenta.cargar(ctx);
        if (cuenta.url == null || cuenta.url.isEmpty()) {
            return Result.success();
        }
        Uri arbol = CarpetaRomsUtil.obtenerUriValido(
                ctx, ctx.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE));
        if (arbol == null) {
            return Result.success();
        }
        int pendientes = ActualizadorRoms.pendientes(ctx, cuenta.url, cuenta.clave, arbol);
        if (pendientes > 0) {
            avisar(ctx, pendientes);
        }
        return Result.success();
    }

    private void avisar(Context ctx, int pendientes) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel canal = new NotificationChannel(
                    CANAL, "Actualizaciones de juegos", NotificationManager.IMPORTANCE_DEFAULT);
            canal.setDescription("Avisa cuando un juego descargado tiene una versión nueva.");
            NotificationManager nm = ctx.getSystemService(NotificationManager.class);
            if (nm != null) {
                nm.createNotificationChannel(canal);
            }
        }
        Intent abrir = new Intent(ctx, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT
                | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ? PendingIntent.FLAG_IMMUTABLE : 0);
        PendingIntent pi = PendingIntent.getActivity(ctx, 0, abrir, flags);
        String texto = pendientes == 1
                ? "1 juego de tu biblioteca tiene una versión nueva. Abre RomsLibrary para actualizarlo."
                : pendientes + " juegos de tu biblioteca tienen una versión nueva. Abre RomsLibrary para actualizarlos.";
        Notification n = new NotificationCompat.Builder(ctx, CANAL)
                .setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle("Hay juegos para actualizar")
                .setContentText(texto)
                .setStyle(new NotificationCompat.BigTextStyle().bigText(texto))
                .setAutoCancel(true)
                .setContentIntent(pi)
                .build();
        try {
            NotificationManagerCompat.from(ctx).notify(ID_NOTIFICACION, n);
        } catch (SecurityException e) {
            // Sin permiso POST_NOTIFICATIONS (Android 13+): se avisará al abrir la app.
            GuardadosSync.log("aviso de actualizaciones sin permiso de notificaciones");
        }
    }
}
