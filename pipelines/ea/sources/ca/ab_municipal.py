"""Alberta municipal elections: the mayor and the councillors of Calgary and Edmonton by voting station.

Both cities publish each general election as open data with one row per candidate and voting station: Edmonton's
for 2010 to 2025 and Calgary's for 2013 to 2025. A voting station is a reporting unit; the channel of its votes
follows the kind of station (election day, advance, mail-in, or an institutional or special station). Each ward's
councillor race and the mayor's race is a contest decided by plurality, as the Local Authorities Election Act
provides. The files print no winners, so the candidate with the most votes is recorded as elected, and a contest
whose only candidate was acclaimed has a tacit result set. School board trustee races, which the cities also
count, are left out.

Ward outlines come from each city's open data: Edmonton's historical and current wards, and Calgary's wards as the
civic census published them for 2012 and 2018, with the current wards. A city's outline is the union of its wards.
"""

import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping
from csv import DictReader
from datetime import date, timedelta
from typing import NamedTuple
from uuid import uuid5

import shapely
import shapely.geometry
import shapely.ops

from ea import package, raw
from ea.package import Package

SOURCE = "ca.ab_municipal"
CANADA = "01a13fb6-ea9b-72f8-8170-25b12e45cb98"
AB = "01a13fb6-eab7-7501-a0f9-4907b2d3060e"
AB_AREA = "01a13fb6-eab7-7501-a0f9-48fa1f7bbf15"
EDMONTON = "https://data.edmonton.ca/api/views/{}/rows.csv?accessType=DOWNLOAD"
CALGARY = "https://data.calgary.ca/api/views/{}/rows.csv?accessType=DOWNLOAD"
EDMONTON_GEO = "https://data.edmonton.ca/api/geospatial/{}?method=export&format=GeoJSON"
CALGARY_GEO = "https://data.calgary.ca/api/geospatial/{}?method=export&format=GeoJSON"
DAYS = {
    2010: date(2010, 10, 18),
    2013: date(2013, 10, 21),
    2017: date(2017, 10, 16),
    2021: date(2021, 10, 18),
    2025: date(2025, 10, 20),
}
FILES = {
    ("edmonton", 2010): EDMONTON.format("yznh-9n2u"),
    ("edmonton", 2013): EDMONTON.format("h9v5-2eis"),
    ("edmonton", 2017): EDMONTON.format("hcz8-v3q4"),
    ("edmonton", 2021): EDMONTON.format("xifx-5erj"),
    ("edmonton", 2025): EDMONTON.format("32te-6grv"),
    ("calgary", 2013): CALGARY.format("fhmw-xp86"),
    ("calgary", 2017): CALGARY.format("atsy-3a4w"),
    ("calgary", 2021): CALGARY.format("kiuh-sx23"),
    ("calgary", 2025): CALGARY.format("gqvk-p23x"),
}
# The authorities' own candidate totals, which the station files are checked against.
TOTALS = {
    ("edmonton", 2013): EDMONTON.format("b6ng-fzk2"),
    ("edmonton", 2017): EDMONTON.format("gg6p-rhvt"),
    ("edmonton", 2021): EDMONTON.format("sni2-g2xh"),
    ("edmonton", 2025): EDMONTON.format("fv5x-8efu"),
    ("calgary", 2013): CALGARY.format("ibk8-8j45"),
    ("calgary", 2017): CALGARY.format("wfy7-ea5g"),
}
WARDS_FILES = {
    ("edmonton", "wards"): EDMONTON_GEO.format("nydb-6rce"),
    ("calgary", "2012"): CALGARY_GEO.format("2pm8-2956"),
    ("calgary", "2018"): CALGARY_GEO.format("pryy-7s2m"),
    ("calgary", "current"): CALGARY_GEO.format("tz8z-hyaz"),
}
PAGES = {
    "edmonton": "https://data.edmonton.ca/",
    "calgary": "https://data.calgary.ca/",
}
# Which ward map each election used, and the dates a map is in force. Edmonton's open data holds no map for the
# twelve wards of 2010, so those wards carry no outline.
VINTAGES: dict[str, dict[int, str]] = {
    "edmonton": {2010: "2010", 2013: "2013", 2017: "2017", 2021: "current", 2025: "current"},
    "calgary": {2013: "2012", 2017: "2018", 2021: "current", 2025: "current"},
}
SPANS: dict[str, dict[str, tuple[date | None, date | None]]] = {
    "edmonton": {
        "2010": (None, date(2013, 10, 21)),
        "2013": (date(2013, 10, 21), date(2017, 10, 16)),
        "2017": (date(2017, 10, 16), date(2021, 10, 18)),
        "current": (date(2021, 10, 18), None),
    },
    "calgary": {
        "2012": (None, date(2017, 10, 16)),
        "2018": (date(2017, 10, 16), date(2021, 10, 18)),
        "current": (date(2021, 10, 18), None),
    },
}
CITIES = {
    "edmonton": "Edmonton",
    "calgary": "Calgary",
}
SKIPPED = {"over votes", "under votes"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCES = {city: key("source", city) for city in CITIES}
PLURALITY = key("electoral_system", "plurality")


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


def fetch() -> None:
    for url in [*FILES.values(), *TOTALS.values()]:
        raw.fetch(SOURCE, url, suffix=".csv")
    for url in WARDS_FILES.values():
        raw.fetch(SOURCE, url, suffix=".json")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------------------------------------------- names


def _word(word: str) -> str:
    if not word.isupper():
        return word
    if word in {"JR.", "SR.", "JR", "SR"}:
        return word.capitalize().rstrip(".") + "."
    if re.fullmatch(r"[IVX]+", word):
        return word
    out = word.lower().title()
    out = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), out)
    out = re.sub(r"\bMac([a-z]{3,})", lambda m: m.group(0), out)
    out = re.sub(r"(?<=['-])([a-z])", lambda m: m.group(1).upper(), out)
    return out


