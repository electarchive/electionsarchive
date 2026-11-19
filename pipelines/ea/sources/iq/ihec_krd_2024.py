"""The 2024 Kurdistan Region Parliament election from Iraq's Independent High Electoral Commission (IHEC).

IHEC's page "Final results of the Kurdistan Region of Iraq Parliament election 2024" links, for each of the four
governorates that voted (Erbil, Sulaymaniyah, Duhok, Halabja), two PDF tables with a text layer:

- the "parties" table: the governorate's valid votes, its seats split by men, women, and minorities, and, for each
  entity that won a seat, its votes and seats. The entities are party lists, and the individual candidates who ran
  for the minority (Christian and Turkmen) quota seats;
- the "candidates" table: every candidate of every list, with the candidate's own votes, sex, number on the list, the
  list's votes, and a status of "winner" for the elected.

A four-page scan from the commission's certified results of November 2024 lists the 100 winners with their seat types;
it has no text layer and carries no votes, so it is cited and its seat counts are compared, not read.

The commission reports each governorate as a whole, which is the finest unit it publishes. Outlines come from
the UN OCHA common operational dataset for Iraq's administrative boundaries (CC BY-IGO, 2019). Its governorate layer
predates Halabja's separation from Sulaymaniyah, so Halabja's outline is the dataset's Halabcha district, which is
the governorate's territory, and Sulaymaniyah's is its governorate outline minus that district.
"""

import functools
import json
import re
import subprocess
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package

