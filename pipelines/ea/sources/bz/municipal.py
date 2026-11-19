"""Belize: city and town council elections, 2003 to 2024, by municipality.

The Elections and Boundaries Department (now Commission) publishes one PDF per election day with each council's
candidates and their votes: the mayor, elected directly, and the councillors, elected at large (ten in Belize City, six
elsewhere), each elector voting for up to that many. The text layer is read with `pdftotext -layout`. Every party's
printed total is checked against its candidates, and every contest against its printed turnout.
"""

import functools
import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import shapely
import shapely.geometry
import shapely.ops

from ea import package, raw
from ea.package import Package

SOURCE = "bz.municipal"
PAGE = "https://elections.gov.bz/municipal-election-results/"
BASE = "https://elections.gov.bz/wp-content/uploads"
BELIZE = str(uuid5(package.NAMESPACE, "polity\x1fBZ"))
VOTES, REGISTERED, CAST, INVALID = 1, 2, 3, 5
SCHEME = "bz.municipality"
OSM = "https://nominatim.openstreetmap.org/lookup"

URLS = {
    "2003": f"{BASE}/2023/12/2003-Municipal-Election-Results.pdf",
    "2006": f"{BASE}/2023/12/2006-Municipal-Election-Results.pdf",
    "2009": f"{BASE}/2023/12/Belize-City-Council-Election-Results-2009-UPDATED.pdf",
    "2012": f"{BASE}/2023/12/2012-Municipal-Election-Results.pdf",
    "2021": f"{BASE}/2023/12/2021-Municipal-Election-Resuls-Final.pdf",
    "2024-belize-city": f"{BASE}/2024/03/BELIZE-CITY-MUNICIPAL-RESULTS-2024-3.pdf",
    "2024-belmopan": f"{BASE}/2024/03/BELMOPAN-CITY-RESULTS-2024.pdf",
    "2024-benque-viejo": f"{BASE}/2024/03/BENQUE-VIEJO-DEL-CARMEN-TOWN-COUNCIL-RESULTS-2024-2.pdf",
    "2024-corozal": f"{BASE}/2024/03/COROZAL-TOWN-RESULTS-2024.pdf",
    "2024-dangriga": f"{BASE}/2024/03/DANGRIGA-TOWN-RESULTS-2024.pdf",
    "2024-orange-walk": f"{BASE}/2024/03/ORANGE-WALK-TOWN-RESULTS-2024.pdf",
    "2024-punta-gorda": f"{BASE}/2024/03/PUNTA-GORDA-TOWN-2024.pdf",
    "2024-san-ignacio": f"{BASE}/2024/03/SAN-IGNACIO-SANTA-ELENA-2024.pdf",
    "2024-san-pedro": f"{BASE}/2024/03/SAN-PEDRO-TOWN-RESULTS-2024.pdf",
}
DAYS = {
    date(2003, 3, 5): ("2003",),
    date(2006, 3, 1): ("2006",),
    date(2009, 3, 4): ("2009",),
    date(2012, 3, 7): ("2012",),
    date(2021, 3, 3): ("2021",),
    date(2024, 3, 6): tuple(name for name in URLS if name.startswith("2024-")),
}
# Where the Department prints the date as it prints the election, for the record of what the sheets say.
PRINTED = {
    date(2003, 3, 5): "Wednesday, 5th March 2003",
    date(2006, 3, 1): "Wednesday, 1st March, 2006",
    date(2009, 3, 4): "Wednesday, 4th March 2009",
    date(2012, 3, 7): "March 7, 2012",
    date(2021, 3, 3): "Wednesday, 3rd March 2021",
    date(2024, 3, 6): "Wednesday, 6th March 2024",
}


@dataclass(frozen=True)
class Town:
    key: str
    name: str
    seats: int
    words: tuple[str, ...]
    osm: tuple[int, ...]