def tidy(text: str) -> str:
    """A ballot name with each all-capitals word in title case."""
    return " ".join(_word(word) for word in " ".join(text.split()).split())


def person(text: str) -> str:
    """A name printed as `SURNAME, Given`, or as the given names then the surname, in ballot order."""
    text = " ".join(text.split())
    if "," in text:
        surname, given = (part.strip() for part in text.split(",", 1))
        suffix = re.match(r"(JR|SR)\.?,\s*(.*)", given, re.IGNORECASE)
        if suffix:
            surname, given = f"{surname} {suffix.group(1).capitalize()}.", suffix.group(2)
        return tidy(f"{given.replace(',', ' ')} {surname}")
    return tidy(text)


def tokens(name: str) -> tuple[str, ...]:
    """The letters-only words of a name, sorted, so that `Kristine ACIELO` meets `ACIELO, Kristine`."""
    return tuple(sorted(re.findall(r"[a-z]+", fold(name.replace("(", " ").replace(")", " ")))))


# ---------------------------------------------------------------------------------------------------- rows


class Row(NamedTuple):
    office: str  # mayor or councillor
    ward: str  # the ward the councillor race is for; empty for the mayor
    station: str  # the station's key in its year
    place: str  # the ward the station lies in; empty when it serves the whole city
    label: str  # the station's name
    channel: int
    candidate: str
    party: str
    votes: int
    acclaimed: bool


def channel_of(kind: str, label: str) -> int:
    text = f"{kind} {label}".lower()
    if "mail" in text:
        return 3
    if "advance" in text:
        return 2
    if any(word in text for word in ("hospital", "institution", "senior", "care facility", "travelling", "mobile")):
        return 6
    if kind.lower() in {"regular", "election day"}:
        return 1
    return 9


def _int(text: str) -> int:
    text = text.replace(",", "").strip()
    return int(text) if text else 0


def _csv(city: str, year: int, urls: Mapping[tuple[str, int], str]) -> list[dict[str, str]]:
    path = raw.cached(SOURCE, urls[(city, year)]).path
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(DictReader(handle))


def ward_key(text: str) -> str:
    text = fold(text.replace("\u2019", "'"))
    text = re.sub(r"^ward\s+", "", text)
    text = re.sub(r"\s+ward$", "", text)
    return text.lstrip("0") if text.strip("0").isdigit() else text


