# RomsLibrary

Tu propia librería de juegos retro en tu servidor, con una app Android para
instalarlos en la consola (Retroid, móviles...) y sincronizar las partidas
entre consolas.

Este repositorio es el **servicio** (y su despliegue). La app Android está en
[RomsLibrary-App](https://github.com/IvanFreireDacoba/RomsLibrary-App); su
APK se descarga de la sección *Releases* de ese repositorio.

- **Servicio** (`servicio/`): guarda los juegos, sirve la app, gestiona las
  consolas y sus partidas, y se federa con otras librerías públicas.
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
                          │ volumen /datos: ROMs, carátulas, partidas,
                          │ SQLite (consolas, ajustes, federación)
```

### Librería pública o privada

- **Pública** (por defecto): compartes tus juegos con las demás librerías
  públicas y ves los suyos. Tu servicio se da de alta solo en el índice
  (`https://sund.es`) en cuanto tiene URL pública.
- **Privada**: solo ves y ofreces tus juegos.

Se cambia en cualquier momento desde el gestor (*Ajustes del servicio*). Los
juegos de otras librerías **no se pueden editar** desde la tuya: solo
descargarlos o **copiarlos** a tu librería personal (eligiendo el nombre).
Si dos librerías tienen el mismo juego (mismo SHA-256), se muestra una sola
vez.

### Consolas y partidas

Al configurar la app, cada consola recibe un **identificador** público
`XXXXX-XXXXX` y un nombre. Las partidas (y los *save states*) se
sincronizan al abrir la app: gana siempre la copia más reciente. Desde
*Sincronizar consolas* puedes vincular consolas **tuyas** para que compartan
las partidas; la otra consola tiene que **aceptar**, y puedes desvincular o
bloquear en cualquier momento.

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
3. En el gestor, abre **Ajustes del servicio**: comprueba el nombre, la URL
   pública y si quieres la librería pública o privada. Ahí está también la
   **clave de acceso** de la app (solo la piden las librerías privadas).
4. Sube tus juegos desde el propio gestor (tus juegos, de tus copias
   originales).

### Conectar la app

1. Instala la APK oficial: está en las *Releases* de RomsLibrary-App y en
   cualquier servicio RomsLibrary (`https://<servicio>/APIs/RomLibraryApp?accion=apk`).
2. Al abrirla por primera vez, en *Configura RomsLibrary* pon la URL de tu
   servicio (por defecto viene `https://sund.es`), el nombre de la consola y,
   solo si tu librería es privada, la **clave de acceso** del gestor.
3. La app enseña su **identificador** debajo del título: es el que usarás
   para vincular otras consolas tuyas.

La app se actualiza sola: tu servicio le ofrece siempre la última versión
oficial.

### Copias de seguridad

Todo lo importante está en volúmenes de Docker:

| Volumen | Contenido |
|---|---|
| `romslibrary-datos` | juegos, carátulas, partidas de las consolas y `romslibrary.db` |
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
  públicas.

## Desarrollo

`servicio/app/` (FastAPI). `docker build -t romslibrary servicio/`. Las
variables de entorno están documentadas en `servicio/app/config.py`.

## Licencia

[MIT](LICENSE): puedes usar, copiar, modificar y redistribuir el código como
quieras, también en proyectos comerciales; solo hay que conservar el aviso de
copyright y la licencia.

No es obligatorio, pero si lo usas o lo modificas se agradece un enlace a este
repositorio (https://github.com/IvanFreireDacoba/RomsLibrary-Servicio) para que otros puedan
encontrar el original.
