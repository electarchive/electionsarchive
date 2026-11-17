import csv
import functools
import io
import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "at.wien"
AUSTRIA = str(uuid5(package.NAMESPACE, "polity\x1fAT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUT"))


@dataclass(frozen=True)
class Edition:
    """One Viennese Gemeinderat (and Landtag) election and the files that hold its results by polling district."""

    key: str
    day: date
    files: tuple[str, ...]


OLD = "https://www.wien.gv.at/wahl/NET"
NEW = "https://www.wien.gv.at/wahlergebnis"


def _old(code: str) -> tuple[str, ...]:
    return tuple(f"{OLD}/{code}/{code}_99999999_9999_{kind}.xls" for kind in ("SPR", "WVB"))


EDITIONS = [
    Edition("gr-1996", date(1996, 10, 13), _old("GR961")),
    Edition("gr-2001", date(2001, 3, 25), _old("GR011")),
    Edition("gr-2005", date(2005, 10, 23), _old("GR051")),
    Edition("gr-2010", date(2010, 10, 10), _old("GR101")),
    Edition("gr-2015", date(2015, 10, 11), _old("GR151")),
    Edition(
        "gr-2020",
        date(2020, 10, 11),
        tuple(f"{NEW}/downloads/GR201/GR201_99999999_9999_{kind}.xls" for kind in ("SPR", "WVB")),
    ),
    Edition("gr-2025", date(2025, 4, 27), (f"{NEW}/ogd/GR251.csv",)),
]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
BODY = key("body", "landtag")
LAND = key("area", "at-9.land", "9")
DISTRICTS = [
    "Innere Stadt",
    "Leopoldstadt",
    "Landstraße",
    "Wieden",
    "Margareten",
    "Mariahilf",
    "Neubau",
    "Josefstadt",
    "Alsergrund",
    "Favoriten",
    "Simmering",
    "Meidling",
    "Hietzing",
    "Penzing",
    "Rudolfsheim-Fünfhaus",
    "Ottakring",
    "Hernals",
    "Währing",
    "Döbling",
    "Brigittenau",
    "Floridsdorf",
    "Donaustadt",
    "Liesing",
]
MEASURES = ((2, "registered"), (3, "cast"), (4, "valid"), (5, "invalid"))
SHORT = {"SPOE": "SPÖ", "OEVP": "ÖVP", "FPOE": "FPÖ", "GRUE": "GRÜNE", "OENP": "ÖNP", "BGOE": "BGÖ"}
PERSISTENT = [
    (re.compile(r"^(spö|spoe)", re.IGNORECASE), "SPÖ"),
    (re.compile(r"^(övp|oevp|wiener volkspartei)", re.IGNORECASE), "ÖVP"),
    (re.compile(r"^(fpö|fpoe|freiheitliche)", re.IGNORECASE), "FPÖ"),
    (re.compile(r"^(grüne|grue)", re.IGNORECASE), "GRÜNE"),
    (re.compile(r"^(neos)", re.IGNORECASE), "NEOS"),
    (re.compile(r"^(lif)$", re.IGNORECASE), "LIF"),
]


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.key}" for e in EDITIONS)]


def fetch() -> None:
    for edition in EDITIONS:
        for url in edition.files:
            raw.fetch(SOURCE, url, suffix=url.rsplit(".", 1)[1])


def parse() -> None:
    geo().write()
    registry().write()
    for edition in EDITIONS:
        election(edition).write()


def emit() -> None:
    package.run("load", packages())


@dataclass
class Leaf:
    district: int
    polling: str
    kind: str
    registered: int | None
    cast: int
    invalid: int
    votes: list[int]

    @property
    def valid(self) -> int:
        return self.cast - self.invalid


@dataclass
class Table:
    labels: list[str]
    leaves: list[Leaf]
    registered: int
    cast: int
    invalid: int
    votes: list[int]


