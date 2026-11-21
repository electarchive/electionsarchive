"""Georgia's November 3, 2026 general and special election as the Secretary of State certified it.

The Secretary of State's results portal publishes every contest by county and by precinct, with the votes split by how
they were cast. The portal marks the election's results official. The 2026 general election, its contests, and their
options already exist; the package attaches a certified result set at county grain (the default) and one at precinct
grain to each statewide, congressional, and legislative contest and to each constitutional amendment. Contests for a
district that a county shares with others report each county's part as a county-part unit.

The nine district attorney contests are new to the archive. The `circuits` package adds their judicial circuits, shaped
as the union of their counties' outlines, and the `district-attorney` package adds the offices, contests, candidates,
and the same two result sets.
"""

import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple
from uuid import uuid5

import duckdb
import polars as pl
import shapefile
import shapely
import shapely.geometry

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA

SOURCE = "us.ga_certified_2026"
SLUG = "11032026GeneralAndSpecialElection"
PORTAL = f"https://results.sos.ga.gov/results/public/Georgia/elections/{SLUG}"
EVENT = f"https://results.sos.ga.gov/results/public/api/elections/Georgia/{SLUG}/data"
EXPORT = f"https://results.sos.ga.gov/cdn/results/Georgia/export-{SLUG}.json"
PUBLISHER = "Georgia Secretary of State"
TITLE = "November 3, 2026 - General & Special Elections results"
DAY = date(2026, 11, 3)
REFERENCE = DATA / "reference" / "us"
UNITS = f"{SOURCE}/units"
CIRCUITS = f"{SOURCE}/circuits"
ATTORNEYS = f"{SOURCE}/district-attorney"
COUNTY_SHAPES = "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_county_500k.zip"
DA_TITLE = re.compile(r"District Attorney - (?P<circuit>.+? Judicial Circuit)(?: \((?P<kind>[^)]+)\))?")
ELECTION = "2026 Georgia general election"
CHANNELS = {"election day": 1, "advance voting": 2, "absentee by mail": 3, "provisional": 5}
STATEWIDE = {
    "US Senate": "2026 U.S. Senate election in Georgia",
    "Governor": "2026 Georgia gubernatorial election",
    "Lieutenant Governor": "2026 Lieutenant Governor election",
    "Secretary of State": "2026 Secretary of State election",
    "Attorney General": "2026 Attorney General election",
    "Commissioner of Agriculture": "2026 Commissioner of Agriculture election",
    "Commissioner of Insurance": "2026 Commissioner of Insurance election",
    "State School Superintendent": "2026 School Superintendent election",
    "Commissioner of Labor": "2026 Commissioner of Labor election",
    "PSC - District 3": "2026 Public Service Commissioner election (District 3)",
    "PSC - District 5": "2026 Public Service Commissioner election (District 5)",
}
# The portal spells one candidate differently from the archive's ballot label.
SPELLINGS = {
    ("2026 Georgia House of Representatives election, District 59", "devantasullysullivan"): "devontasullysullivan"
}
METHOD = (
    "County totals from the Secretary of State's results portal, which marks the election's results official "
    "(published November 20, 2026). Contests for a district report each county's part of the district. The portal "
    "marks no winner, and a Georgia general election needs a majority, so no outcome is stored here. Write-in votes "
    "are not in the portal's files."
)
PRECINCT_METHOD = (
    "Votes by precinct from the Secretary of State's official results, split by how the ballots were cast (election "
    "day, advance voting, absentee by mail, and provisional) where the portal gives the split, and as one total "
    "where it doesn't. The precinct rows sum to the county totals of the default result set except where the "
    "portal's precinct file falls short of its own county total."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def norm(text: str) -> str:
    text = re.sub(r"(\s*\([^)]*\))+\s*$", "", text)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def ordinal(number: int) -> str:
    suffix = "th" if 10 <= number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def fetch() -> None:
    raw.fetch(SOURCE, EVENT, suffix=".json")
    raw.fetch(SOURCE, EXPORT, suffix=".json")
    raw.fetch(SOURCE, COUNTY_SHAPES, suffix=".zip")


def event() -> dict[str, Any]:
    data = json.loads(raw.cached(SOURCE, EVENT).path.read_text())
    if data["election"]["isOfficialResults"] is not True or data["election"]["electionDate"] != DAY.isoformat():
        raise ValueError("the portal does not mark the November 3, 2026 results official")
    return data


def export() -> dict[str, Any]:
    data = json.loads(raw.cached(SOURCE, EXPORT).path.read_text())
    if data["electionDate"] != DAY.isoformat():
        raise ValueError(f"the export is for {data['electionDate']}")
    return data


class Contest(NamedTuple):
    id: str
    name: str
    kind: str
    district: str | None
    options: dict[str, str]  # normalized ballot label -> option id


class Reference(NamedTuple):
    polity: str
    election: str
    counties: dict[str, tuple[str, str, str]]  # normalized county name -> FIPS, unit id, area id
    contests: dict[str, Contest]


def reference() -> Reference:
    con = duckdb.connect()
    path = {
        name: str(REFERENCE / f"{name}.parquet")
        for name in ("polity", "area", "reporting_unit", "contest", "contest_option", "election")
    }
    (polity,) = con.execute(f"select id from '{path['polity']}' where iso_code = 'US-GA'").fetchone() or (None,)
    if polity is None:
        raise LookupError("the reference holds no US-GA polity")
    found = con.execute(
        f"select id from '{path['election']}' where polity_id = ? and date_from = date '{DAY}' and name = ?",
        [polity, ELECTION],
    ).fetchall()
    if len(found) != 1:
        raise LookupError(f"the reference holds {len(found)} elections named {ELECTION}")
    election = found[0][0]
    counties: dict[str, tuple[str, str, str]] = {}
    for fips, unit, area, name in con.execute(
        f"""select u.code, u.id, u.area_id, a.name from '{path["reporting_unit"]}' u
            join '{path["area"]}' a on a.id = u.area_id
            where u.scheme_code = 'us.county' and u.polity_id = ?""",
        [polity],
    ).fetchall():
        counties[norm(name.removesuffix(", GA"))] = (fips, unit, area)
    contests: dict[str, Contest] = {}
    for contest, name, kind, district, option, label in con.execute(
        f"""select c.id, c.name, c.kind, c.district_area_id, o.id, o.ballot_label
            from '{path["contest"]}' c join '{path["contest_option"]}' o on o.contest_id = c.id
            where c.election_id = ?""",
        [election],
    ).fetchall():
        entry = contests.setdefault(name, Contest(contest, name, kind, district, {}))
        entry.options[norm(label)] = option
    return Reference(polity, election, counties, contests)


def contest_name(item: dict[str, Any], ref: Reference) -> str | None:
    title = item["name"].split("/")[0].strip()
    if title in STATEWIDE:
        return STATEWIDE[title]
    if found := re.fullmatch(r"US House of Representatives - District (\d+)", title):
        return f"2026 U.S. House election in Georgia's {ordinal(int(found[1]))} congressional district"
    if found := re.fullmatch(r"State Senate - District (\d+)", title):
        return f"2026 Georgia Senate election, District {found[1]}"
    if found := re.fullmatch(r"State House of Representatives - District (\d+)", title):
        return f"2026 Georgia House of Representatives election, District {found[1]}"
    if found := re.fullmatch(r"Proposed Constitutional Amendment (\d+)", title):
        (named,) = [name for name in ref.contests if name.startswith(f"Amendment {found[1]}:")]
        return named
    return None


def precinct_code(fips: str, county: str, precinct: str) -> str:
    return f"{DAY}:GA:{fips}:{county.upper()}:{precinct}"


def county_part_code(fips: str, district: str) -> str:
    return f"{DAY}:{fips}:{district}"


def district_key(name: str) -> str | None:
    if found := re.search(r"(\d+)(?:st|nd|rd|th) congressional district", name):
        return "USH:" + found[1]
    if found := re.fullmatch(r"2026 Georgia (Senate|House of Representatives) election, District (\d+)", name):
        return ("SD" if found[1] == "Senate" else "HD") + ":" + found[2]
    return None


def split(option: dict[str, Any], group: list[dict[str, Any]], total: int) -> dict[int, int]:
    """The votes by channel when the portal splits them and the split sums to the total, else one total."""
    amounts = {CHANNELS[g["groupName"].lower()]: g["voteCount"] for g in group if g["voteCount"] is not None}
    if len(amounts) == len(group) and sum(amounts.values()) == total:
        return amounts
    return {0: total}


def county_shapes() -> dict[str, shapely.Geometry]:
    """Georgia's county outlines from the Census Bureau's cartographic boundary file, by five-digit FIPS code."""
    with zipfile.ZipFile(raw.cached(SOURCE, COUNTY_SHAPES).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist() if "." in name}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    outlines: dict[str, shapely.Geometry] = {}
    for item in reader.iterShapeRecords():
        geoid = str(item.record["GEOID"])  # ty: ignore[not-subscriptable]
        if geoid.startswith("13") and item.shape is not None:
            outlines[geoid] = shapely.geometry.shape(item.shape.__geo_interface__)
    return outlines


class Attorney(NamedTuple):
    item: dict[str, Any]
    number: str  # the portal's district number, as in SDA5
    circuit: str  # the circuit's name, such as Atlantic Judicial Circuit
    special: bool


def attorney_items(data: dict[str, Any]) -> list[Attorney]:
    found = []
    for item in data["results"]["ballotItems"]:
        match = re.fullmatch(r"SDA(\d+)", item["id"])
        if match is None:
            continue
        title = DA_TITLE.fullmatch(item["name"])
        if title is None or title["kind"] not in (None, "Special Primary"):
            raise ValueError(f"{item['id']}: unexpected title {item['name']}")
        found.append(Attorney(item, match[1], title["circuit"], title["kind"] is not None))
    return found


def ballot_name(label: str) -> tuple[str, bool]:
    """A candidate's name and incumbency from the portal's label, which appends (I) and the party."""
    return re.sub(r"\s*\((?:I|Rep|Dem)\)", "", label).strip(), "(I)" in label


def district_attorneys(
    ref: Reference,
    data: dict[str, Any],
    stamp: datetime,
    fips_of: dict[str, str],
    name_of: dict[str, str],
    county_ref: dict[str, tuple[str, str, str]],
    precincts: dict[tuple[str, str], str],
) -> tuple[Package, Package]:
    """The judicial circuits of the district attorney contests, and the contests with their certified results."""
    con = duckdb.connect()
    parties = {
        name: found
        for found, name in con.execute(
            f"select id, name from '{REFERENCE / 'party.parquet'}' where pipeline = 'us.fec' "
            "and name in ('Democratic Party', 'Republican Party')"
        ).fetchall()
    }
    party_of = {"Dem": parties["Democratic Party"], "Rep": parties["Republican Party"]}
    (state_area,) = con.execute(
        f"select id from '{REFERENCE / 'area.parquet'}' where scheme_code = 'us.state' and name = 'Georgia'"
    ).fetchone() or (None,)
    if state_area is None:
        raise LookupError("the reference holds no Georgia state area")
    (state_polity,) = con.execute(
        f"select id from '{REFERENCE / 'polity.parquet'}' where iso_code = 'US-GA'"
    ).fetchone() or (None,)
    if state_polity != ref.polity:
        raise LookupError("the Georgia polity differs from the election's")
    src = key("source", "results")
    census = key("source", "county-shapes")
    attorneys = attorney_items(data)
    counties_of: dict[str, list[str]] = defaultdict(list)
    for locality in data["localResults"]:
        for item in locality["ballotItems"]:
            if item["id"].startswith("SDA"):
                counties_of[item["id"]].append(fips_of[locality["id"]])
    shapes = county_shapes()
    values: list[tuple[str, str, str, int, int]] = []

    geo = Package(
        CIRCUITS,
        SOURCE,
        "Georgia's judicial circuits that elected a district attorney on November 3, 2026, shaped as the union of "
        "their counties",
    )
    geo.external |= {ref.polity, state_area, "us.judicial_district"}
    geo.add(
        "source",
        id=census,
        kind="official_body",
        publisher="U.S. Census Bureau",
        title="Cartographic Boundary Files, 2024, counties at 1:500,000",
        url="https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html",
        license="Public domain",
        attribution="U.S. Census Bureau",
    )
    geo.artifact(raw.cached(SOURCE, COUNTY_SHAPES), census)

    p = Package(
        ATTORNEYS,
        SOURCE,
        "Certified results of Georgia's November 3, 2026 district attorney contests by county and by precinct",
        depends=[UNITS, CIRCUITS],
    )
    p.external.add(ref.election)
    p.external |= set(party_of.values())
    for url in (EVENT, EXPORT):
        p.uses(raw.cached(SOURCE, url))
    for circuit in attorneys:
        item = circuit.item
        fips = sorted(counties_of[item["id"]])
        missing = [code for code in fips if code not in shapes]
        if not fips or missing:
            raise LookupError(f"{item['id']}: no county shapes for {missing}")
        label = circuit.circuit
        code = f"GA:circuit-{circuit.number}"
        polity = geo.id("polity", code)
        area = geo.id("area", code)
        path = f"us/ga/{slug(label)}"
        geo.add("polity", id=polity, kind="judicial_district")
        geo.name(polity, f"{label}, GA", "en", "official")
        geo.add("polity_parent", polity_id=polity, parent_id=ref.polity)
        geo.add("slug", path=path, entity_id=polity, is_current=True)
        geo.add("area", id=area, scheme_code="us.judicial_district")
        geo.add("area_code", area_id=area, code=code)
        geo.add("area_parent", area_id=area, parent_id=state_area)
        geo.name(area, f"{label}, GA", "en", "official")
        geo.add("polity_territory", polity_id=polity, area_id=area)
        shape = shapely.union_all([shapes[code] for code in fips])
        shape = shapely.set_srid(
            shapely.MultiPolygon(
                [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) == 3]
            ),
            4326,
        )
        geo.add(
            "area_geometry",
            area_id=area,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="official",
            source_id=census,
        )
        for county in fips:
            geo.external.add(county_ref[county][2])
            geo.add("area_membership", area_id=county_ref[county][2], parent_id=area, partial=False)
        geo.add(
            "reporting_unit",
            id=geo.id("unit", code),
            polity_id=polity,
            scheme_code="us.judicial_district",
            code=code,
            area_relation="equals",
            area_id=area,
            kind="standard",
        )

        office = p.id("office", code)
        p.add("office", id=office, polity_id=polity, kind="other")
        p.external.add(polity)
        title = f"District Attorney, {label}"
        p.name(office, title, "en", "official")
        contest = p.id("contest", code)
        p.add(
            "contest",
            id=contest,
            election_id=ref.election,
            kind="office",
            office_id=office,
            district_area_id=area,
            magnitude=item["voteFor"],
            round_number=1,
            default_result_kind="counted",
        )
        p.external.add(area)
        p.name(contest, f"2026 {title} election", "en", "official")
        p.add("slug", path=f"us/ga/{DAY}-general/district-attorney-{slug(label)}", entity_id=contest, is_current=True)
        p.add("citation", id=p.id("citation", contest), entity_id=contest, source_id=src)
        options: dict[str, str] = {}
        for position, option in enumerate(item["ballotOptions"], start=1):
            name, incumbent = ballot_name(option["name"])
            party = party_of.get(option["politicalParty"])
            who = norm(name)
            oid = options[option["id"]] = p.id("option", code, who)
            person = p.id("person", code, who)
            p.add("person", id=person)
            p.name(person, name, "en", "official")
            p.add(
                "contest_option",
                id=oid,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=name,
            )
            p.add(
                "candidacy",
                id=p.id("candidacy", code, who),
                person_id=person,
                option_id=oid,
                role="head",
                ballot_name=name,
                member_of_party_id=party,
                is_independent=False,
                is_incumbent=incumbent,
            )

        county_set = p.id("result_set", code, "county")
        precinct_set = p.id("result_set", code, "precinct")
        for result_set, default, note in ((county_set, True, METHOD), (precinct_set, False, PRECINCT_METHOD)):
            p.add(
                "result_set",
                id=result_set,
                election_id=ref.election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                source_id=src,
                status="active",
                is_default=default,
                completeness=1.0,
                as_of=stamp,
            )
            p.add(
                "note",
                id=p.id("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=note
                + (
                    " The portal lists the contest as a special primary; it was held with the general election."
                    if circuit.special
                    else ""
                ),
                lang="en",
            )
        rows: list[tuple[str, str, str, int, int]] = []
        totals: dict[str, int] = defaultdict(int)
        for locality in data["localResults"]:
            local = next((i for i in locality["ballotItems"] if i["id"] == item["id"]), None)
            if local is None:
                continue
            county = fips_of[locality["id"]]
            p.external.add(county_ref[county][1])
            for option in local["ballotOptions"]:
                oid = options[option["id"]]
                rows.append((county_set, county_ref[county][1], oid, 0, option["voteCount"]))
                totals[oid] += option["voteCount"]
                for precinct in option["precinctResults"] or []:
                    if (locality["id"], precinct["id"]) not in precincts:
                        raise LookupError(f"{item['id']}: precinct {precinct['name']} has no unit")
                    unit = key("unit", precinct_code(county, name_of[county], precinct["id"]))
                    for channel, amount in split(option, precinct["groupResults"], precinct["voteCount"]).items():
                        rows.append((precinct_set, unit, oid, channel, amount))
        for option in item["ballotOptions"]:
            oid = options[option["id"]]
            if totals[oid] != option["voteCount"]:
                raise ValueError(f"{item['name']} {option['name']}: counties don't sum to the statewide total")
            p.add("declared", result_set_id=county_set, option_id=oid, measure=1, value=option["voteCount"])
        values.extend(rows)
    p.frame(
        "result_value",
        pl.DataFrame(
            [(*row[:4], 1, 0, 0, row[4]) for row in values],
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ),
    )
    return geo, p


def build() -> list[Package]:
    ref = reference()
    meta = event()
    data = export()
    stamp = datetime.fromisoformat(meta["election"]["asOf"].split(".")[0] + "+00:00")
    statewide = {item["id"]: item for item in data["results"]["ballotItems"]}

    mapped: dict[str, tuple[Contest, dict[str, Any]]] = {}
    for item in statewide.values():
        name = contest_name(item, ref)
        if name is None:
            continue
        if name not in ref.contests:
            raise LookupError(f"{item['id']}: the reference has no contest named {name}")
        if name in {c.name for c, _ in mapped.values()}:
            raise ValueError(f"two portal contests map to {name}")
        mapped[item["id"]] = (ref.contests[name], item)
    # The portal's option ids by contest, matched to the archive's ballot labels.
    options: dict[str, dict[str, str]] = {}
    for cid, (contest, item) in mapped.items():
        matched: dict[str, str] = {}
        for option in item["ballotOptions"]:
            label = SPELLINGS.get((contest.name, norm(option["name"])), norm(option["name"]))
            if label not in contest.options:
                raise LookupError(f"{contest.name}: no option for {option['name']}")
            matched[option["id"]] = contest.options[label]
        if len(set(matched.values())) != len(matched):
            raise ValueError(f"{contest.name}: two portal options match one archive option")
        options[cid] = matched

    # Votes: contest -> county -> option -> total, and contest -> county -> precinct -> option -> by channel.
    county_votes: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    precinct_votes: dict[str, dict[str, dict[str, dict[str, dict[int, int]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(dict))
    )
    precincts: dict[tuple[str, str], str] = {}
    fips_of: dict[str, str] = {}
    for locality in data["localResults"]:
        found = ref.counties.get(norm(locality["name"]))
        if found is None:
            raise LookupError(f"no county unit for {locality['name']}")
        fips_of[locality["id"]] = found[0]
        for item in locality["ballotItems"]:
            if item["id"] not in mapped:
                continue
            for option in item["ballotOptions"]:
                oid = options[item["id"]].get(option["id"])
                if oid is None:
                    raise LookupError(f"{locality['name']} {item['id']}: unknown option {option['name']}")
                county_votes[item["id"]][locality["id"]][oid] = option["voteCount"]
                for pr in option["precinctResults"] or []:
                    precincts[(locality["id"], pr["id"])] = pr["name"]
                    precinct_votes[item["id"]][locality["id"]][pr["id"]][oid] = split(
                        option, pr["groupResults"], pr["voteCount"]
                    )
    for cid, (contest, item) in mapped.items():
        for option in item["ballotOptions"]:
            oid = options[cid][option["id"]]
            if sum(by[oid] for by in county_votes[cid].values()) != option["voteCount"]:
                raise ValueError(f"{contest.name} {option['name']}: counties don't sum to the statewide total")

    src = key("source", "results")
    units = Package(
        UNITS,
        SOURCE,
        "Georgia's precincts and county parts for November 3, 2026, with the Secretary of State as the source of "
        "the election's results",
    )
    units.add(
        "source",
        id=src,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=PORTAL,
        license=None,
        attribution=PUBLISHER,
    )
    for url in (EVENT, EXPORT):
        units.artifact(raw.cached(SOURCE, url), src)
    units.external |= {ref.polity, "us.precinct", "us.county_part"}
    county_ref = {fips_of[lid]: next(v for v in ref.counties.values() if v[0] == fips_of[lid]) for lid in fips_of}
    name_of = {fips_of[locality["id"]]: locality["name"].removesuffix(" County") for locality in data["localResults"]}
    for fips, (_, unit, area) in county_ref.items():
        units.external |= {unit, area}
    for (lid, pid), name in sorted(precincts.items()):
        fips = fips_of[lid]
        code = precinct_code(fips, name_of[fips], pid)
        units.add(
            "reporting_unit",
            id=key("unit", code),
            polity_id=ref.polity,
            scheme_code="us.precinct",
            code=code,
            parent_id=county_ref[fips][1],
            area_relation="within",
            area_id=county_ref[fips][2],
            kind="standard",
            valid_from=DAY,
            valid_to=DAY + timedelta(days=1),
        )
        units.name(key("unit", code), name, "en", "official")
    parts: dict[tuple[str, str], set[str]] = defaultdict(set)
    for cid, (contest, _) in mapped.items():
        district = district_key(contest.name)
        if district is None:
            continue
        if contest.district is None:
            raise LookupError(f"{contest.name} has no district area")
        for lid in county_votes[cid]:
            parts[(fips_of[lid], district)].add(contest.district)
    for (fips, district), areas in sorted(parts.items()):
        code = county_part_code(fips, district)
        units.add(
            "reporting_unit",
            id=key("unit", code),
            polity_id=ref.polity,
            scheme_code="us.county_part",
            code=code,
            parent_id=county_ref[fips][1],
            area_relation="within",
            area_id=county_ref[fips][2],
            kind="standard",
            valid_from=DAY,
            valid_to=DAY + timedelta(days=1),
        )
        for area in sorted(areas):
            units.external.add(area)
            units.add(
                "reporting_unit_area",
                unit_id=key("unit", code),
                area_id=area,
                valid_from=DAY,
                valid_to=DAY + timedelta(days=1),
                partial=True,
                method="official_list",
            )

    p = Package(
        f"{SOURCE}/general",
        SOURCE,
        "Certified results of Georgia's November 3, 2026 general election by county and by precinct",
        depends=[UNITS],
    )
    p.external.add(ref.election)
    p.external |= {unit for _, unit, _ in county_ref.values()}
    for url in (EVENT, EXPORT):
        p.uses(raw.cached(SOURCE, url))
    value_rows: list[tuple[str, str, str, int, int]] = []
    for cid, (contest, item) in sorted(mapped.items(), key=lambda pair: pair[1][1]["ballotOrder"]):
        district = district_key(contest.name)
        option_ids = list(options[cid].values())
        p.external |= {contest.id, *option_ids}
        county_set = key("result_set", cid, "county")
        precinct_set = key("result_set", cid, "precinct")
        for result_set, default, note in ((county_set, True, METHOD), (precinct_set, False, PRECINCT_METHOD)):
            p.add(
                "result_set",
                id=result_set,
                election_id=ref.election,
                contest_id=contest.id,
                stage="certified",
                kind="counted",
                source_id=src,
                status="active",
                is_default=default,
                completeness=1.0,
                as_of=stamp,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=note,
                lang="en",
            )
        for lid, by_option in sorted(county_votes[cid].items()):
            fips = fips_of[lid]
            if district is None:
                unit = county_ref[fips][1]
            else:
                unit = key("unit", county_part_code(fips, district))
            for oid, amount in by_option.items():
                value_rows.append((county_set, unit, oid, 0, amount))
            for pid, by_precinct in precinct_votes[cid][lid].items():
                punit = key("unit", precinct_code(fips, name_of[fips], pid))
                for oid, channels in by_precinct.items():
                    for channel, amount in channels.items():
                        value_rows.append((precinct_set, punit, oid, channel, amount))
        for option in item["ballotOptions"]:
            p.add(
                "declared",
                result_set_id=county_set,
                option_id=options[cid][option["id"]],
                measure=1,
                value=option["voteCount"],
            )
    p.frame(
        "result_value",
        pl.DataFrame(
            [(*row[:4], 1, 0, 0, row[4]) for row in value_rows],
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ),
    )
    circuits, attorneys = district_attorneys(ref, data, stamp, fips_of, name_of, county_ref, precincts)
    return [units, p, circuits, attorneys]


def packages() -> list[str]:
    return [UNITS, f"{SOURCE}/general", CIRCUITS, ATTORNEYS]


def parse() -> None:
    fetch()
    for built in build():
        built.write()


def emit() -> None:
    package.run("load", packages())
