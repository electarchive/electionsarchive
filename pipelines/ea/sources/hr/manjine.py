"""Croatian national minority council and representative elections 2003-2023, from the State Electoral Commission.

The commission publishes, for each county, one PDF per election year that holds every unit's results: the county's
own minority councils and representatives, then each town and municipality. The PDFs list only the elected
candidates, each with a vote count, and the electorate, turnout, and valid and invalid ballots of each contest.
"""

import html
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl
import shapely

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.hr import izbori, lokalni

SOURCE = "hr.manjine"
SITE = "https://www.izbori.hr/arhiva-izbora"
DATA = f"{SITE}/data/manjine"
YEARS = [2003, 2007, 2011, 2015, 2019, 2023]
DAY = {
    2003: date(2003, 5, 18),
    2007: date(2007, 6, 17),
    2011: date(2011, 7, 10),
    2015: date(2015, 5, 31),
    2019: date(2019, 5, 5),
    2023: date(2023, 5, 7),
}
RUNOFF = {2019: date(2019, 5, 19)}
COUNTIES = [f"{n:02d}" for n in range(1, 22)]
REGISTERED, CAST, VALID, INVALID, VOTES, VOTERS = 2, 3, 4, 5, 1, 14


def report_url(year: int, county: str) -> str:
    return f"{DATA}/{year}/izabrani/i_90_{county}_0000.pdf"


def fetch() -> None:
    urls = [report_url(y, c) for y in YEARS for c in COUNTIES]
    got = raw.fetch_many(SOURCE, urls, suffix=".pdf", workers=4)
    print(f"{SOURCE}: {len(got)} of {len(urls)} reports cached", flush=True)
    for url in (lokalni.GB_COUNTIES, lokalni.GB_TOWNS):
        raw.fetch(SOURCE, url, suffix=".geojson")


def clean(text: object) -> str:
    return " ".join(str(text if text is not None else "").replace("\xa0", " ").split())


def fold(text: object) -> str:
    plain = unicodedata.normalize("NFKD", clean(text).replace("đ", "d").replace("Đ", "D"))
    return " ".join(plain.encode("ascii", "ignore").decode().lower().split())


# Reading the PDFs.

LATIN = {"þ": "č", "ü": "ć", "ý": "Č", "û": "Ć", "å": "ž", "ä": "Ž", "â": "Š", "ã": "š", "ð": "Đ", "ñ": "đ"}


def _shift(text: str) -> str:
    """Text of the 2003 report's body font, whose glyph codes sit 29 below their characters."""
    out = []
    for ch in text:
        code = ord(ch)
        if code == 3:
            out.append(" ")
        elif 3 < code < 0x7F:
            out.append(chr(code + 29))
        else:
            out.append(LATIN.get(ch, ch))
    return "".join(out)


WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', re.DOTALL)


def _words(path: Path) -> list[list[tuple[float, float, str]]]:
    """The words of each page of a 2003 report with their positions, since the PDF's stream order is scrambled."""
    text = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    pages = []
    for chunk in text.split("<page ")[1:]:
        found = [
            (float(x0), (float(y0) + float(y1)) / 2, html.unescape(word)) for x0, y0, _, y1, word in WORD.findall(chunk)
        ]
        pages.append(found)
    return pages


def _unmix(seg: str) -> str:
    """A name part set in the second font: plain capitals, or capitals whose codes sit 29 above."""
    if re.fullmatch(r"[A-ZŠČĆŽĐ.\-']+", seg):
        return seg
    if re.fullmatch(r"[A-Za-zŠČĆŽĐ^_`.\-']+", seg) and re.search(r"[a-z^_`]", seg):
        return "".join(chr(ord(ch) - 29) if "a" <= ch <= "z" or ch in "^_`" else ch for ch in seg)
    return _shift(seg)


