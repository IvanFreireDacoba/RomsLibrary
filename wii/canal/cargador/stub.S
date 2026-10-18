/*
 * Un canal arranca en 0x3400 con la MMU apagada (el punto de entrada del DOL
 * no se usa). rvl.ld deja esta sección .stub justo ahí. Prepara lo que el crt0
 * de libogc espera encontrar (lo mismo que hace el Homebrew Channel):
 * cachés, traducción de direcciones por BAT y MMU encendida; luego salta a
 * __app_start (0x81330000, el cargador de verdad).
 *
 *   0x80000000 -> 0x00000000  MEM1+MEM2 bajos, con caché     (IBAT0/DBAT0)
 *   0x90000000 -> 0x10000000  MEM2, con caché                 (IBAT4/DBAT4)
 *   0xC0000000 -> 0x00000000  sin caché (hardware, DMA)       (DBAT1)
 *   0xD0000000 -> 0x10000000  MEM2 sin caché                  (DBAT5)
 */
        .section .stub, "ax"
        .globl _stub_start
_stub_start:
        /* HID0: cachés apagadas e invalidadas, BHT/BTIC y data flush assist. */
        lis     3, 0x0011
        ori     3, 3, 0x0c64
        mtspr   1008, 3
        isync
        /* MSR: solo coma flotante (todo lo demás apagado). */
        li      4, 0x2000
        mtmsr   4
        /* HID0: cachés de instrucciones y datos encendidas. */
        ori     3, 3, 0xc000
        mtspr   1008, 3
        isync

        /* Todos los BAT a cero. */
        li      0, 0
        mtspr   528, 0
        mtspr   530, 0
        mtspr   532, 0
        mtspr   534, 0
        mtspr   536, 0
        mtspr   538, 0
        mtspr   540, 0
        mtspr   542, 0
        mtspr   560, 0
        mtspr   562, 0
        mtspr   564, 0
        mtspr   566, 0
        mtspr   568, 0
        mtspr   570, 0
        mtspr   572, 0
        mtspr   574, 0
        isync

        /* Registros de segmento: nada fuera de los BAT. */
        lis     0, 0x8000
        mtsr    0, 0
        mtsr    1, 0
        mtsr    2, 0
        mtsr    3, 0
        mtsr    4, 0
        mtsr    5, 0
        mtsr    6, 0
        mtsr    7, 0
        mtsr    8, 0
        mtsr    9, 0
        mtsr    10, 0
        mtsr    11, 0
        mtsr    12, 0
        mtsr    13, 0
        mtsr    14, 0
        mtsr    15, 0
        isync

        /* BAT0 (I y D): 256 MB en 0x80000000 -> 0, con caché, lectura/escritura. */
        li      3, 2
        lis     4, 0x8000
        ori     4, 4, 0x1fff
        mtspr   529, 3          /* IBAT0L */
        mtspr   528, 4          /* IBAT0U */
        mtspr   537, 3          /* DBAT0L */
        mtspr   536, 4          /* DBAT0U */
        isync
        /* BAT4 (I y D): 256 MB en 0x90000000 -> 0x10000000. */
        addis   3, 3, 0x1000
        addis   4, 4, 0x1000
        mtspr   561, 3          /* IBAT4L */
        mtspr   560, 4          /* IBAT4U */
        mtspr   569, 3          /* DBAT4L */
        mtspr   568, 4          /* DBAT4U */
        isync
        /* DBAT1: 256 MB en 0xC0000000 -> 0, sin caché (WIMG 0101). */
        li      3, 0x2a
        lis     4, 0xc000
        ori     4, 4, 0x1fff
        mtspr   539, 3          /* DBAT1L */
        mtspr   538, 4          /* DBAT1U */
        isync
        /* DBAT5: 256 MB en 0xD0000000 -> 0x10000000, sin caché. */
        addis   3, 3, 0x1000
        addis   4, 4, 0x1000
        mtspr   571, 3          /* DBAT5L */
        mtspr   570, 4          /* DBAT5U */
        isync
        /* HID4: activa los BAT 4-7. */
        lis     3, 0x8200
        mtspr   1011, 3
        isync

        /* MMU encendida (MSR IR|DR) y a __app_start. */
        lis     3, __app_start@h
        ori     3, 3, __app_start@l
        mtsrr0  3
        mfmsr   3
        ori     3, 3, 0x30
        mtsrr1  3
        rfi
