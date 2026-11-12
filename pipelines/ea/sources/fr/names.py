import re

from ea.names import display

ABBREVIATED = {"J": "Jean", "M": "Marie", "P": "Pierre", "A": "Anne"}
PARTICLES = {"DE", "D'", "DU", "DES"}


def person_name(forename: str | None, surname: str) -> str:
    return display(" ".join(part for part in (forename, surname) if part), "fr")


def head_parts(head: str) -> tuple[str | None, str]:
    words = re.sub(r"\b([A-Z])\.\s+(?=\w)", r"\1.", " ".join(head.replace(",", " ").split())).split()
    split = next((i for i, word in enumerate(words) if any(c.islower() for c in word)), None)
    if split is None:
        split = next((i for i, word in enumerate(words) if i and re.match(r"[A-Z][.-]\w", word)), None)
    if split is None:
        split = len(words) - 1 if len(words) > 1 else len(words)
    surname, forename = words[:split], " ".join(words[split:])
    while len(surname) > 1 and surname[-1].upper() in PARTICLES:
        surname = [surname[-1], *surname[:-1]]
    forename = re.sub(r"^([JMPA])[.-](\w)", lambda m: f"{ABBREVIATED[m[1]]}-{m[2]}", forename)
    return forename or None, " ".join(surname)
