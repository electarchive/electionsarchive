"""Greece's national elections from the Ministry of Interior's results site, down to the municipal unit or station.

The Ministry publishes each election as a set of small script files, one per electoral district (`ep_N.js`), and,
for the elections from 2012 to 2023, one per municipality (`dhm_N.js`) and municipal unit (`den_N.js`); from 2023 the
votes cast abroad come by city. Files saved from a browser concatenate many of them under `<!-- url -->` headers, or
gather them in a JSON object keyed by address; `split` takes them apart again.

The 1996, 1999, 2000, and 2004 European pages give only the national result. The 2004 to 2009 parliamentary and 2009
European elections, which the Ministry published only by electoral district, are built by `ypes_national`. The 2019
and 2024 European elections, which the Ministry published by polling station, are built by `ypes_ep`.

The September 2015 parliamentary election also goes down to the polling station: each station's pair of `tm_<id>.js`
files (static names and dynamic counts) sits under its municipal unit, or under its district when it has none, and the
build fails unless each unit's and district's stations add up to that unit's and district's own file.
"""

import html
import json
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "gr.ypes"
SITE = "https://ekloges.ypes.gr/"
GREECE = str(uuid5(package.NAMESPACE, "polity\x1fGR"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGRC"))
DERIVED = 1
INDEPENDENTS = 998
BLANK_MEASURES = (2, 3, 4, 5, 6)
ABROAD = 57

A = "browser://20261116-222821-308b6c/"
B = "browser://20261116-224930-393d08/"
C = "browser://20261117-133413-6c56bf/"
D = "browser://20261119-211248-684d3e/"
E = "browser://20261120-002242-eef882/"
OLD = "http://ekloges-prev.singularlogic.eu/"
GEO = f"{SOURCE}/national-geo"  # `ypes` builds the local elections' geo and registry
REGISTRY = f"{SOURCE}/national-registry"
STATION = "gr.polling_station"


@dataclass(frozen=True)
class Election:
    key: str
    day: date
    family: str  # parliament, euro, or referendum
    files: tuple[str, ...]
    extra: tuple[str, ...] = ()  # single district files the bundles lack
    legacy: bool = False  # a page that gives only the national result
    stations: tuple[str, ...] = ()  # files of polling-station counts, one `tm_<id>.js` pair for each station

    @property
    def package(self) -> str:
        return f"{SOURCE}/{self.key}"

    @property
    def path(self) -> str:
        suffix = {"parliament": "general", "euro": "european-parliament", "referendum": "referendum"}[self.family]
        return f"gr/{self.day.isoformat()}-{suffix}"


ELECTIONS = [
    Election("ps-2012-05", date(2012, 5, 6), "parliament", (B + "gr-2012-may-national-district-municipality.txt",)),
    Election("ps-2012-06", date(2012, 6, 17), "parliament", (B + "gr-2012-june-national-district-municipality.txt",)),
    Election("eu-2014", date(2014, 5, 25), "euro", (B + "gr-2014-may-euro-national-district-municipality.txt",)),
    Election("ps-2015-01", date(2015, 1, 25), "parliament", (B + "gr-2015-jan-national-district-municipality.txt",)),
    Election(
        "ref-2015", date(2015, 7, 5), "referendum", (B + "gr-2015-referendum-national-district-municipality.txt",)
    ),
    Election(
        "ps-2015-09",
        date(2015, 9, 20),
        "parliament",
        (E + "ps-2015-09-upper-levels-v2015b.txt",),  # its national file is `dyn1/v/epik_1.js`
        stations=tuple(E + f"ps-2015-09-ep{n}-stations-v2015b.txt" for n in range(1, 57)),
    ),
    Election(
        "ps-2019",
        date(2019, 7, 7),
        "parliament",
        (C + "gr-vouli-2019-07-ep-dhm-den-bundle.json",),
    ),
    Election(
        "ps-2023-05",
        date(2023, 5, 21),
        "parliament",
        (C + "gr-vouli-2023-05-ep-dhm-den-abroad-bundle.json",),
    ),
    Election(
        "ps-2023-06",
        date(2023, 6, 25),
        "parliament",
        (C + "gr-vouli-2023-06-ep-dhm-den-abroad-bundle.json",),
    ),
]
LEGACY = "https://eklogesapps.ypes.gr/{0}/eklogesapps.ypes.gr/ekloges/content/gr/elec_data/{1}_epi_res.html"
ELECTIONS += [
    Election("ps-1996", date(1996, 9, 22), "parliament", (LEGACY.format("ekloges1996", "1996NE"),), legacy=True),
    Election("eu-1999", date(1999, 6, 13), "euro", (LEGACY.format("ekloges1999", "1999UE"),), legacy=True),
    Election("ps-2000", date(2000, 4, 9), "parliament", (LEGACY.format("ekloges2000", "2000NE"),), legacy=True),
    Election("eu-2004", date(2004, 6, 13), "euro", (LEGACY.format("ekloges2004eu", "2004UE"),), legacy=True),
]
ELECTIONS.sort(key=lambda e: e.day)
BY_KEY = {e.key: e for e in ELECTIONS}
STATICS = {  # elections whose district, municipality, and unit tables are in a statics file
    "ps-2012-05",
    "ps-2012-06",
    "eu-2014",
    "ps-2015-01",
    "ref-2015",
    "ps-2015-09",
    "ps-2019",
    "ps-2023-05",
    "ps-2023-06",
}
UNITS = {  # municipal-unit results
    "ps-2012-05",
    "ps-2012-06",
    "eu-2014",
    "ps-2015-01",
    "ref-2015",
    "ps-2015-09",
    "ps-2019",
    "ps-2023-05",
    "ps-2023-06",
}


def fetch() -> None:
    """Store the files saved from a browser, listed in `index.json` of the folder named by `EA_GR_FETCHED`."""
    folder = Path(os.environ["EA_GR_FETCHED"])
    index = json.loads((folder / "index.json").read_text())
    for url, entry in index.items():
        content = (folder / entry["file"]).read_bytes()
        raw.store(
            SOURCE,
            entry.get("source", url),
            content,
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]).astimezone(UTC),
            media_type=entry["media_type"],
        )