def _line2003(words: list[tuple[float, float, str]]) -> str:
    ordered = [word for _, _, word in sorted(words)]
    row = len(ordered) > 2 and bool(re.fullmatch(r"\d+\.", _shift(ordered[0]).strip()))
    # Some words (candidates' names and unit headings) are set in a second font, as plain capitals; every other word
    # is in the shifted font and ends in a control code. A name part of a row may sit in the same word as another.
    text = ""
    previous_shifted = False
    previous = ""
    for index, word in enumerate(ordered):
        name = row and index > 0 and not re.fullmatch(r"\d[\d.]*", _shift(word).strip())
        if name:
            segments = [seg for seg in word.split("\x03") if seg.strip()]
            decoded = " ".join(_unmix(seg) for seg in segments)
            shifted = bool(segments) and all(_unmix(seg) == _shift(seg) for seg in segments) and "\x03" in word
        else:
            plain = not row and bool(re.search(r"[A-ZŠČĆŽĐ]", word)) and not re.search(r"[\x00-\x1f]", word)
            decoded = word if plain else _shift(word)
            shifted = not plain
        if index:
            glued = shifted and previous_shifted and not previous.endswith("\x03") and not word.startswith("\x03")
            votes = row and not name and bool(re.fullmatch(r"\d[\d.]*", decoded.strip()))
            text += "đ" if glued else "  " if votes else " "
        text += decoded
        previous, previous_shifted = word, shifted
    text = re.sub(r"(?<=[A-ZČĆŽŠĐ])đ|đ(?=[A-ZČĆŽŠĐ])", "Đ", text)
    if re.match(r"^(GRAD|OPĆINA)\s", text) and not row:
        # Part of a unit's name may be set in a font whose codes sit 29 above the capitals.
        text = "".join(chr(ord(ch) - 29) if "a" <= ch <= "z" or ch in "^_`" else ch for ch in text)
    return text


def pdf_text(path: Path, year: int) -> list[str]:
    if year == 2003:
        lines: list[str] = []
        for page in _words(path):
            rows: list[list[tuple[float, float, str]]] = []
            for word in sorted(page, key=lambda w: w[1]):
                if rows and abs(rows[-1][0][1] - word[1]) < 4:
                    rows[-1].append(word)
                else:
                    rows.append([word])
            lines += [_line2003(row) for row in rows]
        return lines
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    if year in (2007, 2011):
        text = "".join(LATIN.get(ch, ch) for ch in text)
    return text.split("\n")


# Parsing the reports.

MINORITIES = {
    "albansk": ("Albanian", "albanska"),
    "bosnjack": ("Bosniak", "bošnjačka"),
    "bugarsk": ("Bulgarian", "bugarska"),
    "crnogorsk": ("Montenegrin", "crnogorska"),
    "cesk": ("Czech", "češka"),
    "madarsk": ("Hungarian", "mađarska"),
    "makedonsk": ("Macedonian", "makedonska"),
    "njemack": ("German", "njemačka"),
    "poljsk": ("Polish", "poljska"),
    "romsk": ("Romani", "romska"),
    "rumunjsk": ("Romanian", "rumunjska"),
    "rusinsk": ("Rusyn", "rusinska"),
    "rusk": ("Russian", "ruska"),
    "slovack": ("Slovak", "slovačka"),
    "slovensk": ("Slovene", "slovenska"),
    "srpsk": ("Serb", "srpska"),
    "talijansk": ("Italian", "talijanska"),
    "tursk": ("Turkish", "turska"),
    "ukrajinsk": ("Ukrainian", "ukrajinska"),
    "vlask": ("Vlach", "vlaška"),
    "zidovsk": ("Jewish", "židovska"),
    "austrijsk": ("Austrian", "austrijska"),
}


def minority_of(word: str) -> str:
    folded = fold(word)
    for stem in MINORITIES:
        if folded.startswith(stem):
            return stem
    raise ValueError(f"unknown minority {word!r}")


@dataclass
class Candidate:
    name: str
    votes: int
    deputy: str = ""
    raw: str = ""


@dataclass
class Round:
    registered: int | None = None
    voters: int | None = None
    cast: int | None = None
    valid: int | None = None
    invalid: int | None = None
    elected: list[Candidate] = field(default_factory=list)
    note: str = ""  # the report's wording when no candidate was elected


@dataclass
class Contest:
    year: int
    county: str
    unit: str  # "" for the county itself, else the town or municipality as headed
    kind: str  # council or representative
    minority: str  # a key of MINORITIES
    rounds: list[Round] = field(default_factory=list)
    cue: str = ""  # the kind the report's own wording of the elected candidates implies

    @property
    def ident(self) -> tuple[str, ...]:
        return (self.county, fold(self.unit), self.kind, self.minority)


