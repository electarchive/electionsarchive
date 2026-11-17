"""Bosnia and Herzegovina's 4 October 2026 general election from the Central Election Commission's results site.

The site (rezultati.izbori.ba) serves its certified results as JSON files: the lists of contests, parties, and regions,
and for every electoral district a file of its own totals and a file for each municipality and for the ballots counted
centrally (postal, absentee, mobile, and tendered). The same results come as one CSV, which also carries the seats
and the candidates elected. The site publishes polling station results as a further file for each station; those
answer the archive with a refusal, so this module takes each contest down to its municipalities.
"""

import csv
import functools
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid5

import polars as pl
import shapely

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ba import cik_1996_2000 as shapes

SOURCE = "ba.cik_general_2026"
BIH = str(uuid5(package.NAMESPACE, "polity\x1fBA"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBIH"))
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
_IDS = Package(SOURCE, SOURCE, "ids")
DAY = date(2026, 10, 4)
SLUG = "2026-10-04"
ENTITY, MUNICIPALITY, DISTRICT = "ba.entitet_2026", "ba.opcina_2026", "ba.izborna_jedinica_2026"
STATIONS = "ba.station"  # the polling station scheme the Commission's other results already use


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


SOURCE_ID = key("source", SOURCE)
SHAPES_ID = key("source", shapes.SHAPES)
DATA = "https://rezultati.izbori.ba/assets/data/"
TOTALS = f"{DATA}totalized_results/"
CSV_URL = f"{TOTALS}reports/rezultati_bih_2026.csv"
REFERENCE = ("contests.json", "parties.json", "regions.json", "last-update.json")
ENTITY_NAMES = {"1": "Federation of Bosnia and Herzegovina", "2": "Republika Srpska"}
ENTITY_BS = {"1": "Federacija Bosne i Hercegovine", "2": "Republika Srpska"}
RACES = {
    "1": ("Presidency of Bosnia and Herzegovina", "Predsjedništvo Bosne i Hercegovine"),
    "2": (
        "House of Representatives of the Parliamentary Assembly of Bosnia and Herzegovina",
        "Predstavnički dom Parlamentarne skupštine Bosne i Hercegovine",
    ),
    "4": (
        "House of Representatives of the Parliament of the Federation of Bosnia and Herzegovina",
        "Predstavnički dom Parlamenta Federacije Bosne i Hercegovine",
    ),
    "5": ("President of Republika Srpska", "Predsjednik Republike Srpske"),
    "6": ("National Assembly of Republika Srpska", "Narodna skupština Republike Srpske"),
    "7": ("Cantonal assembly", "Skupština kantona"),
}
CANDIDATE_RACES = {"1", "5"}
INDEPENDENT = re.compile(r"(NEOVISNI|NEZAVISNI) KANDIDAT$")
MOSTAR, BRCKO = "199", "200"
OUTLINE_NAMES = {  # the geoBoundaries outline of each municipality whose name differs, by the Commission's code
    "006": "Kupra na Uni",
    "007": "Novi Grad@west",
    "036": "Doboj East",
    "107": "Kupres (BiH)",
    "108": "Kupres",
    "133": "Novo Sarajevo",
    "136": "Centar",
    "137": "Stari Grad",
    "141": "Trnovo (BiH)",
    "142": "Trnovo (RS)",
    "143": "Pale-Prača",
    "165": "Foča-Ustikolina",
    "168": "Ustiprača",
    "199": "Mostar",
    "200": "Brcko District",
}
NO_OUTLINE = {"040": "Stanari", "147": "Višegrad"}  # municipalities newer than the 2013 outlines, or missing from them
COUNT_METHOD = (
    "Votes by municipality as the Central Election Commission's results site publishes them in its certified "
    "results. Each district also has a unit for the postal, absentee, mobile, and tendered ballots, which the "
    "Commission counts centrally and prints for the whole district. The district totals the Commission prints are "
    "stored as declared."
)
SEATS_NOTE = (
    " The seats are the district seats the Commission prints with the district's results; the compensation seats "
    "that the electoral law allocates over the whole entity are not in the data the site publishes."
)


def body_of(cc: str) -> str:
    """The race number the archive's other Bosnian results use for the body a district elects."""
    return {"2": "7", "3": "6", "4": "4", "5": "2"}.get(cc[0], "") if cc not in {"600", "701702", "703"} else ""


def race_of(cc: str) -> str:
    if cc == "600":
        return "5"
    if cc in {"701702", "703"}:
        return "1"
    return body_of(cc)


# Fetching.


def reference_url(name: str) -> str:
    return f"{DATA}{name}"


def contest_url(cc: str) -> str:
    return f"{DATA}contests/{cc}.json"


def district_url(cc: str) -> str:
    return f"{TOTALS}districts/{cc}.json"


def region_url(code: str) -> str:
    return f"{TOTALS}regions/0/{code}.json"


def load(url: str) -> Any:
    return json.loads(raw.cached(SOURCE, url).path.read_bytes())


@functools.cache
def regions() -> list[dict[str, Any]]:
    return load(reference_url("regions.json"))


@functools.cache
def districts() -> list[str]:
    return [str(c) for c in sorted({cs for x in regions() if x["l"] == 1 for cs in x["cs"]}, key=int)]


def unit_codes() -> list[str]:
    """The region files below the districts: each municipality and each district's centrally counted ballots."""
    return [x["c"] for x in regions() if x["l"] == 4 and re.fullmatch(r"\d+\.(\d{3}|SCB\d+)", x["c"])]


def fetch() -> None:
    for name in REFERENCE:
        raw.fetch(SOURCE, reference_url(name), suffix=".json")
    raw.fetch(SOURCE, CSV_URL, suffix=".csv")
    for url in shapes.OUTLINES.values():
        raw.fetch(shapes.SHAPES, url, suffix=".geojson")
    urls = [contest_url(cc) for cc in districts()] + [district_url(cc) for cc in districts()]
    urls += [region_url(x["c"]) for x in regions() if x["l"] == 1]
    urls += [region_url(code) for code in unit_codes()]
    got = raw.fetch_many(SOURCE, urls, suffix=".json")
    missing = [u for u in urls if u not in got]
    if missing:
        raise SystemExit(f"{SOURCE}: {len(missing)} files could not be fetched, such as {missing[0]}")
    print(f"{SOURCE}: {len(got):,} files", flush=True)


# Reading the results.


@dataclass
class Option:
    label: str
    pc: str  # the party's or candidate's certification number
    position: int
    votes: int
    seats: int = 0
    elected: bool = False
    person: str | None = None  # the candidate's number in the Commission's register, for races of candidates
    name: str | None = None
    nationality: str | None = None


@dataclass
class Contest:
    cc: str
    name: str  # the Commission's title for the district
    options: list[Option]
    ballots: int
    invalid: int
    valid: int
    registered: int
    units: dict[str, dict[str, Any]]  # the region files below the district, by the code after the district's


def clean(text: str) -> str:
    return " ".join(str(text).split())


def mapping_of(row: dict[str, str]) -> str:
    """The district code of one CSV row, from the contest's title, its electoral unit, and its valid ballots."""
    contest, unit = row["Contest Name"], row["Electoral Unit"].split(" / ")[0]
    number = re.search(r"(\d+)([AB]?)$", unit)
    if contest.startswith("PREDSJEDNIK I POTPREDSJEDNICI"):
        return "600"
    if contest.startswith("PREDSJEDNIŠTVO"):
        return "701702" if row["Total Valid"] == FBIH_PRESIDENCY_VALID else "703"
    assert number is not None, contest
    n, letter = int(number.group(1)), number.group(2)
    if contest.startswith("SKUPŠTINE KANTONA"):
        return str(200 + n)
    if contest.startswith("NARODNA SKUPŠTINA"):
        return str(300 + n)
    if contest.startswith("ZASTUPNIČKI/PREDSTAVNIČKI DOM PARLAMENTA FED"):
        return str(400 + n)
    return str((510 if letter == "A" else 520) + n)


FBIH_PRESIDENCY_VALID = "839168"


@functools.cache
def csv_rows() -> dict[str, list[dict[str, str]]]:
    text = raw.cached(SOURCE, CSV_URL).path.read_text(encoding="utf-8-sig")
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in csv.DictReader(text.splitlines()):
        out[mapping_of(row)].append(row)
    return out


def counts(file: dict[str, Any]) -> dict[int, int]:
    """The accounting a region file prints: its valid and invalid ballots, ballots cast, and registered voters."""
    ct = {x["cn"]: x["cv"] for x in file["ct"]}
    st = file["st"][0]
    valid, invalid = int(ct["total-valid-votes"]), int(ct["total-invalid-votes"])
    return {VALID: valid, INVALID: invalid, BALLOTS: int(st["v_rec"]), REGISTERED: int(st["v_exp"])}


@functools.cache
def contests() -> dict[str, Contest]:
    parties = {x["pc"]: x for x in load(reference_url("parties.json"))}
    out: dict[str, Contest] = {}
    for cc in districts():
        meta, district = load(contest_url(cc)), load(district_url(cc))
        rows = csv_rows()[cc]
        by_party: dict[str, dict[str, str]] = {}
        for row in rows:
            if row["Row Type"] == ("CANDIDATE" if race_of(cc) in CANDIDATE_RACES else "LIST"):
                by_party[row["Party ID"]] = row
        printed = {x["pc"]: x["v"] for x in district["rs"]}
        options = []
        for b in sorted(meta["bos"], key=lambda b: b["to"]):
            row = by_party[b["pc"]]
            votes = int(row["Candidate Votes" if race_of(cc) in CANDIDATE_RACES else "List Votes"])
            if votes != printed.get(b["pc"], 0):
                raise ValueError(
                    f"{cc} {b['pc']}: {votes} votes in the CSV, {printed.get(b['pc'])} in the district file"
                )
            if race_of(cc) in CANDIDATE_RACES:
                cp = b["cp"]
                options.append(
                    Option(
                        clean(row["Candidate Name"]),
                        b["pc"],
                        b["to"],
                        votes,
                        elected=row["Elected"] == "YES",
                        person=str(cp["cid"]),
                        name=clean(b["bon"]),
                        nationality=cp.get("nat") or None,
                    )
                )
            else:
                options.append(
                    Option(
                        clean(b["bon"] or parties[b["pc"]]["pn"]),
                        b["pc"],
                        b["to"],
                        votes,
                        seats=int(row["List Mandates"] or 0),
                    )
                )
        acc = counts(district)
        first = rows[0]
        if (int(first["Processed Ballots"]), int(first["Total Invalid"]), int(first["Total Valid"])) != (
            acc[BALLOTS],
            acc[INVALID],
            acc[VALID],
        ):
            raise ValueError(f"{cc}: the CSV's ballots differ from the district file's")
        units = {
            x["c"].split(".", 1)[1]: load(region_url(x["c"]))
            for x in regions()
            if x["l"] == 4 and x["c"].startswith(f"{cc}.") and re.fullmatch(r"\d+\.(\d{3}|SCB\d+)", x["c"])
        }
        title = next(x["n"] for x in regions() if x["l"] == 3 and x["c"] == cc)
        out[cc] = Contest(cc, clean(title), options, acc[BALLOTS], acc[INVALID], acc[VALID], acc[REGISTERED], units)
    return out


def proper(text: str) -> str:
    """A name in title case from the Commission's capitals, keeping words it already mixes ("BiH")."""
    words = []
    for i, word in enumerate(clean(text).split(" ")):
        if any(ch.islower() or ch.isdigit() for ch in word):
            words.append(word)
        elif i and word in {"NA", "I", "U", "POD", "KOD"}:
            words.append(word.lower())
        else:
            words.append("-".join(part[:1] + part[1:].lower() for part in word.split("-")))
    return " ".join(words)


@functools.cache
def municipality_names() -> dict[str, str]:
    out: dict[str, str] = {}
    for x in regions():
        if x["l"] == 4 and re.fullmatch(r"\d+\.\d{3}", x["c"]):
            out[x["cc"]] = proper(re.sub(r"\s*\(\d+\)$", "", x["n"]))
    return dict(sorted(out.items()))


@functools.cache
def entity_of() -> dict[str, str]:
    """Each municipality's entity, from the districts it votes in; Brčko District, in both, has none."""
    seen: dict[str, set[str]] = defaultdict(set)
    for cc, c in contests().items():
        side = "1" if cc[0] in "24" or cc in {"511", "512", "513", "514", "515", "701702"} else "2"
        for code in c.units:
            if not code.startswith("SCB"):
                seen[code].add(side)
    return {code: next(iter(sides)) for code, sides in seen.items() if len(sides) == 1}


# Places.


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def entity_area(entity: str) -> str:
    return area(ENTITY, entity)


def district_area(cc: str) -> str:
    """The area a contest's district covers: its own electoral district, or the entity for the offices elected there."""
    if cc in {"600", "703"}:
        return entity_area("2")
    if cc == "701702":
        return entity_area("1")
    return area(DISTRICT, cc)


def district_entity(cc: str) -> str:
    return "1" if cc[0] in "24" or cc in {"511", "512", "513", "514", "515"} else "2"


def district_name(cc: str) -> tuple[str, str]:
    """A district's English and Bosnian names: "Canton 3" and "Kanton 3", "Electoral unit 2A" and its Bosnian form."""
    c = contests()[cc]
    bs = proper(re.sub(r"\s*\(\d+\)$", "", c.name))
    n = bs.split(" ")[-1]
    en = f"Canton {n}" if bs.startswith("Kanton") else f"Electoral unit {n}"
    return f"{en} ({RACES[body_of(cc)][0]})", f"{bs} ({RACES[body_of(cc)][1]})"


def outline_for(code: str, name: str, by_name: dict[str, list[shapely.Geometry]]) -> shapely.Geometry | None:
    if code in NO_OUTLINE:
        return None
    wanted, _, side = OUTLINE_NAMES.get(code, name).partition("@")
    found = by_name.get(shapes.fold_latin(wanted), [])
    if side:
        found = sorted(found, key=lambda g: shapely.centroid(g).x)[:1]
    return found[0] if len(found) == 1 else None


def source_rows(p: Package, *, outlines: bool = False) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Centralna izborna komisija Bosne i Hercegovine",
        title="Opći izbori 2026, rezultati (results site)",
        url="https://rezultati.izbori.ba/",
        license="Official election results of Bosnia and Herzegovina, published for public use",
        license_url="https://www.izbori.ba/",
        attribution="Central Election Commission of Bosnia and Herzegovina, izbori.ba",
    )
    if outlines:
        p.add(
            "source",
            id=SHAPES_ID,
            kind="academic",
            publisher="geoBoundaries",
            title="geoBoundaries gbOpen Bosnia and Herzegovina ADM1 and ADM3 (simplified, 2013), from Wikimedia Commons",
            url="https://www.geoboundaries.org/",
            license="Public domain",
            license_url="https://www.geoboundaries.org/",
            attribution="geoBoundaries (William & Mary geoLab); original data from Wikimedia Commons",
        )


def geometry(p: Package, id: str, shape: shapely.Geometry) -> None:
    shape = shapes.multipolygon(shape)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=DAY,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="academic",
        source_id=SHAPES_ID,
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bosnia and Herzegovina's entities, municipalities, and electoral districts as the Central Election "
        "Commission's 2026 general election results name them, with outlines from geoBoundaries",
    )
    p.external |= {BIH, COUNTRY}
    source_rows(p, outlines=True)
    for url in shapes.OUTLINES.values():
        p.artifact(raw.cached(shapes.SHAPES, url), SHAPES_ID)
    for code, rank, kind, noun in (
        (ENTITY, 1, "administrative", "entity"),
        (MUNICIPALITY, 2, "administrative", "municipality"),
        (DISTRICT, 2, "electoral_district", "electoral district"),
    ):
        p.add("scheme", code=code, polity_id=BIH, kind=kind, rank=rank, noun=noun, code_system=None)
        p.add(
            "boundary_set",
            id=key("boundary_set", code, "2026"),
            scheme_code=code,
            vintage="2026",
            valid_from=DAY,
            as_of=DAY,
        )
    entity_shapes = dict(shapes.outlines("ADM1"))
    for code, text in ENTITY_NAMES.items():
        id = entity_area(code)
        p.add("area", id=id, scheme_code=ENTITY)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, text, "en", "official")
        p.name(id, ENTITY_BS[code], "bs", "native", False)
        geometry(p, id, entity_shapes[shapes.ENTITY_OUTLINES[code]])
    by_name: dict[str, list[shapely.Geometry]] = defaultdict(list)
    for name, shape in shapes.outlines("ADM3"):
        by_name[shapes.fold_latin(name)].append(shape)
    outlines: dict[str, shapely.Geometry] = {}
    entities = entity_of()
    for code, text in municipality_names().items():
        id = area(MUNICIPALITY, code)
        p.add("area", id=id, scheme_code=MUNICIPALITY)
        p.add("area_code", area_id=id, code=code)
        entity = entities.get(code)
        p.add("area_parent", area_id=id, parent_id=entity_area(entity) if entity else COUNTRY)
        p.name(id, text, "bs", "official")
        shape = outline_for(code, text, by_name)
        if shape is None and code not in NO_OUTLINE:
            raise ValueError(f"municipality {code} {text}: no single geoBoundaries outline")
        if shape is not None:
            outlines[code] = shape
            geometry(p, id, shape)
    for cc, c in contests().items():
        if race_of(cc) in CANDIDATE_RACES:
            continue
        id = area(DISTRICT, cc)
        p.add("area", id=id, scheme_code=DISTRICT)
        p.add("area_code", area_id=id, code=cc)
        p.add("area_parent", area_id=id, parent_id=entity_area(district_entity(cc)))
        en, bs = district_name(cc)
        p.name(id, en, "en", "official")
        p.name(id, bs, "bs", "native", False)
        members = [outlines[code] for code in c.units if code in outlines]
        geometry(p, id, shapely.union_all([shapes.multipolygon(m) for m in members]))
    return p


