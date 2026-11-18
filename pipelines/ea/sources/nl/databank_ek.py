"""Eerste Kamer elections 2003-2023, island council elections 2011-2023, and the electoral colleges of 2019 and 2023,
from the Kiesraad's Databank Verkiezingsuitslagen (CC0) and the 2023 CSVs on data.overheid.nl.

The Senate is elected in one nationwide list-PR contest by the members of the twelve Provincial Councils and, since
2019, the electoral colleges of Bonaire, Saba, and Sint Eustatius and, since 2023, the college of non-resident
voters. Each elector's vote is weighted by the population the elector stands for. The databank's detail page of each
election embeds the national result and serves one JSON document per province or college. Elections up to 2007
publish only the weighted votes; 2011 and 2015 only the votes the electors cast; 2019 and 2023 both.

The island council and electoral college elections of the Caribbean Netherlands are one contest per island (and one
for the non-resident college), counted by island; the 2020 Sint Eustatius election is counted by polling station.
The 2023 CSVs add every candidate's votes and the elected members."""

import functools
import html
import json
import re
from collections import defaultdict
from collections.abc import Iterator
from datetime import date
from typing import Any, NamedTuple
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.nl import verkiezingsuitslagen as databank

SOURCE = "nl.databank_ek"
HOST = databank.HOST
EK = ["EK20030526", "EK20070529", "EK20110523", "EK20150526", "EK20190527", "EK20230530"]
ER = ["ER20110302", "ER20150318", "ER20190320", "ER20201021", "ER20230315"]
KC = ["KC20190320", "KC20230315"]
CODES = [*EK, *ER, *KC]
OPEN = "https://data.overheid.nl/sites/default/files/dataset/"
CSV = {
    "ER20230315": OPEN + "139bd69c-dfc7-47e3-9cfa-79d789769079/resources/Verkiezingsuitslag_ER2023.csv",
    "KC20230315": OPEN + "b4beb17d-37a1-4f23-a888-3f764e9badbc/resources/uitslag_Kiescolleges_2023.csv",
}
PLAN_YEARS = range(2003, 2024)
PDOK = databank.PDOK
REGISTERED, VOTES, VALID, INVALID, BLANK, PREFERENCE, WEIGHTED = 2, 1, 4, 5, 6, 7, 11
NETHERLANDS = databank.NETHERLANDS
COUNTRY = databank.COUNTRY
PROVINCE_SCHEME = "nl.provincie_ek"
ISLAND_SCHEME = "nl.openbaar_lichaam_ek"
STATION_SCHEME = "nl.stembureau_ek"
ISLANDS = {"bonaire": "NL-BQ1", "saba": "NL-BQ2", "sint eustatius": "NL-BQ3"}
ABROAD = "abroad"
STATION = re.compile(r"^(\d{3}) - (.+)$")
KINDS = {
    "EK": ("indirect", "eerste-kamer", "Dutch Senate election", "Eerste Kamerverkiezing"),
    "ER": ("local", "eilandsraad", "Dutch Caribbean island council elections", "Eilandsraadsverkiezingen"),
    "KC": ("local", "kiescollege", "Dutch Caribbean electoral college elections", "Kiescollegeverkiezingen"),
}
TOTALS_REPORT: dict[str, str] = {}


def day(code: str) -> date:
    return databank.day(code)


def page_url(code: str) -> str:
    return databank.page_url(code)


def region_url(code: str, region: int) -> str:
    return databank.region_json_url(code, region)


def shape_url(year: int) -> str:
    return databank.shape_url(year, "provincie")


def embedded(code: str) -> dict[str, Any]:
    text = raw.cached(SOURCE, page_url(code)).path.read_text(encoding="utf-8")
    found = re.search(r'<textarea id="UitslagData"[^>]*>(.*?)</textarea>', text, re.DOTALL)
    assert found is not None, f"{code}: the page embeds no result"
    data = json.loads(html.unescape(found.group(1)))
    assert data["Info"]["Code"] == code
    return data


