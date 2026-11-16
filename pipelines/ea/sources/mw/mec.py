import functools
import re
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.mw import legacy, pdf

SOURCE = "mw.mec"
MALAWI = str(uuid5(package.NAMESPACE, "polity\x1fMW"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fMWI"))
PAGE = "https://mec.org.mw/election-results/"
DAYS = {1994: date(1994, 5, 17), 1999: date(1999, 6, 15), 2004: date(2004, 5, 20), 2009: date(2009, 5, 19)}
DAY_2019 = date(2019, 5, 21)
DAY_2020 = date(2020, 6, 23)
BASE = "https://mec.org.mw/wp-content/uploads"
FILES = {
    "1994": "2018/11/PPE-RESULTS-1994.pdf",
    "1999": "2018/11/PPE-RESULTS-1999.pdf",
    "2004-parliament": "2018/11/PARLIAMENTARY-SUMMARY-RESULTS-FOR-2004-ELECTIONS.pdf",
    "2009-president": "2018/11/PRESIDENTIAL-SUMMARY-RESULTS-FOR-2009-ELECTIONS.pdf",
    "2009-parliament": "2018/11/Parliamentary-Results-@29May09.pdf",
    "2019-president": "2022/11/2019-Presidential-Results-By-Polling-Station.pdf",
    "2019-parliament": "2022/11/2019-Parliamentary-Results-By-Polling-Station.pdf",
    "2019-local": "2022/11/2019-Local-Government-Results-By-Polling-Station.pdf",
    "2020-president": "2022/11/2020-Fresh-Presidential-Election-Results-Per-station.pdf",
}


def url(name: str) -> str:
    return f"{BASE}/{FILES[name]}"


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, url(name), suffix=".pdf")


def path(name: str) -> Path:
    return raw.DATA / raw.cached(SOURCE, url(name)).storage_key


def split(text: str) -> tuple[str, str]:
    """Split a printed place such as `Chitipa East 001` into its name and code."""
    found = re.fullmatch(r"(.*?)\s+(\d+)", text.replace(" Region ", " "))
    if found is None:
        raise ValueError(f"no code in {text!r}")
    return found[1].strip(), found[2]


def centres(name: str) -> pl.DataFrame:
    """One line per candidate at each station of a 2019 contest, with the station's totals, cached as Parquet."""
    cache = raw.parsed(SOURCE) / f"{name}.parquet"
    if cache.exists():
        return pl.read_parquet(cache)
    rows: list[dict[str, object]] = []
    stations = list(pdf.read_stations(path(name)))
    names = {
        text.rsplit(" ", 1)[1]: text.rsplit(" ", 1)[0]
        for text in (station.centre.text for station in stations)
        if " " in text
    }
    for station in stations:
        if sum(votes for _, _, votes in station.rows) != station.totals.get("valid") or station.totals.get(
            "valid", 0
        ) + station.totals.get("null", 0) != station.totals.get("cast"):
            raise ValueError(
                f"{name} page {station.page}: {station.centre.text} station {station.number} does not add up"
            )
        region, region_code = split(station.region.text)
        district, district_code = split(station.district.text)
        constituency, constituency_code = split(station.constituency.text)
        ward, ward_code = split(station.ward.text)
        text = station.centre.text
        centre_code = text.rsplit(" ", 1)[-1]
        centre = text.rsplit(" ", 1)[0] if " " in text else names[centre_code]
        for candidate, party, votes in station.rows:
            rows.append(
                {
                    "page": station.page,
                    "region": region,
                    "region_code": region_code,
                    "district": district,
                    "district_code": district_code,
                    "constituency": constituency,
                    "constituency_code": constituency_code,
                    "ward": ward,
                    "ward_code": ward_code,
                    "centre": centre,
                    "centre_code": centre_code,
                    "station": station.number,
                    "candidate": candidate,
                    "party": party,
                    "votes": votes,
                    "valid": station.totals["valid"],
                    "null": station.totals["null"],
                    "cast": station.totals["cast"],
                }
            )
    frame = pl.DataFrame(rows)
    cache.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(cache)
    return frame


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


MEC_SOURCE = key("source", SOURCE)
ASSEMBLY = key("body", "national_assembly")
COUNCILS = key("body", "councils")
PRESIDENCY = key("office", "president")
SYSTEM_FPTP = key("electoral_system", "fptp")
SYSTEM_PRESIDENT_PLURALITY = key("electoral_system", "president", "plurality")
SYSTEM_PRESIDENT_MAJORITY = key("electoral_system", "president", "majority")
INDEPENDENT = {"IND", "INDEPENDENT"}
PARTY_FILES = ("2019-president", "2019-parliament", "2019-local")
FRESH_PARTIES = ("MCP", "MMD", "DPP")


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def parties() -> list[str]:
    found: set[str] = set(FRESH_PARTIES)
    for name in PARTY_FILES:
        found |= set(centres(name)["party"])
    for year in DAYS:
        for result in [*books(year)[0], *books(year)[1]]:
            found |= {code for _, code, _ in result.candidates}
    return sorted({norm(code) for code in found if not independent(code)})


def independent(code: str) -> bool:
    return code.upper().split(" ")[0] in INDEPENDENT


def norm(code: str) -> str:
    """A party's printed abbreviation, upper-cased because the source varies its case, such as UDF and UDf."""
    return "IND" if independent(code) else code.strip().upper()


@functools.cache
def books(year: int) -> tuple[list[legacy.Result], list[legacy.Result]]:
    """The year's constituency results for the National Assembly and its district results for President."""
    if year == 1994 or year == 1999:
        results = legacy.ppe(legacy.text(path(str(year))))
        return [r for r in results if r.kind == "parliament"], [r for r in results if r.kind == "president"]
    if year == 2004:
        return legacy.parliament_2004(path("2004-parliament")), []
    parliament = [r for r in legacy.parliament_2009(legacy.text(path("2009-parliament"))) if r.candidates]
    return parliament, legacy.president_2009(legacy.text(path("2009-president")))


def party(code: str) -> str:
    return key("party", norm(code))


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{day}" for day in DAYS.values()),
        f"{SOURCE}/{DAY_2019}",
        f"{SOURCE}/{DAY_2020}",
    ]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        general(year).write()
    tripartite().write()
    fresh().write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Malawi's 3 regions, 28 districts, 193 constituencies, 462 wards, and the polling stations of the 2019 "
        "and 2020 elections, as the Malawi Electoral Commission prints them",
    )
    p.external |= {MALAWI, COUNTRY}
    _source(p)
    for name in PARTY_FILES:
        p.artifact(raw.cached(SOURCE, url(name)), MEC_SOURCE)
    for code, rank, kind, noun in (
        ("mw.region", 1, "administrative", "region"),
        ("mw.district", 2, "administrative", "district"),
        ("mw.constituency", 3, "electoral_district", "constituency"),
        ("mw.ward", 4, "electoral_district", "ward"),
        ("mw.station", 5, "precinct", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=MALAWI, kind=kind, rank=rank, noun=noun, code_system=None)
    places = pl.concat([centres(name) for name in PARTY_FILES]).select(
        "region", "region_code", "district", "district_code", "constituency", "constituency_code", "ward", "ward_code"
    )
    for scheme, level, parent in (
        ("mw.region", "region", None),
        ("mw.district", "district", "region"),
        ("mw.constituency", "constituency", "district"),
        ("mw.ward", "ward", "constituency"),
    ):
        columns = [f"{level}_code", level] + ([f"{parent}_code"] if parent else [])
        for row in places.select(columns).unique(f"{level}_code").sort(f"{level}_code").iter_rows(named=True):
            id = area(scheme, row[f"{level}_code"])
            p.add("area", id=id, scheme_code=scheme)
            p.add("area_code", area_id=id, code=row[f"{level}_code"])
            p.add(
                "area_parent",
                area_id=id,
                parent_id=area(f"mw.{parent}", row[f"{parent}_code"]) if parent else COUNTRY,
            )
            p.name(id, row[level], "en", "official")
    return p


def _source(p: Package) -> None:
    p.add(
        "source",
        id=MEC_SOURCE,
        kind="official_body",
        publisher="Malawi Electoral Commission",
        title="Election results published by the Malawi Electoral Commission",
        url=PAGE,
        license="No license stated; published by the electoral authority for public use",
        attribution="Malawi Electoral Commission",
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Malawi's parties, National Assembly, local councils, presidency, and electoral systems",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {MALAWI}
    p.add("body", id=ASSEMBLY, polity_id=MALAWI, kind="unicameral")
    p.name(ASSEMBLY, "National Assembly", "en", "official")
    p.add("body", id=COUNCILS, polity_id=MALAWI, kind="council")
    p.name(COUNCILS, "Local government councils", "en", "official")
    p.add("office", id=PRESIDENCY, polity_id=MALAWI, kind="head_of_state")
    p.name(PRESIDENCY, "President of Malawi", "en", "official")
    for id, since, until, rules, method in (
        (
            SYSTEM_FPTP,
            date(1994, 1, 1),
            None,
            {"formula": "fptp"},
            "One member for each constituency or ward, elected by the most votes.",
        ),
        (
            SYSTEM_PRESIDENT_PLURALITY,
            date(1994, 1, 1),
            date(2020, 2, 3),
            {"formula": "fptp"},
            "The candidate with the most votes in the single national count is elected President.",
        ),
        (
            SYSTEM_PRESIDENT_MAJORITY,
            date(2020, 2, 3),
            None,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "The candidate with more than half of the valid votes is elected President; otherwise the two leading "
                "candidates go to a second round."
            ),
        ),
    ):
        p.add(
            "electoral_system", id=id, polity_id=MALAWI, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for code in parties():
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=MALAWI)
        p.add("external_id", entity_id=id, scheme="mw.mec.party", value=code)
        p.name(id, code, "en", "abbreviation")
    return p


METHOD = (
    "Counted by the Malawi Electoral Commission for each polling station, as its results file prints them. Each "
    "station's candidate votes add up to its total valid votes, and its valid and null and void votes add up to the "
    "total votes. The Commission's file declares no winners or seat counts, so none are stored."
)


def _contest(
    p: Package,
    election: str,
    tag: str,
    frame: pl.DataFrame,
    *,
    path: str,
    name: str,
    kind: str,
    district: str,
    system: str,
    body: str | None,
    office: str | None,
    units: dict[tuple[str, int], str],
    registered: dict[tuple[str, int], int] | None = None,
) -> str:
    """Add one contest, its options, its result set, and the station values read from `frame`."""
    contest = key("contest", tag)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind=kind,
        office_id=office,
        body_id=body,
        district_area_id=district,
        magnitude=1,
        round_number=1,
        electoral_system_id=system,
        default_result_kind="counted",
    )
    p.name(contest, name, "en", "official")
    p.add("slug", path=path, entity_id=contest, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=MEC_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options: dict[str, str] = {}
    for candidate, code in frame.select("candidate", "party").unique().sort("candidate").iter_rows():
        option = options[candidate] = key("option", tag, candidate)
        solo = independent(code)
        person = key("person", tag, candidate)
        p.add("person", id=person)
        p.name(person, candidate, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=None if solo else party(code),
            ballot_label=candidate,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, candidate),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate,
            member_of_party_id=None if solo else party(code),
            is_independent=solo,
        )
    rows = frame.with_columns(
        pl.col("candidate").replace_strict(options, return_dtype=pl.String).alias("option"),
        pl.struct("centre_code", "station")
        .map_elements(lambda row: units[(row["centre_code"], row["station"])], return_dtype=pl.String)
        .alias("unit"),
    )
    p.frame(
        "result_value",
        rows.select(
            pl.lit(result_set).alias("result_set_id"),
            pl.col("unit").alias("unit_id"),
            pl.col("option").alias("option_id"),
            pl.lit(0).alias("channel"),
            pl.lit(1).alias("measure"),
            pl.lit(0).alias("count_round"),
            pl.lit(0).alias("flags"),
            pl.col("votes").alias("value"),
        ),
    )
    stations = rows.unique(["centre_code", "station"])
    for measure, column in ((4, "valid"), (5, "null"), (3, "cast"), (2, "registered")):
        if column in stations.columns:
            p.frame(
                "result_value",
                stations.select(
                    pl.lit(result_set).alias("result_set_id"),
                    pl.col("unit").alias("unit_id"),
                    pl.lit(None, pl.String).alias("option_id"),
                    pl.lit(0).alias("channel"),
                    pl.lit(measure).alias("measure"),
                    pl.lit(0).alias("count_round"),
                    pl.lit(0).alias("flags"),
                    pl.col(column).alias("value"),
                ),
            )
    return result_set


