"""Alberta school board trustee elections in Calgary and Edmonton, by voting station.

The Local Authorities Election Act has public and separate school board trustees elected on the municipal voting day.
No province-wide collector publishes them, but the cities' own open data counts the four boards that lie mostly inside
them: the Calgary Board of Education (CBE), the Calgary Catholic School District (CCSD), Edmonton Public Schools
(the Edmonton School Division, EPSB), and the Edmonton Catholic Separate School Division (ECSD). The same station files
that hold the mayor's and councillors' votes hold each trustee race: Edmonton's for 2010 to 2025 and Calgary's for 2013
to 2025.

A trustee ward elects one trustee by plurality. Edmonton's wards carry their own names (letters for EPSB, 71 to 77 for
ECSD) and Calgary's are pairs of city wards. Calgary's 2013 and 2017 files print only the station's city ward, so a
race's ward pair is read from the city's candidate totals file, which labels each candidate with it and also lists
the acclaimed trustees. The files print no winners, so the candidate with the most votes is recorded as elected where the
file holds the whole race. Calgary Catholic wards also take in Airdrie, Cochrane, and Chestermere, whose votes the
city's file does not hold, so those races are stored as counted in Calgary with no outcome, and for 2013 and 2017, when
the files do not say which wards reach beyond the city, every Calgary Catholic race is treated that way.

Calgary's trustee wards are the unions of the city wards the races name, dated to the ward map of each election. A
Calgary Catholic ward that reaches beyond the city, and every Edmonton trustee ward, which the city does not publish,
has no outline.
"""

import functools
import re
from collections import defaultdict
from datetime import timedelta
from typing import NamedTuple
from uuid import uuid5

import shapely
import shapely.ops

from ea import package, raw
from ea.package import Package
from ea.sources.ca import ab_municipal as city

SOURCE = "ca.ab_school"
CANADA = city.CANADA
AB = city.AB
AB_AREA = city.AB_AREA
DAYS = city.DAYS
FILES = city.FILES
TOTALS = city.TOTALS
SOURCES_PAGES = city.PAGES
SKIPPED = city.SKIPPED
CITIES = city.CITIES
BOARDS = {
    "cbe": ("calgary", "Calgary Board of Education", "CBE"),
    "ccsd": ("calgary", "Calgary Catholic School District", "CCSD"),
    "epsb": ("edmonton", "Edmonton Public Schools", "EPSB"),
    "ecsd": ("edmonton", "Edmonton Catholic Separate School Division", "ECSD"),
}
OF_CITY = {"calgary": ("cbe", "ccsd"), "edmonton": ("epsb", "ecsd")}
TOWNS = re.compile(r"cochrane|airdrie|chestermere|plus", re.IGNORECASE)
SCHOOL = re.compile(r"school|cbe|ccsd", re.IGNORECASE)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCES = {name: key("source", name) for name in CITIES}
PLURALITY = key("electoral_system", "plurality")
fold = city.fold
slug = city.slug
tokens = city.tokens
person = city.person


def fetch() -> None:
    city.fetch()


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------------------------------------------- rows


class Row(NamedTuple):
    board: str
    ward: str  # the trustee ward's key within its board and year
    partial: bool  # the race's electors live partly outside the city, so the city's file holds part of it
    station: str
    place: str  # the city ward the station lies in; empty when it serves the whole city
    label: str
    channel: int
    candidate: str
    party: str
    votes: int
    acclaimed: bool


def _edmonton_board(text: str) -> str | None:
    low = text.lower()
    if "school" not in low:
        return None
    return "ecsd" if "catholic" in low else "epsb"


