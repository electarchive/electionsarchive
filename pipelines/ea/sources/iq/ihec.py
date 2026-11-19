"""Iraq's Council of Representatives election of 10 October 2021, from the Independent High Electoral Commission.

The 2018 election, published only as PDFs, is read by `ea.sources.iq.ihec_2018` and built here with the rest.

The Commission publishes its final results as a Power BI report embedded on its results page. The report's data model
holds a table of every candidate's final votes by electoral district, with the seat each candidate stood for and
whether the candidate won, and a table of every polling station's voters and valid and invalid ballots. The
pipeline reads both through the report's public query endpoint, one page of rows at a time.
"""

import json
import re
from datetime import UTC, date, datetime
from uuid import uuid5

import httpx
import polars as pl
import shapely
import shapely.geometry

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.cr.tse import dsr

SOURCE = "iq.ihec"
IRAQ = str(uuid5(package.NAMESPACE, "polity\x1fIQ"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fIRQ"))
HOME = "https://ihec.iq/result-2021/"
REPORT = (
    "https://app.powerbi.com/view?r=eyJrIjoiNmUzYjAzNTYtZTJiNS00NzhkLTg0ZWUtMzlkZDAyZGM5NmMzIiwidCI6IjNkZTVhZmM2LWZh"
    "MDItNDM3OS04MDJkLThjZjY3YjNmYzQ0ZiIsImMiOjEwfQ%3D%3D"
)
QUERY = "https://wabi-east-asia-b-primary-api.analysis.windows.net/public/reports/querydata"
REPORT_KEY = "6e3b0356-e2b5-478d-84ee-39dd02dc96c3"
MODEL = 1997
PAGE_ROWS = 30000
GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/IRQ/ADM1"
GB_GOVERNORATES = f"{GEOBOUNDARIES}/geoBoundaries-IRQ-ADM1.geojson"
GB_META = f"{GEOBOUNDARIES}/geoBoundaries-IRQ-ADM1-metaData.json"
TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "final": (
        "final_result",
        (
            "id",
            "edno",
            "govno",
            "govname_ar",
            "govname_en",
            "edname",
            "edname_en",
            "seattype",
            "seat",
            "candidatelotterynumber",
            "candidateid",
            "candidatename",
            "candidategender",
            "partyname",
            "party_en",
            "affiliation",
            "votes",
            "winner",
            "iswinning",
            "istied",
        ),
    ),
    "stations": (
        "EDAS",
        (
            "PC_PS",
            "Edno",
            "GOV",
            "ED",
            "PC_NO",
            "PS_NO",
            "PC_Type",
            "ValidBallot",
            "InvalidBallot",
            "Voters",
            "Voters_male",
            "Voters_female",
        ),
    ),
    "centers": ("EDAS", ("PC_PS", "PC_NAME")),
    "cancelled": (
        "CANCELED_PS_STATS_28_11",
        ("PC_PS", "Edno", "PC_NO", "PS_NO", "ValidBallot", "InvalidBallot", "Voters", "reason"),
    ),
    "districts": (
        "1_constituency",
        ("edno", "edname", "GEOCode", "ConstituencyCode", "SeatType", "TotalVoters", "عدد المقاعد"),
    ),
}
"""The tables of the report's data model, by the name each is cached under, with the columns read."""


def query(entity: str, columns: tuple[str, ...], restart: list[list[str]] | None = None) -> dict[str, object]:
    """A query of one table of the report's data model, every row at the grain of the columns, one page at a time."""
    window: dict[str, object] = {"Count": PAGE_ROWS}
    if restart is not None:
        window["RestartTokens"] = restart
    select = [
        {"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": column}, "Name": f"t.{column}"}
        for column in columns
    ]
    command = {
        "Query": {"Version": 2, "From": [{"Name": "t", "Entity": entity, "Type": 0}], "Select": select},
        "Binding": {
            "Primary": {"Groupings": [{"Projections": list(range(len(columns)))}]},
            "DataReduction": {"DataVolume": 4, "Primary": {"Window": window}},
            "Version": 1,
        },
    }
    return {
        "version": "1.0.0",
        "queries": [{"Query": {"Commands": [{"SemanticQueryDataShapeCommand": command}]}}],
        "cancelQueries": [],
        "modelId": MODEL,
    }