def cached(url: str) -> raw.Artifact:
    for key in (
        url,
        *(k for k in raw.keys(SOURCE) if k.endswith(url.removeprefix("http://").removeprefix("https://"))),
    ):
        try:
            return raw.cached(SOURCE, key)
        except LookupError:
            continue
    raise LookupError(f"{url} is not in the {SOURCE} raw cache")


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1253")


def split(text: str, url: str) -> dict[str, str]:
    if text.lstrip().startswith("{"):
        try:
            bundle = json.loads(text)
        except ValueError:
            bundle = None
        if isinstance(bundle, dict) and bundle and all(str(k).startswith("http") for k in bundle):
            return {str(k): str(v) for k, v in bundle.items()}  # a bundle of files, keyed by their addresses
    if text.startswith("### "):  # files saved from a browser, each under a `### <path>` line
        parts = re.split(r"^### (\S+)\n", text, flags=re.MULTILINE)
        return dict(zip(parts[1::2], parts[2::2], strict=True))
    if text.startswith("// http"):  # files saved from a browser, each under a `// <address>` line
        parts = re.split(r"^// (http\S+)\n", text, flags=re.MULTILINE)
        return dict(zip(parts[1::2], parts[2::2], strict=True))
    parts = re.split(r"<!-- (\S+) -->\n?", text)
    if len(parts) == 1:
        return {url: text}
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"expected an object, found {type(value).__name__}")
    return value


def listing(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"expected a list, found {type(value).__name__}")
    return value


def rows_of(value: object) -> list[dict[str, object]]:
    return [mapping(row) for row in listing(value)]


def bracket(text: str, name: str) -> list[list[object]]:
    """The array that follows `name:` in a statics file; empty when the file has none."""
    found = re.search(rf"\b{name}:\s*(\[)", text)
    if found is None:
        return []
    start, depth = found.end() - 1, 0
    for i in range(start, len(text)):
        if text[i] in "[{":
            depth += 1
        elif text[i] in "]}":
            depth -= 1
            if depth == 0:
                return [listing(row) for row in listing(json.loads(text[start : i + 1]))]
    raise ValueError(f"unbalanced {name}")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: object) -> str:
    return key("area", scheme, code)


@dataclass
class Figures:
    """One reporting level of one election: registered voters, ballots, and party votes."""

    registered: int
    valid: int
    invalid: int
    blank: int
    votes: dict[int, int]
    seats: dict[int, int]
    ballots: int | None = None  # ballots cast, when invalid and blank ballots are published only together

    @property
    def cast(self) -> int:
        return self.valid + self.invalid + self.blank if self.ballots is None else self.ballots


def figures(row: object, parties: object, seats: str | None) -> Figures:
    low = {k.lower(): v for k, v in mapping(row).items()}
    votes: dict[int, int] = {}
    won: dict[int, int] = {}
    for entry in rows_of(parties):
        got = {k.lower(): v for k, v in entry.items()}
        pid = int(str(got["party_id"]))
        votes[pid] = int(str(got["votes"]))
        if seats is not None and got.get(seats.lower()):
            won[pid] = int(str(got[seats.lower()]))
    return Figures(
        registered=int(str(low["gramenoi"])),
        valid=int(str(low["egkyra"])),
        invalid=int(str(low["akyra"])),
        blank=int(str(low["leyka"])),
        votes=votes,
        seats=won,
    )


@dataclass
class Data:
    """Everything one election's files hold."""

    national: Figures
    districts: dict[int, Figures]
    municipalities: dict[int, Figures]
    units: dict[int, Figures]
    names: dict[int, str]  # districts
    seats: dict[int, int]  # district seats, from the statics
    parties: dict[int, str]
    municipality_names: dict[int, tuple[str, int]]  # name and district
    unit_names: dict[int, tuple[str, int]]  # name and municipality
    updated: str
    official: bool
    cities: dict[int, Figures] = field(default_factory=dict)  # votes abroad, by city
    city_names: dict[int, tuple[str, str]] = field(default_factory=dict)  # city and country
    stations: list[Station] = field(default_factory=list)


@dataclass
class Station:
    id: int
    district: int  # the district whose contest counts the station
    unit: int  # the Ministry's code of the municipal unit, or 0 for a station of voters registered elsewhere
    kind: str
    name: str
    figures: Figures


def number(text: str) -> int:
    return int(text.replace(".", ""))


