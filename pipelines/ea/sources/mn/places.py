"""Mongolia's provinces (aimag), the capital, and the soums and districts the result files name."""

import re
from dataclasses import dataclass

# ISO 3166-2 suffix, Mongolian name, English name
AIMAGS: dict[str, tuple[str, str]] = {
    "073": ("Архангай", "Arkhangai"),
    "071": ("Баян-Өлгий", "Bayan-Ölgii"),
    "069": ("Баянхонгор", "Bayankhongor"),
    "067": ("Булган", "Bulgan"),
    "037": ("Дархан-Уул", "Darkhan-Uul"),
    "061": ("Дорнод", "Dornod"),
    "063": ("Дорноговь", "Dornogovi"),
    "059": ("Дундговь", "Dundgovi"),
    "065": ("Говь-Алтай", "Govi-Altai"),
    "064": ("Говьсүмбэр", "Govisümber"),
    "039": ("Хэнтий", "Khentii"),
    "043": ("Ховд", "Khovd"),
    "041": ("Хөвсгөл", "Khövsgöl"),
    "053": ("Өмнөговь", "Ömnögovi"),
    "035": ("Орхон", "Orkhon"),
    "055": ("Өвөрхангай", "Övörkhangai"),
    "049": ("Сэлэнгэ", "Selenge"),
    "051": ("Сүхбаатар", "Sükhbaatar"),
    "047": ("Төв", "Töv"),
    "046": ("Увс", "Uvs"),
    "057": ("Завхан", "Zavkhan"),
    "1": ("Улаанбаатар", "Ulaanbaatar"),
}
CAPITAL = "1"
DISTRICTS = (
    "Багануур",
    "Багахангай",
    "Баянгол",
    "Баянзүрх",
    "Налайх",
    "Сонгинохайрхан",
    "Сүхбаатар",
    "Хан-Уул",
    "Чингэлтэй",
)


def norm(text: str) -> str:
    text = re.sub(r"\b(аймаг|аймгийн|нийслэл|нийслэлийн|хот|сум|сумын|дүүрэг|дүүргийн)\b", "", text.lower())
    return re.sub(r"[\s\-–.,()/]+", "", text)


_AIMAG_KEYS = {norm(name): code for code, (name, _) in AIMAGS.items()}
_AIMAG_KEYS |= {"улаанбаатар": CAPITAL, "нийслэл": CAPITAL, "улаанбаатархот": CAPITAL}
_DISTRICT_KEYS = {norm(name) for name in DISTRICTS}


def aimag_code(text: str) -> str | None:
    if re.sub(r"[\s.]+", "", text.lower()) in ("нийслэл", "нийслэлийн", "улаанбаатархот"):
        return CAPITAL
    return _AIMAG_KEYS.get(norm(text))


def is_district(text: str) -> bool:
    return norm(text) in _DISTRICT_KEYS


@dataclass(frozen=True)
class Place:
    aimag: str
    soum: str

    @property
    def code(self) -> str:
        return f"{self.aimag}:{norm(self.soum)}"


def place_of(aimag: str | None, soum: str) -> Place | None:
    """A place from the aimag and soum names as a file prints them; the capital's districts need no aimag."""
    soum = re.sub(r"\s+(сум|дүүрэг)$", "", soum.strip(), flags=re.IGNORECASE).strip()
    if aimag:
        code = aimag_code(aimag)
        if code is None:
            return None
    elif is_district(soum):
        code = CAPITAL
    else:
        return None
    return Place(code, soum)