SOURCE = "iq.ihec_krd_2024"
PAGE = "https://ihec.iq/kurdistan-final-results/"
UPLOADS = "https://ihec.iq/wp-content/uploads/2024/10"
WINNERS = (
    "https://ihec.iq/wp-content/uploads/2024/11/%D8%A7%D9%84%D9%86%D8%AA%D8%A7%D8%A6%D8%AC-"
    "%D8%A7%D9%84%D9%85%D8%B5%D8%A7%D8%AF%D9%82-%D8%B9%D9%84%D9%8A%D9%87%D8%A7-%D9%84%D8%A7%D9%86%D8%AA%D8%AE%D8%A7"
    "%D8%A8%D8%A7%D8%AA-%D8%A8%D8%B1%D9%84%D9%85%D8%A7%D9%86-%D8%A5%D9%82%D9%84%D9%8A%D9%85-%D9%83%D9%88%D8%B1%D8%AF"
    "%D8%B3%D8%AA%D8%A7%D9%86-%D8%A7%D9%84%D8%B9%D8%B1%D8%A7%D9%82-2024.pdf"
)
OUTLINES = (
    "https://data.humdata.org/dataset/488bb3cd-3ce9-49d3-862a-3ce7975c63e1/resource/"
    "a9ed4d00-1182-4fdc-a0e4-82bbd20786db/download/irq_admin_boundaries.geojson.zip"
)
SHAPES = "iq.ocha_cod_ab"
IRAQ = str(uuid5(package.NAMESPACE, "polity\x1fIQ"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fIRQ"))
DAY = date(2024, 10, 20)
SCHEME = "iq.muhafaza"
VOTES, VALID, PREFERENCE = 1, 4, 7
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)
SHAPES_ID = key("source", SHAPES)
SYSTEM = key("electoral_system", "kurdistan_parliament")
BODY = key("body", "kurdistan_parliament")


@dataclass(frozen=True)
class Governorate:
    slug: str
    file: str  # the file stem on ihec.iq
    code: str  # ISO 3166-2
    seats: int
    en: str
    ar: str
    ckb: str


GOVERNORATES = (
    Governorate("erbil", "Erbil", "IQ-AR", 34, "Erbil", "أربيل", "هەولێر"),
    Governorate("sulaymaniyah", "Sulamania", "IQ-SU", 38, "Sulaymaniyah", "السليمانية", "سلێمانی"),
    Governorate("duhok", "Dohuk", "IQ-DA", 25, "Duhok", "دهوك", "دهۆک"),
    Governorate("halabja", "Halabja", "IQ-HA", 3, "Halabja", "حلبجة", "هەڵەبجە"),
)
# The OCHA governorate layer is keyed by its own place code. It has no Halabja, which is its Halabcha district
# (IQG06Q05) and lies inside Sulaymaniyah (IQG06).
PCODES = {"IQ-AR": "IQG11", "IQ-SU": "IQG06", "IQ-DA": "IQG09"}
HALABJA_DISTRICT = "IQG06Q05"


def pdf(g: Governorate, kind: str) -> str:
    return f"{UPLOADS}/{g.file}-{kind}.pdf"


URLS = [pdf(g, kind) for g in GOVERNORATES for kind in ("parties", "candidates")]

# The commission's text layer drops letters that Kurdish adds to the Arabic script (ڵ, ێ, ە and others), so a list's
# printed name is read back from the page image. Each entry is the list's number on the ballot, its name in Sorani
# Kurdish or Arabic as printed, and an English name where one is established.
PARTIES: dict[str, tuple[str, str, str | None]] = {
    "114": ("گۆڕان", "ckb", "Gorran Movement"),
    "123": ("بزووتنەوەی پەیوەندی ئیسلامیی کوردستان", "ckb", None),
    "129": ("یەکێتیی نیشتمانیی کوردستان", "ckb", "Patriotic Union of Kurdistan"),
    "133": ("هاوپەیمانی هەرێمی کوردستان", "ckb", None),
    "147": ("کۆمەڵی دادگەری کوردستان/عێراق", "ckb", "Kurdistan Justice Group"),
    "148": ("یەکگرتووی ئیسلامیی کوردستان", "ckb", "Kurdistan Islamic Union"),
    "149": ("الجبهة التركمانية العراقية", "ar", "Iraqi Turkmen Front"),
    "157": ("نەوەی نوێ", "ckb", "New Generation Movement"),
    "161": ("هەڵوێست", "ckb", "Halwest"),
    "162": ("هاوپەیمانی نیشتمانی", "ckb", None),
    "164": ("سەردەم", "ckb", None),
    "169": ("بەرەی گەل", "ckb", "People's Front"),
    "190": ("پارتی دیموکراتی کوردستان", "ckb", "Kurdistan Democratic Party"),
    "198": ("الحركة الاسلامية في كوردستان العراق", "ar", "Islamic Movement in Kurdistan"),
}
COMPONENTS = {"مسيحي": "Christian", "تركماني": "Turkmen"}

RULES = {"formula": "sainte_lague", "lists": {"order": "open", "vacancy": "next_by_votes"}}
SYSTEM_NOTE = (
    "The Kurdistan Region's Parliament has 100 seats, elected on one day in four multi-member governorate "
    "constituencies: Erbil 34, Sulaymaniyah 38, Duhok 25, and Halabja 3. Voters choose a list and may mark one "
    "candidate on it. Five of the seats are reserved for minorities (Christian and Turkmen candidates, who stand as "
    "individuals) and the seats are also split between men and women as each governorate's table prints. Seats are "
    "stored as the commission declared them. Dividing the votes of the lists that won seats by the Sainte-Laguë "
    "divisors reproduces the declared general seats in Sulaymaniyah and Halabja, and in Erbil and Duhok it gives one "
    "seat more to the Kurdistan Democratic Party and one less to a smaller list; the tables do not say why."
)
METHOD = (
    "Votes by governorate from the commission's two PDF tables for this governorate. A list's votes are the figure "
    "printed beside each of its candidates and in the party table (the two agree for every list); they include the "
    "votes cast for the list as a whole, so the candidates' own votes, stored as preference votes, add up to less. "
    "The governorate's valid votes are the printed total of valid votes. The entities the commission itemizes, the lists "
    "of its candidate table and the minority-seat candidates that won, add up to less than that total (by 13,366 in "
    "Erbil, 7,399 in Sulaymaniyah, 6,282 in Duhok, and 19 in Halabja), and the commission does not say where the rest "
    "went; both figures are stored as printed. Winners are the candidates printed as winners, "
    "plus the minority candidates who won, which the party table names. The candidates' names are read from the text "
    "layer of the PDFs, which omits some Kurdish letters."
)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def _pdf(path: Path) -> None:
    if not path.read_bytes().startswith(b"%PDF"):
        raise ValueError(f"{path.name} is not a PDF")


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for url in [*URLS, WINNERS]:
        raw.fetch(SOURCE, url, suffix=".pdf", check=_pdf)
    raw.fetch(SHAPES, OUTLINES, suffix=".zip")


def parse() -> None:
    geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())


