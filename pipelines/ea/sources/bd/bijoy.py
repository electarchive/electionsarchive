"""Text in the SutonnyMJ font of the Bijoy keyboard, as a PDF's text layer gives it, turned into Unicode Bengali.

Bijoy fonts put Bengali glyphs on the code points of Windows-1252, store a pre-base vowel sign before its consonant
and a reph after its consonant, and draw common conjuncts as single glyphs. The table below is the standard Bijoy
layout; `unicode()` maps each glyph and then moves the vowel signs and rephs to their Unicode order.
"""

import unicodedata

HALANT = "্"
REPH = ""  # stands for the reph until it is moved before its consonant cluster
PRE = {"ি", "ে", "ৈ"}
POST = {"া", "ী", "ু", "ূ", "ৃ", "ৗ", "ো", "ৌ"}
CONSONANTS = set("কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহৎ") | {"\u09dc", "\u09dd", "\u09df"}

GLYPHS = {
    "Av": "আ",
    "A": "অ",
    "B": "ই",
    "C": "ঈ",
    "D": "উ",
    "E": "ঊ",
    "F": "ঋ",
    "G": "এ",
    "H": "ঐ",
    "I": "ও",
    "J": "ঔ",
    "K": "ক",
    "L": "খ",
    "M": "গ",
    "N": "ঘ",
    "O": "ঙ",
    "P": "চ",
    "Q": "ছ",
    "R": "জ",
    "S": "ঝ",
    "T": "ঞ",
    "U": "ট",
    "V": "ঠ",
    "W": "ড",
    "X": "ঢ",
    "Y": "ণ",
    "Z": "ত",
    "_": "থ",
    "`": "দ",
    "a": "ধ",
    "b": "ন",
    "c": "প",
    "d": "ফ",
    "e": "ব",
    "f": "ভ",
    "g": "ম",
    "h": "য",
    "i": "র",
    "j": "ল",
    "k": "শ",
    "l": "ষ",
    "m": "স",
    "n": "হ",
    "o": "\u09dc",
    "p": "\u09dd",
    "q": "\u09df",
    "r": "ৎ",
    "s": "ং",
    "t": "ঃ",
    "u": "ঁ",
    "0": "০",
    "1": "১",
    "2": "২",
    "3": "৩",
    "4": "৪",
    "5": "৫",
    "6": "৬",
    "7": "৭",
    "8": "৮",
    "9": "৯",
    "•": "ঙ্",
    "v": "া",
    "w": "ি",
    "x": "ী",
    "y": "ু",
    "z": "ু",
    "“": "ু",
    "–": "ু",
    "~": "ূ",
    "ƒ": "ূ",
    "‚": "ূ",
    "„": "ৃ",
    "…": "ৃ",
    "†": "ে",
    "‡": "ে",
    "ˆ": "ৈ",
    "‰": "ৈ",
    "Š": "ৗ",
    "|": "।",
    "&": HALANT,
    "^": "্ব",
    "‘": "্তু",
    "’": "্থ",
    "‹": "্ক",
    "Œ": "্ক্র",
    "”": "চ্",
    "—": "্ত",
    "˜": "দ্",
    "™": "দ্",
    "š": "ন্",
    "›": "ন্",
    "œ": "্ন",
    "Ÿ": "্ব",
    "¡": "্ব",
    "¢": "্ভ",
    "£": "্ভ্র",
    "¤": "ম্",
    "¥": "্ম",
    "¦": "্ব",
    "§": "্ম",
    "¨": "্য",
    "©": REPH,
    "ª": "্র",
    "«": "্র",
    "¬": "্ল",
    "­": "্ল",
    "®": "ষ্",
    "¯": "স্",
    "°": "ক্ক",
    "±": "ক্ট",
    "²": "ক্ষ্ম",
    "³": "ক্ত",
    "´": "ক্ম",
    "µ": "ক্র",
    "¶": "ক্ষ",
    "·": "ক্স",
    "¸": "গু",
    "¹": "জ্ঞ",
    "º": "গ্দ",
    "»": "গ্ধ",
    "¼": "ঙ্ক",
    "½": "ঙ্গ",
    "¾": "জ্জ",
    "¿": "্ত্র",
    "À": "জ্ঝ",
    "Á": "জ্ঞ",
    "Â": "ঞ্চ",
    "Ã": "ঞ্ছ",
    "Ä": "ঞ্জ",
    "Å": "ঞ্ঝ",
    "Æ": "ট্ট",
    "Ç": "ড্ড",
    "È": "ণ্ট",
    "É": "ণ্ঠ",
    "Ê": "ণ্ড",
    "Ë": "ত্ত",
    "Ì": "ত্থ",
    "Í": "ত্ম",
    "Î": "ত্র",
    "Ï": "দ্দ",
    "Ð": "-",
    "Ñ": "-",
    "Ò": '"',
    "Ó": '"',
    "Ô": "'",
    "Õ": "'",
    "Ö": "্র",
    "×": "দ্ধ",
    "Ø": "দ্ব",
    "Ù": "দ্ম",
    "Ú": "ন্ঠ",
    "Û": "ন্ড",
    "Ü": "ন্ধ",
    "Ý": "ন্স",
    "Þ": "প্ট",
    "ß": "প্ত",
    "à": "প্প",
    "á": "প্স",
    "â": "ব্জ",
    "ã": "ব্দ",
    "ä": "ব্ধ",
    "å": "ভ্র",
    "æ": "ু",  # the u-kar drawn under র in SutonnyMJ
    "ç": "ম্ফ",
    "è": "্ন",
    "é": "ল্ক",
    "ê": "ল্গ",
    "ë": "ল্ট",
    "ì": "ল্ড",
    "í": "ল্প",
    "î": "ল্ফ",
    "ï": "শু",
    "ð": "শ্চ",
    "ñ": "শ্ছ",
    "ò": "ষ্ণ",
    "ó": "ষ্ট",
    "ô": "ষ্ঠ",
    "õ": "ষ্ফ",
    "ö": "স্খ",
    "÷": "স্ট",
    "ø": "্ল",
    "ù": "স্ফ",
    "ú": "্প",
    "û": "হু",
    "ü": "হৃ",
    "ý": "হ্ন",
    "þ": "হ্ম",
    "\x8f": "ক্র",
    "ÿ": "ক্ষ",
}
LONGEST = max(len(k) for k in GLYPHS)


