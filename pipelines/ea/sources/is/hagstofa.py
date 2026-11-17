"""Iceland's elections from Statistics Iceland's PxWeb tables, down to the municipality."""

import importlib
import json
import os
import re
import subprocess
import unicodedata
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from html import unescape
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "is.hagstofa"
API = "https://px.hagstofa.is/pxen/api/v1/en/Ibuar/kosningar"
ICELAND = str(uuid5(package.NAMESPACE, "polity\x1fIS"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fISL"))

COMMISSION = "is.landskjorstjorn"
NOTICES = {
    "count": "https://island.is/s/landskjorstjorn/frett/tilkynning-um-nidurstodu-talningar-vid-thjodaratkvaedagreidslu-29-agust-2026",
    "final": "https://island.is/s/landskjorstjorn/frett/landskjorstjorn-tilkynnir-nidurstodu-thjodaratkvaedagreidslu",
}
REPORT = (
    "https://assets.ctfassets.net/8k0h54kbe6bj/79iCOCyZXCBCCV8k6jz0QG/5ef76afbf75aebefad481fdc853f5ae3/"
    "0ae957db-7952-4e0b-bfbc-5992a7fdcbb4.pdf"
)
TABLES = {
    # Althing
    "KOS02121": "althingi/althurslit",
    "KOS02124": "althingi/althurslit",
    "KOS02122": "althingi/althurslit",
    "KOS02118c": "althingi/althurslit",
    "KOS02118b": "althingi/althurslit",
    "KOS02118a": "althingi/althurslit",
    "KOS02019": "althingi/althurslit",
    "KOS02018": "althingi/althurslit",
    "KOS02123": "althingi/althurslit",
    "KOS02101": "althingi/althkjosendur",
    "KOS02100": "althingi/althkjosendur",
    "KOS02057": "althingi/althkjornir",
    # presidential
    "KOS07100": "forsetakosningar",
    "KOS07102": "forsetakosningar",
    "KOS07104": "forsetakosningar",
    "KOS07110": "forsetakosningar",
    "KOS07112": "forsetakosningar",
    "KOS07002": "forsetakosningar",
    "KOS07000": "forsetakosningar",
    # referenda
    "KOS04101": "thjodaratkv",
    "KOS04102": "thjodaratkv",
    "KOS04103": "thjodaratkv",
    "KOS04105": "thjodaratkv",
    "KOS04108": "thjodaratkv",
    "KOS04109": "thjodaratkv",
    # overview
    "KOS01900": "kosningayfirlit",
    "KOS02000": "kosningayfirlit",
    "KOS02001": "kosningayfirlit",
    "KOS02002": "kosningayfirlit",
    "KOS02003": "kosningayfirlit",
    "KOS02004": "kosningayfirlit",
    "KOS02005": "kosningayfirlit",
    "KOS02006": "kosningayfirlit",
    # local government
    "KOS03101": "sveitastjorn/svf_yfirlit",
    "KOS03103": "sveitastjorn/svf_yfirlit",
    "KOS03107": "sveitastjorn/svf_yfirlit",
    "KOS03201": "sveitastjorn/svf_urslit",
    "KOS03202": "sveitastjorn/svf_urslit",
    "KOS03203": "sveitastjorn/svf_urslit",
    "KOS03204": "sveitastjorn/svf_urslit",
    "KOS03204a": "sveitastjorn/svf_urslit",
    "KOS03204b": "sveitastjorn/svf_urslit",
    "KOS03205": "sveitastjorn/svf_urslit",
    "KOS03206": "sveitastjorn/svf_urslit",
    "KOS03207": "sveitastjorn/svf_urslit",
    "KOS03208": "sveitastjorn/svf_urslit",
    "KOS03208a": "sveitastjorn/svf_urslit",
    "KOS03210": "sveitastjorn/svf_urslit",
    "KOS03211": "sveitastjorn/svf_urslit",
    "KOS03212": "sveitastjorn/svf_urslit",
    "KOS03213": "sveitastjorn/svf_urslit",
    "KOS03214": "sveitastjorn/svf_urslit",
    "KOS03214a": "sveitastjorn/svf_urslit",
    "KOS03302": "sveitastjorn/svf_frambj",
    "KOS03304": "sveitastjorn/svf_frambj",
    "KOS03305": "sveitastjorn/svf_frambj",
    "KOS03306": "sveitastjorn/svf_frambj",
    "KOS03307": "sveitastjorn/svf_frambj",
    "KOS03308": "sveitastjorn/svf_frambj",
}


def url(table: str) -> str:
    return f"{API}/{TABLES[table]}/{table}.px"


def body(table: str) -> dict[str, object]:
    variables = json.loads(raw.cached(SOURCE, url(table)).path.read_bytes())["variables"]
    return {
        "query": [{"code": v["code"], "selection": {"filter": "all", "values": ["*"]}} for v in variables],
        "response": {"format": "json-stat2"},
    }


def fetch() -> None:
    for notice in NOTICES.values():
        raw.fetch(COMMISSION, notice, suffix=".html")
    raw.fetch(COMMISSION, REPORT, suffix=".pdf")
    for table in TABLES:
        raw.fetch(SOURCE, url(table), suffix=".json")
        raw.fetch(SOURCE, url(table), suffix=".json", body=body(table))


# ---------------------------------------------------------------- tables


class Grid:
    """One json-stat2 table: dimension names, their labels, and cells addressed by a label for each dimension."""

    def __init__(self, document: dict[str, object]) -> None:
        self.title = str(document.get("label", ""))
        ids = document["id"]
        assert isinstance(ids, list)
        self.dims: list[str] = [str(d) for d in ids]
        dimension = document["dimension"]
        assert isinstance(dimension, dict)
        self.labels: dict[str, list[str]] = {}
        for dim in self.dims:
            category = dimension[dim]["category"]
            index, label = category["index"], category["label"]
            order = sorted(index, key=lambda k: index[k]) if isinstance(index, dict) else index
            self.labels[dim] = [str(label[k]) for k in order]
        sizes = [len(self.labels[d]) for d in self.dims]
        values = document["value"]
        assert isinstance(values, list) and len(values) == _product(sizes)
        self.cells: dict[tuple[str, ...], float | None] = {}
        for position, combo in enumerate(_combos([self.labels[d] for d in self.dims])):
            self.cells[combo] = values[position]

    def get(self, **where: str) -> int | None:
        """The one integer cell that matches the labels given for every dimension."""
        key = tuple(where[d] for d in self.dims)
        value = self.cells[key]
        if value is None:
            return None
        if value != int(value):
            raise ValueError(f"{self.title}: {key} is {value}, not a count")
        return int(value)


def _product(sizes: list[int]) -> int:
    n = 1
    for size in sizes:
        n *= size
    return n


def _combos(lists: list[list[str]]) -> Iterator[tuple[str, ...]]:
    if not lists:
        yield ()
        return
    for head in lists[0]:
        for rest in _combos(lists[1:]):
            yield (head, *rest)


_GRIDS: dict[str, Grid] = {}


def grid(table: str) -> Grid:
    if table not in _GRIDS:
        _GRIDS[table] = Grid(json.loads(raw.cached(SOURCE, url(table), body(table)).path.read_bytes()))
    return _GRIDS[table]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\x96", "-").replace("Ž", "'").replace("´", "'")
    return " ".join(text.split())


# ---------------------------------------------------------------- dates

MONTHS = {
    name: number
    for number, name in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        1,
    )
}


@dataclass(frozen=True)
class Day:
    first: date
    last: date
    precision: str
    text: str


def day_of(label: str) -> Day:
    """A date from a label such as '1944, 20-23 May Referendum', '1880, September', or '1874, autumn'."""
    text = clean(label)
    match = re.match(r"^(\d{4})\.?,?\s*(.*)$", text)
    assert match, label
    year, rest = int(match.group(1)), match.group(2).lower()
    month = next((n for name, n in MONTHS.items() if name in rest), None)
    days = re.search(r"(\d{1,2})(?:\s*-\s*(\d{1,2}))?\.?\s+(?:" + "|".join(MONTHS) + ")", rest)
    if month is None:
        return Day(date(year, 1, 1), date(year, 12, 31), "year", text)
    if days is None:
        end = date(year + (month == 12), month % 12 + 1, 1)
        return Day(date(year, month, 1), date.fromordinal(end.toordinal() - 1), "month", text)
    first = int(days.group(1))
    last = int(days.group(2) or first)
    return Day(date(year, month, first), date(year, month, last), "day", text)


# ---------------------------------------------------------------- names

REGIONS = {
    "Whole country",
    "Whole Country",
    "Iceland total",
    "Total",
    "Capital area",
    "Capital region",
    "Southwest",
    "West",
    "Westfjord",
    "Westfjords",
    "Northwest",
    "Northeast",
    "East",
    "South",
    "Soutwest",
    "Suðurland",
}
ALIASES = {
    "Akraneskaupstaður": "Akranes",
    "Akureyrarkaupstaður": "Akureyri",
    "Akureyrarbær": "Akureyri",
    "Akureyarbær": "Akureyri",
    "Blönduósbær": "Blönduóssbær",
    "Bolungarvíkurkaupstaður": "Bolungarvík",
    "Grindavíkurbær": "Grindavík",
    "Grindarvíkurbær": "Grindavík",
    "Grundafjarðarbær": "Grundarfjarðarbær",
    "Grímsnes- og Grafningshr.": "Grímsnes- og Grafningshreppur",
    "Hafnarfjarðarkaupstaður": "Hafnarfjörður",
    "Hveragerðisbær": "Hveragerði",
    "Kópavogsbær": "Kópavogur",
    "Reykjavíkurborg": "Reykjavík",
    "Sandgerðisbær": "Sandgerði",
    "Seltjarnarnesbær": "Seltjarnarnes",
    "Seltjarnarneskaupstaður": "Seltjarnarnes",
    "Seyðisfjarðarkaupstaður": "Seyðisfjörður",
    "Sveitarfélagið Skagafjörður": "Skagafjörður",
    "Skagaströnd": "Sveitarfélagið Skagaströnd",
    "Skeiða og Gnúpverjahreppur": "Skeiða- og Gnúpverjahreppur",
    "Sveitarfélagið Stykkishólmur": "Stykkishólmur",
    "Stykkishólmsbær": "Stykkishólmur",
    "Vestmannaeyjabær": "Vestmannaeyjar",
    "Ólafsfjörður": "Ólafsfjarðarbær",
    "Akrahreppur og Sveitarfélagið Skagafjörður": "Skagafjörður",
    "Kjósahreppur": "Kjósarhreppur",
    "Blönduós": "Blönduóssbær",
    "Seyðiafjarðarkaupstaður": "Seyðisfjörður",
    "Fjarðabyggð (Breiðdalshreppur meðtalinn)": "Fjarðabyggð",
    "Sandgerðisbær og Garður": "Sandgerði og Garður",
}
CONSTITUENCIES = {
    # code: (name, native name, valid from, valid to)
    "reykjavik-1959": ("Reykjavík", None),
    "reykjanes-1959": ("Reykjanes", None),
    "west-1959": ("West", "Vesturland"),
    "westfjords-1959": ("Westfjords", "Vestfirðir"),
    "northwest-1959": ("Northwest", "Norðurland vestra"),
    "northeast-1959": ("Northeast", "Norðurland eystra"),
    "east-1959": ("East", "Austurland"),
    "south-1959": ("South", "Suðurland"),
    "northwest-2003": ("Northwest", "Norðvesturkjördæmi"),
    "northeast-2003": ("Northeast", "Norðausturkjördæmi"),
    "south-2003": ("South", "Suðurkjördæmi"),
    "southwest-2003": ("Southwest", "Suðvesturkjördæmi"),
    "reykjavik-south-2003": ("Reykjavík south", "Reykjavíkurkjördæmi suður"),
    "reykjavik-north-2003": ("Reykjavík north", "Reykjavíkurkjördæmi norður"),
}
OLD = {
    "Reykjavík": "reykjavik-1959",
    "Reykjanes": "reykjanes-1959",
    "West": "west-1959",
    "Westfjords": "westfjords-1959",
    "Westfjord": "westfjords-1959",
    "Northwest": "northwest-1959",
    "North West": "northwest-1959",
    "Northeast": "northeast-1959",
    "North East": "northeast-1959",
    "East": "east-1959",
    "South": "south-1959",
}
NEW = {
    "Northwest": "northwest-2003",
    "Northeast": "northeast-2003",
    "Notheast": "northeast-2003",
    "South": "south-2003",
    "Southwest": "southwest-2003",
    "Reykjavík south": "reykjavik-south-2003",
    "Reykjavík north": "reykjavik-north-2003",
    "Reykjavík (south)": "reykjavik-south-2003",
    "Reykjavík (north)": "reykjavik-north-2003",
}

LOCAL_DAYS = {
    2002: date(2002, 5, 25),
    2006: date(2006, 5, 27),
    2010: date(2010, 5, 29),
    2014: date(2014, 5, 31),
    2018: date(2018, 5, 26),
    2022: date(2022, 5, 14),
}

MEASURE = {"votes": 1, "registered": 2, "cast": 3, "valid": 4, "invalid": 5, "blank": 6}


def canon(name: str) -> str:
    text = clean(name)
    return ALIASES.get(text, text)


def constituency(label: str, era: str) -> str | None:
    text = clean(label)
    text = re.sub(r" constituency$| total$", "", text)
    text = re.sub(r"^Constituency of ", "", text).replace("sourth", "south")
    if era == "old":
        return OLD.get(text)
    return NEW.get(text) if "(" not in text else None


def is_region(label: str) -> bool:
    text = clean(label)
    return (
        text in REGIONS
        or text.endswith((" total", " constituency"))
        or text.startswith(("Constituency of ", "Reykjavík south", "Reykjavík north"))
    )


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


# ---------------------------------------------------------------- parties

PARTY_FIXES = {"alliance": "social democratic alliance"}
OTHERS = {"other", "others", "others and outside parties"}
INDEPENDENTS = {"outside parties"}
NATIVE = {
    "independence party": "Sjálfstæðisflokkurinn",
    "progressive party": "Framsóknarflokkurinn",
    "social democratic alliance": "Samfylkingin",
    "left-green movement": "Vinstrihreyfingin – grænt framboð",
    "pirate party": "Píratar",
    "reform": "Viðreisn",
    "central party": "Miðflokkurinn",
    "people's party": "Flokkur fólksins",
    "socialist party of iceland": "Sósíalistaflokkur Íslands",
    "social democratic party": "Alþýðuflokkurinn",
    "people's alliance": "Alþýðubandalagið",
    "women's alliance": "Samtök um kvennalista",
    "bright future": "Björt framtíð",
    "liberal party": "Frjálslyndi flokkurinn",
}
LOCAL_PARTIES = [
    (r"sjálfstæðisflokk", "independence party"),
    (r"framsóknarflokk|^framsókn$", "progressive party"),
    (r"samfylking", "social democratic alliance"),
    (r"vinstri.?hreyfing.*græn|vinstri.?græn", "left-green movement"),
    (r"píratar", "pirate party"),
    (r"viðreisn", "reform"),
    (r"miðflokk", "central party"),
    (r"flokk(?:ur|s) fólksins", "people's party"),
    (r"sósíalistaflokk", "socialist party of iceland"),
    (r"alþýðuflokk", "social democratic party"),
    (r"alþýðubandalag", "people's alliance"),
    (r"kvennalist", "women's alliance"),
    (r"björt framtíð", "bright future"),
    (r"frjálslyndi flokk", "liberal party"),
]
ALTHING_NAMES = {
    "KOS02121": "Political organisation",
    "KOS02124": "Political party",
    "KOS02122": "Political organisation",
    "KOS02118c": "Political organisation",
    "KOS02118b": "Political organisation",
    "KOS02118a": "Political organisation",
    "KOS02019": "Political organisation",
    "KOS02018": "Political organisation",
}


def party_text(label: str) -> str:
    text = clean(label).replace("Peoples Party", "People's Party")
    return re.sub(r"\s*\([A-ZÞÆÖÐÁÉÍÓÚÝ]\)$", "", text)


def party_id_of(label: str) -> str:
    text = party_text(label).lower()
    text = re.sub(r"^the ", "", text)
    return PARTY_FIXES.get(text, text)


def party(pid: str) -> str:
    return key("party", pid)


def parties() -> dict[str, str]:
    """Every national party named in an Althing table, by its normalized key, with the longest printed name."""
    found: dict[str, str] = {}
    for table, dim in ALTHING_NAMES.items():
        for label in grid(table).labels[dim]:
            text = party_text(label)
            pid = party_id_of(label)
            if text in ("Total", "Valid votes") or pid in OTHERS or pid in INDEPENDENTS:
                continue
            if len(text) > len(found.get(pid, "")) or (pid in PARTY_FIXES.values() and text.startswith("The Social")):
                found[pid] = text
    return found


def local_party(text: str) -> str | None:
    """The national party a local list names, when it names exactly one and nobody else."""
    rest = re.sub(r"^[A-ZÞÆÖÐÁÉÍÓÚÝ]{1,2}\s*[-–]?\s*(?:listi|listinn)?\s*[-–]?\s*", "", clean(text)).lower()
    rest = re.sub(r"^(?:b|d|s|v|p|c|m|f|j)[-\s]*(?:listi|listinn)\s*", "", rest)
    if " og " in rest or "," in rest:
        return None
    hits = {pid for pattern, pid in LOCAL_PARTIES if re.search(pattern, rest)}
    return hits.pop() if len(hits) == 1 else None


# ---------------------------------------------------------------- geography

MUNI_TABLES = {
    "KOS02101": "Municipality",
    "KOS07002": "Constituency/Municipality",
    "KOS04105": "Constituency/Municipality",
    **{t: "Municipality" for t in ("KOS03201", "KOS03202", "KOS03203", "KOS03204", "KOS03204a", "KOS03204b")},
    **{t: "Municipality" for t in ("KOS03205", "KOS03206", "KOS03207", "KOS03208", "KOS03208a")},
}


def hierarchy(table: str) -> list[tuple[str | None, str, str]]:
    """The municipalities of a table as (constituency, canonical name, printed label), skipping its total rows."""
    out: list[tuple[str | None, str, str]] = []
    current: str | None = None
    for label in grid(table).labels[MUNI_TABLES[table]]:
        text = clean(label)
        if text in REGIONS and not text.endswith(" total"):
            continue
        if text.endswith((" constituency", " total")) or text.startswith("Constituency of "):
            current = constituency(text, "new")
            continue
        if text in ("Reykjavík south", "Reykjavík north"):
            current = NEW[text]
            text = f"Reykjavík ({text.split()[1]})"
        out.append((current, canon(text), label))
    return out


def municipalities() -> list[str]:
    names = {name for table in MUNI_TABLES for _, name, _ in hierarchy(table)}
    names |= {"Reykjavík"}
    return sorted(names)


def geo() -> Package:
    p = Package(
        "is.hagstofa/geo",
        SOURCE,
        "Iceland's Althing constituencies of 1959 to 1999 and since 2003, and the municipalities that Statistics "
        "Iceland names in its election tables",
    )
    p.external |= {ICELAND, TERRITORY}
    p.add("scheme", code="is.landid", polity_id=ICELAND, kind="administrative", rank=0, noun="country")
    p.add("scheme", code="is.kjordaemi", polity_id=ICELAND, kind="electoral_district", rank=1, noun="constituency")
    p.add("scheme", code="is.sveitarfelag", polity_id=ICELAND, kind="administrative", rank=2, noun="municipality")
    for code, (english, native) in CONSTITUENCIES.items():
        id = area("is.kjordaemi", code)
        p.add("area", id=id, scheme_code="is.kjordaemi")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, english, "en", "official")
        if native:
            p.name(id, native, "is", "native", False)
    for name in municipalities():
        id = area("is.sveitarfelag", name)
        p.add("area", id=id, scheme_code="is.sveitarfelag")
        p.add("area_code", area_id=id, code=name)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, name, "is", "official")
    return p