def edmonton(year: int) -> list[Row]:
    rows: list[Row] = []
    for line in city._csv("edmonton", year, FILES):
        if year == 2010:
            board = _edmonton_board(line["RaceName"])
            ward, label, kind = line["WardName"], line["StationName"], line["StationTypeSortOrder"]
            name = line["CandidateName"]
            votes, acclaimed, party = city._int(line["VotesReceived"]), line["Acclaimed"].lower() == "true", ""
            kind = {"1": "Regular", "2": "Advance", "5": "Institutional mobile", "9": "Special"}[kind]
        else:
            board = _edmonton_board(line["Contest Name"])
            ward, label, kind = line["Ward Name"], line["Voting Station Description"], line["Voting Station Type"]
            if "Candidate Last Name" in line:
                name = f"{line['Candidate Last Name']}, {line['Candidate First Name']}"
            else:
                name = line["Candidate Name"]
            votes = city._int(line["Votes Received"])
            acclaimed = line["Acclaimed"].lower() in {"yes", "true"}
            party = line.get("Party Name", "").strip()
        if board is None or person(name).lower() in SKIPPED:
            continue
        rows.append(
            Row(
                board,
                city.ward_key(ward),
                False,
                label,
                city._edmonton_place(label),
                label,
                city.channel_of(kind, label),
                person(name),
                party,
                votes,
                acclaimed,
            )
        )
    return rows


def pair(text: str) -> tuple[str, ...]:
    """The city wards a Calgary trustee race names, such as `4(6, 7)` or `WARDS 1 AND 2`."""
    text = re.sub(r"^\d+\(", "(", text.strip())
    text = re.sub(r"(?i)^.*?wards?\s+", "", text)
    return tuple(str(int(n)) for n in re.findall(r"\d+", text))


def _calgary_board(text: str) -> str | None:
    low = text.lower()
    if not SCHOOL.search(low):
        return None
    return "ccsd" if ("separate" in low or "ccsd" in low) else "cbe"


@functools.cache
def calgary_totals(year: int) -> dict[tuple[str, tuple[str, ...]], tuple[tuple[str, ...], int, bool]]:
    """The candidate totals Calgary printed for 2013 and 2017: (board, name tokens) to (ward pair, votes, acclaimed)."""
    found: dict[tuple[str, tuple[str, ...]], tuple[tuple[str, ...], int, bool]] = {}
    if (("calgary"), year) not in TOTALS:
        return found
    for line in city._csv("calgary", year, TOTALS):
        board = _calgary_board(line["Office"])
        if board is None:
            continue
        found[(board, tokens(person(line["Candidate Name"])))] = (
            pair(line["Ward"]),
            city._int(line["Total Votes"]),
            line["Acclaimed"].strip().lower() == "acclaimed",
        )
    return found


def calgary(year: int) -> list[Row]:
    rows: list[Row] = []
    totals = calgary_totals(year)
    problems: list[str] = []
    for line in city._csv("calgary", year, FILES):
        if year in (2013, 2017):
            board = _calgary_board(line["Office"])
            ward, station = line["Ward"].strip(), line["Voting Station ID"].strip()
            label = line.get("Voting Station Name", "") or f"Station {station}"
            kind = line.get("Voting Station Type", "Regular")
            name, party, votes = line["Ballot Name"], "", city._int(line["Votes"])
            office = ""
        else:
            office = line["OfficeType"].strip()
            board = _calgary_board(office)
            ward, station = line["Ward"].strip(), line["VotingStationCode"].strip()
            label, kind = line["VotingStation"], line["VotingStationType"]
            name, votes = line["CandidateName"], city._int(line["Votes"])
            party = line.get("Party", "").strip()
            party = "" if party.lower() == "none" else party
        if board is None or person(name).lower() in SKIPPED:
            continue
        if year in (2013, 2017):
            hit = totals.get((board, tokens(person(name))))
            if hit is None:
                problems.append(f"calgary {year} {board} {name} is not in the candidate totals")
                continue
            wards, partial = hit[0], board == "ccsd"
        else:
            wards, partial = pair(office), bool(TOWNS.search(office))
        rows.append(
            Row(
                board,
                "-".join(wards),
                partial,
                station,
                "" if ward in {"", "0"} else ward,
                label,
                0 if year == 2013 else city.channel_of(kind, label if year == 2017 else ""),
                person(name),
                party,
                votes,
                False,
            )
        )
    if problems:
        raise ValueError("\n".join(problems))
    return rows


@functools.cache
def rows(name: str, year: int) -> list[Row]:
    return edmonton(year) if name == "edmonton" else calgary(year)


def vintage(name: str, year: int) -> str:
    return city.VINTAGES[name][year] if name == "calgary" else str(year)


