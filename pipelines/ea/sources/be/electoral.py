"""Which municipalities make up each electoral district of the Belgian federal and regional parliaments.

The federal election service's files name each district but give no outline. The districts of the national bodies are
unions of administrative arrondissements (the Chamber and Senate arrondissements of 1894 to 1991, the constituencies
of 1995 to 2010, and the Walloon and Flemish constituencies), or whole provinces, so a district's name is parsed into
the arrondissements it lists. An arrondissement is the set of municipalities of Statbel's 2018 municipal plan whose NIS
codes start with its two-digit code; Mouscron, Comines-Warneton, and Fourons are placed where they lay before the
language-border law of 1962 took effect. From the Walloon constituency of 2019, Statbel's own arrondissement labels
are used, because the administrative arrondissement of La Louvière was cut from its neighbors by then.
"""

import functools
import re
import unicodedata
from datetime import date

# the date the language-border law of 8 November 1962 moved Mouscron, Comines-Warneton, and Fourons between provinces
BORDER = date(1963, 9, 1)
# the first election whose Walloon constituencies follow the arrondissements as Statbel labels them
LOUVIERE = date(2019, 1, 1)

# the two-digit NIS arrondissement codes, by the names the files print in Dutch, French, and English
ARRONDISSEMENTS: dict[str, tuple[str, ...]] = {
    "11": ("antwerpen", "anvers", "antwerp"),
    "12": ("mechelen", "malines"),
    "13": ("turnhout",),
    "21": ("brussel-hoofdstad", "bruxelles-capitale", "brussels-capital"),
    "23": ("halle-vilvoorde", "hal-vilvorde"),
    "24": ("leuven", "louvain"),
    "25": ("nijvel", "nivelles"),
    "31": ("brugge", "bruges"),
    "32": ("diksmuide", "dixmude"),
    "33": ("ieper", "ypres"),
    "34": ("kortrijk", "courtrai"),
    "35": ("oostende", "ostende"),
    "36": ("roeselare", "roulers"),
    "37": ("tielt",),
    "38": ("veurne", "furnes"),
    "41": ("aalst", "alost"),
    "42": ("dendermonde", "termonde"),
    "43": ("eeklo",),
    "44": ("gent", "gand"),
    "45": ("oudenaarde", "audenarde"),
    "46": ("sint-niklaas", "saint-nicolas"),
    "51": ("aat", "ath"),
    "52": ("charleroi",),
    "53": ("bergen", "mons"),
    "54": ("moeskroen", "mouscron"),
    "55": ("zinnik", "soignies"),
    "56": ("thuin",),
    "57": ("doornik", "tournai"),
    "61": ("hoei", "huy"),
    "62": ("luik", "liege"),
    "63": ("verviers",),
    "64": ("borgworm", "waremme"),
    "71": ("hasselt",),
    "72": ("maaseik",),
    "73": ("tongeren",),
    "81": ("aarlen", "arlon"),
    "82": ("bastenaken", "bastogne"),
    "83": ("marche-en-famenne", "marche"),
    "84": ("neufchateau",),
    "85": ("virton",),
    "91": ("dinant",),
    "92": ("namen", "namur"),
    "93": ("philippeville",),
}
# La Louvière is an arrondissement of its own only in Statbel's labels, so it takes the code 59
LA_LOUVIERE = "59"
# the arrondissement codes of each province, by the names the files print
PROVINCES: dict[str, tuple[str, ...]] = {
    "antwerpen": ("11", "12", "13"),
    "antwerp": ("11", "12", "13"),
    "vlaams-brabant": ("23", "24"),
    "flemish brabant": ("23", "24"),
    "waals brabant": ("25",),
    "walloon brabant": ("25",),
    "west-vlaanderen": ("31", "32", "33", "34", "35", "36", "37", "38"),
    "west-flanders": ("31", "32", "33", "34", "35", "36", "37", "38"),
    "oost-vlaanderen": ("41", "42", "43", "44", "45", "46"),
    "east-flanders": ("41", "42", "43", "44", "45", "46"),
    "henegouwen": ("51", "52", "53", "54", "55", "56", "57"),
    "hainaut": ("51", "52", "53", "54", "55", "56", "57"),
    "luik": ("61", "62", "63", "64"),
    "liege": ("61", "62", "63", "64"),
    "limburg": ("71", "72", "73"),
    "luxemburg": ("81", "82", "83", "84", "85"),
    "luxembourg": ("81", "82", "83", "84", "85"),
    "namen": ("91", "92", "93"),
    "namur": ("91", "92", "93"),
}
BRUSSELS = ("21", "23")
# From 1900 the files print some districts under the name of their first arrondissement alone in some elections
# and in full in others, and print the Flemish district of 1999 without Ostend
ALIASES = {
    "aarlen": "aarlen-marche-bastenaken",
    "dinant": "dinant-philippeville",
    "neufchateau": "neufchateau-virton",
    "doornik-aat": "doornik-aat-moeskroen",
}
FLEMISH_ALIASES = {"veurne-diksmuide-ieper": "veurne-diksmuide-ieper-oostende"}
# the Senate's Luxembourg, Limburg, and Namur districts of 1949 to 1991 each cover their whole province
SENATE = {"aarlen": PROVINCES["luxemburg"], "hasselt": PROVINCES["limburg"], "namen": PROVINCES["namen"]}
# the Chamber's constituencies are provinces from 2003 and the Flemish Parliament's from 2004
PROVINCIAL = {"chamber": date(2003, 1, 1), "flemish": date(2004, 1, 1)}
GERMAN_SPEAKING = ("63003", "63012", "63013", "63023", "63048", "63049", "63067", "63073", "63087")


