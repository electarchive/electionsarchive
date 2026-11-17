"""Taiwan's township elections from the Central Election Commission's election database: township, town, and
county-city mayors (subject D2), heads of the special municipalities' indigenous districts (D1), township, town, and
county-city councils (R2), and indigenous district councils (R1). Each election is read at the finest unit the
Commission's JSON files publish: polling stations where they exist, else the township or the council's electoral area.
The packages share `tw.cec/geo` and `tw.cec/registry`, which call `extend_geo`, `geo`, and `registry` here."""

import functools
import json
import re
from datetime import date
from typing import NamedTuple

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.tw.cec import BASE, CEC, COUNTY_EN, INDEPENDENT, SOURCE, TAIWAN, area, key

LOCAL_LIST = "https://db.cec.gov.tw/static/elections/list/ELC_{}.json"
# subject -> (package stem, election slug, English noun, Chinese name)
LOCAL = {
    "D2": ("township-mayor", "township-mayors", "township mayoral", "鄉鎮市長選舉"),
    "D1": ("indigenous-district-head", "indigenous-district-heads", "indigenous district head", "直轄市原住民區長選舉"),
    "R2": ("township-council", "township-councils", "township council", "鄉鎮市民代表選舉"),
    "R1": (
        "indigenous-district-council",
        "indigenous-district-councils",
        "indigenous district council",
        "直轄市原住民區民代表選舉",
    ),
}
LOCAL_LEVELS = (("tickets", "D"), ("tickets", "A"), ("tickets", "L"), ("tickets", "T"))
LOCAL_PROFILE_LEVELS = ("D", "A", "L", "T")


class Theme(NamedTuple):
    subject: str
    type_id: str
    theme_id: str
    day: str
    session: int
    name: str


@functools.cache
def local_themes() -> list[Theme]:
    found: list[Theme] = []
    for subject in LOCAL:
        artifact = raw.fetch(SOURCE, LOCAL_LIST.format(subject), suffix=".json")
        for entry in json.loads(artifact.path.read_text(encoding="utf-8")):
            for item in entry["theme_items"]:
                found.append(
                    Theme(
                        subject,
                        item["legislator_type_id"],
                        item["theme_id"],
                        item["vote_date"],
                        int(item["session"]),
                        item["theme_name"],
                    )
                )
    return sorted(found, key=lambda theme: (theme.subject, theme.day, theme.type_id))


def local_days(subject: str) -> list[str]:
    return sorted({theme.day for theme in local_themes() if theme.subject == subject})


def local_url(kind: str, theme: Theme, level: str, code: str = "00_000_00_000_0000") -> str:
    return f"{BASE}{kind}/ELC/{theme.subject}/{theme.type_id}/{theme.theme_id}/{level}/{code}.json"


@functools.cache
def local_counties(theme: Theme) -> list[str]:
    """The county codes (`prv_city_00_000_0000`) a theme reports, from its county-level profiles."""
    url = local_url("profiles", theme, "C")
    data = json.loads(raw.fetch(SOURCE, url, suffix=".json").path.read_text(encoding="utf-8"))
    return sorted({f"{row['prv_code']}_{row['city_code']}_00_000_0000" for row in next(iter(data.values()))})


def local_urls(theme: Theme) -> list[str]:
    urls = [local_url("profiles", theme, "N"), local_url("profiles", theme, "C")]
    for code in local_counties(theme):
        urls.extend(local_url("tickets", theme, level, code) for _, level in LOCAL_LEVELS)
        urls.extend(local_url("profiles", theme, level, code) for level in LOCAL_PROFILE_LEVELS)
    return urls


def fetch() -> None:
    urls = [url for theme in local_themes() for url in local_urls(theme)]
    raw.fetch_many(SOURCE, urls, suffix=".json", workers=8)


