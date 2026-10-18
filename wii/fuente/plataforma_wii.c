/*
 * Wii (libogc): red por /dev/net (IOS), SD y USB por libfat, mandos por
 * WPAD (Wiimote y Clásico) y PAD (GameCube).
 *
 * El Homebrew Channel arranca las apps con IOS58, que trae USB 2.0, así que el
 * disco de USB Loader GX se monta tal cual («usb:»). SDL2main (la función main
 * de verdad, que llama a la nuestra) ya inicia IOS, los mandos y libfat. Solo FAT32: libfat no
 * entiende NTFS ni las particiones WBFS.
 */
#include "plataforma.h"
#include "guion.h"

#include <dirent.h>
#include <errno.h>
#include <fat.h>
#include <gccore.h>
#include <network.h>
#include <ogc/conf.h>
#include <ogc/lwp_watchdog.h>
#include <ogc/usbstorage.h>
#include <sdcard/wiisd_io.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/statvfs.h>
#include <unistd.h>
#include <wiiuse/wpad.h>

#ifndef POLLIN
#define POLLIN 0x0001          /* el de IOS (el estándar) */
#endif
/* Apodo de la consola (SYSCONF «IPL.NIK»): hasta 10 caracteres UTF-16 BE. */
#define LARGO_APODO 22

static char carpeta_app[256] = "sd:/apps/romslibrary";
static volatile int salir;
static int usb_montado, sd_montado;

static void al_apagar(void) { salir = 1; }
static void al_reiniciar(u32 irq, void *ctx) { (void)irq; (void)ctx; salir = 1; }
static void al_apagar_wiimote(s32 canal) { (void)canal; salir = 1; }

int plat_debe_salir(void) { return salir || guion_fin(); }

static int montado(const char *raiz) {
    DIR *d = opendir(raiz);
    if (d) closedir(d);
    return d != NULL;
}

int plat_iniciar(int argc, char **argv) {
    SYS_SetPowerCallback(al_apagar);
    SYS_SetResetCallback(al_reiniciar);
    WPAD_Init();
    WPAD_SetPowerButtonCallback(al_apagar_wiimote);
    PAD_Init();

    /* SDL2main (antes de main) ya ha recargado IOS y montado sd: y usb: con
     * fatInitDefault(). Algunos discos tardan en despertar: se reintenta ~10 s. */
    sd_montado = montado("sd:/") || fatMountSimple("sd", &__io_wiisd);
    usb_montado = montado("usb:/");
    /* argv[0] = "sd:/apps/romslibrary/boot.dol" (o usb:/...). */
    if (argc > 0 && argv && argv[0] && strstr(argv[0], ":/")) {
        snprintf(carpeta_app, sizeof carpeta_app, "%s", argv[0]);
        char *barra = strrchr(carpeta_app, '/');
        if (barra) *barra = 0;
        /* "usb1:" -> "usb:" (libfat monta "usb"). */
        if (!strncmp(carpeta_app, "usb1:", 5)) memmove(carpeta_app + 3, carpeta_app + 4, strlen(carpeta_app + 4) + 1);
    } else if (!sd_montado && usb_montado) {
        snprintf(carpeta_app, sizeof carpeta_app, "usb:/apps/romslibrary");
    }
    /* Pruebas automáticas (ver guion.h). */
    char ruta[300], texto[4096];
    snprintf(ruta, sizeof ruta, "%s/guion-pruebas.txt", carpeta_app);
    FILE *g = fopen(ruta, "r");
    if (g) {
        size_t l = fread(texto, 1, sizeof texto - 1, g);
        texto[l] = 0;
        fclose(g);
        guion_iniciar(texto);
    }
    return 0;
}

void plat_esperar_usb(void) {
    for (int i = 0; i < 16 && !usb_montado && !salir; i++) {
        usb_montado = fatMountSimple("usb", &__io_usbstorage);
        if (!usb_montado) usleep(500000);
    }
}