def _edmonton_place(label: str) -> str:
    """The ward an Edmonton station lies in, read from its name, or nothing for a city-wide station."""
    found = re.match(r"(?:Ward )?(\d{1,2})(?:-\d+| )", label)
    if found:
        return ward_key(found.group(1))
    found = re.match(r"([^-]+?) - ", label)
    if found and "city" not in found.group(1).lower():
        return ward_key(found.group(1))
    return ""


def edmonton(year: int) -> list[Row]:
    rows: list[Row] = []
    for line in _csv("edmonton", year, FILES):
        if year == 2010:
            office = {"Mayor": "mayor", "Councillor": "councillor"}.get(line["RaceName"].strip())
            ward, label, kind = line["WardName"], line["StationName"], line["StationTypeSortOrder"]
            name = line["CandidateName"]
            votes, acclaimed, party = _int(line["VotesReceived"]), line["Acclaimed"].lower() == "true", ""
            code = {"1": "Regular", "2": "Advance", "5": "Institutional mobile", "9": "Special"}[kind]
            kind = code
        else:
            office = {"Mayor": "mayor", "Councillor": "councillor"}.get(line["Contest Name"].strip())
            ward = line["Ward Name"]
            label = line["Voting Station Description"]
            kind = line["Voting Station Type"]
            if "Candidate Last Name" in line:
                name = f"{line['Candidate Last Name']}, {line['Candidate First Name']}"
            else:
                name = line["Candidate Name"]
            votes = _int(line["Votes Received"])
            acclaimed = line["Acclaimed"].lower() in {"yes", "true"}
            party = line.get("Party Name", "").strip()
        if office is None or person(name).lower() in SKIPPED:
            continue
        rows.append(
            Row(
                office,
                "" if office == "mayor" else ward_key(ward),
                label,
                _edmonton_place(label),
                label,
                channel_of(kind, label),
                person(name),
                party,
                votes,
                acclaimed,
            )
        )
    return rows


def calgary(year: int) -> list[Row]:
    rows: list[Row] = []
    for line in _csv("calgary", year, FILES):
        if year in (2013, 2017):
            office = {"MAYOR": "mayor", "COUNCILLOR": "councillor"}.get(line["Office"].strip())
            ward, station = line["Ward"].strip(), line["Voting Station ID"].strip()
            label = line.get("Voting Station Name", "") or f"Station {station}"
            kind = line.get("Voting Station Type", "Regular")
            name, party = line["Ballot Name"], ""
            votes = _int(line["Votes"])
            seat = ""
        else:
            text = line["OfficeType"].strip()
            if text.upper() == "MAYOR":
                office, seat = "mayor", ""
            elif found := re.fullmatch(r"(?i)councillor (?:for )?ward (\d+)", text):
                office, seat = "councillor", found.group(1)
            else:
                office, seat = None, ""
            ward, station = line["Ward"].strip(), line["VotingStationCode"].strip()
            label, kind = line["VotingStation"], line["VotingStationType"]
            name, votes = line["CandidateName"], _int(line["Votes"])
            party = line.get("Party", "").strip()
            party = "" if party.lower() == "none" else party
        if office is None:
            continue
        rows.append(
            Row(
                office,
                seat,
                station,
                "" if ward in {"", "0"} else ward,
                label,
                0 if year == 2013 else channel_of(kind, label if year == 2017 else ""),
                person(name),
                party,
                votes,
                False,
            )
        )
    if year in (2013, 2017):  # the earlier files give the station's ward, so a councillor's ward is the one he ran in
        home: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            if row.office == "councillor" and row.place:
                home[row.candidate][row.place] += row.votes
        rows = [
            row._replace(ward=home[row.candidate].most_common(1)[0][0]) if row.office == "councillor" else row
            for row in rows
        ]
    return rows


def rows(city: str, year: int) -> list[Row]:
    return edmonton(year) if city == "edmonton" else calgary(year)