# ---------------------------------------------------------------- registry

STATS = key("source", SOURCE)
COMMISSION_SOURCE = key("source", COMMISSION)
ALTHING_BODY = key("body", "althing")
COUNCIL_BODY = key("body", "municipal-council")
PRESIDENCY = key("office", "president")
SYSTEM_1959 = key("electoral_system", "althing", 1959)
SYSTEM_2003 = key("electoral_system", "althing", 2003)
SYSTEM_LOCAL = key("electoral_system", "local")
SYSTEM_PRESIDENT = key("electoral_system", "president")
SYSTEM_REFERENDUM = key("electoral_system", "referendum")
LOCAL_NOTE = (
    "Each municipal council is elected by proportional representation from candidate lists, or, in the "
    "municipalities with no list, by direct personal voting."
)
SYSTEMS = [
    (
        SYSTEM_1959,
        date(1959, 1, 1),
        date(2003, 1, 1),
        {"formula": "dhondt"},
        (
            "Constituency seats are allocated to lists by the D'Hondt method, and a fixed number of adjustment seats "
            "are allocated to lists nationwide to even out the disproportion between the parties' votes and seats."
        ),
    ),
    (
        SYSTEM_2003,
        date(2003, 1, 1),
        None,
        {"formula": "dhondt"},
        (
            "Constituency seats are allocated to lists by the D'Hondt method, and nine adjustment seats are allocated "
            "to the parties that win at least five percent of the valid votes nationwide, so that each party's seats "
            "follow its votes. The adjustment seats are assigned to constituencies, and Statistics Iceland counts a "
            "member under the constituency that seat was assigned to."
        ),
    ),
    (SYSTEM_LOCAL, date(1900, 1, 1), None, {"formula": "dhondt"}, LOCAL_NOTE),
    (
        SYSTEM_PRESIDENT,
        date(1944, 1, 1),
        None,
        {"formula": "fptp"},
        (
            "The president is elected directly for four years. The candidate with the most votes is elected, and an "
            "election with a single candidate is not held."
        ),
    ),
    (SYSTEM_REFERENDUM, date(1900, 1, 1), None, {"formula": "referendum"}, "A referendum is decided by a majority."),
]


def candidate_names() -> list[str]:
    """Every presidential candidate that Statistics Iceland names, by the name as printed."""
    names: set[str] = set()
    for table in ("KOS07112", "KOS07110"):
        g = grid(table)
        for label in g.labels[g.dims[0]]:
            text = re.sub(r"\s*\d{4}\s*$", "", clean(label))
            if text not in ("Total", "Alls"):
                names.add(text)
    return sorted(names)


