"""Sweden's Second Chamber (andra kammaren) elections of 1924 to 1968 by constituency, digitized from Statistics
Sweden's Riksdagsmannavalen volumes.

Each volume of Riksdagsmannavalen (Sveriges officiella statistik) prints a table of valid ballots by party and members
elected by party for every constituency (valkrets), usually beside the same figures for the election before. The
tables were read from the page images, which have no text layer under the figures, into the TSV files in
`scb_andra_kammaren/`, one per volume, with one row per constituency and year as printed and the national row (Hela
riket) last. Before a package is written, every row's party ballots must add up to its printed total and its party
seats to its printed seat total, and every column must add up to the national row; `locators.tsv` holds the box of
every figure on its page.

The volumes and the tables used:

- Riksdagsmannavalen åren 1925–1928, table 3: 1928 and 1924.
- Riksdagsmannavalen åren 1933–1936, table 3: 1936 and 1932.
- Riksdagsmannavalen åren 1941–1944, table 4: 1944 and 1940.
- Riksdagsmannavalen åren 1949–1952, table 3: 1952 and 1948.
- Riksdagsmannavalen åren 1957–1958, part I, table 2: 1958 and 1956.
- Riksdagsmannavalen åren 1959–1960, part I, table 2: 1960 (its 1958 rows match the 1957–1958 volume).
- Riksdagsmannavalen åren 1965–1968, part 1, tables 4 and 5: 1968, with the electorate.
"""

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.se import scb, val

SOURCE = scb.SOURCE
FOLDER = Path(__file__).with_name("scb_andra_kammaren")
SERIES = (
    "https://share.scb.se/ov9993/data/historisk%20statistik/SOS%201911-%2FValstatistiken%2F"
    "Riksdagsmannavalen%20%28SOS%29%201909-1968%2FValstatistik-Riksdagsmannavalen-"
)
REGISTRY = f"{SOURCE}/andra-kammaren-registry"
GEO = f"{SOURCE}/andra-kammaren-geo"
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"

VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
SWEDEN = val.SWEDEN
COUNTRY = val.COUNTRY


@dataclass(frozen=True)
class Volume:
    key: str  # the TSV in FOLDER
    file: str  # the PDF's name on share.scb.se, without the series prefix
    title: str
    table: str
    layout: dict[int, str]  # the PDF pages of the table and the columns each prints; seats* is every seat column

    @property
    def url(self) -> str:
        return f"{SERIES}{self.file}.pdf"

    @property
    def pages(self) -> list[int]:
        return list(self.layout)

    def page_columns(self, page: int) -> list[str]:
        out: list[str] = []
        for column in self.layout[page].split():
            out += seat_columns(self.key) + ["seats"] if column == "seats*" else [column]
        return out

    @property
    def source_id(self) -> str:
        return scb.key("source", "riksdagsmannavalen", self.key)


VOLUMES = {
    v.key: v
    for v in (
        Volume(
            "1925-1928",
            "1925-1926-1927-1928",
            "Riksdagsmannavalen åren 1925–1928",
            "tab. 3",
            {220: "H B L F", 221: "S KH K O total invalid seats*"},
        ),
        Volume(
            "1933-1936",
            "1933-1934-1935-1936",
            "Riksdagsmannavalen åren 1933–1936",
            "tab. 3",
            {204: "H B FP S SP", 205: "K N O total invalid seats*"},
        ),
        Volume(
            "1941-1944",
            "1941-1942-1943-1944",
            "Riksdagsmannavalen åren 1941–1944",
            "tab. 4",
            {216: "H B FP S", 217: "K O total invalid seats*"},
        ),
        Volume(
            "1949-1952",
            "1949-1950-1951-1952",
            "Riksdagsmannavalen åren 1949–1952",
            "tab. 3",
            {86: "H B FP S", 87: "K O total invalid seats*"},
        ),
        Volume(
            "1957-1958",
            "1957-1958-I",
            "Riksdagsmannavalen åren 1957–1958, I",
            "tab. 2",
            {12: "H C FP S", 13: "K O total invalid seats*"},
        ),
        Volume(
            "1959-1960",
            "1959-1960-I",
            "Riksdagsmannavalen åren 1959–1960, I",
            "tab. 2",
            {14: "H C FP S", 15: "K O total invalid seats*"},
        ),
        Volume(
            "1965-1968",
            "1965-1968-1",
            "Riksdagsmannavalen åren 1965–1968, del 1: Andrakammarvalet 1968",
            "tabell 4 and 5",
            {20: "registered total invalid H C", 21: "F S68 MP KDS S K O", 22: "seats*"},
        ),
    )
}