# Bodies, offices, and parties.


def body(cc: str) -> str | None:
    race = race_of(cc)
    if race in {"2", "4", "6"}:
        return key("body", race)
    if race == "7":
        return key("body", race, cc)
    return None


def office(cc: str) -> str | None:
    return key("office", race_of(cc)) if race_of(cc) in CANDIDATE_RACES else None


def party(pc: str) -> str:
    return key("party", pc)


@functools.cache
def party_spellings() -> dict[str, Counter[str]]:
    """The names of each party or list on a ballot (independent candidates stand for no party)."""
    names = {x["pc"]: x["pn"] for x in load(reference_url("parties.json"))}
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for cc, c in contests().items():
        for o in c.options:
            if not INDEPENDENT.search(names[o.pc]):
                out[o.pc][clean(names[o.pc])] += 2
        for row in csv_rows()[cc]:
            if row["Row Type"] != "CANDIDATE" or race_of(cc) in CANDIDATE_RACES:
                pc = row["Party ID"]
                if pc in out:
                    out[pc][clean(row["Political Party"])] += 1
    return out


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Bosnia and Herzegovina's legislatures and elected offices, and the parties and lists on the Central Election "
        "Commission's 2026 general election ballots",
    )
    p.external |= {BIH}
    for race, kind in (("2", "lower_house"), ("4", "lower_house"), ("6", "unicameral")):
        p.add("body", id=key("body", race), polity_id=BIH, kind=kind)
        p.name(key("body", race), RACES[race][0], "en", "official")
        p.name(key("body", race), RACES[race][1], "bs", "native", False)
    for cc in districts():
        if race_of(cc) == "7":
            id = key("body", "7", cc)
            n = cc[1:].lstrip("0")
            p.add("body", id=id, polity_id=BIH, kind="unicameral")
            p.name(id, f"Assembly of Canton {n}", "en", "official")
            p.name(id, f"Skupština Kantona {n}", "bs", "native", False)
    p.add("office", id=key("office", "1"), polity_id=BIH, kind="head_of_state")
    p.name(key("office", "1"), "Member of the Presidency of Bosnia and Herzegovina", "en", "official")
    p.name(key("office", "1"), "Član Predsjedništva Bosne i Hercegovine", "bs", "native", False)
    p.add("office", id=key("office", "5"), polity_id=BIH, kind="other")
    p.name(key("office", "5"), RACES["5"][0], "en", "official")
    p.name(key("office", "5"), RACES["5"][1], "bs", "native", False)
    for pc, spellings in sorted(party_spellings().items()):
        id = party(pc)
        p.add("party", id=id, kind="party", polity_id=BIH)
        p.add("external_id", entity_id=id, scheme=f"{SOURCE}.party", value=pc)
        best = spellings.most_common(1)[0][0]
        for text in sorted(spellings):
            p.name(id, text, "bs", "official", text == best)
    return p