# Reading the tables.


def clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", text)
    return " ".join(text.split())


def table(url: str) -> list[list[str]]:
    """The rows of a PDF's text layer, each split into its columns."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows = []
    for line in unicodedata.normalize("NFKC", text).split("\n"):
        line = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069\x0c]", "", line).strip()
        if line:
            rows.append(re.split(r"\s{2,}", line))
    return rows


@dataclass
class Entity:
    number: str
    name: str  # as printed in the party table
    votes: int
    seats: int
    component: str | None  # for a minority-seat candidate


@dataclass
class Summary:
    valid: int
    men: int
    women: int
    minorities: int
    seats: int
    entities: list[Entity]


@functools.cache
def summary(g: Governorate) -> Summary:
    rows = table(pdf(g, "parties"))
    head: dict[str, int] = {}
    entities: list[Entity] = []
    labels = {
        "المجموع الكلي للاصوات الصحيحة": "valid",
        "عدد مقاعد الرجال": "men",
        "عدد مقاعد النساء": "women",
        "عدد مقاعد الأقليات": "minorities",
        "عدد المقاعد الكلي": "seats",
    }
    for row in rows:
        if len(row) == 2 and row[1] in labels:
            head[labels[row[1]]] = int(row[0])
        elif len(row) == 5 and all(row[i].isdigit() for i in (0, 1, 3, 4)):
            name = row[2]
            found = re.search(r"\)(" + "|".join(COMPONENTS) + r")\(", name)
            entities.append(
                Entity(
                    row[3],
                    name,
                    int(row[1]),
                    int(row[0]),
                    COMPONENTS[found.group(1)] if found else None,
                )
            )
    if set(head) != set(labels.values()) or not entities:
        raise ValueError(f"{g.slug}: the party table's header or rows were not read")
    summary_ = Summary(entities=entities, **head)
    if sum(e.seats for e in entities) != summary_.seats or summary_.seats != g.seats:
        raise ValueError(f"{g.slug}: the seats of the party table do not add up to {g.seats}")
    if summary_.men + summary_.women + summary_.minorities != summary_.seats:
        raise ValueError(f"{g.slug}: men, women, and minority seats do not add up to the total")
    return summary_


@dataclass(frozen=True)
class Candidate:
    number: int  # the candidate's number on the list
    name: str
    sex: str | None
    votes: int
    winner: bool


@dataclass
class Listing:
    name: str  # as printed
    votes: int
    candidates: list[Candidate]


@functools.cache
def listings(g: Governorate) -> dict[str, Listing]:
    """The candidate table by list number."""
    found: dict[str, Listing] = {}
    for row in table(pdf(g, "candidates")):
        if "الحالة" in row[0] or re.fullmatch(r"\d+ ?- ?\d+", row[0]):
            continue
        winner = row[0] == "فائز"
        if winner:
            row = row[1:]
        votes, seat, sex = row[:3]
        gov, number, name, list_votes, cno = row[-1], row[-2], row[-3], row[-4], row[-5]
        person = " ".join(row[3:-5])
        if (
            seat != "عام"
            or sex not in ("ذكر", "انثى")
            or not all(x.isdigit() for x in (votes, number, list_votes, cno))
        ):
            raise ValueError(f"{g.slug}: unexpected candidate row {row}")
        if gov != clean(g.ar) and not (g.slug == "erbil" and gov == "أربيل"):
            raise ValueError(f"{g.slug}: row for governorate {gov}")
        listing = found.setdefault(number, Listing(name, int(list_votes), []))
        if listing.votes != int(list_votes):
            raise ValueError(f"{g.slug}: list {number} prints two vote totals")
        listing.candidates.append(
            Candidate(int(cno), person, {"ذكر": "male", "انثى": "female"}[sex], int(votes), winner)
        )
    for number, listing in found.items():
        numbers = [c.number for c in listing.candidates]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"{g.slug}: list {number} repeats a candidate number")
        if sum(c.votes for c in listing.candidates) > listing.votes:
            raise ValueError(f"{g.slug}: list {number}'s candidates have more votes than the list")
    return found


def entities(g: Governorate) -> list[tuple[Entity, Listing | None]]:
    """The party table's entities with their candidate lists, then the lists without a seat, all in ballot order."""
    seated = {e.number: e for e in summary(g).entities}
    found = listings(g)
    unknown = (set(found) | {n for n, e in seated.items() if e.component is None}) - set(PARTIES)
    if unknown:
        raise ValueError(f"{g.slug}: lists {sorted(unknown)} have no curated name")
    out: list[tuple[Entity, Listing | None]] = []
    for number in sorted({*found, *seated}, key=int):
        listing = found.get(number)
        entity = seated.get(number) or Entity(
            number, listing.name if listing else "", listing.votes if listing else 0, 0, None
        )
        if listing and entity.votes != listing.votes:
            raise ValueError(
                f"{g.slug}: list {number} has {listing.votes} votes in one table and {entity.votes} in the other"
            )
        out.append((entity, listing))
    return out