@dataclass(frozen=True)
class Election:
    year: int
    volume: str
    start: date
    end: date
    note: str = ""

    @property
    def day(self) -> date:
        return self.end if self.start == self.end else self.start


ELECTIONS = [
    Election(
        1924,
        "1925-1928",
        date(1924, 9, 19),
        date(1924, 9, 21),
        "Voting took place on Sunday 21 September in 25 constituencies (in some towns the day before), and on "
        "Friday 19 September in Stockholms stad and Göteborgs stad and on the Saturday in the Malmö constituency.",
    ),
    Election(
        1928,
        "1925-1928",
        date(1928, 9, 15),
        date(1928, 9, 21),
        "Voting took place on Sunday 16 September in most of the country, on Saturday 15 September in the "
        "Fyrstadskretsen and Göteborgs stad constituencies and in a number of towns, and on Friday 21 September in "
        "Stockholms stad.",
    ),
    Election(
        1932,
        "1933-1936",
        date(1932, 9, 17),
        date(1932, 9, 20),
        "Sunday 18 September was the general polling day; Göteborg, the Fyrstadskretsen constituency, and a number "
        "of towns voted on Saturday 17 September, and Stockholms stad on Tuesday 20 September.",
    ),
    Election(1936, "1933-1936", date(1936, 9, 20), date(1936, 9, 20)),
    Election(1940, "1941-1944", date(1940, 9, 15), date(1940, 9, 15)),
    Election(1944, "1941-1944", date(1944, 9, 17), date(1944, 9, 17)),
    Election(1948, "1949-1952", date(1948, 9, 19), date(1948, 9, 19)),
    Election(1952, "1949-1952", date(1952, 9, 21), date(1952, 9, 21)),
    Election(1956, "1957-1958", date(1956, 9, 16), date(1956, 9, 16)),
    Election(
        1958,
        "1957-1958",
        date(1958, 6, 1),
        date(1958, 6, 1),
        "An extra election, held after the Second Chamber was dissolved on 28 April 1958.",
    ),
    Election(1960, "1959-1960", date(1960, 9, 18), date(1960, 9, 18)),
    Election(1968, "1965-1968", date(1968, 9, 15), date(1968, 9, 15)),
]
BY_YEAR = {e.year: e for e in ELECTIONS}


# ---------------------------------------------------------------------------------------------------------------------
# Constituencies. A constituency that was a whole county (or, for Stockholm, the city or the county around it) is
# that county's area; the six others are this pipeline's own constituency areas.


@dataclass(frozen=True)
class Krets:
    name: str
    scheme: str
    code: str

    @property
    def area(self) -> str:
        if self.scheme == val.CONSTITUENCY:
            return scb.key("area", "andra-kammaren", self.code)
        return scb.area(self.scheme, self.code)


KRETSAR = {
    "stockholms-stad": Krets("Stockholms stads valkrets", val.COUNTY, scb.STOCKHOLM_CITY),
    "stockholms-lan": Krets("Stockholms läns valkrets", val.COUNTY, scb.STOCKHOLM_COUNTY),
    "uppsala": Krets("Uppsala läns valkrets", val.COUNTY, "03"),
    "sodermanland": Krets("Södermanlands läns valkrets", val.COUNTY, "04"),
    "ostergotland": Krets("Östergötlands läns valkrets", val.COUNTY, "05"),
    "jonkoping": Krets("Jönköpings läns valkrets", val.COUNTY, "06"),
    "kronoberg": Krets("Kronobergs läns valkrets", val.COUNTY, "07"),
    "kalmar": Krets("Kalmar läns valkrets", val.COUNTY, "08"),
    "gotland": Krets("Gotlands läns valkrets", val.COUNTY, "09"),
    "blekinge": Krets("Blekinge läns valkrets", val.COUNTY, "10"),
    "kristianstad": Krets("Kristianstads läns valkrets", val.COUNTY, "11"),
    "fyrstadskretsen": Krets(
        "Malmö, Hälsingborgs, Landskrona och Lunds städers valkrets (Fyrstadskretsen)",
        val.CONSTITUENCY,
        "AK-fyrstadskretsen",
    ),
    "malmohus": Krets("Malmöhus läns valkrets", val.CONSTITUENCY, "AK-malmohus"),
    "halland": Krets("Hallands läns valkrets", val.COUNTY, "13"),
    "goteborgs-stad": Krets("Göteborgs stads valkrets", val.CONSTITUENCY, "AK-goteborgs-stad"),
    "goteborg-bohus": Krets(
        "Göteborgs och Bohus läns landstingsområdes valkrets", val.CONSTITUENCY, "AK-goteborg-bohus"
    ),
    "alvsborg-norra": Krets("Älvsborgs läns norra valkrets", val.CONSTITUENCY, "AK-alvsborg-norra"),
    "alvsborg-sodra": Krets("Älvsborgs läns södra valkrets", val.CONSTITUENCY, "AK-alvsborg-sodra"),
    "skaraborg": Krets("Skaraborgs läns valkrets", val.COUNTY, "16"),
    "varmland": Krets("Värmlands läns valkrets", val.COUNTY, "17"),
    "orebro": Krets("Örebro läns valkrets", val.COUNTY, "18"),
    "vastmanland": Krets("Västmanlands läns valkrets", val.COUNTY, "19"),
    "kopparberg": Krets("Kopparbergs läns valkrets", val.COUNTY, "20"),
    "gavleborg": Krets("Gävleborgs läns valkrets", val.COUNTY, "21"),
    "vasternorrland": Krets("Västernorrlands läns valkrets", val.COUNTY, "22"),
    "jamtland": Krets("Jämtlands läns valkrets", val.COUNTY, "23"),
    "vasterbotten": Krets("Västerbottens läns valkrets", val.COUNTY, "24"),
    "norrbotten": Krets("Norrbottens läns valkrets", val.COUNTY, "25"),
}
OWN_SINCE, OWN_UNTIL = date(1921, 1, 1), date(1971, 1, 1)
OWN_NOTES = {
    "AK-fyrstadskretsen": "The towns of Malmö, Hälsingborg (Helsingborg), Landskrona, and Lund, which formed a "
    "Second Chamber constituency of their own.",
    "AK-malmohus": "Malmöhus län without the four towns of the Fyrstadskretsen constituency.",
    "AK-goteborgs-stad": "The city of Göteborg, a Second Chamber constituency of its own.",
    "AK-goteborg-bohus": "Göteborgs och Bohus län without the city of Göteborg: the area of the county council "
    "(landstingsområde).",
    "AK-alvsborg-norra": "The northern constituency of Älvsborgs län.",
    "AK-alvsborg-sodra": "The southern constituency of Älvsborgs län.",
}