def legacy(election: Election) -> Data:
    """A 1996 to 2004 page: the national ballots, and each party's share, votes, and seats."""
    page = cached(election.files[0]).path.read_bytes().decode("cp1253", errors="replace")
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    head = re.search(r"Εγγεγραμμένοι ([\d.]+) Ψήφισαν ([\d.]+) Έγκυρα ([\d.]+) \S+ ([\d.]+)", text)
    start = text.find("Έλαβαν Κόμμα % Ψήφοι Έδρες")
    if head is None or start < 0:
        raise LookupError(f"{election.key}: the page has no national table")
    rows = re.findall(r"(\S.*?) (\d+,\d+) ([\d.]+) (\d+)(?= |$)", text[start + len("Έλαβαν Κόμμα % Ψήφοι Έδρες") :])
    parties: dict[int, str] = {}
    votes: dict[int, int] = {}
    seats: dict[int, int] = {}
    for i, (name, _, count, won) in enumerate(rows, 1):
        parties[i] = name.strip()
        votes[i] = number(count)
        if int(won):
            seats[i] = int(won)
    national = Figures(
        registered=number(head.group(1)),
        valid=number(head.group(3)),
        invalid=0,
        blank=0,
        votes=votes,
        seats=seats,
        ballots=number(head.group(2)),
    )
    return Data(
        national=national,
        districts={},
        municipalities={},
        units={},
        names={},
        seats={},
        parties=parties,
        municipality_names={},
        unit_names={},
        updated="",
        official=True,
    )


STATION_KINDS = {"Κ": "standard", "Ε": "standard", "Μ": "standard", "Φ": "prison"}


def load_stations(election: Election) -> list[Station]:
    stat: dict[int, dict[str, object]] = {}
    dyn: dict[int, dict[str, object]] = {}
    for url in election.stations:
        for name, text in split(decode(cached(url).path.read_bytes()), url).items():
            found = re.search(r"/(stat|dyn)/v/\d+/tm_(\d+)\.js$", name)
            if found is None:
                continue
            row = mapping(json.JSONDecoder().raw_decode(text.strip())[0])
            (stat if found.group(1) == "stat" else dyn)[int(found.group(2))] = row
    if set(stat) != set(dyn):
        raise ValueError(f"{election.key}: stations lack a static file or a count: {sorted(set(stat) ^ set(dyn))[:5]}")
    return [
        Station(
            tm,
            int(str(stat[tm]["EP_ID"])),
            int(str(stat[tm]["DEN_ID"])),
            STATION_KINDS[str(stat[tm]["TM_KIND"])],
            re.sub(r"\s+", " ", str(stat[tm]["Descr"])).strip(),
            figures(dyn[tm], dyn[tm]["party"], None),
        )
        for tm in sorted(stat)
    ]


def load(election: Election) -> Data:
    if election.legacy:
        return legacy(election)
    segments: dict[str, str] = {}
    for url in (*election.files, *election.extra):
        artifact = cached(url)
        segments.update(split(decode(artifact.path.read_bytes()), url))
    stations = load_stations(election)
    ep: dict[int, object] = {}
    dhm: dict[int, object] = {}
    den: dict[int, object] = {}
    city: dict[int, object] = {}
    epik: object | None = None
    statics = ""
    parties: dict[int, str] = {}
    names: dict[int, str] = {}
    seats: dict[int, int] = {}
    stat_ep: dict[int, dict[str, object]] = {}
    for name, text in segments.items():
        if name.endswith(("ep_party.js", "model.el.js")):
            continue
        found = re.search(r"/(ep|dhm|den|city|epik)_(\d+)\.js$", name) or re.search(r"/(epik)\.js$", name)
        if name.endswith("/statics.js"):
            statics = text
        elif found is None:
            continue
        else:
            kind = found.group(1)
            index = int(found.group(2)) if found.lastindex == 2 else 1
            if "/stat/" in name:
                if kind == "ep":
                    stat_ep[index] = mapping(json.JSONDecoder().raw_decode(text.strip())[0])
                continue
            if not text.strip():
                continue
            value = json.JSONDecoder().raw_decode(text.strip())[0]
            if isinstance(value, list) and not value:
                continue  # a district the election did not have
            if kind == "ep":
                ep[index] = value
            elif kind == "dhm":
                dhm[index] = value
            elif kind == "den":
                den[index] = value
            elif kind == "city":
                city[index] = value
            elif kind == "epik" and (epik is None or "/dyn1/" in name):
                epik = value
    if epik is None:
        raise LookupError(f"{election.key} has no national file")
    for name, text in segments.items():
        if name.endswith("model.el.js"):
            parties.update(model_parties(text))
    municipality_names: dict[int, tuple[str, int]] = {}
    unit_names: dict[int, tuple[str, int]] = {}
    city_names: dict[int, tuple[str, str]] = {}
    if statics:
        countries = {int(str(row[0])): str(row[1]) for row in bracket(statics, "country")}
        for row in bracket(statics, "city"):
            city_names[int(str(row[0]))] = (str(row[1]), countries.get(int(str(row[2])), ""))
        for row in bracket(statics, "party"):
            parties[int(str(row[0]))] = str(row[2])
        for row in bracket(statics, "ep"):
            names[int(str(row[0]))] = str(row[1])
            seats[int(str(row[0]))] = int(str(row[4]))
        for row in bracket(statics, "dhm"):
            municipality_names[int(str(row[0]))] = (str(row[1]), int(str(row[3])))
        for row in bracket(statics, "den"):
            unit_names[int(str(row[0]))] = (str(row[1]), int(str(row[3])))
    for index, row in stat_ep.items():
        names.setdefault(index, str(row.get("Descr")))
        if index not in seats and row.get("Edres") is not None:
            seats[index] = int(str(row["Edres"]))
    seat_field = "EdresEpik"
    national = figures(epik, mapping(epik)["party"], seat_field)
    districts = {n: figures(row, mapping(row)["party"], "Edres") for n, row in ep.items()}
    if election.family == "euro":
        for row in districts.values():
            row.seats = {}
    return Data(
        national=national,
        districts=districts,
        municipalities={n: figures(row, mapping(row)["party"], None) for n, row in dhm.items()},
        units={n: figures(row, mapping(row)["party"], None) for n, row in den.items()},
        names=names,
        seats=seats,
        parties=parties,
        municipality_names=municipality_names,
        unit_names=unit_names,
        updated=str(mapping(epik).get("Updated") or ""),
        official=bool(mapping(epik).get("official")),
        cities={n: figures(row, mapping(row)["party"], None) for n, row in city.items()},
        city_names=city_names,
        stations=stations,
    )


