import io
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import elstats
from ea.sources.us.alged import PLACES, SCHOOLS
from ea.sources.us.alged import SOURCE as ALGED
from ea.store import DATA, connect

SOURCE = "us.elstats_local"
VERSION = "5"
PARSED = DATA / "parsed" / SOURCE
TOWNS = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_09_cousub_500k.zip"
COUNTY = ("board-of-commissioners", "Board of Commissioners", None)
SHERIFF = ("sheriff", "Sheriff", "other")
TREASURER = ("treasurer", "Treasurer", "other")
STATES = {
    "VA": (
        1996,
        {
            "Board of Supervisors": ("board-of-supervisors", "Board of Supervisors", None),
            "County Board Member": ("county-board", "County Board", None),
            "City Council": ("council", "City Council", None),
            "Town Council": ("council", "Town Council", None),
            "School Board": ("board-of-education", "Board of Education", None),
            "Mayor": ("mayor", "Mayor", "mayor"),
            "Chairman of the Board of Supervisors": (
                "chair-of-the-board-of-supervisors",
                "Chair of the Board of Supervisors",
                "other",
            ),
            "Chairman of the School Board": ("chair-of-the-school-board", "Chair of the School Board", "other"),
            "Sheriff": SHERIFF,
            "Commonwealth's Attorney": ("commonwealth-s-attorney", "Commonwealth's Attorney", "other"),
            "Commissioner of the Revenue": ("commissioner-of-the-revenue", "Commissioner of the Revenue", "other"),
            "Treasurer": TREASURER,
            "Clerk of Court": ("clerk-of-court", "Clerk of the Circuit Court", "other"),
            "Town Recorder": ("town-recorder", "Town Recorder", "other"),
        },
        [
            "Town",
            "Locality",
            "Board of Supervisors District",
            "County School Board District",
            "City School Board District",
        ],
        {"43"},
        (
            "Totals are the Department of Elections' locality rows, or the sums of its precinct rows where the archive "
            "has no locality row."
        ),
    ),
    "NH": (
        1970,
        {
            "County Commissioner": COUNTY,
            "County Treasurer": TREASURER,
            "County Sheriff": SHERIFF,
            "District Attorney": ("district-attorney", "District Attorney", "other"),
            "Register of Probate": ("register-of-probate", "Register of Probate", "other"),
            "County Recorder of Deeds": ("register-of-deeds", "Register of Deeds", "other"),
        },
        ["County"],
        set(),
        "Totals are the sums of the Secretary of State's town and ward rows.",
    ),
    "VT": (
        2010,
        {
            "County Sheriff": SHERIFF,
            "District Attorney": ("district-attorney", "State's Attorney", "other"),
            "Probate Judge": ("probate-judge", "Probate Judge", "other"),
            "Assistant Judge": ("assistant-judges", "Assistant Judges", None),
            "High Bailiff": ("high-bailiff", "High Bailiff", "other"),
        },
        ["County"],
        set(),
        (
            "Totals are the sums of the Secretary of State's town rows, or of its ward rows where the archive has no "
            "town row."
        ),
    ),
    "ID": (
        1990,
        {
            "County Commissioner": COUNTY,
            "County Sheriff": SHERIFF,
            "County Prosecuting Attorney": ("district-attorney", "Prosecuting Attorney", "other"),
            "County Treasurer": TREASURER,
            "County Clerk": ("clerk", "Clerk", "other"),
            "County Assessor": ("assessor", "Assessor", "other"),
            "County Coroner": ("coroner", "Coroner", "other"),
            "Mayor": ("mayor", "Mayor", "mayor"),
            "City Council": ("council", "City Council", None),
            "Alderman": ("council", "City Council", None),
        },
        ["County", "County Commissioner District", "City/Town"],
        {"25"},
        (
            "Totals are the Secretary of State's county rows, or the sums of its precinct rows where the archive has "
            "no county row."
        ),
    ),
    "NM": (
        2014,
        {
            "County Commissioner": COUNTY,
            "County Clerk": ("clerk", "Clerk", "other"),
            "County Treasurer": TREASURER,
            "County Sheriff": SHERIFF,
            "County Assessor": ("assessor", "Assessor", "other"),
            "Probate Judge": ("probate-judge", "Probate Judge", "other"),
            "Mayor": ("mayor", "Mayor", "mayor"),
            "City Mayor": ("mayor", "Mayor", "mayor"),
            "Town Mayor": ("mayor", "Mayor", "mayor"),
            "Village Mayor": ("mayor", "Mayor", "mayor"),
            "City Councilor": ("council", "City Council", None),
            "City Commissioner": ("council", "City Commission", None),
            "City Trustee": ("council", "Board of Trustees", None),
            "Town Councilor": ("council", "Town Council", None),
            "Town Commissioner": ("council", "Town Commission", None),
            "Town Trustee": ("council", "Board of Trustees", None),
            "Village Councilor": ("council", "Village Council", None),
            "Village Trustee": ("council", "Board of Trustees", None),
            "School Board Member": ("board-of-education", "Board of Education", None),
        },
        [
            "County",
            "County Commissioner District",
            "City",
            "Town",
            "Village",
            "Mayor District",
            "City Council District",
            "City Commissioner District",
            "Town Council District",
            "Town Commissioner District",
            "Village Council District",
            "School Board District",
        ],
        {"25", "43", "47"},
        "Totals are the sums of the Secretary of State's precinct rows across every voting method.",
    ),
    "CT": (
        2015,
        {
            "Mayor": ("mayor", "Mayor", "mayor"),
            "First Selectman": ("first-selectman", "First Selectman", "other"),
            "Selectman": ("board-of-selectmen", "Board of Selectmen", None),
            "City/Town Council": ("council", "Council", None),
            "Board of Aldermen/women": ("board-of-aldermen", "Board of Aldermen", None),
            "Representative Town Meeting": ("representative-town-meeting", "Representative Town Meeting", None),
            "Board of Education": ("board-of-education", "Board of Education", None),
            "Board of Finance": ("board-of-finance", "Board of Finance", None),
            "Board of Assessment Appeals": ("board-of-assessment-appeals", "Board of Assessment Appeals", None),
            "Planning and Zoning Commission": (
                "planning-and-zoning-commission",
                "Planning and Zoning Commission",
                None,
            ),
            "Zoning Board of Appeals": ("zoning-board-of-appeals", "Zoning Board of Appeals", None),
            "City/Town Clerk": ("clerk", "Town Clerk", "other"),
            "City/Town Treasurer": ("treasurer", "Treasurer", "other"),
            "Tax Collector": ("tax-collector", "Tax Collector", "other"),
            "Constables": ("constables", "Constables", None),
            "Library Trustees": ("library-trustees", "Library Trustees", None),
        },
        ["City/Town", "Board of Aldermen District", "Representative Town Meeting District"],
        set(),
        (
            "Totals are the sums of the Secretary of the State's town rows, or of its polling place rows where the "
            "archive has no town row, across election day and absentee ballots."
        ),
    ),
}
STAGES = {
    "General Election": "general",
    "General": "general",
    "Local Election": "local",
    "Regular Local Election": "local",
    "Special Election": "special",
    "Special": "special",
    "Municipal": "municipal",
    "Primary Election": "primary",
    "Primary": "primary",
}
ALIASES = {
    "silver": "silvercity",
    "jemezmountains": "jemezmountain",
    "hondo": "hondovalley",
    "moriartyedgewood": "moriarty",
}
MUNICIPAL = {
    "Town",
    "City/Town",
    "City",
    "Village",
    "City Council District",
    "City Commissioner District",
    "Town Council District",
    "Town Commissioner District",
    "Village Council District",
    "Board of Aldermen District",
    "Representative Town Meeting District",
}
RANKS = {"Locality": 0, "County": 0, "City/Town": 1, "Ward": 1, "Precinct": 2, "Polling Place": 2}
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Libertarian": "libertarian-party",
    "Green": "green-party",
}
WINNERS = (
    " The archive marks winners only {unit} by {unit}, so the winners shown are the candidates with the most votes, "
    "as many as the contest elects."
)
FLAGGED = (
    " The winners shown are the candidates the archive marks as elected in most of their rows, which can differ "
    "from the top vote-getters where the town's minority representation rules apply."
)
DISTRICT = (
    "Only part of the {whole} votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)


def url(state: str, year: int) -> str:
    search = {
        "global": {"years": {"from": year, "to": year}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": []},
        "contests": {"candidates": [], "divisions": [], "offices": [{"free": office} for office in STATES[state][1]]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return elstats.SEARCH.format(tenant=elstats.TENANTS[state][0]) + urlencode(
        {"search": json.dumps(search, separators=(",", ":"))}
    )


def fetch() -> None:
    raw.fetch(SOURCE, TOWNS, suffix=".zip")
    for state, (first, *_) in STATES.items():
        for year in range(first, 2026):
            raw.fetch(SOURCE, url(state, year), suffix=".csv")


def parse() -> None:
    frames = []
    for state, (first, offices, districts, *_) in STATES.items():
        for year in range(first, 2026):
            frame = pl.read_csv(
                raw.cached(SOURCE, url(state, year)).path, infer_schema=False, truncate_ragged_lines=True
            )
            if frame.height == 0:
                continue
            frames.append(
                frame.filter(
                    pl.col("office_name").is_in(list(offices))
                    & pl.col("district_type").is_in(districts)
                    & pl.col("election_type").is_in(list(STAGES))
                    & pl.col("division_type").is_in(list(RANKS))
                    & ~pl.col("candidate_name").str.contains(r"(?i)^total |^unknown candidate$")
                ).select(
                    pl.lit(state).alias("state"),
                    pl.col("contest_id").alias("contest"),
                    pl.col("election_date").str.slice(0, 10).str.to_date().alias("day"),
                    pl.col("election_type").replace_strict(STAGES).alias("stage"),
                    pl.col("office_name").alias("office"),
                    pl.col("district_type").alias("type"),
                    pl.col("district_name").alias("district"),
                    pl.col("candidate_id").alias("candidate"),
                    pl.col("candidate_name").str.replace_all(r"\s+", " ").str.strip_chars().alias("name"),
                    pl.col("candidate_party_name").alias("party"),
                    pl.when(pl.col("election_type").str.contains("Primary"))
                    .then(pl.col("primary_party"))
                    .alias("scope"),
                    pl.col("division_type").replace_strict(RANKS, return_dtype=pl.Int8).alias("rank"),
                    pl.col("division_id").alias("division"),
                    pl.col("division_name").str.replace_all(r"\s+", " ").str.strip_chars().alias("precinct"),
                    pl.col("number_seats").cast(pl.Int16).alias("magnitude"),
                    pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64).alias("votes"),
                    (pl.col("is_winner") == "true").alias("winner"),
                )
            )
    frame = pl.concat(frames).with_columns(
        candidate=pl.when(pl.col("name").str.contains(r"(?i)^write"))
        .then(pl.lit("write-ins"))
        .when(pl.col("name").str.contains(r"(?i)^all other|^scatter"))
        .then(pl.lit("others"))
        .otherwise("candidate"),
        name=pl.when(pl.col("name").str.contains(r"(?i)^write"))
        .then(pl.lit("Write-ins"))
        .when(pl.col("name").str.contains(r"(?i)^all other|^scatter"))
        .then(pl.lit("Others"))
        .otherwise("name"),
    )
    precincts = (
        frame.filter((pl.col("rank") == 2) & pl.col("state").is_in(["VA", "ID", "NM", "CT"]))
        .group_by("state", "contest", "candidate", "division", "precinct")
        .agg(pl.col("votes").sum())
    )
    frame = (
        frame.filter(pl.col("rank") == pl.col("rank").min().over("state", "contest"))
        .group_by(
            "state", "contest", "day", "stage", "scope", "office", "type", "district", "candidate", "name", "magnitude"
        )
        .agg(pl.col("party").drop_nulls().first(), pl.col("votes").sum(), pl.col("winner").mean() > 0.5)
    )
    with zipfile.ZipFile(raw.cached(ALGED, SCHOOLS[0]).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    schools: dict[str, dict[str, str]] = {}
    for record in shapefile.Reader(dbf=io.BytesIO(parts["dbf"])).iterRecords():
        if record is not None and record["STUSPS"] in STATES:
            text = re.sub(r" (Public|Municipal|Consolidated|Independent)? ?Schools$", "", str(record["NAME"]))
            schools.setdefault(str(record["STUSPS"]), {})[elstats.plain(text)] = str(record["GEOID"])
    with zipfile.ZipFile(raw.cached(ALGED, PLACES).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    towns: dict[str, dict[str, str]] = {}
    for record in shapefile.Reader(dbf=io.BytesIO(parts["dbf"])).iterRecords():
        usps = "" if record is None else str(record["STUSPS"])
        if record is not None and usps in STATES and record["LSAD"] in STATES[usps][3]:
            for text in (str(record["NAME"]), re.sub(r" (City|Town)$", "", str(record["NAME"]))):
                towns.setdefault(usps, {})[elstats.plain(text)] = str(record["GEOID"])
    with zipfile.ZipFile(raw.cached(SOURCE, TOWNS).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    connecticut: dict[str, str] = {}
    for record in shapefile.Reader(dbf=io.BytesIO(parts["dbf"])).iterRecords():
        if record is not None and record["COUSUBFP"] != "00000":
            towns.setdefault("CT", {})[elstats.plain(str(record["NAME"]))] = str(record["GEOID"])
            connecticut[elstats.plain(str(record["NAME"]))] = str(record["GEOID"])[:5]
    with connect() as conn:
        for code, text in conn.execute(
            """select u.code, n.text from reporting_unit u
               join name n on n.entity_id = u.area_id and n.is_preferred
               where u.scheme_code = 'us.place' and u.code like '09%'"""
        ).fetchall():
            towns["CT"][elstats.plain(text.removesuffix(", CT"))] = code
        counties: dict[str, dict[str, str]] = {}
        jurisdictions: dict[str, str] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515'"""
        ).fetchall():
            counties.setdefault(iso[3:], {})[
                elstats.plain(text) + ("city" if code[:2] == "51" and int(code[2:]) >= 500 else "county")
            ] = code
            jurisdictions[code] = text.upper() + (" CITY" if code[:2] == "51" and int(code[2:]) >= 500 else "")
    labels = []
    for state, contest, office, kind, district in (
        frame.select("state", "contest", "office", "type", "district").unique().iter_rows()
    ):
        locality, _, seat = re.sub(r"\s*\([^)]*\)", "", district).partition(" - ")
        numbered = re.fullmatch(r"(.+?) (\d+)", locality)
        if not seat and numbered and kind != "City/Town":
            locality, seat = numbered[1], numbered[2]
        if locality.endswith(" Ward"):
            locality, seat = locality.removesuffix(" Ward"), f"Ward {seat}"
        plain = elstats.plain(re.sub(r"^Mt\.? ", "Mount ", locality))
        code = next(
            (
                code
                for key in (plain, f"{plain}city", f"{plain}county")
                if (code := counties[state].get(key)) is not None
            ),
            None,
        )
        if kind in MUNICIPAL and not locality.endswith("County"):
            code = towns.get(state, {}).get(plain)
        elif STATES[state][1][office][0] in ("board-of-education", "chair-of-the-school-board"):
            cleaned = elstats.plain(
                re.sub(
                    r"( \d+)+$", "", re.sub(r" (Municipal|Muncipal|Public|Consolidated|Independent)\b", "", locality)
                )
            )
            school = next(
                (
                    found
                    for key in (
                        plain,
                        f"{plain}city",
                        f"{plain}county",
                        f"{cleaned}city",
                        ALIASES.get(cleaned, cleaned),
                    )
                    if (found := schools.get(state, {}).get(key)) is not None
                ),
                None,
            )
            code = None if school is None else f"sd{school}"
        labels.append({"state": state, "contest": contest, "code": code, "seat": seat or None})
    labels = pl.DataFrame(
        labels, schema={"state": pl.String, "contest": pl.String, "code": pl.String, "seat": pl.String}
    )
    missing = labels.filter(pl.col("code").is_null())
    print(f"dropped {missing.height} contests with no polity: {missing.group_by('state').len().rows()}")
    frame = frame.join(labels.filter(pl.col("code").is_not_null()), on=["state", "contest"])
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    contests = (
        frame.filter(
            ~pl.col("office").is_in(
                [
                    office
                    for state in STATES.values()
                    for office, (key, *_) in state[1].items()
                    if key in ("mayor", "council")
                ]
            )
            & ((pl.col("code").str.len_chars() == 5) | pl.col("code").str.starts_with("sd") | (pl.col("state") == "CT"))
        )
        .select(
            "state",
            "contest",
            "code",
            "day",
            pl.col("district").str.replace(r"\s*\([^)]*\)", "").str.replace(r" - .*$", "").alias("town"),
        )
        .unique()
    )
    precincts = precincts.join(contests, on=["state", "contest"])
    homes = (
        precincts.filter(pl.col("code").str.len_chars() == 5)
        .group_by("state", "division")
        .agg(pl.col("code").unique())
        .filter(pl.col("code").list.len() == 1)
        .select("state", "division", pl.col("code").list.first().alias("county"))
    )
    precincts = (
        precincts.join(homes, on=["state", "division"], how="left")
        .with_columns(
            county=pl.when(pl.col("state") == "CT")
            .then(
                pl.col("town")
                .map_elements(elstats.plain, return_dtype=pl.String)
                .replace_strict(connecticut, default=None)
            )
            .otherwise("county")
        )
        .filter(pl.col("county").is_not_null().all().over("state", "contest"))
        .with_columns(
            unit=pl.concat_str(
                pl.col("day").dt.year().cast(pl.String),
                "state",
                "county",
                pl.when(pl.col("state") == "CT")
                .then(pl.col("town").str.to_uppercase())
                .otherwise(pl.col("county").replace_strict(jurisdictions, default=None)),
                pl.col("precinct").str.to_uppercase(),
                separator=":",
            )
        )
        .group_by("state", "contest", "candidate", "unit")
        .agg(pl.col("votes").sum())
    )
    precincts = precincts.join(
        precincts.group_by("state", "contest", "candidate")
        .agg(pl.col("votes").sum())
        .join(
            frame.group_by("state", "contest", "candidate").agg(pl.col("votes").sum()),
            on=["state", "contest", "candidate"],
            how="full",
            coalesce=True,
        )
        .group_by("state", "contest")
        .agg(pl.col("votes").eq_missing(pl.col("votes_right")).all().alias("matched"))
        .filter("matched")
        .select("state", "contest"),
        on=["state", "contest"],
    )
    print(f"{precincts.select('state', 'contest').n_unique()} of {contests.height} contests by precinct")
    precincts.write_parquet(PARSED / "precincts.parquet")
    wanted = set(frame["code"])
    records = []
    for prefix, shapes in (("", PLACES), ("sd", SCHOOLS[0]), ("", TOWNS)):
        with zipfile.ZipFile(raw.cached(SOURCE if shapes == TOWNS else ALGED, shapes).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        records += [
            (
                prefix + str(record["GEOID"]),
                str(record["NAME"]),
                str(record["STUSPS"]),
                json.dumps(shape.__geo_interface__),
            )
            for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
            if record is not None and shape is not None and prefix + str(record["GEOID"]) in wanted
        ]
    with connect() as conn:
        pl.DataFrame(
            conn.execute(
                """select code, name, state, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select code, name, state, st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as f(code, name, state, geojson)) shapes""",
                [[record[index] for record in records] for index in range(4)],
            ).fetchall(),
            schema=["code", "name", "state", "geom", "point"],
            orient="row",
        ).write_parquet(PARSED / "places.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    places = pl.read_parquet(PARSED / "places.parquet")
    grouped = {keys: rows for keys, rows in pl.read_parquet(PARSED / "precincts.parquet").group_by("state", "contest")}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="County, city, town, and school board elections and primaries from the archives of Virginia, New Hampshire, Idaho, New Mexico, Connecticut, and Vermont",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources = {state: cs.source(elstats.TENANTS[state][1], elstats.TITLE) for state in STATES}
        stored: set[str] = set()
        for state, (first, *_) in STATES.items():
            for year in range(first, 2026):
                if (artifact := raw.cached(SOURCE, url(state, year))).storage_key not in stored:
                    stored.add(artifact.storage_key)
                    cs.artifact(artifact, sources[state])
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        cs.artifact(raw.cached(SOURCE, TOWNS), census)
        cs.add("scheme", code="us.town", polity_id=cs.polity("US"), kind="administrative", rank=3, noun="town")
        states = {
            iso[3:]: (polity, text)
            for iso, polity, text in conn.execute(
                """select p.iso_code, p.id, n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        }
        polities: dict[str, tuple[UUID, str, str]] = {}
        for code, polity, text, path, kind in conn.execute(
            """select c.code, t.polity_id, n.text, s.path, p.kind from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district', 'us.town')"""
        ).fetchall():
            polities[f"sd{code}" if kind == "school_district" else code] = (polity, text.removeprefix("City of "), path)
        taken = {
            path
            for (path,) in conn.execute(
                """select s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.path like 'us/%%' and c.pipeline <> %s""",
                [SOURCE],
            ).fetchall()
        }
        held = dict(
            conn.execute(
                """select s.entity_id, s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.is_current and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        for (path,) in conn.execute(
            "select s.path from slug s join changeset c on c.id = s.changeset_id where c.pipeline = %s", [SOURCE]
        ).fetchall():
            taken.add(path)
        for code, text, usps, geom, point in places.iter_rows():
            if code in polities:
                continue
            school, town = code.startswith("sd"), len(code) == 10
            geoid = code.removeprefix("sd")
            scheme = "us.school_district" if school else "us.town" if town else "us.place"
            polity = cs.key("us.polity", f"school:{geoid}" if school else f"town:{geoid}" if town else f"place:{geoid}")
            path = f"us/{usps.lower()}/{slug(text)}"
            if path in taken:
                path = f"{path}-{'town' if usps in ('VA', 'CT') else 'city'}"
            if path in taken:
                raise ValueError(f"{text} would take {path}")
            taken.add(path)
            polities[code] = (polity, text, path)
            cs.add("polity", id=polity, kind="school_district" if school else "municipality", iso_code=None)
            name(polity, text)
            cs.add("polity_parent", polity_id=polity, parent_id=states[usps][0])
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area(scheme, geoid)
            cs.add("area", id=area, scheme_code=scheme)
            cs.add("area_code", area_id=area, code=geoid, valid_from=None)
            cs.add(
                "area_parent",
                area_id=area,
                parent_id=cs.area("us.county", geoid[:5]) if town else cs.area("us.state", geoid[:2]),
            )
            name(area, f"{text}, {usps}")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=census,
            )
            cs.add(
                "reporting_unit",
                id=cs.unit(scheme, geoid),
                polity_id=polity,
                scheme_code=scheme,
                code=geoid,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}
        labels = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'")}
        parties = {label: cs.key("us.party", key) for label, key in PARTIES.items()}
        if cs.minted & {*systems.values(), *parties.values()}:
            raise ValueError("the systems and parties must already exist")
        covered = {
            (owner, day.year, None if precision == "year" else day.month)
            for owner, day, precision in conn.execute(
                """select coalesce(c.office_id, c.body_id), e.date_from, e.date_precision from contest c
                   join election e on e.id = c.election_id join changeset x on x.id = c.changeset_id
                   where x.pipeline = %s""",
                [ALGED],
            ).fetchall()
        }
        statewide = {
            value: entity
            for value, entity in conn.execute(
                "select value, entity_id from external_id where scheme = 'us.statewide.person'"
            ).fetchall()
        }

        precincts: dict[str, UUID] = dict(
            conn.execute(
                """select code, id from reporting_unit where scheme_code = 'us.precinct'
                   and split_part(code, ':', 2) in ('VA', 'ID', 'NM', 'CT')"""
            ).fetchall()
        )
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                """select code, id, area_id from reporting_unit where scheme_code = 'us.county'
                   and left(code, 2) in ('51', '16', '35', '09')"""
            ).fetchall()
        }
        sets = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = %s", [f"{SOURCE}.result_set"]
            ).fetchall()
        }
        wards = {
            entity
            for (entity,) in conn.execute(
                """select distinct r.contest_id from result_set r join changeset x on x.id = r.changeset_id
                   where x.pipeline = 'us.city_precincts'"""
            ).fetchall()
        }
        minted: dict[str, date] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        owners: set[UUID] = set()
        elections: set[UUID] = set()
        persons: set[UUID] = set()
        districts: set[str] = set()
        paths: set[str] = set()
        skipped = 0
        for (_, contest_id), rows in frame.sort(
            "state", "contest", "votes", "name", descending=[False, False, True, False]
        ).group_by("state", "contest", maintain_order=True):
            first = rows.row(0, named=True)
            state, code, office, day, stage = (
                first["state"],
                first["code"],
                first["office"],
                first["day"],
                first["stage"],
            )
            if code not in polities:
                continue
            polity, place, path = polities[code]
            key, role, kind = STATES[state][1][office]
            geoid = code.removeprefix("sd")
            scheme = {5: "us.county", 7: "us.place", 10: "us.town"}.get(len(code), "us.school_district")
            whole = (
                "school district"
                if code.startswith("sd")
                else "town"
                if len(code) == 10 or len(code) == 7 and state != "CT"
                else "city"
                if state == "CT" or state == "VA" and int(code[2:]) >= 500
                else "county"
            )
            if key == "mayor":
                title = f"Mayor of {place}"
            elif key == "council":
                title = f"{place} {'Town' if state == 'VA' and len(code) == 7 or len(code) == 10 else 'City'} Council"
            else:
                title = f"{place} {role}"
            owner = cs.key("us.office" if kind else "us.body", f"{state}:{code}:{key}")
            if (owner, day.year, day.month) in covered or (owner, day.year, None) in covered:
                skipped += 1
                continue
            if owner in cs.minted and owner not in owners:
                if kind:
                    cs.add("office", id=owner, polity_id=polity, kind=kind)
                else:
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                name(owner, title)
            owners.add(owner)

            primary = stage == "primary"
            kind_of = "primary" if primary else "general" if day.month == 11 and stage != "municipal" else "local"
            parent = cs.key("us.election", f"{day}:{state}" + (":primary" if primary else ""))
            if parent in cs.minted and parent not in elections:
                elections.add(parent)
                cs.add(
                    "election",
                    id=parent,
                    polity_id=states[state][0],
                    kind=kind_of,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(
                    parent,
                    f"{day.year} {states[state][1]} " + ("primary" if primary else f"{kind_of} elections"),
                )
                cs.add("slug", path=f"us/{state.lower()}/{day}-{kind_of}", entity_id=parent)
            election = cs.key("us.election", f"{day}:{state}:{code}" + (":primary" if primary else ""))
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind_of,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} {place} {kind_of} election")
                cs.add("slug", path=f"{path}/{day}-{kind_of}", entity_id=election)

            seat = first["seat"]
            seat = f"District {seat.lstrip('0')}" if seat and re.fullmatch(r"\d+", seat) else seat
            area, unit = cs.area(scheme, geoid), cs.unit(scheme, geoid)
            partial = seat is not None and not re.search(r"(?i)at[- ]?large", seat)
            if partial:
                district = f"{geoid[:2]}:{code}:{key}:{slug(seat)}"
                area, unit = cs.area("us.local_district", district), cs.unit("us.local_district", district)
                if area in cs.minted and district not in districts:
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=cs.area(scheme, geoid), valid_from=None)
                    name(area, f"{title}, {seat}")
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
                districts.add(district)
            contest_key = f"{state}:local:{contest_id}"
            contest = cs.key("us.contest", contest_key)
            magnitude = first["magnitude"] or 1
            special = stage == "special"
            scope = first["scope"]
            label = (
                PARTIES.get(scope or "")
                or next(
                    (
                        key
                        for key in (f"{state}:{slug(scope)}-party", f"{state}:{slug(scope)}", f"{slug(scope)}-party")
                        if key in labels
                    ),
                    None,
                )
                if scope
                else None
            )
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office" if kind else "body_seats",
                office_id=owner if kind else None,
                body_id=None if kind else owner,
                party_scope_id=None if label is None else cs.key("us.party", label),
                district_area_id=area,
                magnitude=magnitude,
                round_number=1,
                electoral_system_id=systems["fptp" if magnitude == 1 else "block"],
                default_result_kind="counted",
                term="remainder" if special else "full",
            )
            name(
                contest,
                (f"{day.year} {place} mayoral election" if key == "mayor" else f"{day.year} {title} election")
                + (f", {seat}" if seat else "")
                + (f", {scope} primary" if primary else "")
                + (" (special)" if special else ""),
            )
            contest_path = (
                f"{path}/{day}-{kind_of}/{key}"
                + (f"-{slug(seat)}" if seat else "")
                + (f"-{slug(scope or 'open')}" if primary else "")
            )
            contest_path += "-special" if special else ""
            suffix = 1
            candidate = held.get(contest)
            while candidate is None or candidate in paths or candidate in taken and held.get(contest) != candidate:
                candidate = contest_path if suffix == 1 else f"{contest_path}-{suffix}"
                suffix += 1
            paths.add(candidate)
            cs.add("slug", path=candidate, entity_id=contest)
            if partial:
                cs.add("note", entity_id=contest, kind="caveat", body_md=DISTRICT.format(whole=whole))
            counted = None if contest in wards else grouped.get((state, contest_id))
            previous = None
            result_set = cs.key(f"{SOURCE}.result_set", contest_key) if counted is None or contest_key in sets else None
            if counted is not None:
                if contest_key in sets:
                    previous = result_set
                    cs.add(
                        "result_set",
                        id=previous,
                        election_id=election,
                        contest_id=contest,
                        stage="certified",
                        kind="counted",
                        status="superseded",
                        source_id=sources[state],
                        is_default=False,
                        completeness=1,
                    )
                result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:precinct")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[state],
                is_default=True,
                supersedes_id=previous,
                completeness=1,
            )
            flagged = state == "CT" and rows["winner"].any()
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=STATES[state][4]
                + (FLAGGED if flagged else WINNERS.format(unit="town" if state in ("NH", "VT") else "precinct")),
            )
            rank = 0
            for candidate_id, ballot, party, votes, winner in rows.select(
                "candidate", "name", "party", "votes", "winner"
            ).iter_rows():
                if candidate_id in ("write-ins", "others"):
                    option = cs.key("us.option", f"{contest_key}:{candidate_id}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="write_in" if candidate_id == "write-ins" else "others",
                        ballot_label=ballot,
                    )
                else:
                    rank += 1
                    person = statewide.get(f"{state}:{candidate_id}") or cs.key(
                        f"{SOURCE}.person", f"{state}:{candidate_id}"
                    )
                    if person in cs.minted and person not in persons:
                        cs.add("person", id=person)
                        name(person, ballot)
                    persons.add(person)
                    option = cs.key("us.option", f"{contest_key}:{candidate_id}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(party or ""),
                        ballot_label=ballot,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{candidate_id}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=party == "Independent",
                    )
                    if winner if flagged else rank <= magnitude and votes > 0:
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="won" if primary else "elected",
                            seats=None,
                        )
                if result_set in cs.minted and counted is None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
                if result_set in cs.minted and counted is not None:
                    for place, value in (
                        counted.filter(pl.col("candidate") == candidate_id).select("unit", "votes").iter_rows()
                    ):
                        target = precincts.get(place) or cs.unit("us.precinct", place)
                        if place not in precincts:
                            minted.setdefault(place, day)
                        if area != counties[place.split(":")[2]][1]:
                            memberships.add((target, area, day))
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=target,
                            option_id=option,
                            channel=0,
                            measure=1,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        for place, day in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", place),
                polity_id=states[place.split(":")[1]][0],
                scheme_code="us.precinct",
                code=place,
                parent_id=counties[place.split(":")[2]][0],
                area_relation="within",
                area_id=counties[place.split(":")[2]][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        for target, area, day in memberships:
            cs.add(
                "reporting_unit_area",
                unit_id=target,
                area_id=area,
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        print(f"skipped {skipped} contests the local elections database already holds")
        return cs.write()