# ---------------------------------------------------------------------------------------------------------------------
# Parties. Each column of a table is a party, a joint designation, or the others; the party a column stands for can
# change from one election to the next.


@dataclass(frozen=True)
class Party:
    name: str
    abbreviation: str
    kind: str = "party"
    english: str | None = None
    historical: tuple[tuple[str, date | None, date | None], ...] = ()


PARTIES = {
    "H": Party(
        "Högerpartiet",
        "H",
        english="Right Party",
        historical=(("Allmänna valmansförbundet", None, date(1938, 1, 1)), ("Högern", None, date(1952, 1, 1))),
    ),
    "C": Party(
        "Centerpartiet",
        "C",
        english="Centre Party",
        historical=(("Bondeförbundet", None, date(1957, 6, 1)),),
    ),
    "L": Party("Sveriges liberala parti", "L", english="Liberal Party of Sweden"),
    "F": Party("Frisinnade folkpartiet", "F", english="Free-minded People's Party"),
    "LF": Party(
        "Frisinnade folkpartiet och Sveriges liberala parti",
        "L+F",
        kind="bloc",
        english="Free-minded and Liberal parties",
    ),
    "FP": Party("Folkpartiet", "FP", english="People's Party"),
    "S": Party("Sveriges socialdemokratiska arbetareparti", "S", english="Social Democrats"),
    "KH": Party("Sveriges kommunistiska parti (Höglundsriktningen)", "K (Höglund)", english="Höglund Communists"),
    "K": Party("Sveriges kommunistiska parti", "K", english="Communist Party of Sweden"),
    "SP": Party(
        "Socialistiska partiet",
        "SP",
        english="Socialist Party",
        historical=(("Sveriges kommunistiska parti (Kilbomsriktningen)", None, date(1934, 1, 1)),),
    ),
    "N": Party("Sveriges nationella förbund", "N", english="National League of Sweden"),
    "KDS": Party("Kristen demokratisk samling", "KDS", english="Christian Democratic Unity"),
    "S68": Party("Samling 68", "S 68", kind="bloc", english="Samling 68 (Right, Centre, and People's parties)"),
    "MP": Party("Mittenpartierna", "Mp", kind="bloc", english="Centre parties (Centre and People's parties)"),
}

