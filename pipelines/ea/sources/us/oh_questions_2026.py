import json
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda
from ea.store import DATA, connect

SOURCE = "us.oh_questions_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
FILE = DATA / "inbox" / "manual" / "MF-367" / "OH" / "2026-11-general-qandi.xlsx"
URL = "https://www.ohiosos.gov/elections/elections-calendar-and-results/"
PUBLISHER = "Ohio Secretary of State"
TITLE = "November 3, 2026 General Election: questions and issues"
PLACES = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_place_national.zip"
COUSUBS = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_cousubs_national.zip"
DIRECTORY = "https://educationdata.urban.org/api/v1/school-districts/ccd/directory/2022/?fips=39"
SCHEMES = {
    "Township": "us.town",
    "Village": "us.place",
    "City": "us.place",
    "County": "us.county",
    "School District": "us.school_district",
}


LIQUOR = "Miscellaneous, local liquor option"
ADDED: list[dict[str, str]] = [
    {
        "county": "Hamilton",
        "kind": "City",
        "name": "Cheviot",
        "label": "Current expenses",
        "text": "Tax levy, renewal: 3 mills, 5 years, beginning 2026. Purpose: Current expenses.",
    },
    {
        "county": "Stark",
        "kind": "County",
        "name": "Stark",
        "label": "Current expenses (Stark County District Library)",
        "text": "Tax levy, renewal and increase: 2.3 mills, 8 years, beginning 2027. Purpose: Current expenses (Stark County District Library).",
    },
    {
        "county": "Hamilton",
        "kind": "City",
        "name": "Cincinnati",
        "label": "Liquor permit D6, Sunday sale, Cincinnati precinct 9-D",
        "text": f"{LIQUOR}: Sunday sale at a grocery store. Purpose: Liquor permit D6, Sunday sale, Cincinnati precinct 9-D.",
    },
    {
        "county": "Hamilton",
        "kind": "Village",
        "name": "Terrace Park",
        "label": "Liquor permit D6, Sunday sale, Terrace Park precinct A",
        "text": f"{LIQUOR}: Sunday sale at a bar and restaurant. Purpose: Liquor permit D6, Sunday sale, Terrace Park precinct A.",
    },
    {
        "county": "Hamilton",
        "kind": "Township",
        "name": "Springfield",
        "label": "Liquor permit D6, Sunday sale, Springfield Township precinct C",
        "text": f"{LIQUOR}: Sunday sale at a bar and restaurant. Purpose: Liquor permit D6, Sunday sale, Springfield Township precinct C.",
    },
    {
        "county": "Summit",
        "kind": "City",
        "name": "Akron",
        "label": "Sunday sale of wine and mixed beverages, Akron precinct 4-F",
        "text": f"{LIQUOR}: Sunday sale for consumption off the premises. Purpose: Sunday sale of wine and mixed beverages, Akron precinct 4-F.",
    },
    {
        "county": "Summit",
        "kind": "City",
        "name": "Akron",
        "label": "Liquor permit C2, Akron precinct 6-C",
        "text": f"{LIQUOR}: permit C2 at a convenience store. Purpose: Liquor permit C2, Akron precinct 6-C.",
    },
    {
        "county": "Summit",
        "kind": "City",
        "name": "Akron",
        "label": "Liquor permit D6, Sunday sale, Akron precinct 6-C",
        "text": f"{LIQUOR}: Sunday sale at a convenience store. Purpose: Liquor permit D6, Sunday sale, Akron precinct 6-C.",
    },
    {
        "county": "Summit",
        "kind": "City",
        "name": "Cuyahoga Falls",
        "label": "Liquor permit D6, Sunday sale, Cuyahoga Falls precinct 4-C",
        "text": f"{LIQUOR}: Sunday sale at a bar. Purpose: Liquor permit D6, Sunday sale, Cuyahoga Falls precinct 4-C.",
    },
    {
        "county": "Franklin",
        "kind": "School District",
        "name": "South-Western City School District",
        "label": "School construction",
        "text": "Bond Issue: $220000000, 38 years, beginning 2026. Purpose: School construction.",
    },
    {
        "county": "Franklin",
        "kind": "Township",
        "name": "Hamilton",
        "label": "Road maintenance",
        "text": "Tax Levy, additional: 0.8 mills, continuing, beginning 2026. Purpose: Road maintenance.",
    },
    {
        "county": "Franklin",
        "kind": "Township",
        "name": "Sharon",
        "label": "Road maintenance",
        "text": "Tax Levy, additional: 2.46 mills, continuing, beginning 2026. Purpose: Road maintenance.",
    },
    {
        "county": "Franklin",
        "kind": "City",
        "name": "Columbus",
        "label": "Weekday sales of beer, wine, and mixed beverages, Columbus precinct 07-D, Neubite",
        "text": f"{LIQUOR}: Weekday sales of beer, wine, and mixed beverages. Purpose: Neubite, L.L.C. (dba Aveneu), 1017 Mt. Vernon Avenue, Columbus precinct 07-D.",
    },
    {
        "county": "Franklin",
        "kind": "City",
        "name": "Columbus",
        "label": "Sunday sales of wine and mixed beverages, Columbus precinct 07-D, Neubite",
        "text": f"{LIQUOR}: Sunday sales of wine and mixed beverages. Purpose: Neubite, L.L.C. (dba Aveneu), 1017 Mt. Vernon Avenue, Columbus precinct 07-D.",
    },
    {
        "county": "Franklin",
        "kind": "City",
        "name": "Columbus",
        "label": "Sunday sales of wine and mixed beverages, Columbus precinct 10-A, Dublin & Grandview Duchess",
        "text": f"{LIQUOR}: Sunday sales of wine and mixed beverages. Purpose: Dublin & Grandview Duchess, 1090 Dublin Road, Columbus precinct 10-A.",
    },
    {
        "county": "Franklin",
        "kind": "City",
        "name": "Columbus",
        "label": "Sunday sales of wine, mixed beverages, and spirituous liquor, Columbus precinct 10-D, Born on Third",
        "text": f"{LIQUOR}: Sunday sales of wine, mixed beverages, and spirituous liquor. Purpose: Born on Third, 3411 Trabue Rd, Columbus precinct 10-D.",
    },
    {
        "county": "Franklin",
        "kind": "City",
        "name": "Columbus",
        "label": "Sunday sales of wine and mixed beverages, Columbus precinct 22-D, Englefield",
        "text": f"{LIQUOR}: Sunday sales of wine and mixed beverages. Purpose: Englefield, Inc., dba Morse Rd. Duchess, 927 Morse Road, Columbus precinct 22-D.",
    },
    {
        "county": "Franklin",
        "kind": "City",
        "name": "Columbus",
        "label": "Sunday sales of wine, mixed beverages, and spirituous liquor, Columbus precinct 32-A, Third Way Café LLC",
        "text": f"{LIQUOR}: Sunday sales of wine, mixed beverages, and spirituous liquor. Purpose: Third Way Café LLC, 3059 W. Broad Street, Columbus precinct 32-A.",
    },
    {
        "county": "Franklin",
        "kind": "City",
        "name": "Columbus",
        "label": "Sunday sales of wine and mixed beverages, Columbus precinct 49-F, RaceTrac",
        "text": f"{LIQUOR}: Sunday sales of wine and mixed beverages. Purpose: RaceTrac, Inc., 30 Rathmell Road, Columbus precinct 49-F.",
    },
    {
        "county": "Franklin",
        "kind": "City",
        "name": "Columbus",
        "label": "Weekday sales of beer, wine, and mixed beverages, Columbus precinct 55-D, Englefield",
        "text": f"{LIQUOR}: Weekday sales of beer, wine, and mixed beverages. Purpose: Englefield, Inc., dba E. Broad St. Duchess, 825 E. Broad Street, Columbus precinct 55-D.",
    },
    {
        "county": "Franklin",
        "kind": "City",
        "name": "Columbus",
        "label": "Sunday sales of wine and mixed beverages, Columbus precinct 55-D, Englefield",
        "text": f"{LIQUOR}: Sunday sales of wine and mixed beverages. Purpose: Englefield, Inc., dba E. Broad St. Duchess, 825 E. Broad Street, Columbus precinct 55-D.",
    },
    {
        "county": "Franklin",
        "kind": "City",
        "name": "Columbus",
        "label": "Sunday sales of wine and mixed beverages, Columbus precinct 68-A, Convenient Plus Food Mart",
        "text": f"{LIQUOR}: Sunday sales of wine and mixed beverages. Purpose: Convenient Plus Food Mart, 5435 E. Livingston Avenue, Columbus precinct 68-A.",
    },
    {
        "county": "Franklin",
        "kind": "Township",
        "name": "Madison",
        "label": "Weekday sales of wine and mixed beverages, Madison Township precinct A, RaceTrac",
        "text": f"{LIQUOR}: Weekday sales of wine and mixed beverages. Purpose: RaceTrac, 2555 London Groveport Road, Madison Township precinct A.",
    },
    {
        "county": "Franklin",
        "kind": "Township",
        "name": "Madison",
        "label": "Sunday sales of wine and mixed beverages, Madison Township precinct A, RaceTrac",
        "text": f"{LIQUOR}: Sunday sales of wine and mixed beverages. Purpose: RaceTrac, 2555 London Groveport Road, Madison Township precinct A.",
    },
    {
        "county": "Franklin",
        "kind": "Township",
        "name": "Plain",
        "label": "Weekday sales of beer, wine, mixed beverages, and spirituous liquor, Plain Township precinct A, The Golf Club Company",
        "text": f"{LIQUOR}: Weekday sales of beer, wine, mixed beverages, and spirituous liquor. Purpose: The Golf Club Company, 4522 Kitzmiller Road, Plain Township precinct A.",
    },
    {
        "county": "Franklin",
        "kind": "Township",
        "name": "Plain",
        "label": "Sunday sales of wine, mixed beverages, and spirituous liquor, Plain Township precinct A, The Golf Club Company",
        "text": f"{LIQUOR}: Sunday sales of wine, mixed beverages, and spirituous liquor. Purpose: The Golf Club Company, 4522 Kitzmiller Road, Plain Township precinct A.",
    },
]