/* Mira el sector 0 (y el de arranque de la 1.ª partición) para decir qué es. */
const char *plat_problema_usb(void) {
    if (usb_montado) return NULL;
    if (!__io_usbstorage.startup() || !__io_usbstorage.isInserted())
        return "No hay ningún disco USB conectado.";
    static u8 sector[4096] __attribute__((aligned(32)));
    if (!__io_usbstorage.readSectors(0, 1, sector)) return "No se puede leer el disco USB.";
    const char *nada = "El disco USB no tiene una partición FAT32 que RomsLibrary pueda usar.";
    if (sector[510] != 0x55 || sector[511] != 0xAA) return nada;
    if (!memcmp(sector + 3, "NTFS    ", 8)) return "El disco USB está en NTFS: RomsLibrary necesita FAT32.";
    if (!memcmp(sector + 3, "EXFAT   ", 8)) return "El disco USB está en exFAT: RomsLibrary necesita FAT32.";
    u8 tipo = sector[0x1C2];
    if (tipo == 0xEE) return "El disco USB tiene tabla de particiones GPT: tiene que ser MBR con una partición FAT32.";
    if (tipo == 0x07) {
        u32 lba = sector[0x1C6] | sector[0x1C7] << 8 | sector[0x1C8] << 16 | (u32)sector[0x1C9] << 24;
        if (__io_usbstorage.readSectors(lba, 1, sector)) {
            if (!memcmp(sector + 3, "EXFAT   ", 8)) return "El disco USB está en exFAT: RomsLibrary necesita FAT32.";
            if (!memcmp(sector + 3, "NTFS    ", 8)) return "El disco USB está en NTFS: RomsLibrary necesita FAT32.";
        }
    }
    return nada;
}

int plat_panoramica(void) { return CONF_GetAspectRatio() == CONF_ASPECT_16_9; }

void plat_terminar(void) {
    net_deinit();
    if (usb_montado) fatUnmount("usb:");
    if (sd_montado) fatUnmount("sd:");
    WPAD_Shutdown();
}

const char *plat_carpeta_app(void) { return carpeta_app; }

const char *plat_raiz(const char *dispositivo) {
    if (!strcmp(dispositivo, "usb")) return usb_montado ? "usb:" : NULL;
    if (!strcmp(dispositivo, "sd")) return sd_montado ? "sd:" : NULL;
    return NULL;
}

long long plat_libre(const char *raiz) {
    struct statvfs s;
    char ruta[16];
    snprintf(ruta, sizeof ruta, "%s/", raiz);
    if (statvfs(ruta, &s) != 0) return -1;
    return (long long)s.f_bavail * (long long)s.f_frsize;
}

int plat_red_iniciar(char *error, size_t n) {
    s32 r = -1;
    for (int i = 0; i < 5 && !salir; i++) {
        r = net_init();
        if (r >= 0) break;
        if (r != -EAGAIN) usleep(500000);
    }
    if (r < 0) {
        snprintf(error, n, "No se pudo conectar a la red (%d). Revisa la conexión en Ajustes de la Wii > Internet.", (int)r);
        return -1;
    }
    return 0;
}

int plat_conectar(const char *host, int puerto, char *error, size_t n) {
    struct hostent *h = net_gethostbyname(host);
    if (!h || !h->h_addr_list || !h->h_addr_list[0]) {
        snprintf(error, n, "No se encuentra %s (DNS).", host);
        return -1;
    }
    s32 s = net_socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (s < 0) {
        snprintf(error, n, "Sin sockets libres (%d).", (int)s);
        return -1;
    }
    struct sockaddr_in dir;
    memset(&dir, 0, sizeof dir);
    dir.sin_family = AF_INET;
    dir.sin_port = htons(puerto);
    memcpy(&dir.sin_addr, h->h_addr_list[0], sizeof dir.sin_addr);
    s32 r = net_connect(s, (struct sockaddr *)&dir, sizeof dir);
    if (r < 0) {
        net_close(s);
        snprintf(error, n, "No se pudo conectar con %s (%d).", host, (int)r);
        return -1;
    }
    return s;
}

int plat_enviar(int s, const void *datos, int n) {
    int r = net_send(s, datos, n, 0);
    return r < 0 ? -1 : r;
}

int plat_recibir(int s, void *datos, int n, int espera_ms) {
    struct pollsd p = {.socket = s, .events = POLLIN, .revents = 0};
    int r = net_poll(&p, 1, espera_ms);
    if (r == 0) return -2;
    if (r < 0) return -1;
    if (n > 32768) n = 32768;       /* IOS no acepta lecturas mucho mayores */
    r = net_recv(s, datos, n, 0);
    return r < 0 ? -1 : r;
}

void plat_cerrar(int s) {
    if (s >= 0) net_close(s);
}

/*
 * La Wii no ofrece un generador aleatorio por hardware a las apps. Se toma la
 * variación del contador de tiempo (60,75 MHz) alrededor de llamadas a IOS,
 * que no tardan siempre lo mismo, más la MAC; mbedTLS lo mezcla con SHA-512.
 */