def plain(text: str) -> str:
    stripped = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    spaced = re.sub(r"\s*-\s*", "-", stripped.casefold().replace("’", "'"))
    return " ".join(re.sub(r"[^a-z0-9 -]+", " ", spaced).split())


def _vocabulary() -> dict[str, tuple[str, ...]]:
    found: dict[str, tuple[str, ...]] = {}
    for code, names in ARRONDISSEMENTS.items():
        for name in names:
            found[name] = (code,)
    found["la louviere"] = (LA_LOUVIERE,)
    for name in ("brussel", "bruxelles", "brussels"):
        found[name] = BRUSSELS
    found["huy-waremme"] = ("61", "64")
    found["bmfp"] = ("21",)
    return found


VOCABULARY = _vocabulary()


def tokens(text: str, provinces: bool) -> list[str] | None:
    """The arrondissement, province, or Brussels words a district name lists, the longest word first at each position,
    or None when something in the name is not one of them."""
    body = plain(re.sub(r"^.*? – ", "", text))
    body = re.sub(r"^(constituency of|electoral district of)\s+", "", body)
    words = sorted({*VOCABULARY, *(PROVINCES if provinces else ())}, key=len, reverse=True)
    found: list[str] = []
    while body := body.lstrip("- "):
        for word in words:
            if body.startswith(word) and (len(body) == len(word) or body[len(word)] in "- "):
                found.append(word)
                body = body[len(word) :]
                break
        else:
            return None
    return found


def codes(text: str, day: date, body: str) -> set[str] | None:
    """The arrondissement codes of a district's name, or None when the name is not geographic. `body` is `chamber`,
    `senate`, `flemish`, or `walloon`."""
    provinces = body in PROVINCIAL and day >= PROVINCIAL[body]
    if (body == "chamber" and 1900 <= day.year <= 1991) or body == "flemish":
        name = plain(re.sub(r"^.*? – ", "", text))
        text = (ALIASES if body == "chamber" else FLEMISH_ALIASES).get(name, text)
    parts = tokens(text, provinces)
    if not parts:
        return None
    out: set[str] = set()
    for word in parts:
        if body == "senate" and word in SENATE:
            out.update(SENATE[word])
        elif provinces and word in PROVINCES:
            out.update(PROVINCES[word])
        else:
            out.update(VOCABULARY[word])
    return out


MOVED = {"54007": "34", "54010": "33", "73109": "63"}  # Mouscron, Comines-Warneton, and Fourons before BORDER


def atoms(day: date, body: str) -> dict[str, str]:
    """The arrondissement code of each municipality of the 2018 plan, as the districts of an election on `day` hold it."""
    return _atoms(day < BORDER, day >= LOUVIERE and body == "walloon")


@functools.cache
def _atoms(before_border: bool, labeled: bool) -> dict[str, str]:
    from ea.sources.be import nis

    reader, _ = nis._reader("2018", False)
    found: dict[str, str] = {}
    for record in reader.iterRecords():
        if record is None:
            continue
        row = dict(record.as_dict())
        code = nis._column(row, "CNIS5_")
        atom = code[:2]
        if labeled:
            label = plain(re.sub(r"^Arrondissement (de |d'|d’)?", "", str(row["T_ARRD_FR"]), flags=re.IGNORECASE))
            atom = VOCABULARY[label][0] if label in VOCABULARY else atom
        elif before_border and code in MOVED:
            atom = MOVED[code]
        found[code] = atom
    return found