@functools.cache
def regions(code: str) -> list[tuple[int, str]]:
    levels = embedded(code)["Regios"]["Regios"]
    options = [o for level in levels for o in level["Options"]]
    return [(int(o["Id"]), databank.clean(o["Value"])) for o in options if o["Value"] != "-"]


def fetch() -> None:
    ctx = databank.context()
    for code in CODES:
        raw.fetch(SOURCE, page_url(code), suffix=".html", verify=ctx)
        for region, _ in regions(code):
            raw.fetch(SOURCE, region_url(code, region), suffix=".json", verify=ctx)
    for url in CSV.values():
        raw.fetch(SOURCE, url, suffix=".csv")
    for year in PLAN_YEARS:
        raw.fetch(SOURCE, shape_url(year), suffix=".json")


class Row(NamedTuple):
    label: str
    uri: str | None
    votes: int | None
    weighted: int | None
    seats: int | None


class Region(NamedTuple):
    id: int
    name: str
    registered: int | None
    valid: int | None
    blank: int | None
    invalid: int | None
    weight: int | None
    rows: list[Row]


def _region(data: dict[str, Any]) -> Region:
    stem = data["Stemregio"]
    whole = databank._whole
    return Region(
        id=int(stem["StemregioId"]),
        name=databank.clean(stem["Naam"]),
        registered=whole(stem["Kiesgerechtigden"]),
        valid=whole(stem["AantalGeldigeStemmen"]),
        blank=whole(stem["AantalBlancoStemmen"]),
        invalid=whole(stem["AantalOngeldigeStemmen"]),
        weight=whole(stem["Stemwaarde"]),
        rows=[
            Row(
                databank.clean(row["Naam"]),
                row["PartijUri"] or None,
                whole(row["AantalStemmen"]),
                whole(row["AantalGewogenstemmen"]),
                whole(row["AantalZetels"]),
            )
            for row in stem["Partij"]
        ],
    )


@functools.cache
def national(code: str) -> Region:
    return _region(embedded(code))


@functools.cache
def part(code: str, region: int) -> Region:
    path = raw.cached(SOURCE, region_url(code, region)).path
    return _region(json.loads(path.read_text(encoding="utf-8")))


def parts(code: str) -> list[Region]:
    """The province, island, or college regions of an election, or its polling stations."""
    return [part(code, region) for region, _ in regions(code)]


def key(kind: str, *items: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(item) for item in items)])))


def fold(text: str) -> str:
    return databank.fold(text)


KIESRAAD = key("source", "kiesraad.databank")
OPEN_DATA = key("source", "kiesraad.open")
GEO_SOURCE = key("source", "cbs.provincie")


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def unit(code: str) -> str:
    return key("unit", code)


def station_unit(code: str, number: str) -> str:
    return key("unit", STATION_SCHEME, f"{code}:{number}")


def body(kind: str, code: str) -> str:
    return key("body", kind, code)


def party(label: str) -> str:
    return key("party", party_uris().get(fold(label), fold(label)))


@functools.cache
def party_uris() -> dict[str, str]:
    """The databank's own party key for a list label, where its pages tie one to a party page."""
    found: dict[str, set[str]] = {}
    for code in CODES:
        for region in [national(code), *parts(code)]:
            for row in region.rows:
                if row.uri:
                    found.setdefault(fold(row.label), set()).add(row.uri)
    return {label: next(iter(uris)) for label, uris in found.items() if len(uris) == 1}


@functools.cache
def labels() -> dict[str, dict[str, int]]:
    """Each party key's spellings of its list label, with how many elections use each."""
    out: dict[str, dict[str, int]] = {}
    for code in CODES:
        seen = {row.label for region in [national(code), *parts(code)] for row in region.rows}
        for text in seen:
            spellings = out.setdefault(party(text), {})
            spellings[text] = spellings.get(text, 0) + 1
    return out


