"""
Copia en <destino> los textos de licencia de lo que va dentro del paquete
(los enseña el apartado «Versión», ver romslibrary/legal.py) y escribe
INDICE.txt (fichero<TAB>título). Se ejecuta con el Python del empaquetado:
    python licencias.py <destino> <carpeta con las descargadas>
"""

import importlib.metadata as md
import shutil
import sys
from pathlib import Path

destino, descargadas = Path(sys.argv[1]), Path(sys.argv[2])
destino.mkdir(parents=True, exist_ok=True)
indice: dict[str, str] = {}

TITULOS = {
    "pygame-ce-LGPL.txt": "pygame-ce (LGPL 2.1)",
    "LICENSE.sdl2.txt": "SDL2 (zlib)", "LICENSE.sdl2_image.txt": "SDL2_image (zlib)",
    "LICENSE.sdl2_mixer.txt": "SDL2_mixer (zlib)", "SDL_ttf-LICENSE.txt": "SDL2_ttf (zlib)",
    "LICENSE.freetype.txt": "FreeType (FTL)", "LICENSE.png.txt": "libpng", "LICENSE.jpeg.txt": "libjpeg",
    "LICENSE.tiff.txt": "libtiff", "LICENSE.webp.txt": "libwebp (BSD)", "LICENSE.zlib.txt": "zlib",
    "LICENSE.FLAC.txt": "FLAC (BSD)", "LICENSE.ogg-vorbis.txt": "Ogg / Vorbis (BSD)",
    "LICENSE.opus.txt": "Opus (BSD)", "LICENSE.opusfile.txt": "opusfile (BSD)",
    "LICENSE.mpg123.txt": "mpg123 (LGPL 2.1)", "LICENSE.fluidsynth.txt": "FluidSynth (LGPL 2.1)",
    "gamecontrollerdb-LICENSE.txt": "SDL_GameControllerDB (zlib)",
    "harfbuzz-COPYING.txt": "HarfBuzz (MIT antiguo)", "libsndfile-COPYING.txt": "libsndfile (LGPL 2.1)",
    "wavpack-COPYING.txt": "WavPack (BSD)", "brotli-LICENSE.txt": "Brotli (MIT)",
}

for f in sorted(descargadas.iterdir()):
    if f.is_file():
        shutil.copy(f, destino / f.name)
        indice[f.name] = TITULOS.get(f.name, f.name)


def de_paquete(dist: str, titulo: str, nombre: str) -> None:
    try:
        ficheros = md.distribution(dist).files or []
    except md.PackageNotFoundError:
        return
    for f in ficheros:
        if any(k in f.name.upper() for k in ("LICENSE", "COPYING")):
            shutil.copy(f.locate(), destino / nombre)
            indice[nombre] = titulo
            return


de_paquete("certifi", "certifi (MPL 2.0)", "certifi-LICENSE.txt")
de_paquete("pyinstaller", "PyInstaller (GPL 2 con excepción para el cargador)", "pyinstaller-COPYING.txt")
for candidato in (Path(sys.base_prefix) / "LICENSE.txt",
                  Path(sys.base_prefix) / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "LICENSE.txt"):
    if candidato.is_file():
        shutil.copy(candidato, destino / "python-LICENSE.txt")
        indice["python-LICENSE.txt"] = f"Python {sys.version_info[0]}.{sys.version_info[1]} (PSF)"
        break
for ruta, nombre, titulo in (("/usr/share/doc/fonts-dejavu-core/copyright", "dejavu-copyright.txt", "Fuente DejaVu"),
                             ("/usr/share/doc/libssl1.1/copyright", "openssl-copyright.txt", "OpenSSL 1.1"),
                             ("/usr/share/doc/libbz2-1.0/copyright", "bzip2-copyright.txt", "bzip2"),
                             ("/usr/share/doc/liblzma5/copyright", "xz-copyright.txt", "xz / liblzma"),
                             ("/usr/share/doc/libgcc-s1/copyright", "gcc-runtime-copyright.txt",
                              "Bibliotecas de ejecución de GCC (libgcc, libstdc++, libgomp)")):
    # Bibliotecas del sistema de la build de Linux (en Windows van otras, ya en la licencia de Python).
    if sys.platform != "win32" and Path(ruta).is_file():
        shutil.copy(ruta, destino / nombre)
        indice[nombre] = titulo

(destino / "INDICE.txt").write_text("".join(f"{k}\t{v}\n" for k, v in sorted(indice.items())), encoding="utf-8")
print(f"licencias: {len(indice)} en {destino}")