void plat_entropia(unsigned char *salida, size_t n) {
    static u8 mac[6];
    static int con_mac;
    if (!con_mac) {
        net_get_mac_address(mac);
        con_mac = 1;
    }
    for (size_t i = 0; i < n; i++) {
        u64 a = gettime();
        u32 b;
        CONF_GetLanguage();                     /* ida y vuelta a IOS */
        b = (u32)(gettime() - a);
        salida[i] = (u8)(b ^ (b >> 8) ^ (b >> 16) ^ (u8)a ^ mac[i % 6]);
    }
}

uint64_t plat_ms(void) { return ticks_to_millisecs(gettime()); }

void plat_dormir_ms(int ms) { usleep(ms * 1000); }

void plat_nombre_consola(char *salida, size_t n) {
    char nombre[64] = {0};
    if (CONF_GetNickName((u8 *)nombre) > 0 && nombre[0]) {
        /* El apodo de la Wii va en UTF-16 BE: se queda solo lo ASCII. */
        size_t j = 0;
        for (int i = 0; i + 1 < LARGO_APODO && j + 1 < n; i += 2) {
            if (!nombre[i] && !nombre[i + 1]) break;
            if (!nombre[i] && nombre[i + 1] >= 32 && nombre[i + 1] < 127) salida[j++] = nombre[i + 1];
        }
        salida[j] = 0;
        if (j) return;
    }
    snprintf(salida, n, "Wii");
}

void plat_leer_botones(unsigned *pulsados, unsigned *mantenidos) {
    unsigned p = 0, m = 0;
    WPAD_ScanPads();
    PAD_ScanPads();
    for (int c = 0; c < 4; c++) {
        u32 wd = WPAD_ButtonsDown(c), wh = WPAD_ButtonsHeld(c);
        u16 gd = PAD_ButtonsDown(c), gh = PAD_ButtonsHeld(c);
        s8 gy = PAD_StickY(c);
        struct { u32 w; u16 g; unsigned b; } mapa[] = {
            {WPAD_BUTTON_UP | WPAD_CLASSIC_BUTTON_UP, PAD_BUTTON_UP, BOTON_ARRIBA},
            {WPAD_BUTTON_DOWN | WPAD_CLASSIC_BUTTON_DOWN, PAD_BUTTON_DOWN, BOTON_ABAJO},
            {WPAD_BUTTON_LEFT | WPAD_CLASSIC_BUTTON_LEFT, PAD_BUTTON_LEFT, BOTON_IZQUIERDA},
            {WPAD_BUTTON_RIGHT | WPAD_CLASSIC_BUTTON_RIGHT, PAD_BUTTON_RIGHT, BOTON_DERECHA},
            {WPAD_BUTTON_A | WPAD_CLASSIC_BUTTON_A, PAD_BUTTON_A, BOTON_A},
            {WPAD_BUTTON_B | WPAD_CLASSIC_BUTTON_B, PAD_BUTTON_B, BOTON_B},
            {WPAD_BUTTON_1 | WPAD_CLASSIC_BUTTON_X, PAD_BUTTON_START, BOTON_MENU},
            {WPAD_BUTTON_2 | WPAD_CLASSIC_BUTTON_Y, PAD_BUTTON_Y, BOTON_IDIOMA},
            {WPAD_BUTTON_MINUS | WPAD_CLASSIC_BUTTON_MINUS | WPAD_CLASSIC_BUTTON_FULL_L, PAD_TRIGGER_L, BOTON_ANTERIOR},
            {WPAD_BUTTON_PLUS | WPAD_CLASSIC_BUTTON_PLUS | WPAD_CLASSIC_BUTTON_FULL_R, PAD_TRIGGER_R, BOTON_SIGUIENTE},
            {WPAD_BUTTON_HOME | WPAD_CLASSIC_BUTTON_HOME, PAD_TRIGGER_Z, BOTON_SALIR},
        };
        for (size_t i = 0; i < sizeof mapa / sizeof mapa[0]; i++) {
            if ((wd & mapa[i].w) || (gd & mapa[i].g)) p |= mapa[i].b;
            if ((wh & mapa[i].w) || (gh & mapa[i].g)) m |= mapa[i].b;
        }
        /* Palanca del mando de GameCube como cruceta. */
        if (gy > 50) m |= BOTON_ARRIBA;
        if (gy < -50) m |= BOTON_ABAJO;
    }
    /* La palanca no tiene "pulsado": se detecta el flanco aquí. */
    static unsigned palanca_antes;
    unsigned palanca = m & (BOTON_ARRIBA | BOTON_ABAJO);
    p |= palanca & ~palanca_antes;
    palanca_antes = palanca;
    p |= guion_paso();
    *pulsados = p;
    *mantenidos = m | p;
}

void plat_fotograma(struct SDL_Renderer *r) { (void)r; }