def _units(p: Package, day: date, frames: list[pl.DataFrame]) -> dict[tuple[str, int], str]:
    stations = (
        pl.concat([f.select("centre_code", "centre", "station", "ward_code") for f in frames])
        .unique(["centre_code", "station"])
        .sort("centre_code", "station")
    )
    units: dict[tuple[str, int], str] = {}
    for row in stations.iter_rows(named=True):
        code = f"{day}:{row['centre_code']}:{row['station']}"
        unit = units[(row["centre_code"], row["station"])] = key("unit", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=MALAWI,
            scheme_code="mw.station",
            code=code,
            area_relation="within",
            area_id=area("mw.ward", row["ward_code"]),
            kind="standard",
        )
    return units


def tripartite() -> Package:
    p = Package(
        f"{SOURCE}/{DAY_2019}",
        SOURCE,
        "The 21 May 2019 tripartite elections for President, National Assembly, and local councils, by polling station",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {MALAWI, COUNTRY}
    for name in PARTY_FILES:
        p.uses(raw.cached(SOURCE, url(name)))
    president, parliament, local = (centres(name) for name in PARTY_FILES)
    units = _units(p, DAY_2019, [president, parliament, local])
    election = key("election", DAY_2019)
    path = f"mw/{DAY_2019}"
    p.add(
        "election", id=election, polity_id=MALAWI, kind="general", date_from=DAY_2019, date_to=DAY_2019, status="held"
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, "2019 Malawi tripartite elections", "en", "official")
    _contest(
        p,
        election,
        f"{DAY_2019}:president",
        president,
        path=f"{path}/president",
        name="2019 Malawi presidential election",
        kind="office",
        district=COUNTRY,
        system=SYSTEM_PRESIDENT_PLURALITY,
        body=None,
        office=PRESIDENCY,
        units=units,
    )
    for code in sorted(set(parliament["constituency_code"])):
        part = parliament.filter(pl.col("constituency_code") == code)
        name = part["constituency"][0]
        _contest(
            p,
            election,
            f"{DAY_2019}:parliament:{code}",
            part,
            path=f"{path}/parliament/{code}-{changeset.slug(name)}",
            name=f"2019 National Assembly election, {name}",
            kind="body_seats",
            district=area("mw.constituency", code),
            system=SYSTEM_FPTP,
            body=ASSEMBLY,
            office=None,
            units=units,
        )
    for code in sorted(set(local["ward_code"])):
        part = local.filter(pl.col("ward_code") == code)
        name = part["ward"][0]
        _contest(
            p,
            election,
            f"{DAY_2019}:local:{code}",
            part,
            path=f"{path}/local/{code}-{changeset.slug(name)}",
            name=f"2019 local government election, {name} ward ({part['district'][0]})",
            kind="body_seats",
            district=area("mw.ward", code),
            system=SYSTEM_FPTP,
            body=COUNCILS,
            office=None,
            units=units,
        )
    return p


def fresh() -> Package:
    p = Package(
        f"{SOURCE}/{DAY_2020}",
        SOURCE,
        "The 23 June 2020 fresh presidential election, by polling station",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/{DAY_2019}"],
    )
    p.external |= {MALAWI, COUNTRY}
    p.uses(raw.cached(SOURCE, url("2020-president")))
    p.uses(raw.cached(SOURCE, url("2019-president")))
    wards = (
        centres("2019-president")
        .select("centre_code", "ward_code")
        .unique("centre_code")
        .select("centre_code", "ward_code")
    )
    ward_of = dict(wards.iter_rows())
    names = ("LAZARUS CHAKWERA", "PETER DOMINICO SINOSI DRIVER KUWANI", "ARTHUR PETER MUTHARIKA")
    rows: list[dict[str, object]] = []
    for count in pdf.read_counts(path("2020-president"), 3):
        code = count.centre.text.split(" ", 1)[0]
        for name, party_code, votes in zip(names, FRESH_PARTIES, count.votes, strict=True):
            rows.append(
                {
                    "centre_code": code,
                    "centre": count.centre.text.split(" ", 1)[1],
                    "station": count.station,
                    "ward_code": ward_of[code],
                    "candidate": name,
                    "party": party_code,
                    "votes": votes,
                    "registered": count.registered,
                    "null": count.null,
                    "cast": count.cast,
                }
            )
    frame = pl.DataFrame(rows)
    units = _units(p, DAY_2020, [frame])
    election = key("election", DAY_2020)
    path_ = f"mw/{DAY_2020}"
    p.add(
        "election",
        id=election,
        polity_id=MALAWI,
        kind="general",
        date_from=DAY_2020,
        date_to=DAY_2020,
        status="held",
        rerun_of_id=key("election", DAY_2019),
    )
    p.add("slug", path=path_, entity_id=election, is_current=True)
    p.name(election, "2020 Malawi fresh presidential election", "en", "official")
    result_set = _contest(
        p,
        election,
        f"{DAY_2020}:president",
        frame,
        path=f"{path_}/president",
        name="2020 Malawi fresh presidential election",
        kind="office",
        district=COUNTRY,
        system=SYSTEM_PRESIDENT_MAJORITY,
        body=None,
        office=PRESIDENCY,
        units=units,
    )
    totals = pdf.read_total(path("2020-president"), 3)
    for name, value in zip(names, totals, strict=True):
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=key("option", f"{DAY_2020}:president", name),
            measure=1,
            value=value,
        )
    return p