def registry() -> Package:
    p = Package(
        "is.hagstofa/registry",
        SOURCE,
        "Iceland's Althing, municipal councils, and presidency, its parties, and the electoral systems, from "
        "Statistics Iceland's election tables",
        depends=["is.hagstofa/geo"],
    )
    p.external.add(ICELAND)
    p.add(
        "source",
        id=STATS,
        kind="official_body",
        publisher="Hagstofa Íslands (Statistics Iceland)",
        title="Elections: PxWeb statistical database",
        url="https://px.hagstofa.is/pxen/pxweb/en/Ibuar/Ibuar__kosningar/",
        license="Statistics Iceland terms of use: reuse with Statistics Iceland acknowledged as the source",
        license_url="https://hagstofa.is/en/about-us/terms-of-use",
        attribution="Source: Statistics Iceland, hagstofa.is",
    )
    p.add(
        "source",
        id=COMMISSION_SOURCE,
        kind="official_body",
        publisher="Landskjörstjórn (National Electoral Commission of Iceland)",
        title="Notices of the count of the 29 August 2026 referendum",
        url="https://island.is/s/landskjorstjorn",
        license="Public notices of the National Electoral Commission",
        license_url="https://island.is/s/landskjorstjorn",
        attribution="Landskjörstjórn, island.is",
    )
    for notice in NOTICES.values():
        p.artifact(raw.cached(COMMISSION, notice), COMMISSION_SOURCE)
    p.artifact(raw.cached(COMMISSION, REPORT), COMMISSION_SOURCE)
    for table in TABLES:
        p.artifact(raw.cached(SOURCE, url(table)), STATS)
        p.artifact(raw.cached(SOURCE, url(table), body(table)), STATS)
    p.add("body", id=ALTHING_BODY, polity_id=ICELAND, kind="unicameral")
    p.name(ALTHING_BODY, "Althing", "en", "official")
    p.name(ALTHING_BODY, "Alþingi", "is", "native", False)
    p.add("body", id=COUNCIL_BODY, polity_id=ICELAND, kind="council")
    p.name(COUNCIL_BODY, "Municipal councils", "en", "official")
    p.name(COUNCIL_BODY, "Sveitarstjórnir", "is", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=ICELAND, kind="head_of_state")
    p.name(PRESIDENCY, "President of Iceland", "en", "official")
    p.name(PRESIDENCY, "Forseti Íslands", "is", "native", False)
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=ICELAND, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for name in candidate_names():
        person = key("person", name)
        p.add("person", id=person, birth_precision=None)
        p.name(person, name, "is", "official")
    for pid, text in sorted(parties().items()):
        id = party(pid)
        p.add("party", id=id, kind="party", polity_id=ICELAND)
        p.name(id, text, "en", "official")
        if pid in NATIVE:
            p.name(id, NATIVE[pid], "is", "native", False)
    return p


# ---------------------------------------------------------------- building blocks

ENGLISH_MONTHS = [None, *(name.capitalize() for name in MONTHS)]
ICELANDIC_MONTHS = [
    None, "janúar", "febrúar", "mars", "apríl", "maí", "júní", "júlí", "ágúst", "september", "október", "nóvember",
    "desember",
]  # fmt: skip
KINDS = {
    "general": ("general", "Icelandic parliamentary election", "Alþingiskosningar"),
    "special": ("special", "Icelandic parliamentary election of nationally elected members", "Landskjör til Alþingis"),
    "presidential": ("general", "Icelandic presidential election", "Forsetakosningar"),
    "referendum": ("referendum", "Icelandic referendum", "Þjóðaratkvæðagreiðsla"),
    "local": ("local", "Icelandic local government elections", "Sveitarstjórnarkosningar"),
    "reykjavik": ("local", "Reykjavík City Council election", "Borgarstjórnarkosningar í Reykjavík"),
}


@dataclass(frozen=True)
class Event:
    kind: str
    day: Day
    label: str = ""

    @property
    def stamp(self) -> str:
        day = self.day
        if day.precision == "day":
            return day.first.isoformat()
        if day.precision == "month":
            return f"{day.first.year}-{day.first.month:02d}"
        return str(day.first.year)

    @property
    def ekey(self) -> str:
        return f"{self.kind}-{self.stamp}"

    @property
    def package(self) -> str:
        return f"{SOURCE}/{self.ekey}"

    @property
    def path(self) -> str:
        return f"is/{self.stamp}-{self.kind}"


class Build:
    """One election's package and the helpers that every family of elections shares."""

    def __init__(self, event: Event, summary: str, names: list[tuple[str, str, str]] | None = None) -> None:
        self.event = event
        self.ekey = event.ekey
        self.p = Package(event.package, SOURCE, summary, depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"])
        self.p.external |= {ICELAND, TERRITORY}
        self.units: dict[tuple[str, str], str] = {}
        self.counts: Counter[str] = Counter()
        self.sets: list[str] = []
        self.cited: set[str] = set()
        self.valued: Counter[str] = Counter()
        kind, english, native = KINDS[event.kind]
        day = event.day
        english_date = (
            f"{ENGLISH_MONTHS[day.first.month]} {day.first.year}" if day.precision != "year" else str(day.first.year)
        )
        native_date = (
            f"{ICELANDIC_MONTHS[day.first.month]} {day.first.year}" if day.precision != "year" else str(day.first.year)
        )
        self.election = key("election", self.ekey)
        self.p.add(
            "election",
            id=self.election,
            polity_id=ICELAND,
            kind=kind,
            date_from=day.first,
            date_to=day.last,
            date_precision=day.precision,
            original_text=day.text,
            status="held",
        )
        self.p.name(self.election, f"{english_date} {english}", "en", "official")
        self.p.name(self.election, f"{native} {native_date}", "is", "native", False)
        self.p.add("slug", path=event.path, entity_id=self.election, is_current=True)

    def cite(self, *tables: str) -> None:
        for table in tables:
            if table not in self.cited:
                self.cited.add(table)
                self.p.uses(raw.cached(SOURCE, url(table), body(table)))

    def unit(self, scheme: str, code: str, where: str) -> str:
        found = self.units.get((scheme, code))
        if found is not None:
            return found
        id = self.units[(scheme, code)] = key("unit", scheme, self.ekey, code)
        self.p.add(
            "reporting_unit",
            id=id,
            polity_id=ICELAND,
            scheme_code=scheme,
            code=f"{self.ekey}:{code}",
            area_relation="equals",
            area_id=where,
            kind="standard",
        )
        return id

    def national(self) -> str:
        return self.unit("is.landid", "IS", TERRITORY)

    def constituency(self, code: str) -> str:
        return self.unit("is.kjordaemi", code, area("is.kjordaemi", code))

    def municipality(self, name: str) -> str:
        return self.unit("is.sveitarfelag", name, area("is.sveitarfelag", name))

    def contest(
        self,
        suffix: str,
        title: tuple[str, str | None],
        *,
        kind: str,
        where: str,
        system: str,
        body_id: str | None = None,
        office: str | None = None,
        magnitude: int | None = None,
        round_number: int | None = None,
    ) -> str:
        id = key("contest", self.ekey, suffix)
        self.p.add(
            "contest",
            id=id,
            election_id=self.election,
            kind=kind,
            office_id=office,
            body_id=body_id,
            district_area_id=where,
            magnitude=magnitude,
            round_number=round_number,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        self.p.name(id, title[0], "en", "official")
        if title[1]:
            self.p.name(id, title[1], "is", "native", False)
        self.p.add("slug", path=f"{self.event.path}/{changeset.slug(title[0])}", entity_id=id, is_current=True)
        return id

    def result_set(
        self,
        suffix: str,
        contest: str | None,
        note: str,
        *,
        source: str | None = None,
        stage: str = "official",
        default: bool = True,
        supersedes: str | None = None,
    ) -> str:
        id = key("result_set", self.ekey, suffix)
        self.p.add(
            "result_set",
            id=id,
            election_id=self.election,
            contest_id=contest,
            stage=stage,
            kind="counted",
            source_id=source or STATS,
            status="active" if default else "superseded",
            is_default=default,
            completeness=1.0,
            supersedes_id=supersedes,
        )
        self.p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
        self.sets.append(id)
        return id

    def option(
        self,
        contest: str,
        suffix: str,
        label: str,
        position: int,
        kind: str,
        party_id: str | None = None,
    ) -> str:
        id = key("option", self.ekey, contest, suffix)
        self.p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind=kind,
            party_id=party_id,
            ballot_position=position,
            ballot_label=label,
        )
        return id

    def value(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        self.counts["values"] += 1
        self.valued[result_set] += 1
        self.p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )

    def declare(self, result_set: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        self.p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)

    def outcome(self, result_set: str, option: str, seats: int | None, kind: str = "won") -> None:
        self.p.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)

    def note(self, entity: str, kind: str, text: str, suffix: str = "") -> None:
        self.p.add("note", id=key("note", entity, kind, suffix), entity_id=entity, kind=kind, body_md=text, lang="en")

    def finish(self) -> Package:
        for row in self.p.rows.get("result_set", []):
            if not self.valued[str(row["id"])]:
                row["completeness"] = None
        return self.p

    def caveat(self, text: str) -> None:
        self.note(self.election, "caveat", text, text)


# Where two of Statistics Iceland's own tables disagree, the pair of figures and what the package does about it.
KNOWN: dict[str, tuple[int, int]] = {
    "2002 registered voters (municipalities, national total)": (203141, 204945),
    "2006 registered voters (municipalities, national total)": (215932, 216163),
    "2010 registered voters (municipalities, national total)": (224828, 225855),
    "2014 registered voters (municipalities, national total)": (238654, 239734),
    "2018 registered voters (municipalities, national total)": (247890, 247943),
    "2006 Kópavogur valid votes": (14592, 14591),
    "2006 Sveitarfélagið Garður valid votes": (802, 803),
    "2006 Blönduóssbær valid votes": (589, 588),
    "2006 Langanesbyggð valid votes": (304, 303),
    "2006 Skeiða- og Gnúpverjahreppur valid votes": (315, 314),
    "2022 Seltjarnarnes valid votes": (2470, 2469),
    "2022 Langanesbyggð valid votes": (347, 345),
    "2022 Reykjanesbær valid votes": (6783, 6782),
    "2012 votes cast nationwide (participation table, results table)": (163294, 163251),
    "2016 votes cast nationwide (participation table, results table)": (185430, 185390),
    "2020 votes cast nationwide (participation table, results table)": (168790, 168821),
    "2024 votes cast nationwide (participation table, results table)": (215644, 215635),
}


def mismatch(b: Build, what: str, ours: int | None, theirs: int | None) -> None:
    if (ours or 0) == (theirs or 0):
        return
    if KNOWN.get(what) == (ours, theirs):
        b.caveat(
            f"Statistics Iceland's tables disagree on {what.split(' ', 1)[1]}: {ours:,} in the first and "
            f"{theirs:,} in the second. Each figure is stored as its table prints it."
        )
        return
    if os.environ.get("IS_DISCOVER"):
        print("DIFF", repr(what), (ours, theirs))
        return
    raise ValueError(f"{what}: parsed {ours}, the source prints {theirs}")


SOURCE_NOTE = "Counted by the election authorities and published by Statistics Iceland in its PxWeb tables. "


# ---------------------------------------------------------------- events


def day_icelandic(label: str) -> Day:
    text = clean(label)
    match = re.match(r"^(\d{4}),\s*(jan|nóv|des)\.?$", text)
    if match is None:
        return day_of(text)
    month = {"jan": 1, "nóv": 11, "des": 12}[match.group(2)]
    year = int(match.group(1))
    end = date(year + (month == 12), month % 12 + 1, 1)
    return Day(date(year, month, 1), date.fromordinal(end.toordinal() - 1), "month", text)


def events() -> list[Event]:
    found: list[Event] = []
    for label in grid("KOS01900").labels["Year"]:
        if not re.search("presidential|pesidential|referendum", label, re.IGNORECASE):
            found.append(Event("general", day_of(label), label))
    found += [Event("special", day_of(label), label) for label in grid("KOS02002").labels["Year"]]
    found += [Event("presidential", day_of(label), label) for label in grid("KOS02004").labels["Year"]]
    found += [Event("referendum", day_of(label), label) for label in grid("KOS02003").labels["Year"]]
    found += [
        Event("referendum", day_of(label.split()[0]), label)
        for label in grid("KOS04101").labels["Referenda"]
        if label.startswith(("1908", "1916"))
    ]
    found.append(Event("referendum", Day(date(2026, 8, 29), date(2026, 8, 29), "day", "2026, 29 August"), "2026"))
    for label in grid("KOS02005").labels["Year"]:
        year = int(label)
        day = Day(LOCAL_DAYS[year], LOCAL_DAYS[year], "day", label) if year in LOCAL_DAYS else day_of(label)
        found.append(Event("local", day, label))
    for label in grid("KOS02006").labels["Year"]:
        if int(label[:4]) < 1938:
            found.append(Event("reykjavik", day_icelandic(label), label))
    return sorted(found, key=lambda e: (e.day.first, e.kind))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(e.package for e in events())]