# The party behind each column, where it differs from the column's own name.
COLUMN_PARTY: dict[tuple[int, str], str] = {
    (1924, "B"): "C",
    (1928, "B"): "C",
    (1932, "B"): "C",
    (1936, "B"): "C",
    (1940, "B"): "C",
    (1944, "B"): "C",
    (1948, "B"): "C",
    (1952, "B"): "C",
    (1932, "FP"): "LF",
}
LABELS: dict[str, str] = {
    "H": "Högern",
    "B": "Bondeförbundet",
    "C": "Centerpartiet",
    "L": "Liberala",
    "F": "Frisinnade",
    "FP": "Folkpartiet",
    "S": "Socialdemokrater",
    "KH": "Kommunister (Höglundsriktningen)",
    "K": "Kommunister",
    "SP": "Socialistiska partiet",
    "N": "Nationella",
    "KDS": "Kristen demokratisk samling",
    "S68": "Samling 68",
    "MP": "Mittenpartierna",
    "O": "Övriga",
}
YEAR_LABELS: dict[tuple[int, str], str] = {
    (1924, "K"): "Kommunister (Kilbomsriktningen)",
    (1932, "FP"): "Liberala och frisinnade",
    (1932, "SP"): "Kilbomskommunister",
    (1932, "K"): "Sillénkommunister",
    (1960, "H"): "Högerpartiet",
    (1968, "H"): "Högerpartiet",
}


def party(key: str) -> str:
    return scb.key("party", "andra-kammaren", key)


# ---------------------------------------------------------------------------------------------------------------------
# Reading the tables.

type Row = dict[str, int | None]


def _number(text: str) -> int | None:
    return None if text in ("-", "—", "") else int(text)


def rows(volume: str) -> list[tuple[int, str, Row]]:
    path = FOLDER / f"{volume}.tsv"
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader((line for line in f if not line.startswith("#")), delimiter="\t")
        return [
            (int(r["year"]), r["krets"], {k: _number(v) for k, v in r.items() if k not in ("year", "krets")})
            for r in reader
        ]


def columns(volume: str) -> list[str]:
    path = FOLDER / f"{volume}.tsv"
    with path.open(encoding="utf-8") as f:
        header = next(line for line in f if not line.startswith("#"))
    return header.rstrip("\n").split("\t")[2:]


def party_columns(volume: str) -> list[str]:
    cols = columns(volume)
    return cols[: cols.index("total")]


def seat_columns(volume: str) -> list[str]:
    return [c for c in columns(volume) if c.startswith("seats:")]


def check(volume: str) -> None:
    """Every row adds up to its printed totals, and every column to the printed national row."""
    found = rows(volume)
    parties, seats = party_columns(volume), seat_columns(volume)
    for year, krets, row in found:
        if sum(row[c] or 0 for c in parties) != row["total"]:
            raise ValueError(f"{volume} {year} {krets}: the party ballots do not add up to the printed total")
        if sum(row[c] or 0 for c in seats) != row["seats"]:
            raise ValueError(f"{volume} {year} {krets}: the party seats do not add up to the printed total")
        if krets != "riket" and krets not in KRETSAR:
            raise ValueError(f"{volume}: unknown constituency {krets}")
    for year in sorted({y for y, _, _ in found}):
        units = [row for y, k, row in found if y == year and k != "riket"]
        national = [row for y, k, row in found if y == year and k == "riket"]
        if len(units) != len(KRETSAR) or len(national) != 1:
            raise ValueError(f"{volume} {year}: expected {len(KRETSAR)} constituencies and a national row")
        for column in columns(volume):
            if sum(row[column] or 0 for row in units) != (national[0][column] or 0):
                raise ValueError(f"{volume} {year}: column {column} does not add up to the national row")


def page_sizes() -> dict[tuple[str, int], tuple[int, int]]:
    """(volume, page) -> (width, height) of the page in pixels at 100 dpi."""
    with (FOLDER / "pages.tsv").open(encoding="utf-8") as f:
        return {
            (r["volume"], int(r["page"])): (int(r["width"]), int(r["height"]))
            for r in csv.DictReader(f, delimiter="\t")
        }


def locators() -> dict[tuple[str, int, str, str], tuple[int, float, float, float, float]]:
    """(volume, year, krets, column) -> (page, x0, y0, x1, y1), in pixels of the page at 100 dpi."""
    out: dict[tuple[str, int, str, str], tuple[int, float, float, float, float]] = {}
    with (FOLDER / "locators.tsv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            out[(r["volume"], int(r["year"]), r["krets"], r["column"])] = (
                int(r["page"]),
                float(r["x0"]),
                float(r["y0"]),
                float(r["x1"]),
                float(r["y1"]),
            )
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Packages.


def fetch() -> None:
    for v in VOLUMES.values():
        raw.fetch(SOURCE, v.url, suffix=".pdf")


def packages() -> list[str]:
    return [REGISTRY, GEO, *(election_key(e) for e in ELECTIONS)]


def election_key(e: Election) -> str:
    return f"{SOURCE}/andra-kammaren-{e.year}"


def parse() -> None:
    for volume in VOLUMES:
        check(volume)
    registry().write()
    geo().write()
    for e in ELECTIONS:
        election(e).write()