TOWNS = (
    Town("belize-city", "Belize City", 10, ("BELIZE CITY",), (14902774,)),
    Town("belmopan", "Belmopan", 6, ("BELMOPAN",), (10331791,)),
    Town("orange-walk", "Orange Walk Town", 6, ("ORANGE WALK",), (14411947,)),
    Town("corozal", "Corozal Town", 6, ("COROZAL",), (11520698,)),
    Town("san-ignacio-santa-elena", "San Ignacio and Santa Elena", 6, ("SAN IGNACIO",), (14097408, 14045854)),
    Town("benque-viejo", "Benque Viejo del Carmen", 6, ("BENQUE", "BENJUE", "BEQUE"), (14101754,)),
    Town("dangriga", "Dangriga", 6, ("DANGRIGA",), (16761293,)),
    Town("punta-gorda", "Punta Gorda", 6, ("PUNTA GORDA",), (3394396,)),
    Town("san-pedro", "San Pedro", 6, ("SAN PEDRO",), (11520689,)),
)
BY_KEY = {t.key: t for t in TOWNS}

PARTIES = {
    "PUP": "People's United Party",
    "UDP": "United Democratic Party",
}
INDEPENDENT = "IND"
HEADERS = {
    "PUP": "PUP",
    "UDP": "UDP",
    "VIP": "VIP",
    "NABR": "NABR",
    "WTP": "WTP",
    "PNP": "PNP",
    "BENQUEASSOCIATION": "BENQUE ASSOCIATION",
    "CONGRESSOFTHEPGPEOPLE": "CONGRESS OF THE PG PEOPLE",
    "INDEPENDENT": INDEPENDENT,
}

# Printed turnout that the same sheet's votes contradict, or that the Department copied from another council's sheet.
STAT_FIXES: dict[tuple[str, str], tuple[set[str], str]] = {
    ("2006", "corozal"): (
        {"registered", "cast", "rejected"},
        (
            "the sheet's turnout block repeats Belmopan's (5,059 electors, 3,576 used and 3,564 cast ballots), and the "
            "mayoral candidates' votes alone exceed those ballots, so no turnout is stored"
        ),
    ),
}
OFFICE_FIXES = {
    ("2009", "punta-gorda", "MORGAN, Hervan Marion"): "MAYOR",
}
NAME_FIXES = {"LESL IE DEAN MARTINEZ": "LESLIE DEAN MARTINEZ"}
INDEPENDENTS_2012 = (
    "The 2012 sheet prints no office beside a candidate. Each party's first candidate stands for mayor and the rest "
    "for councillor, as every party's slate is a mayor and up to as many councillors as the council has seats. A "
    "candidate on the sheet under INDEPENDENT could stand for either, so those candidates are not stored."
)


@dataclass
class Cand:
    name: str
    office: str  # "mayor" or "council"
    party: str | None
    votes: int
    elected: bool