def blank_void(event: Event, kind: str) -> tuple[int | None, int | None]:
    """Blank and void ballots of a general election, from the table that covers every election since 1908."""
    g = grid("KOS02000")
    if event.kind == "special":
        return None, None
    year, month = event.day.first.year, event.day.first.month
    found = [
        label
        for label in g.labels["Year"]
        if int(label[:4]) == year
        and ("presidential" in label.lower()) == (kind == "presidential")
        and ("referendum" in label.lower()) == (kind == "referendum")
    ]
    if len(found) > 1:
        found = [label for label in found if str(ENGLISH_MONTHS[month]).lower() in label.lower()]
    if len(found) != 1:
        return None, None
    label = found[0]
    return (
        g.get(Year=label, Unit="Number", Ballots="Blank ballots"),
        g.get(Year=label, Unit="Number", Ballots="Void ballots"),
    )


TURNOUT = {
    "KOS01900": ("Votes", "Voters on the electoral roll", "Votes cast"),
    "KOS02001": ("Votes and voters", "Voters on the electoral roll", "Votes cast"),
    "KOS02002": ("Votes and voters", "Voters on the electoral roll", "Votes cast"),
    "KOS02003": ("Votes and voters", "Voters on the electoral roll", "Votes cast"),
    "KOS02004": ("Votes and voters", "Voters on the electoral roll", "Votes cast"),
    "KOS02005": ("Votes and voters", "Voters on the electoral roll", "Votes cast"),
    "KOS02006": ("Votes and voters", "Voters on the electoral roll", "Votes cast"),
}


def turnout(table: str, label: str) -> tuple[int | None, int | None]:
    """Registered voters and votes cast, in total, for one election of a national overview table."""
    dim, registered, cast = TURNOUT[table]
    g = grid(table)
    return (
        g.get(Year=label, **{dim: registered, "Sex": "Total"}),
        g.get(Year=label, **{dim: cast, "Sex": "Total"}),
    )


# ---------------------------------------------------------------- Althing

CONSTITUENCY_TABLES = {
    2003: "KOS02122",
    2007: "KOS02122",
    2009: "KOS02122",
    2013: "KOS02118c",
    2016: "KOS02118b",
    2017: "KOS02118a",
    2021: "KOS02019",
    2024: "KOS02018",
}
ALTHING_METHOD = (
    SOURCE_NOTE
    + "Votes are the valid votes for each list. Voters on the electoral roll and votes cast are counted in each "
    "municipality, and blank and void ballots in each constituency, so each is stored at the unit where the source "
    "counts it."
)


def lookup(g: Grid, **by: str) -> int | None:
    """One cell of a table whose dimension names differ from table to table, addressed by role."""
    where: dict[str, str] = {}
    for dim in g.dims:
        role = (
            "year"
            if dim.startswith("Year")
            else "cat"
            if dim == "Category"
            else "org"
            if dim.startswith("Political")
            else "const"
            if dim.startswith("Constituency")
            else dim
        )
        where[dim] = by[role]
    return g.get(**where)


def year_of(label: str) -> int:
    match = re.search(r"(\d{4})\s*$", label)
    if match:
        return int(match.group(1))
    return 1900 + int(label.split()[-1])


def option_kind(label: str) -> str:
    pid = party_id_of(label)
    return "others" if pid in OTHERS else "independents" if pid in INDEPENDENTS else "list"


def althing(event: Event) -> Package:
    year = event.day.first.year
    if event.kind == "special" or event.day.first < date(1959, 10, 1):
        return turnout_only(event)
    b = Build(event, f"The {event.stamp} Althing election by constituency from Statistics Iceland")
    registered, cast = turnout("KOS01900", event.label)
    blank, void = blank_void(event, "general")
    b.cite("KOS01900", "KOS02000")
    if year < 2003:
        return althing_national(b, event, registered, cast, blank, void)
    return althing_constituencies(b, event, registered, cast)


def turnout_only(event: Event) -> Package:
    table = "KOS02002" if event.kind == "special" else "KOS01900"
    b = Build(
        event,
        f"The {event.stamp} Althing election, national turnout from Statistics Iceland"
        if event.kind != "special"
        else f"The {event.stamp} election of nationally elected Althing members, national turnout",
    )
    b.cite(table, "KOS02000")
    registered, cast = turnout(table, event.label)
    blank, void = blank_void(event, "general")
    result_set = b.result_set(
        "turnout",
        None,
        SOURCE_NOTE + "Statistics Iceland publishes only the number of voters on the electoral roll and of votes cast "
        "for this election, nationwide, and the blank and void ballots where its table of them lists the election. "
        "No results by party or constituency are published.",
    )
    unit = b.national()
    for measure, value in ((2, registered), (3, cast), (6, blank), (5, void)):
        b.value(result_set, unit, None, measure, value)
        b.declare(result_set, None, measure, value)
    dated(b, event)
    return b.finish()


def dated(b: Build, event: Event) -> None:
    if event.kind == "general" and event.day.first.year in (1908, 1914):
        other = "11 April 1908" if event.day.first.year == 1908 else "10 September 1914"
        b.caveat(
            f"Statistics Iceland's overview table dates this election {event.day.text.split(', ', 1)[1]}, "
            f"while its table of participation in Althing elections dates it {other}."
        )
    if event.kind == "presidential" and event.day.first.year in (1952, 2004):
        other = {1952: "29 July 1952", 2004: "26 May 2004"}[event.day.first.year]
        b.caveat(
            f"Statistics Iceland's overview table dates this election {other}, while its table of presidential "
            f"elections dates it {event.day.text.split(', ', 1)[1]} {event.day.first.year}."
        )


def althing_national(
    b: Build, event: Event, registered: int | None, cast: int | None, blank: int | None, void: int | None
) -> Package:
    year = event.day.first.year
    g = grid("KOS02124")
    label = next(text for text in g.labels["Year"] if year_of(text) == year)
    b.cite("KOS02124")
    seats = None
    national = grid("KOS02121")
    if year >= 1963:
        b.cite("KOS02121")
        seats = national.get(Category="Members elected", **{"Political organisation": "Total"}, Year=str(year))
    contest = b.contest(
        "althing",
        (f"{event.day.first.year} Althing election", f"Alþingiskosningar {year}"),
        kind="list_allocation",
        where=TERRITORY,
        system=SYSTEM_1959,
        body_id=ALTHING_BODY,
        magnitude=seats,
        round_number=1,
    )
    result_set = b.result_set(
        "althing",
        contest,
        ALTHING_METHOD.replace(
            "Voters on the electoral roll and votes cast are counted in each municipality, and blank and void "
            "ballots in each constituency, so each is stored at the unit where the source counts it.",
            "Votes are published for each of the eight constituencies in force until 1999. Voters on the electoral "
            "roll, votes cast, and blank and void ballots are published only for the whole country and are stored "
            "at a national unit. Seats are published only for the whole country.",
        ),
    )
    consts = [c for c in g.labels["Constituency"] if c != "Whole Country"]
    options: dict[str, str] = {}
    names = [n for n in g.labels["Political party"] if n != "Valid votes"]
    for position, name in enumerate(names, 1):
        totals = [g.get(Year=label, **{"Political party": name}, Constituency=c) for c in consts]
        if (
            all(v is None for v in totals)
            and g.get(Year=label, **{"Political party": name}, Constituency="Whole Country") is None
        ):
            continue
        pid = party_id_of(name)
        text = clean(name)
        options[pid] = b.option(
            contest,
            pid,
            text,
            position,
            option_kind(name),
            party(pid) if option_kind(name) == "list" and pid in parties() else None,
        )
    sums: Counter[str] = Counter()
    valid_sum = 0
    for c in consts:
        code = OLD[c]
        unit = b.constituency(code)
        valid = g.get(Year=label, **{"Political party": "Valid votes"}, Constituency=c)
        b.value(result_set, unit, None, 4, valid)
        valid_sum += valid or 0
        for pid, option in options.items():
            name = next(n for n in names if party_id_of(n) == pid)
            v = g.get(Year=label, **{"Political party": name}, Constituency=c)
            b.value(result_set, unit, option, 1, v)
            sums[pid] += v or 0
    whole = g.get(Year=label, **{"Political party": "Valid votes"}, Constituency="Whole Country")
    mismatch(b, f"{year} valid votes by constituency", valid_sum, whole)
    b.declare(result_set, None, 4, whole)
    for pid, option in options.items():
        name = next(n for n in names if party_id_of(n) == pid)
        published = g.get(Year=label, **{"Political party": name}, Constituency="Whole Country")
        mismatch(b, f"{year} {name}", sums[pid], published)
        b.declare(result_set, option, 1, published)
        if year >= 1963 and option_kind(name) == "list":
            seated = national_seats(year, pid)
            if seated:
                b.outcome(result_set, option, seated)
    national_unit = b.national()
    for measure, value in ((2, registered), (3, cast), (6, blank), (5, void)):
        b.value(result_set, national_unit, None, measure, value)
        b.declare(result_set, None, measure, value)
    if year >= 1963:
        aggregate = national_seats(year, "others and outside parties")
        if aggregate:
            b.p.accept(
                "outcomes.seats_magnitude",
                f"Statistics Iceland counts {aggregate} seat in this election only under 'others and outside parties', "
                "so the seats it gives to the named lists add up to one fewer than the Althing's members",
            )
            b.note(
                result_set,
                "caveat",
                f"Statistics Iceland gives {aggregate} of the seats only in an aggregate for others and outside "
                "parties, so no list is credited with it.",
                "aggregate",
            )
    if None not in (cast, blank, void):
        mismatch(b, f"{year} ballots", whole, (cast or 0) - (blank or 0) - (void or 0))
    dated(b, event)
    return b.finish()


def national_seats(year: int, pid: str) -> int | None:
    g = grid("KOS02121")
    for name in g.labels["Political organisation"]:
        if party_id_of(name) == pid and name != "Total":
            return g.get(Category="Members elected", **{"Political organisation": name}, Year=str(year))
    return None