def page_url(name: str, page: int) -> str:
    return f"{QUERY}#{name}-{page}"


def _store(name: str, page: int, body: dict[str, object]) -> raw.Artifact:
    try:
        return raw.cached(SOURCE, page_url(name, page))
    except LookupError:
        response = httpx.post(
            QUERY,
            json=body,
            headers={"X-PowerBI-ResourceKey": REPORT_KEY, "User-Agent": raw.USER_AGENT},
            timeout=300,
        )
        response.raise_for_status()
        return raw.store(
            SOURCE,
            page_url(name, page),
            response.content,
            suffix=".json",
            retrieved_at=datetime.now(UTC),
            media_type="application/json",
        )


def fetch() -> None:
    from ea.sources.iq import ihec_2018

    ihec_2018.fetch()
    raw.fetch(SOURCE, HOME, suffix=".html")
    raw.fetch(SOURCE, GB_GOVERNORATES, suffix=".geojson")
    raw.fetch(SOURCE, GB_META, suffix=".json")
    for name, (entity, columns) in TABLES.items():
        restart = None
        page = 0
        while True:
            _, restart = dsr(_store(name, page, query(entity, columns, restart)).path.read_bytes())
            if restart is None:
                break
            page += 1


def pages(name: str) -> list[raw.Artifact]:
    """The cached pages of one table, in order."""
    out: list[raw.Artifact] = []
    while True:
        artifact = raw.cached(SOURCE, page_url(name, len(out)))
        out.append(artifact)
        if dsr(artifact.path.read_bytes())[1] is None:
            return out


def table(name: str) -> list[dict[str, object]]:
    """Every row of one cached table, keyed by column."""
    columns = TABLES[name][1]
    return [dict(zip(columns, row, strict=True)) for a in pages(name) for row in dsr(a.path.read_bytes())[0]]