@dataclass
class Sheet:
    town: str
    cands: list[Cand] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    skipped: list[tuple[str, int]] = field(default_factory=list)
    blocks: list[tuple[str | None, list[Cand], int | None]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def n(text: str) -> int:
    text = text.strip()
    if re.fullmatch(r"\d\.\d{3}", text):
        return int(text.replace(".", ""))
    return int(re.sub(r"[,\s]", "", text))


def clean(name: str) -> str:
    text = name.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    text = re.sub(r"\s{2,}", ", ", text.strip())
    text = re.sub(r"\s+", " ", text).replace(' " ', ' "').strip()
    return NAME_FIXES.get(text, text)


def fetch() -> None:
    from ea.sources.bz import polling

    for link in URLS.values():
        raw.fetch(SOURCE, link, suffix=".pdf")
    raw.fetch(SOURCE, outline_url(), suffix=".json")
    polling.fetch()


def outline_url() -> str:
    ids = ",".join(f"R{osm}" for town in TOWNS for osm in town.osm)
    return f"{OSM}?osm_ids={ids}&format=geojson&polygon_geojson=1"


@functools.cache
def text(name: str) -> str:
    path = raw.cached(SOURCE, URLS[name]).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def town_of(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or re.match(r"the following", stripped, re.IGNORECASE) or len(stripped) > 95:
        return None
    if re.search(r"\d{3}\s*$", stripped) and not re.search(r"ELECTIONS?\b", stripped, re.IGNORECASE):
        return None
    upper = stripped.upper()
    for town in TOWNS:
        if any(word in upper for word in town.words):
            return town.key
    return None


NUMBER = r"\d[\d,]*(?:, \d{3})?"


def candidate(party: bool) -> re.Pattern[str]:
    owner = r"(?:(?P<party>[A-Z]{3})\s+)?" if party else ""
    return re.compile(
        rf"^\s*(?:\d+\s+)?(?P<name>\S.*?)(?:\s+|(?<=[”\x22])\s*){owner}(?P<office>MAYORAL|MAYOR|COUNCILLOR)\s+"
        rf"(?P<votes>{NUMBER})(?:\s+(?P<pct>\d+\.\d+))?(?:\s+(?P<el>ELECTED))?\s*$",
        re.IGNORECASE,
    )


CAND = candidate(False)
CAND_PARTY = candidate(True)
NOOFFICE = re.compile(rf"^\s*(?P<name>\S.*?)\s{{2,}}(?P<votes>{NUMBER})\s+(?P<pct>\d+\.\d+)\s*$")
TOTAL = re.compile(rf"^\s*TOTAL (?:NO\.? OF )?VOTES\s+(?P<votes>{NUMBER})\s*$", re.IGNORECASE)
OVERALL = re.compile(rf"^\s*OVERALL VOTES POLLED\s+(?P<votes>{NUMBER})\s*$", re.IGNORECASE)
STATS = (
    ("registered", re.compile(r"No\. of (?:Registered (?:Electors|Persons)|Persons Registered)\D*?(\d[\d.,]*)")),
    ("cast", re.compile(r"No\. of (?:Ballots Cast|Persons Voted)\D*?(\d[\d.,]*)")),
    ("rejected", re.compile(r"^\s*(?:\d\.\s+)?(?:No\. of|Total) Ballots Rejected\D*?(\d[\d.,]*)", re.IGNORECASE)),
)


def header(line: str) -> str | None:
    if re.search(r"\d", line) or not line.strip():
        return None
    first = re.split(r"\s{2,}", line.strip())[0]
    code = re.sub(r"[^A-Z]", "", first.upper())
    return HEADERS.get(code)


def sheets(name: str, year: str, layout: str) -> dict[str, Sheet]:
    """The candidates and printed turnout of every council in a sheet-style document (all years but 2012)."""
    found: dict[str, Sheet] = {}
    current: Sheet | None = None
    party: str | None = None
    block: list[Cand] = []
    labelled = False

    def close(total: int | None) -> None:
        nonlocal block
        if block and current is not None:
            current.blocks.append((party if labelled else None, block, total))
        block = []

    for line in text(name).splitlines():
        town = town_of(line)
        if town is not None and not CAND.match(line) and not TOTAL.match(line):
            if current is None or current.town != town:
                close(None)
                current = found.setdefault(town, Sheet(town))
            party, labelled = None, False
            continue
        if current is None:
            continue
        total = TOTAL.match(line)
        if total:
            close(n(total["votes"]))
            continue
        if OVERALL.match(line):
            close(None)
            continue
        got = header(line)
        if got is not None:
            close(None)
            party, labelled = got, True
            continue
        hit = (CAND_PARTY if layout == "party-column" else CAND).match(line)
        if hit:
            office = "mayor" if hit["office"].upper().startswith("MAYOR") else "council"
            who = hit["party"].upper() if layout == "party-column" and hit["party"] else party
            cand = Cand(clean(hit["name"]), office, None, n(hit["votes"]), bool(hit["el"]))
            if layout == "party-column":
                party, labelled = who, True
            cand.party = who if labelled else None
            block.append(cand)
            continue
        if re.search(r"(MAYOR|COUNCILLOR)\s+\d", line, re.IGNORECASE):
            raise ValueError(f"{year} {current.town}: unparsed candidate line {line!r}")
        plain = NOOFFICE.match(line)
        if plain and (year, current.town, clean(plain["name"])) in OFFICE_FIXES:
            nm = clean(plain["name"])
            office = OFFICE_FIXES[year, current.town, nm]
            block.append(Cand(nm, office.lower().replace("councillor", "council"), party, n(plain["votes"]), False))
            continue
        for key, pattern in STATS:
            stat = pattern.search(line)
            if stat and key not in current.stats:
                close(None)
                current.stats[key] = n(stat.group(1))
                break
    close(None)
    for sheet in found.values():
        sheet.cands = [c for _, cs, _ in sheet.blocks for c in cs]
    return {key: sheet for key, sheet in found.items() if sheet.cands}


PARTY12 = r"U\.D\.P|P\.U\.P|V\.I\.P|P\.N\.P|INDEPENDENT"
LEAD12 = re.compile(
    r"^(?:Belize City|San Pedro|Benque Viejo|Corozal Town|Orange Walk|San Ignacio &|Belmopan|Dangriga|Punta Gorda)\s+"
)
ROW12 = re.compile(rf"^(?P<name>[A-Z][A-Z .'\"\-/&,]*?)\s*(?P<party>{PARTY12})\s*(?P<rest>.*)$")
STARTS12 = (
    ("Belize City", "belize-city"),
    ("San Pedro", "san-pedro"),
    ("Benque Viejo", "benque-viejo"),
    ("Corozal Town", "corozal"),
    ("Orange Walk", "orange-walk"),
    ("San Ignacio", "san-ignacio-santa-elena"),
    ("Belmopan", "belmopan"),
    ("Dangriga", "dangriga"),
    ("Punta Gorda", "punta-gorda"),
)


def inline(body: str) -> dict[str, Sheet]:
    """The 2012 sheet: one table, each council's rows in order, the council's turnout beside its first row."""
    found: dict[str, Sheet] = {}
    current: Sheet | None = None
    last: Cand | None = None
    pending: int | None = None
    need_cast = False
    codes: list[str] = []
    for line in body.splitlines():
        if re.match(r"\s*SUMMARY", line):
            break
        for start, key in STARTS12:
            if line.startswith(start):
                current = found[key] = Sheet(key)
                last, pending, need_cast, codes = None, None, False, []
        if current is None or not line.strip():
            continue
        stripped = LEAD12.sub("", line.strip(), count=1)
        stripped = re.sub(
            r"^(?:Council|Town|City|del Carmen|Santa Elena Town|Santa Elena)\s+(?=[A-Z]{2})", "", stripped
        )
        rejected = re.search(r"rejected\s+(\d+)", line)
        if rejected:
            current.stats["rejected"] = n(rejected.group(1))
            continue
        stray = re.fullmatch(r"(?:[A-Za-z .&]{0,14}\s+)?(\d+)", line.strip()) if not re.search(PARTY12, line) else None
        if stray:
            value = n(stray.group(1))
            if need_cast:
                current.stats["cast"] = value
                need_cast = False
            else:
                pending = value
            continue
        row = ROW12.match(stripped)
        if not row:
            continue
        tokens = row["rest"].split()
        elected = any(t.lower() == "elected" for t in tokens)
        numbers = [t for t in tokens if re.fullmatch(r"\d[\d,]*", t)]
        percent = [t for t in tokens if t.endswith("%")]
        votes: int | None = None
        if percent:
            before = []
            for token in tokens:
                if token.endswith("%"):
                    break
                if re.fullmatch(r"\d[\d,]*", token):
                    before.append(n(token))
            after = [n(t) for t in numbers][len(before) :]
            current.stats["registered"] = before[0]
            if len(before) > 1:
                current.stats["cast"] = before[1]
            else:
                need_cast = True
            votes = after[0] if after else None
        elif numbers:
            votes = n(numbers[-1])
        if votes is None and pending is not None:
            votes, pending = pending, None
        if votes is None:
            raise ValueError(f"2012 {current.town}: no votes for {row['name']!r}")
        label = row["party"]
        code = INDEPENDENT if label == "INDEPENDENT" else label.replace(".", "")
        codes.append(code)
        if code == INDEPENDENT:
            current.skipped.append((clean(row["name"]), votes))
            continue
        office = "council" if codes.count(code) > 1 else "mayor"
        last = Cand(clean(row["name"]), office, code, votes, elected)
        current.cands.append(last)
    return found


def sheet_for(day: date, name: str) -> dict[str, Sheet]:
    year = str(day.year)
    if year == "2012":
        return inline(text("2012"))
    if year == "2024":
        return sheets(name, year, "party-column")
    return sheets(name, year, "party-header" if year != "2021" else "plain")


@functools.cache
def election(day: date) -> dict[str, Sheet]:
    found: dict[str, Sheet] = {}
    for name in DAYS[day]:
        for key, sheet in sheet_for(day, name).items():
            if key in found:
                raise ValueError(f"{day}: {key} appears twice")
            found[key] = sheet
    for key, sheet in found.items():
        check(day, key, sheet)
    return found


def debug(day: date) -> None:
    for name in DAYS[day]:
        for sheet in sheet_for(day, name).values():
            print(sheet.town, sheet.stats)
            for c in sheet.cands:
                print("  ", c)


def check(day: date, key: str, sheet: Sheet) -> None:
    where = f"{day} {key}"
    town = BY_KEY[key]
    for party, cands, total in sheet.blocks:
        if total is not None and total != sum(c.votes for c in cands):
            label = party or "The party's"
            sheet.notes.append(
                f"The sheet prints {total:,} as the {label} candidates' total votes, but its candidate rows add to "
                f"{sum(c.votes for c in cands):,}; the rows are stored"
            )
    mayors = [c for c in sheet.cands if c.office == "mayor"]
    council = [c for c in sheet.cands if c.office == "council"]
    if not mayors or not council:
        raise ValueError(f"{where}: {len(mayors)} mayoral and {len(council)} council candidates")
    for fix in STAT_FIXES.get((str(day.year), key), (set(), ""))[0]:
        sheet.stats.pop(fix, None)
    cast = sheet.stats.get("cast")
    if cast is not None and sum(c.votes for c in mayors) > cast and (str(day.year), key) not in STAT_FIXES:
        sheet.stats.pop("cast")
        sheet.notes.append(
            f"The sheet prints {cast:,} ballots cast, but the mayoral candidates' votes alone add to "
            f"{sum(c.votes for c in mayors):,}, so the ballots cast are not stored"
        )
        cast = None
    if cast is not None and sheet.stats.get("registered", cast) < cast:
        raise ValueError(f"{where}: {cast} ballots cast exceed {sheet.stats['registered']} electors")
    if sum(c.elected for c in mayors) != 1:
        raise ValueError(f"{where}: {sum(c.elected for c in mayors)} mayors elected")
    if sum(c.elected for c in council) != town.seats:
        raise ValueError(f"{where}: {sum(c.elected for c in council)} councillors elected of {town.seats} seats")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


EBC_SOURCE = key("source", "ebc")
OSM_SOURCE = key("source", "osm")
SYSTEM_MAYOR = key("electoral_system", "mayor")
SYSTEM_COUNCIL = key("electoral_system", "council")


def area(town: str) -> str:
    return key("area", SCHEME, town)


def party(code: str) -> str:
    return key("party", code)


def packages() -> list[str]:
    from ea.sources.bz import polling

    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS), *polling.packages()]


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        build(day).write()
    from ea.sources.bz import polling

    polling.build().write()


