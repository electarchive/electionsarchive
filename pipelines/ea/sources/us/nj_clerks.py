import io
import json
import re
import zipfile
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nj_clerks"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
CLARITY = "https://results.enr.clarityelections.com/NJ/{county}/{election}/{version}/reports/detailxml.zip"
TITLE = "Election night reporting results"
COUNTIES = {
    "34013": (
        "Essex",
        "Essex County Clerk",
        {
            72004: 191383,
            74374: 194144,
            74651: 195083,
            75024: 204515,
            75962: 205358,
            91801: 211120,
            92902: 222724,
            95431: 226995,
            98882: 235761,
            100979: 237203,
            109146: 277021,
            109367: 277032,
            111516: 284996,
            112121: 286451,
            112593: 287522,
            112990: 289499,
            116147: 314208,
            117752: 318100,
            117798: 318323,
            119027: 325088,
            120254: 334306,
            121011: 335515,
            121184: 339247,
            122756: 355330,
            123240: 357033,
            123621: 357908,
            123818: 358609,
            124685: 366658,
            126019: 369109,
            126190: 370574,
            126285: 372379,
            126286: 372380,
            127278: 379486,
        },
    ),
    "34001": (
        "Atlantic",
        "Atlantic County Clerk",
        {
            111509: 285597,
        },
    ),
    "34005": (
        "Burlington",
        "Burlington County Clerk",
        {
            115745: 304047,
        },
    ),
    "34011": (
        "Cumberland",
        "Cumberland County Clerk",
        {
            57844: 157222,
            58625: 157591,
            64715: 183815,
            72006: 191793,
            93016: 222459,
            99212: 235827,
            119033: 324797,
            122757: 355708,
            125167: 367287,
        },
    ),
    "34021": (
        "Mercer",
        "Mercer County Clerk",
        {
            122320: 346005,
            122845: 355689,
        },
    ),
    "34029": (
        "Ocean",
        "Ocean County Clerk",
        {
            49398: 123230,
            50446: 123670,
            53405: 140398,
            54203: 149081,
            57186: 154255,
            57818: 157041,
            58791: 158028,
            59378: 163466,
            63889: 175621,
            64650: 183572,
            72001: 191335,
            72649: 192506,
            92666: 222288,
            93876: 224884,
            94209: 225363,
            98863: 235870,
            99696: 236497,
            106715: 272713,
            111502: 285027,
            112180: 286485,
            116150: 314049,
        },
    ),
    "34031": (
        "Passaic",
        "Passaic County Clerk",
        {
            111517: 285136,
            111875: 285584,
            112430: 288484,
            112986: 289715,
        },
    ),
    "34035": (
        "Somerset",
        "Somerset County Clerk",
        {
            47457: 117708,
            48657: 119515,
            49988: 122782,
            54232: 149079,
            57828: 156959,
            59376: 163294,
            64714: 183691,
            71997: 191273,
            92896: 222712,
            111878: 285578,
            112011: 285767,
            116175: 314304,
            116897: 316360,
        },
    ),
}
SUBDIVISIONS = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_34_cousub_500k.zip"
DISTRICTS = tuple(
    f"https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_34_{kind}_500k.zip" for kind in ("unsd", "scsd")
)
ALIASES = {
    "BLOOMFILED": "BLOOMFIELD",
    "WETS ORANGE": "WEST ORANGE",
    "FAIRFEILD": "FAIRFIELD",
    "N CALDWELL": "NORTH CALDWELL",
    "GREENBROOK": "GREEN BROOK",
    "MONTOMGERY": "MONTGOMERY",
}
DATES = {122845: date(2024, 11, 5)}
SUFFIXES = {"borough": ("BOROUGH", "BORO"), "township": ("TOWNSHIP", "TWP"), "city": ("CITY",), "town": ("TOWN",)}
GENERIC = re.compile(r"(?i)\b(?:regional|school|schools|district|public|consolidated|of|the)\b|[^A-Za-z]")
REGIONAL = {
    r"W[ES]+T ESS?EX": "West Essex Regional School District",
    r"CALDWELL ?[-/]+ ?W(?:EST|\.)? CALDWELL": "Caldwell-West Caldwell School District",
    r"MAPLEWOOD ?[-/]+ ?S(?:OUTH|\.)? ORANGE|SOUTH ORANGE ?[-/]+ ?MAPLEWOOD": "South Orange-Maplewood School District",
}
SKIPPED = re.compile(
    r"COUNTY COMMITTEE|STATE COMMITTEE|STATE PUBLIC QUESTION|COUNTY PUBLIC QUESTION|SENATE|ASSEMBLY|CONGRESS|"
    r"PRESIDENT|GOVERNOR|SHERIFF|SURROGATE|COUNTY CLERK|COUNTY EXECUTIVE|COMMISSIONER|FREEHOLDER|REGISTER|DELEGATE|"
    r"U\.S\.|\((?:DEM|REP)\)"
)
NUMBERS = {"ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5"}
ORDINALS = {"FIRST": "1", "SECOND": "2", "THIRD": "3", "FOURTH": "4", "FIFTH": "5"}
PARTIES = {"DEM": "democratic-party", "REP": "republican-party"}
METHOD = (
    "Votes are the {publisher}'s election night reporting results by election district, summed across the "
    "election day, mail-in, provisional, and emergency ballots; mail-in and provisional ballots the county "
    "counted apart from the districts are kept as their own units. Every candidate's district votes add up to "
    "the county's total."
)
UNTITLED = (
    "The county's results name this question only by its number; the question's text is on the sample ballot, "
    "which the archive does not hold."
)