def compare() -> None:
    """The party table's seats against the winners printed in the candidate table, and the national totals."""
    total, seats = 0, 0
    for g in GOVERNORATES:
        s = summary(g)
        winners = Counter({n: sum(c.winner for c in x.candidates) for n, x in listings(g).items()})
        for e in s.entities:
            if e.component is None and winners[e.number] != e.seats:
                raise ValueError(f"{g.slug}: list {e.number} has {e.seats} seats but {winners[e.number]} winners")
        if sum(winners.values()) + sum(1 for e in s.entities if e.component) != s.seats:
            raise ValueError(f"{g.slug}: winners do not add up to {s.seats} seats")
        listed = sum(e.votes for e, _ in entities(g))
        print(f"{g.slug}: {s.valid} valid votes, {listed} on the {len(entities(g))} entities printed, {s.seats} seats")
        total += s.valid
        seats += s.seats
    if seats != 100:
        raise ValueError(f"{seats} seats, expected 100")
    print(f"national: {total} valid votes, {seats} seats", flush=True)


# Places.


def area(g: Governorate) -> str:
    return key("area", SCHEME, g.code)


def _layer(archive: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    return json.loads(archive.read(name))["features"]


@functools.cache
def outlines() -> dict[str, shapely.Geometry]:
    """Outlines by ISO 3166-2 code: Halabja is the Halabcha district and Sulaymaniyah is what is left around it."""
    with zipfile.ZipFile(raw.cached(SHAPES, OUTLINES).path) as archive:
        governorates = {
            f["properties"]["adm1_pcode"]: shapely.make_valid(shapely.geometry.shape(f["geometry"]))
            for f in _layer(archive, "irq_admin1.geojson")
        }
        districts = {
            f["properties"]["adm2_pcode"]: shapely.make_valid(shapely.geometry.shape(f["geometry"]))
            for f in _layer(archive, "irq_admin2.geojson")
        }
    halabja = districts[HALABJA_DISTRICT]
    if not governorates[PCODES["IQ-SU"]].buffer(1e-6).covers(halabja):
        raise ValueError("the Halabcha district is not inside Sulaymaniyah")
    found = {code: governorates[pcode] for code, pcode in PCODES.items()}
    found["IQ-SU"] = shapely.difference(found["IQ-SU"], halabja)
    found["IQ-HA"] = halabja
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The four governorates of the Kurdistan Region of Iraq, with outlines from the OCHA common operational dataset",
    )
    p.external |= {IRAQ, COUNTRY}
    shapes = raw.cached(SHAPES, OUTLINES)
    p.add(
        "source",
        id=SHAPES_ID,
        kind="academic",
        publisher="UN OCHA",
        title="Iraq administrative boundaries, COD-AB (governorates and districts, 2019), from the Humanitarian Data Exchange",
        url="https://data.humdata.org/dataset/cod-ab-irq",
        license="CC BY-IGO",
        license_url="https://creativecommons.org/licenses/by/3.0/igo/",
        attribution="UN OCHA, Iraq COD-AB (Central Statistical Organization and Kurdistan Region Statistics Office)",
    )
    p.artifact(shapes, SHAPES_ID)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=IRAQ,
        kind="administrative",
        rank=1,
        noun="governorate",
        code_system="iso_3166_2",
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME, 2019),
        scheme_code=SCHEME,
        vintage="2019",
        as_of=shapes.retrieved_at.date(),
    )
    for g in GOVERNORATES:
        id = area(g)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=g.code)
        p.add("area_kind", area_id=id, kind="governorate", source_id=SHAPES_ID)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, g.en, "en", "english")
        p.name(id, g.ar, "ar", "official", False)
        p.name(id, g.ckb, "ckb", "native", False)
        shape = shapely.make_valid(outlines()[g.code])
        parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
        shape = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=SHAPES_ID,
        )
    p.add(
        "note",
        id=key("note", area(GOVERNORATES[3]), "outline"),
        entity_id=area(GOVERNORATES[3]),
        kind="caveat",
        body_md="The outline is the Halabcha district of the OCHA Iraq boundaries, which is Halabja governorate's "
        "territory: its governorate layer dates from before Halabja separated from Sulaymaniyah in 2014. "
        "Sulaymaniyah's outline here is that layer's Sulaymaniyah with the district removed.",
        lang="en",
    )
    return p