def emit() -> None:
    package.run("load", packages())


def outlines() -> dict[str, shapely.Geometry]:
    collection = json.loads(raw.cached(SOURCE, outline_url()).path.read_bytes())
    by_id = {int(f["properties"]["osm_id"]): shapely.geometry.shape(f["geometry"]) for f in collection["features"]}
    found: dict[str, shapely.Geometry] = {}
    for town in TOWNS:
        shape = shapely.ops.unary_union([by_id[i] for i in town.osm])
        if shape.geom_type not in ("Polygon", "MultiPolygon") or not shape.is_valid:
            raise ValueError(f"{town.name}: no valid outline")
        found[town.key] = shapely.force_2d(shape)
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Belize's nine city and town council areas, with outlines from OpenStreetMap",
    )
    p.external.add(BELIZE)
    artifact = raw.cached(SOURCE, outline_url())
    p.add(
        "source",
        id=OSM_SOURCE,
        kind="community",
        publisher="OpenStreetMap contributors",
        title="OpenStreetMap administrative boundaries of Belize's cities and towns, through Nominatim",
        url="https://www.openstreetmap.org/",
        license="Open Database License 1.0",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="© OpenStreetMap contributors",
        notes=(
            "Each outline is the boundary relation the OpenStreetMap community maintains for the city or town; San "
            "Ignacio and Santa Elena, which share one council, are the union of their two relations. The outlines are "
            "as retrieved, not as drawn in the year of each election."
        ),
    )
    p.artifact(artifact, OSM_SOURCE)
    p.add("scheme", code=SCHEME, polity_id=BELIZE, kind="administrative", rank=2, noun="city or town council area")
    vintage = artifact.retrieved_at.date()
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME, vintage),
        scheme_code=SCHEME,
        vintage=f"OpenStreetMap {vintage}",
        valid_from=date(2003, 3, 5),
        as_of=vintage,
    )
    shapes = outlines()
    for town in TOWNS:
        id = area(town.key)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=town.key)
        p.name(id, town.name, "en", "official")
        shape = shapes[town.key]
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=date(2003, 3, 5),
            geom=shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=OSM_SOURCE,
        )
    return p