def declared(city: str, year: int) -> dict[tuple[str, str, tuple[str, ...]], int]:
    """The authority's candidate totals, by office, ward (Edmonton only), and name."""
    found: dict[tuple[str, str, tuple[str, ...]], int] = defaultdict(int)
    if (city, year) not in TOTALS:
        return found
    for line in _csv(city, year, TOTALS):
        low = {k.lower().replace(" ", "_"): v for k, v in line.items()}
        if city == "edmonton":
            office = {"mayor": "mayor", "councillor": "councillor"}.get(low["contest"].strip().lower())
            name = low.get("candidate_name") or low["candidatename"]
            votes = low.get("votes_received") or low.get("votes") or "0"
            ward = "" if office == "mayor" else ward_key(low.get("ward_name") or low["wardname"])
        else:
            office = {"mayor": "mayor", "councillor": "councillor"}.get(low["office"].strip().lower())
            name, votes, ward = low["candidate_name"], low["total_votes"], ""
            if office == "mayor" and low["ward"].strip() != "0":
                continue  # a ward's own subtotal; the ward-0 row holds the city's total
        if office is not None:
            found[(office, ward, tokens(person(name)))] += _int(votes)
    return found


def turnout(city: str, year: int) -> dict[int, int]:
    """Edmonton's declared eligible voters (measure 2) and ballots cast (measure 3), where it prints them."""
    if city != "edmonton" or year not in (2017, 2021, 2025):
        return {}
    for line in _csv(city, year, TOTALS):
        low = {k.lower().replace(" ", "_"): v for k, v in line.items()}
        if low.get("contest", "").strip().lower() != "mayor":
            continue
        if year == 2017 and low.get("ballots_cast"):
            return {2: _int(low["city_wide_eligible_voters"]), 3: _int(low["ballots_cast"])}
        if year == 2021:
            return {2: _int(low["eligiblevoters"]), 3: _int(low["municipalballotscast"])}
        if year == 2025:
            return {2: _int(low["estimated_eligible_voters"]), 3: _int(low["number_of_eligible_voters_who_voted"])}
    return {}


# ---------------------------------------------------------------------------------------------------- geography


def _shape(geometry: dict[str, object]) -> shapely.Geometry:
    shape = shapely.make_valid(shapely.geometry.shape(geometry))
    shape = shapely.simplify(shape, 0.00005, preserve_topology=True)
    parts = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon([p for part in parts for p in shapely.get_parts(part)]), 4326)


def _wkb(shape: shapely.Geometry) -> str:
    return shapely.to_wkb(shapely.set_srid(shape, 4326), hex=True, include_srid=True)


class Ward(NamedTuple):
    name: str
    shape: shapely.Geometry | None


def wards(city: str) -> dict[str, dict[str, Ward]]:
    """Each ward map's wards of a city, by vintage and ward key."""
    found: dict[str, dict[str, Ward]] = {}
    if city == "edmonton":
        url = WARDS_FILES[(city, "wards")]
        data = json.loads(raw.cached(SOURCE, url).path.read_text(encoding="utf8"))
        for feature in data["features"]:
            props = feature["properties"]
            start = props["effective_start_date"][:10]
            vintage = {"2007-10-15": None, "2013-10-21": "2013", "2017-10-16": "2017", "2021-10-18": "current"}[start]
            if vintage is None:
                continue
            name = props["name_2"].strip()
            found.setdefault(vintage, {})[ward_key(props["name_1"])] = Ward(
                re.sub(r"^Ward 0?(\d)", r"Ward \1", name), _shape(feature["geometry"])
            )
        found["2010"] = {str(n): Ward(f"Ward {n}", None) for n in range(1, 13)}
    else:
        for vintage in ("2012", "2018", "current"):
            data = json.loads(raw.cached(SOURCE, WARDS_FILES[(city, vintage)]).path.read_text(encoding="utf8"))
            found[vintage] = {
                str(int(feature["properties"]["ward_num"])): Ward(
                    f"Ward {int(feature['properties']['ward_num'])}", _shape(feature["geometry"])
                )
                for feature in data["features"]
            }
    return found


def used(city: str) -> list[str]:
    """The vintages of a city's ward maps that an election uses."""
    return sorted({VINTAGES[city][year] for (c, year) in FILES if c == city})


