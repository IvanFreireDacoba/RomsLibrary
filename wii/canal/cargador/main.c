/*
 * Cargador del canal de RomsLibrary: lo que arranca el menú de la Wii al abrir
 * el canal. Busca apps/romslibrary/boot.dol en la SD y en el USB y lo lanza
 * (como hace el Homebrew Channel), así la app sigue actualizándose sola en su
 * carpeta y el canal no hay que volver a instalarlo nunca.
 *
 * Arranca en 0x3400 (stub.S). Se enlaza en 0x81330000 (como el HBC): el boot.dol se copia por debajo
 * (0x80003F00...) sin pisar este código. La ruta del boot.dol se le pasa en
 * argv (la convención de libogc), justo debajo de BASE.
 */
#include <fat.h>
#include <gccore.h>
#include <ogc/machine/processor.h>
#include <ogc/usbstorage.h>
#include <sdcard/wiisd_io.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wiiuse/wpad.h>

#define BASE 0x81330000
#define ARGS_MAX 1024
#define MIN_DIR 0x80003400
#define MAX_DIR (BASE - 1 - ARGS_MAX)
#define ARGS_DIR (MAX_DIR + 1)

typedef struct {
    u32 pos_texto[7], pos_datos[11];
    u32 dir_texto[7], dir_datos[11];
    u32 tam_texto[7], tam_datos[11];
    u32 bss, tam_bss, entrada;
} CabeceraDol;

static void *xfb;

/* Solo si algo va mal: un mensaje en pantalla y volver al menú con cualquier botón. */
static void error(const char *mensaje) {
    VIDEO_Init();
    GXRModeObj *modo = VIDEO_GetPreferredMode(NULL);
    xfb = MEM_K0_TO_K1(SYS_AllocateFramebuffer(modo));
    console_init(xfb, 20, 20, modo->fbWidth, modo->xfbHeight, modo->fbWidth * VI_DISPLAY_PIX_SZ);
    VIDEO_Configure(modo);
    VIDEO_SetNextFramebuffer(xfb);
    VIDEO_SetBlack(FALSE);
    VIDEO_Flush();
    VIDEO_WaitVSync();
    printf("\n\n\n   RomsLibrary\n\n   %s\n\n   Pulsa cualquier boton para volver al menu de Wii.\n", mensaje);
    WPAD_Init();
    PAD_Init();
    for (;;) {
        WPAD_ScanPads();
        PAD_ScanPads();
        if (WPAD_ButtonsDown(0) || PAD_ButtonsDown(0)) break;
        VIDEO_WaitVSync();
    }
    SYS_ResetSystem(SYS_RETURNTOMENU, 0, 0);
}

static FILE *abrir(char *ruta, size_t n) {
    static const char *sitios[] = {"sd:/apps/romslibrary/boot.dol", "usb:/apps/romslibrary/boot.dol"};
    for (int i = 0; i < 2; i++) {
        FILE *f = fopen(sitios[i], "rb");
        if (f) {
            snprintf(ruta, n, "%s", sitios[i]);
            return f;
        }
    }
    return NULL;
}

int main(void) {
    fatMountSimple("sd", &__io_wiisd);
    char ruta[64];
    FILE *f = abrir(ruta, sizeof ruta);
    /* Sin la app en la SD: el disco USB puede tardar en despertar. */
    for (int i = 0; !f && i < 16; i++) {
        if (fatMountSimple("usb", &__io_usbstorage)) f = abrir(ruta, sizeof ruta);
        if (!f) usleep(500000);
    }
    if (!f) error("No encuentro apps/romslibrary/boot.dol ni en la SD ni en el USB.\n"
                  "   Descomprime RomsLibrary-Wii.zip en la raiz de la SD.");

    static CabeceraDol c __attribute__((aligned(32)));
    if (fread(&c, 1, sizeof c, f) != sizeof c) error("boot.dol no se puede leer.");
    /* Cada sección a su sitio, comprobando que no pisa este cargador. */
    for (int i = 0; i < 18; i++) {
        u32 pos = i < 7 ? c.pos_texto[i] : c.pos_datos[i - 7];
        u32 dir = i < 7 ? c.dir_texto[i] : c.dir_datos[i - 7];
        u32 tam = i < 7 ? c.tam_texto[i] : c.tam_datos[i - 7];
        if (!tam) continue;
        if (dir < MIN_DIR || dir + tam > MAX_DIR) error("boot.dol no es valido (fuera de memoria).");
        if (fseek(f, pos, SEEK_SET) || fread((void *)dir, 1, tam, f) != tam) error("boot.dol esta cortado.");
        DCFlushRange((void *)dir, tam);
        if (i < 7) ICInvalidateRange((void *)dir, tam);
    }
    fclose(f);
    if (c.bss && (c.bss < MIN_DIR || c.bss + c.tam_bss > MAX_DIR)) error("boot.dol no es valido (bss).");

    /* argv[0] = la ruta, para que la app sepa su carpeta (sd: o usb:). */
    u32 *p = (u32 *)c.entrada;
    if (p[1] == ARGV_MAGIC) {
        struct __argv *a = (struct __argv *)&p[2];
        size_t l = strlen(ruta) + 2;                      /* "ruta\0\0" */
        memset((void *)ARGS_DIR, 0, l);
        memcpy((void *)ARGS_DIR, ruta, l - 2);
        a->argvMagic = ARGV_MAGIC;
        a->commandLine = (char *)ARGS_DIR;
        a->length = l;
        DCFlushRange(&p[2], sizeof *a);
        DCFlushRange((void *)ARGS_DIR, l);
    }

    fatUnmount("sd:");
    fatUnmount("usb:");
    /* Sin «stub» del Homebrew Channel: al salir, la app vuelve al menú de Wii. */
    memset((void *)0x80001800, 0, 16);
    DCFlushRange((void *)0x80001800, 16);

    SYS_ResetSystem(SYS_SHUTDOWN, 0, 0);
    u32 nivel;
    _CPU_ISR_Disable(nivel);                   /* sin interrupciones hasta que la app ponga las suyas */
    (void)nivel;
    *(vu32 *)0x800000F8 = 0x0E7BE2C0;   /* velocidad del bus y de la CPU (como el HBC) */
    *(vu32 *)0x800000FC = 0x2B73A840;
    ((void (*)(void))c.entrada)();
    return 0;
}
