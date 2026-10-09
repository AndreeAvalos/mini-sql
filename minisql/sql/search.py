"""Buscar y reemplazar en texto, sin Qt (la barra de búsqueda de los editores lo usa)."""
import bisect
import re
from dataclasses import dataclass

IDENT_CHARS = r"[\w$#]"        # lo que forma un identificador de Oracle (para "palabra completa")


@dataclass(frozen=True)
class SearchOptions:
    case: bool = False          # distinguir mayúsculas
    whole_word: bool = False    # solo palabras completas (no "EMP" dentro de "EMPLEADO")
    regex: bool = False         # el texto es una expresión regular


def compile_search(pattern: str, options: SearchOptions):
    """Expresión regular de la búsqueda, o None si no hay nada que buscar.
    Lanza ValueError con un mensaje en español si la expresión regular no es válida."""
    if not pattern:
        return None
    body = pattern if options.regex else re.escape(pattern)
    if options.whole_word:
        body = rf"(?<!{IDENT_CHARS})(?:{body})(?!{IDENT_CHARS})"
    flags = re.MULTILINE | (0 if options.case else re.IGNORECASE)
    try:
        return re.compile(body, flags)
    except re.error as e:
        raise ValueError(f"Expresión regular inválida: {e}") from None


def find_matches(text: str, pattern: str, options: SearchOptions = SearchOptions()):
    """[(inicio, fin)] de cada coincidencia (índices de Python). Las vacías (p. ej. '^') se ignoran."""
    rx = compile_search(pattern, options)
    if rx is None:
        return []
    return [m.span() for m in rx.finditer(text) if m.end() > m.start()]


def replacement_for(text: str, span, pattern: str, replacement: str, options: SearchOptions = SearchOptions()):
    """Texto que reemplaza la coincidencia `span`. Con expresiones regulares acepta \\1, \\g<nombre>…"""
    if not options.regex:
        return replacement
    m = compile_search(pattern, options).match(text, span[0])
    if not m or m.end() != span[1]:
        return replacement
    try:
        return m.expand(replacement)
    except (re.error, IndexError) as e:
        raise ValueError(f"Reemplazo inválido: {e}") from None


def replace_all(text: str, pattern: str, replacement: str, options: SearchOptions = SearchOptions()):
    """(texto nuevo, cantidad de reemplazos). Para probar la lógica; el editor reemplaza in situ."""
    matches = find_matches(text, pattern, options)
    parts, last = [], 0
    for span in matches:
        parts += [text[last:span[0]], replacement_for(text, span, pattern, replacement, options)]
        last = span[1]
    parts.append(text[last:])
    return "".join(parts), len(matches)


class QtPositions:
    """Convierte índices de Python a posiciones de Qt. Qt cuenta en UTF-16: un emoji u otro carácter
    fuera del plano básico ocupa 2 posiciones en Qt y 1 en Python."""

    def __init__(self, text: str):
        self.wide = [i for i, ch in enumerate(text) if ord(ch) > 0xFFFF]

    def __call__(self, index: int) -> int:
        return index + bisect.bisect_left(self.wide, index) if self.wide else index

    def to_python(self, position: int) -> int:
        """Inverso: posición de Qt -> índice de Python."""
        index = position
        for i, wide_index in enumerate(self.wide):
            if wide_index + i < position:
                index -= 1
        return index