def codes() -> list[str]:
    seen: Counter[str] = Counter()
    for day in DAYS:
        for sheet in election(day).values():
            for c in sheet.cands:
                if c.party and c.party != INDEPENDENT:
                    seen[c.party] += 1
    return sorted(seen)


def body(town: str) -> str:
    return key("body", town)


def office(town: str) -> str:
    return key("office", town)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Belize's city and town councils, their mayoralties, the parties on their ballots, and the Department's "
        "municipal result files",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(BELIZE)
    p.add(
        "source",
        id=EBC_SOURCE,
        kind="official_body",
        publisher="Elections and Boundaries Commission of Belize",
        title="Municipal election results published by the Elections and Boundaries Department",
        url=PAGE,
        license="No license stated; published by the electoral authority for public use",
        attribution="Elections and Boundaries Commission of Belize",
    )
    for link in URLS.values():
        p.artifact(raw.cached(SOURCE, link), EBC_SOURCE)
    for town in TOWNS:
        b = body(town.key)
        p.add("body", id=b, polity_id=BELIZE, kind="council")
        p.name(b, f"{town.name} council", "en", "official")
        o = office(town.key)
        p.add("office", id=o, polity_id=BELIZE, body_id=b, kind="mayor")
        p.name(o, f"Mayor of {town.name}", "en", "official")
    for id, rules, text_ in (
        (SYSTEM_MAYOR, {"formula": "fptp"}, "The mayor is elected by all the council's voters, by the most votes."),
        (
            SYSTEM_COUNCIL,
            {"formula": "block"},
            (
                "The councillors are elected at large, ten in Belize City and six in every other council. Each "
                "elector votes for up to that many candidates, and the candidates with the most votes are elected."
            ),
        ),
    ):
        p.add("electoral_system", id=id, polity_id=BELIZE, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text_, lang="en")
    for code in codes():
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=BELIZE)
        p.add("external_id", entity_id=id, scheme="bz.municipal.party", value=code)
        p.name(id, code, "en", "abbreviation")
        if code in PARTIES:
            p.name(id, PARTIES[code], "en", "official", False)
    return p


