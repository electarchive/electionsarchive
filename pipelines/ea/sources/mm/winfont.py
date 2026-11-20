"""Decoder for the Win Innwa family of legacy Burmese fonts (WinResearcher, WinTaungyi) used in the UEC's 2010 and
2015 results tables. The PDFs carry each glyph as a Latin letter, so extracted text reads as `OD;usefav;` and must be
mapped to Unicode and put in logical order. The table was fitted against the township names that the same
publication prints in Unicode elsewhere."""

import re

CONSONANT = {
    "u": "က",
    "c": "ခ",
    "*": "ဂ",
    "C": "ဃ",
    "i": "င",
    "p": "စ",
    "q": "ဆ",
    "Z": "ဇ",
    "#": "ဉ",
    "n": "ည",
    "ñ": "ည",
    "w": "တ",
    "x": "ထ",
    "'": "ဒ",
    '"': "ဓ",
    "e": "န",
    "E": "န",
    "y": "ပ",
    "z": "ဖ",
    "A": "ဗ",
    "b": "ဘ",
    "r": "မ",
    ",": "ယ",
    "&": "ရ",
    "½": "ရ",
    "v": "လ",
    "0": "ဝ",
    "o": "သ",
    "[": "ဟ",
    "V": "ဠ",
    "t": "အ",
    "O": "ဥ",
    "P": "ဏ",
    "X": "ထ",
    "{": "ဧ",
    "Ó": "ဉာ",
}
# Conjunct glyphs: a consonant stacked under the one before.
STACK = {
    "´": "္ဒ",
    "¾": "္ဂ",
    "Å": "္တ",
    "å": "္တ",
    "ú": "္က",
    "Á": "္ဗ",
    "©": "္ခ",
    "®": "္မ",
    "Ö": "္ဏ",
    "¬": "္ထ",
    "ö": "္",
}
# Dependent signs with their place in the Unicode order of a syllable.
SIGN: dict[str, list[tuple[str, int]]] = {
    "s": [("ျ", 1)],
    "j": [("ြ", 2)],
    "B": [("ြ", 2)],
    "N": [("ြ", 2)],
    "M": [("ြ", 2)],
    "G": [("ွ", 3)],
    "S": [("ှ", 4)],
    "T": [("ွ", 3), ("ှ", 4)],
    "R": [("ျ", 1), ("ွ", 3)],
    "I": [("ှ", 4), ("ု", 7)],
    "a": [("ေ", 5)],
    "d": [("ိ", 6)],
    "D": [("ီ", 6)],
    "k": [("ု", 7)],
    "K": [("ု", 7)],
    "l": [("ူ", 7)],
    "L": [("ူ", 7)],
    "J": [("ဲ", 7)],
    "m": [("ာ", 8)],
    "g": [("ါ", 8)],
    ":": [("ါ", 8), ("်", 11)],
    "H": [("ံ", 9)],
    "h": [("့", 10)],
    "U": [("့", 10)],
    "Y": [("့", 10)],
    "f": [("်", 11)],
    ";": [("း", 12)],
}
PREFIX = {"a", "j", "B", "N", "M"}
KINZI = "F"


def decode(legacy: str) -> str:
    """Unicode text for a string in a Win Innwa font; characters with no known glyph become U+FFFD."""
    out: list[str] = []
    prefix: list[tuple[str, int]] = []
    base: list[str] = []
    signs: list[tuple[str, int]] = []

    def flush() -> None:
        nonlocal base, signs
        if base:
            marks = sorted(set(signs), key=lambda x: x[1])
            text = "".join(t for t, _ in marks)
            if base == ["ဥ"]:
                if "ီ" in text:
                    base, text = ["ဦ"], text.replace("ီ", "")
                elif "်" in text or "့" in text:
                    base = ["ဉ"]
            out.append("".join(base) + text)
        base, signs = [], []

    for ch in legacy:
        if ch in PREFIX:
            flush()
            prefix.extend(SIGN[ch])
        elif ch in CONSONANT:
            flush()
            base, signs, prefix = [CONSONANT[ch]], prefix, []
        elif ch in STACK and base:
            base.append(STACK[ch])
        elif ch == KINZI and base:
            base.insert(0, "င်္")
        elif ch in SIGN and base:
            signs.extend(SIGN[ch])
        elif ch.isascii() and not ch.isalpha() and ch not in ":;'":
            flush()
            out.append(ch)
        else:
            flush()
            out.append("�")
    flush()
    return re.sub(r"\s+", " ", "".join(out)).strip()