TOWN = ("prv_code", "city_code", "dept_code")
VILLAGE = (*TOWN, "li_code")
STATION = (*VILLAGE, "tbox_no")
SEATS = (*TOWN, "area_code")
MARKS = {"*": "elected", "!": "elected", " ": "lost", "-": "lost", "?": "tied"}
LOCAL_METHOD = {
    "D2": (
        "Each voter casts one vote for one candidate for township, town, or county-city mayor. The candidate with "
        "the most votes in the township wins the office."
    ),
    "D1": (
        "Each voter casts one vote for one candidate for head of the indigenous district. The candidate with the "
        "most votes in the district wins the office."
    ),
    "R2": (
        "Each voter casts one vote for one candidate in the electoral area, and the candidates with the most votes "
        "fill the area's seats. Plains indigenous voters elect their own representatives in separate contests. Where "
        "the law reserves seats for women, a woman with fewer votes can take the seat of a man who placed higher; "
        "the Commission marks those women as elected and those men as not elected."
    ),
    "R1": (
        "Each voter casts one vote for one candidate in the electoral area, and the candidates with the most votes "
        "fill the area's seats. Where the law reserves seats for women, a woman with fewer votes can take the seat of "
        "a man who placed higher; the Commission marks those women as elected and those men as not elected."
    ),
}
LOCAL_NOTE = (
    "Valid votes for each candidate and, where the Commission publishes them, the valid and invalid ballots, ballots "
    "cast, and registered voters at each {unit}, as published by the Central Election Commission. Voters registered "
    "is the number of voters on the roll. Official totals are the Commission's own."
)
LOCAL_OFFICE = {"D2": key("office", "township-mayor"), "D1": key("office", "indigenous-district-head")}
LOCAL_BODY = {"R2": key("body", "township-council"), "R1": key("body", "indigenous-district-council")}
LOCAL_SYSTEM = {subject: key("electoral_system", LOCAL[subject][0]) for subject in LOCAL}
LOCAL_START = {"D2": date(1998, 1, 1), "D1": date(2014, 1, 1), "R2": date(2010, 1, 1), "R1": date(2014, 1, 1)}
LOCAL_NAMES = {
    "D2": ("Township, town, and county-city mayor", "鄉鎮市長"),
    "D1": ("Head of an indigenous district", "直轄市原住民區長"),
    "R2": ("Township, town, and county-city council", "鄉鎮市民代表會"),
    "R1": ("Indigenous district council", "直轄市原住民區民代表會"),
}


def fold(value: object) -> str:
    return re.sub(r"[\s　]+", "", str(value))


@functools.cache
def local_index() -> frozenset[str]:
    return frozenset(raw.keys(SOURCE))


@functools.cache
def local_rows(theme: Theme, kind: str, level: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for county in local_counties(theme):
        url = local_url(kind, theme, level, county)
        if url in local_index():
            data = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))
            for part in data.values():
                rows.extend(part)
    return rows


def local_artifacts(theme: Theme) -> list[raw.Artifact]:
    return [raw.cached(SOURCE, url) for url in local_urls(theme) if url in local_index()]


def at(row: dict[str, object], fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(row[field]) for field in fields)


class Places(NamedTuple):
    county: dict[tuple[str, ...], str]
    township: dict[tuple[str, ...], str]
    village: dict[tuple[str, ...], str]
    seats: dict[tuple[str, ...], str]


@functools.cache
def local_places(theme: Theme) -> Places:
    listing = json.loads(raw.cached(SOURCE, local_url("profiles", theme, "C")).path.read_text(encoding="utf-8"))
    county: dict[tuple[str, ...], str] = {
        (str(row["prv_code"]), str(row["city_code"])): fold(row["area_name"]) for row in next(iter(listing.values()))
    }
    township: dict[tuple[str, ...], str] = {}
    for kind, level in (("profiles", "D"), ("tickets", "D")):
        for row in local_rows(theme, kind, level):
            township.setdefault(at(row, TOWN), fold(row["area_name"]))
    village: dict[tuple[str, ...], str] = {}
    for kind in ("tickets", "profiles"):
        for row in local_rows(theme, kind, "L"):
            village.setdefault(at(row, VILLAGE), fold(row["area_name"]))
    seats: dict[tuple[str, ...], str] = {}
    for row in local_rows(theme, "tickets", "A"):
        seats.setdefault(at(row, SEATS), fold(row["area_name"]))
    return Places(county, township, village, seats)