DAY = date(2021, 10, 10)
PATH = "iq/2021-10-10-council-of-representatives"
VOTES, REGISTERED = 1, 2
GOVERNORATE_SCHEME = "iq.governorate"
DISTRICT_SCHEME = "iq.ihec_district"
CONSTITUENCY_SCHEME = "iq.ihec_constituency"
# The Commission's governorate numbers, with the ISO 3166-2 code, the English name, and the Arabic name. Baghdad
# has two numbers, one for the Rusafa and one for the Karkh side of the city, each with its own districts.
GOVERNORATES: dict[int, tuple[str, str, str]] = {
    1: ("IQ-BG", "Baghdad", "بغداد"),
    23: ("IQ-BG", "Baghdad", "بغداد"),
    4: ("IQ-DA", "Duhok", "دهوك"),
    5: ("IQ-AR", "Erbil", "أربيل"),
    6: ("IQ-SU", "Sulaymaniyah", "السليمانية"),
    12: ("IQ-NI", "Nineveh", "نينوى"),
    14: ("IQ-KI", "Kirkuk", "كركوك"),
    21: ("IQ-DI", "Diyala", "ديالى"),
    22: ("IQ-AN", "Anbar", "الأنبار"),
    24: ("IQ-BB", "Babil", "بابل"),
    25: ("IQ-KA", "Karbala", "كربلاء"),
    26: ("IQ-WA", "Wasit", "واسط"),
    27: ("IQ-SD", "Saladin", "صلاح الدين"),
    28: ("IQ-NA", "Najaf", "النجف"),
    31: ("IQ-QA", "Al-Qadisiyah", "القادسية"),
    32: ("IQ-MU", "Muthanna", "المثنى"),
    33: ("IQ-DQ", "Dhi Qar", "ذي قار"),
    34: ("IQ-MA", "Maysan", "ميسان"),
    35: ("IQ-BA", "Basra", "البصرة"),
}
SIDES = {1: ("Baghdad Rusafa", "بغداد الرصافة"), 23: ("Baghdad Karkh", "بغداد الكرخ")}
# The quota seats: the results table's district number for each seat, the constituency the district table gives it,
# where its votes are cast (None for all of Iraq, else the governorate number), and its English and Arabic names.
QUOTA: dict[str, tuple[str, int | None, str, str]] = {
    "0120": ("0001", None, "Christian seat, Baghdad", "المكون المسيحي - بغداد"),
    "0420": ("0001", None, "Christian seat, Duhok", "المكون المسيحي - دهوك"),
    "0520": ("0001", None, "Christian seat, Erbil", "المكون المسيحي - أربيل"),
    "1220": ("0001", None, "Christian seat, Nineveh", "المكون المسيحي - نينوى"),
    "1420": ("0001", None, "Christian seat, Kirkuk", "المكون المسيحي - كركوك"),
    "0130": ("0002", None, "Sabian Mandaean seat", "المكون الصابئي"),
    "1250": ("1298", 12, "Shabak seat, Nineveh", "مقعد الشبك - نينوى"),
    "1260": ("1299", 12, "Yazidi seat, Nineveh", "مقعد الإيزيدي - نينوى"),
    "2640": ("2600", 26, "Feyli Kurdish seat, Wasit", "المكون الفيلي - واسط"),
}
CONSTITUENCIES = {
    "0001": ("Christian constituency, all of Iraq", None),
    "0002": ("Sabian Mandaean constituency, all of Iraq", None),
    "1298": ("Shabak constituency, Nineveh", 12),
    "1299": ("Yazidi constituency, Nineveh", 12),
    "2600": ("Feyli Kurdish constituency, Wasit", 26),
}
GENDER = {100: "male", 101: "female"}
INDEPENDENT = "مرشحون مستقلون"
METHOD = (
    "Iraq elected the 329 members of its Council of Representatives on 10 October 2021, with special voting for "
    "the security forces, displaced people, and prisoners on 8 October. Under Law 9 of 2020, 320 seats are filled "
    "in 83 multi-member electoral districts, each drawn within one governorate, by single non-transferable vote: "
    "each voter marks one candidate, and the candidates with the most votes win. A quarter of each governorate's "
    "seats are reserved for women; where the women elected fall short of the quota, the woman with the most votes "
    "takes a seat from the last man elected, so the seats a district fills can differ from its allocation when the "
    "quota moves a seat between the districts of a governorate. Nine more seats are reserved for minorities: five "
    "Christian seats, one each for Baghdad, Nineveh, Kirkuk, Duhok, and Erbil, and one Sabian Mandaean seat, each "
    "voted for across Iraq; a Shabak and a Yazidi seat in Nineveh; and a Feyli Kurdish seat in Wasit. Each quota "
    "seat is stored as its own contest, as the Commission's final results list them, and goes to its candidate "
    "with the most votes. The figures are the Commission's final results after the recounts and the rulings of "
    "the Judicial Electoral Panel, as its results report lists them; they hold each candidate's votes by district "
    "and the electors registered in each district, and the report gives no valid or invalid ballots by district, "
    "because votes cast in special and out-of-district stations are counted toward the voter's home district."
)


def arabic(text: str) -> str:
    """An Arabic name with the stretching letter (tatweel) the Commission uses for display removed."""
    return re.sub(r"\s+", " ", text.replace("ـ", "")).strip()


def packages() -> list[str]:
    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        f"{SOURCE}/cor-2018",
        f"{SOURCE}/cor-2021",
        f"{SOURCE}/cor-2021-stations",
    ]