BODY = scb.key("body", "andra-kammaren")
SYSTEMS: dict[str, tuple[date, date | None, dict[str, object], str]] = {
    "andra-kammaren-1909": (
        date(1909, 1, 1),
        date(1952, 1, 1),
        {"formula": "dhondt"},
        (
            "Each constituency elects its members by party-list proportional representation, allocated by the d'Hondt "
            "method. Until 1948, parties could combine their lists under a joint party designation (kartell), and the "
            "seats were first divided among the joint designations and then within them."
        ),
    ),
    "andra-kammaren-1952": (
        date(1952, 1, 1),
        date(1971, 1, 1),
        {"formula": "modified_sainte_lague", "firstDivisor": 1.4},
        (
            "Each constituency elects its members by party-list proportional representation, allocated by the modified "
            "Sainte-Laguë method (jämkade uddatalsmetoden) with a first divisor of 1.4."
        ),
    ),
}


def system(year: int) -> str:
    return scb.key("electoral_system", "andra-kammaren-1952" if year >= 1952 else "andra-kammaren-1909")


def _sources(p: Package) -> None:
    for v in VOLUMES.values():
        p.add(
            "source",
            id=v.source_id,
            kind="official_body",
            publisher="Statistics Sweden (Statistiska centralbyrån)",
            title=f"Sveriges officiella statistik: {v.title}",
            url=v.url,
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution="Statistics Sweden (SCB)",
        )
        p.artifact(raw.cached(SOURCE, v.url), v.source_id)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The Second Chamber of the bicameral Riksdag, its electoral systems, the parties of its elections of 1924 to "
        "1968, and the Riksdagsmannavalen volumes they are digitized from",
    )
    p.external.add(SWEDEN)
    _sources(p)
    p.add("body", id=BODY, polity_id=SWEDEN, kind="lower_house")
    p.name(BODY, "Riksdagens andra kammare", "sv", "official")
    p.name(BODY, "Second Chamber of the Riksdag", "en", "official")
    p.add(
        "note",
        id=scb.key("note", BODY),
        entity_id=BODY,
        kind="context",
        body_md="The directly elected lower house of the bicameral Riksdag under the Riksdag Act of 1866. It sat from "
        "1867 until the unicameral Riksdag replaced both chambers on 1 January 1971.",
        lang="en",
    )
    for name, (since, until, rules, method) in SYSTEMS.items():
        id = scb.key("electoral_system", name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=SWEDEN,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=scb.key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for key, x in PARTIES.items():
        id = party(key)
        p.add("party", id=id, kind=x.kind, polity_id=SWEDEN)
        p.name(id, x.name, "sv", "official")
        p.name(id, x.abbreviation, "sv", "abbreviation", False)
        if x.english:
            p.name(id, x.english, "en", "official")
        for text, since, until in x.historical:
            p.add(
                "name",
                id=p.id("name", id, "sv", "historical", text),
                entity_id=id,
                text=text,
                lang="sv",
                kind="historical",
                valid_from=since,
                valid_to=until,
                is_preferred=False,
            )
    return p


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The six Second Chamber constituencies of 1921 to 1970 that were not a whole county",
        depends=[f"{val.SOURCE}/geo"],
    )
    p.external |= {SWEDEN, COUNTRY, val.CONSTITUENCY}
    for krets in KRETSAR.values():
        if krets.scheme != val.CONSTITUENCY:
            continue
        id = krets.area
        p.add("area", id=id, scheme_code=val.CONSTITUENCY)
        p.add("area_code", area_id=id, code=krets.code, valid_from=OWN_SINCE, valid_to=OWN_UNTIL)
        p.add("area_parent", area_id=id, parent_id=COUNTRY, valid_from=OWN_SINCE, valid_to=OWN_UNTIL)
        p.name(id, krets.name, "sv", "official")
        p.add(
            "note",
            id=scb.key("note", id),
            entity_id=id,
            kind="context",
            body_md=OWN_NOTES[krets.code],
            lang="en",
        )
    return p


METHOD = (
    "Valid ballots by party and members elected by party in each constituency, as printed in {table} of "
    "Statistics Sweden's {title} (Sveriges officiella statistik), read from the page images; every figure carries "
    "its page and box. Every constituency's party ballots add up to its printed total, its party seats to its "
    "printed seat total, and every column adds up to the printed national total. Valid ballots are the printed "
    "total; {invalid} Ballots cast are the valid ballots plus the invalid ones. Where parties went to the polls "
    "under a joint party designation (kartell), the table counts the joint ballots under the party it names, and "
    "its footnotes list the cases; a party column with a dash is not stored."
)
INVALID_TEXT = (
    "invalid ballots are the envelopes without accepted contents (valkuvert utan godkänt innehåll), which the table "
    "prints beside them."
)
INVALID_1968 = (
    "invalid ballots are the voters with invalid ballots (röstande med ogiltiga valsedlar), and the electorate is "
    "the persons entitled to vote (röstberättigade) in table 4."
)