def province_code(name: str) -> str:
    return databank.PROVINCE_CODES[fold(name)]


def station_number(name: str) -> str:
    found = STATION.match(name)
    assert found is not None, f"{name} is not a polling station"
    return found.group(1)


def region_unit(name: str) -> tuple[str, str | None, str]:
    """A region's unit id, its area id (None where it has no area), and its scheme."""
    folded = fold(name)
    if folded in databank.PROVINCE_CODES:
        number = province_code(name)
        return unit(number), area(PROVINCE_SCHEME, number), PROVINCE_SCHEME
    if folded in ISLANDS:
        return unit(ISLANDS[folded]), area(ISLAND_SCHEME, ISLANDS[folded]), ISLAND_SCHEME
    assert folded in ("buitenland", "niet ingezetenen"), f"unknown region {name}"
    return unit(ABROAD), None, ""


def package_key(code: str) -> str:
    return f"{SOURCE}/{code[:2].lower()}-{day(code)}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(code) for code in CODES)]


def _artifacts(code: str) -> list[raw.Artifact]:
    found = [raw.cached(SOURCE, page_url(code))]
    found += [raw.cached(SOURCE, region_url(code, region)) for region, _ in regions(code)]
    if code in CSV:
        found.append(raw.cached(SOURCE, CSV[code]))
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Dutch provinces, Caribbean public bodies, the Senate, island councils, and electoral colleges, and the "
        "parties of their elections from the Kiesraad's databank",
    )
    p.external |= {NETHERLANDS, COUNTRY}
    p.add(
        "source",
        id=KIESRAAD,
        kind="official_body",
        publisher="Kiesraad",
        title="Databank Verkiezingsuitslagen (Eerste Kamer, eilandsraden, kiescolleges)",
        url=HOST,
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Kiesraad",
    )
    p.add(
        "source",
        id=OPEN_DATA,
        kind="official_body",
        publisher="Kiesraad",
        title="Verkiezingsuitslag eilandsraad en kiescolleges 2023 (open data)",
        url="https://data.overheid.nl/dataset/verkiezingsuitslag-eilandsraad-2023",
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Kiesraad",
    )
    for code in CODES:
        for artifact in _artifacts(code):
            p.artifact(artifact, OPEN_DATA if artifact.url in CSV.values() else KIESRAAD)
    for code, rank, kind, noun, system in (
        (PROVINCE_SCHEME, 1, "administrative", "province", "iso_3166_2"),
        (ISLAND_SCHEME, 1, "administrative", "public body", "iso_3166_2"),
        (STATION_SCHEME, 3, "precinct", "polling station", None),
    ):
        p.add("scheme", code=code, polity_id=NETHERLANDS, kind=kind, rank=rank, noun=noun, code_system=system)

    def place(scheme: str, code: str, text: str, nl_body: str | None = None) -> str:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, text, "nl", "official")
        p.add(
            "reporting_unit",
            id=unit(code),
            polity_id=NETHERLANDS,
            scheme_code=scheme,
            code=code,
            area_relation="equals",
            area_id=id,
            kind="standard",
        )
        _ = nl_body
        return id

    names: dict[str, str] = {}
    for code in CODES:
        for region in parts(code):
            if not STATION.match(region.name):
                names.setdefault(fold(region.name), region.name)
    for folded, text in sorted(names.items()):
        if folded in databank.PROVINCE_CODES:
            place(PROVINCE_SCHEME, province_code(text), text)
        elif folded in ISLANDS:
            place(ISLAND_SCHEME, ISLANDS[folded], text)
    p.add(
        "reporting_unit",
        id=unit(ABROAD),
        polity_id=NETHERLANDS,
        scheme_code=ISLAND_SCHEME,
        code=ABROAD,
        area_relation="unlocated",
        kind="postal_district",
    )
    for code in ER:
        for region in parts(code):
            found = STATION.match(region.name)
            if found:
                island = next(r for r in parts(code) if not STATION.match(r.name))
                p.add(
                    "reporting_unit",
                    id=station_unit(code, found.group(1)),
                    polity_id=NETHERLANDS,
                    scheme_code=STATION_SCHEME,
                    code=f"{code}:{found.group(1)}",
                    parent_id=region_unit(island.name)[0],
                    area_relation="within",
                    area_id=region_unit(island.name)[1],
                    kind="standard",
                )
    senate = body("EK", "NL")
    p.add("body", id=senate, polity_id=NETHERLANDS, kind="upper_house")
    p.name(senate, "Eerste Kamer der Staten-Generaal", "nl", "official")
    p.name(senate, "Senate of the Netherlands", "en", "official", False)
    for number, text in sorted({v: ISLAND_NAMES[k] for k, v in ISLANDS.items()}.items()):
        p.add("body", id=body("ER", number), polity_id=NETHERLANDS, kind="council")
        p.name(body("ER", number), f"Eilandsraad van {text}", "nl", "official")
        p.name(body("ER", number), f"Island Council of {text}", "en", "official", False)
        p.add("body", id=body("KC", number), polity_id=NETHERLANDS, kind="electoral_college")
        p.name(body("KC", number), f"Kiescollege {text}", "nl", "official")
        p.name(body("KC", number), f"Electoral College of {text}", "en", "official", False)
    p.add("body", id=body("KC", ABROAD), polity_id=NETHERLANDS, kind="electoral_college")
    p.name(body("KC", ABROAD), "Kiescollege niet-ingezetenen", "nl", "official")
    p.name(body("KC", ABROAD), "Electoral college of non-resident voters", "en", "official", False)
    for id, spellings in sorted(labels().items()):
        ranked = sorted(spellings, key=lambda text: (spellings[text], text), reverse=True)
        p.add("party", id=id, kind="party", polity_id=NETHERLANDS)
        for rank, text in enumerate(ranked):
            p.name(id, text, "nl", "official", rank == 0)
    return p