NUMBER = re.compile(r"(\d[\d.]*)")
ROW = re.compile(r"^\s*(\d+)\.\s+(.*?)\s{2,}(\d[\d.]*)\s*$")
BARE = re.compile(r"^\s*(\d+)\.\s+(\S.*?)\s*$")
HEAD_MUNI = re.compile(r"^(GRAD|OPĆINA)\s+(\S.*)$")
MINORITY_OLD = re.compile(r"^Za (\S+) nacionalnu manjinu\s*$")
MINORITY_NEW = re.compile(r"^(\S+) nacionalna manjina\s*$")


WORDLIKE = re.compile(r"[A-Za-zČĆŽŠĐčćžšđ.',\-]+|\d{2}\.\d{2}\.\d{4}\.?")


def sanitize(text: str, year: int) -> str:
    """A candidate's name with the words dropped that the 2003 report's fonts leave unreadable; empty when none is left."""
    text = text.replace("\xad", "-")
    if year == 2003:
        words = [w for w in clean(text).split() if WORDLIKE.fullmatch(w)]
    else:
        words = [
            w for w in clean(text).split() if all(ch.isalpha() or ch in ".',-–" for ch in w) or DATE_ONLY.fullmatch(w)
        ]
    upper = [w for w in words if w[:1].isupper() and not DATE_ONLY.fullmatch(w)]
    return " ".join(words) if len(upper) >= 2 else ""


DATE_ONLY = re.compile(r"\d{2}\.\d{2}\.\d{4}\.?")


def count(text: str) -> int | None:
    found = NUMBER.search(text.split(":", 1)[-1])
    return int(found.group(1).replace(".", "")) if found else None


def parse_report(year: int, county: str, lines: list[str]) -> list[Contest]:
    out: list[Contest] = []
    unit = ""
    kind = ""
    current: Contest | None = None
    rnd: Round | None = None
    pending = ""
    previous = ""
    headed = False
    for line in lines:
        text = clean(line)
        if not text:
            continue
        if text in ("GRAD", "OPĆINA"):
            pending = text
            continue
        if pending:
            text, pending = f"{pending} {text}", ""
        head = HEAD_MUNI.match(text)
        if head and text == text.upper() and "IZBORI" not in text and "ŽUPANIJ" not in text:
            unit = "" if county == "21" else clean(head.group(2))
            if county == "21":
                unit = ""
            else:
                unit = clean(text)
            current = rnd = None
            headed = False
            continue
        if text.startswith("IZBORI") or (text.startswith("Izbori") and text.isupper()):
            headed = True
            if "ČLANOV" in text:
                kind = "council"
            elif "PREDSTAVNIK" in text:
                kind = "representative"
            if "U ŽUPANIJI" in text:
                unit = ""
            if "U GRADU ZAGREBU" in text:
                unit = ""
            current = rnd = None
            continue
        found = MINORITY_OLD.match(text) or MINORITY_NEW.match(text)
        if found and not text.startswith("Za predstavni"):
            current = Contest(year, county, unit, kind, minority_of(found.group(1)))
            rnd = Round()
            current.rounds.append(rnd)
            out.append(current)
            continue
        if current is None or rnd is None:
            continue
        if re.match(r"^U [Vv]ijeće", text):
            current.cue = "council"
        elif re.match(r"^Za predstavni", text):
            current.cue = "representative"
        if not headed and current.cue:
            current.kind = current.cue
        if text.startswith("Drugi krug"):
            rnd = Round()
            current.rounds.append(rnd)
            continue
        if text.startswith("Prvi krug"):
            continue
        if text.startswith("Izbori nisu održani"):
            current.rounds.clear()
            rnd = None
            continue
        if text.startswith("Ukupno birača"):
            rnd.registered = count(text)
        elif text.startswith("Pristupilo glasovanju"):
            rnd.voters = count(text)
        elif text.startswith("Glasovalo birača"):
            if "listićima" in text:
                rnd.cast = count(text)
            else:
                rnd.voters = count(text)
        elif text.startswith("Važećih"):
            rnd.valid = count(text)
        elif text.startswith("Nevažećih"):
            rnd.invalid = count(text)
        elif re.match(r"^Zamjeni(k|ca)\b", text):
            if rnd.elected:
                rnd.elected[-1].deputy = clean(text.split(":", 1)[1])
        elif ROW.match(line) and not re.match(r"^\s*\d+\.\s+\d", line):
            row = ROW.match(line)
            assert row is not None
            rnd.elected.append(
                Candidate(sanitize(row.group(2), year), int(row.group(3).replace(".", "")), raw=clean(row.group(2)))
            )
        elif "nije izabran" in text or previous.endswith("nije bilo") or "odaziva" in text:
            rnd.note = clean(f"{rnd.note} {text}")
        previous = text
    for c in out:
        # A heading that calls a contest the representative's, over a body of several elected members.
        if c.kind == "representative" and c.cue == "council" and any(len(r.elected) > 1 for r in c.rounds):
            c.kind = "council"
        # A heading that calls a contest the council's, over one elected candidate whom the report names a representative.
        elif (
            c.kind == "council"
            and c.cue == "representative"
            and c.rounds
            and all(len(r.elected) == 1 for r in c.rounds)
        ):
            c.kind = "representative"
    return out