def polity(city: str) -> str:
    return key("polity", city)


def city_area(city: str) -> str:
    return key("area", "city", city)


def ward_area(city: str, vintage: str, ward: str) -> str:
    return key("area", "ward", city, vintage, ward)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Calgary and Edmonton and the wards their councillors are elected for, with the outlines the cities "
        "publish as open data",
        depends=[],
    )
    p.external |= {CANADA, AB, AB_AREA}
    for city, label in CITIES.items():
        p.add(
            "source",
            id=SOURCES[city],
            kind="official_body",
            publisher=f"City of {label}",
            title=f"{label} open data: municipal election results and ward boundaries",
            url=PAGES[city],
            license=f"Open Government Licence - {'City of Edmonton' if city == 'edmonton' else 'City of Calgary'}",
            license_url="https://data.edmonton.ca/stories/s/OGL/"
            if city == "edmonton"
            else "https://data.calgary.ca/stories/s/Open-Calgary-Terms-of-Use/u45n-7awa",
            attribution=f"Contains information licensed under the Open Government Licence - City of {label}",
        )
    for (city, _), url in WARDS_FILES.items():
        p.artifact(raw.cached(SOURCE, url), SOURCES[city])
    p.add(
        "scheme",
        code="ca-ab.municipality",
        polity_id=AB,
        kind="administrative",
        rank=3,
        noun="municipality",
        code_system=None,
    )
    p.add("scheme", code="ca-ab.ward", polity_id=AB, kind="electoral_district", rank=4, noun="ward", code_system=None)
    p.add(
        "scheme",
        code="ca-ab.result_unit",
        polity_id=AB,
        kind="precinct",
        rank=5,
        noun="reporting unit",
        code_system=None,
    )
    for city, label in CITIES.items():
        maps = wards(city)
        place = city_area(city)
        town = polity(city)
        p.add("area", id=place, scheme_code="ca-ab.municipality")
        p.add("area_parent", area_id=place, parent_id=AB_AREA)
        p.name(place, label, "en", "official")
        p.add("polity", id=town, kind="municipality", iso_code=None)
        p.name(town, f"City of {label}", "en", "official")
        p.add("polity_parent", polity_id=town, parent_id=AB)
        p.add("polity_territory", polity_id=town, area_id=place)
        p.add("slug", path=f"ca/ab/{city}", entity_id=town, is_current=True)
        for vintage in used(city):
            start, end = SPANS[city][vintage]
            members = maps[vintage]
            shapes = [ward.shape for ward in members.values() if ward.shape is not None]
            if shapes:
                outline = shapely.ops.unary_union(shapes)
                parts = [part for part in shapely.get_parts(outline) if shapely.get_type_id(part) in (3, 6)]
                outline = shapely.set_srid(
                    shapely.MultiPolygon([q for part in parts for q in shapely.get_parts(part)]), 4326
                )
                p.add(
                    "area_geometry",
                    area_id=place,
                    valid_from=start,
                    valid_to=end,
                    geom=_wkb(outline),
                    label_point=_wkb(shapely.point_on_surface(outline)),
                    quality="official",
                    source_id=SOURCES[city],
                )
            for number, ward in sorted(members.items()):
                area = ward_area(city, vintage, number)
                p.add("area", id=area, scheme_code="ca-ab.ward")
                p.add("area_parent", area_id=area, parent_id=place)
                p.name(area, ward.name, "en", "official")
                if ward.shape is not None:
                    p.add(
                        "area_geometry",
                        area_id=area,
                        valid_from=start,
                        valid_to=end,
                        geom=_wkb(ward.shape),
                        label_point=_wkb(shapely.point_on_surface(ward.shape)),
                        quality="official",
                        source_id=SOURCES[city],
                    )
    return p


# ---------------------------------------------------------------------------------------------------- registry


def body(city: str) -> str:
    return key("body", city)


def mayoralty(city: str) -> str:
    return key("office", city)


