import collections
import fcntl
import hashlib
import json
import re
import subprocess
import time
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import cast

import polars as pl
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

from ea import batch, raw
from ea.apply import apply, publish, revert
from ea.changeset import Changeset
from ea.store import DATA, Connection, connect

PIPELINE = "names.latin"
VERSION = "1"
PUBLISHER = "Elections Archive"
TITLE = "Latin-script and sentence-case names from the archive's naming pass"
SOURCE_URL = "https://electionsarchive.com/"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
WORK = DATA / "names"
STATE = WORK / "state.json"
QUEUE = WORK / "queue.json"
ANSWERS = WORK / "answers.json"
DRAFTS = WORK / "drafts.json"
REVIEWS = WORK / "review.tsv"
LOCK = WORK / ".lock"
BOX = "storagebox:names"
TICKET = ("5370", "electarchive/ops")
LIMIT = 300_000
MODEL = "claude-sonnet-5-5"
EFFORT = "low"
JOB_CAP = 40.0
CHUNK_CHARS = 6_000
GENERATED = ("transliteration", "english", "official")

NONLATIN = (
    r"[Ͱ-ϿЀ-ԯ԰-֏֐-׿؀-ۿ܀-ࣿऀ-෿"
    r"฀-࿿က-႟Ⴀ-ჿᄀ-ᇿሀ-᎟ក-៿᠀-᢯"
    r"ⴰ-⵿぀-ヿ㄀-ㇿ㐀-䶿一-鿿ꀀ-꓏가-힯"
    r"豈-﫿יִ-﷿ﹰ-﻿ｦ-ﾟ]"
)
LATIN = r"[A-Za-zÀ-ɏḀ-ỿ]"
HOLDERS = f"""
select n.entity_id from name n where {{scope}}
group by 1 having not bool_or(n.text ~ '{LATIN}' and n.text !~ '{NONLATIN}')
"""
ROWS = """
select n.entity_id::text, e.type::text, n.text, n.lang, n.kind::text, n.is_preferred, c.pipeline
from name n
join entity e on e.id = n.entity_id and e.retired_by is null
join changeset c on c.id = n.changeset_id
where n.entity_id = any(%s::uuid[])
  and not exists (select from entity_merge m where m.entity_id = n.entity_id)
order by n.entity_id
"""

PEOPLE = {"person"}
PLACES = {"area", "reporting_unit", "polity"}
BODIES = {"party", "alliance", "body", "office", "contest", "contest_option", "election", "government"}
KINDS = {"official": 3, "native": 2, "ballot": 1, "historical": 0}
SCRIPTS = {
    "CYRILLIC": "Cyrl", "GREEK": "Grek", "HANGUL": "Hang", "CJK": "Hani", "HIRAGANA": "Jpan", "KATAKANA": "Jpan",
    "ARMENIAN": "Armn", "ETHIOPIC": "Ethi", "GEORGIAN": "Geor", "ARABIC": "Arab", "HEBREW": "Hebr",
    "DEVANAGARI": "Deva", "BENGALI": "Beng", "MYANMAR": "Mymr", "THAI": "Thai", "LATIN": "Latn",
}  # fmt: skip
LOSSY = {"Arab", "Hebr", "Jpan", "Deva", "Beng", "Mymr", "Thai"}
CASED = {"Cyrl", "Grek", "Armn", "Latn"}
SCRIPT_LANGS = {"Cyrl": "ru", "Grek": "el", "Hang": "ko", "Armn": "hy", "Ethi": "am", "Geor": "ka", "Jpan": "ja"}
ICU = {
    "sr": "Serbian-Latin/BGN",
    "ru": "Russian-Latin/BGN",
    "ky": "Kirghiz-Latin/BGN",
    "kk": "Kazakh-Latin/BGN",
    "be": "Belarusian-Latin/BGN",
    "mk": "Macedonian-Latin/BGN",
    "tg": "Russian-Latin/BGN",
    "ka": "Georgian-Latin/BGN",
    "am": "Amharic-Latin/BGN",
    "ti": "Amharic-Latin/BGN",
    "om": "Amharic-Latin/BGN",
}
BULGARIAN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sht", "ъ": "a", "ь": "y", "ю": "yu", "я": "ya",
}  # fmt: skip
MONGOLIAN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "ye", "ё": "yo", "ж": "j", "з": "z", "и": "i",
    "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "ө": "ö", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ү": "ü", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "i",
    "ы": "y", "ь": "i", "э": "e", "ю": "yu", "я": "ya",
}  # fmt: skip
UKRAINIAN = {
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh", "з": "z",
    "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ю": "iu", "я": "ia", "ь": "", "'": "", "’": "", "ʼ": "", "зг": "zgh",
}  # fmt: skip
GREEK = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th", "ι": "i", "κ": "k", "λ": "l",
    "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f",
    "χ": "ch", "ψ": "ps", "ω": "o", "ϊ": "i", "ϋ": "y", "ΐ": "i", "ΰ": "y", "αι": "ai", "ει": "ei", "οι": "oi",
    "ου": "ou", "γγ": "ng", "γκ": "gk", "γξ": "nx", "γχ": "nch", "μπ": "mp", "ντ": "nt",
}  # fmt: skip
VOICED = set("αεηιοωυϊϋβγδζλμνρ")
ARMENIAN = {
    **dict(
        zip(
            "աբգդեզէըթժիլխծկհձղճմյնշոչպջռսվտրցփքօֆ",
            [
                "a",
                "b",
                "g",
                "d",
                "e",
                "z",
                "e",
                "y",
                "t",
                "zh",
                "i",
                "l",
                "kh",
                "ts",
                "k",
                "h",
                "dz",
                "gh",
                "ch",
                "m",
                "y",
                "n",
                "sh",
                "o",
                "ch",
                "p",
                "j",
                "r",
                "s",
                "v",
                "t",
                "r",
                "ts",
                "p",
                "k",
                "o",
                "f",
            ],
        )
    ),
    "ու": "u",
    "և": "ev",
}
ARMENIAN_INITIAL = {"ե": "ye", "ո": "vo", "և": "yev"}
UKRAINIAN_INITIAL = {"є": "ye", "ї": "yi", "й": "y", "ю": "yu", "я": "ya"}
INITIALS = ("g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h")
MEDIALS = (
    "a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu",
    "eu", "ui", "i",
)  # fmt: skip
FINALS = (
    "", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "p", "t", "t",
    "ng", "t", "t", "k", "t", "p", "t",
)  # fmt: skip
CARRIED = {
    1: "g", 2: "kk", 3: "ks", 4: "n", 5: "nj", 6: "n", 7: "d", 8: "r", 9: "lg", 10: "lm", 11: "lb", 12: "ls",
    13: "lt", 14: "lp", 15: "r", 16: "m", 17: "b", 18: "bs", 19: "s", 20: "ss", 21: "ng", 22: "j", 23: "ch",
    24: "k", 25: "t", 26: "p", 27: "",
}  # fmt: skip
VELAR = {1, 2, 3, 9, 24}
DENTAL = {7, 19, 20, 22, 23, 25, 27}
LABIAL = {14, 17, 18, 26}
UNITS = ("특별자치시", "특별자치도", "특별시", "광역시", "시", "도", "군", "구", "읍", "면", "동", "리", "가")
SEATS = {"갑": "A", "을": "B", "병": "C", "정": "D", "무": "E"}
TAIWAN_UNITS = {
    "里": "Li",
    "村": "Village",
    "鄉": "Township",
    "鎮": "Town",
    "市": "City",
    "區": "District",
    "縣": "County",
}
DISTRICT = re.compile(r"^[가-힣\d\s]+(?:선거구|제\d+|[시군구읍면동리가][갑을병정무]?)$")
ROMAN = {"II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII"}