@dataclass
class Report:
    year: int
    county: str
    contests: list[Contest]


def reports(year: int | None = None) -> list[Report]:
    out = []
    for y in YEARS if year is None else [year]:
        for county in COUNTIES:
            path = raw.cached(SOURCE, report_url(y, county)).path
            out.append(Report(y, county, parse_report(y, county, pdf_text(path, y))))
    return out


# Units.

ALIASES = {
    # Names the 2003 report prints with a letter missing, and spellings the commission has since changed.
    "ivanjska": "ivanska",
    "ulovac": "dulovac",
    "akovo": "dakovo",
    "urdenovac": "durdenovac",
    "malinska": "malinska dubasnica",
    "kastelir": "kastelir labinci",
    "tar": "tar vabriga",
    "sveta nedjelja": "sveta nedelja",
}
CROATIA = izbori.CROATIA


def unit_place(county: str, unit: str) -> str:
    """The folded name of a town or municipality, as the Croatian geography package knows it."""
    name = izbori.place(re.sub(r"\s*-\s+", " - ", unit.replace("–", "-")))
    name = ALIASES.get(name, name)
    if (county, name) not in izbori.municipalities():
        raise ValueError(f"unknown unit {unit!r} in county {county}")
    return name


def area_of(c: Contest) -> tuple[str, str]:
    """The scheme and code of the area whose members elect a contest."""
    if not c.unit:
        if c.county == "21":
            return "hr.opcina", "21:zagreb"
        return "hr.zupanija", c.county
    return "hr.opcina", f"{c.county}:{unit_place(c.county, c.unit)}"


def area_id(scheme: str, code: str) -> str:
    return izbori.area(scheme, code)


def place_names(c: Contest) -> tuple[str, str, str]:
    """English and Croatian names of a contest's unit, and the slug part that places it."""
    if not c.unit:
        if c.county == "21":
            return "Zagreb", "Grad Zagreb", "21-zagreb"
        who = izbori.title(re.sub(r"\s+ŽUPANIJA$", "", izbori.counties()[c.county], flags=re.IGNORECASE))
        return f"{who} County", f"{who} županija", f"{c.county}-{slug(who)}"
    name = unit_place(c.county, c.unit)
    canonical = izbori.municipalities()[(c.county, name)][1]
    who = izbori.title(clean(canonical).split(" - ")[0])
    return who, who, f"{c.county}-{slug(who)}"


def names_of(c: Contest) -> tuple[str, str, str]:
    english_group, croatian_group = MINORITIES[c.minority]
    where_en, where_hr, where = place_names(c)
    if c.kind == "council":
        return (
            f"{english_group} National Minority Council of {where_en}",
            f"Vijeće nacionalne manjine ({croatian_group}): {where_hr}",
            f"{where}/{slug(english_group)}-council",
        )
    return (
        f"Representative of the {english_group} National Minority in {where_en}",
        f"Predstavnik nacionalne manjine ({croatian_group}): {where_hr}",
        f"{where}/{slug(english_group)}-representative",
    )


UNREADABLE = "Name not legible in the report"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)


def seat(c: Contest) -> str:
    """What a contest fills, the same across years: one body or office per minority and unit."""
    return key("body" if c.kind == "council" else "office", *c.ident)