# Parties, the parliament, and its electoral system.


def party(number: str) -> str:
    return key("party", number)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Kurdistan Region Parliament, its electoral system, and the lists on the 2024 ballots",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {IRAQ}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="المفوضية العليا المستقلة للانتخابات (Independent High Electoral Commission of Iraq)",
        title="Final results of the 2024 Kurdistan Region of Iraq Parliament election",
        url=PAGE,
        license="Official election results of Iraq, published for public use",
        license_url=PAGE,
        attribution="Independent High Electoral Commission of Iraq (IHEC), ihec.iq",
    )
    p.artifact(raw.cached(SOURCE, PAGE), SOURCE_ID)
    p.add("body", id=BODY, polity_id=IRAQ, kind="unicameral")
    p.name(BODY, "Kurdistan Region Parliament", "en", "official")
    p.name(BODY, "برلمان إقليم كوردستان العراق", "ar", "native", False)
    p.name(BODY, "پەرلەمانی کوردستانی عێراق", "ckb", "native", False)
    p.add("electoral_system", id=SYSTEM, polity_id=IRAQ, valid_from=DAY, valid_to=None, rules=RULES, rules_version=1)
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    for number, (name, lang, english) in sorted(PARTIES.items(), key=lambda item: int(item[0])):
        id = party(number)
        p.add("party", id=id, kind="party", polity_id=IRAQ)
        p.name(id, name, lang, "official")
        if english:
            p.name(id, english, "en", "english", False)
    return p


# The election.