ISLAND_NAMES = {"bonaire": "Bonaire", "saba": "Saba", "sint eustatius": "Sint Eustatius"}


# ---- boundaries ----


@functools.cache
def shapes() -> dict[str, list[tuple[int, shapely.Geometry]]]:
    """The province shapes by area code, each kept from the first plan year it appears in or changes in."""
    out: dict[str, list[tuple[int, shapely.Geometry]]] = {}
    for year in PLAN_YEARS:
        data = json.loads(raw.cached(SOURCE, shape_url(year)).path.read_bytes())
        for feature in data["features"]:
            number = databank.PROVINCE_STAT[str(feature["properties"]["statcode"])]
            shape = databank._polygons(feature)
            history = out.setdefault(number, [])
            if not history or not databank._same(history[-1][1], shape):
                history.append((year, shape))
    return out


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Boundaries of the Dutch provinces from Statistics Netherlands' generalized area divisions of each year "
        "2003-2023",
        depends=[f"{SOURCE}/registry"],
    )
    p.add(
        "source",
        id=GEO_SOURCE,
        kind="official_body",
        publisher="Centraal Bureau voor de Statistiek (CBS), through PDOK",
        title="CBS Gebiedsindelingen, provinces (generalized), 2003-2023",
        url="https://www.pdok.nl/introductie/-/article/cbs-gebiedsindelingen",
        license="Creative Commons Attribution 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Centraal Bureau voor de Statistiek (CBS), Kadaster; PDOK",
        notes="One plan for each year 2003-2023, as the area division stood on 1 January.",
    )
    for year in PLAN_YEARS:
        p.artifact(raw.cached(SOURCE, shape_url(year)), GEO_SOURCE)
    for number, history in sorted(shapes().items()):
        for index, (year, shape) in enumerate(history):
            following = history[index + 1][0] if index + 1 < len(history) else None
            p.add(
                "area_geometry",
                area_id=area(PROVINCE_SCHEME, number),
                valid_from=date(year, 1, 1),
                valid_to=date(following, 1, 1) if following else None,
                geom=shapely.to_wkb(shape, hex=True, include_srid=True),
                label_point=shapely.to_wkb(
                    shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
                ),
                quality="official",
                source_id=GEO_SOURCE,
            )
    return p