def system(name: str) -> str:
    return key("electoral_system", name)


# Names of candidates.

DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\.?")
LETTERS = re.compile(r"[A-Za-zČĆŽŠĐčćžšđ.'\-]+")


def person_of(text: str) -> tuple[str, date | None]:
    """A candidate's name as printed without titles, and the birth date the report prints to tell namesakes apart."""
    born = DATE.search(text)
    birth = None
    if born:
        try:
            birth = date(int(born.group(3)), int(born.group(2)), int(born.group(1)))
        except ValueError:
            birth = None
    core = clean(text.split(",", 1)[0])
    words = [w for w in core.split() if LETTERS.fullmatch(w)]
    return izbori.title(" ".join(words)), birth


# Bodies, offices, electoral systems, and the source.


def contests(year: int) -> list[Contest]:
    return [c for r in reports(year) for c in r.contests if c.rounds]


def all_contests() -> list[Contest]:
    return [c for y in YEARS for c in contests(y)]


METHOD = (
    "The State Electoral Commission (Državno izborno povjerenstvo) publishes, for each county, one report with every "
    "unit's results: the county's own minority councils and representatives, then those of each town and "
    "municipality. A report gives each contest's registered voters, the voters who took part, and the valid and "
    "invalid ballots, and lists the elected candidates with their votes. It does not publish the votes of candidates "
    "who were not elected, so only the elected candidates appear here. A contest in which no candidate stood is "
    "not recorded as held. The council and representative elections of one unit and minority are separate contests."
)
SYSTEM_COUNCIL = (
    "Members of a national minority on a county, town, or municipal roll elect the minority's council of 10, 15, or "
    "25 members (Ustavni zakon o pravima nacionalnih manjina, art. 24; Zakon o izboru vijeća i predstavnika nacionalnih "
    "manjina). Each voter may vote for as many candidates as there are seats, and the candidates with the most votes "
    "are elected. A council that the first vote leaves incomplete is filled in a second vote two weeks later."
)
SYSTEM_REPRESENTATIVE = (
    "Where a national minority is too small for a council it elects a representative and a deputy. Each voter votes "
    "for one candidate and the candidate with the most votes is elected."
)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Boundaries of Croatia's counties and of the towns and municipalities that elected minority councils and "
        "representatives before 2013, from OpenStreetMap through geoBoundaries",
        depends=[f"{izbori.SOURCE}/geo"],
    )
    county_source, town_source = key("source", "geoboundaries-adm1"), key("source", "geoboundaries-adm2")
    for id, title_, license_, license_url, notes in (
        (
            county_source,
            "geoBoundaries gbOpen: Croatia ADM1 (counties)",
            "Open Data Commons Open Database License 1.0 (OpenStreetMap contributors)",
            "https://opendatacommons.org/licenses/odbl/1-0/",
            "Boundary year 2017, built December 2023. Used for 2003 to 2012 as the nearest open outline.",
        ),
        (
            town_source,
            "geoBoundaries gbOpen: Croatia ADM2 (municipalities and towns)",
            "Creative Commons Attribution-ShareAlike 2.0 (OpenStreetMap contributors)",
            "https://www.openstreetmap.org/copyright",
            (
                "Boundary year 2021, built December 2023. Used for 2003 to 2012 as the nearest open outline; the "
                "municipalities created after 2003 are drawn on their later boundaries, so the outlines of their "
                "parents are slightly too large before then. Shapes of islands are not used."
            ),
        ),
    ):
        p.add(
            "source",
            id=id,
            kind="academic",
            publisher="geoBoundaries (William & Mary geoLab), from OpenStreetMap",
            title=title_,
            url="https://www.geoboundaries.org/",
            license=license_,
            license_url=license_url,
            attribution="geoBoundaries; © OpenStreetMap contributors",
            notes=notes,
        )
    p.artifact(raw.cached(SOURCE, lokalni.GB_COUNTIES), county_source)
    p.artifact(raw.cached(SOURCE, lokalni.GB_TOWNS), town_source)
    counties_file = json.loads(raw.cached(SOURCE, lokalni.GB_COUNTIES).path.read_bytes())
    counties = {
        lokalni.COUNTIES_BY_SHAPE[f["properties"]["shapeName"]]: lokalni._shape(f) for f in counties_file["features"]
    }
    early = [c for c in all_contests() if c.year < 2013]
    wanted: dict[str, set[str]] = {}
    for c in early:
        scheme, code = area_of(c)
        if scheme == "hr.opcina" and code != "21:zagreb":
            county, name = code.split(":")
            wanted.setdefault(name, set()).add(county)
    shapes: dict[str, shapely.Geometry] = {}
    for f in json.loads(raw.cached(SOURCE, lokalni.GB_TOWNS).path.read_bytes())["features"]:
        shape_name = f["properties"]["shapeName"]
        if shape_name.startswith("Otok "):
            continue
        folded = lokalni.place(re.sub(r"^(općina|grad)\s+", "", shape_name, flags=re.IGNORECASE))
        folded = lokalni.SHAPE_ALIASES.get(folded, folded)
        homes = wanted.get(folded)
        if not homes:
            continue
        shape = lokalni._shape(f)
        if len(homes) > 1:
            point = shapely.point_on_surface(shape)
            homes = {h for h in homes if counties[h].contains(point)}
            if len(homes) != 1:
                continue
        shapes.setdefault(f"{next(iter(homes))}:{folded}", shape)
    used = {area_of(c) for c in early}
    for scheme, code in sorted(used):
        if scheme == "hr.zupanija":
            shape = counties[code]
            source = county_source
        elif code == "21:zagreb":
            shape = counties["21"]
            source = county_source
        elif code in shapes:
            shape = shapes[code]
            source = town_source
        else:
            continue
        p.add(
            "area_geometry",
            area_id=area_id(scheme, code),
            valid_from=date(2003, 1, 1),
            valid_to=date(2013, 1, 1),
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=source,
        )
    return p