METHOD = (
    "Counted for each council and published by the Elections and Boundaries Department. Each candidate's votes are "
    "stored as printed, and so are the council's registered electors, ballots cast, and rejected ballots where the "
    "Department prints them. Every voter chooses a mayor and up to {seats} councillors, so ballots cast are not the "
    "sum of the votes."
)


def build(day: date) -> Package:
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Belize city and town council elections of {day}, by council",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(BELIZE)
    for name in DAYS[day]:
        p.uses(raw.cached(SOURCE, URLS[name]))
    eid = key("election", day)
    path = f"bz/{day}-municipal"
    p.add(
        "election",
        id=eid,
        polity_id=BELIZE,
        kind="local",
        date_from=day,
        date_to=day,
        date_precision="day",
        original_text=PRINTED[day],
        status="held",
    )
    p.add("slug", path=path, entity_id=eid, is_current=True)
    p.name(eid, f"{day.year} Belize city and town council elections", "en", "official")
    found = election(day)
    for town in TOWNS:
        if town.key in found:
            _council(p, day, eid, path, town, found[town.key])
    return p


def _put(p: Package, rs: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=rs,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def _council(p: Package, day: date, eid: str, path: str, town: Town, sheet: Sheet) -> None:
    from ea.sources.bz import polling

    refined = day == polling.DAY and town.key in polling.LAYOUTS
    unit = key("unit", day, town.key)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=BELIZE,
        scheme_code=SCHEME,
        code=f"{day}:{town.key}",
        area_relation="equals",
        area_id=area(town.key),
        kind="standard",
    )
    p.name(unit, town.name, "en", "official")
    fixes = STAT_FIXES.get((str(day.year), town.key))
    for office_kind in ("mayor", "council"):
        tag = f"{day}:{town.key}:{office_kind}"
        mayor = office_kind == "mayor"
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=eid,
            kind="office" if mayor else "body_seats",
            office_id=office(town.key) if mayor else None,
            body_id=None if mayor else body(town.key),
            district_area_id=area(town.key),
            magnitude=1 if mayor else town.seats,
            round_number=1,
            electoral_system_id=SYSTEM_MAYOR if mayor else SYSTEM_COUNCIL,
            default_result_kind="counted",
        )
        p.name(contest, f"{town.name} {'mayor' if mayor else 'council'}, {day.year}", "en", "official")
        p.add("slug", path=f"{path}/{town.key}/{office_kind}", entity_id=contest, is_current=True)
        rs = key("result_set", tag)
        p.add(
            "result_set",
            id=rs,
            election_id=eid,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=EBC_SOURCE,
            status="active",
            is_default=not refined,
            completeness=1.0,
        )
        method = METHOD.format(seats=town.seats)
        if fixes:
            method += f" {fixes[1][0].upper()}{fixes[1][1:]}."
        for note in sheet.notes:
            method += f" {note}."
        if refined:
            method += " A finer set by polling area, from the Department's combined 2009 sheet, supersedes it."
        if day.year == 2012:
            method += f" {INDEPENDENTS_2012}"
        if day.year == 2021:
            method += " The 2021 sheets print no party beside a candidate, so no party is stored."
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=method, lang="en")
        for position, c in enumerate([c for c in sheet.cands if c.office == office_kind], 1):
            option = key("option", tag, position)
            owner = party(c.party) if c.party and c.party != INDEPENDENT else None
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=owner,
                ballot_position=position,
                ballot_label=c.name,
            )
            person = key("person", tag, position)
            p.add("person", id=person)
            p.name(person, c.name, "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", tag, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=c.name,
                member_of_party_id=owner,
                is_independent=c.party == INDEPENDENT,
            )
            if c.elected:
                p.add("outcome", result_set_id=rs, option_id=option, kind="elected")
            _put(p, rs, unit, option, VOTES, c.votes)
        _put(p, rs, unit, None, REGISTERED, sheet.stats.get("registered"))
        _put(p, rs, unit, None, CAST, sheet.stats.get("cast"))
        _put(p, rs, unit, None, INVALID, sheet.stats.get("rejected"))