# ---- elections ----


def _election(p: Package, code: str) -> tuple[str, str]:
    when = day(code)
    kind, slug, english, native = KINDS[code[:2]]
    election = key("election", code)
    p.add(
        "election",
        id=election,
        polity_id=NETHERLANDS,
        kind=kind,
        date_from=when,
        date_to=when,
        date_precision="day",
        status="held",
    )
    base = f"nl/{when}-{slug}"
    title = f"{when.year} {english}" if code[:2] == "EK" else f"{english} of {when}"
    p.name(election, title, "en", "official")
    p.name(election, f"{native} {when.year}" if code[:2] == "EK" else f"{native} van {when}", "nl", "native", False)
    p.add("slug", path=base, entity_id=election, is_current=True)
    return election, base


def _options(p: Package, contest: str, code: str, scope: str, rows: list[Row]) -> dict[str, str]:
    options: dict[str, str] = {}
    for position, row in enumerate(rows, start=1):
        options[row.label] = key("option", code, scope, row.label)
        p.add(
            "contest_option",
            id=options[row.label],
            contest_id=contest,
            kind="list",
            party_id=party(row.label),
            ballot_position=position,
            ballot_label=row.label,
        )
    return options


def _frame(rows: list[tuple[str, str | None, str | None, int, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "result_set_id": pl.String,
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "value": pl.Int64,
        },
        orient="row",
    ).with_columns(
        channel=pl.lit(0, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        flags=pl.lit(0, pl.Int16),
    )


def _accounting(
    out: list[tuple[str, str | None, str | None, int, int]],
    result_set: str,
    unit_id: str,
    region: Region,
    weighted: bool = False,
) -> None:
    """A unit's electorate, valid, invalid, and blank figures. Where the figures are weighted, the valid votes are
    left out: they are the sum of the weighted list votes, which are not stored as votes."""
    for measure, value in (
        (REGISTERED, region.registered),
        (VALID, None if weighted else region.valid),
        (INVALID, region.invalid),
        (BLANK, region.blank),
    ):
        if value is not None:
            out.append((result_set, unit_id, None, measure, value))


SENATE_METHOD = (
    "Votes for each list in each province, each Caribbean electoral college, and (from 2023) the college of "
    "non-resident voters, as the Kiesraad's Databank Verkiezingsuitslagen publishes them. The Senate is elected by "
    "the members of the Provincial Councils and the colleges, and each member's vote is weighted by the population "
    "the member stands for. The databank's list figures are the weighted votes in 2003 and 2007 (stored as electoral "
    "votes, with that election's electorate and blank figures weighted too, and its valid votes left out as they are the sum of the list votes), the votes the electors cast in "
    "2011 and 2015 (stored as votes, which the databank gives unweighted), and both in 2019 and 2023. Seats are "
    "declared nationally."
)


def _senate(p: Package, code: str, election: str, base: str) -> pl.DataFrame:
    whole = national(code)
    weighted_only = not any(row.weighted for row in whole.rows) and day(code).year < 2011
    contest = key("contest", code, "national")
    seats = sum(row.seats or 0 for row in whole.rows)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=body("EK", "NL"),
        magnitude=seats or None,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, "Eerste Kamer", "nl", "official")
    p.add("slug", path=f"{base}/national", entity_id=contest, is_current=True)
    options = _options(p, contest, code, "national", whole.rows)
    result_set = key("result_set", code, "national")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=KIESRAAD,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=SENATE_METHOD, lang="en"
    )
    rows: list[tuple[str, str | None, str | None, int, int]] = []
    sums: dict[tuple[str, int], int] = defaultdict(int)
    weights: list[str] = []
    for region in parts(code):
        unit_id = region_unit(region.name)[0]
        _accounting(rows, result_set, unit_id, region, weighted_only)
        if region.weight:
            weights.append(f"{region.name} {region.weight:,}")
        for row in region.rows:
            if row.votes is not None:
                measure = WEIGHTED if weighted_only else VOTES
                rows.append((result_set, unit_id, options[row.label], measure, row.votes))
                sums[row.label, measure] += row.votes
            if row.weighted is not None:
                rows.append((result_set, unit_id, options[row.label], WEIGHTED, row.weighted))
                sums[row.label, WEIGHTED] += row.weighted
    for row in whole.rows:
        measure = WEIGHTED if weighted_only else VOTES
        if row.votes is not None:
            assert sums[row.label, measure] == row.votes, f"{code}: {row.label} does not add up"
            p.add("declared", result_set_id=result_set, option_id=options[row.label], measure=measure, value=row.votes)
        if row.weighted is not None:
            assert sums[row.label, WEIGHTED] == row.weighted, f"{code}: {row.label} weighted does not add up"
            p.add(
                "declared", result_set_id=result_set, option_id=options[row.label], measure=WEIGHTED, value=row.weighted
            )
        if row.seats:
            p.add("outcome", result_set_id=result_set, option_id=options[row.label], kind="won", seats=row.seats)
    if weighted_only:
        assert whole.valid == sum(r.votes or 0 for r in whole.rows), f"{code}: valid votes differ from the lists"
    for measure, value in (
        (REGISTERED, whole.registered),
        (VALID, None if weighted_only else whole.valid),
        (BLANK, whole.blank),
    ):
        if value is not None:
            total = sum(v for r, u, o, m, v in rows if m == measure and o is None)
            assert total == value, f"{code}: measure {measure} regions {total} vs national {value}"
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    if weights:
        p.add(
            "note",
            id=key("note", result_set, "weights"),
            entity_id=result_set,
            kind="caveat",
            body_md="Vote weight (stemwaarde) of each member's vote, where the databank states it: "
            + "; ".join(weights)
            + ".",
            lang="en",
        )
    listed = sum(row.votes or 0 for row in whole.rows)
    TOTALS_REPORT[code] = (
        f"{code}: provinces and colleges add up to the national list votes {listed:,}, valid {whole.valid:,}, "
        f"{seats} seats"
    )
    return _frame(rows)


