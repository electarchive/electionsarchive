"""Convert text set in the legacy Preeti font, which puts Devanagari glyphs on Latin code points, to Unicode."""

import re

# Sequences that join two typed characters into one letter, read before single characters.
SEQUENCES = {
    "O{": "ई",
    "P]": "ऐ",
    "pm": "ऊ",
    "Km": "फ्",
    "k'm": "फु",
    'k"m': "फू",
    "km": "फ",
    "Qm": "क्त",
    "¨'": "ुङ",
    "qm": "क्र",
    "Efm": "झ",
    "S'm": "कु",
    "Sm": "क",
    "Em": "झ्",
    "em": "झ",
}
CHARS = {
    "a": "ब", "b": "द", "c": "अ", "d": "म", "e": "भ", "f": "ा", "g": "न", "h": "ज", "i": "ष्", "j": "व",
    "k": "प", "l": "ि", "n": "ल", "o": "य", "p": "उ", "q": "त्र", "r": "च", "s": "क", "t": "त",
    "u": "ग", "v": "ख", "w": "ध", "x": "ह", "y": "थ", "z": "श",
    "A": "ब्", "B": "द्य", "C": "ऋ", "D": "म्", "E": "भ्", "F": "ँ", "G": "न्", "H": "ज्", "I": "क्ष्", "J": "व्",
    "K": "प्", "L": "ी", "M": "ः", "N": "ल्", "O": "इ", "P": "ए", "Q": "त्त", "R": "च्", "S": "क्", "T": "त्",
    "U": "ग्", "V": "ख्", "W": "ध्", "X": "ह्", "Y": "थ्", "Z": "श्",
    "0": "ण्", "1": "ज्ञ", "2": "द्द", "3": "घ", "4": "द्ध", "5": "छ", "6": "ट", "7": "ठ", "8": "ड", "9": "ढ",
    "!": "१", "@": "२", "#": "३", "$": "४", "%": "५", "^": "६", "&": "७", "*": "८", "(": "९", ")": "०",
    "-": "(", "_": ")", "=": ".", "+": "ं", "'": "ु", '"': "ू", "[": "ृ", "]": "े", "}": "ै", "\\": "्",
    "|": "्र", "«": "्र", "/": "र", "?": "रु", "¿": "रू", ";": "स", ":": "स्", ">": "श्र", "<": "?", "{": "\x00",
    "ª": "ङ", "Ë": "ङ्ग", "Í": "ङ्क", "Ø": "्य", "§": "ट्ट", "›": "द्र", "¶": "ठ्ठ", "Ý": "ट्ठ", "å": "द्व",
    "~": "ञ्", "¨": "ङ", "ç": "ॐ", "‹": "ङ्घ", "Œ": "त्त्", "÷": "/", "–": "–", ",": ",", ".": "।", " ": " ", "Ö": "=", "×": "×", "Ù": ";",
    "…": "‘", "Ú": "’", "Æ": "”", "Ü": "%", "´": "झ", "£": "घ्", "¤": "झ्", "‰": "झ्", "ˆ": "फ्", "Î": "ङ्ख",
}  # fmt: skip
CONSONANT = "[क-ह]"
CLUSTER = f"(?:{CONSONANT}़?्)*{CONSONANT}़?"
SIGNS = "[ा-ौंःँ]"


def unicode(text: str) -> str:
    """Unicode Devanagari for a run of Preeti text; raises on a character the table does not know."""
    text = re.sub(r"\{+", "{", text)  # a reph typed twice is one reph
    out: list[str] = []
    i = 0
    while i < len(text):
        for seq, rep in SEQUENCES.items():
            if text.startswith(seq, i):
                out.append(rep)
                i += len(seq)
                break
        else:
            char = text[i]
            if char not in CHARS:
                raise ValueError(f"unknown Preeti character {char!r} in {text!r}")
            out.append(CHARS[char])
            i += 1
    s = "".join(out)
    # A half letter followed by the vowel sign aa is the full letter.
    s = s.replace("््", "्").replace("्ा", "")
    s = s.replace("अा", "आ").replace("आे", "ओ").replace("आै", "औ")
    s = s.replace("ाे", "ो").replace("ाै", "ौ")
    # A vowel sign typed before the half r that joins its consonant follows it in Unicode.
    s = re.sub("([ुूृेैं]+)्र", r"्र\1", s)
    # The short i sign is typed before the consonant cluster it follows in Unicode.
    s = re.sub(f"ि({CLUSTER})", r"\1ि", s)
    # The reph is typed after the syllable it begins in Unicode.
    s = re.sub(f"({CLUSTER}{SIGNS}*)\x00", r"र्\1", s)
    if "\x00" in s:
        raise ValueError(f"reph without a syllable in {text!r}")
    return s


DIGITS = str.maketrans("!@#$%^&*()", "1234567890")


def number(text: str) -> int:
    """An integer printed in Preeti digits, with or without thousands commas or spaces."""
    clean = text.translate(DIGITS).replace(",", "").replace(" ", "").rstrip("=")
    if not clean.isdigit():
        raise ValueError(f"not a Preeti number: {text!r}")
    return int(clean)