def election() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The 2024 Kurdistan Region Parliament election by governorate, with every candidate's votes, from the "
        "Independent High Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {IRAQ}
    for g in GOVERNORATES:
        for kind in ("parties", "candidates"):
            p.artifact(raw.cached(SOURCE, pdf(g, kind)), SOURCE_ID)
    p.artifact(raw.cached(SOURCE, WINNERS), SOURCE_ID)
    p.uses(raw.cached(SOURCE, PAGE))
    compare()
    election_id = key("election", DAY)
    p.add(
        "election",
        id=election_id,
        polity_id=IRAQ,
        kind="general",
        date_from=DAY,
        date_to=DAY,
        status="held",
    )
    p.name(election_id, "2024 Kurdistan Region parliamentary election", "en", "official")
    p.name(election_id, "انتخابات برلمان إقليم كوردستان العراق 2024", "ar", "native", False)
    p.name(election_id, "هەڵبژاردنی پەرلەمانی کوردستانی عێراق 2024", "ckb", "native", False)
    path = f"iq/{DAY}-kurdistan-parliament"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    rows: list[tuple[str, str, str | None, int, int]] = []
    for g in GOVERNORATES:
        s = summary(g)
        contest = key("contest", DAY, g.slug)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=BODY,
            district_area_id=area(g),
            magnitude=g.seats,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"Kurdistan Region Parliament, {g.en}", "en", "official")
        p.name(contest, f"برلمان إقليم كوردستان العراق، محافظة {g.ar}", "ar", "native", False)
        p.name(contest, f"پەرلەمانی کوردستان، {g.ckb}", "ckb", "native", False)
        p.add("slug", path=f"{path}/{g.slug}", entity_id=contest, is_current=True)
        rs = key("result_set", DAY, g.slug)
        p.add(
            "result_set",
            id=rs,
            election_id=election_id,
            contest_id=contest,
            stage="certified",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
        unit = key("unit", DAY, g.slug)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=IRAQ,
            scheme_code=SCHEME,
            code=f"{DAY}:{g.code}",
            area_relation="equals",
            area_id=area(g),
            kind="standard",
        )
        p.name(unit, g.en, "en", "english")
        rows.append((rs, unit, None, VALID, s.valid))
        p.add("declared", result_set_id=rs, option_id=None, measure=VALID, value=s.valid)
        for entity, listing in entities(g):
            option = key("option", DAY, g.slug, entity.number)
            if entity.component:
                minority(p, g, contest, rs, option, entity)
                rows.append((rs, unit, option, VOTES, entity.votes))
                p.add("declared", result_set_id=rs, option_id=option, measure=VOTES, value=entity.votes)
                continue
            name, _, _ = PARTIES[entity.number]
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(entity.number),
                ballot_position=int(entity.number),
                ballot_label=name,
            )
            rows.append((rs, unit, option, VOTES, entity.votes))
            p.add("declared", result_set_id=rs, option_id=option, measure=VOTES, value=entity.votes)
            p.add(
                "outcome",
                result_set_id=rs,
                option_id=option,
                kind="won" if entity.seats else "lost",
                seats=entity.seats,
            )
            assert listing is not None
            for c in sorted(listing.candidates, key=lambda c: c.number):
                tag = (DAY, g.slug, entity.number, c.number)
                child = key("option", *tag)
                who = key("person", *tag)
                p.add(
                    "contest_option",
                    id=child,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=option,
                    party_id=party(entity.number),
                    ballot_position=c.number,
                    ballot_label=c.name,
                )
                p.add("person", id=who, gender=c.sex)
                p.name(who, c.name, "ar", "official")
                candidacy = key("candidacy", *tag)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=who,
                    option_id=option,
                    role="list_member",
                    list_position=c.number,
                    ballot_name=c.name,
                    member_of_party_id=party(entity.number),
                    is_independent=False,
                )
                rows.append((rs, unit, child, PREFERENCE, c.votes))
                if c.winner:
                    p.add("outcome", result_set_id=rs, option_id=option, candidacy_id=candidacy, kind="elected")
    frame = pl.DataFrame(
        rows,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "value": pl.Int64,
        },
        orient="row",
    ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))
    p.frame("result_value", frame)
    p.accept(
        "accounting.votes_valid",
        "The commission prints each governorate's valid votes, and the entities it itemizes add up to less (by "
        "13,366 in Erbil, 7,399 in Sulaymaniyah, 6,282 in Duhok, and 19 in Halabja); the difference is on no printed "
        "line, and the source's own figures cause it.",
    )
    return p


def minority(p: Package, g: Governorate, contest: str, rs: str, option: str, entity: Entity) -> None:
    name = clean(re.sub(r"\)[^)(]*\(", "", entity.name))
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="candidate",
        ballot_position=int(entity.number),
        ballot_label=name,
    )
    who = key("person", DAY, g.slug, entity.number)
    p.add("person", id=who)
    p.name(who, name, "ar", "official")
    p.add(
        "candidacy",
        id=key("candidacy", DAY, g.slug, entity.number),
        person_id=who,
        option_id=option,
        role="head",
        ballot_name=name,
        is_independent=True,
    )
    p.add("outcome", result_set_id=rs, option_id=option, kind="elected", seats=entity.seats)
    p.add(
        "note",
        id=key("note", option),
        entity_id=option,
        kind="context",
        body_md=f"Elected to a seat reserved for the {entity.component} minority; the candidate stood as an individual.",
        lang="en",
    )