ISLAND_METHOD = (
    "Votes for each list on one island or in one electoral college, as the Kiesraad's Databank Verkiezingsuitslagen "
    "publishes them from the official reports. The databank counts the electorate, valid votes, blank votes, and "
    "invalid votes of the island, and its lists' votes and seats."
)
CSV_METHOD = (
    " The Kiesraad's CSV adds each candidate's votes and the members elected; candidates' votes are stored as "
    "preference votes within their list."
)


class Candidate(NamedTuple):
    list_number: int
    list_label: str
    number: int
    initials: str
    first: str
    infix: str
    last: str
    gender: str
    votes: int
    elected: bool


@functools.cache
def candidates(code: str) -> dict[str, list[Candidate]]:
    """The candidates of one 2023 CSV, by region name."""
    frame = pl.read_csv(
        raw.cached(SOURCE, CSV[code]).path, separator=";", encoding="utf8", infer_schema=False, ignore_errors=True
    ).filter(pl.col("KandidaatNummer").is_not_null() & (pl.col("KandidaatNummer") != ""))
    out: dict[str, list[Candidate]] = defaultdict(list)
    keyed: dict[tuple[str, str, str], dict[str, str]] = defaultdict(dict)
    for row in frame.iter_rows(named=True):
        keyed[row["Regio"], row["LijstNummer"], row["KandidaatNummer"]].update(
            {k: (v or "") for k, v in row.items() if k not in ("VeldType", "Waarde")} | {row["VeldType"]: row["Waarde"]}
        )
    for (name, _, _), fields in keyed.items():
        out[databank.clean(name)].append(
            Candidate(
                int(fields["LijstNummer"]),
                databank.clean(fields["LijstNaam"]),
                int(fields["KandidaatNummer"]),
                fields["KandidaatInitialen"],
                fields["KandidaatVoornaam"],
                fields["KandidaatTussenvoegsel"],
                fields["KandidaatAchternaam"],
                fields["KandidaatGeslacht"],
                int(fields.get("KandidaatAantalStemmen") or 0),
                fields.get("KandidaatGekozen") == "1",
            )
        )
    return out