def declared_edmonton(year: int) -> dict[tuple[str, str, tuple[str, ...]], int]:
    found: dict[tuple[str, str, tuple[str, ...]], int] = defaultdict(int)
    if ("edmonton", year) not in TOTALS:
        return found
    for line in city._csv("edmonton", year, TOTALS):
        low = {k.lower().replace(" ", "_"): v for k, v in line.items()}
        board = _edmonton_board(low["contest"])
        if board is None:
            continue
        name = low.get("candidate_name") or low["candidatename"]
        votes = low.get("votes_received") or low.get("votes") or "0"
        ward = city.ward_key(low.get("ward_name") or low["wardname"])
        found[(board, ward, tokens(person(name)))] += city._int(votes)
    return found


# ---------------------------------------------------------------------------------------------------- geography


class Race(NamedTuple):
    board: str
    ward: str
    partial: bool


def races(name: str, year: int) -> dict[Race, list[Row]]:
    found: dict[Race, list[Row]] = defaultdict(list)
    for row in rows(name, year):
        found[Race(row.board, row.ward, row.partial)].append(row)
    return found


def acclaimed_only(year: int) -> dict[Race, list[str]]:
    """Calgary trustees acclaimed in 2013 and 2017, who appear in the candidate totals and in no station file."""
    seen = {(row.board, tokens(row.candidate)) for row in rows("calgary", year)}
    found: dict[Race, list[str]] = defaultdict(list)
    for (board, who), (wards, _, acclaimed) in calgary_totals(year).items():
        if acclaimed and (board, who) not in seen:
            found[Race(board, "-".join(wards), board == "ccsd")].append(" ".join(who))
    return found


def acclaimed_names(year: int) -> dict[tuple[str, tuple[str, ...]], str]:
    out: dict[tuple[str, tuple[str, ...]], str] = {}
    for line in city._csv("calgary", year, TOTALS) if ("calgary", year) in TOTALS else []:
        board = _calgary_board(line["Office"])
        if board and line["Acclaimed"].strip().lower() == "acclaimed":
            out[(board, tokens(person(line["Candidate Name"])))] = person(line["Candidate Name"])
    return out


def ward_name(board: str, ward: str) -> str:
    short = BOARDS[board][2]
    if BOARDS[board][0] == "calgary":
        numbers = ward.split("-")
        return f"{short} Wards " + " and ".join(numbers)
    return f"{short} Ward {ward.upper()}"


def division_area(board: str) -> str:
    return key("area", "division", board)


def ward_area(board: str, vint: str, ward: str) -> str:
    return key("area", "ward", board, vint, ward)


def polity(board: str) -> str:
    return key("polity", board)


