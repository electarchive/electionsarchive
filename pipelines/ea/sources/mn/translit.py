"""Latin transliteration of Mongolian Cyrillic, for URL slugs."""

TABLE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i",
    "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "ө": "u", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ү": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}  # fmt: skip


def latin(text: str) -> str:
    return "".join(TABLE.get(ch, ch) for ch in text.lower())
