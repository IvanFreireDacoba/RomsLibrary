# RomsLibrary

Tu propia librería de juegos retro en tu servidor, con una app Android para
instalarlos en la consola (Retroid, móviles...) y sincronizar las partidas
entre consolas.

> **Legal:** RomsLibrary sirve para ordenar y mantener una librería de juegos
> propia, sincronizar partidas entre consolas y que los creadores de juegos
> fanmade publiquen los suyos. No incluye ROMs ni concede derechos sobre juegos
> u otros contenidos de terceros. **No está permitido publicar como públicos
> juegos con copyright**; **los juegos privados deben obtenerse de manera
> legítima**. No nos responsabilizamos de los usos contrarios a estas normas.
> Ver [LEGAL.md](LEGAL.md). La licencia MIT cubre solo el código.

Este repositorio es el **servicio** (y su despliegue). La app Android está en
[RomsLibrary-App](https://github.com/IvanFreireDacoba/RomsLibrary-App); su
APK se descarga de la sección *Releases* de ese repositorio.

- **Servicio** (`servicio/`): guarda los juegos, sirve la app y se federa
  con otras librerías públicas.
- **Índice** (`servicio/app/indice.py`): servicio aparte con la lista de
  librerías públicas. El oficial está en `https://sund.es`; no aloja juegos.
- **Partidas** (`servicio/app/partidas.py`): servicio aparte con la identidad
  de las consolas, sus partidas y la vinculación entre ellas. Uno solo para
  todos, en `https://sund.es/partidas/`: las partidas no dependen de la
  librería de la que venga el juego. No guarda juegos.
- **Gestor web** (`https://tu-dominio/gestor/`): subir, editar, borrar y
  descargar juegos; ajustes del servicio. Protegido con Authentik.
- **App Android** ([RomsLibrary-App](https://github.com/IvanFreireDacoba/RomsLibrary-App)):
  una sola APK para todo el mundo; cada uno la conecta a la librería que
  quiera (por defecto `https://sund.es`).

## Cómo encaja todo

```
                  internet (HTTPS)
                         │
                   ┌─────▼─────┐
                   │   Caddy   │  certificados automáticos
                   └─┬───────┬─┘
     /APIs, /federacion│       │ /gestor  (Authentik delante)
                   ┌───▼───────▼──┐        ┌────────────┐
                   │ romslibrary  │◄──────►│ Authentik  │ (+ PostgreSQL)
                   │  (FastAPI)   │        └────────────┘
                   └──────┬───────┘
                          │ volumen /datos: ROMs, carátulas,
                          │ SQLite (ajustes, federación, versiones)

  Las partidas no pasan por tu librería: las apps las guardan en el servicio
  de partidas de sund.es, sea cual sea la librería.
```

### Librería pública o privada, y juegos compartidos

- **Privada** (por defecto): sin su clave no se ve nada.
- **Pública**: se da de alta en el índice (`https://sund.es`) y se ve sin
  clave, pero **solo lo que marques como «Compartido»**. Se activa en el
  gestor (*Ajustes del servicio*) aceptando antes el aviso legal. Volver a
  privada se puede siempre y te saca del índice al momento.
- **Cada juego** es «Solo para mí» o «Compartido», y se elige **siempre al
  subirlo** (por defecto, «Solo para mí»; se cambia en *Editar*). Compartir
  exige indicar **por qué se puede compartir**: homebrew o juego propio,
  licencia libre (cuál), permiso del titular (de quién), fan game original o
  enlace a la web de su autor. Si el contenido es un juego publicado según
  No-Intro/Redump (por el contenido, no por el nombre; también ROM hacks), el
  gestor lo avisa en rojo y pide confirmación expresa, sin impedirlo ni
  guardar quién confirma. Solo lo
  compartido sale por la API sin clave, por la federación y en las copias
  entre librerías; lo «Solo para mí» no sale nunca.
- **Acceso a la librería entera:** con su clave, o desde la app con
  **«Solicitar acceso»**: la consola manda su nombre e identificador y el
  gestor enseña la solicitud para **aceptarla o rechazarla**. Cada consola
  aceptada tiene su propio token (no se reparte la clave) y se le puede
  quitar el acceso cuando quieras. Da acceso solo a tus consolas: dar a
  otras personas juegos que no puedes compartir es distribuirlos.

La app descarga los juegos de otra librería **directamente de ella**: tu
servicio nunca reenvía juegos ajenos. Los juegos de otras librerías **no se
pueden editar** desde la tuya: solo descargarlos o **copiarlos** a tu
librería (entran como «Solo para mí»). Si dos librerías tienen el mismo
juego (mismo SHA-256), se muestra una sola vez.

### Índice de librerías públicas

Un servicio aparte, con su propio contenedor y su base de datos: solo
guarda el nombre y la URL de cada librería pública y emite los tickets con
los que una librería lee el catálogo de otra. Las librerías que no
contactan en 7 días dejan de listarse. Quien administra el índice puede
retirar una librería (por ejemplo, tras un aviso de un titular de derechos)
y entonces ya no puede volver a darse de alta sola:

```bash
docker exec <contenedor-del-índice> python indice.py listar
docker exec <contenedor-del-índice> python indice.py retirar <id|url> "motivo"
docker exec <contenedor-del-índice> python indice.py readmitir <id|url>
```

Para montar un índice propio: la misma imagen con `uvicorn indice:app` y un
volumen para `/datos`; luego apunta tus librerías a él con
`ROMSLIBRARY_INDICE_URL`.

### Versiones de los juegos

La identidad de un juego no es su nombre (lo pone quien sube) sino su
**cadena de SHA-256**: v1 → v2 → v3. Cada juego tiene un id fijo y su
historial (`versiones.py`); los que ya estaban entraron solos como v1.

- **Añadir update** (gestor → *Editar*): el ROM nuevo sustituye al actual
  **con el mismo nombre de fichero** y su SHA se añade al historial. Solo se
  acepta si el código de la **cabecera** del ROM (`cabeceras.py`: GB/GBC,
  GBA, N64, GameCube, PS1, PS2, PSP, también dentro de .zip) coincide con el
  del juego: un juego nunca entra como versión de otro distinto. Si el juego tiene
  cabecera, la nueva también tiene que tenerla (no se puede saltar); los
  formatos sin cabecera legible (.chd, .cso, .rvz) piden confirmación.
  Tampoco se aceptan: el SHA de otro juego, la versión actual ni una anterior
  (nunca se vuelve atrás) ni otra extensión. Solo juegos de un fichero.
- **La app** (`ActualizadorRoms`, al abrirse, cada 10 min como mucho) pide
  `accion=versiones` (versiones superadas con su tamaño y la actual de cada
  juego), calcula el SHA solo de los ficheros de la consola con un tamaño que
  coincida (con caché), sube las partidas, baja la versión actual a un
  temporal, comprueba su SHA y la pone **con el nombre que tenía en la
  consola**: los emuladores siguen encontrando las partidas (los *save
  states* pueden no valer entre versiones). La v1 y la v2 de dos consolas
  pasan las dos a la actual.

Límite conocido: los romhacks conservan el código del juego base (todos los
hacks de un mismo juego llevan su código), así que la cabecera no distingue un
hack de otro sobre la misma base.

### Verificación de volcados (No-Intro / Redump)

`verificacion.py` comprueba cada juego (su versión actual) contra las bases de
volcados buenos que usa RetroArch: No-Intro (GB, GBC, GBA, N64) y Redump
(PS1, PS2, PSP, GameCube). Las descarga de
[libretro-database](https://github.com/libretro/libretro-database)
(CC-BY-SA 4.0) a `/datos/dats` y las renueva cada 14 días. Se calcula el SHA-1
como en esas bases (el ROM de dentro del .zip, N64 en orden .z64, cada pista en
PS1). En el gestor se ve un distintivo en la tarjeta y el detalle en *Editar*:

- **✔ verificado**: coincide con un volcado oficial (con su nombre).
- **⚠ dudoso**: disco con el mismo número de serie y el MISMO tamaño que el
  oficial pero otro contenido: datos alterados (disco dañado o imagen
  modificada). Suele no arrancar.
- **otro volcado**: mismo número de serie, otro tamaño (otro programa u otro
  corte de pistas, frecuente en PS1): normalmente funciona.
- **desconocido**: no está en la base (romhacks, traducciones, homebrew).

Es solo un aviso: nunca impide subir ni actualizar.

### Confianza y linaje entre librerías

- El **índice** guarda una **confianza** (0-100) por librería que pone SOLO
  su administrador: `python indice.py confianza <id|url> <0-99>` en el contenedor del índice.
  La librería de **referencia** (`ROMSLIBRARY_INDICE_REFERENCIA`, sund.es)
  vale siempre 100. Cada 6 h el índice lee los catálogos y calcula una
  **sugerencia** (`indice.py reputacion [ahora]`) comparando con la
  referencia: juegos con su mismo SHA actual o más al día (su cadena la
  continúa) suman, los anticuados no, cada contradicción de historial resta
  25; solo pesa del todo con 20 juegos en común y tiene tope 95. Nunca se
  aplica sola.
- **Linaje** (`linaje.py`): las cadenas se juntan por prioridad: la propia,
  la referencia y las de confianza ≥ 70 (de más a menos). Una cadena que
  extiende la aceptada la sustituye (una librería de confianza puede llevar un
  juego más al día que la referencia); una que contradice se descarta. Nunca
  se ordena por fechas: subir hoy una versión vieja no la hace nueva.
- Efectos: de otras librerías no se muestran versiones superadas; en tus
  juegos el gestor avisa de «hay versión nueva» si una librería de confianza
  la tiene; la app actualiza también desde ellas (descargando directamente).

### Fuentes externas (web del autor)

Para juegos que su autor publica en su web (traducciones...). En *Editar →
Fuente externa* (o *Añadir desde un enlace del autor* al subir) se guarda el
enlace de descarga directa y la página del autor (`externos.py`):

- Al guardarlo, el servidor lo descarga una vez: si es la versión actual se
  guarda; si es una versión nueva del mismo juego (cabecera) entra como
  update; si no, se rechaza. Se guarda copia aquí solo como respaldo.
- La app descarga **de ese enlace** (cuenta para las estadísticas del autor),
  comprueba el SHA y, si no responde o no coincide, baja la copia de la
  librería. También las actualizaciones.
- Cada 12 h se mira (sin descargar) si el fichero del enlace ha cambiado
  (ETag, fecha, tamaño): el gestor avisa («enlace cambiado») y se importa
  volviendo a guardar el enlace. Nunca se importa solo.
- Solo enlaces https a direcciones públicas, comprobando cada redirección
  (`redsegura.py`), y tienen que ser el fichero, no una página.

### Consolas y partidas

Las partidas (y los *save states*) van al **servicio de partidas** de
sund.es, aparte de las librerías: se conservan aunque la librería de la que
vino el juego cierre o pase a privada, y la misma consola tiene la misma
identidad uses la librería que uses.

- Cada consola tiene un **identificador** público `XXXXX-XXXXX` y un nombre.
  Su contraseña es el ANDROID_ID (en PC, una credencial aleatoria): solo
  viaja al servicio de partidas para abrir una sesión y allí se guarda como
  HMAC con una clave que no está en la base de datos. Las librerías nunca la
  ven.
- Se sincronizan al abrir la app: gana la copia más reciente. De cada
  partida se guardan las 3 versiones anteriores, para recuperarla si se
  sobrescribe.
- *Sincronizar consolas* vincula consolas **tuyas** para que compartan las
  partidas: la otra tiene que **aceptar**, y puedes desvincular o bloquear
  en cualquier momento.

## Despliegue

### Requisitos

- Un servidor con Docker y Docker Compose (Linux, 2 GB de RAM mínimo por
  Authentik; el disco que necesites para tus juegos).
- Dos nombres DNS apuntando a la IP del servidor: uno para el servicio
  (`romslibrary.tu-dominio.es`) y otro para el inicio de sesión
  (`auth.romslibrary.tu-dominio.es`).
- Puertos **80** y **443** abiertos (Caddy pide los certificados HTTPS solo).

### Pasos

```bash
git clone <este repositorio> romslibrary
cd romslibrary/despliegue
cp .env.example .env
nano .env        # dominios, nombre de tu librería y los 3 secretos
docker compose up -d --build
```

Los secretos se generan con `openssl rand -base64 36` (uno distinto para
`ROMSLIBRARY_SECRETO_PROXY`, `AUTHENTIK_SECRET_KEY` y `AUTHENTIK_PG_PASS`).

El primer arranque tarda un par de minutos (Authentik prepara su base de
datos y aplica la configuración de RomsLibrary). Después:

1. Abre `https://romslibrary.tu-dominio.es/gestor/` → te lleva al inicio de
   sesión. Entra con **`ADMIN` / `admin`** (o lo que pusieras en `.env`).
2. **Cambia la contraseña** en `https://auth.romslibrary.tu-dominio.es`
   (icono de usuario → *Configuración*).
3. En el gestor, abre **Ajustes del servicio**: comprueba el nombre y la URL
   pública. La librería empieza **privada**; para hacerla pública, marca la
   casilla y acepta el aviso legal. Ahí está también la **clave de acceso**
   de la app (solo la piden las librerías privadas).
4. Sube tus juegos desde el propio gestor (tus juegos, de tus copias
   originales).

### Conectar la app

1. Instala la APK oficial: está en las *Releases* de RomsLibrary-App y en
   cualquier servicio RomsLibrary (`https://<servicio>/APIs/RomLibraryApp?accion=apk`).
2. Al abrirla por primera vez, en *Configura RomsLibrary* pon la URL de tu
   servicio (por defecto viene `https://sund.es`), el nombre de la consola y,
   solo si tu librería es privada, la **clave de acceso** del gestor.
3. La app enseña su **identificador** debajo del título: es el que usarás
   para vincular otras consolas tuyas. Las partidas van siempre al servicio
   de partidas de sund.es, no a tu librería.

La app se actualiza sola: tu servicio le ofrece siempre la última versión
oficial.

### Copias de seguridad

Todo lo importante está en volúmenes de Docker:

| Volumen | Contenido |
|---|---|
| `romslibrary-datos` | juegos, carátulas y `romslibrary.db` |
| `authentik-postgres` | usuarios y configuración de Authentik |

```bash
docker compose stop romslibrary
docker run --rm -v despliegue_romslibrary-datos:/datos -v "$PWD":/copia alpine \
    tar czf /copia/romslibrary-datos.tgz -C /datos .
docker compose start romslibrary
```

### Actualizar

```bash
git pull
docker compose up -d --build
```

## Seguridad

- El gestor solo es accesible a través de Authentik; además, el servicio
  rechaza cualquier petición al gestor que no traiga el secreto compartido
  con el proxy (`ROMSLIBRARY_SECRETO_PROXY`), así que no basta con llegar al
  contenedor saltándose Caddy.
- Las librerías públicas no piden clave a la app; las privadas, una clave de
  acceso por servicio (regenerable desde el gestor), que la app manda en una
  cabecera, nunca en la URL.
  Las partidas solo se pueden tocar presentando a la vez el ID interno de la
  consola y su identificador; los demás solo ven el identificador.
- Entre librerías públicas nunca viaja un secreto: cada acceso usa un ticket
  de 10 minutos emitido y validado por el índice, y nadie puede registrar la
  URL de otro (el índice comprueba que la URL es tuya antes de darla de alta).
- Sube solo juegos que tengas derecho a distribuir. Si tu librería es
  pública, los juegos que subas los podrán ver y copiar las demás librerías
  públicas y descargar cualquiera con la app.

## Desarrollo

`servicio/app/` (FastAPI). `docker build -t romslibrary servicio/`. Las
variables de entorno están documentadas en `servicio/app/config.py`.

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
  administrador responde de lo que guarda, publica y comparte en su librería.
  Ver [LEGAL.md](LEGAL.md).
- Una librería **pública** solo enseña sus juegos «Compartidos», a cualquiera
  que use la app sin clave y a las demás librerías públicas; los «Solo para
  mí» no salen de ella.
- Las librerías nacen privadas; hacer una pública exige aceptar un aviso
  legal en el gestor.
- El índice solo guarda la lista de librerías públicas (nombre y dirección):
  no aloja, no transmite y no reenvía los juegos de las demás librerías. Las
  descargas van directas de cada librería a la consola.
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
repositorio (https://github.com/IvanFreireDacoba/RomsLibrary-Servicio) para que otros puedan
encontrar el original.