def _legacy(edition: Edition) -> Table:
    """The older files are tab-separated text with fixed-width zero-padded numbers, one line per polling district."""
    spr, wvb = (raw.cached(SOURCE, url).path.read_bytes().decode("cp1252").splitlines() for url in edition.files)
    header = [c.strip() for c in spr[0].split("\t")]
    width = len(header) - 1
    labels = [SHORT.get(c, c) for c in header[8:width]]
    leaves = []
    for line in spr[1:]:
        cells = line.split("\t")
        if cells[0] != "4":
            continue
        figures = [int(c) for c in cells[5:width]]
        number = cells[4]
        leaves.append(
            Leaf(
                int(cells[3]),
                number,
                "postal" if number == "000" else "standard",
                None if number == "000" else figures[0],
                figures[1],
                figures[2],
                figures[3:],
            )
        )
    total = next(line.split("\t") for line in wvb[1:] if line.startswith("1\t"))
    figures = [int(c) for c in total[5:width]]
    return Table(labels, leaves, figures[0], figures[1], figures[2], figures[3:])


def _current(edition: Edition) -> Table:
    text = raw.cached(SOURCE, edition.files[0]).path.read_bytes().decode("cp1252")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    header = rows[0]
    end = header.index("Status")
    labels = [" ".join(c.split()) for c in header[16:end]]
    leaves = []
    for row in rows[1:]:
        postal = row[6] == "Bezirk" and row[11] == "Parteistimmen"
        if row[6] != "Sprengel" and not postal:
            continue
        figures = [int(c or 0) for c in row[13:end]]
        district = int(row[9][1:3])
        if postal:
            number = "wk" if row[12].startswith("Wahlkarten") else "bw"
        else:
            number = row[10]
        leaves.append(
            Leaf(
                district,
                number,
                "postal" if postal else "standard",
                None if postal else figures[0],
                figures[1],
                figures[2],
                figures[3:],
            )
        )
    total = next(row for row in rows[1:] if row[6] == "Wien")
    figures = [int(c) for c in total[13:end]]
    return Table(labels, leaves, figures[0], figures[1], figures[2], figures[3:])


@functools.cache
def table(edition: Edition) -> Table:
    return _current(edition) if edition.files[0].endswith(".csv") else _legacy(edition)


def party_key(edition: Edition, label: str) -> str:
    for pattern, name in PERSISTENT:
        if pattern.search(label):
            return name
    return f"{label}@{edition.key}"


def party(edition: Edition, label: str) -> str:
    return key("party", party_key(edition, label))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Vienna and its 23 municipal districts as the city's election office codes them in its results",
    )
    p.external |= {AUSTRIA, COUNTRY}
    for code, rank, noun in (("at-9.land", 1, "state"), ("at-9.bezirk", 2, "municipal district")):
        p.add("scheme", code=code, polity_id=AUSTRIA, kind="administrative", rank=rank, noun=noun, code_system=None)
    p.add("area", id=LAND, scheme_code="at-9.land")
    p.add("area_code", area_id=LAND, code="9")
    p.add("area_parent", area_id=LAND, parent_id=COUNTRY)
    p.name(LAND, "Wien", "de", "official")
    p.name(LAND, "Vienna", "en", "official", False)
    for number, text in enumerate(DISTRICTS, start=1):
        id = key("area", "at-9.bezirk", number)
        p.add("area", id=id, scheme_code="at-9.bezirk")
        p.add("area_code", area_id=id, code=f"{number:02d}")
        p.add("area_parent", area_id=id, parent_id=LAND)
        p.name(id, text, "de", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Viennese Landtag and the lists on its ballots, as the city's election office prints them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Stadt Wien",
        title="Wiener Wahlergebnisse: Gemeinderatswahlen",
        url="https://www.wien.gv.at/politik/wahlergebnisse",
        attribution="Stadt Wien",
    )
    for edition in EDITIONS:
        for url in edition.files:
            p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add("body", id=BODY, polity_id=AUSTRIA, kind="unicameral")
    p.name(BODY, "Viennese Landtag", "en", "official")
    p.name(BODY, "Wiener Landtag und Gemeinderat", "de", "native", False)
    seen: set[str] = set()
    for edition in EDITIONS:
        for label in table(edition).labels:
            id = party(edition, label)
            if id in seen:
                continue
            seen.add(id)
            p.add("party", id=id, kind="party", polity_id=AUSTRIA)
            p.add("external_id", entity_id=id, scheme="at.wien.party", value=party_key(edition, label))
            p.name(id, label, "de", "abbreviation" if len(label) < 12 else "official")
    return p