def parse() -> None:
    from ea.sources.iq import ihec_2018

    for build in (geo(), registry(), ihec_2018.election(), election(), stations()):
        build.write()


def emit() -> None:
    package.run("load", packages())


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def governorate_area(iso: str) -> str:
    return key("area", "governorate", iso)


def district_area(edno: str) -> str:
    return key("area", "district", edno)


IHEC = key("source", "ihec")
GEO = key("source", "geoboundaries-adm1")
COUNCIL = key("body", "council-of-representatives")
SYSTEM_DISTRICT = key("electoral_system", "district")
SYSTEM_QUOTA = key("electoral_system", "quota")
STATION_SCHEME = "iq.ihec_station"
BALLOTS, VALID, INVALID = 3, 4, 5
ELECTION = key("election", 2021)
BOUNDARY_FROM = date(2005, 1, 1)


def district_names(edno: str, govno: int) -> tuple[str, str]:
    number = int(edno[2:])
    english, native = SIDES.get(govno, GOVERNORATES[govno][1:])
    return f"{english} {number}", f"{native} {number}"


def regular() -> list[dict[str, object]]:
    """The 83 electoral districts of the district table, checked against the results table."""
    rows = [row for row in table("districts") if row["edno"] not in CONSTITUENCIES]
    if len(rows) != 83 or sum(int(str(row["عدد المقاعد"])) for row in rows) != 320:
        raise ValueError(f"the district table holds {len(rows)} districts")
    for row in rows:
        if str(row["edno"])[:2] != f"{row['GEOCode']:02}":
            raise ValueError(f"district {row['edno']} lies outside governorate {row['GEOCode']}")
    return rows