def _csv_lists(code: str) -> dict[str, dict[str, tuple[int, int]]]:
    """For each region of a 2023 CSV, each list's label with its votes and seats, as the file states them."""
    frame = pl.read_csv(
        raw.cached(SOURCE, CSV[code]).path, separator=";", encoding="utf8", infer_schema=False, ignore_errors=True
    ).filter(pl.col("VeldType").is_in(["LijstAantalStemmen", "LijstAantalZetels"]))
    out: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for row in frame.iter_rows(named=True):
        slot = out[databank.clean(row["Regio"])][databank.clean(row["LijstNaam"])]
        slot[0 if row["VeldType"] == "LijstAantalStemmen" else 1] = int(row["Waarde"] or 0)
    return {region: {label: (v[0], v[1]) for label, v in lists.items()} for region, lists in out.items()}


def _person_name(c: Candidate) -> str:
    return " ".join(part for part in (c.first or c.initials, c.infix, c.last) if part)


def _island(p: Package, code: str, election: str, base: str) -> pl.DataFrame:
    category = code[:2]
    csv = code in CSV
    all_regions = parts(code)
    stations = [r for r in all_regions if STATION.match(r.name)]
    totals = [r for r in all_regions if not STATION.match(r.name)]
    listed = _csv_lists(code) if csv else {}
    cands = candidates(code) if csv else {}
    rows: list[tuple[str, str | None, str | None, int, int]] = []
    taken: set[str] = set()
    for region in totals:
        folded = fold(region.name)
        abroad = folded in ("buitenland", "niet ingezetenen")
        number = ABROAD if abroad else ISLANDS[folded]
        contest = key("contest", code, number)
        seats = sum(row.seats or 0 for row in region.rows)
        district = None if abroad else area(ISLAND_SCHEME, number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body(category, number),
            district_area_id=district,
            magnitude=seats or None,
            round_number=1,
            default_result_kind="counted",
        )
        title = "niet-ingezetenen" if abroad else region.name
        noun = "Eilandsraad" if category == "ER" else "Kiescollege"
        p.name(contest, f"{noun} {title}", "nl", "official")
        slug = f"{base}/{changeset.slug(title)}"
        assert slug not in taken
        taken.add(slug)
        p.add("slug", path=slug, entity_id=contest, is_current=True)
        options = _options(p, contest, code, number, region.rows)
        result_set = key("result_set", code, number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=OPEN_DATA if csv else KIESRAAD,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=ISLAND_METHOD + (CSV_METHOD if csv else ""),
            lang="en",
        )
        if csv:
            found = listed[region.name]
            page = {row.label: (row.votes or 0, row.seats or 0) for row in region.rows}
            assert all(page.get(label) == figures for label, figures in found.items()), (
                f"{code} {region.name}: the CSV and the page differ"
            )
            # The page's Saba list of 2023 carries 95 votes for a list ("Blanco (Lijst 4)") the CSV leaves out.
            left = sum(votes for label, (votes, _) in page.items() if label not in found)
            assert left == (region.valid or 0) - sum(votes for votes, _ in found.values()), (
                f"{code} {region.name}: list votes do not add up to the valid votes"
            )
        here = stations if not abroad and len(totals) == 1 else []
        scope = [(station_unit(code, station_number(r.name)), r) for r in here]
        if not scope:
            scope = [(region_unit(region.name)[0], region)]
        for unit_id, source in scope:
            _accounting(rows, result_set, unit_id, source)
            for row in source.rows:
                if row.votes is not None:
                    rows.append((result_set, unit_id, options[row.label], VOTES, row.votes))
        if here:
            for measure, value in (
                (REGISTERED, region.registered),
                (VALID, region.valid),
                (INVALID, region.invalid),
                (BLANK, region.blank),
            ):
                if value is not None:
                    p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            for row in region.rows:
                if row.votes is not None:
                    p.add(
                        "declared",
                        result_set_id=result_set,
                        option_id=options[row.label],
                        measure=VOTES,
                        value=row.votes,
                    )
            for measure in (REGISTERED, VALID, INVALID, BLANK):
                given = sum(v for r, u, o, m, v in rows if r == result_set and m == measure and o is None)
                stated = {
                    REGISTERED: region.registered,
                    VALID: region.valid,
                    INVALID: region.invalid,
                    BLANK: region.blank,
                }[measure]
                assert stated is None or given == stated or measure == REGISTERED, f"{code}: station totals differ"
        for row in region.rows:
            if row.seats:
                p.add("outcome", result_set_id=result_set, option_id=options[row.label], kind="won", seats=row.seats)
        for c in cands.get(region.name, []):
            list_option = options[c.list_label]
            tag = (code, number, c.list_number, c.number)
            candidate = key("option", *tag)
            person = key("person", *tag)
            name = _person_name(c)
            ballot = " ".join(part for part in (c.initials, c.infix, c.last) if part)
            p.add(
                "contest_option",
                id=candidate,
                contest_id=contest,
                kind="candidate",
                parent_option_id=list_option,
                party_id=party(c.list_label),
                ballot_position=c.number,
                ballot_label=ballot,
            )
            p.add("person", id=person, gender={"m": "male", "v": "female"}.get(c.gender))
            p.name(person, name, "nl", "official")
            candidacy = key("candidacy", *tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=list_option,
                role="list_member",
                list_position=c.number,
                ballot_name=ballot,
                member_of_party_id=party(c.list_label),
                is_independent=False,
            )
            rows.append((result_set, region_unit(region.name)[0], candidate, PREFERENCE, c.votes))
            if c.elected:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=list_option,
                    candidacy_id=candidacy,
                    kind="elected",
                )
    return _frame(rows)