def unshaped() -> list[str]:
    """Units of the elections before 2013 that the open boundaries do not draw."""
    p = geo()
    drawn = {r["area_id"] for r in p.rows.get("area_geometry", [])}
    return sorted(
        {
            f"{c.county} {c.unit or 'county'}"
            for c in all_contests()
            if c.year < 2013 and area_id(*area_of(c)) not in drawn
        }
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The national minority councils and representatives of Croatia's counties, towns, and municipalities, their "
        "electoral systems, and the source of their election results",
    )
    p.external.add(CROATIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Državno izborno povjerenstvo Republike Hrvatske",
        title="Arhiva izbora: izbori za članove vijeća i predstavnike nacionalnih manjina, izabrani kandidati",
        url=f"{SITE}/",
        license="Official results of the Republic of Croatia, published for public use; reuse with the source "
        "acknowledged",
        license_url=f"{SITE}/",
        attribution="Državno izborno povjerenstvo Republike Hrvatske (DIP), izbori.hr",
    )
    for id, rules, text in (
        (system("council"), {"formula": "block"}, SYSTEM_COUNCIL),
        (system("representative"), {"formula": "fptp"}, SYSTEM_REPRESENTATIVE),
    ):
        p.add("electoral_system", id=id, polity_id=CROATIA, valid_from=date(2003, 1, 1), rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    seen: set[str] = set()
    for c in sorted(all_contests(), key=lambda c: -c.year):
        id = seat(c)
        if id in seen:
            continue
        seen.add(id)
        english, native, _ = names_of(c)
        if c.kind == "council":
            p.add("body", id=id, polity_id=CROATIA, kind="council")
        else:
            p.add("office", id=id, polity_id=CROATIA, kind="other")
        p.name(id, english, "en", "official")
        p.name(id, native, "hr", "native", False)
    return p


# One package per voting day.


def package_key(day: date) -> str:
    return f"{SOURCE}/{day}"


def days() -> list[tuple[int, int, date]]:
    out = [(y, 1, DAY[y]) for y in YEARS]
    out += [(y, 2, d) for y, d in RUNOFF.items()]
    return sorted(out, key=lambda x: x[2])


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(d) for _, _, d in days())]


def round_of(c: Contest, number: int) -> Round | None:
    return c.rounds[number - 1] if len(c.rounds) >= number else None


def verdict(c: Contest, r: Round) -> str:
    if not r.elected:
        return ""
    if r.note and "nije izabran" in r.note:
        return "tied"
    return "elected"


