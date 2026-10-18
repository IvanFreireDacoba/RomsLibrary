# Cargador del canal (contenido 1 del WAD). Lo compila construir_canal.sh.
include $(DEVKITPPC)/wii_rules

CFLAGS := -Os -Wall $(MACHDEP) -I$(LIBOGC_INC)
LDFLAGS := $(MACHDEP) -Wl,--section-start,.init=0x81330000 -L$(LIBOGC_LIB)

cargador.dol: cargador.elf
	elf2dol $< $@

cargador.elf: main.o stub.o
	$(CC) -o $@ $^ $(LDFLAGS) -lfat -lwiiuse -lbte -logc -lm

main.o: main.c
	$(CC) $(CFLAGS) -c $< -o $@

stub.o: stub.S
	$(CC) $(MACHDEP) -c $< -o $@

clean:
	rm -f main.o stub.o cargador.elf cargador.dol