def _glyphs(text: str) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(text):
        for size in range(LONGEST, 0, -1):
            piece = text[i : i + size]
            if piece in GLYPHS:
                out.extend(GLYPHS[piece])
                i += size
                break
        else:
            out.append(text[i])
            i += 1
    return out


def _cluster_start(chars: list[str], end: int) -> int:
    """The index where the consonant cluster ending at `end` (inclusive) starts."""
    start = end
    while start >= 2 and chars[start - 1] == HALANT and chars[start - 2] in CONSONANTS:
        start -= 2
    return start


def _cluster_end(chars: list[str], start: int) -> int:
    """The index just past the consonant cluster starting at `start`, rephs included."""
    i = start
    while i < len(chars) and chars[i] in CONSONANTS:
        if i + 2 < len(chars) and chars[i + 1] == HALANT and chars[i + 2] in CONSONANTS:
            i += 2
        else:
            i += 1
            break
    return i


def unicode(text: str) -> str:
    chars: list[str] = []
    for c in _glyphs(text):
        if not (c == HALANT and chars and chars[-1] == HALANT):
            chars.append(c)
    # A vowel sign or candrabindu typed before a conjunct's second half belongs after it.
    i = 1
    while i < len(chars) - 1:
        if chars[i] == HALANT and (chars[i - 1] in POST or chars[i - 1] == "ঁ") and chars[i + 1] in CONSONANTS:
            chars[i - 1], chars[i], chars[i + 1] = chars[i], chars[i + 1], chars[i - 1]
        i += 1
    # The reph follows its consonant cluster, and sometimes that cluster's vowel sign.
    i = 0
    while i < len(chars):
        if chars[i] == REPH:
            end = i - 1
            if end >= 0 and chars[end] in POST:
                end -= 1
            if end >= 0 and chars[end] in CONSONANTS:
                start = _cluster_start(chars, end)
                chars[start:start] = ["র", HALANT]
                del chars[i + 2]
                i += 2
                continue
            chars[i : i + 1] = ["র", HALANT]
        i += 1
    # A pre-base vowel sign goes after its consonant cluster; e plus aa or au length mark make o and au.
    out: list[str] = []
    i = 0
    while i < len(chars):
        c = chars[i]
        if c in PRE and i + 1 < len(chars) and chars[i + 1] in CONSONANTS:
            end = _cluster_end(chars, i + 1)
            # a reph already moved in front of the cluster: র্ + consonant
            out.extend(chars[i + 1 : end])
            following = chars[end] if end < len(chars) else ""
            nukta = following == "ঁ"
            if nukta:
                end += 1
                following = chars[end] if end < len(chars) else ""
            if c == "ে" and following == "া":
                out.append("ো")
                end += 1
            elif c == "ে" and following == "ৗ":
                out.append("ৌ")
                end += 1
            else:
                out.append(c)
            if nukta:
                out.append("ঁ")
            i = end
            continue
        out.append(c)
        i += 1
    result = "".join(out).replace("অা", "আ")
    # the candrabindu goes after a vowel sign that follows it
    for kar in POST:
        result = result.replace("ঁ" + kar, kar + "ঁ")
    return unicodedata.normalize("NFC", " ".join(result.split()))