LEGACY_FILES = {
    1994: ("1994",),
    1999: ("1999",),
    2004: ("2004-parliament",),
    2009: ("2009-president", "2009-parliament"),
}


def general(year: int) -> Package:
    """A general election from the printed constituency and district tables, one unit for each."""
    day = DAYS[year]
    parliament, president = books(year)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day:%-d %B %Y} general election: National Assembly by constituency"
        + (" and President by district" if president else ""),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {MALAWI, COUNTRY}
    for name in LEGACY_FILES[year]:
        p.uses(raw.cached(SOURCE, url(name)))
    election = key("election", day)
    path_ = f"mw/{day}"
    p.add("election", id=election, polity_id=MALAWI, kind="general", date_from=day, date_to=day, status="held")
    p.add("slug", path=path_, entity_id=election, is_current=True)
    p.name(election, f"{year} Malawi general election", "en", "official")
    constituency = f"mw.constituency.{year}"
    district = f"mw.district.{year}"
    for code, rank, kind, noun in (
        (district, 2, "administrative", "district"),
        (constituency, 3, "electoral_district", "constituency"),
    ):
        p.add("scheme", code=code, polity_id=MALAWI, kind=kind, rank=rank, noun=noun, code_system=None)
    if year == 2004:
        printed = legacy.national_2004(path("2004-parliament"))
        summed = (
            sum(r.registered or 0 for r in parliament),
            sum(r.valid or 0 for r in parliament),
            sum(r.null or 0 for r in parliament),
            sum(r.cast or 0 for r in parliament),
        )
        if printed != summed:
            raise ValueError(f"2004 constituency rows sum to {summed}, the printed national summary gives {printed}")
    mismatched: list[str] = []
    unbalanced: list[str] = []
    over: list[str] = []
    for scheme, results, office in ((constituency, parliament, False), (district, president, True)):
        if not results:
            continue
        tag = f"{day}:{'president' if office else 'parliament'}"
        units: dict[str, str] = {}
        for result in results:
            code = changeset.slug(result.name) if office and not result.code else result.code
            place = area(scheme, code)
            p.add("area", id=place, scheme_code=scheme)
            p.add("area_code", area_id=place, code=code)
            p.add("area_parent", area_id=place, parent_id=COUNTRY)
            p.name(place, result.name, "en", "official")
            unit = units[code] = key("unit", f"{day}:{scheme}:{code}")
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=MALAWI,
                scheme_code=scheme,
                code=f"{day}:{code}",
                area_relation="equals",
                area_id=place,
                kind="standard",
            )
        groups = [("", results)] if office else [(r.code, [r]) for r in results]
        for code, group in groups:
            first = group[0]
            contest_tag = tag if office else f"{tag}:{code}"
            frame = pl.DataFrame(
                [
                    {
                        "unit": units[changeset.slug(r.name) if office and not r.code else r.code],
                        "candidate": name,
                        "party": norm(party_code),
                        "votes": votes,
                    }
                    for r in group
                    for name, party_code, votes in r.candidates
                ]
            )
            _legacy_contest(
                p,
                election,
                contest_tag,
                frame,
                group,
                office=office,
                year=year,
                name=first.name,
                path_=path_,
                code=code,
                scheme=scheme,
                units=units,
                mismatched=mismatched,
            )
    for result in [*parliament, *president]:
        if (
            result.valid is not None
            and result.null is not None
            and result.cast is not None
            and result.valid + result.null != result.cast
        ):
            unbalanced.append(
                f"{result.name} (printed valid {result.valid:,} + null and void {result.null:,}, votes cast {result.cast:,})"
            )
        if result.registered is not None and result.cast is not None and result.cast > result.registered:
            over.append(f"{result.name} (votes cast {result.cast:,}, registered {result.registered:,})")
    if unbalanced:
        p.accept(
            "accounting.ballots_sum",
            "The printed valid and null and void votes do not add up to the votes cast for "
            + "; ".join(unbalanced)
            + ". Each is stored as printed.",
        )
    if over:
        p.accept(
            "accounting.turnout",
            "The printed votes cast exceed the printed registered voters for "
            + "; ".join(over)
            + ". Each is stored as printed.",
        )
    if mismatched:
        p.accept(
            "accounting.votes_valid",
            "The printed table's total valid votes differ from the sum of its candidates' votes for "
            + "; ".join(mismatched)
            + ". Each is stored as printed.",
        )
    return p