def years(name: str) -> list[int]:
    return sorted(y for (c, y) in FILES if c == name)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The Calgary and Edmonton school boards and the trustee wards they elect for, with outlines for the wards "
        "that are unions of Calgary's city wards",
        depends=[],
    )
    p.external |= {CANADA, AB, AB_AREA}
    for name, label in CITIES.items():
        p.add(
            "source",
            id=SOURCES[name],
            kind="official_body",
            publisher=f"City of {label}",
            title=f"{label} open data: municipal election results and ward boundaries",
            url=SOURCES_PAGES[name],
            license=f"Open Government Licence - City of {label}",
            license_url="https://data.edmonton.ca/stories/s/OGL/"
            if name == "edmonton"
            else "https://data.calgary.ca/stories/s/Open-Calgary-Terms-of-Use/u45n-7awa",
            attribution=f"Contains information licensed under the Open Government Licence - City of {label}",
        )
    for (name, _), url in city.WARDS_FILES.items():
        if name == "calgary":
            p.artifact(raw.cached(city.SOURCE, url), SOURCES[name])
    p.add(
        "scheme",
        code="ca-ab.school_division",
        polity_id=AB,
        kind="administrative",
        rank=3,
        noun="school division",
        code_system=None,
    )
    p.add(
        "scheme",
        code="ca-ab.school_ward",
        polity_id=AB,
        kind="electoral_district",
        rank=4,
        noun="trustee ward",
        code_system=None,
    )
    p.add(
        "scheme",
        code="ca-ab.school_station",
        polity_id=AB,
        kind="precinct",
        rank=5,
        noun="voting station",
        code_system=None,
    )
    maps = city.wards("calgary")
    for board, (name, full, short) in BOARDS.items():
        area, town = division_area(board), polity(board)
        p.add("area", id=area, scheme_code="ca-ab.school_division")
        p.add("area_parent", area_id=area, parent_id=AB_AREA)
        p.name(area, full, "en", "official")
        p.add("polity", id=town, kind="school_district", iso_code=None)
        p.name(town, full, "en", "official")
        p.name(town, short, "en", "short")
        p.add("polity_parent", polity_id=town, parent_id=AB)
        p.add("polity_territory", polity_id=town, area_id=area)
        p.add("slug", path=f"ca/ab/{board}", entity_id=town, is_current=True)
        done: set[tuple[str, str]] = set()
        outlines: dict[str, list[shapely.Geometry]] = defaultdict(list)
        for year in years(name):
            vint = vintage(name, year)
            for race in sorted(races(name, year)):
                if race.board != board:
                    continue
                if (vint, race.ward) in done:
                    continue
                done.add((vint, race.ward))
                _add_ward(p, board, name, vint, race, maps, outlines)
            if name == "calgary":
                for race in acclaimed_only(year):
                    if race.board == board and (vint, race.ward) not in done:
                        done.add((vint, race.ward))
                        _add_ward(p, board, name, vint, race, maps, outlines)
        if board == "cbe":
            for vint, shapes in sorted(outlines.items()):
                start, end = city.SPANS["calgary"][vint]
                outline = shapely.ops.unary_union(shapes)
                parts = [part for part in shapely.get_parts(outline) if shapely.get_type_id(part) in (3, 6)]
                outline = shapely.set_srid(
                    shapely.MultiPolygon([q for part in parts for q in shapely.get_parts(part)]), 4326
                )
                p.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=start,
                    valid_to=end,
                    geom=city._wkb(outline),
                    label_point=city._wkb(shapely.point_on_surface(outline)),
                    quality="official",
                    source_id=SOURCES["calgary"],
                )
    return p


def _add_ward(
    p: Package,
    board: str,
    name: str,
    vint: str,
    race: Race,
    maps: dict[str, dict[str, city.Ward]],
    outlines: dict[str, list[shapely.Geometry]],
) -> None:
    area = ward_area(board, vint, race.ward)
    p.add("area", id=area, scheme_code="ca-ab.school_ward")
    p.add("area_parent", area_id=area, parent_id=division_area(board))
    p.name(area, ward_name(board, race.ward), "en", "official")
    if name != "calgary" or race.partial:
        return
    members = [maps[vint][w].shape for w in race.ward.split("-") if w in maps[vint]]
    if len(members) != len(race.ward.split("-")) or any(m is None for m in members):
        return
    union = shapely.ops.unary_union(members)
    parts = [part for part in shapely.get_parts(union) if shapely.get_type_id(part) in (3, 6)]
    shape = shapely.set_srid(shapely.MultiPolygon([q for part in parts for q in shapely.get_parts(part)]), 4326)
    start, end = city.SPANS["calgary"][vint]
    p.add(
        "area_geometry",
        area_id=area,
        valid_from=start,
        valid_to=end,
        geom=city._wkb(shape),
        label_point=city._wkb(shapely.point_on_surface(shape)),
        quality="official",
        source_id=SOURCES["calgary"],
    )
    outlines[vint].append(shape)


# ---------------------------------------------------------------------------------------------------- registry


def body(board: str) -> str:
    return key("body", board)