def local_county(theme: Theme, code: tuple[str, ...]) -> str:
    return local_places(theme).county[(code[0], code[1])]


def seat_area(day: str, theme: Theme, code: tuple[str, ...]) -> str:
    places = local_places(theme)
    return area("tw.council_area", day, theme.type_id, local_county(theme, code), places.township[code[:3]], code[3])


def local_geo() -> tuple[
    set[str], set[tuple[str, str]], set[tuple[str, str, str]], dict[str, tuple[str, str, str, str]]
]:
    counties: set[str] = set()
    townships: set[tuple[str, str]] = set()
    villages: set[tuple[str, str, str]] = set()
    seats: dict[str, tuple[str, str, str, str]] = {}
    for theme in local_themes():
        places = local_places(theme)
        for code, name in places.township.items():
            counties.add(local_county(theme, code))
            townships.add((local_county(theme, code), name))
        for code, name in places.village.items():
            villages.add((local_county(theme, code), places.township[code[:3]], name))
        for code, name in places.seats.items():
            seats[seat_area(theme.day, theme, code)] = (
                f"{theme.day}|{theme.type_id}|{local_county(theme, code)}|{places.township[code[:3]]}|{code[3]}",
                local_county(theme, code),
                places.township[code[:3]],
                name,
            )
    return counties, townships, villages, seats


def parties() -> set[str]:
    return {
        str(row["party_name"])
        for theme in local_themes()
        for level in ("D", "A")
        for row in local_rows(theme, "tickets", level)
    }


def packages() -> list[str]:
    return [f"tw.cec/{LOCAL[subject][0]}-{day}" for subject in LOCAL for day in local_days(subject)]


class Seat(NamedTuple):
    """One contest of a township election: a township's mayoralty or one electoral area of a council."""

    theme: Theme
    code: tuple[str, ...]
    rows: list[dict[str, object]]
    totals: dict[str, object] | None


def local_contests(subject: str, theme: Theme) -> list[Seat]:
    council = subject in ("R1", "R2")
    fields = SEATS if council else TOWN
    rows: dict[tuple[str, ...], list[dict[str, object]]] = {}
    for row in local_rows(theme, "tickets", "A" if council else "D"):
        rows.setdefault(at(row, fields), []).append(row)
    totals = {at(row, fields): row for row in local_rows(theme, "profiles", "A" if council else "D")}
    if council and not totals:
        # the earliest council election publishes totals for the township only, so an area has none
        totals = {}
    return [
        Seat(theme, code, sorted(group, key=lambda row: int(str(row["cand_no"]))), totals.get(code))
        for code, group in sorted(rows.items())
    ]