def election(year: int, number: int, day: date) -> Package:
    depends = [f"{izbori.SOURCE}/geo", f"{SOURCE}/registry"]
    if number > 1:
        depends.append(package_key(DAY[year]))
    english = f"{year} Croatian national minority council and representative elections"
    native = f"Izbori za članove vijeća i predstavnike nacionalnih manjina {year}."
    if number > 1:
        english += ", second round"
        native += ", drugi krug"
    p = Package(package_key(day), SOURCE, f"The {english} from the State Electoral Commission", depends)
    p.external.add(CROATIA)
    for county in COUNTIES:
        artifact = raw.cached(SOURCE, report_url(year, county))
        if number == 1:
            p.artifact(artifact, SOURCE_ID)
        else:
            p.uses(artifact)
    election_id = key("election", year, number)
    p.add(
        "election",
        id=election_id,
        polity_id=CROATIA,
        kind="local" if number == 1 else "runoff",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, english, "en", "official")
    p.name(election_id, native, "hr", "native", False)
    path = f"hr/{day}-minority" + ("" if number == 1 else "-runoff")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.add("note", id=key("note", election_id), entity_id=election_id, kind="methodology", body_md=METHOD, lang="en")
    columns: dict[str, list[object]] = {k: [] for k in ("result_set_id", "unit_id", "option_id", "measure", "value")}
    units: set[str] = set()
    for c in contests(year):
        r = round_of(c, number)
        if r is None:
            continue
        scheme, code = area_of(c)
        english_name, native_name, tail = names_of(c)
        contest_id = key("contest", year, number, *c.ident)
        result_set = key("result_set", year, number, *c.ident)
        council = c.kind == "council"
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="body_seats" if council else "office",
            office_id=None if council else seat(c),
            body_id=seat(c) if council else None,
            district_area_id=area_id(scheme, code),
            magnitude=(len(r.elected) or None) if council else 1,
            round_number=number,
            previous_round_id=key("contest", year, number - 1, *c.ident) if number > 1 else None,
            electoral_system_id=system("council" if council else "representative"),
            default_result_kind="counted",
        )
        p.name(contest_id, english_name, "en", "official")
        p.name(contest_id, native_name, "hr", "native", False)
        p.add("slug", path=f"{path}/{tail}", entity_id=contest_id, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        file = report_url(year, c.county).rsplit("/", 1)[1]
        where = place_names(c)[0]
        p.add(
            "citation",
            id=key("citation", result_set),
            entity_id=result_set,
            source_id=SOURCE_ID,
            locator={"file": file, "unit": where, "contest": c.kind, "minority": MINORITIES[c.minority][0]},
        )
        unit_code = f"{day}:{code}"
        unit = key("unit", unit_code)
        if unit not in units:
            units.add(unit)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CROATIA,
                scheme_code=scheme,
                code=unit_code,
                area_relation="equals",
                area_id=area_id(scheme, code),
                kind="aggregate",
            )
            p.name(unit, where, "hr", "official")
        for measure, value in (
            (REGISTERED, r.registered),
            (VOTERS, r.voters),
            (CAST, r.cast),
            (VALID, r.valid),
            (INVALID, r.invalid),
        ):
            if value is not None:
                for name, item in zip(columns, (result_set, unit, None, measure, value), strict=True):
                    columns[name].append(item)
        outcome = verdict(c, r)
        for rank, candidate in enumerate(r.elected, 1):
            option = key("option", year, number, *c.ident, rank)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="candidate",
                ballot_position=rank,
                ballot_label=candidate.name or UNREADABLE,
            )
            who = key("person", year, number, *c.ident, rank)
            name, born = person_of(candidate.name)
            p.add("person", id=who, birth_date=born, birth_precision="day" if born else None)
            if name:
                p.name(who, name, "hr", "official")
            p.add(
                "candidacy",
                id=key("candidacy", year, number, *c.ident, rank),
                person_id=who,
                option_id=option,
                role="head",
                list_position=rank,
                ballot_name=candidate.name or None,
            )
            if candidate.deputy:
                deputy = key("person", year, number, *c.ident, rank, "deputy")
                deputy_name, deputy_born = person_of(candidate.deputy)
                p.add(
                    "person",
                    id=deputy,
                    birth_date=deputy_born,
                    birth_precision="day" if deputy_born else None,
                )
                p.name(deputy, deputy_name or candidate.deputy, "hr", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", year, number, *c.ident, rank, "deputy"),
                    person_id=deputy,
                    option_id=option,
                    role="substitute",
                    ballot_name=candidate.deputy,
                )
            for name_, item in zip(columns, (result_set, unit, option, VOTES, candidate.votes), strict=True):
                columns[name_].append(item)
            p.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
    frame = pl.DataFrame(
        columns,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "value": pl.Int64,
        },
    ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))
    p.frame("result_value", frame)
    short, over = 0, []
    for c in contests(year):
        r = round_of(c, number)
        if r is None or c.kind != "representative" or r.valid is None:
            continue
        listed = sum(e.votes for e in r.elected)
        if listed < r.valid:
            short += 1
        elif listed > r.valid:
            over.append(f"{names_of(c)[0]} ({listed} votes listed, {r.valid} valid ballots)")
    if short or over:
        reason = (
            "The commission lists only the elected candidate of a representative contest, so the votes listed fall "
            f"short of the valid ballots in {short} contests where other candidates also received votes"
        )
        if over:
            reason += "; in " + "; ".join(over) + " the printed votes exceed the printed valid ballots"
        p.accept("accounting.votes_valid", reason + ".")
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for year, number, day in days():
        election(year, number, day).write()