def _totals(p: Package, code: str, election: str) -> None:
    whole = parts(code)
    cast = sum((r.valid or 0) + (r.blank or 0) + (r.invalid or 0) for r in whole if not STATION.match(r.name))
    registered = sum(r.registered or 0 for r in whole if not STATION.match(r.name))
    TOTALS_REPORT[code] = f"{code}: regions' valid + blank + invalid votes {cast:,} of {registered:,} registered voters"
    p.add(
        "note",
        id=key("note", election, "totals"),
        entity_id=election,
        kind="caveat",
        body_md=f"The databank's figures add up to {cast:,} votes cast (valid, blank, and invalid) among {registered:,} "
        "registered voters.",
        lang="en",
    )


def build(code: str) -> Package:
    p = Package(
        package_key(code),
        SOURCE,
        f"{KINDS[code[:2]][2]} of {day(code)} from the Kiesraad's databank",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {NETHERLANDS, COUNTRY}
    for artifact in _artifacts(code):
        p.uses(artifact)
    election, base = _election(p, code)
    if code.startswith("EK"):
        values = _senate(p, code, election, base)
    else:
        values = _island(p, code, election, base)
        _totals(p, code, election)
    p.frame("result_value", values)
    return p


def parse() -> None:
    registry().write()
    geo().write()
    for code in CODES:
        build(code).write()
    for line in sorted(TOTALS_REPORT.values()):
        print(line)


def emit() -> None:
    package.run("load", packages())


def iterate() -> Iterator[str]:
    yield from CODES