def althing_constituencies(b: Build, event: Event, registered: int | None, cast: int | None) -> Package:
    year = event.day.first.year
    table = CONSTITUENCY_TABLES[year]
    g = grid(table)
    b.cite(table, "KOS02101", "KOS02123")
    munis = grid("KOS02101")
    ballots = grid("KOS02123")
    by_constituency: dict[str, list[tuple[str, str]]] = {}
    for c, name, label in hierarchy("KOS02101"):
        if c is not None:
            by_constituency.setdefault(c, []).append((name, label))
    org_dim = next(d for d in g.dims if d.startswith("Political"))
    const_dim = next(d for d in g.dims if d.startswith("Constituency"))
    orgs = [n for n in g.labels[org_dim] if n != "Total"]
    consts = {c: constituency(c, "new") for c in g.labels[const_dim]}
    ballot_labels = {constituency(c, "new"): c for c in ballots.labels["Constituency"]}
    ballot_labels["whole"] = "Whole country"
    year_key = {"year": str(year)}

    def cell(cat: str, org: str, const: str) -> int | None:
        return lookup(g, cat=cat, org=org, const=const, **year_key)

    def total_label(const: str) -> str:
        return next(c for c in g.labels[const_dim] if consts[c] is None and c in ("Total", "Whole country"))

    whole = total_label("")
    reg_sum = cast_sum = 0
    apart: list[str] = []
    totals: Counter[str] = Counter()
    for label, code in consts.items():
        if code is None:
            continue
        seats = cell("Members elected", "Total", label)
        english = CONSTITUENCIES[code][0]
        contest = b.contest(
            f"althing:{code}",
            (f"{year} Althing election, {english}", f"Alþingiskosningar {year}, {CONSTITUENCIES[code][1]}"),
            kind="list_allocation",
            where=area("is.kjordaemi", code),
            system=SYSTEM_2003,
            body_id=ALTHING_BODY,
            magnitude=seats,
            round_number=1,
        )
        result_set = b.result_set(f"althing:{code}", contest, ALTHING_METHOD)
        options: dict[str, str] = {}
        for position, name in enumerate(orgs, 1):
            votes = cell("Valid votes", name, label)
            if votes is None:
                continue
            pid = party_id_of(name)
            options[name] = b.option(
                contest,
                pid,
                clean(name),
                position,
                option_kind(name),
                party(pid) if option_kind(name) == "list" and pid in parties() else None,
            )
        unit = b.constituency(code)
        valid = cell("Valid votes", "Total", label)
        row = ballot_labels[code]
        b.value(result_set, unit, None, 4, valid)
        b.declare(result_set, None, 4, valid)
        void = ballots.get(Year=str(year), Unit="Votes total", Constituency=row, **{"Votes/ballots": "Void ballots"})
        blank = ballots.get(Year=str(year), Unit="Votes total", Constituency=row, **{"Votes/ballots": "Blank ballots"})
        const_cast = ballots.get(
            Year=str(year), Unit="Votes total", Constituency=row, **{"Votes/ballots": "Votes cast"}
        )
        const_valid = ballots.get(
            Year=str(year), Unit="Votes total", Constituency=row, **{"Votes/ballots": "Valid votes"}
        )
        mismatch(b, f"{year} {english} valid votes", const_valid, valid)
        mismatch(b, f"{year} {english} ballots", const_cast, (valid or 0) + (blank or 0) + (void or 0))
        for measure, value in ((5, void), (6, blank)):
            b.value(result_set, unit, None, measure, value)
            b.declare(result_set, None, measure, value)
        listed = 0
        for name, option in options.items():
            v = cell("Valid votes", name, label)
            b.value(result_set, unit, option, 1, v)
            b.declare(result_set, option, 1, v)
            listed += v or 0
            totals[party_id_of(name)] += v or 0
            won = cell("Members elected", name, label)
            if won:
                b.outcome(result_set, option, won)
        mismatch(b, f"{year} {english} list votes", listed, valid)
        registered_here = cast_here = 0
        for name, printed in by_constituency.get(code, []):
            r = munis.get(Municipality=printed, Year=str(year), Category="Voters on the electroral roll", Sex="Total")
            c = munis.get(Municipality=printed, Year=str(year), Category="Votes cast", Sex="Total")
            if r is None and c is None:
                continue
            mu = b.municipality(name)
            b.value(result_set, mu, None, 2, r)
            b.value(result_set, mu, None, 3, c)
            registered_here += r or 0
            cast_here += c or 0
        constituency_label = next(lab for lab in munis.labels["Municipality"] if constituency(lab, "new") == code)
        published_registered = munis.get(
            Municipality=constituency_label, Year=str(year), Category="Voters on the electroral roll", Sex="Total"
        )
        published_cast = munis.get(Municipality=constituency_label, Year=str(year), Category="Votes cast", Sex="Total")
        mismatch(b, f"{year} {english} registered voters by municipality", registered_here, published_registered)
        mismatch(b, f"{year} {english} votes cast by municipality", cast_here, published_cast)
        if cast_here != const_cast:
            apart.append(f"{english} {cast_here:,} against {const_cast:,}")
        b.declare(result_set, None, 2, published_registered)
        b.declare(result_set, None, 3, published_cast)
        reg_sum += registered_here
        cast_sum += cast_here
    mismatch(b, f"{year} registered voters", reg_sum, registered)
    mismatch(b, f"{year} votes cast", cast_sum, cast)
    if apart:
        b.caveat(
            "Statistics Iceland's table of votes cast by municipality and its table of ballots by constituency add up "
            "to the same national total but differ in some constituencies (municipal total against constituency "
            "total: " + "; ".join(apart) + "). Votes cast are stored for each municipality, and blank and void "
            "ballots and list votes for each constituency, as printed."
        )
    for name in orgs:
        published = cell("Valid votes", name, whole)
        if published is not None:
            mismatch(b, f"{year} {name} nationwide", totals[party_id_of(name)], published)
    dated(b, event)
    return b.finish()


# ---------------------------------------------------------------- presidency


def candidate_name(label: str, year: int) -> tuple[str, bool]:
    """A candidate's name from a label such as 'Katrín Jakobsdóttir 2024', and whether the label is the total row."""
    text = re.sub(rf"\s*{year}\s*$", "", clean(label))
    return text, text in ("Total", "Alls")


def president(event: Event) -> Package:
    year = event.day.first.year
    old = year < 2004
    b = Build(event, f"The {year} Icelandic presidential election by constituency from Statistics Iceland")
    registered, cast = turnout("KOS02004", event.label)
    totals = grid("KOS07100")
    cons = grid("KOS07104" if old else "KOS07102")
    cands = grid("KOS07112" if old else "KOS07110")
    b.cite("KOS02004", "KOS02000", "KOS07100", "KOS07104" if old else "KOS07102", "KOS07112" if old else "KOS07110")
    cand_dim = cands.dims[0]
    number = "Number valid votes" if old else "Number"
    whole = "Whole Country" if old else "Whole country"
    labels = cands.labels[cand_dim]
    names = []
    for label in labels:
        name, total = candidate_name(label, year)
        if label.rstrip().endswith(str(year)) and not total:
            names.append((name, label))
    contest = b.contest(
        "president",
        (f"{year} Icelandic presidential election", f"Forsetakosningar {year}"),
        kind="office",
        where=TERRITORY,
        system=SYSTEM_PRESIDENT,
        office=PRESIDENCY,
        magnitude=1,
        round_number=1,
    )
    muni_years = grid("KOS07002").labels["Year"]
    has_municipal = str(year) in muni_years
    if has_municipal:
        b.cite("KOS07002")
    if not old:
        b.cite("KOS07000")
    note = SOURCE_NOTE + (
        "Candidates' votes, valid votes, blank ballots, and void ballots are counted in each constituency. "
        + (
            "Voters on the electoral roll and votes cast are counted in each municipality."
            if has_municipal
            else "Voters on the electoral roll and votes cast are counted in each constituency."
            if not old
            else "Votes cast are counted in each constituency, and voters on the electoral roll nationwide."
        )
    )
    result_set = b.result_set("president", contest, note)
    ys = str(year)
    constituency_codes = (
        {c: constituency(c, "old") for c in cons.labels["Constituency"] if c != "Whole Country"}
        if old
        else {c: constituency(c, "new") for c in cons.labels["Constituency"] if c != "Whole Country"}
    )
    options: dict[str, str] = {}
    people: dict[str, str] = {}
    votes_by_name = {}
    for position, (name, label) in enumerate(names, 1):
        total = (
            cands.get(**{cand_dim: label, "Unit": number, "Constituency": whole})
            if old
            else cands.get(**{cand_dim: label, "Valid votes": number, "Constituency": whole})
        )
        votes_by_name[name] = total
        options[name] = b.option(contest, name, name, position, "candidate")
        person = people[name] = key("person", name)
        b.p.add(
            "candidacy",
            id=key("candidacy", b.ekey, name),
            person_id=person,
            option_id=options[name],
            role="head",
            ballot_name=name,
        )
    leader = max(votes_by_name, key=lambda n: votes_by_name[n] or 0)
    for name, option in options.items():
        b.outcome(result_set, option, None, "elected" if name == leader else "lost")
    cast_sum = valid_sum = 0
    totals_by_name: Counter[str] = Counter()
    for label, code in constituency_codes.items():
        assert code is not None, label
        unit = b.constituency(code)
        v_total = cons.get(
            Year=ys, Constituency=label, Unit="Number", Votes="Votes total" if old else "Votes cast total"
        )
        v_valid = cons.get(Year=ys, Constituency=label, Unit="Number", Votes="Valid votes")
        v_blank = cons.get(Year=ys, Constituency=label, Unit="Number", Votes="Blank ballots")
        v_void = cons.get(Year=ys, Constituency=label, Unit="Number", Votes="Void ballots")
        mismatch(b, f"{year} {label} ballots", v_total, (v_valid or 0) + (v_blank or 0) + (v_void or 0))
        for measure, value in ((4, v_valid), (6, v_blank), (5, v_void)):
            b.value(result_set, unit, None, measure, value)
        listed = 0
        for name, cand_label in names:
            v = (
                cands.get(**{cand_dim: cand_label, "Unit": number, "Constituency": label})
                if old
                else cands.get(**{cand_dim: cand_label, "Valid votes": number, "Constituency": label})
            )
            b.value(result_set, unit, options[name], 1, v)
            listed += v or 0
            totals_by_name[name] += v or 0
        mismatch(b, f"{year} {label} candidate votes", listed, v_valid)
        cast_sum += v_total or 0
        valid_sum += v_valid or 0
        if not has_municipal:
            b.value(result_set, unit, None, 3, v_total)
            if not old:
                reg = grid("KOS07000").get(
                    Year=ys, Category="Voters on the electoral roll", Sex="Total", Constituency=label
                )
                b.value(result_set, unit, None, 2, reg)
    if has_municipal:
        g = grid("KOS07002")
        reg_sum = mcast_sum = 0
        for code, name, label in hierarchy("KOS07002"):
            r = g.get(
                **{"Constituency/Municipality": label}, Year=ys, Category="Voters on the electoral roll", Sex="Total"
            )
            c = g.get(**{"Constituency/Municipality": label}, Year=ys, Category="Votes cast", Sex="Total")
            if r is None and c is None:
                continue
            mu = b.municipality(name)
            b.value(result_set, mu, None, 2, r)
            b.value(result_set, mu, None, 3, c)
            reg_sum += r or 0
            mcast_sum += c or 0
        mismatch(b, f"{year} registered voters by municipality", reg_sum, registered)
        mismatch(b, f"{year} votes cast by municipality", mcast_sum, cast)
        b.declare(result_set, None, 2, registered)
        b.declare(result_set, None, 3, cast)
    else:
        national = b.national()
        if old:
            b.value(result_set, national, None, 2, registered)
        b.declare(result_set, None, 2, registered)
        b.declare(result_set, None, 3, cast)
    for name, option in options.items():
        b.declare(result_set, option, 1, votes_by_name[name])
        mismatch(b, f"{year} {name} nationwide", totals_by_name[name], votes_by_name[name])
    t_cast = totals.get(Year=ys, Votes="Votes cast total", Unit="Number")
    t_valid = totals.get(Year=ys, Votes="Valid votes", Unit="Number")
    mismatch(b, f"{year} votes cast nationwide", cast_sum, t_cast)
    mismatch(b, f"{year} valid votes nationwide", valid_sum, t_valid)
    mismatch(b, f"{year} votes cast nationwide (participation table, results table)", cast, t_cast)
    b.declare(result_set, None, 4, t_valid)
    b.declare(result_set, None, 6, totals.get(Year=ys, Votes="Blank ballots", Unit="Number"))
    b.declare(result_set, None, 5, totals.get(Year=ys, Votes="Void ballots", Unit="Number"))
    dated(b, event)
    return b.finish()


# ---------------------------------------------------------------- referenda

BLANK_VOID_LABELS = {
    "1918": "1918, referendum",
    "1944:Abrogation": "1944,  referendum. Derog. Union Act of 1918",
    "1944:Constitution": "1944,  referendum, Constitution",
    "2010": "2010, referendum",
    "2011": "2011, referendum",
    "2012": "2012, referendum",
}
REFERENDUM_METHOD = SOURCE_NOTE + "Each answer's votes are the valid votes for it."


def referendum_titles(year: int) -> list[tuple[str, str]]:
    """The referendum questions of one polling day as (key, source label)."""
    if year == 2012:
        return [(f"q{n}", label) for n, label in enumerate(grid("KOS04108").labels["Questions"], 1)]
    found = [label for label in grid("KOS04101").labels["Referenda"] if label.startswith(str(year))]
    return [(clean(label[4:]).split(" ")[0].strip(".") if len(found) > 1 else "q", label) for label in found]