CUYAHOGA_LIQUOR: list[tuple[str, str, str, str]] = [
    (
        "City",
        "Cleveland",
        "Sunday sales of wine and mixed beverages, 2976, Inc., Chillie's Beverage & Deli",
        "Sunday sales of wine and mixed beverages at a carryout and grocery store. Purpose: 2976, Inc., dba Chillie's Beverage & Deli, 2976 East 116th Street, Cleveland.",
    ),
    (
        "City",
        "Cleveland",
        "Sunday sales of wine and mixed beverages, Aarjun Krupa, Old Brooklyn Food Mart",
        "Sunday sales of wine and mixed beverages at a carryout and grocery store. Purpose: Aarjun Krupa, L.L.C., dba Old Brooklyn Food Mart, 4785 Pearl Road, Cleveland.",
    ),
    (
        "City",
        "Cleveland",
        "Sunday sales of beer, Haba Inc, Sunoco One Stop",
        "Sunday sales of beer at a convenience store and gas station. Purpose: Haba Inc, dba Sunoco One Stop, 4402 Lorain Avenue, Cleveland.",
    ),
    (
        "City",
        "Cleveland",
        "Sunday sales of wine and mixed beverages, Sadguru 1 Krupa, Memphis Mart",
        "Sunday sales of wine and mixed beverages at a carryout and grocery store. Purpose: Sadguru 1 Krupa, L.L.C., dba Memphis Mart, 6226 Memphis Avenue, Cleveland.",
    ),
    (
        "City",
        "Cleveland",
        "Sunday sales of wine and mixed beverages, Jaspreet, Inc., Convenient Food Mart",
        "Sunday sales of wine and mixed beverages at a carryout and grocery store. Purpose: Jaspreet, Inc., dba Convenient Food Mart, 12520 Lorain Avenue, Cleveland.",
    ),
    (
        "City",
        "Cleveland",
        "Sunday sales of wine and mixed beverages, Shivay CFM, Convenient Food Mart",
        "Sunday sales of wine and mixed beverages at a carryout and grocery store. Purpose: Shivay CFM, L.L.C., dba Convenient Food Mart, 3753 Pearl Road, Cleveland.",
    ),
    (
        "City",
        "Parma Heights",
        "Sunday sales of wine, mixed beverages, and spirituous liquor, Matador Parma, Santo Matador Street Tacos",
        "Sunday sales of wine, mixed beverages, and spirituous liquor at a restaurant. Purpose: Matador Parma LLC, dba Santo Matador Street Tacos, 6699 West 130th Street, Parma Heights.",
    ),
    (
        "City",
        "Parma Heights",
        "Sunday sales of wine and mixed beverages, 12215 Gas LLC, Heights Bar and Grill",
        "Sunday sales of wine and mixed beverages at a convenience store and gas station. Purpose: 12215 Gas LLC, dba Heights Bar and Grill, 12215 Huffman Road, Parma Heights.",
    ),
    (
        "City",
        "South Euclid",
        "Spirituous liquor, VMPR, Green Luxury Loft",
        "Weekday sales of spirituous liquor at a wedding and event venue, D3 and D5 permits. Purpose: VMPR, L.L.C., dba Green Luxury Loft, 800 South Green Road, South Euclid.",
    ),
    (
        "City",
        "South Euclid",
        "Sunday sales of wine, mixed beverages, and spirituous liquor, VMPR, Green Luxury Loft",
        "Sunday sales of wine, mixed beverages, and spirituous liquor at a wedding and event venue, D-6 permit. Purpose: VMPR, L.L.C., dba Green Luxury Loft, 800 South Green Road, South Euclid.",
    ),
    (
        "City",
        "Bay Village",
        "Sunday sales of beer, wine, and mixed beverages, BAYarts",
        "Sunday sales of beer, wine, and mixed beverages at a nonprofit arts organization. Purpose: BAYarts, 28601 and 28795 Lake Road, Bay Village.",
    ),
    (
        "City",
        "Maple Heights",
        "Sunday sales of wine, mixed beverages, and spirituous liquor, Dunham Inn",
        "Sunday sales of wine, mixed beverages, and spirituous liquor at a bar. Purpose: Dunham Inn, L.L.C., 6140 Dunham Road, Maple Heights.",
    ),
    (
        "City",
        "Shaker Heights",
        "Sunday sales of wine and mixed beverages, Shivay 22, Shaker Smoke & Beverage",
        "Sunday sales of wine and mixed beverages at a carryout and grocery store. Purpose: Shivay 22, L.L.C., dba Shaker Smoke & Beverage, 3753 Lee Road, Shaker Heights.",
    ),
    (
        "City",
        "University Heights",
        "Sunday sales of wine and mixed beverages, Trader Joe's East",
        "Sunday sales of wine and mixed beverages at a grocery store. Purpose: Trader Joe's East Inc., dba Trader Joe's, 2643 Warrensville Center Road, University Heights.",
    ),
]
ADDED += [
    {
        "county": "Cuyahoga",
        "kind": kind,
        "name": name,
        "label": label,
        "text": f"{LIQUOR}: {text}",
    }
    for kind, name, label, text in CUYAHOGA_LIQUOR
]
ADDED += [
    {
        "county": "Cuyahoga",
        "kind": "Village",
        "name": "Oakwood",
        "geoid": "3957750",
        "label": f"Charter Amendment: Section {section}",
        "text": f"Miscellaneous. Purpose: Charter Amendment: Section {section}.",
    }
    for section in ("6.06", "6.07", "7.01", "7.03", "7.04", "7.07", "7.08", "7.14", "10.11", "13.07", "13.08")
]
ADDED.append(
    {
        "county": "Cuyahoga",
        "kind": "City",
        "name": "Olmsted Falls",
        "label": "Tax on income, increase to 2 percent",
        "text": "Income tax, increase: 2 percent, beginning 2027. Purpose: General municipal operations, new equipment, extension and enlargement of municipal services and facilities, and capital improvements.",
    }
)