# The election.


class Facts:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            name: [] for name in ("result_set_id", "unit_id", "option_id", "channel", "measure", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
        row = (result_set, unit, option, 0, measure, int(value))
        for column, item in zip(self.columns.values(), row, strict=True):
            column.append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


def contest_name(cc: str) -> tuple[str, str]:
    race = race_of(cc)
    en, bs = RACES[race]
    if cc == "701702":
        return f"{en}, members elected in the {ENTITY_NAMES['1']}", f"{bs}, bošnjački i hrvatski član"
    if cc == "703":
        return f"{en}, member elected in the {ENTITY_NAMES['2']}", f"{bs}, srpski član"
    if race == "5":
        return en, bs
    d_en, d_bs = district_name(cc)
    place_en, place_bs = d_en.split(" (")[0], d_bs.split(" (")[0]
    if race == "7":
        return f"Assembly of {place_en}", f"Skupština {place_bs}"
    return f"{en}, {place_en}", f"{bs}, {place_bs}"


def election() -> Package:
    p = Package(
        f"{SOURCE}/{SLUG}",
        SOURCE,
        "The 2026 Bosnian general election by municipality from the Central Election Commission's results site",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {BIH, STATIONS}
    names = {x["pc"]: x["pn"] for x in load(reference_url("parties.json"))}
    listed = set(party_spellings())
    election_id = key("election", SLUG)
    p.add("election", id=election_id, polity_id=BIH, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.name(election_id, "2026 Bosnian general election", "en", "official")
    path = f"ba/{SLUG}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values = Facts()
    units: dict[str, str] = {}
    over: list[str] = []
    people: set[str] = set()
    seen_slugs: set[str] = set()

    def unit(code: str, kind: str, place: str) -> str:
        id = key("unit", SLUG, code)
        if code not in units:
            units[code] = id
            p.add(
                "reporting_unit",
                id=id,
                polity_id=BIH,
                scheme_code=STATIONS,
                code=f"{SLUG}:{code}",
                area_relation="within",
                area_id=place,
                kind=kind,
            )
        return id

    for cc, c in contests().items():
        race = race_of(cc)
        contest, result_set = key("contest", SLUG, cc), key("result_set", SLUG, cc)
        en, bs = contest_name(cc)
        seats = sum(o.seats for o in c.options)
        elected = sum(o.elected for o in c.options)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="office" if race in CANDIDATE_RACES else "list_allocation",
            office_id=office(cc),
            body_id=body(cc),
            district_area_id=district_area(cc),
            magnitude=max(1, elected) if race in CANDIDATE_RACES else seats,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, en, "en", "official")
        p.name(contest, bs, "bs", "native", False)
        tail = changeset.slug(en)
        if tail in seen_slugs:
            tail = f"{tail}-{cc}"
        seen_slugs.add(tail)
        p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        note = COUNT_METHOD + ("" if race in CANDIDATE_RACES else SEATS_NOTE)
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
        options: dict[str, str] = {}
        for o in c.options:
            option = key("option", SLUG, cc, o.pc)
            options[o.pc] = option
            independent = bool(INDEPENDENT.search(names[o.pc]))
            if race in CANDIDATE_RACES:
                assert o.person and o.name
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None if independent else party(o.pc),
                    ballot_position=o.position,
                    ballot_label=o.label,
                )
                person = key("person", o.person)
                if person not in people:
                    people.add(person)
                    p.add("person", id=person)
                    p.name(person, proper(o.name), "bs", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", SLUG, cc, o.pc),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=proper(o.name),
                    member_of_party_id=None if independent else party(o.pc),
                    is_independent=True if independent else None,
                )
                if o.elected:
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
            else:
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=party(o.pc) if o.pc in listed else None,
                    ballot_position=o.position,
                    ballot_label=o.label,
                )
                if o.seats:
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=o.seats)
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=o.votes)
        for measure, n in ((REGISTERED, c.registered), (BALLOTS, c.ballots), (VALID, c.valid), (INVALID, c.invalid)):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=n)
        summed: Counter[str] = Counter()
        accounting: Counter[int] = Counter()
        for code, file in sorted(c.units.items()):
            if code.startswith("SCB"):
                id = unit(f"scb:{cc}", "aggregate", district_area(cc))
            else:
                id = unit(f"m:{code}", "aggregate", area(MUNICIPALITY, code))
            acc = counts(file)
            if acc[BALLOTS] > acc[REGISTERED] and not code.startswith("SCB"):
                over.append(f"{code} in {en} ({acc[BALLOTS]} ballots, {acc[REGISTERED]} registered)")
            for measure, n in acc.items():
                if not (measure == REGISTERED and code.startswith("SCB")):  # the centrally counted ballots have no roll
                    values.add(result_set, id, None, measure, n)
                accounting[measure] += n
            for row in file["rs"]:
                values.add(result_set, id, options[row["pc"]], VOTES, row["v"])
                summed[row["pc"]] += int(row["v"])
        if (accounting[VALID], accounting[INVALID], accounting[BALLOTS], accounting[REGISTERED]) != (
            c.valid,
            c.invalid,
            c.ballots,
            c.registered,
        ):
            raise ValueError(f"{cc}: the units' accounting differs from the district's")
        for o in c.options:
            if summed[o.pc] != o.votes:
                raise ValueError(f"{cc}: {o.label} has {summed[o.pc]} votes in the units, {o.votes} printed")
    for url in sorted({reference_url(n) for n in REFERENCE} | {CSV_URL}):
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    cited: set[str] = set()
    for cc in districts():
        urls = [contest_url(cc), district_url(cc), *(region_url(f"{cc}.{code}") for code in contests()[cc].units)]
        for url in urls:
            artifact = raw.cached(SOURCE, url)
            if artifact.storage_key in cited:
                p.uses(artifact)
            else:
                cited.add(artifact.storage_key)
                p.artifact(artifact, SOURCE_ID)
    for x in regions():
        if x["l"] == 1:
            p.artifact(raw.cached(SOURCE, region_url(x["c"])), SOURCE_ID)
    if over:
        p.accept(
            "accounting.turnout",
            "The Commission's own results print more ballots cast than registered voters for "
            + "; ".join(over)
            + ". The centrally counted ballots (postal, absentee, mobile, and tendered) belong to voters on other "
            "units' rolls, so that unit's ballots can exceed the voters registered to it.",
        )
    frame = values.frame()
    p.frame("result_value", frame)
    return p


def totals() -> list[str]:
    """Each body's districts summed against the totals the Commission prints for the body."""
    lines = []
    bodies = {"001": ("701702", "703"), "002": None, "004": None, "005": ("600",), "006": None, "007": None}
    for x in regions():
        if x["l"] != 1:
            continue
        code = x["c"]
        file = load(region_url(code))
        ct = {y["cn"]: y["cv"] for y in file["ct"]}
        parts = bodies[code] or tuple(str(c) for c in x["cs"])
        here = sum(contests()[c].valid for c in parts)
        printed = int(ct["total-valid-votes"])
        verdict = "match" if here == printed else f"differ by {here - printed}"
        lines.append(f"{code} {x['n'][:48]}: districts {here:,} valid, national {printed:,}, {verdict}")
        if here != printed:
            raise ValueError(lines[-1])
    return lines


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{SLUG}"]


def parse() -> None:
    geo().write()
    registry().write()
    election().write()
    for line in totals():
        print(f"  {line}", flush=True)


def emit() -> None:
    package.run("load", packages())
