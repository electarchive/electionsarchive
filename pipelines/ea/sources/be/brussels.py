"""Municipal council elections in the 19 municipalities of the Brussels-Capital Region, 2006 to 2024.

The Region's local government office publishes one workbook per election year, with each municipality's electorate,
ballots, valid votes, seats, and every list's votes and seats. The workbooks hold municipality totals, so each contest
has one reporting unit, the municipality. The Region's open data portal publishes the municipal boundaries.
"""

import functools
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import fastexcel
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "be.brussels"
PAGE = "https://elections.brussels/resultats-elections"
WORKBOOK = (
    "https://elections.brussels/sites/default/files/uploads/%C3%89lections/"
    "Aper%C3%A7us%20des%20r%C3%A9sultats_{year}.xlsx"
)
YEARS = (2006, 2012, 2018, 2024)
BOUNDARIES = (
    "https://opendata.brussel.be/api/explore/v2.1/catalog/datasets/"
    "limites-administratives-des-communes-en-region-de-bruxelles-capitale/exports/geojson"
)
BOUNDARIES_PAGE = (
    "https://opendata.brussel.be/explore/dataset/limites-administratives-des-communes-en-region-de-bruxelles-capitale/"
)
BELGIUM = str(uuid5(package.NAMESPACE, "polity\x1fBE"))
BELGIUM_AREA = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBEL"))
SCHEME = "be-bru.commune"
RERUN = "2025-02"
MONTHS = {
    "janvier": 1,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
}
ALIASES = {"Ville de Bruxelles": "Bruxelles"}
LOCAL_LIST = re.compile(r"^lb|bourgmestre|burgemeester")
METHOD = (
    "Counted by the municipalities and published by the Brussels-Capital Region's local government office, "
    "Bruxelles Pouvoirs Locaux, in a workbook with one column pair per municipality. Registered voters are the "
    "electorate, ballots cast are the ballots deposited, valid votes are the votes for lists, and blank votes are the "
    "blank and invalid ballots together. The workbook holds list votes and seats per municipality; candidates' "
    "preference votes are not stored here. A list's name is as the workbook prints it, and the workbook prints some "
    "lists in the same municipality under different names from one election to the next."
)
RERUN_METHOD = (
    METHOD
    + " The October 2024 vote in this municipality was annulled because of irregularities, and the workbook gives "
    "the results of the new election held in February 2025."
)


@dataclass
class Row:
    label: str
    votes: int
    seats: int


@dataclass
class Commune:
    name: str
    rerun: bool
    registered: int
    cast: int
    blank: int
    valid: int
    seats: int
    lists: list[Row]
    printed_lists: int
    printed_seated: int
    total_votes: int
    total_seats: int


def url(year: int) -> str:
    return WORKBOOK.format(year=year)


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for year in YEARS:
        raw.fetch(SOURCE, url(year), suffix=".xlsx")
    raw.fetch(SOURCE, BOUNDARIES, suffix=".geojson")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def folded(label: str) -> str:
    return clean(label).casefold()


def number(cell: str | None) -> int | None:
    text = clean(cell).replace(" ", "")
    if text in ("", "-"):
        return None
    return int(float(text))


SOURCE_ID = key("source", "results")
GEO_SOURCE_ID = key("source", "boundaries")
BODY = key("body", "municipal-councils")


def area(nis: str) -> str:
    return key("area", SCHEME, nis)


def party(label: str, commune: str) -> str:
    return key("party", commune, folded(label)) if LOCAL_LIST.search(folded(label)) else key("party", folded(label))


@functools.cache
def boundaries() -> list[dict[str, object]]:
    import json

    features = json.loads(raw.cached(SOURCE, BOUNDARIES).path.read_bytes())["features"]
    found = [
        {
            "nis": str(feature["properties"]["national_code"]),
            "fr": clean(feature["properties"]["name_fr"]),
            "nl": clean(feature["properties"]["name_nl"]),
            "shape": shapely.geometry.shape(feature["geometry"]),
        }
        for feature in features
    ]
    assert len(found) == 19 and len({row["nis"] for row in found}) == 19, "expected the 19 municipalities"
    return sorted(found, key=lambda row: str(row["nis"]))


@functools.cache
def nis_of() -> dict[str, str]:
    return {str(row["fr"]): str(row["nis"]) for row in boundaries()}


def election_day(year: int) -> date:
    text = sheet(year)[0][0]
    found = re.search(r"du (\d+)(?:er)? (\w+) (\d{4})", text)
    assert found is not None, text
    return date(int(found[3]), MONTHS[found[2]], int(found[1]))