def notice_text(name: str) -> str:
    html = raw.cached(COMMISSION, NOTICES[name]).path.read_text(encoding="utf-8", errors="replace")
    html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.DOTALL)
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def notice_number(text: str, pattern: str) -> int:
    match = re.search(pattern, text)
    if match is None:
        raise LookupError(f"the notice has nothing for {pattern}")
    return int(match.group(1).replace(".", ""))


# the report's constituency columns, left to right
REPORT_COLUMNS = [
    ("Norðvestur-", "northwest-2003"),
    ("Norðaustur-", "northeast-2003"),
    ("Suður-", "south-2003"),
    ("Suðvestur-", "southwest-2003"),
    ("Reykjavíkur-", "reykjavik-south-2003"),
    ("Reykjavíkur-", "reykjavik-north-2003"),
]
REPORT_ROWS = {
    "registered": "Fjöldi kjósenda á kjörskrá",
    "yes": "Fjöldi atkvæða greidd Já",
    "no": "Fjöldi atkvæða greidd Nei",
    "blank": "Fjöldi auðra seðla",
    "void": "Fjöldi ógildra seðla (ekki auðir)",
    "cast": "Fjöldi greiddra atkvæða",
    "valid": "Fjöldi gildra atkvæða",
}


def report_tables() -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """The commission's report: the national figures, and the figures of each constituency by its code."""
    path = raw.cached(COMMISSION, REPORT).path
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    lines = text.splitlines()
    split = next(i for i, line in enumerate(lines) if "eftir kjördæmum" in line)
    header = " ".join(lines[split : split + 8])
    at = 0
    for name, _ in REPORT_COLUMNS:
        at = header.find(name, at)
        if at < 0:
            raise LookupError("the report's constituency columns are not in the expected order")
        at += len(name)
    if header.find("suður", at) < 0 or header.find("norður", header.find("suður", at)) < 0:
        raise LookupError("the report's Reykjavík columns are not in the expected order")

    def row(block: list[str], label: str) -> list[int]:
        found = [line.split(label, 1)[1] for line in block if label in line]
        if len(found) != 1:
            raise LookupError(f"the report has {len(found)} rows for {label}")
        rest = re.sub(r"\d+(?:,\d+)?%", "", found[0])
        return [int(t.replace(".", "")) for t in re.findall(r"\d{1,3}(?:\.\d{3})*|\d+", rest)]

    national: dict[str, int] = {}
    by_unit: dict[str, dict[str, int]] = {code: {} for _, code in REPORT_COLUMNS}
    for name, label in REPORT_ROWS.items():
        top = row(lines[:split], label)
        cells = row(lines[split:], label)
        if len(top) != 1 or len(cells) != len(REPORT_COLUMNS):
            raise LookupError(f"the report's figures for {label} are not one national and six constituency values")
        national[name] = top[0]
        for (_, code), value in zip(REPORT_COLUMNS, cells, strict=True):
            by_unit[code][name] = value
    return national, by_unit


def referendum_2026(event: Event) -> Package:
    b = Build(event, "The 29 August 2026 Icelandic referendum on resuming accession negotiations, national totals")
    for notice in NOTICES.values():
        b.p.uses(raw.cached(COMMISSION, notice))
    b.p.uses(raw.cached(COMMISSION, REPORT))
    counted, final = notice_text("count"), notice_text("final")
    registered = notice_number(counted, r"Fjöldi kjósenda á kjörskrá var ([\d.]+)")
    cast = notice_number(counted, r"Greidd atkvæði voru ([\d.]+)")
    valid = notice_number(counted, r"Gild atkvæði: ([\d.]+)")
    blank = notice_number(counted, r"Auðir seðlar: ([\d.]+)")
    void = notice_number(counted, r"Ógildir seðlar: ([\d.]+)")
    yes0 = notice_number(counted, r"Já: ([\d.]+)")
    no0 = notice_number(counted, r"Nei: ([\d.]+)")
    yes = notice_number(final, r"Já sögðu ([\d.]+) eða")
    no = notice_number(final, r"Nei sögðu ([\d.]+) eða")
    combined = notice_number(final, r"Auðir og ógildir seðlar voru ([\d.]+)")
    mismatch(b, "2026 valid votes", yes0 + no0, valid)
    mismatch(b, "2026 ballots", valid + blank + void, cast)
    mismatch(b, "2026 ballots in the final notice", yes + no + combined, cast)
    question = "Should Iceland resume accession negotiations with the European Union?"
    contest = b.contest(
        "referendum:q",
        (
            f"2026 Icelandic referendum: {question}",
            "Þjóðaratkvæðagreiðsla 2026: Á Ísland að hefja á ný aðildarviðræður við Evrópusambandið?",
        ),
        kind="ballot_question",
        where=TERRITORY,
        system=SYSTEM_REFERENDUM,
    )
    b.p.add("ballot_question", contest_id=contest, type="referendum")
    yes_option = b.option(contest, "yes", "Já", 1, "answer")
    no_option = b.option(contest, "no", "Nei", 2, "answer")
    unit = b.national()
    first = b.result_set(
        "count",
        contest,
        "The National Electoral Commission's notice of 30 August 2026, after the district election boards' counts "
        "reached it and before it ruled on the disputed ballots. Voters on the electoral roll are published subject "
        "to corrections made after the roll was issued on 24 July.",
        source=COMMISSION_SOURCE,
        stage="preliminary",
        default=False,
    )
    for option, v in ((yes_option, yes0), (no_option, no0)):
        b.value(first, unit, option, 1, v)
        b.declare(first, option, 1, v)
    for measure, value in ((2, registered), (3, cast), (4, valid), (6, blank), (5, void)):
        b.value(first, unit, None, measure, value)
        b.declare(first, None, measure, value)
    b.outcome(first, yes_option, None, "won" if yes0 > no0 else "lost")
    b.outcome(first, no_option, None, "won" if no0 > yes0 else "lost")
    last = b.result_set(
        "final",
        contest,
        "The National Electoral Commission's ruling of 22 September 2026 on the disputed ballots, which declared the "
        "result. Its notice gives blank and void ballots together, and gives neither the voters on the roll nor the "
        "votes cast, which the count notice gives. The result is advisory.",
        source=COMMISSION_SOURCE,
        supersedes=first,
        default=False,
    )
    for option, v in ((yes_option, yes), (no_option, no)):
        b.value(last, unit, option, 1, v)
        b.declare(last, option, 1, v)
    b.value(last, unit, None, 5, combined)
    b.declare(last, None, 5, combined)
    b.note(
        last,
        "caveat",
        "The National Electoral Commission counts blank and void ballots together after its ruling. The figure is "
        "stored as invalid ballots.",
    )
    b.outcome(last, yes_option, None, "won" if yes > no else "lost")
    b.outcome(last, no_option, None, "won" if no > yes else "lost")
    # the commission's report of 22 September 2026 gives every figure by constituency
    national, by_unit = report_tables()
    mismatch(b, "2026 report yes votes", national["yes"], yes)
    mismatch(b, "2026 report no votes", national["no"], no)
    mismatch(b, "2026 report blank and void ballots", national["blank"] + national["void"], combined)
    mismatch(b, "2026 report ballots", national["cast"], cast)
    mismatch(b, "2026 report valid votes", national["valid"], yes + no)
    for what in ("yes", "no", "blank", "void", "cast", "valid", "registered"):
        mismatch(b, f"2026 report {what} by constituency", sum(v[what] for v in by_unit.values()), national[what])
    by_constituency = b.result_set(
        "constituency",
        contest,
        "The National Electoral Commission's report of 22 September 2026, which gives the declared result by "
        "constituency. It supersedes the national ruling with the same totals, and gives blank and void ballots "
        "separately. Its count of voters on the electoral roll, 272,543, is lower than the 272,591 of the count notice, "
        "which the roll's corrections after 24 July account for.",
        source=COMMISSION_SOURCE,
        supersedes=last,
    )
    measures = {"registered": 2, "cast": 3, "valid": 4, "void": 5, "blank": 6}
    for code, figures in by_unit.items():
        mismatch(b, f"2026 {code} ballots", figures["valid"] + figures["blank"] + figures["void"], figures["cast"])
        mismatch(b, f"2026 {code} valid votes", figures["yes"] + figures["no"], figures["valid"])
        constituency_unit = b.constituency(code)
        b.value(by_constituency, constituency_unit, yes_option, 1, figures["yes"])
        b.value(by_constituency, constituency_unit, no_option, 1, figures["no"])
        for name, measure in measures.items():
            b.value(by_constituency, constituency_unit, None, measure, figures[name])
    b.declare(by_constituency, yes_option, 1, national["yes"])
    b.declare(by_constituency, no_option, 1, national["no"])
    for name, measure in measures.items():
        b.declare(by_constituency, None, measure, national[name])
    b.outcome(by_constituency, yes_option, None, "won" if national["yes"] > national["no"] else "lost")
    b.outcome(by_constituency, no_option, None, "won" if national["no"] > national["yes"] else "lost")
    return b.finish()


def referendum(event: Event) -> Package:
    year = event.day.first.year
    if year == 2026:
        return referendum_2026(event)
    b = Build(event, f"The {year} Icelandic referendum from Statistics Iceland")
    b.cite("KOS04101", "KOS02000")
    questions = referendum_titles(year)
    detailed = year >= 2010
    if detailed:
        b.cite("KOS04102", "KOS04103", "KOS04105")
    if year == 2012:
        b.cite("KOS04108", "KOS04109")
    if year >= 2010:
        return referendum_constituencies(b, event, questions)
    g = grid("KOS04101")
    unit = b.national()
    for key_, label in questions:
        text = clean(label[4:])
        contest = b.contest(
            f"referendum:{key_}",
            (f"{year} Icelandic referendum: {text}", None),
            kind="ballot_question",
            where=TERRITORY,
            system=SYSTEM_REFERENDUM,
        )
        b.p.add("ballot_question", contest_id=contest, type="referendum")
        result_set = b.result_set(
            f"referendum:{key_}",
            contest,
            SOURCE_NOTE + "Statistics Iceland publishes this referendum only for the whole country. Voters on the "
            "electoral roll, votes cast, and each answer's votes are stored at a national unit.",
        )
        yes = b.option(contest, "yes", "Yes", 1, "answer")
        no = b.option(contest, "no", "No", 2, "answer")
        row = lambda name: g.get(Referenda=label, Results=name)
        y, n = row("Votes cast, yes"), row("Votes cast, no")
        registered, cast, combined = (
            row("Voters on the electoral roll"),
            row("Votes cast"),
            row("Votes cast, blank and void ballots"),
        )
        for option, v in ((yes, y), (no, n)):
            b.value(result_set, unit, option, 1, v)
            b.declare(result_set, option, 1, v)
        for measure, value in ((2, registered), (3, cast)):
            b.value(result_set, unit, None, measure, value)
            b.declare(result_set, None, measure, value)
        split_key = str(year) if len(questions) == 1 else f"{year}:{key_}"
        label_k = BLANK_VOID_LABELS.get(split_key)
        if label_k is not None:
            gk = grid("KOS02000")
            blank = gk.get(Year=label_k, Unit="Number", Ballots="Blank ballots")
            void = gk.get(Year=label_k, Unit="Number", Ballots="Void ballots")
            mismatch(b, f"{year} blank and void ballots", (blank or 0) + (void or 0), combined)
            for measure, value in ((6, blank), (5, void)):
                b.value(result_set, unit, None, measure, value)
                b.declare(result_set, None, measure, value)
        elif combined is not None:
            b.value(result_set, unit, None, 5, combined)
            b.declare(result_set, None, 5, combined)
            b.note(
                result_set,
                "caveat",
                "Statistics Iceland counts blank and void ballots together for this referendum. The figure is stored "
                "as invalid ballots.",
            )
        if combined is not None:
            mismatch(b, f"{year} ballots cast", (y or 0) + (n or 0) + combined, cast)
        else:
            b.note(
                result_set,
                "caveat",
                f"Statistics Iceland prints {(cast or 0) - (y or 0) - (n or 0):,} votes cast beyond the yes and no votes and "
                "does not say how many of them were blank or void.",
            )
        b.outcome(result_set, yes, None, "won" if (y or 0) > (n or 0) else "lost")
        b.outcome(result_set, no, None, "won" if (n or 0) > (y or 0) else "lost")
        if year == 1944 and not any(q for q in [None]):
            pass
    return b.finish()