@dataclass
class Build:
    p: Package
    e: Election
    values: list[tuple[str, str, str | None, int, int, int]] = field(default_factory=list)
    boxes: list[tuple[str, str, str | None, int, str, float, float, float, float]] = field(default_factory=list)
    pages: set[int] = field(default_factory=set)


def page_id(volume: Volume, page: int) -> str:
    return scb.key("source_page", volume.url, page)


def election(e: Election) -> Package:
    volume = VOLUMES[e.volume]
    p = Package(
        election_key(e),
        SOURCE,
        f"The Second Chamber election of {e.year} by constituency, digitized from Statistics Sweden's {volume.title}",
        depends=[REGISTRY, GEO, f"{SOURCE}/geo", f"{val.SOURCE}/geo"],
    )
    p.external |= {SWEDEN, COUNTRY, val.COUNTY, val.CONSTITUENCY}
    p.uses(raw.cached(SOURCE, volume.url))
    election_id = scb.key("election", "andra-kammaren", e.year)
    p.add(
        "election",
        id=election_id,
        polity_id=SWEDEN,
        kind="general",
        date_from=e.start,
        date_to=e.end,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{e.year} Swedish Second Chamber election", "en", "official")
    p.name(election_id, f"Andrakammarvalet {e.year}", "sv", "native", False)
    slug = f"se/{e.day}-andra-kammaren"
    p.add("slug", path=slug, entity_id=election_id, is_current=True)
    if e.note:
        p.add("note", id=scb.key("note", election_id), entity_id=election_id, kind="context", body_md=e.note, lang="en")
    boxes = locators()
    b = Build(p, e)
    parties = party_columns(e.volume)
    found = [(k, row) for y, k, row in rows(e.volume) if y == e.year and k != "riket"]
    method = METHOD.format(
        table=volume.table,
        title=volume.title,
        invalid=INVALID_1968 if e.year == 1968 else INVALID_TEXT,
    )
    for krets_key, row in found:
        krets = KRETSAR[krets_key]
        contest = scb.key("contest", "andra-kammaren", e.year, krets_key)
        seats = row["seats"] or 0
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=BODY,
            district_area_id=krets.area,
            magnitude=seats,
            round_number=1,
            electoral_system_id=system(e.year),
            default_result_kind="counted",
        )
        p.name(contest, krets.name, "sv", "official")
        p.add("slug", path=f"{slug}/{krets_key}", entity_id=contest, is_current=True)
        result_set = scb.key("result_set", "andra-kammaren", e.year, krets_key)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=volume.source_id,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note", id=scb.key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
        )
        unit = scb.key("unit", "andra-kammaren", e.year, krets_key)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=SWEDEN,
            scheme_code=krets.scheme,
            code=f"{e.day}:ak:{krets_key}",
            area_relation="equals",
            area_id=krets.area,
            kind="standard",
        )
        p.name(unit, krets.name, "sv", "official")

        def put(option: str | None, measure: int, value: int, column: str | None) -> None:
            flags = 0
            if column is not None:
                flags = DIGITIZED
                page, x0, y0, x1, y1 = boxes[(e.volume, e.year, krets_key, column)]
                b.pages.add(page)
                b.boxes.append((result_set, unit, option, measure, page_id(volume, page), x0, y0, x1, y1))
            b.values.append((result_set, unit, option, measure, value, flags))

        for position, column in enumerate(parties, start=1):
            votes = row[column]
            won = row.get(f"seats:{column}") or 0
            if votes is None and not won:
                continue
            option = scb.key("option", "andra-kammaren", e.year, krets_key, column)
            label = YEAR_LABELS.get((e.year, column), LABELS[column])
            if e.year >= 1957 and column == "C":
                label = "Centerpartiet"
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="others" if column == "O" else "list",
                party_id=None if column == "O" else party(COLUMN_PARTY.get((e.year, column), column)),
                ballot_position=position,
                ballot_label=label,
            )
            if votes is not None:
                put(option, VOTES, votes, column)
            if won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
        total, invalid = row["total"] or 0, row["invalid"] or 0
        put(None, VALID, total, "total")
        put(None, INVALID, invalid, "invalid")
        put(None, CAST, total + invalid, None)
        if "registered" in row and row["registered"] is not None:
            put(None, REGISTERED, row["registered"], "registered")
    _write(b, volume)
    return p