def url(county: str, election: int, version: int) -> str:
    return CLARITY.format(county=COUNTIES[county][0], election=election, version=version)


def fetch() -> None:
    for county, (_, _, elections) in COUNTIES.items():
        for election, version in elections.items():
            raw.fetch(SOURCE, url(county, election, version), suffix=".zip")
    for link in (SUBDIVISIONS, *DISTRICTS):
        raw.fetch(SOURCE, link, suffix=".zip")


def _shapes(link: str) -> list[tuple[Mapping[str, object], str]]:
    with zipfile.ZipFile(raw.cached(SOURCE, link).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    return [
        (record.as_dict(), json.dumps(shape.__geo_interface__))
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
        if record is not None and shape is not None
    ]


def parse() -> None:
    entries: dict[str, list[tuple[str, tuple[str, str, str]]]] = {}
    shapes = []
    for record, geom in _shapes(SUBDIVISIONS):
        county = str(record["GEOID"])[:5]
        if county not in COUNTIES or record["COUSUBFP"] == "00000":
            continue
        proper, whole = str(record["NAME"]), str(record["NAMELSAD"])
        kind = whole[len(proper) :].strip().lower()
        bare = re.sub(r"^CITY OF | VILLAGE$", "", proper.upper())
        display = f"{proper} {kind.title()}" if kind in ("township", "borough") and "Village" not in proper else proper
        entries.setdefault(county, []).append((bare, (str(record["GEOID"]), display, kind)))
        shapes.append((str(record["GEOID"]), display, geom))
    towns: dict[str, dict[str, tuple[str, str, str]]] = {}
    for county, listed in entries.items():
        counts = {bare: sum(other == bare for other, _ in listed) for bare, _ in listed}
        for bare, entry in listed:
            for key in [bare] if counts[bare] == 1 else [f"{bare} {suffix}" for suffix in SUFFIXES[entry[2]]]:
                towns.setdefault(county, {})[key] = entry
    districts = {}
    for link in DISTRICTS:
        for record, geom in _shapes(link):
            named = str(record["NAME"])
            if "scsd" in link or "Regional" in named or "-" in named:
                districts[named] = (str(record["GEOID"]), named, geom, GENERIC.sub("", named).upper())
    used: set[str] = set()
    contests, lines = [], []
    for county, (_, publisher, elections) in COUNTIES.items():
        names = sorted(towns[county], key=len, reverse=True)

        def town(text: str, names: list[str] = names) -> str | None:
            text = re.sub(r"\s+", " ", text.upper().replace(".", ""))
            for wrong, right in ALIASES.items():
                text = text.replace(wrong, right)
            return next((name for name in names if text.startswith(name)), None)

        for election, version in elections.items():
            with zipfile.ZipFile(raw.cached(SOURCE, url(county, election, version)).path) as archive:
                root = ElementTree.fromstring(archive.read("detail.xml"))
            day = DATES.get(election) or datetime.strptime(root.findtext("ElectionDate") or "", "%m/%d/%Y").date()
            title = root.findtext("ElectionName") or ""
            if "PRIMARY" in title.upper():
                continue
            runoff = re.search(r"(?i)run ?off", title) is not None
            for node in root.iter("Contest"):
                text = re.sub(r"\s+", " ", node.get("text") or "").strip()
                upper = text.upper()
                if SKIPPED.search(upper):
                    continue
                question = re.search(r"QUESTION|PROPOSAL|REFERENDUM", upper) is not None
                school = re.search(r"EDUCATION|SCHOOL| SD\b|REGIONAL", upper) is not None or (
                    question and "SCHOOL" in title.upper()
                )
                if question:
                    key, body = "question", False
                elif school:
                    key, body = "board-of-education", True
                elif re.search(r"\bMAYOR\b", upper):
                    key, body = "mayor", False
                elif "TRUSTEE" in upper:
                    key, body = "board-of-trustees", True
                elif "COMMITTEE" in upper:
                    key, body = "committee", True
                elif "COUNCIL" in upper:
                    key, body = "council", True
                else:
                    continue
                ward = re.search(
                    r"(\d)(?:ST|ND|RD|TH)?\s*WARD|WARD\s*-?\s*(\d)|(NORTH|SOUTH|EAST|WEST|CENTRAL|FIRST|SECOND|THIRD|FOURTH|FIFTH)\s+WARD",
                    re.sub(r"\(|\)", " ", upper),
                )
                seat = None
                if ward and not question and not school:
                    number = ward[1] or ward[2] or ORDINALS.get(ward[3] or "")
                    seat = f"Ward {number}" if number else f"{(ward[3] or '').title()} Ward"
                elif re.search(r"AT[- ]?LARGE", upper) and not question and not school:
                    seat = "At-Large"
                if any(not (choice.get("totalVotes") or "0").isdigit() for choice in node.iter("Choice")):
                    continue
                if question and not all(
                    re.match(r"(?i)\s*(?:yes|no|for|against)\b", choice.get("text") or "")
                    for choice in node.iter("Choice")
                ):
                    continue
                remainder = re.search(r"UNEX|UNXP|SHORT TERM", upper) is not None
                rows = []
                for choice in node.iter("Choice"):
                    if re.match(r"(?i)\s*no (?:nomination|candidate)", choice.get("text") or ""):
                        continue
                    label = re.sub(r"\s+", " ", choice.get("text") or "").strip()
                    for kind in choice.iter("VoteType"):
                        for place in kind.iter("Precinct"):
                            rows.append(
                                (
                                    label,
                                    choice.get("party"),
                                    place.get("name") or "",
                                    int(votes) if (votes := place.get("votes") or "0").isdigit() else 0,
                                )
                            )
                    contests_total = int(choice.get("totalVotes") or 0)
                    lines.append(
                        {
                            "county": county,
                            "election": election,
                            "contest": node.get("key"),
                            "choice": label,
                            "party": choice.get("party"),
                            "place": None,
                            "votes": contests_total,
                        }
                    )
                found = {
                    towns[county][name][0]
                    for _, _, place, votes in rows
                    if (votes or len(rows) < 500) and (name := town(place)) is not None
                }
                if not found:
                    continue
                letters = re.sub(r"[^A-Z]", "", upper)
                regional = next(
                    (name for pattern, name in REGIONAL.items() if school and re.search(pattern, upper)), None
                )
                if regional is None and school and (len(found) > 1 or "REGIONAL" in upper):
                    regional = max(
                        (entry for entry in districts.values() if entry[3] and entry[3] in letters),
                        key=lambda entry: len(entry[3]),
                        default=(None, None),
                    )[1]
                if regional is not None:
                    used.add(regional)
                if len(found) > 1 and regional is None:
                    continue
                for label, party, place, votes in rows:
                    lines.append(
                        {
                            "county": county,
                            "election": election,
                            "contest": node.get("key"),
                            "choice": label,
                            "party": party,
                            "place": re.sub(r"\s+", " ", place.upper()).strip(),
                            "votes": votes,
                        }
                    )
                contests.append(
                    {
                        "county": county,
                        "publisher": publisher,
                        "election": election,
                        "contest": node.get("key"),
                        "day": day,
                        "title": title,
                        "text": text,
                        "key": key,
                        "body": body,
                        "seat": seat,
                        "remainder": remainder,
                        "magnitude": int(node.get("voteFor") or 1),
                        "town": min(found) if len(found) == 1 else None,
                        "regional": regional,
                        "runoff": runoff,
                        "school": school,
                        "variant": None,
                    }
                )
    groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for row in contests:
        groups.setdefault(
            (
                *(row[k] for k in ("county", "election", "town", "regional", "key", "seat", "remainder")),
                row["text"] if row["key"] == "question" else None,
            ),
            [],
        ).append(row)
    for rows in groups.values():
        if len(rows) == 1:
            continue
        terms = [re.search(r"(\d|ONE|TWO|THREE|FOUR|FIVE)[ -]*(?:YR|YEAR)", str(row["text"]).upper()) for row in rows]
        named = [f"{NUMBERS.get(term[1], term[1])}-year term" if term else None for term in terms]
        ambiguous = 0
        for row, term in zip(rows, named, strict=True):
            if term and named.count(term) == 1:
                row["variant"] = term
            else:
                ambiguous += 1
                row["variant"] = f"contest {ambiguous}" if ambiguous > 1 else None
    councils = {(row["county"], row["town"]) for row in contests if row["key"] == "council"}
    for row in contests:
        if row["key"] == "committee" and (row["county"], row["town"]) in councils:
            row["key"] = "council"
    frame = (
        pl.DataFrame(lines, schema_overrides={"votes": pl.Int64, "place": pl.String, "party": pl.String})
        .with_columns(same=pl.col("choice").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", ""))
        .group_by("county", "election", "contest", "same", "place", maintain_order=True)
        .agg(pl.col("choice").first(), pl.col("party").drop_nulls().first(), pl.col("votes").sum())
        .drop("same")
    )
    contests_frame = pl.DataFrame(contests, infer_schema_length=None)
    totals = frame.filter(pl.col("place").is_null()).drop("place")
    counted = (
        frame.filter(pl.col("place").is_not_null())
        .group_by("county", "election", "contest", "choice")
        .agg(pl.col("votes").sum().alias("counted"))
    )
    checked = (
        totals.join(counted, on=["county", "election", "contest", "choice"], how="left")
        .group_by("county", "election", "contest")
        .agg((pl.col("votes") == pl.col("counted")).all().alias("balanced"))
    )
    contests_frame = contests_frame.join(checked, on=["county", "election", "contest"], how="left")
    print(
        f"{contests_frame.height} contests, {contests_frame.filter('balanced').height} with districts that add up; "
        f"{contests_frame.filter(pl.col('key') == 'question').height} questions"
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    contests_frame.write_parquet(PARSED / "contests.parquet")
    frame.join(
        contests_frame.select("county", "election", "contest"), on=["county", "election", "contest"]
    ).write_parquet(PARSED / "lines.parquet")
    pl.DataFrame(shapes, schema=["code", "display", "geom"], orient="row").write_parquet(PARSED / "towns.parquet")
    schools = [(districts[name][0], name, districts[name][2]) for name in sorted(used)]
    pl.DataFrame(schools, schema=["code", "display", "geom"], orient="row").write_parquet(PARSED / "schools.parquet")


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet").sort("day", "election", "contest")
    grouped = {
        keys: rows for keys, rows in pl.read_parquet(PARSED / "lines.parquet").group_by("county", "election", "contest")
    }
    towns = pl.read_parquet(PARSED / "towns.parquet")
    schools = pl.read_parquet(PARSED / "schools.parquet")
    with connect() as conn:
        polities: dict[str, tuple[UUID, str, str]] = {}
        for scheme, code, polity, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.town', 'us.school_district')
                 and left(c.code, 2) = '34'"""
        ).fetchall():
            polities[f"{scheme}:{code}"] = (polity, text, path)
        places = {
            re.sub(r"[^A-Z]", "", text.upper()): key
            for key, (_, text, _) in polities.items()
            if key.startswith("us.place:")
        }
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/nj/%%'").fetchall())
        known = dict(conn.execute("select value, entity_id from external_id where scheme = 'us.contest'").fetchall())
        taken = set(
            conn.execute(
                """select e.date_from, x.value from contest c
                   join election e on e.id = c.election_id
                   join result_set r on r.contest_id = c.id
                   join changeset g on g.id = r.changeset_id
                   join external_id x on x.entity_id = coalesce(c.office_id, c.body_id)
                     and x.scheme in ('us.office', 'us.body')
                   where x.value like 'NJ:%%' and g.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        )
        precincts: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.precinct' and split_part(code, ':', 2) = 'NJ'"
            ).fetchall()
        )
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and left(code, 2) = '34'"
            ).fetchall()
        }
        parents = {
            value: kind
            for value, kind in conn.execute(
                """select x.value, e.kind from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ '^[0-9-]+:NJ$'"""
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Jersey municipal, school board, and local question elections by election district from the county clerks' results",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def title(text: str) -> str:
            text = re.sub(
                r"\b([A-Z][A-Z'’-]+)\b", lambda match: match[1].title() if len(match[1]) > 1 else match[1], text
            )
            return re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text)

        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block", "two_round", "referendum")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp, block, two_round, and referendum systems must already exist")
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for link in (SUBDIVISIONS, *DISTRICTS):
            cs.artifact(raw.cached(SOURCE, link), census)
        sources = {}
        for county, (clarity, publisher, elections) in COUNTIES.items():
            sources[county] = cs.source(publisher, TITLE)
            cs.add(
                "source",
                id=sources[county],
                kind="official_body",
                publisher=publisher,
                title=TITLE,
                url=f"https://results.enr.clarityelections.com/NJ/{clarity}/",
                license="Public record",
            )
            for election, version in elections.items():
                cs.artifact(raw.cached(SOURCE, url(county, election, version)), sources[county])
        state = cs.polity("US-NJ")
        for code, display, geom in towns.iter_rows():
            bare = re.sub(r"[^A-Z]", "", re.sub(r" (?:Township|Borough|City)$", "", display).upper())
            if f"us.town:{code}" in polities or bare in places and places[bare] in polities:
                if bare in places:
                    polities[f"us.town:{code}"] = polities[places[bare]]
                continue
            polity = cs.key("us.polity", f"town:{code}")
            path = f"us/nj/{slug(display)}"
            if path in slugs and slugs[path] != polity:
                path = f"{path}-{slug(polities[f'us.county:{code[:5]}'][1])}"
            if path in slugs and slugs[path] != polity:
                raise ValueError(f"{display} would take {path}")
            slugs[path] = polity
            polities[f"us.town:{code}"] = (polity, display, path)
            if polity not in cs.minted:
                continue
            cs.add("polity", id=polity, kind="municipality", iso_code=None)
            name(polity, display)
            cs.add("polity_parent", polity_id=polity, parent_id=state)
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area("us.town", code)
            cs.add("area", id=area, scheme_code="us.town")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.county", code[:5]))
            name(area, f"{display}, NJ")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=None,
                quality="official",
                source_id=census,
            )
            cs.add(
                "reporting_unit",
                id=cs.unit("us.town", code),
                polity_id=polity,
                scheme_code="us.town",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        regional: dict[str, str] = {}
        for code, display, geom in schools.iter_rows():
            regional[display] = code
            if f"us.school_district:{code}" in polities:
                continue
            polity = cs.key("us.polity", f"school:{code}")
            path = f"us/nj/{slug(display)}"
            if path in slugs and slugs[path] != polity:
                raise ValueError(f"{display} would take {path}")
            slugs[path] = polity
            polities[f"us.school_district:{code}"] = (polity, display, path)
            if polity not in cs.minted:
                continue
            cs.add("polity", id=polity, kind="school_district", iso_code=None)
            name(polity, display)
            cs.add("polity_parent", polity_id=polity, parent_id=state)
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area("us.school_district", code)
            cs.add("area", id=area, scheme_code="us.school_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", "34"))
            name(area, f"{display}, NJ")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=None,
                quality="official",
                source_id=census,
            )
            cs.add(
                "reporting_unit",
                id=cs.unit("us.school_district", code),
                polity_id=polity,
                scheme_code="us.school_district",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )

        def owner_of(row: dict[str, object]) -> tuple[str, str] | None:
            if row["regional"] is not None:
                code = regional.get(str(row["regional"]))
                return None if code is None else ("us.school_district", code)
            code = str(row["town"])
            if f"us.town:{code}" not in polities:
                return None
            polity = polities[f"us.town:{code}"][0]
            match = next(
                (key for key, value in polities.items() if value[0] == polity and key.startswith("us.place:")), None
            )
            return ("us.place", match.split(":", 1)[1]) if match else ("us.town", code)

        runoffs = {
            (row["town"], row["key"], row["seat"], row["day"])
            for row in contests.filter("runoff").iter_rows(named=True)
        }
        twice = {town for town, *_ in runoffs}
        firsts: dict[tuple[object, object, object], tuple[date, str]] = {}
        owners: dict[str, UUID] = {}
        elections: set[UUID] = set()
        persons: dict[tuple[str, str], UUID] = {}
        districts: set[str] = set()
        minted: dict[str, tuple[str, date]] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        values: list[tuple[str, str, str, int]] = []
        for row in contests.iter_rows(named=True):
            found = owner_of(row)
            if found is None:
                continue
            scheme, code = found
            polity, place, path = polities[f"{scheme}:{code}"]
            day, key, seat = row["day"], row["key"], row["seat"]
            question = key == "question"
            if row["regional"] is not None and row["town"] is not None:
                seat = polities[f"us.town:{row['town']}"][1]
            if not question and (day, f"NJ:{code}:{key}") in taken:
                continue
            kind = "general" if day.month == 11 and day.day <= 8 else "runoff" if row["runoff"] else "local"
            parent = cs.key("us.election", f"{day}:NJ")
            if parent in cs.minted and parent not in elections:
                elections.add(parent)
                cs.add(
                    "election",
                    id=parent,
                    polity_id=state,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(
                    parent,
                    f"{day.year} New Jersey " + ("general election" if kind == "general" else f"{kind} elections"),
                )
                cs.add("slug", path=f"us/nj/{day}-{kind}", entity_id=parent)
            elif parent not in cs.minted and f"{day}:NJ" not in parents:
                raise ValueError(f"no election {day}:NJ")
            election = cs.key("us.election", f"{day}:NJ:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} {place} {'runoff' if kind == 'runoff' else kind} election")
                cs.add("slug", path=f"{path}/{day}-{kind}", entity_id=election)
            whole = cs.area(scheme, code)
            area, unit = whole, cs.unit(scheme, code)
            if row["regional"] is not None and row["town"] is not None:
                area = cs.area("us.town", str(row["town"]))
            contest_key = (
                f"{day}:NJ:{code}:{key}"
                + (f":{slug(row['text'])}" if question else "")
                + (f":{slug(seat)}" if seat else "")
                + (":remainder" if row["remainder"] else "")
                + (f":{slug(row['variant'])}" if row["variant"] else "")
            )
            label = row["text"]
            if question and row["school"] and "EDUCATION" not in label.upper() and "SCHOOL" not in label.upper():
                label = f"Board of Education {label}"
            contest_path = (
                f"{path}/{day}-{kind}/"
                + (slug(label)[:60].rstrip("-") if question else key)
                + (f"-{slug(seat)}" if seat else "")
                + ("-unexpired" if row["remainder"] else "")
                + (f"-{slug(row['variant'])}" if row["variant"] else "")
            )
            contest = cs.key("us.contest", contest_key)
            if contest_path in slugs and slugs[contest_path] not in (contest, known.get(contest_key)):
                continue
            slugs[contest_path] = contest
            if question:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    district_area_id=area,
                    electoral_system_id=systems["referendum"],
                    round_number=1,
                    default_result_kind="counted",
                )
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
                name(contest, f"{day.year} {place}: {label}")
                if re.fullmatch(r"(?i)(?:board of education )?(?:public )?question(?: number| no\.?)? ?\d+", label):
                    cs.add("note", entity_id=contest, kind="caveat", body_md=UNTITLED)
            else:
                body = row["body"]
                display = place.removeprefix("City of ")
                office_title = {
                    "mayor": f"Mayor of {display}",
                    "council": f"{display} Council",
                    "committee": f"{display} Committee",
                    "board-of-trustees": f"{display} Board of Trustees",
                    "board-of-education": f"{display} Board of Education"
                    if scheme != "us.school_district"
                    else place.replace("School District", "Board of Education"),
                }[key]
                if f"{code}:{key}" not in owners:
                    owner = owners[f"{code}:{key}"] = cs.key("us.body" if body else "us.office", f"NJ:{code}:{key}")
                    if owner in cs.minted:
                        if body:
                            cs.add("body", id=owner, polity_id=polity, kind="council")
                        else:
                            cs.add("office", id=owner, polity_id=polity, kind="mayor")
                        name(owner, office_title)
                if (
                    seat
                    and seat.startswith(("Ward", "North", "South", "East", "West", "Central"))
                    and row["regional"] is None
                ):
                    district = f"34:{code}:{key}:{slug(seat)}"
                    area, unit = cs.area("us.local_district", district), cs.unit("us.local_district", district)
                    if area in cs.minted and district not in districts:
                        districts.add(district)
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=district, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                        name(area, f"{office_title}, {seat}")
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polity,
                            scheme_code="us.local_district",
                            code=district,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                        )
                magnitude = row["magnitude"]
                previous = firsts.get((row["town"], key, seat)) if row["runoff"] else None
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if body else "office",
                    office_id=None if body else owners[f"{code}:{key}"],
                    body_id=owners[f"{code}:{key}"] if body else None,
                    district_area_id=area,
                    magnitude=magnitude,
                    round_number=2 if previous else 1,
                    previous_round_id=cs.key("us.contest", previous[1]) if previous else None,
                    electoral_system_id=systems[
                        "two_round"
                        if row["town"] in twice and key in ("mayor", "council")
                        else "fptp"
                        if magnitude == 1
                        else "block"
                    ],
                    default_result_kind="counted",
                    term="remainder" if row["remainder"] else "full",
                )
                name(
                    contest,
                    (
                        f"{day.year} {display} mayoral election"
                        if key == "mayor"
                        else f"{day.year} {office_title} election"
                    )
                    + (f", {seat}" if seat else "")
                    + (" (unexpired term)" if row["remainder"] else "")
                    + (f" ({row['variant']})" if row["variant"] else "")
                    + (" runoff" if previous else ""),
                )
                if not row["runoff"]:
                    firsts[(row["town"], key, seat)] = (day, contest_key)
            cs.add("slug", path=contest_path, entity_id=contest)
            counted = grouped[(row["county"], row["election"], row["contest"])]
            balanced = bool(row["balanced"])
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified" if day < date(2026, 9, 1) else "preliminary",
                kind="counted",
                status="active",
                source_id=sources[row["county"]],
                is_default=True,
                completeness=1,
            )
            if balanced:
                cs.add(
                    "note", entity_id=result_set, kind="methodology", body_md=METHOD.format(publisher=row["publisher"])
                )
            totals = counted.filter(pl.col("place").is_null()).sort("votes", "choice", descending=[True, False])
            options: dict[str, UUID] = {}
            ranked: list[tuple[int, UUID]] = []
            for position, (choice, party, votes) in enumerate(totals.select("choice", "party", "votes").iter_rows()):
                if question:
                    answer = "yes" if re.match(r"(?i)\s*(?:yes|for)\b", choice) else "no"
                    option = options[choice] = cs.key("us.option", f"{contest_key}:{answer}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=1 if answer == "yes" else 2,
                        ballot_label=answer.capitalize(),
                    )
                elif re.match(r"(?i)write|personal choice", choice):
                    option = options[choice] = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                    continue
                else:
                    ballot = title(choice)
                    person_key = (code, slug(ballot))
                    if person_key not in persons:
                        persons[person_key] = cs.key(f"{SOURCE}.person", f"NJ:{code}:{slug(ballot)}")
                        if persons[person_key] in cs.minted:
                            cs.add("person", id=persons[person_key])
                            name(persons[person_key], ballot)
                    option = options[choice] = cs.key("us.option", f"{contest_key}:{slug(ballot)}")
                    party_key = PARTIES.get(party or "")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", party_key) if party_key else None,
                        ballot_label=ballot,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(ballot)}:head"),
                        person_id=persons[person_key],
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=party == "IND",
                    )
                ranked.append((votes, option))
            if result_set not in cs.minted:
                continue
            if question:
                if len(ranked) == 2 and ranked[0][0] != ranked[1][0]:
                    cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="won")
            else:
                magnitude = row["magnitude"]
                advancing = not row["runoff"] and any(
                    town == row["town"] and k == key and s == seat and day < d <= day + timedelta(days=60)
                    for town, k, s, d in runoffs
                )
                cut = 2 if advancing else magnitude
                if len(ranked) <= cut or ranked[cut - 1][0] != ranked[cut][0]:
                    for _, option in ranked[:cut]:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="advanced" if advancing else "elected",
                            seats=None if advancing else 1,
                        )
            if not balanced:
                for choice, votes in totals.select("choice", "votes").iter_rows():
                    values.append((str(result_set), str(unit), str(options[choice]), votes))
                continue
            jurisdiction = COUNTIES[row["county"]][0].upper()
            for choice, place_name, votes in (
                counted.filter(pl.col("place").is_not_null())
                .group_by("choice", "place")
                .agg(pl.col("votes").sum())
                .select("choice", "place", "votes")
                .iter_rows()
            ):
                place_code = f"{day.year}:NJ:{row['county']}:{jurisdiction}:{place_name}"
                target = precincts.get(place_code) or cs.unit("us.precinct", place_code)
                if place_code not in precincts:
                    minted.setdefault(place_code, (row["county"], day))
                if area != counties[row["county"]][1]:
                    memberships.add((target, area, day))
                values.append((str(result_set), str(target), str(options[choice]), votes))
        for place_code, (county, day) in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", place_code),
                polity_id=state,
                scheme_code="us.precinct",
                code=place_code,
                parent_id=counties[county][0],
                area_relation="within",
                area_id=counties[county][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        for target, area, day in memberships:
            cs.add(
                "reporting_unit_area", unit_id=target, area_id=area, valid_from=day, valid_to=day + timedelta(days=1)
            )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
