/*
 * Ajustes de mbedTLS para la app (sobre la configuración por defecto):
 * - Sin red, temporizadores ni ficheros propios de mbedTLS: la red la pone red.c.
 * - Entropía: la da la app (mbedtls_hardware_poll -> plat_entropia).
 * - Sin comprobar las fechas de los certificados: el reloj de una Wii puede
 *   estar mal y dejaría de conectar. La cadena sí se comprueba entera contra
 *   los certificados raíz de Mozilla.
 * - TLS 1.2 (TLS 1.3 en mbedTLS 3.6 necesita PSA Crypto).
 */
#undef MBEDTLS_NET_C
#undef MBEDTLS_TIMING_C
#undef MBEDTLS_HAVE_TIME_DATE
#undef MBEDTLS_FS_IO
#undef MBEDTLS_PSA_ITS_FILE_C
#undef MBEDTLS_PSA_CRYPTO_STORAGE_C
#undef MBEDTLS_SSL_PROTO_TLS1_3
#define MBEDTLS_NO_PLATFORM_ENTROPY
#define MBEDTLS_ENTROPY_HARDWARE_ALT
/* Reloj en milisegundos: lo da la app (red.c, con plat_ms). */
#define MBEDTLS_PLATFORM_MS_TIME_ALT