METHOD = (
    "Results by polling district (Sprengel) from the city election office's results files. Vienna is a state and a "
    "municipality, and its Gemeinderat also sits as the Landtag. Postal and absentee-card ballots (Wahlkarten) are "
    "counted apart from the polling districts. The office prints them as a district-level row for each of the 23 "
    "municipal districts, which is stored as its own unit, so the units add up to the city's result. Registered "
    "voters are listed only for the polling districts."
)


def election(edition: Edition) -> Package:
    year = edition.day.year
    t = table(edition)
    p = Package(
        f"{SOURCE}/{edition.key}",
        SOURCE,
        f"The {edition.day} Viennese Gemeinderat and Landtag election by polling district from the city's election "
        "office",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRIA, COUNTRY}
    for url in edition.files:
        p.uses(raw.cached(SOURCE, url))
    id = key("election", edition.key)
    p.add(
        "election", id=id, polity_id=AUSTRIA, kind="general", date_from=edition.day, date_to=edition.day, status="held"
    )
    p.name(id, f"{year} Viennese Landtag election", "en", "official")
    p.name(id, f"Gemeinderatswahl {year}", "de", "native", False)
    path = f"at/{edition.day}-wien-landtag"
    p.add("slug", path=path, entity_id=id, is_current=True)
    contest = key("contest", edition.key)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        body_id=BODY,
        district_area_id=LAND,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Viennese Landtag", "en", "official")
    p.add("slug", path=f"{path}/{changeset.slug('Landtag')}", entity_id=contest, is_current=True)
    result_set = key("result_set", edition.key)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options = []
    for position, label in enumerate(t.labels, start=1):
        option = key("option", edition.key, label)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(edition, label),
            ballot_position=position,
            ballot_label=label,
        )
    valid = t.cast - t.invalid
    for measure, value in ((2, t.registered), (3, t.cast), (4, valid), (5, t.invalid)):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for option, votes in zip(options, t.votes, strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=votes)
    for leaf in t.leaves:
        code = f"{leaf.district:02d}-{leaf.polling}"
        unit = key("unit", edition.key, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=AUSTRIA,
            scheme_code="at-9.bezirk",
            code=f"{edition.day}:{code}",
            area_relation="within",
            area_id=key("area", "at-9.bezirk", leaf.district),
            kind="postal_district" if leaf.kind == "postal" else "standard",
        )
        where = DISTRICTS[leaf.district - 1]
        if leaf.kind == "postal":
            label = {"wk": "Wahlkarten", "bw": "Briefwahl"}.get(leaf.polling, "Wahlkarten")
            p.name(unit, f"{label}, {where}", "de", "official")
        else:
            p.name(unit, f"Sprengel {int(leaf.polling)}, {where}", "de", "official")
        for measure, field in MEASURES:
            value = getattr(leaf, field)
            if value is None:
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
        for option, votes in zip(options, leaf.votes, strict=True):
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
    over = [leaf for leaf in t.leaves if leaf.registered is not None and leaf.cast > leaf.registered]
    if over:
        p.accept(
            "accounting.turnout",
            f"{len(over)} polling {'district' if len(over) == 1 else 'districts'} counted more ballots than the file "
            "lists registered voters, from voters with an absentee card who vote outside their own district; "
            "stored as printed",
        )
    return p
