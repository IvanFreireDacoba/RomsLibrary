# RomsLibrary — App Android

App para consolas Android (Retroid, móviles…) que se conecta a una librería
[RomsLibrary](https://github.com/IvanFreireDacoba/RomsLibrary-Servicio) e
instala los juegos en la consola (lista para ES-DE), y sincroniza las partidas
entre tus consolas.

## Descargar

La APK está en **[Releases](https://github.com/IvanFreireDacoba/RomsLibrary-App/releases/latest)**.
Una vez instalada se actualiza sola.

## Uso

1. Instala la APK (permite «instalar apps desconocidas» para el navegador o el
   gestor de archivos).
2. Elige la carpeta de ROMs (la misma que usa ES-DE) y concede el acceso a
   todos los archivos.
3. En *Configura RomsLibrary*:
   - **Servidor:** viene `https://sund.es` (librería pública, sin clave).
     Puedes poner cualquier otra librería RomsLibrary.
   - **Clave de acceso:** solo para librerías privadas (se ve en su gestor web).
   - **Nombre de la consola:** con él y su identificador `XXXXX-XXXXX` la
     reconocerán las consolas con las que sincronices partidas.

   Si pulsas *Más tarde* puedes descargar juegos igual, pero la consola no se
   registra y **no se sincronizan partidas**.

### Partidas

Con la consola registrada, las partidas y los *save states* se guardan en la
librería al abrir la app y al desinstalar un juego, y se recuperan al
instalarlo (gana siempre la copia más reciente). Desde *Sincronizar consolas*
puedes vincular otra consola tuya para compartir partidas: la otra tiene que
aceptarlo, y puedes desvincular o bloquear cuando quieras.

## Compilar

Gradle 8.7, SDK 34, JDK 17+:

    gradle :app:assembleDebug

o dentro de Docker con `docker_build.sh` (usa `~/android-sdk` y
`~/gradle-dist/gradle-8.7` del host).

La firma usa `app/debug.keystore`, que **no** está en el repositorio: genera el
tuyo (`keytool -genkeypair -keystore app/debug.keystore -alias romlibrary-debug
-storepass romlibrary-debug -keypass romlibrary-debug -keyalg RSA -validity 10000`).
Las APK firmadas con otra clave no se instalan encima de la oficial.

## Licencia

[MIT](LICENSE): puedes usar, copiar, modificar y redistribuir el código como
quieras, también en proyectos comerciales; solo hay que conservar el aviso de
copyright y la licencia.

No es obligatorio, pero si lo usas o lo modificas se agradece un enlace a este
repositorio (https://github.com/IvanFreireDacoba/RomsLibrary-App) para que otros puedan
encontrar el original.
