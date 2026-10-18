# RomsLibrary — App Android

App para consolas Android (Retroid, móviles…) que se conecta a una librería
[RomsLibrary](https://github.com/IvanFreireDacoba/RomsLibrary-Servicio) e
instala los juegos en la consola (lista para ES-DE), y sincroniza las partidas
entre tus consolas.

> **Legal:** RomsLibrary sirve para ordenar y mantener una librería de juegos
> propia, sincronizar partidas entre consolas y que los creadores de juegos
> fanmade publiquen los suyos. No incluye ROMs ni concede derechos sobre juegos
> u otros contenidos de terceros. **No está permitido publicar como públicos
> juegos con copyright**; **los juegos privados deben obtenerse de manera
> legítima**. No nos responsabilizamos de los usos contrarios a estas normas.
> Ver [LEGAL.md](LEGAL.md). La licencia MIT cubre solo el código.

## Descargar

La APK está en **[Releases](https://github.com/IvanFreireDacoba/RomsLibrary-App/releases/latest)**.
Una vez instalada se actualiza sola.

## Uso

1. Instala la APK (permite «instalar apps desconocidas» para el navegador o el
   gestor de archivos).
2. Elige la carpeta de ROMs (la misma que usa ES-DE) y concede el acceso a
   todos los archivos.
3. En *Configura RomsLibrary*:
   - **Servidor:** viene `https://sund.es`. Puedes poner cualquier otra
     librería RomsLibrary.
   - **Clave de acceso:** sin clave ves solo los juegos que la librería
     comparte (si es pública). Para verla entera, pon su clave o pulsa
     **Solicitar acceso**: su administrador acepta o rechaza esta consola
     desde su gestor, y al aceptarla la app la ve entera sin clave.
   - **Nombre de la consola:** con él y su identificador `XXXXX-XXXXX` la
     reconocerán las consolas con las que sincronices partidas.

   Si pulsas *Más tarde* puedes descargar juegos igual, pero la consola no se
   registra y **no se sincronizan partidas**.

### Juegos que se actualizan solos

Si la librería sube una versión nueva de un juego que tienes instalado (la
reconoce por su SHA-256, no por el nombre), la app la descarga al abrirse y
la pone en lugar de la vieja **con el mismo nombre**, después de subir tus
partidas: los emuladores siguen encontrándolas. Los *save states* pueden no
valer entre versiones. Solo se calcula el SHA de los ficheros cuyo tamaño
coincide con el de alguna versión vieja, así que no recorre toda la consola.

Los juegos que su autor publica en su web (traducciones...) se descargan de
su enlace, con el SHA comprobado; si el enlace falla, de la librería.

### Partidas

Con la consola registrada, las partidas y los *save states* se guardan al
abrir la app y al desinstalar un juego, y se recuperan al instalarlo (gana
siempre la copia más reciente). Van al **servicio de partidas** de sund.es
(`https://sund.es/partidas/`), aparte de las librerías: no dependen de la
librería de la que venga el juego, aunque cierre o pase a privada. El
ANDROID_ID es la contraseña de la consola: solo viaja ahí (por HTTPS) y se
guarda como HMAC; las librerías nunca lo ven.

Desde *Sincronizar consolas* puedes vincular otra consola tuya (Android, o
Linux/Steam Deck/Windows con [RomsLibrary-Cliente](https://github.com/IvanFreireDacoba/RomsLibrary-Cliente))
para compartir partidas: la otra tiene que aceptarlo, y puedes desvincular o
bloquear cuando quieras.

## Compilar

Gradle 8.7, SDK 34, JDK 17+:

    gradle :app:assembleDebug

o dentro de Docker con `docker_build.sh` (usa `~/android-sdk` y
`~/gradle-dist/gradle-8.7` del host).

La firma usa `app/debug.keystore`, que **no** está en el repositorio: genera el
tuyo (`keytool -genkeypair -keystore app/debug.keystore -alias romlibrary-debug
-storepass romlibrary-debug -keypass romlibrary-debug -keyalg RSA -validity 10000`).
Las APK firmadas con otra clave no se instalan encima de la oficial.

## Aviso legal

- RomsLibrary es un sistema para ordenar y mantener **tu propia librería de
  juegos**, sincronizar las partidas entre tus consolas y guardar sus datos de
  guardado, con un sistema de publicación para que los creadores de juegos
  fanmade publiquen gratis los suyos (enlazando a su web, donde se quedan las
  descargas). No incluye juegos, BIOS ni firmwares, ni dice dónde conseguirlos.
- **No está permitido publicar como públicos («Compartido») juegos con
  copyright** de terceros: solo los de su propio autor o titular, o con
  licencia libre o permiso. Dar acceso a tu librería también es compartir.
- **Todos los juegos privados deben obtenerse de manera legítima** (p. ej., la
  copia de seguridad de un original tuyo, que es solo para ti).
- **No nos responsabilizamos de cualquier uso contrario a estas normas**: cada
  persona responde de lo que guarda, publica y comparte. Ver [LEGAL.md](LEGAL.md).
- La app solo descarga lo que ofrece la librería a la que la conectes, o las
  otras librerías públicas que esta te enseñe (en ese caso, directamente de
  ellas). Quien gestiona cada librería es responsable de su contenido.
- ES-DE, RetroArch, Retroid, Steam y los demás nombres citados son marcas de
  sus dueños. Este es un proyecto independiente, sin relación con ellos.
- Aviso legal, retirada de contenidos y privacidad de los servicios que
  funcionan en sund.es (app oficial, librería «SunD» e índice):
  https://sund.es/romslibrary/aviso-legal.
- Si crees que algo publicado con este proyecto vulnera tus derechos,
  escribe a `support@sund.es` o abre un issue en este repositorio.

## Licencia

[MIT](LICENSE): puedes usar, copiar, modificar y redistribuir el código como
quieras, también en proyectos comerciales; solo hay que conservar el aviso de
copyright y la licencia.

No es obligatorio, pero si lo usas o lo modificas se agradece un enlace a este
repositorio (https://github.com/IvanFreireDacoba/RomsLibrary-App) para que otros puedan
encontrar el original.