def _shapes() -> dict[str, shapely.Geometry]:
    found: dict[str, shapely.Geometry] = {}
    for feature in json.loads(raw.cached(SOURCE, GB_GOVERNORATES).path.read_bytes())["features"]:
        shape = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))
        parts = [part for part in shapely.get_parts(shape) if shapely.get_type_id(part) in (3, 6)]
        parts = [piece for part in parts for piece in shapely.get_parts(part)]
        found[feature["properties"]["shapeISO"]] = shapely.set_srid(shapely.MultiPolygon(parts), 4326)
    return found


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Iraq's 18 governorates and the 83 electoral districts and 5 minority constituencies of the 2021 Council of "
        "Representatives election",
    )
    p.external |= {IRAQ, COUNTRY}
    p.add(
        "source",
        id=GEO,
        kind="academic",
        publisher="geoBoundaries (William & Mary geoLab)",
        title="geoBoundaries gbOpen: Iraq ADM1 (governorates)",
        url="https://www.geoboundaries.org/",
        license="CC0 1.0 Universal Public Domain Dedication",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="geoBoundaries",
        notes="Boundary year 2022, built December 2023, from Wikimedia Commons. The eighteen governorates of 2021.",
    )
    p.artifact(raw.cached(SOURCE, GB_GOVERNORATES), GEO)
    p.artifact(raw.cached(SOURCE, GB_META), GEO)
    for scheme, rank, kind, noun, system in (
        (GOVERNORATE_SCHEME, 1, "administrative", "governorate", "iso_3166_2"),
        (DISTRICT_SCHEME, 2, "electoral_district", "electoral district", None),
        (CONSTITUENCY_SCHEME, 2, "electoral_district", "minority constituency", None),
    ):
        p.add("scheme", code=scheme, polity_id=IRAQ, kind=kind, rank=rank, noun=noun, code_system=system)
    p.add(
        "boundary_set",
        id=key("boundary_set", GOVERNORATE_SCHEME, 2021),
        scheme_code=GOVERNORATE_SCHEME,
        vintage="2021",
        valid_from=BOUNDARY_FROM,
        as_of=DAY,
    )
    shapes = _shapes()
    for iso, english, native in sorted(set(GOVERNORATES.values())):
        id = governorate_area(iso)
        shape = shapes[iso]
        p.add("area", id=id, scheme_code=GOVERNORATE_SCHEME)
        p.add("area_code", area_id=id, code=iso)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, f"{english} Governorate", "en", "official")
        p.name(id, f"محافظة {native}", "ar", "native", False)
        p.add(
            "area_geometry",
            area_id=id,
            valid_from=BOUNDARY_FROM,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="academic",
            source_id=GEO,
        )
    for row in regular():
        edno, govno = str(row["edno"]), int(str(row["GEOCode"]))
        id = district_area(edno)
        english, native = district_names(edno, govno)
        p.add("area", id=id, scheme_code=DISTRICT_SCHEME)
        p.add("area_code", area_id=id, code=edno)
        p.add("area_parent", area_id=id, parent_id=governorate_area(GOVERNORATES[govno][0]))
        p.name(id, english, "en", "official")
        p.name(id, native, "ar", "native", False)
    return p


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "Iraq's Council of Representatives and the systems that elect it")
    p.external.add(IRAQ)
    p.add("body", id=COUNCIL, polity_id=IRAQ, kind="unicameral")
    p.name(COUNCIL, "Council of Representatives of Iraq", "en", "official")
    p.name(COUNCIL, "مجلس النواب العراقي", "ar", "native", False)
    for id, rules, text in (
        (
            SYSTEM_DISTRICT,
            {"formula": "sntv"},
            (
                "Each voter marks one candidate in the electoral district, and the candidates with the most votes win "
                "the district's seats (single non-transferable vote, Law 9 of 2020). A quarter of each governorate's "
                "seats are reserved for women: where too few women are elected, the woman with the most votes takes the "
                "seat of the last man elected, which can move a seat between the districts of the governorate."
            ),
        ),
        (
            SYSTEM_QUOTA,
            {"formula": "fptp"},
            "A seat reserved for a minority goes to the candidate for that seat with the most votes.",
        ),
    ):
        p.add("electoral_system", id=id, polity_id=IRAQ, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    return p


class Build:
    """The state shared while the election package's rows are written."""

    def __init__(self, p: Package) -> None:
        self.p = p
        self.values: list[dict[str, object]] = []
        self.parties: set[str] = set()

    def put(self, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
        self.values.append(
            {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "flags": 0,
                "value": value,
            }
        )

    def party(self, text: str) -> str | None:
        name = arabic(text)
        if not name or text == INDEPENDENT:
            return None
        id = key("party", name)
        if id not in self.parties:
            self.parties.add(id)
            kind = "bloc" if name.startswith(("تحالف", "ائتلاف")) else "party"
            self.p.add("party", id=id, kind=kind, polity_id=IRAQ)
            self.p.name(id, name, "ar", "official")
        return id


def election() -> Package:
    p = Package(
        f"{SOURCE}/cor-2021",
        SOURCE,
        "The 2021 Iraqi Council of Representatives election by electoral district, from the Independent High "
        "Electoral Commission's final results",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {IRAQ, COUNTRY}
    b = Build(p)
    p.add(
        "source",
        id=IHEC,
        kind="official_body",
        publisher="Independent High Electoral Commission of Iraq",
        title="Final results of the 2021 Council of Representatives election (results report)",
        url=HOME,
        license="No license stated; published by the electoral authority for public use",
        attribution="Independent High Electoral Commission of Iraq",
    )
    for name in ("final", "districts"):
        for artifact in pages(name):
            p.artifact(artifact, IHEC)
    p.artifact(raw.cached(SOURCE, HOME), IHEC)
    p.add("election", id=ELECTION, polity_id=IRAQ, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=PATH, entity_id=ELECTION, is_current=True)
    p.name(ELECTION, "2021 Iraqi Council of Representatives election", "en", "official")
    p.name(ELECTION, "انتخابات مجلس النواب العراقي 2021", "ar", "native", False)
    p.add("note", id=key("note", ELECTION), entity_id=ELECTION, kind="methodology", body_md=METHOD, lang="en")
    rows = table("final")
    if len({row["candidateid"] for row in rows}) != len(rows):
        raise ValueError("a candidate appears twice in the results table")
    by_district: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_district.setdefault(str(row["edno"]), []).append(row)
    districts = {str(row["edno"]): row for row in table("districts")}
    if set(by_district) != {str(row["edno"]) for row in regular()} | set(QUOTA):
        raise ValueError(f"the results table's districts are {sorted(by_district)}")
    units: dict[str, str] = {}
    for edno, row in districts.items():
        if edno in CONSTITUENCIES:
            english, govno = CONSTITUENCIES[edno]
            area_id = COUNTRY if govno is None else governorate_area(GOVERNORATES[govno][0])
            scheme = CONSTITUENCY_SCHEME
        else:
            english = district_names(edno, int(str(row["GEOCode"])))[0]
            area_id, scheme = district_area(edno), DISTRICT_SCHEME
        unit = units[edno] = key("unit", edno)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=IRAQ,
            scheme_code=scheme,
            code=edno,
            area_relation="equals",
            area_id=area_id,
            kind="aggregate",
        )
        p.name(unit, english, "en", "official")
        p.name(unit, arabic(str(row["edname"])), "ar", "native", False)
    total = 0
    for edno, candidates in sorted(by_district.items()):
        if edno in QUOTA:
            constituency, _, english, native = QUOTA[edno]
            magnitude, system = 1, SYSTEM_QUOTA
            area_id = units_area(constituency)
            unit = units[constituency]
        else:
            constituency = edno
            magnitude, system = int(str(districts[edno]["عدد المقاعد"])), SYSTEM_DISTRICT
            english, native = district_names(edno, int(str(districts[edno]["GEOCode"])))
            area_id, unit = district_area(edno), units[edno]
        contest = key("contest", edno)
        p.add(
            "contest",
            id=contest,
            election_id=ELECTION,
            kind="body_seats",
            body_id=COUNCIL,
            district_area_id=area_id,
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"2021 Council of Representatives election, {english}", "en", "official")
        p.name(contest, native, "ar", "native", False)
        p.add("slug", path=f"{PATH}/{changeset.slug(english)}", entity_id=contest, is_current=True)
        result_set = key("result_set", edno)
        p.add(
            "result_set",
            id=result_set,
            election_id=ELECTION,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=IHEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        b.put(result_set, unit, None, REGISTERED, int(str(districts[constituency]["TotalVoters"])))
        positions = [row["candidatelotterynumber"] for row in candidates]
        unique = len(set(positions)) == len(positions)
        elected = 0
        for row in sorted(candidates, key=lambda row: int(str(row["candidateid"]))):
            number = int(str(row["candidateid"]))
            option = key("option", number)
            name = str(row["candidatename"]).strip()
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=int(str(row["candidatelotterynumber"])) if unique else None,
                ballot_label=name,
            )
            person = key("person", number)
            p.add("person", id=person, gender=GENDER.get(int(str(row["candidategender"]))))
            p.name(person, name, "ar", "official")
            p.add("external_id", entity_id=person, scheme="iq.ihec.candidate", value=str(number))
            party = b.party(str(row["partyname"] or ""))
            candidacy = key("candidacy", number)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=party,
                is_independent=row["partyname"] == INDEPENDENT,
            )
            votes = int(str(row["votes"]))
            total += votes
            b.put(result_set, unit, option, VOTES, votes)
            won = row["winner"] == 1
            elected += won
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if won else "lost",
            )
        if edno in QUOTA and elected != 1:
            raise ValueError(f"quota seat {edno} elects {elected}")
    elected = sum(row["winner"] == 1 for row in rows)
    if elected != 329:
        raise ValueError(f"the results table elects {elected} members")
    print(f"{SOURCE}: {len(rows)} candidates, {total:,} votes, {elected} elected")
    p.frame("result_value", pl.DataFrame(b.values, infer_schema_length=None))
    return p