@dataclass(frozen=True)
class Name:
    entity: str
    text: str
    lang: str
    script: str
    kind: str


@dataclass(frozen=True)
class Want:
    task: str
    group: str
    lang: str
    country: str
    text: str

    @property
    def key(self) -> str:
        return hashlib.md5(
            json.dumps([self.task, self.group, self.lang, self.country, self.text]).encode()
        ).hexdigest()[:16]


Entity = tuple[str, str, list[tuple[str, str, str, bool, str]]]


def script_of(text: str) -> collections.Counter[str]:
    found: collections.Counter[str] = collections.Counter()
    for char in text:
        if char.isalpha():
            found[SCRIPTS.get(unicodedata.name(char, "OTHER").split(" ")[0], "Zyyy")] += 1
    if found["Jpan"]:
        found["Jpan"] += found.pop("Hani", 0)
    return found


def foreign(text: str) -> bool:
    return any(script != "Latn" for script in script_of(text))


def latin(text: str) -> bool:
    found = script_of(text)
    return bool(found) and set(found) == {"Latn"}


def strip(text: str) -> str:
    return unicodedata.normalize(
        "NFC", "".join(char for char in unicodedata.normalize("NFD", text) if unicodedata.category(char) != "Mn")
    )


@cache
def transform(id: str) -> Callable[[str], str]:
    import icu

    return icu.Transliterator.createInstance(id).transliterate


def table(text: str, letters: dict[str, str], initial: dict[str, str] | None = None) -> str:
    out: list[str] = []
    for word in re.split(r"(\W+)", text):
        shouting = len(word) > 1 and word.isupper()
        index = 0
        while index < len(word):
            pair = word[index : index + 2]
            size = 2 if len(pair) == 2 and pair.lower() in letters else 1
            piece = word[index : index + size]
            lower = piece.lower()
            mapped = (initial or {}).get(lower) if index == 0 else None
            mapped = letters.get(lower, piece) if mapped is None else mapped
            if piece != lower and mapped:
                mapped = mapped.upper() if shouting else mapped[0].upper() + mapped[1:]
            out.append(mapped)
            index += size
    return "".join(out)


def bulgarian(text: str) -> str:
    return table(re.sub(r"(?<=[иИ])([яЯ])\b", lambda match: "a" if match[1] == "я" else "A", text), BULGARIAN)


def ukrainian(text: str) -> str:
    return table(text, UKRAINIAN, UKRAINIAN_INITIAL)