def _write(b: Build, volume: Volume) -> None:
    p = b.p
    artifact = raw.cached(SOURCE, volume.url)
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{volume.source_id}\x1f{artifact.storage_key}"))
    sizes = page_sizes()
    for page in sorted(b.pages):
        width, height = sizes[(volume.key, page)]
        p.add(
            "source_page",
            id=page_id(volume, page),
            artifact_id=artifact_id,
            page_number=page,
            image_key=f"pages/{SOURCE}/{artifact.sha256}-{page}.png",
            width=width,
            height=height,
        )
    p.frame(
        "result_value",
        pl.DataFrame(
            b.values,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "flags": pl.Int16,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            b.boxes,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "page_id": pl.String,
                "x0": pl.Float64,
                "y0": pl.Float64,
                "x1": pl.Float64,
                "y1": pl.Float64,
            },
            orient="row",
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
        ),
    )


# ---------------------------------------------------------------------------------------------------------------------
# Locating the figures. `locate` is run by hand after a table is transcribed: it renders each table page at 300 dpi,
# reads its words and their boxes with Tesseract, finds each transcribed figure among them, and writes the boxes to
# locators.tsv and the page sizes to pages.tsv. A figure that the OCR misread takes its row's band and its column's
# median edges. It reports how many figures the OCR read the same as the transcription.

type Word = tuple[str, int, int, int, int]