def referendum_constituencies(b: Build, event: Event, questions: list[tuple[str, str]]) -> Package:
    year = event.day.first.year
    ys = str(year)
    voters = grid("KOS04103")
    munis = grid("KOS04105")
    const_labels = {c: constituency(c, "new") for c in voters.labels["Constituency"] if c != "Whole country"}

    def ballots(category: str, c: str) -> int | None:
        return voters.get(Year=ys, Category=category, Sex="Total", Constituency=c)

    registered = ballots("Voters on the electoral roll", "Whole country")
    cast = ballots("Votes cast", "Whole country")
    blank_total, void_total = ballots("Blank ballots", "Whole country"), ballots("Void ballots", "Whole country")
    valid_total = ballots("Valid votes", "Whole country")
    mismatch(b, f"{year} ballots", cast, (valid_total or 0) + (blank_total or 0) + (void_total or 0))
    # the sets the ballot paper's answers are stored in
    ballot_set = None
    if year == 2012:
        ballot_set = b.result_set(
            "ballot",
            None,
            SOURCE_NOTE + "One ballot paper carried all six questions. Voters on the electoral roll and votes cast are "
            "counted in each municipality, and valid, blank, and void ballots in each constituency.",
        )
    answer_sets: list[tuple[str, str, str, str, str]] = []
    for key_, label in questions:
        text = clean(label[4:]) if year < 2012 else clean(label)
        title = (
            f"{year} Icelandic referendum: {text}"
            if year < 2012
            else f"{year} Icelandic referendum, question {key_[1:]}"
        )
        contest = b.contest(
            f"referendum:{key_}",
            (title, None),
            kind="ballot_question",
            where=TERRITORY,
            system=SYSTEM_REFERENDUM,
        )
        b.p.add("ballot_question", contest_id=contest, type="referendum")
        result_set = b.result_set(f"referendum:{key_}", contest, REFERENDUM_METHOD)
        if year == 2012:
            b.note(contest, "context", f"The question on the ballot: {text}", key_)
        yes = b.option(contest, "yes", "Yes", 1, "answer")
        no = b.option(contest, "no", "No", 2, "answer")
        answer_sets.append((key_, label, contest, result_set, yes + "|" + no))
    unit_cache: dict[str, str] = {}
    sums: dict[str, Counter[str]] = {k: Counter() for k, *_ in answer_sets}
    for c_label, code in const_labels.items():
        assert code is not None
        unit = unit_cache[code] = b.constituency(code)
        valid = ballots("Valid votes", c_label)
        blank, void = ballots("Blank ballots", c_label), ballots("Void ballots", c_label)
        target_sets = [ballot_set] if year == 2012 else [rs for _, _, _, rs, _ in answer_sets]
        for result_set in target_sets:
            assert result_set is not None
            for measure, value in ((4, valid), (6, blank), (5, void)):
                b.value(result_set, unit, None, measure, value)
        for key_, label, contest, result_set, options in answer_sets:
            yes, no = options.split("|")
            if year == 2012:
                g = grid("KOS04109")
                where = {"Constituency": c_label, "Questions": label, "Unit": "Number of valid votes"}
                y, n = g.get(**where, Category="Yes"), g.get(**where, Category="No")
                other = g.get(**where, Category="Unanswered/void")
                mismatch(b, f"{year} {key_} {c_label} answers", (y or 0) + (n or 0) + (other or 0), valid)
                b.value(result_set, unit, None, 5, other)
                sums[key_]["other"] += other or 0
            else:
                g = grid("KOS04102")
                y = g.get(Year=ys, Category="Valid votes - Yes", Constituency=c_label)
                n = g.get(Year=ys, Category="Valid votes - No", Constituency=c_label)
                mismatch(
                    b,
                    f"{year} {c_label} answers",
                    (y or 0) + (n or 0),
                    g.get(Year=ys, Category="Valid votes", Constituency=c_label),
                )
            b.value(result_set, unit, yes, 1, y)
            b.value(result_set, unit, no, 1, n)
            sums[key_]["yes"] += y or 0
            sums[key_]["no"] += n or 0
    # municipal counts of voters and votes cast
    reg_sum = cast_sum = 0
    for code, name, label in hierarchy("KOS04105"):
        r = munis.get(
            **{"Constituency/Municipality": label}, Year=ys, Category="Voters on the electoral roll", Sex="Total"
        )
        c = munis.get(**{"Constituency/Municipality": label}, Year=ys, Category="Votes cast", Sex="Total")
        if r is None and c is None:
            continue
        mu = b.municipality(name)
        for result_set in [ballot_set] if year == 2012 else [rs for _, _, _, rs, _ in answer_sets]:
            assert result_set is not None
            b.value(result_set, mu, None, 2, r)
            b.value(result_set, mu, None, 3, c)
        reg_sum += r or 0
        cast_sum += c or 0
    mismatch(b, f"{year} registered voters by municipality", reg_sum, registered)
    mismatch(b, f"{year} votes cast by municipality", cast_sum, cast)
    for key_, label, contest, result_set, options in answer_sets:
        yes, no = options.split("|")
        if year == 2012:
            g8 = grid("KOS04108")
            ny = g8.get(Questions=label, Unit="Number of valid votes", Category="Yes")
            nn = g8.get(Questions=label, Unit="Number of valid votes", Category="No")
            no_answer = g8.get(Questions=label, Unit="Number of valid votes", Category="Unanswered/void")
            b.declare(result_set, None, 5, no_answer)
            mismatch(b, f"{year} {key_} unanswered nationwide", sums[key_]["other"], no_answer)
            b.note(
                result_set,
                "caveat",
                "Statistics Iceland counts unanswered and void answers to this question together. They are stored "
                "as invalid ballots.",
            )
        else:
            g = grid("KOS04102")
            ny = g.get(Year=ys, Category="Valid votes - Yes", Constituency="Whole country")
            nn = g.get(Year=ys, Category="Valid votes - No", Constituency="Whole country")
            for measure, value in ((4, valid_total), (2, registered), (3, cast), (6, blank_total), (5, void_total)):
                b.declare(result_set, None, measure, value)
        b.declare(result_set, yes, 1, ny)
        b.declare(result_set, no, 1, nn)
        mismatch(b, f"{year} {key_} yes votes", sums[key_]["yes"], ny)
        mismatch(b, f"{year} {key_} no votes", sums[key_]["no"], nn)
        b.outcome(result_set, yes, None, "won" if (ny or 0) > (nn or 0) else "lost")
        b.outcome(result_set, no, None, "won" if (nn or 0) > (ny or 0) else "lost")
    if ballot_set is not None:
        for measure, value in ((2, registered), (3, cast), (4, valid_total), (6, blank_total), (5, void_total)):
            b.declare(ballot_set, None, measure, value)
    return b.finish()


# ---------------------------------------------------------------- local government

LOCAL_TABLES = {
    2002: ("KOS03201", None, "KOS03210", "KOS03302"),
    2006: ("KOS03202", "KOS03205", "KOS03211", "KOS03304"),
    2010: ("KOS03203", "KOS03206", "KOS03212", "KOS03305"),
    2014: ("KOS03204", "KOS03207", "KOS03213", "KOS03306"),
    2018: ("KOS03204a", "KOS03208", "KOS03214", "KOS03307"),
    2022: ("KOS03204b", "KOS03208a", "KOS03214a", "KOS03308"),
}
LETTER = re.compile(r"^([A-ZÞÆÖÐÁÉÍÓÚÝ]{1,2})(?=[\s\-–:.]|$)")
TACIT = re.compile(r"^(?P<m>.+?)[,\s]+(?:sjálfkjörið|one list)(?P<rest>.*)$", re.IGNORECASE)
MARKER = re.compile(r"^[\s,]*(?:elected|without voting|no voting|not voting|,)\s*", re.IGNORECASE)


def authority_seats() -> dict[str, int]:
    return importlib.import_module("ea.sources.is.stjornarradid").authority_seats()


def committee_covered() -> frozenset[str]:
    return importlib.import_module("ea.sources.is.stjornarradid").COVERED


def count(value: float | None) -> int | None:
    return None if value is None else int(value)


@dataclass
class Block:
    name: str
    tacit: bool
    head: list[int | None]
    lists: list[tuple[str, list[int | None]]]


def blocks(g: Grid, rows: dict[str, list[int | None]]) -> list[Block]:
    """The municipalities of a results or candidates table: a header row, then one row for each candidate list."""
    out: list[Block] = []
    labels = [clean(label) for label in g.labels[g.dims[0]]]
    originals = g.labels[g.dims[0]]
    dashed = all(" - " in label for label in labels)
    for i, label in enumerate(labels):
        values = rows[originals[i]]
        following = labels[i + 1] if i + 1 < len(labels) else ""
        tacit = TACIT.match(label)
        if tacit:
            rest = tacit.group("rest")
            while True:
                shorter = MARKER.sub("", rest)
                if shorter == rest:
                    break
                rest = shorter
            name = canon(tacit.group("m").strip(" ,"))
            if not out or out[-1].name != name or not out[-1].tacit:
                out.append(Block(name, True, values, []))
            if rest.strip():
                out[-1].lists.append((rest.strip(), values))
            continue
        if dashed and " - " in label:
            name, rest = label.split(" - ", 1)
            name = canon(name)
            if not out or out[-1].name != name:
                out.append(Block(name, False, [None] * len(values), []))
            out[-1].lists.append((rest, values))
            continue
        if not out or following.startswith(label + " ") or (i == 0):
            out.append(Block(canon(label), False, values, []))
            continue
        head = out[-1]
        if head.tacit and re.match(re.escape(head.name) + r"[,\s]", label):
            head.lists.append((label[len(head.name) :].strip(" ,"), values))
            continue
        prefix = next((p for p in (labels[j] for j in range(i, -1, -1)) if label.startswith(p + " ")), None)
        if prefix is None:
            out.append(Block(canon(label), False, values, [(label, values)]))
        else:
            head.lists.append((label[len(prefix) :].strip(), values))
    return out


def letter(text: str) -> str:
    match = LETTER.match(text) or re.search(r"listabókstafur\s+([A-ZÞÆÖÐÁÉÍÓÚÝ]{1,2})", text)
    return match.group(1) if match else text.lower()


def seats_for(lists: list[tuple[str, list[int | None]]], share: Block | None) -> list[int | None]:
    """The representatives each list elected, from the candidates table: by ballot letter, else by position."""
    if share is None:
        return [None] * len(lists)
    total = share.head[0] or sum(values[0] or 0 for _, values in share.lists)
    by_letter = {letter(text): values[0] for text, values in share.lists}
    got = [by_letter.get(letter(text)) for text, _ in lists]
    if sum(g or 0 for g in got) == total and len({letter(text) for text, _ in lists}) == len(lists):
        return got
    if len(lists) == len(share.lists):
        position = [values[0] for _, values in share.lists]
        if sum(x or 0 for x in position) == total:
            return position
    return [None] * len(lists)