def model_parties(text: str) -> dict[int, str]:
    return {
        int(found.group(1)): found.group(2) or found.group(3)
        for found in re.finditer(r"""[{,](\d+):\{id:\d+,name:(?:"([^"]+)"|'([^']+)'),shortName:""", text)
    }


# ---------------------------------------------------------------- shared

YPES = key("source", SOURCE, "national")  # apart from the local results service in `ypes`
PARLIAMENT = key("body", "parliament")
GREEK_MONTHS = {
    1: "Ιανουαρίου",
    2: "Φεβρουαρίου",
    3: "Μαρτίου",
    4: "Απριλίου",
    5: "Μαΐου",
    6: "Ιουνίου",
    7: "Ιουλίου",
    8: "Αυγούστου",
    9: "Σεπτεμβρίου",
    10: "Οκτωβρίου",
    11: "Νοεμβρίου",
    12: "Δεκεμβρίου",
}
ENGLISH_MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September"]
ENGLISH_MONTHS += ["October", "November", "December"]
NOT_PARTIES = {INDEPENDENTS, 101, 102, 999}
LOADED: dict[str, Data] = {}


def data(election: Election) -> Data:
    if election.key not in LOADED:
        LOADED[election.key] = load(election)
    return LOADED[election.key]


def packages() -> list[str]:
    return [GEO, REGISTRY, *(e.package for e in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for election in ELECTIONS:
        build(election).write()


def emit() -> None:
    package.run("load", packages())


def citations(p: Package, election: Election, registry_too: bool = False) -> None:
    for url in (*election.files, *election.extra, *election.stations):
        if registry_too:
            p.artifact(cached(url), YPES)
        else:
            p.uses(cached(url))


def parent_runs() -> dict[int, list[tuple[date | None, date | None, int]]]:
    """For each municipality, the districts it belonged to and the days the assignment changed."""
    seen: dict[int, list[tuple[date, int]]] = defaultdict(list)
    for election in sorted((e for e in ELECTIONS if e.key in STATICS), key=lambda e: e.day):
        for code, (_, district) in data(election).municipality_names.items():
            seen[code].append((election.day, district))
    runs: dict[int, list[tuple[date | None, date | None, int]]] = {}
    for code, rows in seen.items():
        spans: list[tuple[date, int]] = []
        for day, district in rows:
            if not spans or spans[-1][1] != district:
                spans.append((day, district))
        runs[code] = [
            (
                None if i == 0 else day,
                spans[i + 1][0] if i + 1 < len(spans) else None,
                district,
            )
            for i, (day, district) in enumerate(spans)
        ]
    return runs


def latest(table: str) -> dict[int, str]:
    """The newest name of each district, municipality, or unit."""
    found: dict[int, str] = {}
    for election in sorted(ELECTIONS, key=lambda e: e.day):
        d = data(election)
        if table == "district":
            found.update(d.names)
        elif table == "municipality":
            found.update({k: v[0] for k, v in d.municipality_names.items()})
        else:
            found.update({k: v[0] for k, v in d.unit_names.items()})
    return found


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "Greece's electoral districts, and the municipalities and municipal units that the Ministry of Interior "
        "reports results for",
    )
    p.external |= {GREECE, TERRITORY}
    p.add("scheme", code="gr.country", polity_id=GREECE, kind="administrative", rank=0, noun="country")
    p.add("scheme", code="gr.district", polity_id=GREECE, kind="electoral_district", rank=1, noun="electoral district")
    p.add("scheme", code="gr.municipality", polity_id=GREECE, kind="administrative", rank=2, noun="municipality")
    p.add("scheme", code="gr.unit", polity_id=GREECE, kind="administrative", rank=3, noun="municipal unit")
    p.add("scheme", code=STATION, polity_id=GREECE, kind="precinct", rank=4, noun="polling station")
    districts = latest("district")
    for code, name in sorted(districts.items()):
        if code == 57:
            continue
        id = area("gr.district", code)
        p.add("area", id=id, scheme_code="gr.district")
        p.add("area_code", area_id=id, code=str(code))
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, name, "el", "official")
    runs = parent_runs()
    municipalities = {
        code for election in ELECTIONS if election.key in UNITS for code in data(election).municipality_names
    }
    for code, name in sorted(latest("municipality").items()):
        if code not in municipalities:
            continue
        id = area("gr.municipality", code)
        p.add("area", id=id, scheme_code="gr.municipality")
        p.add("area_code", area_id=id, code=str(code))
        for since, until, district in runs[code]:
            if district == 57:
                continue
            p.add(
                "area_parent",
                area_id=id,
                parent_id=area("gr.district", district),
                valid_from=since,
                valid_to=until,
            )
        p.name(id, name, "el", "official")
    parents: dict[int, int] = {}
    for election in sorted((e for e in ELECTIONS if e.key in UNITS), key=lambda e: e.day):
        parents.update({k: v[1] for k, v in data(election).unit_names.items()})
    for code, name in sorted(latest("unit").items()):
        if code not in parents:
            continue
        id = area("gr.unit", code)
        p.add("area", id=id, scheme_code="gr.unit")
        p.add("area_code", area_id=id, code=str(code))
        p.add("area_parent", area_id=id, parent_id=area("gr.municipality", parents[code]))
        p.name(id, name, "el", "official")
    return p


