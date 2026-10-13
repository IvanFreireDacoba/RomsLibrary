"""
Linaje de versiones entre librerías: qué SHA es más nuevo que cuál.

Cada librería publica, para cada juego, su CADENA de versiones (de la más
vieja a la actual). Aquí se juntan las cadenas por orden de prioridad:

    1. la de esta misma librería (cada una manda en lo suyo),
    2. la librería de referencia del índice (sund.es, confianza 100),
    3. las demás librerías con confianza >= UMBRAL, de más a menos confianza.

Las librerías por debajo del umbral NO aportan linaje: sus juegos se ven,
pero nunca deciden qué versión es más nueva.

Una cadena se acepta si es coherente con lo ya aceptado para ese juego:
- si la extiende (contiene la cadena aceptada al principio y añade versiones
  después) pasa a ser la buena: una librería de confianza puede llevar un
  juego más al día que la referencia;
- si es un trozo inicial de la aceptada, solo está desactualizada: no cambia nada;
- si contradice (las dos cadenas siguen versiones distintas a partir de un
  punto, o una dice que es más viejo algo que la otra pone delante) se
  DESCARTA y se anota como conflicto.

Nunca se usan fechas: el orden lo da la cadena, así que subir hoy una versión
vieja no la hace parecer nueva.
"""

UMBRAL = 70


def _prefijo(a: list[str], b: list[str]) -> bool:
    return len(a) <= len(b) and b[:len(a)] == a


class Linaje:
    def __init__(self):
        self.cadenas: list[dict] = []        # {"shas": [...], "fuente": id}
        self.de_sha: dict[str, int] = {}     # sha -> índice en self.cadenas
        self.conflictos: list[dict] = []

    def anadir(self, shas: list[str], fuente: str) -> None:
        """Añade una cadena (vieja -> actual). Llamar en orden de prioridad."""
        shas = [s for i, s in enumerate(shas) if s and s not in shas[:i]]
        if not shas:
            return
        tocadas = {self.de_sha[s] for s in shas if s in self.de_sha}
        if not tocadas:
            self._guardar(len(self.cadenas), shas, fuente, nueva=True)
            return
        if len(tocadas) > 1:
            # Une dos juegos que ya se tenían por distintos: contradice.
            self.conflictos.append({"fuente": fuente, "shas": shas, "motivo": "mezcla juegos distintos"})
            return
        i = tocadas.pop()
        aceptada = self.cadenas[i]["shas"]
        if _prefijo(aceptada, shas):
            if len(shas) > len(aceptada):
                self._guardar(i, shas, fuente, nueva=False)     # la extiende: versiones más nuevas
        elif _prefijo(shas, aceptada):
            pass                                                # desactualizada, pero coherente
        else:
            self.conflictos.append({"fuente": fuente, "shas": shas, "motivo": "historial distinto"})

    def _guardar(self, i: int, shas: list[str], fuente: str, nueva: bool) -> None:
        if nueva:
            self.cadenas.append({"shas": shas, "fuente": fuente})
        else:
            self.cadenas[i] = {"shas": shas, "fuente": fuente}
        for s in shas:
            self.de_sha[s] = i

    # ---------- consultas ----------

    def actual_de(self, sha: str) -> str | None:
        """La versión más nueva conocida del juego al que pertenece `sha`."""
        i = self.de_sha.get(sha)
        return self.cadenas[i]["shas"][-1] if i is not None else None

    def superada(self, sha: str) -> bool:
        actual = self.actual_de(sha)
        return actual is not None and actual != sha

    def numero(self, sha: str) -> int | None:
        i = self.de_sha.get(sha)
        return self.cadenas[i]["shas"].index(sha) + 1 if i is not None else None

    def superadas(self):
        """(sha viejo, n, sha actual, n actual) de todas las versiones superadas."""
        for c in self.cadenas:
            shas = c["shas"]
            for n, s in enumerate(shas[:-1], start=1):
                yield s, n, shas[-1], len(shas)