@functools.cache
def sheet(year: int) -> list[list[str]]:
    reader = fastexcel.read_excel(str(raw.cached(SOURCE, url(year)).path))
    frame = reader.load_sheet(0, header_row=None, dtypes="string").to_polars()
    return [[cell or "" for cell in row] for row in frame.rows()]


@functools.cache
def communes(year: int) -> tuple[Commune, ...]:
    rows = sheet(year)
    top = next(i for i, row in enumerate(rows) if clean(row[0]) == "" and sum(bool(clean(c)) for c in row) >= 19)
    columns = [i for i, cell in enumerate(rows[top]) if clean(cell)]
    assert len(columns) == 19, (year, len(columns))
    labels = {clean(row[0]).casefold(): row for row in rows[top + 1 : top + 8]}

    def figure(name: str, column: int) -> int:
        value = number(labels[name][column])
        assert value is not None, (year, name)
        return value

    end = next(i for i, row in enumerate(rows) if clean(row[0]) == "Total")
    start = next(i for i, row in enumerate(rows) if clean(row[1]) == "Votes valables") + 1
    found: list[Commune] = []
    for column in columns:
        name = clean(rows[top][column])
        rerun = name.endswith("*")
        name = ALIASES.get(name.rstrip("*").strip(), name.rstrip("*").strip())
        lists: list[Row] = []
        seen: set[str] = set()
        for row in rows[start:end]:
            label = clean(row[0])
            if not label or label.casefold() in ("listes sans élus", "listes avec élus"):
                continue
            votes, seats = number(row[column]), number(row[column + 1])
            if votes is None:
                assert seats is None, (year, name, label)
                continue
            assert seats is not None and folded(label) not in seen, (year, name, label)
            seen.add(folded(label))
            lists.append(Row(label, votes, seats))
        found.append(
            Commune(
                name,
                rerun,
                figure("électeurs inscrits", column),
                figure("bulletins déposés", column),
                figure("bulletins blancs et nuls", column),
                figure("votes valables", column),
                figure("membres à élire", column),
                lists,
                figure("listes", column),
                figure("listes avec élus", column),
                number(rows[end][column]) or 0,
                number(rows[end][column + 1]) or 0,
            )
        )
    return tuple(found)


def days() -> dict[int, date]:
    return {year: election_day(year) for year in YEARS}


def election_keys() -> Iterator[tuple[str, int, bool]]:
    for year in YEARS:
        yield str(days()[year]), year, False
        if any(c.rerun for c in communes(year)):
            yield RERUN, year, True


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name, _, _ in election_keys())]


def parse() -> None:
    geo().write()
    registry().write()
    for name, year, rerun in election_keys():
        election(name, year, rerun).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The 19 municipalities of the Brussels-Capital Region with their boundaries from the Region's open data",
    )
    p.external |= {BELGIUM, BELGIUM_AREA}
    artifact = raw.cached(SOURCE, BOUNDARIES)
    p.add(
        "source",
        id=GEO_SOURCE_ID,
        kind="official_body",
        publisher="Brussels-Capital Region (Paradigm, UrbIS)",
        title="Administrative boundaries of municipalities in the Brussels-Capital Region",
        url=BOUNDARIES_PAGE,
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Région de Bruxelles-Capitale, UrbIS",
    )
    p.artifact(artifact, GEO_SOURCE_ID)
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=BELGIUM,
        kind="administrative",
        rank=3,
        noun="municipality",
        code_system="refnis",
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", SCHEME, artifact.retrieved_at.year),
        scheme_code=SCHEME,
        vintage=str(artifact.retrieved_at.year),
        as_of=artifact.retrieved_at.date(),
    )
    for row in boundaries():
        id = area(str(row["nis"]))
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=str(row["nis"]))
        p.add("area_parent", area_id=id, parent_id=BELGIUM_AREA)
        p.name(id, str(row["fr"]), "fr", "official")
        if row["nl"] != row["fr"]:
            p.name(id, str(row["nl"]), "nl", "official", False)
        shape = shapely.make_valid(row["shape"])
        parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
        shape = shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(parts))), 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=GEO_SOURCE_ID,
        )
    return p