def local_package(subject: str, day: str) -> Package:
    stem, slug, noun, native = LOCAL[subject]
    year = day[:4]
    council = subject in ("R1", "R2")
    themes = [theme for theme in local_themes() if theme.subject == subject and theme.day == day]
    when = date.fromisoformat(day)
    p = Package(
        f"tw.cec/{stem}-{day}",
        SOURCE,
        f"The {year} Taiwanese {noun} elections by polling station from the Central Election Commission",
        depends=["tw.cec/geo", "tw.cec/registry"],
    )
    p.external.add(TAIWAN)
    for theme in themes:
        for artifact in local_artifacts(theme):
            p.uses(artifact)
    election = key("election", f"{subject}-{day}")
    path = f"tw/{day}-{slug}"
    p.add("election", id=election, polity_id=TAIWAN, kind="local", date_from=when, date_to=when, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} Taiwanese {noun} elections", "en", "official")
    p.name(election, f"{themes[0].session}年{native}", "zh", "native", False)
    # reporting units
    unit_rows: dict[str, dict[str, object]] = {}
    values: list[tuple[str, str, str | None, int, int]] = []
    differences: list[str] = []
    unaccounted = False
    for theme in themes:
        places = local_places(theme)
        level = "T" if local_rows(theme, "tickets", "T") else None
        seats = local_contests(subject, theme)
        accounting = {at(row, STATION): row for row in local_rows(theme, "profiles", "T")}
        by_station: dict[tuple[str, ...], list[dict[str, object]]] = {}
        for row in local_rows(theme, "tickets", "T"):
            by_station.setdefault(at(row, STATION), []).append(row)
        station_seat: dict[tuple[str, ...], dict[tuple[str, ...], list[dict[str, object]]]] = {}
        for code, rows in by_station.items():
            seat = at(rows[0], SEATS if council else TOWN)
            if council and any(at(row, SEATS) != seat for row in rows):
                raise ValueError(f"{day}: station {code} votes in more than one area")
            station_seat.setdefault(seat, {})[code] = rows
        for seat in seats:
            tag = f"{theme.type_id}:{'_'.join(seat.code)}"
            county = local_county(theme, seat.code)
            township = places.township[seat.code[:3]]
            if council:
                district = seat_area(day, theme, seat.code)
                label = places.seats[seat.code]
                english = f"{year} {COUNTY_EN.get(county, county)} {label} council election"
                native_name = f"{county}{label}"
            else:
                district = area("tw.township", county, township)
                english = f"{year} {COUNTY_EN.get(county, county)} {township} mayoral election"
                native_name = f"{county}{township}{native[:-2]}選舉"
            contest = key("contest", f"{subject}-{day}", tag)
            result_set = key("result_set", f"{subject}-{day}", tag)
            winners = sum(1 for row in seat.rows if MARKS[str(row["is_victor"])] == "elected")
            magnitude = int(str(seat.totals["elected_num"])) if seat.totals else winners
            contest_row: dict[str, object] = {
                "id": contest,
                "election_id": election,
                "kind": "body_seats" if council else "office",
                "district_area_id": district,
                "magnitude": magnitude,
                "round_number": 1,
                "electoral_system_id": LOCAL_SYSTEM[subject],
                "default_result_kind": "counted",
            }
            contest_row["body_id" if council else "office_id"] = (
                LOCAL_BODY[subject] if council else LOCAL_OFFICE[subject]
            )
            p.add("contest", **contest_row)
            p.name(contest, english, "en", "official")
            p.name(contest, native_name, "zh", "native", False)
            p.add(
                "slug", path=f"{path}/{theme.type_id.lower()}-{'-'.join(seat.code)}", entity_id=contest, is_current=True
            )
            p.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=CEC,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=LOCAL_NOTE.format(
                    unit="polling station" if level else "township" if not council else "electoral area"
                ),
                lang="en",
            )
            option_of: dict[int, str] = {}
            for row in seat.rows:
                no = int(str(row["cand_no"]))
                party_name = str(row["party_name"])
                independent = party_name in INDEPENDENT
                party_id = None if independent else key("party", party_name)
                option = key("option", f"{subject}-{day}", tag, no)
                option_of[no] = option
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_id,
                    ballot_position=no,
                    ballot_label=str(row["cand_name"]),
                )
                person = key("person", f"{subject}-{day}", tag, no)
                birth = str(row.get("cand_birthyear") or "").strip()
                p.add(
                    "person",
                    id=person,
                    birth_date=date(int(birth), 1, 1) if birth.isdigit() else None,
                    birth_precision="year" if birth.isdigit() else None,
                    gender={"1": "male", "2": "female"}.get(str(row.get("cand_sex") or "")),
                )
                p.name(person, str(row["cand_name"]), "zh", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", f"{subject}-{day}", tag, no),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=str(row["cand_name"]),
                    member_of_party_id=party_id,
                    is_independent=independent,
                    is_incumbent=True if row.get("is_current") == "Y" else None,
                )
                p.add("outcome", result_set_id=result_set, option_id=option, kind=MARKS[str(row["is_victor"])])
                p.add(
                    "declared", result_set_id=result_set, option_id=option, measure=1, value=int(str(row["ticket_num"]))
                )
            if seat.totals:
                for measure, field in (
                    (4, "valid_ticket"),
                    (5, "invalid_ticket"),
                    (3, "vote_ticket"),
                    (2, "votable_population"),
                ):
                    p.add(
                        "declared",
                        result_set_id=result_set,
                        option_id=None,
                        measure=measure,
                        value=int(str(seat.totals[field])),
                    )
            # the unit-level figures
            sums: dict[int, int] = {}
            ballots = {2: 0, 3: 0, 4: 0, 5: 0}
            have_accounts = False
            if level:
                for code, rows in station_seat.get(seat.code, {}).items():
                    village = places.village[code[:4]]
                    unit_code = f"{subject}:{day}:{'_'.join(code[:4])}:{code[4]}"
                    unit = key("unit", unit_code)
                    unit_rows.setdefault(
                        unit,
                        {
                            "id": unit,
                            "polity_id": TAIWAN,
                            "scheme_code": "tw.station",
                            "code": unit_code,
                            "area_relation": "within",
                            "area_id": area("tw.village", county, township, village),
                            "kind": "standard",
                        },
                    )
                    for row in rows:
                        no = int(str(row["cand_no"]))
                        sums[no] = sums.get(no, 0) + int(str(row["ticket_num"]))
                        values.append((result_set, unit, option_of[no], 1, int(str(row["ticket_num"]))))
                    account = accounting.get(code)
                    if account:
                        have_accounts = True
                        for measure, field in (
                            (2, "votable_population"),
                            (3, "vote_ticket"),
                            (4, "valid_ticket"),
                            (5, "invalid_ticket"),
                        ):
                            value = int(str(account[field]))
                            ballots[measure] += value
                            values.append((result_set, unit, None, measure, value))
            else:
                unit_code = f"{subject}:{day}:{'_'.join(seat.code)}"
                unit = key("unit", unit_code)
                unit_rows.setdefault(
                    unit,
                    {
                        "id": unit,
                        "polity_id": TAIWAN,
                        "scheme_code": "tw.council_area" if council else "tw.township",
                        "code": unit_code,
                        "area_relation": "equals",
                        "area_id": district,
                        "kind": "aggregate",
                    },
                )
                for row in seat.rows:
                    no = int(str(row["cand_no"]))
                    sums[no] = int(str(row["ticket_num"]))
                    values.append((result_set, unit, option_of[no], 1, sums[no]))
                if seat.totals:
                    have_accounts = True
                    for measure, field in (
                        (2, "votable_population"),
                        (3, "vote_ticket"),
                        (4, "valid_ticket"),
                        (5, "invalid_ticket"),
                    ):
                        value = int(str(seat.totals[field]))
                        ballots[measure] = value
                        values.append((result_set, unit, None, measure, value))
            if level and seat.totals and not have_accounts:
                unaccounted = True
            for row in seat.rows:
                no = int(str(row["cand_no"]))
                if sums.get(no, 0) != int(str(row["ticket_num"])):
                    differences.append(
                        f"{english}: the unit figures for {row['cand_name']} add up to {sums.get(no, 0)}, and the "
                        f"Commission's total is {row['ticket_num']}"
                    )
            if seat.totals and have_accounts and level:
                for measure, field, word in (
                    (4, "valid_ticket", "valid"),
                    (5, "invalid_ticket", "invalid"),
                    (3, "vote_ticket", "cast"),
                    (2, "votable_population", "registered"),
                ):
                    if ballots[measure] != int(str(seat.totals[field])):
                        differences.append(
                            f"{english}: the unit figures add up to {ballots[measure]} {word} ballots, and the "
                            f"Commission's total is {seat.totals[field]}"
                        )
    if unaccounted:
        p.accept(
            "accounting.declared",
            "The Commission publishes valid and invalid ballots, ballots cast, and registered voters for each "
            "township only; its polling-station files for this election hold candidate votes alone",
        )
    if differences:
        p.accept(
            "accounting.declared",
            "The Commission's published totals differ from its own unit-level figures. " + "; ".join(differences[:20]),
        )
        p.add(
            "note",
            id=key("note", "differences", f"{subject}-{day}"),
            entity_id=election,
            kind="caveat",
            body_md="The Commission's published totals differ from its own unit-level figures in this election: "
            + "; ".join(differences)
            + ". The unit-level figures are stored as published.",
            lang="en",
        )
    units = pl.DataFrame(list(unit_rows.values()))
    p.frame("reporting_unit", units)
    facts = pl.DataFrame(
        values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
    ).filter(pl.col("value") >= 0)
    p.frame(
        "result_value",
        facts.select(
            result_set_id="result_set_id",
            unit_id="unit_id",
            option_id="option_id",
            channel=pl.lit(0),
            measure="measure",
            count_round=pl.lit(0),
            flags=pl.lit(0),
            value="value",
        ),
    )
    return p


