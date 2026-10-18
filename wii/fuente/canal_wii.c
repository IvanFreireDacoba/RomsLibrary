#include "canal.h"

#include <fat.h>
#include <gccore.h>
#include <malloc.h>
#include <network.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wiiuse/wpad.h>

static u32 be32(const unsigned char *p) { return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3]; }
static u32 redondear(u32 v, u32 a) { return (v + a - 1) & ~(a - 1); }

int canal_version_instalada(void) {
    u32 tam = 0;
    if (ES_GetStoredTMDSize(CANAL_TITULO, &tam) < 0 || !tam) return -1;
    signed_blob *s = memalign(32, redondear(tam, 32));
    if (!s) return -1;
    int v = -1;
    if (ES_GetStoredTMD(CANAL_TITULO, s, tam) >= 0) v = ((tmd *)SIGNATURE_PAYLOAD(s))->title_version;
    free(s);
    return v;
}

int canal_comprobar(int ios, char *error, size_t n) {
    u32 tam = 0;
    if (ios > 0 && (ES_GetStoredTMDSize(0x0000000100000000ULL | (u32)ios, &tam) < 0 || !tam)) {
        snprintf(error, n, "No encuentro el IOS %d (el cIOS de d2x). Sin él la Wii no acepta canales homebrew; "
                 "instálalo con ModMii o usa un gestor de WAD.", ios);
        return -1;
    }
    return 0;
}

/* Copia alineada a 32 bytes (lo que pide IOS para el DMA). */
static void *alineado(const unsigned char *datos, u32 tam) {
    void *p = memalign(32, redondear(tam ? tam : 1, 32));
    if (p) memcpy(p, datos, tam);
    return p;
}

int canal_instalar(const unsigned char *wad, size_t tam, int ios, char *error, size_t n) {
    /* Cabecera del WAD: tamaños de cada parte, cada una alineada a 0x40. */
    if (tam < 0x40 || be32(wad) != 0x20) {
        snprintf(error, n, "El fichero descargado no es un WAD.");
        return -1;
    }
    u32 t_cert = be32(wad + 8), t_crl = be32(wad + 12), t_tik = be32(wad + 16), t_tmd = be32(wad + 20),
        t_datos = be32(wad + 24);
    u32 o_cert = 0x40, o_crl = o_cert + redondear(t_cert, 0x40), o_tik = o_crl + redondear(t_crl, 0x40),
        o_tmd = o_tik + redondear(t_tik, 0x40), o_datos = o_tmd + redondear(t_tmd, 0x40);
    if ((size_t)o_datos + t_datos > tam || !t_cert || !t_tik || !t_tmd) {
        snprintf(error, n, "El WAD está incompleto.");
        return -1;
    }
    void *cert = alineado(wad + o_cert, t_cert), *tik = alineado(wad + o_tik, t_tik), *tmdb = alineado(wad + o_tmd, t_tmd);
    void *trozo = memalign(32, 0x10000);
    if (!cert || !tik || !tmdb || !trozo) {
        free(cert); free(tik); free(tmdb); free(trozo);
        snprintf(error, n, "Sin memoria.");
        return -1;
    }
    tmd *t = (tmd *)SIGNATURE_PAYLOAD((signed_blob *)tmdb);

    /* Recargar el IOS deja inservibles la red, los discos y los mandos. */
    if (ios > 0) {
        WPAD_Shutdown();
        net_deinit();
        fatUnmount("sd:");
        fatUnmount("usb:");
        if (IOS_ReloadIOS(ios) < 0) {
            snprintf(error, n, "No se pudo cargar el IOS %d.", ios);
            free(cert); free(tik); free(tmdb); free(trozo);
            return -1;
        }
    }
    s32 r = ES_AddTicket(tik, t_tik, cert, t_cert, NULL, 0);
    const char *paso = "el ticket";
    if (r >= 0) {
        paso = "el TMD";
        r = ES_AddTitleStart(tmdb, t_tmd, cert, t_cert, NULL, 0);
    }
    u32 pos = o_datos;
    for (int i = 0; r >= 0 && i < t->num_contents; i++) {
        tmd_content *c = &t->contents[i];
        u32 cifrado = redondear((u32)c->size, 16);        /* AES-CBC: múltiplo de 16 */
        if ((size_t)pos + cifrado > tam) {
            r = -1;
            paso = "un contenido (WAD corto)";
            break;
        }
        paso = "un contenido";
        s32 cfd = ES_AddContentStart(t->title_id, c->cid);
        if (cfd < 0) {
            r = cfd;
            break;
        }
        for (u32 hecho = 0; r >= 0 && hecho < cifrado; hecho += 0x10000) {
            u32 l = cifrado - hecho < 0x10000 ? cifrado - hecho : 0x10000;
            memcpy(trozo, wad + pos + hecho, l);
            r = ES_AddContentData(cfd, trozo, l);
        }
        if (r >= 0) r = ES_AddContentFinish(cfd);
        pos += redondear(cifrado, 0x40);
    }
    if (r >= 0) {
        paso = "el final de la instalación";
        r = ES_AddTitleFinish();
    } else {
        ES_AddTitleCancel();
    }
    free(cert); free(tik); free(tmdb); free(trozo);
    if (r < 0) {
        snprintf(error, n, "La Wii rechazó %s (error %d). Si es -1017 o -2011, el IOS %d no acepta canales homebrew.",
                 paso, (int)r, ios);
        return -1;
    }
    return 0;
}

void canal_volver_al_menu(void) { SYS_ResetSystem(SYS_RETURNTOMENU, 0, 0); }