def _legacy_contest(
    p: Package,
    election: str,
    tag: str,
    frame: pl.DataFrame,
    group: list[legacy.Result],
    *,
    office: bool,
    year: int,
    name: str,
    path_: str,
    code: str,
    scheme: str,
    units: dict[str, str],
    mismatched: list[str],
) -> None:
    contest = key("contest", tag)
    title = f"{year} Malawi presidential election" if office else f"{year} National Assembly election, {name}"
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office" if office else "body_seats",
        office_id=PRESIDENCY if office else None,
        body_id=None if office else ASSEMBLY,
        district_area_id=COUNTRY if office else area(scheme, code),
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM_PRESIDENT_PLURALITY if office else SYSTEM_FPTP,
        default_result_kind="counted",
    )
    p.name(contest, title, "en", "official")
    slug = f"{path_}/president" if office else f"{path_}/parliament/{code}-{changeset.slug(name)}"
    p.add("slug", path=slug, entity_id=contest, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=MEC_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=(
            "Counted by the Malawi Electoral Commission, as its printed results table gives each "
            + ("district" if office else "constituency")
            + ": candidate votes, total valid votes, null and void votes, votes cast, and registered voters where "
            "printed. The table declares no winners or seat counts, so none are stored."
        ),
        lang="en",
    )
    options: dict[tuple[str, str], str] = {}
    for candidate, party_code in frame.select("candidate", "party").unique().sort("candidate", "party").iter_rows():
        option = options[(candidate, party_code)] = key("option", tag, candidate, party_code)
        person = key("person", tag, candidate, party_code)
        solo = party_code == "IND"
        p.add("person", id=person)
        p.name(person, candidate, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=None if solo else party(party_code),
            ballot_label=candidate,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, candidate, party_code),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate,
            member_of_party_id=None if solo else party(party_code),
            is_independent=solo,
        )
    values = frame.with_columns(
        pl.struct("candidate", "party")
        .map_elements(lambda row: options[(row["candidate"], row["party"])], return_dtype=pl.String)
        .alias("option")
    )
    p.frame(
        "result_value",
        values.select(
            pl.lit(result_set).alias("result_set_id"),
            pl.col("unit").alias("unit_id"),
            pl.col("option").alias("option_id"),
            pl.lit(0).alias("channel"),
            pl.lit(1).alias("measure"),
            pl.lit(0).alias("count_round"),
            pl.lit(0).alias("flags"),
            pl.col("votes").alias("value"),
        ),
    )
    for result in group:
        unit = units[changeset.slug(result.name) if office and not result.code else result.code]
        for measure, value in ((2, result.registered), (3, result.cast), (4, result.valid), (5, result.null)):
            if value is not None:
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
        total = sum(votes for _, _, votes in result.candidates)
        if result.valid is not None and result.valid != total:
            mismatched.append(
                f"{result.name} ({result.code or 'district'}: printed {result.valid:,}, candidates {total:,})"
            )