GEOIDS = {("Montgomery", "City", "Oakwood"): "3957764"}

WORDING: dict[str, str] = json.loads(Path(__file__).with_name("oh_franklin_wording.json").read_text())


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(
            SOURCE,
            URL,
            FILE.read_bytes(),
            suffix=".xlsx",
            retrieved_at=datetime.fromtimestamp(FILE.stat().st_mtime, UTC),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    for url, suffix in ((PLACES, ".zip"), (COUSUBS, ".zip"), (DIRECTORY, ".json")):
        raw.fetch(SOURCE, url, suffix=suffix)


def gazetteer(url: str) -> pl.DataFrame:
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        frame = pl.read_csv(archive.read(archive.namelist()[0]), separator="\t", infer_schema=False)
    return frame.rename(lambda column: column.strip()).filter(pl.col("USPS") == "OH")


def clean(value: object) -> str | None:
    text = re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""
    return None if text in ("", "N/A", "None") else text


def describe(row: dict[str, object]) -> str:
    kind = clean(row["Question Type"]) or "Question"
    change = clean(row["Description"])
    head = f"{kind}, {change.lower()}" if change else kind
    details = []
    if (mills := clean(row["Millage"])) is not None:
        details.append(f"{mills} mills")
    if (percent := clean(row["Percent"])) is not None:
        details.append(f"{float(percent) * 100:g} percent")
    if (amount := clean(row["Dollar Amount"])) is not None:
        details.append(f"${amount}")
    if (years := clean(row["Length of Levy in Years or Continuing Period of Time (cpt)"])) is not None:
        details.append("continuing" if years.lower() == "cpt" else f"{years} years")
    if (start := clean(row["Commencing Year or Effective Date"])) is not None:
        details.append(f"beginning {start[:4]}")
    text = head + (": " + ", ".join(details) if details else "")
    purpose = clean(row["Purpose"])
    return f"{text}. Purpose: {purpose}." if purpose else f"{text}."


def parse() -> None:
    frame = pl.read_excel(raw.cached(SOURCE, URL).path, sheet_name="Issues Summary", infer_schema_length=0)
    rows = []
    for row in frame.iter_rows(named=True):
        kind = clean(row["Subdivision Type"])
        name = clean(row["Subdivision Name"])
        if kind not in SCHEMES or name is None or clean(row["County"]) is None:
            continue
        label = clean(row["Purpose"]) or clean(row["Question Type"]) or "Question"
        rows.append(
            {
                "county": clean(row["County"]),
                "kind": kind,
                "name": name,
                "text": WORDING.get(f"{name}|{label}") or describe(row),
                "label": label,
                "geoid": GEOIDS.get((clean(row["County"]), kind, name), ""),
            }
        )
    rows += [{"geoid": "", **row} for row in ADDED]
    PARSED.mkdir(parents=True, exist_ok=True)
    out = pl.DataFrame(rows)
    out.write_parquet(PARSED / "questions.parquet")
    print(out.group_by("kind").len().sort("kind").rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "questions.parquet")
    with connect() as conn:
        counties: dict[str, str] = {}
        towns: dict[tuple[str, str], str] = {}
        places: dict[str, list[str]] = {}
        schools: dict[str, list[str]] = {}
        found: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        place_geoids = set(gazetteer(PLACES)["GEOID"])
        for scheme, code, polity, area, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, a.id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.town', 'us.school_district')
                 and left(c.code, 2) = '39'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (
                polity,
                area,
                text,
                path,
                f"{code}-sd" if scheme == "us.school_district" and code in place_geoids else code,
            )
            if scheme == "us.county":
                counties[ceda._core(text)] = code
            elif scheme == "us.town":
                towns[(code[:5], ceda._core(text))] = key
            elif scheme == "us.place":
                places.setdefault(ceda._city(text), []).append(key)
            else:
                schools.setdefault(ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/oh/%%'").fetchall())
        keyed = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value like %s",
                [f"{DAY}:OH:%"],
            ).fetchall()
        )
        candidates: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        for geoid, full in gazetteer(PLACES).select("GEOID", "NAME").iter_rows():
            label = re.sub(r" (city|village|CDP)$", "", full)
            candidates.setdefault(("place", ceda._city(label)), []).append((f"place:{geoid}", geoid, label))
        for geoid, full in gazetteer(COUSUBS).select("GEOID", "NAME").iter_rows():
            if full.endswith("township"):
                candidates.setdefault(("town", geoid[:5] + ceda._core(full)), []).append(
                    (f"town:{geoid}", geoid, full.title())
                )
        school_county: dict[str, str] = {}
        for entry in json.loads(raw.cached(SOURCE, DIRECTORY).path.read_text())["results"]:
            school_county[entry["leaid"]] = str(entry["county_code"])
            candidates.setdefault(("school", ceda._core(entry["lea_name"])), []).append(
                (f"school:{entry['leaid']}", entry["leaid"], entry["lea_name"])
            )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio township, village, city, county, and school district questions on the November 3, 2026 ballot, from the Secretary of State's issue summaries",
            polity="US-OH",
        )
        system = cs.key("us.electoral_system", "referendum")
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL)
        cs.artifact(raw.cached(SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:OH")
        elections: set[UUID] = set()
        counts: dict[str, int] = {}
        kept = 0
        taken = {path for path in slugs}
        ohio = cs.polity("US-OH")
        state_area = cs.area("us.state", "39")
        for row in frame.sort("kind", "county", "name", "label").iter_rows(named=True):
            name = row["name"]
            county = counties.get(ceda._core(f"{row['county']} County"))
            spec: list[tuple[str, str, str]] = []
            scheme = ""
            if row["kind"] == "County":
                key = f"us.county:{county}" if county is not None else None
            elif row["kind"] == "Township":
                bare = name if "Township" in name else f"{name} Township"
                key = towns.get((county, ceda._core(bare))) if county else None
                spec = candidates.get(("town", (county or "") + ceda._core(bare)), [])
                scheme = "us.town"
            elif row["kind"] == "School District":
                options = schools.get(ceda._core(name), [])
                if len(options) > 1:
                    options = [o for o in options if school_county.get(o.split(":")[1]) == county]
                key = options[0] if len(options) == 1 else None
                spec = candidates.get(("school", ceda._core(name)), [])
                if len(spec) > 1:
                    spec = [s for s in spec if school_county.get(s[1]) == county]
                if len(spec) > 1:
                    spec = [s for s in spec if s[2].lower() == name.lower().removesuffix(" school district")] or spec
                scheme = "us.school_district"
            else:
                options = places.get(ceda._city(name), [])
                spec = candidates.get(("place", ceda._city(name)), [])
                if row["geoid"]:
                    options = [o for o in options if o == f"us.place:{row['geoid']}"]
                    spec = [s for s in spec if s[1] == row["geoid"]]
                key = options[0] if len(options) == 1 else None
                scheme = "us.place"
            if key is None and len(spec) == 1:
                owner, geoid, text = spec[0]
                key = f"{scheme}:{geoid}"
                if key not in found:
                    polity_id = cs.key("us.polity", owner)
                    path = f"us/oh/{slug(text)}"
                    if path in taken:
                        path = f"{path}-{slug(row['county'])}-county" if scheme == "us.town" else f"{path}-city"
                    if path in taken:
                        print("slug taken:", path)
                        continue
                    taken.add(path)
                    area_id = cs.area(scheme, geoid)
                    if polity_id in cs.minted:
                        cs.add(
                            "polity",
                            id=polity_id,
                            kind="school_district" if scheme == "us.school_district" else "municipality",
                            iso_code=None,
                        )
                        cs.add("name", entity_id=polity_id, text=text, lang="en", kind="official", is_preferred=True)
                        cs.add("polity_parent", polity_id=polity_id, parent_id=ohio)
                        cs.add("slug", path=path, entity_id=polity_id)
                    if area_id in cs.minted:
                        cs.add("area", id=area_id, scheme_code=scheme)
                        cs.add("area_code", area_id=area_id, code=geoid, valid_from=None)
                        cs.add(
                            "area_parent",
                            area_id=area_id,
                            parent_id=cs.area("us.county", geoid[:5]) if scheme == "us.town" else state_area,
                        )
                        cs.add(
                            "name", entity_id=area_id, text=f"{text}, OH", lang="en", kind="official", is_preferred=True
                        )
                    if polity_id in cs.minted:
                        cs.add("polity_territory", polity_id=polity_id, area_id=area_id)
                        cs.add(
                            "reporting_unit",
                            id=cs.unit(scheme, geoid),
                            polity_id=polity_id,
                            scheme_code=scheme,
                            code=geoid,
                            area_relation="equals",
                            area_id=area_id,
                            kind="standard",
                            valid_from=None,
                        )
                    keycode = f"{geoid}-sd" if scheme == "us.school_district" and geoid in place_geoids else geoid
                    found[key] = (polity_id, area_id, text, path, keycode)
            if key is None or key not in found:
                print("no polity:", row["kind"], row["county"], name)
                continue
            kept += 1
            polity, area, place, path, code = found[key]
            election = cs.key("us.election", f"{DAY}:OH:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="scheduled",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{DAY.year} {place} general election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            label = row["label"]
            counts[key] = counts.get(key, 0) + 1
            stem = slug(label)
            contest_key = f"{DAY}:OH:{code}:question:{stem}-{counts[key]}"
            contest_path = f"{path}/{DAY}-general/{stem}-{counts[key]}"
            owner = slugs.get(contest_path)
            if owner is not None and owner != keyed.get(contest_key):
                print("slug taken:", contest_path)
                continue
            contest = cs.key("us.contest", contest_key)
            slugs[contest_path] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            cs.add(
                "name",
                entity_id=contest,
                text=f"{DAY.year} {place}: {label}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            for position, answer in enumerate(("Yes", "No")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{answer.lower()}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add("note", entity_id=contest, kind="context", body_md=row["text"])
            cs.add("citation", entity_id=contest, source_id=source)
        print(f"{kept} of {frame.height} questions")
        return cs.write()