def party(pid: int) -> str:
    return key("party", pid)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Greek parties and the Hellenic Parliament, from the Ministry of Interior's results site",
        depends=[GEO],
    )
    p.external.add(GREECE)
    p.add(
        "source",
        id=YPES,
        kind="official_body",
        publisher="Υπουργείο Εσωτερικών (Ministry of Interior)",
        title="Εκλογές: αποτελέσματα (election results)",
        url=SITE,
        license="Official publication; reuse terms are not stated",
        license_url=SITE,
        attribution="Υπουργείο Εσωτερικών",
    )
    for election in ELECTIONS:
        citations(p, election, True)  # every page and file the elections cite
    p.add("body", id=PARLIAMENT, polity_id=GREECE, kind="unicameral")
    p.name(PARLIAMENT, "Hellenic Parliament", "en", "official")
    p.name(PARLIAMENT, "Βουλή των Ελλήνων", "el", "native", False)
    names: dict[int, dict[str, date]] = defaultdict(dict)
    for election in ELECTIONS:
        if election.legacy:
            continue
        d = data(election)
        used = set(d.national.votes) | {pid for f in d.districts.values() for pid in f.votes}
        for pid in used - NOT_PARTIES:
            text = d.parties.get(pid)
            if text:
                names[pid][text.strip()] = max(election.day, names[pid].get(text.strip(), date.min))
    for pid, spellings in sorted(names.items()):
        id = party(pid)
        p.add("party", id=id, kind="party", polity_id=GREECE)
        # ypes_national registers its own party entities under gr.ypes.party with codes that overlap these, so
        # this pipeline keeps a scheme of its own: one scheme and value must never name two entities.
        p.add("external_id", entity_id=id, scheme="gr.ypes.results.party", value=str(pid))
        newest = max(spellings, key=lambda text: spellings[text])
        for text in sorted(spellings):
            p.name(id, text, "el", "official" if text == newest else "ballot", text == newest)
    return p


# ---------------------------------------------------------------- elections

METHOD = {
    "districts": (
        "The Ministry of Interior reports each electoral district's registered voters, ballots, and votes for each "
        "party. The values here are those district figures, one unit for each district."
    ),
    "units": (
        "The Ministry of Interior reports each municipal unit's registered voters, ballots, and votes for each party. "
        "A district's own total is a little larger than the sum of its municipal units, because it also holds ballots "
        "that the Ministry does not attribute to a municipal unit. Those ballots are stored as one aggregate unit for "
        "the district, computed as the district's total less its municipal units, so that the units add up to the "
        "district."
    ),
    "legacy": (
        "The Ministry of Interior's archived page gives the national ballots and each party's share, votes, and seats. "
        "It publishes invalid and blank ballots as one figure, so they are not stored separately, and it gives no "
        "results below the nation in the copy held here."
    ),
    "national": (
        "The values are the figures of every unit in every district. The declared national totals are the Ministry's "
        "own national file, which the values are checked against."
    ),
}


ACCEPTED: dict[str, dict[str, str]] = {
    "eu-1999": {
        "accounting.votes_valid": (
            "The Ministry's archived page lists 6,428,696 valid ballots, and its party table adds up to 6,429,036, "
            "340 more; the page gives no correction."
        )
    },
    "ref-2015": {
        "accounting.turnout": (
            "One municipal unit's own figures list 18 ballots against 17 registered voters; the Ministry's file "
            "gives no correction."
        )
    },
}


@dataclass
class Leaf:
    unit: str
    figures: Figures
    flags: int = 0


def subtract(whole: Figures, parts: list[Figures]) -> Figures:
    return Figures(
        registered=whole.registered - sum(x.registered for x in parts),
        valid=whole.valid - sum(x.valid for x in parts),
        invalid=whole.invalid - sum(x.invalid for x in parts),
        blank=whole.blank - sum(x.blank for x in parts),
        votes={pid: v - sum(x.votes.get(pid, 0) for x in parts) for pid, v in whole.votes.items()},
        seats={},
    )


GREEK = dict(
    zip(
        "αβγδεζηθικλμνξοπρσςτυφχψω",
        ("a", "v", "g", "d", "e", "z", "i", "th", "i", "k", "l", "m", "n", "x", "o", "p", "r", "s", "s")
        + ("t", "y", "f", "ch", "ps", "o"),
        strict=True,
    )
)