def greek(text: str) -> str:
    plain = unicodedata.normalize("NFC", unicodedata.normalize("NFD", text).replace("\u0301", ""))
    out: list[str] = []
    for word in re.split(r"(\W+)", plain):
        shouting = len(word) > 1 and word.isupper()
        lower = word.lower()
        index = 0
        while index < len(word):
            pair = lower[index : index + 2]
            if pair[:1] in "αεη" and pair[1:] == "υ":
                after = lower[index + 2 : index + 3]
                mapped = GREEK[pair[0]] + ("v" if after and after in VOICED else "f")
                size = 2
            elif len(pair) == 2 and pair in GREEK:
                mapped, size = GREEK[pair], 2
            else:
                mapped, size = GREEK.get(lower[index], word[index]), 1
            piece = word[index : index + size]
            if piece != piece.lower() and mapped:
                mapped = mapped.upper() if shouting else mapped[0].upper() + mapped[1:]
            out.append(mapped)
            index += size
    return "".join(out)


def capitalized(text: str) -> str:
    return re.sub(r"(?<![\w’'])(\w)", lambda match: match[1].upper(), text)


def hangul(text: str) -> str:
    syllables: list[tuple[int, int, int] | str] = []
    for char in text:
        code = ord(char) - 0xAC00
        syllables.append((code // 588, code % 588 // 28, code % 28) if 0 <= code < 11172 else char)
    out: list[str] = []
    carry = ""
    for index, syllable in enumerate(syllables):
        if isinstance(syllable, str):
            out.append(carry + syllable)
            carry = ""
            continue
        initial, medial, final = syllable
        onset = carry or INITIALS[initial]
        carry = ""
        after = syllables[index + 1] if index + 1 < len(syllables) else None
        coda = FINALS[final]
        if isinstance(after, tuple) and final:
            following = after[0]
            if following == 11:
                coda, carry = "", CARRIED[final]
                if final in (7, 25) and after[1] == 20:
                    carry = "j" if final == 7 else "ch"
            elif following in (2, 6):
                coda = "ng" if final in VELAR else "n" if final in DENTAL else "m" if final in LABIAL else coda
                if final in (8, 12, 13, 15) and following == 2:
                    coda, carry = "l", "l"
            elif following == 5:
                if final in (4, 8):
                    coda, carry = "l", "l"
                else:
                    coda = "ng" if final in VELAR else "n" if final in DENTAL else "m" if final in LABIAL else coda
                    carry = "n"
            elif final == 27 and following in (0, 3, 12):
                coda, carry = "", {0: "k", 3: "t", 12: "ch"}[following]
        out.append(onset + MEDIALS[medial] + coda)
        if carry and not isinstance(after, tuple):
            out.append(carry)
            carry = ""
    return "".join(out)


def unit(text: str) -> str:
    for suffix in sorted(UNITS, key=len, reverse=True):
        if text.endswith(suffix) and len(text) > len(suffix):
            return f"{hangul(text[: -len(suffix)]).capitalize()}-{hangul(suffix)}"
    return hangul(text).capitalize()


def korean_run(run: str) -> str:
    seat = ""
    if len(run) > 2 and run[-1] in SEATS and run[-2] in "시군구":
        run, seat = run[:-1], SEATS[run[-1]]
    parts: list[str] = []
    start = 0
    for index in range(1, len(run) - 1):
        if run[index] in "시군구" and index - start >= 1 and len(run) - index - 1 >= 2:
            parts.append(run[start : index + 1])
            start = index + 1
    parts.append(run[start:])
    return " ".join(unit(part) for part in parts if part) + (f" {seat}" if seat else "")


def korean(text: str) -> str:
    text = re.sub(r"제\s*(\d+)\s*(?:투표구|투)", r" Polling District \1 ", text)
    text = re.sub(r"제\s*(\d+)\s*선거구", r" District \1 ", text)
    text = re.sub(r"(?<=[시군구])([가-힣])선거구", lambda match: f" District {hangul(match[1]).capitalize()} ", text)
    text = re.sub(r"선거구", " District ", text)
    text = re.sub(r"제?(\d+)(동|가|리)", lambda match: f" {match[1]}-{hangul(match[2])} ", text)
    text = re.sub(r"제\s*(\d+)(?!\d)", r" District \1 ", text)
    text = text.replace("·", ", ")
    out = re.sub(r"[가-힣]+", lambda match: korean_run(match[0]), text)
    out = re.sub(r"(\w)\(", r"\1 (", out)
    return re.sub(r"\s+([,)])", r"\1", re.sub(r"\(\s+", "(", re.sub(r"\s{2,}", " ", out))).strip()


def taiwan(text: str) -> str:
    def run(match: re.Match[str]) -> str:
        chars = match[0]
        suffix = TAIWAN_UNITS.get(chars[-1]) if len(chars) > 2 else None
        body = chars[:-1] if suffix else chars
        syllables = strip(transform("Han-Latin")(body)).split()
        joined = syllables[0] if syllables else ""
        for syllable in syllables[1:]:
            joined += ("'" if syllable[:1] in "aeo" else "") + syllable
        return joined.capitalize() + (f" {suffix}" if suffix else "")

    return re.sub(r"[㐀-鿿豈-﫿]+", run, text)


def romanizer(lang: str, script: str, group: str, country: str) -> Callable[[str], str] | None:
    if script == "Grek":
        return greek
    if script == "Cyrl":
        if lang == "bg":
            return bulgarian
        if lang == "uk":
            return ukrainian
        if lang == "mn":
            return lambda text: table(text, MONGOLIAN)
        bgn = transform(ICU.get(lang, "Russian-Latin/BGN"))
        return lambda text: unicodedata.normalize("NFC", bgn(text)).replace("·", "").replace("ĭ", "y").replace("Ĭ", "Y")
    if script == "Armn":
        return lambda text: table(text, ARMENIAN, ARMENIAN_INITIAL)
    if script == "Geor":
        bgn = transform(ICU["ka"])
        return lambda text: capitalized(strip(bgn(text)).replace("’", ""))
    if script == "Ethi":
        bgn = transform(ICU.get(lang, ICU["am"]))
        return lambda text: capitalized(strip(bgn(text)))
    if group == "person" or script in LOSSY:
        return None
    if script == "Hang":
        return korean
    if script == "Hani" and lang == "zh" and country == "tw":
        return taiwan
    if script == "Hani":
        return None
    return lambda text: capitalized(strip(transform("Any-Latin")(text)))


def titled(text: str, whole: bool = False) -> str:
    if whole and any(char.islower() for char in text):
        return text
    return re.sub(
        r"\w+",
        lambda match: (
            match[0].capitalize() if len(match[0]) > 1 and match[0].isupper() and match[0] not in ROMAN else match[0]
        ),
        text,
    )


def tidy(text: str) -> str:
    return re.sub(r"\s{2,}", " ", unicodedata.normalize("NFC", text)).strip()


def group_of(type: str) -> str | None:
    if type in PEOPLE:
        return "person"
    if type in PLACES:
        return "place"
    if type in BODIES:
        return "body"
    return None


def plan(entity: Entity, answers: dict[str, dict[str, object]]) -> tuple[list[Name], list[Want]]:
    id, type, rows = entity
    group = group_of(type)
    if group is None:
        return [], []
    named = [row for row in rows if row[2] in KINDS and foreign(row[0]) and row[0].strip()]
    if not named:
        return [], []
    mixed = [row for row in named if row[1] in ("en", "und") and script_of(row[0])["Latn"] > 0]
    own = [row for row in named if row not in mixed]
    names: list[Name] = []
    wants: list[Want] = []
    base = max(own, key=lambda row: (row[3], KINDS[row[2]], len(row[0])), default=None)
    if group == "body" and mixed:
        best = max(mixed, key=lambda row: (row[3], KINDS[row[2]]))
        found = script_of(best[0])
        script = max((code for code in found if code != "Latn"), key=lambda code: found[code])
        lang = base[1] if base is not None and base[1] not in ("en", "und") else SCRIPT_LANGS.get(script, "und")
        country = (base or best)[4]
        swap = romanizer(lang, script, "place", country)
        if swap is not None:
            fragment: Callable[[str], str] = swap
            pattern = r"[^\x00-\x7FÀ-ɏ]+(?:[\s·,()\d-]*[^\x00-\x7FÀ-ɏ]+)*\d*"
            text = tidy(re.sub(pattern, lambda match: titled(fragment(match[0]), True), best[0]))
            if latin(text):
                names.append(Name(id, text, "en", "Latn", "english"))
                return names, wants
    if base is None:
        return names, wants
    text, lang, _, _, country = base
    found = script_of(text)
    if found["Latn"] >= sum(found.values()) / 2:
        return names, wants
    script = max((code for code in found if code != "Latn"), key=lambda code: found[code])
    if lang in ("en", "und"):
        lang = SCRIPT_LANGS.get(script, lang)
    if group == "body" and type == "contest" and script == "Hang" and DISTRICT.search(text):
        english = korean(text)
        if latin(english):
            names.append(Name(id, english, "en", "Latn", "english"))
        return names, wants
    if group == "body":
        want = Want("translate", type, lang, country, text)
        answer = answers.get(want.key)
        if answer is None:
            return names, [want]
        english = answer.get("english")
        if isinstance(english, str) and latin(english):
            personal = answer.get("personal") is True
            names.append(
                Name(
                    id, tidy(english), lang if personal else "en", "Latn", "transliteration" if personal else "english"
                )
            )
        native = answer.get("native")
        if isinstance(native, str) and native != text and same(native, text):
            names.append(Name(id, tidy(native), lang, script, "official"))
        return names, wants
    cased = text
    if group == "person" and script in CASED:
        if script == "Grek":
            if titled(text) != text:
                want = Want("case", group, lang, country, text)
                answer = answers.get(want.key)
                native = answer.get("native") if answer is not None else None
                if answer is None:
                    wants.append(want)
                elif isinstance(native, str) and native != text and same(native, text):
                    names.append(Name(id, tidy(native), lang, script, "official"))
        else:
            cased = titled(text)
            if cased != text:
                names.append(Name(id, tidy(cased), lang, script, "official"))
    convert = romanizer(lang, script, group, country)
    if convert is None:
        want = Want("romanize", group, lang, country, text)
        answer = answers.get(want.key)
        if answer is None:
            return names, [*wants, want]
        romanized = answer.get("latin")
        if isinstance(romanized, str) and latin(romanized):
            names.append(Name(id, tidy(romanized), lang, "Latn", "transliteration"))
        return names, wants
    source = cased if group == "person" else titled(text, True)
    romanized = tidy(titled(convert(source), group == "place"))
    if latin(romanized) or (not foreign(romanized) and any(char.isalpha() for char in romanized)):
        names.append(Name(id, romanized, lang, "Latn", "transliteration"))
    return names, wants


def same(left: str, right: str) -> bool:
    return strip(left).casefold() == strip(right).casefold()


def entities(conn: Connection, ids: list[str]) -> Iterator[Entity]:
    for start in range(0, len(ids), 20_000):
        current: tuple[str, str, list[tuple[str, str, str, bool, str]]] | None = None
        with conn.cursor() as cursor:
            for entity, type, text, lang, kind, preferred, pipeline in cursor.execute(
                ROWS, [ids[start : start + 20_000]]
            ):
                if current is None or current[0] != entity:
                    if current is not None:
                        yield current
                    current = (entity, type, [])
                current[2].append((text, lang, kind, preferred, pipeline.split(".")[0]))
        if current is not None:
            yield current


def candidates(conn: Connection, state: dict[str, object]) -> tuple[list[str], str]:
    until = conn.execute("select coalesce(max(published_at), now())::text from changeset").fetchone()
    assert until is not None
    since = state.get("since")
    pending = cast(list[str], state.get("pending", []))
    if since is None:
        scope = "true"
        params: list[object] = []
    else:
        scope = """n.entity_id in (
            select distinct m.entity_id from name m join changeset c on c.id = m.changeset_id
            where c.status = 'published' and c.published_at > %s and c.published_at <= %s and c.pipeline <> %s
            union select unnest(%s::uuid[]))"""
        params = [since, until[0], PIPELINE, pending]
    ids = [str(id) for (id,) in conn.execute(HOLDERS.format(scope=scope), params)]
    return ids, until[0]


def box(*args: str) -> bool:
    return subprocess.run(["rsync", "-a", *args], capture_output=True, check=False).returncode == 0


def held(file: Path) -> dict[str, dict[str, object]]:
    return json.loads(file.read_text()) if file.exists() else {}


def source(cs: Changeset) -> object:
    id = cs.source(PUBLISHER, TITLE)
    if id in cs.minted:
        cs.add(
            "source",
            id=id,
            kind="compendium",
            publisher=PUBLISHER,
            title=TITLE,
            url=SOURCE_URL,
            license=LICENSE,
            license_url=LICENSE_URL,
            notes="Transliterations in each language's official romanization (BGN/PCGN where none exists), English "
            "translations, and sentence-case forms of all-caps names, generated by the archive and reviewed by a "
            "second model pass; they never replace a source's own names.",
        )
    return id


def emit(conn: Connection, names: list[Name]) -> list[str]:
    folders: list[str] = []
    grouped: dict[str, list[Name]] = collections.defaultdict(list)
    for name in names:
        grouped[name.lang].append(name)
    for lang, rows in sorted(grouped.items()):
        for start in range(0, len(rows), LIMIT):
            chunk = rows[start : start + LIMIT]
            cs = Changeset(
                conn,
                pipeline=PIPELINE,
                version=VERSION,
                summary=f"Latin-script and sentence-case names generated by the archive for {len(chunk):,} "
                f"{lang} names that had no Latin-script form",
                polity=None,
                kind="edit",
            )
            sid = source(cs)
            for name in chunk:
                cs.add(
                    "name",
                    entity_id=name.entity,
                    text=name.text,
                    lang=name.lang,
                    script=name.script,
                    kind=name.kind,
                    is_preferred=True,
                    source_id=sid,
                )
            folders.append(str(cs.write()))
    return folders


def granted(conn: Connection, id: str, folder: Path) -> list[str]:
    problems: list[str] = []
    found = conn.execute("select checks from changeset where id = %s", [id]).fetchone()
    checks = cast(dict[str, object], found[0] if found is not None else {})
    if checks.get("findings"):
        problems.append(f"findings {json.dumps(checks['findings'])}")
    diff = cast(dict[str, dict[str, int]], checks.get("diff", {}))
    for table, change in diff.items():
        inserted, updated, deleted = change.get("insert", 0), change.get("update", 0), change.get("delete", 0)
        if updated or deleted or (inserted and table not in ("name", "source")) or (table == "source" and inserted > 1):
            problems.append(f"{table} {change}")
    staged = pl.read_parquet(folder / "name.parquet")
    ours = conn.execute(
        "select id::text from source where publisher = %s and title = %s", [PUBLISHER, TITLE]
    ).fetchone()
    minted = (
        pl.read_parquet(folder / "source.parquet")["id"].cast(pl.String).to_list()
        if (folder / "source.parquet").exists()
        else []
    )
    sources = {str(value) for value in staged["source_id"].unique().to_list()}
    if sources - {*(ours or ()), *minted}:
        problems.append("names not marked as generated by the archive")
    wrong = staged.filter(
        ~pl.col("kind").is_in(GENERATED) | ((pl.col("kind") != "official") & (pl.col("script") != "Latn"))
    )
    if len(wrong):
        problems.append(f"{len(wrong)} names of another kind")
    return problems


def kept(conn: Connection, id: str) -> list[str]:
    changed = conn.execute("select count(*) from name_history where superseded_by = %s", [id]).fetchone()
    return [f"{changed[0]} existing names changed"] if changed is not None and changed[0] else []


def record(published: list[tuple[str, int]], held_back: list[tuple[str, str]]) -> None:
    lines = [f"- `{id}`: {count:,} names, applied and published" for id, count in published]
    lines += [f"- `{id}`: held in pending_review for Firat ({why})" for id, why in held_back]
    if not lines:
        return
    body = "Naming pass (`ea names latin`), under the standing grant for add-only name changesets:\n\n" + "\n".join(
        lines
    )
    subprocess.run(
        ["gh", "issue", "comment", TICKET[0], "-R", TICKET[1], "--body", body], capture_output=True, check=False
    )


def run() -> str:
    WORK.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "another naming pass is running"
        box(f"{BOX}/answers.json", str(ANSWERS))
        answers = held(ANSWERS)
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
        queue = held(QUEUE)
        names: list[Name] = []
        pending: set[str] = set()
        with connect() as conn:
            conn.execute("set statement_timeout = 0")
            ids, until = candidates(conn, state)
            for entity in entities(conn, ids):
                found, wants = plan(entity, answers)
                names += found
                for want in wants:
                    queue[want.key] = want.__dict__
                    pending.add(entity[0])
            folders = emit(conn, names) if names else []
        published: list[tuple[str, int]] = []
        held_back: list[tuple[str, str]] = []
        for folder in folders:
            path = Path(folder)
            id = path.name
            status = apply(path)
            if status == "unchanged":
                continue
            with connect() as conn:
                problems = granted(conn, id, path) if status == "pending_review" else [f"status {status}"]
                if problems:
                    held_back.append((id, "; ".join(problems)))
                    continue
                publish(conn, id)
                if problems := kept(conn, id):
                    revert(conn, id, "names.latin may only add names: " + "; ".join(problems))
                    held_back.append((id, "reverted after publish: " + "; ".join(problems)))
                    continue
            published.append((id, len(pl.read_parquet(path / "name.parquet"))))
        queue = {key: item for key, item in queue.items() if key not in answers}
        QUEUE.write_text(json.dumps(queue, ensure_ascii=False))
        box(str(QUEUE), f"{BOX}/queue.json")
        STATE.write_text(json.dumps({"since": until, "pending": sorted(pending), "at": time.time()}))
        record(published, held_back)
        notes = [f"{len(ids):,} entities without a Latin-script name, {len(names):,} names generated"]
        notes += [f"published {id} ({count:,} names)" for id, count in published]
        notes += [f"held {id}: {why}" for id, why in held_back]
        notes.append(f"{len(queue):,} names wait for the model step (ea names model on the Mac)")
        return "; ".join(notes)


TRANSLATE = """You give English names to political parties, alliances, legislatures and councils, offices, contests (races), ballot lists, and elections for electionsarchive.com, a world election archive. A header gives the kind of thing every item names, the country's ISO code, and the language; the items map each id to the name as the source prints it.

For each item, reply with an object:
- "english": the name in English. Use the established English name when one exists (a party's own English name, the usual English name of a parliament or council), otherwise translate it faithfully and concisely, in title case, as a careful English-language newspaper would. Never translate the proper nouns inside it: romanize people's names and place names in the country's official romanization (Revised Romanization for Korean, ELOT 743 for Greek, Bulgaria's 2009 system, Ukraine's 2010 system, MNS 5217 for Mongolian, BGN/PCGN otherwise), and keep an acronym only when the source gives one, romanized. For a contest, write the race's name, such as "Gangdong-gu Mayor" or "Jung-gu District 2"; for a council, write it like "Buk-gu Council".
- "native": only when the source name is written entirely in capital letters, the same name in its own script with the capitalization the organization uses in running text, restoring Greek accents. Change nothing but capitalization and accents. Leave it out otherwise.
- "personal": true only when the name is just a person's name (an independent candidate or a list named after a person); "english" is then that name romanized, not translated, in normal capitalization.

Reply with only a JSON object mapping each id to its object. Include every id you were given and nothing else."""

ROMANIZE = """You romanize the names of people and places for electionsarchive.com, a world election archive. A header says whether the items are people or places and gives the country's ISO code and the language; the items map each id to the name as the source prints it.

Write each name in Latin script as the country conventionally writes it in English, with normal capitalization, keeping the name parts in the source's order. Never translate a name; only romanize it.
- Korea (kr), people: the family name in its conventional spelling (Kim, Lee, Park, Choi, Jung, Kang, Cho, Yoon, Jang, Lim, Han, Oh, Seo, Shin, Kwon, Hwang, Ahn, Song, Yoo, Hong), a space, then the given name in Revised Romanization with its syllables joined by a hyphen: 김상술 → Kim Sang-sul.
- Japan (jp): modified Hepburn without macrons (ō → o, ū → u), family name first as in the source: 井筒 伸幸 → Izutsu Nobuyuki; a name written partly in kana is read as written. Places: Hepburn, with the municipal suffix joined by a hyphen: 中津川市 → Nakatsugawa-shi, 小谷村 → Otari-mura.
- Taiwan (tw), people: the Wade-Giles-based spelling of Taiwanese passports, family name first, given-name syllables joined by a hyphen: 蘇清泉 → Su Ching-chuan; a two-character family name stays joined: 張廖萬堅 → Chang-Liao Wan-chien.
- South Asia and Myanmar: the conventional English spelling of the name in that country: नन्द किशोर बस्नेत → Nanda Kishor Basnet.
- Arabic, Hebrew, and Persian: the conventional English spelling used in that country: حسن محمد أحمد عصفور → Hasan Muhammad Ahmad Asfour.

Reply with only a JSON object mapping each id to the romanized name. Include every id you were given and nothing else."""

CASE = """Each item maps an id to a Greek name printed in capital letters on a ballot. Give the same name in normal Greek capitalization with its accents, as it is written in running text: ΡΟΥΣΣΟΥ ΙΩΑΝΝΑ του ΑΝΔΡΕΑ → Ρούσσου Ιωάννα του Ανδρέα. Change nothing but capitalization and accents.

Reply with only a JSON object mapping each id to the name. Include every id you were given and nothing else."""

REVIEW = """You are the senior reviewer of names prepared for electionsarchive.com, a world election archive. A header gives the task ("translate" gives an English name; "romanize" writes a person's or place's name in Latin script as the country conventionally does; "case" restores normal capitalization and accents to a Greek all-caps name), the kind of thing named, the country's ISO code, and the language; the items map each id to the source name ("text") and the draft.

Check each draft against the source. Report only real problems: a wrong or misleading translation, a missed established English name, a translated proper noun, a romanization that breaks the country's convention (Korean people: conventional family name and hyphenated Revised Romanization given name; Japan: Hepburn without macrons; Taiwan: Wade-Giles-based passport spelling), a misread character, or a "native" or "case" form that changes anything but capitalization and accents.

Reply with only a JSON object mapping each id that needs a change to {"fix": <the corrected draft, in the same shape as the draft>, "why": "<short reason>"}. Reply {} when every draft is right."""

PROMPTS = {"translate": TRANSLATE, "romanize": ROMANIZE, "case": CASE, "review": REVIEW}


def chunks(
    task: str, header: dict[str, str], items: list[tuple[str, object]]
) -> dict[str, tuple[batch.Want, dict[str, str]]]:
    out: dict[str, tuple[batch.Want, dict[str, str]]] = {}
    groups: list[list[tuple[str, object]]] = [[]]
    size = 0
    for key, payload in sorted(items, key=lambda pair: json.dumps(pair[1], ensure_ascii=False)):
        weight = len(json.dumps(payload, ensure_ascii=False)) + 8
        if groups[-1] and size + weight > CHUNK_CHARS:
            groups.append([])
            size = 0
        groups[-1].append((key, payload))
        size += weight
    price_in, price_out = batch.PRICES[MODEL]
    for group in groups:
        if not group:
            continue
        body = {str(index + 1): payload for index, (_, payload) in enumerate(group)}
        prompt = (
            json.dumps(header, ensure_ascii=False) + "\n\nItems:\n" + json.dumps(body, ensure_ascii=False, indent=0)
        )
        params: dict[str, object] = {
            "model": MODEL,
            "max_tokens": 16_000,
            "output_config": {"effort": EFFORT},
            "system": [{"type": "text", "text": PROMPTS[task], "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            "messages": [{"role": "user", "content": prompt}],
        }
        written = len(group) * 8 if task == "review" else len(prompt) * (1.6 if task == "translate" else 0.9)
        cost = (len(prompt) / 2.0 * price_in + len(PROMPTS[task]) / 4.0 * price_in + written / 2.5 * price_out) / 1e6
        digest = hashlib.md5(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        out[f"{task}/{digest}"] = (
            (cast(MessageCreateParamsNonStreaming, params), cost),
            {str(index + 1): key for index, (key, _) in enumerate(group)},
        )
    return out


def batches(
    task: str, items: dict[str, dict[str, object]], payload: Callable[[str, dict[str, object]], object]
) -> dict[str, tuple[batch.Want, dict[str, str]]]:
    grouped: dict[tuple[str, str, str, str], list[tuple[str, object]]] = collections.defaultdict(list)
    for key, item in items.items():
        grouped[(str(item["task"]), str(item["group"]), str(item["lang"]), str(item["country"]))].append(
            (key, payload(key, item))
        )
    out: dict[str, tuple[batch.Want, dict[str, str]]] = {}
    for (kind, group, lang, country), rows in grouped.items():
        header = {"kind": group, "country": country, "language": lang} | ({"task": kind} if task == "review" else {})
        out |= chunks(task, header, rows)
    return out


def reply(key: str) -> dict[str, object] | None:
    message = json.loads(raw.cached(PIPELINE, key).path.read_text())
    text = "".join(block["text"] for block in message["content"] if block["type"] == "text").strip()
    start = text.find("{")
    if start == -1:
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError:
        return None
    return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None


def valid(item: dict[str, object], answer: object) -> dict[str, object] | None:
    task = item["task"]
    text = str(item["text"])
    if task == "romanize":
        return {"latin": answer.strip()} if isinstance(answer, str) and latin(answer) else None
    if task == "case":
        return {"native": answer.strip()} if isinstance(answer, str) and same(answer, text) else None
    if not isinstance(answer, dict):
        return None
    english = answer.get("english")
    if not isinstance(english, str) or not latin(english):
        return None
    out: dict[str, object] = {"english": english.strip()}
    native = answer.get("native")
    if isinstance(native, str) and native.strip() != text and same(native, text):
        out["native"] = native.strip()
    if answer.get("personal") is True:
        out["personal"] = True
    return out


def drafted(answer: dict[str, object], task: str) -> object:
    if task == "romanize":
        return answer["latin"]
    if task == "case":
        return answer["native"]
    return answer


def advance(stage: str, wanted: dict[str, tuple[batch.Want, dict[str, str]]]) -> tuple[bool, str]:
    projected = sum(want[1] for want, _ in wanted.values())
    if projected > JOB_CAP:
        raise SystemExit(f"BLOCKED: {stage} would cost about ${projected:.2f}, over the ${JOB_CAP:.0f} cap per job")
    return batch.ensure(PIPELINE, stage, {key: want for key, (want, _) in wanted.items()})


def quote() -> str:
    box(f"{BOX}/queue.json", str(QUEUE))
    queue = held(QUEUE)
    answers = held(ANSWERS)
    todo = {key: item for key, item in queue.items() if key not in answers}
    lines: list[str] = []
    total = 0.0
    for task in ("translate", "romanize", "case"):
        items = {key: item for key, item in todo.items() if item["task"] == task}
        wanted = batches(task, items, lambda _, item: item["text"])
        draft = sum(want[1] for want, _ in wanted.values())
        review = sum(
            want[1]
            for want, _ in batches("review", items, lambda _, item: {"text": item["text"], "draft": "x" * 16}).values()
        )
        total += draft + review
        lines.append(f"{task}: {len(items):,} names, {len(wanted)} requests, ${draft:.2f} plus ${review:.2f} review")
    return "; ".join(lines) + f"; about ${total:.2f} in all"


def model() -> str:
    WORK.mkdir(parents=True, exist_ok=True)
    box(f"{BOX}/queue.json", str(QUEUE))
    box(f"{BOX}/answers.json", str(ANSWERS))
    queue = held(QUEUE)
    answers = held(ANSWERS)
    drafts = held(DRAFTS)
    notes: list[str] = []
    todo = {key: item for key, item in queue.items() if key not in answers and key not in drafts}
    if todo:
        wanted: dict[str, tuple[batch.Want, dict[str, str]]] = {}
        for task in ("translate", "romanize", "case"):
            items = {key: item for key, item in todo.items() if item["task"] == task}
            wanted |= batches(task, items, lambda _, item: item["text"])
        ready, note = advance("draft", wanted)
        notes.append(note)
        if not ready:
            return "; ".join(notes)
        keys = raw.keys(PIPELINE)
        for request, (_, ids) in wanted.items():
            parsed = reply(request) if request in keys else None
            for local, key in ids.items():
                answer = valid(queue[key], parsed.get(local)) if parsed is not None else None
                if answer is not None:
                    drafts[key] = answer
        DRAFTS.write_text(json.dumps(drafts, ensure_ascii=False))
    unreviewed = {key: queue[key] for key in drafts if key not in answers and key in queue}
    if unreviewed:
        wanted = {}
        for task in ("translate", "romanize", "case"):
            items = {key: item for key, item in unreviewed.items() if item["task"] == task}
            wanted |= batches(
                "review",
                items,
                lambda key, item: {"text": item["text"], "draft": drafted(drafts[key], str(item["task"]))},
            )
        ready, note = advance("review", wanted)
        notes.append(note)
        if not ready:
            return "; ".join(notes)
        keys = raw.keys(PIPELINE)
        flagged = 0
        with REVIEWS.open("a") as log:
            for request, (_, ids) in wanted.items():
                if request not in keys:
                    continue
                parsed = reply(request) or {}
                for local, key in ids.items():
                    answer = drafts[key]
                    found = parsed.get(local)
                    fix = found.get("fix") if isinstance(found, dict) else None
                    if fix is not None:
                        flagged += 1
                        why = str(cast(dict[str, object], found).get("why", ""))
                        log.write(
                            f"{queue[key]['text']}\t{json.dumps(answer, ensure_ascii=False)}\t"
                            f"{json.dumps(fix, ensure_ascii=False)}\t{why}\n"
                        )
                        answer = valid(queue[key], fix) or answer
                    answers[key] = answer
        notes.append(f"review changed {flagged} of {len(unreviewed)} drafts")
        ANSWERS.write_text(json.dumps(answers, ensure_ascii=False))
        box(str(ANSWERS), f"{BOX}/answers.json")
    notes.append(f"{len(answers):,} answers, {sum(key not in answers for key in queue):,} waiting")
    return "; ".join(notes)