def local(event: Event) -> Package:
    year = event.day.first.year
    if year not in LOCAL_TABLES:
        return local_reykjavik(event)
    turnout_table, votes_table, results_table, seats_table = LOCAL_TABLES[year]
    b = Build(event, f"The {year} Icelandic local government elections by municipality from Statistics Iceland")
    b.cite(turnout_table, results_table, seats_table, "KOS02005")
    if votes_table:
        b.cite(votes_table)
    registered_all, cast_all = turnout("KOS02005", event.label)
    t = grid(turnout_table)
    results = grid(results_table)
    seats = grid(seats_table)
    first_unit = results.labels[results.dims[1]][0]
    result_rows = {label: [count(results.cells[(label, first_unit)])] for label in results.labels[results.dims[0]]}
    seat_unit = seats.labels["Unit"][-1]
    seat_rows = {label: [count(seats.cells[(label, seat_unit, "Total")])] for label in seats.labels[seats.dims[0]]}
    r_blocks = {x.name: x for x in blocks(results, result_rows)}
    s_blocks = {x.name: x for x in blocks(seats, seat_rows)}
    committee = authority_seats() if year == 2022 else {}
    covered_names = committee_covered() if year == 2022 else frozenset()
    v = grid(votes_table) if votes_table else None
    v_labels = {canon(label): label for label in v.labels["Municipality"]} if v is not None else {}
    note = SOURCE_NOTE + (
        "Each municipal council is elected by proportional representation from candidate lists. Voters on the "
        "electoral roll, votes cast, and blank and void ballots are the municipality's, and each list's votes are "
        "its valid votes. Seats are the representatives each list elected."
    )
    reg_sum = cast_sum = 0
    gaps: list[str] = []
    list_gaps: list[str] = []
    used: set[str] = set()
    for label in t.labels["Municipality"]:
        if is_region(label):
            continue
        name = canon(label)
        registered = t.get(Municipality=label, Unit="Voters on the electoral roll", Sex="Total")
        cast = t.get(Municipality=label, Unit="Votes cast", Sex="Total")
        valid = blank = void = None
        if v is not None:
            vl = v_labels[name]
            valid = v.get(Municipality=vl, Unit="Valid votes")
            blank = v.get(Municipality=vl, Unit="Blank ballots")
            void = v.get(Municipality=vl, Unit="Void ballots")
            counted = v.get(Municipality=vl, Unit="Votes cast, total")
            if cast is None:
                cast = counted
            elif counted is not None:
                mismatch(b, f"{year} {name} votes cast", counted, cast)
        block = r_blocks.get(name)
        if block is None and valid is not None:
            block = next(
                (x for x in r_blocks.values() if x.head[0] == valid and x.name not in used and not x.tacit), None
            )
        title = (f"{year} {name} municipal election", None)
        if block is not None:
            used.add(block.name)
        area_id = area("is.sveitarfelag", name)
        if block is not None and block.tacit:
            contest = b.contest(
                f"local:{name}",
                title,
                kind="list_allocation",
                where=area_id,
                system=SYSTEM_LOCAL,
                body_id=COUNCIL_BODY,
            )
            tacit_set = b.result_set(
                f"local:{name}",
                contest,
                SOURCE_NOTE + "Only one candidate list stood in this municipality, so no vote was held and the "
                "list was elected without one.",
            )
            b.p.rows["result_set"][-1]["kind"] = "tacit"
            share = s_blocks.get(name)
            seated = seats_for(block.lists, share)
            if share is not None and share.head[0] is not None:
                b.p.rows["contest"][-1]["magnitude"] = share.head[0]
            for position, (text, _) in enumerate(block.lists, 1):
                pid = local_party(text)
                option = b.option(contest, letter(text), text, position, "list", party(pid) if pid else None)
                if seated[position - 1]:
                    b.outcome(tacit_set, option, seated[position - 1], "elected")
            continue
        covered = year == 2022 and name in covered_names
        if block is None:
            contest = b.contest(
                f"local:{name}", title, kind="body_seats", where=area_id, system=SYSTEM_LOCAL, body_id=COUNCIL_BODY
            )
            result_set = b.result_set(
                f"local:{name}",
                contest,
                SOURCE_NOTE + "This municipality's council was elected by direct personal voting, without candidate "
                "lists. Statistics Iceland publishes the municipality's turnout and ballots, and not the votes "
                "for each person.",
                default=not covered,
            )
            unit = b.municipality(name)
            for measure, value in ((2, registered), (3, cast), (4, valid), (6, blank), (5, void)):
                b.value(result_set, unit, None, measure, value)
            reg_sum += registered or 0
            cast_sum += cast or 0
            gap(b, result_set, gaps, name, cast, valid, blank, void)
            continue
        contest = b.contest(
            f"local:{name}", title, kind="list_allocation", where=area_id, system=SYSTEM_LOCAL, body_id=COUNCIL_BODY
        )
        share = s_blocks.get(name)
        magnitude = share.head[0] if share is not None and share.head[0] is not None else None
        if magnitude is None and share is not None:
            magnitude = sum(vals[0] or 0 for _, vals in share.lists) or None
        disputed = name in committee and committee[name] != magnitude
        if disputed:
            magnitude = committee[name]
        b.p.rows["contest"][-1]["magnitude"] = magnitude
        result_set = b.result_set(f"local:{name}", contest, note, default=not covered)
        unit = b.municipality(name)
        for measure, value in ((2, registered), (3, cast), (4, valid), (6, blank), (5, void)):
            b.value(result_set, unit, None, measure, value)
            b.declare(result_set, None, measure, value)
        listed = 0
        seated = seats_for(block.lists, share)
        if disputed:
            b.note(
                result_set,
                "caveat",
                "Statistics Iceland's table of representatives gives a different number of seats than the "
                f"municipal electoral committee's report, which gives {magnitude}. The seats are not stored here; "
                "the committee's report holds them.",
                "seats",
            )
            seated = [None] * len(block.lists)
        for position, (text, vals) in enumerate(block.lists, 1):
            pid = local_party(text)
            option = b.option(contest, letter(text), text, position, "list", party(pid) if pid else None)
            b.value(result_set, unit, option, 1, vals[0])
            b.declare(result_set, option, 1, vals[0])
            listed += vals[0] or 0
            if seated[position - 1]:
                b.outcome(result_set, option, seated[position - 1])
        mismatch(b, f"{year} {name} list votes", listed, block.head[0])
        if valid is not None:
            mismatch(b, f"{year} {name} valid votes", listed, valid)
            if listed != valid:
                list_gaps.append(name)
                b.note(
                    result_set,
                    "caveat",
                    f"The list votes add up to {listed:,}, and Statistics Iceland prints {valid:,} valid votes. Each "
                    "figure is stored as printed.",
                    "valid",
                )
        gap(b, result_set, gaps, name, cast, valid, blank, void)
        reg_sum += registered or 0
        cast_sum += cast or 0
    mismatch(b, f"{year} registered voters (municipalities, national total)", reg_sum, registered_all)
    mismatch(b, f"{year} votes cast", cast_sum, cast_all)
    national_set(b, registered_all, cast_all)
    if gaps:
        b.p.accept(
            "accounting.ballots_sum",
            f"Statistics Iceland's votes cast differ from the sum of its valid, blank, and void ballots in "
            f"{len(gaps)} municipalities ({', '.join(gaps)}); stored as printed",
        )
    if list_gaps:
        b.p.accept(
            "accounting.votes_valid",
            f"in {len(list_gaps)} municipalities ({', '.join(list_gaps)}) the list votes differ by a vote or two "
            "from the valid votes in Statistics Iceland's table of votes cast; each is stored as printed",
        )
    unmatched = sorted(set(r_blocks) - used - {x.name for x in r_blocks.values() if x.tacit})
    if unmatched:
        raise LookupError(f"{year}: results of {unmatched} match no municipality of the turnout table")
    return b.finish()


def gap(
    b: Build,
    result_set: str,
    gaps: list[str],
    name: str,
    cast: int | None,
    valid: int | None,
    blank: int | None,
    void: int | None,
) -> None:
    if cast is None or valid is None or valid + (blank or 0) + (void or 0) == cast:
        return
    gaps.append(name)
    b.note(
        result_set,
        "caveat",
        f"Statistics Iceland prints {cast:,} votes cast, and {valid:,} valid, {blank or 0:,} blank, and "
        f"{void or 0:,} void ballots, which add up to {valid + (blank or 0) + (void or 0):,}. Each figure is stored as "
        "printed.",
        "ballots",
    )


def national_set(b: Build, registered: int | None, cast: int | None) -> None:
    result_set = b.result_set(
        "national",
        None,
        SOURCE_NOTE + "Statistics Iceland's nationwide count of voters on the electoral roll and of votes cast in "
        "the elections of all municipalities that year, stored at a national unit.",
    )
    unit = b.national()
    for measure, value in ((2, registered), (3, cast)):
        b.value(result_set, unit, None, measure, value)
        b.declare(result_set, None, measure, value)


def local_reykjavik(event: Event) -> Package:
    """A local election year before 2002, where only Reykjavík's results and the national turnout are published."""
    year = event.day.first.year
    label = event.label if event.kind == "reykjavik" else str(year)
    b = Build(
        event,
        f"The {event.stamp} Reykjavík City Council election"
        + (" and national turnout in the local government elections" if event.kind == "local" else "")
        + " from Statistics Iceland",
    )
    b.cite("KOS02006", "KOS03107")
    registered, cast = turnout("KOS02006", label)
    if event.kind == "local":
        b.cite("KOS02005")
        national = turnout("KOS02005", event.label)
        national_set(b, *national)
    results = grid("KOS03107")
    ys = str(year)
    total = None
    if ys in results.labels["Year"] and event.kind in ("reykjavik", "local"):
        total = results.get(Item="Valid votes", **{"Political party": "Total"}, Year=ys)
    seats = results.get(Item="Representatives", **{"Political party": "Total"}, Year=ys) if total is not None else None
    area_id = area("is.sveitarfelag", "Reykjavík")
    contest = b.contest(
        "reykjavik",
        (f"{year} Reykjavík City Council election", f"Borgarstjórnarkosningar í Reykjavík {year}"),
        kind="list_allocation" if total is not None else "body_seats",
        where=area_id,
        system=SYSTEM_LOCAL,
        body_id=COUNCIL_BODY,
        magnitude=seats,
    )
    result_set = b.result_set(
        "reykjavik",
        contest,
        SOURCE_NOTE + "Voters on the electoral roll and votes cast are Reykjavík's, and each list's votes are its "
        "valid votes."
        + (
            ""
            if total is not None
            else " Statistics Iceland publishes only the turnout of this election, not its results by list."
        ),
    )
    unit = b.municipality("Reykjavík")
    for measure, value in ((2, registered), (3, cast), (4, total)):
        b.value(result_set, unit, None, measure, value)
        b.declare(result_set, None, measure, value)
    if total is not None:
        listed = 0
        for position, name in enumerate((n for n in results.labels["Political party"] if n != "Total"), 1):
            votes = results.get(Item="Valid votes", **{"Political party": name}, Year=ys)
            if votes is None:
                continue
            pid = local_party(name)
            option = b.option(contest, name, clean(name), position, "list", party(pid) if pid else None)
            b.value(result_set, unit, option, 1, votes)
            b.declare(result_set, option, 1, votes)
            listed += votes
            won = results.get(Item="Representatives", **{"Political party": name}, Year=ys)
            if won:
                b.outcome(result_set, option, won)
        mismatch(b, f"{year} Reykjavík list votes", listed, total)
    return b.finish()


# ---------------------------------------------------------------- run


def build(event: Event) -> Package:
    if event.kind in ("general", "special"):
        return althing(event)
    if event.kind == "presidential":
        return president(event)
    if event.kind == "referendum":
        return referendum(event)
    if event.kind in ("local", "reykjavik"):
        return local(event)
    raise NotImplementedError(event.ekey)


def parse() -> None:
    geo().write()
    registry().write()
    for event in events():
        try:
            build(event).write()
        except NotImplementedError:
            pass


def emit() -> None:
    package.run("load", packages())