def emit() -> None:
    package.run("load", packages())


# Checking the contests against the totals the reports print.

STATS = re.compile(r"(\d[\d.]*)\s+(\d[\d.]*)\s+[\d,]+\s?%\s+(\d[\d.]*)\s+[\d,]+\s?%\s+(\d[\d.]*)\s+[\d,]+\s?%\s*$")
TALLY = re.compile(r"^\s*Ukupno\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$")


def printed(year: int, county: str) -> tuple[list[int], list[int]] | None:
    """The report's own totals for a county: the contests it called and held, and its table of turnout figures."""
    path = raw.cached(SOURCE, report_url(year, county)).path
    lines = pdf_text(path, year)
    start = next((i for i, line in enumerate(lines) if "PODACI O UKUPNOM" in line), None)
    if start is None:
        return None
    figures = [0, 0, 0, 0]
    tally: list[int] = []
    for line in lines[start:]:
        found = STATS.search(line)
        if found:
            for i in range(4):
                figures[i] += int(found.group(i + 1).replace(".", ""))
        done = TALLY.match(line)
        if done:
            tally = [int(done.group(i)) for i in range(1, 5)]
    return tally, figures


def totals() -> list[str]:
    """Compare each county's contests with the totals its report prints, for the years whose reports print them."""
    out = []
    for year in YEARS:
        if year in (2003, 2023):
            continue
        shortfalls = []
        sums = [0, 0, 0, 0]
        held = [0, 0]
        printed_sums = [0, 0, 0, 0]
        printed_held = [0, 0]
        called = [0, 0]
        printed_called = [0, 0]
        for report in reports(year):
            found = printed(year, report.county)
            if found is None:
                continue
            tally, figures = found
            mine = [0, 0, 0, 0]
            kinds = {"council": [0, 0], "representative": [0, 0]}
            for c in report.contests:
                kinds[c.kind][0] += 1
                if c.rounds:
                    kinds[c.kind][1] += 1
                    r = c.rounds[0]
                    for i, v in enumerate((r.registered, r.voters, r.valid, r.invalid)):
                        mine[i] += v or 0
            for i in range(4):
                sums[i] += mine[i]
                printed_sums[i] += figures[i]
            if tally:
                for i, kind in enumerate(("council", "representative")):
                    called[i] += kinds[kind][0]
                    held[i] += kinds[kind][1]
                    printed_called[i] += tally[2 * i]
                    printed_held[i] += tally[2 * i + 1]
                if [
                    kinds["council"][0],
                    kinds["council"][1],
                    kinds["representative"][0],
                    kinds["representative"][1],
                ] != tally:
                    shortfalls.append(f"{report.county}: called/held {kinds} vs printed {tally}")
        out.append(
            f"{year}: registered, voters, valid, invalid {sums} vs printed {printed_sums}; "
            f"called {called} vs {printed_called}; held {held} vs {printed_held}"
        )
        out += [f"  {year} {line}" for line in shortfalls]
    return out


if __name__ == "__main__":
    print("\n".join(totals()))