def extend_geo(counties: set[str], townships: set[tuple[str, str]], villages: set[tuple[str, str, str]]) -> None:
    """Add the counties, townships, and villages the township elections report to the geo package's sets."""
    more_counties, more_townships, more_villages, _ = local_geo()
    counties |= more_counties
    townships |= more_townships
    villages |= more_villages


def geo(p: Package) -> None:
    """Add the township councils' electoral areas to the geo package."""
    p.add(
        "scheme",
        code="tw.council_area",
        polity_id=TAIWAN,
        kind="electoral_district",
        rank=3,
        noun="township council electoral area",
        code_system=None,
    )
    for id, (code, county, township, name) in sorted(local_geo()[3].items(), key=lambda item: item[1][0]):
        p.add("area", id=id, scheme_code="tw.council_area")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area("tw.township", county, township))
        p.name(id, name, "zh", "official")


def registry(p: Package) -> None:
    """Add the township offices and councils, their electoral systems, and the cited files to the registry package."""
    for theme in local_themes():
        for artifact in local_artifacts(theme):
            p.artifact(artifact, CEC)
    for subject in LOCAL:
        english, native = LOCAL_NAMES[subject]
        if subject in LOCAL_OFFICE:
            p.add("office", id=LOCAL_OFFICE[subject], polity_id=TAIWAN, kind="mayor")
            p.name(LOCAL_OFFICE[subject], english, "en", "official")
            p.name(LOCAL_OFFICE[subject], native, "zh", "native", False)
        else:
            p.add("body", id=LOCAL_BODY[subject], polity_id=TAIWAN, kind="council")
            p.name(LOCAL_BODY[subject], english, "en", "official")
            p.name(LOCAL_BODY[subject], native, "zh", "native", False)
        p.add(
            "electoral_system",
            id=LOCAL_SYSTEM[subject],
            polity_id=TAIWAN,
            valid_from=LOCAL_START[subject],
            valid_to=None,
            rules={"formula": "fptp" if subject in LOCAL_OFFICE else "sntv"},
            rules_version=1,
        )
        p.add(
            "note",
            id=key("note", LOCAL_SYSTEM[subject]),
            entity_id=LOCAL_SYSTEM[subject],
            kind="methodology",
            body_md=LOCAL_METHOD[subject],
            lang="en",
        )


def parse() -> None:
    for subject in LOCAL:
        for day in local_days(subject):
            local_package(subject, day).write()