def party(board: str, name: str) -> str:
    return key("party", board, fold(name))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The boards of trustees, elector organizations, and electoral system of the Calgary and Edmonton school "
        "board elections, with the files their results come from",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {CANADA, AB}
    for url in [*FILES.values(), *TOTALS.values()]:
        name = "edmonton" if "edmonton" in url else "calgary"
        p.artifact(raw.cached(city.SOURCE, url), SOURCES[name])
    p.add(
        "electoral_system",
        id=PLURALITY,
        polity_id=AB,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.name(PLURALITY, "Alberta school board trustee plurality voting", "en", "official")
    for board, (_, full, _short) in BOARDS.items():
        p.add("body", id=body(board), polity_id=polity(board), kind="council")
        p.name(body(board), f"{full} Board of Trustees", "en", "official")
    seen: dict[str, tuple[str, str]] = {}
    for name in CITIES:
        for year in years(name):
            for row in rows(name, year):
                if row.party:
                    seen[party(row.board, row.party)] = (row.board, row.party)
    for id, (board, label) in sorted(seen.items()):
        p.add("party", id=id, kind="party", polity_id=polity(board))
        p.name(id, label, "en", "official")
    return p


# ---------------------------------------------------------------------------------------------------- elections


def _value(p: Package, result_set: str, unit: str, option: str | None, channel: int, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=channel,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


GAPS: dict[int, list[str]] = {}


def election(year: int) -> Package:
    day = DAYS[year]
    close = day + timedelta(days=1)
    names = [n for n in CITIES if (n, year) in FILES]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} school board trustee elections of "
        + " and ".join(CITIES[n] for n in names)
        + ": each trustee ward's race, with the votes of each candidate at each voting station",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CANADA, AB}
    umbrella = key("election", year)
    p.add("election", id=umbrella, polity_id=AB, kind="local", date_from=day, date_to=day, status="held")
    p.name(umbrella, f"{year} Alberta school board trustee elections", "en", "official")
    p.add("slug", path=f"ca/ab/{day}-school", entity_id=umbrella, is_current=True)
    note = (
        "The cities' files give each candidate's votes at each voting station and name no winners. Where a file "
        "holds the whole race, the candidate with the most votes is recorded as elected, which is how the Local "
        "Authorities Election Act decides a race with one seat. Calgary Catholic School District wards that reach "
        "into Airdrie, Cochrane, or Chestermere are stored with the votes counted in Calgary and no outcome."
    )
    p.add("note", id=key("note", year), entity_id=umbrella, kind="methodology", body_md=note, lang="en")
    taken: set[str] = set()
    problems: list[str] = []
    gaps: list[str] = []
    for name in names:
        label = CITIES[name]
        p.uses(raw.cached(city.SOURCE, FILES[(name, year)]))
        if (name, year) in TOTALS:
            p.uses(raw.cached(city.SOURCE, TOTALS[(name, year)]))
        vint = vintage(name, year)
        found = rows(name, year)
        cbe_wards = {w: "-".join(r.ward.split("-")) for r in found if r.board == "cbe" for w in r.ward.split("-")}
        want_edmonton = declared_edmonton(year) if name == "edmonton" else {}
        want_calgary = calgary_totals(year) if name == "calgary" else {}
        # Reporting units, one for each station.
        places: dict[str, str] = {}
        for row in found:
            places.setdefault(row.station, row.place)
            if row.place and places[row.station] and places[row.station] != row.place:
                problems.append(
                    f"{name} {year} station {row.station} lies in wards {places[row.station]} and {row.place}"
                )
        labels = {row.station: row.label for row in found}
        units: dict[str, str] = {}
        for station, place in sorted(places.items()):
            unit = units[station] = key("unit", day, name, station)
            if name == "calgary" and place in cbe_wards:
                area = ward_area("cbe", vint, cbe_wards[place])
            else:
                area = division_area(OF_CITY[name][0])
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CANADA,
                scheme_code="ca-ab.school_station",
                code=f"{day}:{name}:{station}",
                area_relation="within",
                area_id=area,
                kind="standard",
                valid_from=day,
                valid_to=close,
            )
            p.name(unit, labels[station], "en", "official")
        elections: dict[str, str] = {}
        for board in OF_CITY[name]:
            vote = elections[board] = key("election", year, board)
            full = BOARDS[board][1]
            p.add(
                "election",
                id=vote,
                polity_id=polity(board),
                parent_id=umbrella,
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
            )
            p.name(vote, f"{year} {full} election", "en", "official")
            p.add("slug", path=f"ca/ab/{board}/{day}-local", entity_id=vote, is_current=True)
        grouped = races(name, year)
        extra = acclaimed_only(year) if name == "calgary" else {}
        for race in extra:
            grouped.setdefault(race, [])
        for race, members in sorted(grouped.items()):
            board = race.board
            vote = elections[board]
            tag = f"{board}:{race.ward}"
            contest = key("contest", year, tag)
            title = f"Trustee, {ward_name(board, race.ward)}"
            p.add(
                "contest",
                id=contest,
                election_id=vote,
                kind="body_seats",
                office_id=None,
                body_id=body(board),
                district_area_id=ward_area(board, vint, race.ward),
                magnitude=1,
                round_number=1,
                electoral_system_id=PLURALITY,
                default_result_kind="counted",
            )
            p.name(contest, f"{year} {title}", "en", "official")
            path = f"ca/ab/{board}/{day}-local/trustee-ward-{slug(race.ward)}"
            while path in taken:
                path += "-2"
            taken.add(path)
            p.add("slug", path=path, entity_id=contest, is_current=True)
            candidates: dict[str, list[Row]] = defaultdict(list)
            for row in members:
                candidates[row.candidate].append(row)
            for who in extra.get(race, []):
                label_name = acclaimed_names(year).get((board, tuple(who.split())), who.title())
                candidates.setdefault(label_name, [])
            sums = {n: sum(r.votes for r in rs) for n, rs in candidates.items()}
            acclaimed_flag = any(r.acclaimed for r in members)
            tacit = (
                not members
                or (name == "edmonton" and all(r.acclaimed for r in members))
                or (name == "calgary" and len(candidates) == 1 and not any(sums.values()))
            )
            if acclaimed_flag and not tacit:
                problems.append(f"{name} {year} {tag} mixes acclaimed and voted rows")
            top = max(sums.values()) if sums else 0
            winners = [n for n, total in sums.items() if total == top]
            decided = not race.partial
            if not tacit and decided and len(winners) != 1:
                problems.append(f"{name} {year} {tag} has a tie for first place")
            if tacit and len(candidates) != 1:
                problems.append(f"{name} {year} {tag} acclaimed with {len(candidates)} candidates")
            result_set = key("result_set", year, tag)
            p.add(
                "result_set",
                id=result_set,
                election_id=vote,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                source_id=SOURCES[name],
                status="active",
                is_default=True,
                completeness=None if tacit or race.partial else 1.0,
            )
            for name_c in sorted(candidates, key=lambda n: (-sums[n], n)):
                option = key("option", year, tag, name_c)
                owner = next((r.party for r in candidates[name_c] if r.party), "")
                club = party(board, owner) if owner else None
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=club,
                    ballot_position=None,
                    ballot_label=name_c,
                )
                who_id = key("person", year, tag, name_c)
                p.add(
                    "candidacy",
                    id=key("candidacy", year, tag, name_c),
                    person_id=who_id,
                    option_id=option,
                    role="head",
                    ballot_name=name_c,
                    member_of_party_id=club,
                )
                p.add("person", id=who_id, birth_precision=None)
                p.name(who_id, name_c, "en", "official")
                if decided and name_c in winners and (tacit or len(winners) == 1):
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                if tacit:
                    continue
                by_unit: dict[tuple[str, int], int] = defaultdict(int)
                for row in candidates[name_c]:
                    by_unit[(units[row.station], row.channel)] += row.votes
                for (unit, channel), votes in sorted(by_unit.items()):
                    _value(p, result_set, unit, option, channel, 1, votes)
                if name == "edmonton":
                    want = want_edmonton.get((board, race.ward, tokens(name_c))) if want_edmonton else None
                    missing = bool(want_edmonton)
                else:
                    hit = want_calgary.get((board, tokens(name_c)))
                    want = hit[1] if hit else None
                    missing = bool(want_calgary)
                if want is not None:
                    p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=want)
                    if want != sums[name_c]:
                        gaps.append(f"{name} {year} {tag} {name_c}: stations {sums[name_c]}, declared {want}")
                elif missing:
                    problems.append(f"{name} {year} {tag} {name_c}: no declared total")
            _ = label
    if problems:
        raise ValueError("\n".join(problems))
    if gaps:
        GAPS[year] = gaps
        p.accept(
            "accounting.declared",
            f"for {len(gaps)} candidates the city's candidate totals and its voting station file, two files it "
            "publishes, differ by a few votes",
        )
    return p