def party(city: str, name: str) -> str:
    return key("party", city, fold(name))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The city councils, mayoralties, elector organizations, and electoral system of Calgary's and Edmonton's "
        "municipal elections, with the files their results come from",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {CANADA, AB}
    for url in [*FILES.values(), *TOTALS.values()]:
        city = "edmonton" if "edmonton" in url else "calgary"
        p.artifact(raw.cached(SOURCE, url), SOURCES[city])
    p.add(
        "electoral_system",
        id=PLURALITY,
        polity_id=AB,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.name(PLURALITY, "Alberta local plurality voting", "en", "official")
    for city, label in CITIES.items():
        p.add("body", id=body(city), polity_id=polity(city), kind="council")
        p.name(body(city), f"{label} City Council", "en", "official")
        p.add("office", id=mayoralty(city), polity_id=polity(city), kind="mayor")
        p.name(mayoralty(city), f"Mayor of {label}", "en", "official")
    seen: dict[str, tuple[str, str]] = {}
    for city, year in FILES:
        for row in rows(city, year):
            if row.party:
                seen[party(city, row.party)] = (city, row.party)
    for id, (city, name) in sorted(seen.items()):
        p.add("party", id=id, kind="party", polity_id=polity(city))
        p.name(id, name, "en", "official")
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


class Contest(NamedTuple):
    office: str
    ward: str


def election(year: int) -> Package:
    day = DAYS[year]
    close = day + timedelta(days=1)
    cities = [city for (city, y) in FILES if y == year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} municipal elections of "
        + " and ".join(CITIES[c] for c in cities)
        + ": the mayor and each ward's councillor, with the votes of each candidate at each voting station",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {CANADA, AB}
    umbrella = key("election", year)
    p.add("election", id=umbrella, polity_id=AB, kind="local", date_from=day, date_to=day, status="held")
    p.name(umbrella, f"{year} Alberta municipal elections", "en", "official")
    p.add("slug", path=f"ca/ab/{day}-local", entity_id=umbrella, is_current=True)
    note = (
        "The cities' files give each candidate's votes at each voting station and name no winners. The candidate "
        "with the most votes in a race is recorded as elected, which is how the Local Authorities Election Act "
        "decides a race with one seat. School board trustee races are not included. Where the city also publishes "
        "its own candidate totals, they are stored as declared totals and agree with the sum of the stations."
    )
    p.add("note", id=key("note", year), entity_id=umbrella, kind="methodology", body_md=note, lang="en")
    taken: set[str] = set()
    problems: list[str] = []
    gaps: list[str] = []
    for city in cities:
        label = CITIES[city]
        p.uses(raw.cached(SOURCE, FILES[(city, year)]))
        if (city, year) in TOTALS:
            p.uses(raw.cached(SOURCE, TOTALS[(city, year)]))
        vintage = VINTAGES[city][year]
        maps = wards(city)[vintage]
        totals = declared(city, year)
        extra = turnout(city, year)
        vote = key("election", year, city)
        p.add(
            "election",
            id=vote,
            polity_id=polity(city),
            parent_id=umbrella,
            kind="local",
            date_from=day,
            date_to=day,
            status="held",
        )
        p.name(vote, f"{year} {label} election", "en", "official")
        p.add("slug", path=f"ca/ab/{city}/{day}-local", entity_id=vote, is_current=True)
        found = rows(city, year)
        # Reporting units, one for each station.
        units: dict[str, str] = {}
        places: dict[str, str] = {}
        for row in found:
            if row.station in places and places[row.station] != row.place and row.place:
                problems.append(
                    f"{city} {year} station {row.station} lies in wards {places[row.station]} and {row.place}"
                )
            places.setdefault(row.station, row.place)
        names = {row.station: row.label for row in found}
        for station, place in sorted(places.items()):
            unit = units[station] = key("unit", day, city, station)
            area = ward_area(city, vintage, place) if place in maps else city_area(city)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CANADA,
                scheme_code="ca-ab.result_unit",
                code=f"{day}:{city}:{station}",
                area_relation="within",
                area_id=area,
                kind="standard",
                valid_from=day,
                valid_to=close,
            )
            p.name(unit, names[station], "en", "official")
        races: dict[Contest, list[Row]] = defaultdict(list)
        for row in found:
            races[Contest(row.office, row.ward)].append(row)
        for contest_key, members in sorted(races.items()):
            mayoral = contest_key.office == "mayor"
            if not mayoral and contest_key.ward not in maps:
                problems.append(f"{city} {year} has no ward {contest_key.ward}")
                continue
            place = city_area(city) if mayoral else ward_area(city, vintage, contest_key.ward)
            tag = f"{city}:{contest_key.office}:{contest_key.ward}"
            contest = key("contest", year, tag)
            title = f"Mayor of {label}" if mayoral else f"Councillor, {maps[contest_key.ward].name}"
            p.add(
                "contest",
                id=contest,
                election_id=vote,
                kind="office" if mayoral else "body_seats",
                office_id=mayoralty(city) if mayoral else None,
                body_id=None if mayoral else body(city),
                district_area_id=place,
                magnitude=1,
                round_number=1,
                electoral_system_id=PLURALITY,
                default_result_kind="counted",
            )
            p.name(contest, f"{year} {title}", "en", "official")
            path = f"ca/ab/{city}/{day}-local/" + ("mayor" if mayoral else f"councillor-ward-{slug(contest_key.ward)}")
            while path in taken:
                path += "-2"
            taken.add(path)
            p.add("slug", path=path, entity_id=contest, is_current=True)
            candidates: dict[str, list[Row]] = defaultdict(list)
            for row in members:
                candidates[row.candidate].append(row)
            tacit = all(row.acclaimed for row in members)
            sums = {name: sum(r.votes for r in rs) for name, rs in candidates.items()}
            top = max(sums.values())
            winners = [name for name, total in sums.items() if total == top]
            if not tacit and len(winners) != 1:
                problems.append(f"{city} {year} {tag} has a tie for first place")
            if tacit and len(candidates) != 1:
                problems.append(f"{city} {year} {tag} acclaimed with {len(candidates)} candidates")
            result_set = key("result_set", year, tag)
            p.add(
                "result_set",
                id=result_set,
                election_id=vote,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                source_id=SOURCES[city],
                status="active",
                is_default=True,
                completeness=None if tacit else 1.0,
            )
            for position, name in enumerate(sorted(candidates, key=lambda n: (-sums[n], n)), 1):
                option = key("option", year, tag, name)
                owner = next((r.party for r in candidates[name] if r.party), "")
                club = party(city, owner) if owner else None
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=club,
                    ballot_position=None,
                    ballot_label=name,
                )
                p.add(
                    "candidacy",
                    id=key("candidacy", year, tag, name),
                    person_id=(who := key("person", year, tag, name)),
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=club,
                )
                p.add("person", id=who, birth_precision=None)
                p.name(who, name, "en", "official")
                if name in winners and (tacit or len(winners) == 1):
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                if tacit:
                    continue
                by_unit: dict[tuple[str, int], int] = defaultdict(int)
                for row in candidates[name]:
                    by_unit[(units[row.station], row.channel)] += row.votes
                for (unit, channel), votes in sorted(by_unit.items()):
                    _value(p, result_set, unit, option, channel, 1, votes)
                want = totals.get((contest_key.office, contest_key.ward if city == "edmonton" else "", tokens(name)))
                if want is not None:
                    p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=want)
                    if want != sums[name]:
                        gaps.append(f"{city} {year} {tag} {name}: stations {sums[name]}, declared {want}")
                elif totals and not tacit:
                    problems.append(f"{city} {year} {tag} {name}: no declared total")
            if mayoral and extra:
                for measure, value in extra.items():
                    p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    if problems:
        raise ValueError("\n".join(problems))
    if gaps:
        GAPS[year] = gaps
    reasons = []
    if gaps:
        reasons.append(
            f"for {len(gaps)} candidates the city's candidate totals and its voting station file, two files it "
            "publishes, differ by a few votes"
        )
    if any(turnout(city, year) for city in cities):
        reasons.append(
            "the declared eligible voters and ballots cast are the city's totals, which the voting station files "
            "do not carry"
        )
    if reasons:
        p.accept("accounting.declared", "; ".join(reasons))
    return p


GAPS: dict[int, list[str]] = {}
