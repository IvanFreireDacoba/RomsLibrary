# RomsLibrary para Wii: boot.dol para el Homebrew Channel.
# Se compila con empaquetado/construir.sh (Docker devkitpro/devkitppc).
# `make -f Makefile linux` hace la versión de PC para probar (ver plataforma_linux.c).

.DEFAULT_GOAL := wii
MBEDTLS ?= build/mbedtls
COMUNES := main ajustes caratulas catalogo dibujo guion instalar red servicio
MBEDTLS_FUENTES := $(wildcard $(MBEDTLS)/library/*.c)

ifeq ($(MAKECMDGOALS),linux)
CC := gcc
PKG := pkg-config
DIR := build/linux
PLATAFORMA := plataforma_linux canal_linux
MAQUINA :=
EXTRA_LIBS := -lpthread
else
ifeq ($(strip $(DEVKITPPC)),)
$(error Falta DEVKITPPC: compila con empaquetado/construir.sh)
endif
include $(DEVKITPPC)/wii_rules
# (con ruta completa: make 4.3 no pasa el PATH de wii_rules a $(shell))
PKG := $(DEVKITPRO)/portlibs/wii/bin/powerpc-eabi-pkg-config
DIR := build/wii
PLATAFORMA := plataforma_wii canal_wii
MAQUINA := $(MACHDEP) -I$(LIBOGC_INC)
EXTRA_LIBS :=
endif

PAQUETES := SDL2_ttf SDL2_image sdl2 jansson
CFLAGS := -O2 -g -Wall -Wextra -Wno-unused-parameter -Wno-sign-compare -Wno-format-truncation $(MAQUINA) \
          $(shell $(PKG) --cflags $(PAQUETES)) -Ifuente -I$(MBEDTLS)/include \
          '-DMBEDTLS_USER_CONFIG_FILE="mbedtls_rl.h"'
CFLAGS_MBEDTLS := -O2 $(MAQUINA) -Ifuente -I$(MBEDTLS)/include -I$(MBEDTLS)/library \
                  '-DMBEDTLS_USER_CONFIG_FILE="mbedtls_rl.h"'
LIBS := $(shell $(PKG) --libs $(PAQUETES)) $(EXTRA_LIBS) -lm

OBJ := $(addprefix $(DIR)/,$(addsuffix .o,$(COMUNES) $(PLATAFORMA) recursos))
OBJ_MBEDTLS := $(patsubst $(MBEDTLS)/library/%.c,$(DIR)/mbedtls/%.o,$(MBEDTLS_FUENTES))

wii: build/boot.dol
linux: build/romslibrary-linux

build/boot.dol: $(DIR)/romslibrary.elf
	elf2dol $< $@

$(DIR)/romslibrary.elf: $(OBJ) $(DIR)/libmbedtls.a
	$(CC) -o $@ $(OBJ) $(DIR)/libmbedtls.a $(LIBS) $(MAQUINA) -Wl,-Map,$(DIR)/romslibrary.map

build/romslibrary-linux: $(OBJ) $(DIR)/libmbedtls.a
	$(CC) -o $@ $(OBJ) $(DIR)/libmbedtls.a $(LIBS)

$(DIR)/libmbedtls.a: $(OBJ_MBEDTLS)
	$(AR) rcs $@ $^

$(DIR)/mbedtls/%.o: $(MBEDTLS)/library/%.c fuente/mbedtls_rl.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS_MBEDTLS) -c $< -o $@

$(DIR)/%.o: fuente/%.c fuente/*.h
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

$(DIR)/recursos.o: build/recursos.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c $< -o $@

build/recursos.c: empaquetado/incrustar.py recursos/DejaVuSans.ttf recursos/DejaVuSans-Bold.ttf
	python3 empaquetado/incrustar.py $@ recursos/DejaVuSans.ttf recursos/DejaVuSans-Bold.ttf /etc/ssl/certs/ca-certificates.crt

clean:
	rm -rf build/wii build/linux build/boot.dol build/romslibrary-linux build/recursos.c

.PHONY: wii linux clean