def labels() -> dict[str, tuple[str, str]]:
    """Every party key with the spelling to name it, and its municipality when the list is local to one."""
    found: dict[str, tuple[str, str]] = {}
    for year in YEARS:
        for commune in communes(year):
            for row in commune.lists:
                id = party(row.label, commune.name)
                found.setdefault(id, (row.label, commune.name if LOCAL_LIST.search(folded(row.label)) else ""))
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Parties and lists, the municipal councils, and the source of the Brussels municipal election results",
    )
    p.external.add(BELGIUM)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Bruxelles Pouvoirs Locaux (SPRB), with the Institut bruxellois de statistique et d'analyse",
        title="Élections communales: aperçus des résultats",
        url=PAGE,
        attribution="SPRB - Bruxelles Pouvoirs Locaux; Institut bruxellois de statistique et d'analyse",
    )
    p.artifact(raw.cached(SOURCE, PAGE), SOURCE_ID)
    for year in YEARS:
        p.artifact(raw.cached(SOURCE, url(year)), SOURCE_ID)
    p.add("body", id=BODY, polity_id=BELGIUM, kind="council")
    p.name(BODY, "Municipal councils of the Brussels-Capital Region", "en", "official")
    p.name(BODY, "Conseils communaux de la Région de Bruxelles-Capitale", "fr", "native", False)
    p.name(BODY, "Gemeenteraden van het Brussels Hoofdstedelijk Gewest", "nl", "native", False)
    for id, (label, commune) in sorted(labels().items(), key=lambda item: item[1]):
        p.add("party", id=id, kind="party", polity_id=BELGIUM)
        p.name(id, f"{label} ({commune})" if commune else label, "und", "official")
    return p


def date_of(name: str, year: int) -> tuple[date, date, str]:
    if name == RERUN:
        return date(2025, 2, 1), date(2025, 2, 28), "month"
    return days()[year], days()[year], "day"


def election(name: str, year: int, rerun: bool) -> Package:
    p = Package(
        f"{SOURCE}/{name}",
        SOURCE,
        f"The {name[:4]} Brussels municipal council "
        + ("election in Saint-Josse-ten-Noode" if rerun else "elections")
        + " by municipality",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(BELGIUM)
    p.uses(raw.cached(SOURCE, url(year)))
    start, end, precision = date_of(name, year)
    election_id = p.id("election", name)
    rerun_of = p.id("election", days()[year]) if rerun else None
    p.add(
        "election",
        id=election_id,
        polity_id=BELGIUM,
        kind="local",
        date_from=start,
        date_to=end,
        date_precision=precision,
        status="held",
        rerun_of_id=rerun_of,
    )
    title = (
        f"{start.year} Saint-Josse-ten-Noode municipal election"
        if rerun
        else f"{start.year} Brussels municipal elections"
    )
    p.name(election_id, title, "en", "official")
    base = f"be/{name}-saint-josse-local" if rerun else f"be/{name}-local-brussels"
    p.add("slug", path=base, entity_id=election_id, is_current=True)
    wanted = [c for c in communes(year) if c.rerun == rerun]
    by_nis = {str(row["nis"]): row for row in boundaries()}
    short: list[tuple[str, int]] = []
    for commune in wanted:
        nis = nis_of()[commune.name]
        row = by_nis[nis]
        contest = p.id("contest", name, nis)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=BODY,
            district_area_id=area(nis),
            magnitude=commune.seats,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"Municipal council of {commune.name}", "en", "official")
        p.name(contest, f"Conseil communal de {row['fr']}", "fr", "native", False)
        p.name(contest, f"Gemeenteraad van {row['nl']}", "nl", "native", False)
        p.add("slug", path=f"{base}/{changeset.slug(commune.name)}", entity_id=contest, is_current=True)
        result_set = p.id("result_set", name, nis)
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
        p.add(
            "note",
            id=p.id("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=RERUN_METHOD if rerun else METHOD,
            lang="en",
        )
        unit = p.id("unit", name, nis)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BELGIUM,
            scheme_code=SCHEME,
            code=f"{name}:{nis}",
            area_relation="equals",
            area_id=area(nis),
            kind="standard",
        )
        for entry in sorted(commune.lists, key=lambda e: (-e.votes, e.label)):
            option = p.id("option", name, nis, folded(entry.label))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(entry.label, commune.name),
                ballot_label=entry.label,
            )
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=entry.votes,
            )
            if entry.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=entry.seats)
        for measure, value in ((2, commune.registered), (3, commune.cast), (4, commune.valid), (6, commune.blank)):
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
        p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=commune.valid)
        if sum(e.votes for e in commune.lists) != commune.valid:
            short.append((commune.name, sum(e.votes for e in commune.lists) - commune.valid))
    if short:
        p.accept(
            "accounting.votes_valid",
            "the workbook prints a valid-vote total below the sum of its own list votes in "
            + ", ".join(f"{name} (by {gap:,})" for name, gap in short)
            + ", where the gap equals the votes of lists that won no seat, and the ballots cast equal that printed valid "
            "total plus the printed blank and invalid ballots; the printed figures and the list votes are stored as "
            "printed",
        )
    return p