def _words(pdf: Path, page: int, folder: Path) -> tuple[list[Word], int, int]:
    import subprocess

    from PIL import Image

    stem = folder / f"{page}"
    subprocess.run(
        ["pdftoppm", "-r", "300", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", pdf, stem],
        check=True,
    )
    image = stem.with_suffix(".png")
    width, height = Image.open(image).size
    tsv = subprocess.run(
        ["tesseract", image, "-", "--psm", "6", "-l", "eng", "tsv"], capture_output=True, text=True, check=True
    ).stdout
    words: list[Word] = []
    for r in csv.DictReader(tsv.splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE):
        if r["level"] == "5" and r["text"].strip():
            left, top, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
            words.append((r["text"], left, top, left + w, top + h))
    return words, width, height


def _lines(words: list[Word]) -> list[list[Word]]:
    found: list[tuple[float, list[Word]]] = []
    for w in sorted(words, key=lambda w: (w[2] + w[4]) / 2):
        middle = (w[2] + w[4]) / 2
        if found and abs(middle - found[-1][0]) < 14:
            group = found[-1][1] + [w]
            found[-1] = (sum((v[2] + v[4]) / 2 for v in group) / len(group), group)
        else:
            found.append((middle, [w]))
    return [sorted(group, key=lambda w: w[1]) for _, group in found]


def _numbers(line: list[Word]) -> list[tuple[int, tuple[int, int, int, int]]]:
    """Every number a run of up to three adjacent tokens can spell, with the run's box."""
    import re

    tokens = [(re.sub(r"\D", "", w[0]), w) for w in line]
    out: list[tuple[int, tuple[int, int, int, int]]] = []
    for i, (digits, w) in enumerate(tokens):
        if not digits:
            continue
        text, box = digits, [w[1], w[2], w[3], w[4]]
        out.append((int(text), (box[0], box[1], box[2], box[3])))
        for nxt, v in tokens[i + 1 : i + 3]:
            if len(nxt) != 3 or v[1] - box[2] > 40:
                break
            text += nxt
            box = [box[0], min(box[1], v[2]), v[3], max(box[3], v[4])]
            out.append((int(text), (box[0], box[1], box[2], box[3])))
    return out


def locate() -> None:
    import statistics
    import tempfile

    out: list[list[object]] = []
    sizes: list[list[object]] = []
    for volume in VOLUMES.values():
        pdf = raw.cached(SOURCE, volume.url).path
        found = rows(volume.key)
        cols = columns(volume.key)
        boxes: dict[tuple[int, str], tuple[int, float, float, float, float]] = {}
        read: set[tuple[int, str]] = set()
        placed = 0
        wanted = 0
        with tempfile.TemporaryDirectory() as folder:
            for page in volume.pages:
                words, width, height = _words(pdf, page, Path(folder))
                here = volume.page_columns(page)
                sizes.append([volume.key, page, round(width / 3), round(height / 3)])
                lines = [(line, _numbers(line)) for line in _lines(words)]
                # Align the table's rows with the page's lines: each row takes the line, below the last one taken,
                # that spells the most of its figures of 10 or more.
                at: dict[int, int] = {}
                start = 0
                for index, (_, _, row) in enumerate(found):
                    targets = {v for c, v in row.items() if c in here and v is not None and v >= 10}
                    best, score = None, 0
                    for li in range(start, len(lines)):
                        hits = len(targets & {n for n, _ in lines[li][1]})
                        if hits > score:
                            best, score = li, hits
                        if best is not None and li > best + 6:
                            break
                    if best is not None and score >= 2:
                        at[index] = best
                        start = best + 1
                if len(at) < len(found) // 2:
                    # A page of small figures (seats alone): its rows are the lines with a cell for nearly every
                    # column, in order.
                    full = [
                        li
                        for li, (line, _) in enumerate(lines)
                        if sum(
                            w[0].strip(".,:;)|") in ("-", "—", "–", "_") or w[0].strip(".,:;)|").isdigit() for w in line
                        )
                        >= len(here) - 2
                    ]
                    if len(full) == len(found):
                        at = dict(enumerate(full))
                hits: dict[str, list[tuple[int, int, int, int]]] = {}
                for index, li in at.items():
                    for column in here:
                        value = found[index][2][column]
                        if value is None:
                            continue
                        spans = [box for n, box in lines[li][1] if n == value]
                        if value >= 10 and len(spans) >= 1:
                            hits.setdefault(column, []).append(spans[0])
                            x0, y0, x1, y1 = spans[0]
                            boxes[(index, column)] = (page, x0 / 3, y0 / 3, x1 / 3, y1 / 3)
                            read.add((index, column))
                # Seat figures are single digits the OCR cannot place by value: in each line, the digits and dashes
                # right of the invalid ballots column (or, on a page of seats alone, all of them) are the seat
                # columns in order.
                seat_cols = [c for c in here if c.startswith("seats")]
                limit = max((b[2] for b in hits.get("invalid", [])), default=0)
                for index, li in at.items() if seat_cols else ():
                    cells = [
                        w
                        for w in lines[li][0]
                        if w[1] > limit
                        and (w[0].strip(".,:;)|") in ("-", "—", "–", "_") or w[0].strip(".,:;)|").isdigit())
                    ]
                    if len(cells) >= len(seat_cols):
                        for column, w in zip(seat_cols, cells, strict=False):
                            hits.setdefault(column, []).append((w[1], w[2], w[3], w[4]))
                            value = found[index][2][column]
                            if value is not None and w[0].strip(".,:;)|") == str(value):
                                boxes[(index, column)] = (page, w[1] / 3, w[2] / 3, w[3] / 3, w[4] / 3)
                                read.add((index, column))
                # Columns on this page, with their median edges.
                edges = {
                    column: (statistics.median(b[0] for b in bs), statistics.median(b[2] for b in bs))
                    for column, bs in hits.items()
                }
                # Each row's band: its line's, or, for a row the OCR did not align, one interpolated from the rows
                # around it.
                bands: dict[int, tuple[float, float]] = {}
                for index, li in at.items():
                    line = lines[li][0]
                    bands[index] = (statistics.median(w[2] for w in line), statistics.median(w[4] for w in line))
                known = sorted(bands)
                for index in range(len(found)) if len(known) >= 2 else ():
                    if index in bands:
                        continue
                    before = max((k for k in known if k < index), default=known[0])
                    after = min((k for k in known if k > index), default=known[-1])
                    if before == after or before > index or after < index:
                        before, after = (known[0], known[1]) if index < known[0] else (known[-2], known[-1])
                    share = (index - before) / (after - before)
                    bands[index] = (
                        bands[before][0] + share * (bands[after][0] - bands[before][0]),
                        bands[before][1] + share * (bands[after][1] - bands[before][1]),
                    )
                for index, (top, bottom) in bands.items():
                    for column, (x0, x1) in edges.items():
                        if found[index][2][column] is not None and (index, column) not in boxes:
                            boxes[(index, column)] = (page, x0 / 3, top / 3, x1 / 3, bottom / 3)
        for index, (year, krets, row) in enumerate(found):
            for column in cols:
                if row[column] is None:
                    continue
                wanted += 1
                if (index, column) in boxes:
                    placed += 1
                    out.append([volume.key, year, krets, column, *boxes[(index, column)]])
        print(f"{volume.key}: {wanted} figures, {placed} placed, {len(read)} read by the OCR as transcribed")
    with (FOLDER / "locators.tsv").open("w", encoding="utf-8") as f:
        f.write("volume\tyear\tkrets\tcolumn\tpage\tx0\ty0\tx1\ty1\n")
        for r in out:
            f.write("\t".join(str(round(v, 1)) if isinstance(v, float) else str(v) for v in r) + "\n")
    with (FOLDER / "pages.tsv").open("w", encoding="utf-8") as f:
        f.write("volume\tpage\twidth\theight\n")
        for r in sizes:
            f.write("\t".join(str(v) for v in r) + "\n")