def latin(text: str) -> str:
    """Greek text in Latin letters, without accents."""
    plain = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))
    plain = plain.replace("ου", "ou")
    return "".join(GREEK.get(c, c) for c in plain)


def label(election: Election) -> tuple[str, str]:
    same = [e for e in ELECTIONS if e.family == election.family and e.day.year == election.day.year]
    day = election.day
    if len(same) > 1:
        return f"{ENGLISH_MONTHS[day.month]} {day.year}", f"{GREEK_MONTHS[day.month]} {day.year}"
    return str(day.year), str(day.year)


class Build:
    def __init__(self, election: Election) -> None:
        self.election = election
        self.d = data(election)
        self.p = Package(
            election.package,
            SOURCE,
            f"The {election.day.year} {'European Parliament' if election.family == 'euro' else 'Greek parliamentary'} "
            "election, national result, from the Ministry of Interior's archived results page"
            if election.legacy
            else {
                "parliament": "The {} Greek parliamentary election by electoral district and municipal unit",
                "euro": "The {} European Parliament election in Greece by electoral district and municipal unit",
                "referendum": "The {} Greek referendum by electoral district and municipal unit",
            }[election.family].format(election.day.year)
            + (", down to the polling station," if election.stations else "")
            + " from the Ministry of Interior's results site"
            if election.key in UNITS
            else {
                "parliament": "The {} Greek parliamentary election by electoral district",
                "euro": "The {} European Parliament election in Greece by electoral district",
                "referendum": "The {} Greek referendum by electoral district",
            }[election.family].format(election.day.year)
            + " from the Ministry of Interior's results site",
            depends=[GEO, REGISTRY],
        )
        self.p.external |= {GREECE, TERRITORY}
        citations(self.p, election)
        self.id = key("election", election.key)
        self.units: dict[str, Leaf] = {}
        self.leaves: dict[int, list[Leaf]] = defaultdict(list)
        self.everything: list[Leaf] = []
        self.values = 0
        self.sets: list[str] = []
        self.stage = "official" if self.d.official else "preliminary"

    def unit(
        self,
        scheme: str,
        code: str,
        where: str | None,
        relation: str,
        kind: str,
        parent: str | None = None,
        name: str | None = None,
    ) -> str:
        id = key("unit", self.election.key, scheme, code)
        self.p.add(
            "reporting_unit",
            id=id,
            polity_id=GREECE,
            scheme_code=scheme,
            code=f"{self.election.day}:{code}",
            area_relation=relation,
            area_id=where,
            kind=kind,
            parent_id=parent,
        )
        if name:
            self.p.name(id, name, "el", "official")
        return id

    def build(self) -> Package:
        e, d, p = self.election, self.d, self.p
        english, greek = label(e)
        title = {
            "parliament": (f"{english} Greek parliamentary election", f"Βουλευτικές εκλογές {greek}"),
            "euro": (f"{english} European Parliament election in Greece", f"Ευρωεκλογές {greek}"),
            "referendum": (
                f"{e.day.day} {ENGLISH_MONTHS[e.day.month]} {e.day.year} Greek referendum",
                f"Δημοψήφισμα {e.day.day}ης {GREEK_MONTHS[e.day.month]} {e.day.year}",
            ),
        }[e.family]
        p.add(
            "election",
            id=self.id,
            polity_id=GREECE,
            kind="referendum" if e.family == "referendum" else "general",
            date_from=e.day,
            date_to=e.day,
            date_precision="day",
            status="held",
        )
        p.name(self.id, title[0], "en", "official")
        p.name(self.id, title[1], "el", "native", False)
        p.add("slug", path=e.path, entity_id=self.id, is_current=True)
        self.units_and_leaves()
        if e.legacy:
            self.national_contest(
                "national",
                ("National result", "Εθνικό αποτέλεσμα"),
                body=PARLIAMENT if e.family == "parliament" else None,
                kind="list_allocation",
            )
        elif e.family == "parliament":
            for n in sorted(d.districts):
                if n != 57 and self.leaves.get(n):
                    self.district_contest(n)
            self.national_contest(
                "state-list",
                ("State list", "Επικρατείας"),
                body=PARLIAMENT,
                kind="list_allocation",
            )
        elif e.family == "euro":
            self.national_contest(
                "national",
                ("Greek seats in the European Parliament", "Έδρες Ελλάδας στο Ευρωπαϊκό Κοινοβούλιο"),
                body=None,
                kind="list_allocation",
            )
        else:
            self.national_contest(
                "referendum",
                ("Referendum", "Δημοψήφισμα"),
                body=None,
                kind="ballot_question",
            )
        return p

    def units_and_leaves(self) -> None:
        e, d = self.election, self.d
        if e.legacy:
            leaf = Leaf(self.unit("gr.country", "national", TERRITORY, "equals", "standard"), d.national)
            self.leaves[0].append(leaf)
        elif d.stations:
            self.station_leaves()
        elif e.key in UNITS:
            for code, figures_ in sorted(d.units.items()):
                municipality = d.unit_names[code][1]
                n = d.municipality_names[municipality][1]
                leaf = Leaf(self.unit("gr.unit", str(code), area("gr.unit", code), "equals", "standard"), figures_)
                self.leaves[n].append(leaf)
            for n, whole in sorted(d.districts.items()):
                if n == ABROAD and d.cities:
                    for code, figures_ in sorted(d.cities.items()):
                        name, country = d.city_names[code]
                        tag = f"abroad:{code}:{latin(country)}:{latin(name)}"
                        abroad = self.unit("gr.district", tag, None, "unlocated", "abroad_mission")
                        self.leaves[n].append(Leaf(abroad, figures_))
                    rest = subtract(whole, [x.figures for x in self.leaves[n]])
                    if rest.valid or rest.registered or rest.invalid or rest.blank:
                        residual = self.unit("gr.district", "abroad:residual", None, "unlocated", "aggregate")
                        self.leaves[n].append(Leaf(residual, rest, DERIVED))
                    continue
                if n == ABROAD:
                    abroad = self.unit("gr.district", str(n), None, "unlocated", "abroad_mission")
                    self.leaves[n].append(Leaf(abroad, whole))
                    continue
                rest = subtract(whole, [x.figures for x in self.leaves[n]])
                if rest.valid or rest.registered or rest.invalid or rest.blank:
                    code = f"residual:{n}"
                    leaf = Leaf(
                        self.unit("gr.district", code, area("gr.district", n), "within", "aggregate"), rest, DERIVED
                    )
                    self.leaves[n].append(leaf)
        else:
            for n, whole in sorted(d.districts.items()):
                if n == 57:
                    leaf = Leaf(self.unit("gr.district", str(n), None, "unlocated", "abroad_mission"), whole)
                else:
                    leaf = Leaf(self.unit("gr.district", str(n), area("gr.district", n), "equals", "standard"), whole)
                self.leaves[n].append(leaf)
        self.everything = [leaf for n in sorted(self.leaves) for leaf in self.leaves[n]]
        rest = subtract(d.national, [x.figures for x in self.everything])
        self.rest = rest

    def station_leaves(self) -> None:
        """Each polling station is a leaf under its municipal unit, or under its district when it has no unit.

        The stations of a municipal unit must add up to the unit's own file, which is checked here.
        """
        d = self.d
        held: dict[int, list[Figures]] = defaultdict(list)
        for s in d.stations:
            held[s.unit].append(s.figures)
        for code, parts in sorted(held.items()):
            if code:
                if code not in d.units:
                    raise ValueError(f"{self.election.key}: stations lie in unit {code}, which has no file")
                gap = subtract(d.units[code], parts)
                if gap.registered or gap.valid or gap.invalid or gap.blank or any(gap.votes.values()):
                    raise ValueError(f"{self.election.key}: unit {code}'s stations differ from its file")
        if set(d.units) - set(held):
            raise ValueError(f"{self.election.key}: units without stations: {sorted(set(d.units) - set(held))[:5]}")
        districts = {
            n: self.unit("gr.district", str(n), area("gr.district", n), "equals", "aggregate")
            for n in sorted({s.district for s in d.stations})
        }
        parents: dict[int, str] = {}
        for code, (_, municipality) in sorted(d.unit_names.items()):
            if code in held:
                n = d.municipality_names[municipality][1]
                parents[code] = self.unit(
                    "gr.unit", str(code), area("gr.unit", code), "equals", "aggregate", districts[n]
                )
        for s in d.stations:
            if s.unit:
                parent, where = parents[s.unit], area("gr.unit", s.unit)
            else:
                parent, where = districts[s.district], area("gr.district", s.district)
            leaf = self.unit(STATION, str(s.id), where, "within", s.kind, parent, s.name)
            self.leaves[s.district].append(Leaf(leaf, s.figures))
        for n, whole in sorted(d.districts.items()):
            rest = subtract(whole, [x.figures for x in self.leaves[n]])
            if rest.valid or rest.registered or rest.invalid or rest.blank or any(rest.votes.values()):
                raise ValueError(f"{self.election.key}: district {n} differs from its stations")

    def result_set(self, suffix: str, contest: str, note: str) -> str:
        id = key("result_set", self.election.key, suffix)
        self.p.add(
            "result_set",
            id=id,
            election_id=self.id,
            contest_id=contest,
            stage=self.stage,
            kind="counted",
            source_id=YPES,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        self.p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
        self.sets.append(id)
        return id

    def contest(
        self,
        suffix: str,
        title: tuple[str, str],
        *,
        kind: str,
        body: str | None,
        where: str,
        magnitude: int | None,
        transliterated: bool = False,
    ) -> str:
        id = key("contest", self.election.key, suffix)
        self.p.add(
            "contest",
            id=id,
            election_id=self.id,
            kind=kind,
            body_id=body,
            district_area_id=where,
            magnitude=magnitude,
            round_number=1,
            default_result_kind="counted",
        )
        if kind == "ballot_question":
            self.p.add("ballot_question", contest_id=id, type="referendum")
        self.p.name(id, title[0], "en", "transliteration" if transliterated else "official")
        self.p.name(id, title[1], "el", "native", False)
        self.p.add("slug", path=f"{self.election.path}/{changeset.slug(title[0])}", entity_id=id, is_current=True)
        return id

    def options(self, contest: str, suffix: str, pids: list[int]) -> dict[int, str]:
        options: dict[int, str] = {}
        for pid in pids:
            id = options[pid] = key("option", self.election.key, suffix, pid)
            if self.election.family == "referendum":
                kind = "answer"
            elif pid in NOT_PARTIES:
                kind = "others"
            else:
                kind = "list"
            self.p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind=kind,
                party_id=party(pid) if kind == "list" and not self.election.legacy else None,
                ballot_label=self.d.parties.get(pid, f"Party {pid}").strip(),
            )
        return options

    def value(self, rs: str, unit: str, option: str | None, measure: int, value: int, flags: int) -> None:
        self.values += 1
        self.p.add(
            "result_value",
            result_set_id=rs,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=flags,
            value=value,
        )

    def emit(self, rs: str, leaf: Leaf, options: dict[int, str], *, rest: bool = False) -> None:
        f = leaf.figures
        for measure, value in zip(BLANK_MEASURES, (f.registered, f.cast, f.valid, f.invalid, f.blank), strict=True):
            if f.ballots is not None and measure in (5, 6):
                continue
            if value > 0 or (value == 0 and not rest):
                self.value(rs, leaf.unit, None, measure, value, leaf.flags)
        for pid, value in sorted(f.votes.items()):
            if value > 0 or (value == 0 and not rest and pid in options):
                if pid not in options:
                    raise ValueError(f"{self.election.key}: {value} votes for party {pid}, which is not on the list")
                self.value(rs, leaf.unit, options[pid], 1, value, leaf.flags)

    def district_contest(self, n: int) -> None:
        d = self.d
        name = d.names[n]
        seats = d.seats.get(n)
        contest = self.contest(
            f"district-{n}",
            (latin(name).title(), name),
            kind="list_allocation",
            body=PARLIAMENT,
            where=area("gr.district", n),
            magnitude=seats,
            transliterated=True,
        )
        rs = self.result_set(
            f"district-{n}",
            contest,
            METHOD["units" if self.election.key in UNITS else "districts"],
        )
        pids = sorted(
            set(d.districts[n].votes) | {pid for leaf in self.leaves[n] for pid in leaf.figures.votes},
            key=lambda pid: -d.districts[n].votes.get(pid, 0),
        )
        options = self.options(contest, f"district-{n}", pids)
        for leaf in self.leaves[n]:
            self.emit(rs, leaf, options, rest=bool(leaf.flags))
        for pid, count in sorted(d.districts[n].seats.items()):
            self.p.add("outcome", result_set_id=rs, option_id=options[pid], kind="won", seats=count)

    def national_contest(self, suffix: str, title: tuple[str, str], *, body: str | None, kind: str) -> None:
        d = self.d
        seats = sum(d.national.seats.values()) or None
        contest = self.contest(
            suffix, title, kind=kind, body=body, where=TERRITORY, magnitude=seats if kind != "ballot_question" else None
        )
        rs = self.result_set(
            suffix,
            contest,
            METHOD["legacy"]
            if self.election.legacy
            else METHOD["national"] + " " + METHOD["units" if self.election.key in UNITS else "districts"],
        )
        pids = sorted(
            set(d.national.votes) | {pid for leaf in self.everything for pid in leaf.figures.votes},
            key=lambda pid: -d.national.votes.get(pid, 0),
        )
        options = self.options(contest, suffix, pids)
        for leaf in self.everything:
            self.emit(rs, leaf, options, rest=bool(leaf.flags))
        if d.cities:
            self.p.add(
                "note",
                id=key("note", rs, "abroad"),
                entity_id=rs,
                kind="methodology",
                body_md=(
                    "Votes cast at polling stations abroad are stored for each city the Ministry lists, one unit "
                    "for each, with no mapped area. The cities add up to the abroad district's own figures."
                ),
                lang="en",
            )
        rest = self.rest
        if rest.valid > 0 or rest.invalid > 0 or rest.blank > 0 or any(v > 0 for v in rest.votes.values()):
            unit = self.unit("gr.country", "residual", TERRITORY, "within", "aggregate")
            leaf = Leaf(unit, rest, DERIVED)
            self.emit(rs, leaf, options, rest=True)
            self.p.add(
                "note",
                id=key("note", rs, "residual"),
                entity_id=rs,
                kind="caveat",
                body_md=(
                    "The national file holds ballots that the district files do not, such as postal ballots of "
                    "voters abroad. They are stored as one aggregate unit for the country, computed as the "
                    "national total less the districts."
                ),
                lang="en",
            )
        for measure, value in (
            (4, d.national.valid),
            (5, d.national.invalid),
            (6, d.national.blank),
            (3, d.national.cast),
        ):
            if d.national.ballots is not None and measure in (5, 6):
                continue
            self.p.add("declared", result_set_id=rs, option_id=None, measure=measure, value=value)
        if rest.registered >= 0:
            self.p.add("declared", result_set_id=rs, option_id=None, measure=2, value=d.national.registered)
        else:
            self.p.add(
                "note",
                id=key("note", rs, "registered"),
                entity_id=rs,
                kind="caveat",
                body_md=(
                    f"The national file gives {d.national.registered:,} registered voters, and the districts' own "
                    f"figures add up to {d.national.registered - rest.registered:,}. The two do not agree, so "
                    "registered voters are not declared nationally."
                ),
                lang="en",
            )
        for pid, value in d.national.votes.items():
            self.p.add("declared", result_set_id=rs, option_id=options[pid], measure=1, value=value)
        for pid, count in sorted(d.national.seats.items()):
            self.p.add("outcome", result_set_id=rs, option_id=options[pid], kind="won", seats=count)


def build(election: Election) -> Package:
    package_ = Build(election).build()
    if election.key in ACCEPTED:
        for code, reason in ACCEPTED[election.key].items():
            package_.accept(code, reason)
    return package_