def units_area(constituency: str) -> str:
    govno = CONSTITUENCIES[constituency][1]
    return COUNTRY if govno is None else governorate_area(GOVERNORATES[govno][0])


def stations() -> Package:
    """The ballots cast at each polling station, as a second result set on each of the 83 district contests.

    A district's default result set keeps the Commission's candidate votes and registered electors, so its contest
    accounting is unchanged. This one holds the valid and invalid ballots of every station the Commission lists under
    the district, kept apart so that the ballots cast in special and out-of-district stations, which the Commission
    counts toward each voter's home district, are never read as that district's own.
    """
    p = Package(
        f"{SOURCE}/cor-2021-stations",
        SOURCE,
        "Ballots cast at each polling station of the 2021 Iraqi Council of Representatives election, from the "
        "Independent High Electoral Commission's results report",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/cor-2021"],
    )
    p.external |= {IRAQ, COUNTRY, IHEC, ELECTION}
    for name in ("stations", "centers"):
        for artifact in pages(name):
            p.artifact(artifact, IHEC)
    p.add("scheme", code=STATION_SCHEME, polity_id=IRAQ, kind="precinct", rank=3, noun="polling station")
    names = {int(str(row["PC_PS"])): arabic(str(row["PC_NAME"] or "")) for row in table("centers")}
    rows: dict[int, dict[str, object]] = {}
    for row in table("stations"):
        station = int(str(row["PC_PS"]))
        if station in rows:
            if rows[station] != row:
                raise ValueError(f"station {station} appears twice with different figures")
            continue
        rows[station] = row
    districts = {str(row["edno"]) for row in regular()}
    by_district: dict[str, list[int]] = {}
    for station, row in sorted(rows.items()):
        if str(row["Edno"]) not in districts:
            raise ValueError(f"station {station} lies in unknown district {row['Edno']}")
        by_district.setdefault(str(row["Edno"]), []).append(station)
    if len(by_district) != 83:
        raise ValueError(f"stations fall in {len(by_district)} districts")
    values: list[dict[str, object]] = []
    for edno, members in sorted(by_district.items()):
        p.external |= {key("contest", edno), key("unit", edno), district_area(edno)}
        result_set = key("result_set", "stations", edno)
        p.add(
            "result_set",
            id=result_set,
            election_id=ELECTION,
            contest_id=key("contest", edno),
            stage="official",
            kind="counted",
            source_id=IHEC,
            status="active",
            is_default=False,
            completeness=1.0,
        )
        for station in members:
            row = rows[station]
            unit = key("unit", "station", station)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=IRAQ,
                scheme_code=STATION_SCHEME,
                code=str(station),
                parent_id=key("unit", edno),
                area_relation="within",
                area_id=district_area(edno),
                kind="precinct",
            )
            p.name(
                unit, f"{names[station]} - {row['PS_NO']}" if names[station] else str(station), "ar", "native", False
            )
            valid, invalid = int(str(row["ValidBallot"])), int(str(row["InvalidBallot"]))
            if int(str(row["Voters"])) != valid + invalid:
                raise ValueError(f"station {station}: ballots do not add up")
            for measure, value in ((BALLOTS, valid + invalid), (VALID, valid), (INVALID, invalid)):
                values.append(
                    {
                        "result_set_id": result_set,
                        "unit_id": unit,
                        "option_id": None,
                        "channel": 0,
                        "measure": measure,
                        "count_round": 0,
                        "flags": 0,
                        "value": value,
                    }
                )
    cast = sum(int(str(v["value"])) for v in values if v["measure"] == BALLOTS)
    print(f"{SOURCE}: {len(rows)} stations, {cast:,} ballots")
    p.frame("result_value", pl.DataFrame(values, infer_schema_length=None))
    return p
