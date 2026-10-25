import io
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.ncsbe import FILES, PUBLISHER, TITLE
from ea.sources.us.ncsbe import SOURCE as NCSBE
from ea.store import DATA, connect

SOURCE = "us.nc_local"
VERSION = "5"
PARSED = DATA / "parsed" / SOURCE
DAYS = [
    date(int(text[:4]), int(text[4:6]), int(text[6:]))
    for text in [
        "20081104",
        "20100504",
        "20100622",
        "20101102",
        "20120508",
        "20120717",
        "20121106",
        "20140506",
        "20140715",
        "20141104",
        "20150915",
        "20151006",
        "20151103",
        "20160315",
        "20160607",
        "20161108",
        "20170912",
        "20171010",
        "20171107",
        "20180508",
        "20180626",
        "20181106",
        "20190514",
        "20190910",
        "20191008",
        "20191105",
        "20200303",
        "20200623",
        "20201103",
        "20211005",
        "20211102",
        "20220517",
        "20220726",
        "20221108",
        "20221206",
        "20230307",
        "20230912",
        "20231010",
        "20231107",
        "20240305",
        "20241105",
        "20250909",
        "20251007",
        "20251104",
    ]
]
SECOND = {
    date(2010, 6, 22): date(2010, 5, 4),
    date(2012, 7, 17): date(2012, 5, 8),
    date(2014, 7, 15): date(2014, 5, 6),
    date(2016, 6, 7): date(2016, 3, 15),
    date(2018, 6, 26): date(2018, 5, 8),
    date(2020, 6, 23): date(2020, 3, 3),
    date(2022, 7, 26): date(2022, 5, 17),
}
SCOPES = {
    "DEM": "Democratic",
    "REP": "Republican",
    "LIB": "Libertarian",
    "GRE": "Green",
    "CST": "Constitution",
    "UNA": "Unaffiliated",
    "LBT": "Libertarian",
}
SPECIAL_DAYS = {date(2019, 5, 14), date(2022, 12, 6), date(2023, 3, 7)}
PARTY_SUFFIX = re.compile(r"\s*\((DEM|REP|LIB|GRE|CST|UNA|LBT)\)\s*$")
SINGLE = {"sheriff", "register-of-deeds", "clerk-of-superior-court", "coroner", "surveyor", "mayor"}
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_37_place_500k.zip"
KINDS = {"25": "City", "43": "Town", "47": "Village"}
COLUMNS = {
    "county": "county",
    "precinct": "precinct",
    "contest": "contest",
    "contest name": "contest",
    "choice": "choice",
    "party": "party",
    "choice party": "party",
    "total votes": "votes",
    "vote for": "vote_for",
    "vote_for": "vote_for",
    "contest type": "ctype",
    "contest_type": "ctype",
}
OFFICES = {
    "board-of-commissioners-chair": (
        r"(?:COUNTY )?COMMISSION CHAIR(?:MAN)?|(?:BOARD OF )?COMMISSIONERS? CHAIR(?:MAN)?",
        "Chair of the Board of Commissioners",
    ),
    "board-of-commissioners": (
        r"(?:COUNTY )?(?:(?:BOARD|BD) OF ?(?:COUNTY )?COMMISS?I[O0]NERS?|COMMISS?I[O0]NERS?)",
        "Board of Commissioners",
    ),
    "board-of-education-chair": (
        r"(?:COUNTY )?SCHOOL BOARD CHAIR(?:MAN)?|BOARD OF EDUCATION CHAIR(?:MAN)?",
        "Chair of the Board of Education",
    ),
    "board-of-education": (
        r"(?:COUNTY[- ]?(?:WIDE )?)?(?:PUBLIC )?(?:SCHOOLS? )?(?:SYSTEM )?(?:(?:BOARD|BD) OF EDU\w*|SCHOOL BOARD)",
        "Board of Education",
    ),
    "soil-and-water": (
        r"(?:COUNTY )?SOIL (?:AND |& )?WATER CONSERVATI?ON DISTRICT SUPERVISO?R?S?",
        "Soil and Water Conservation District Board of Supervisors",
    ),
    "sheriff": (r"(?:COUNTY )?SHERIFF", "Sheriff"),
    "register-of-deeds": (r"(?:COUNTY )?REGISTER OF DEEDS", "Register of Deeds"),
    "clerk-of-superior-court": (r"(?:COUNTY )?CLERK OF (?:THE )?(?:SUPERIOR )?COURT", "Clerk of Superior Court"),
    "coroner": (r"(?:COUNTY )?CORONER", "Coroner"),
    "surveyor": (r"(?:COUNTY )?SURVEYOR", "Surveyor"),
}
BODIES = {"board-of-commissioners", "board-of-education", "soil-and-water", "council", "special"}
SPECIAL = re.compile(
    r"(?:(?:[A-Z]+ )?CO(?:UNTY|\.) ?- ?|[A-Z]+ - )?(?:CITY OF )?(.+?) (SANITARY(?: (LAND|WATER AND SEWER))? (?:DIST\w*|BOARD)|HOSPITAL)\b.*"
)
MUNICIPAL = re.compile(
    r"(?:CITY |TOWN |VILLAGE )?(?:(MAYOR)|(COUNCIL)(?: ?(?:MEMBERS?|M[AE]N|WOMAN|PERSONS?))?|BOARD OF (COMMISSIONERS)"
    r"|(COMMISSIONERS?)|BOARD OF (ALDERMEN)|(ALDERM[AE]N)|BOARD OF (TRUSTEES)|(TRUSTEES?))\b\s*-?\s*(.*)"
)
BOARDS = {"COUNCIL": "{kind} Council", "COMMISSION": "Board of Commissioners", "ALDERM": "Board of Aldermen"}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LIB": "libertarian-party",
    "GRE": "green-party",
    "CST": "constitution-party",
}
METHOD = (
    "Totals are the sums of the precinct, early voting, absentee, and provisional rows in the State Board's "
    "precinct file. The file does not mark winners, so the winners shown are the candidates with the most votes, as "
    "many as the contest elects."
)
DISTRICT = (
    "Only part of the {whole} votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)


def fetch() -> None:
    for day in DAYS:
        raw.fetch(NCSBE, FILES.format(day=day), suffix=".zip")
    raw.fetch(SOURCE, PLACES, suffix=".zip")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^A-Z]", "", text.upper()): (code, text)
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '37%'"""
            ).fetchall()
        }
        with zipfile.ZipFile(raw.cached(SOURCE, PLACES).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        records = [
            (record.as_dict(), json.dumps(shape.__geo_interface__))
            for record, shape in zip(reader.records(), reader.shapes(), strict=True)
            if record is not None and shape is not None and record["LSAD"] in KINDS
        ]
        places = pl.DataFrame(
            conn.execute(
                """select code, name, kind, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select code, name, kind, st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as f(code, name, kind, geojson)) shapes""",
                [
                    [str(record["GEOID"]) for record, _ in records],
                    [str(record["NAME"]) for record, _ in records],
                    [KINDS[str(record["LSAD"])] for record, _ in records],
                    [geojson for _, geojson in records],
                ],
            ).fetchall(),
            schema=["code", "name", "kind", "geom", "point"],
            orient="row",
        )
    towns = {re.sub(r"[^A-Z]", "", name.upper()): code for code, name in places.select("code", "name").iter_rows()}
    contests: list[dict[str, object]] = []
    results: list[pl.DataFrame] = []
    for day in DAYS:
        with zipfile.ZipFile(raw.cached(NCSBE, FILES.format(day=day)).path) as archive:
            text = archive.read(archive.namelist()[0]).replace(b"\x00", b"")
        first = text.split(b"\n", 1)[0]
        frame = pl.read_csv(
            io.BytesIO(text),
            separator="\t" if b"\t" in first else ",",
            infer_schema=False,
            quote_char='"',
            truncate_ragged_lines=True,
        )
        frame = frame.rename(
            {column: COLUMNS[column.strip().lower()] for column in frame.columns if column.strip().lower() in COLUMNS}
        )
        if "ctype" in frame.columns:
            frame = frame.filter(pl.col("ctype").str.strip_chars() == "C")
        if "vote_for" not in frame.columns:
            frame = frame.with_columns(vote_for=pl.lit(None, dtype=pl.String))
        frame = frame.select(
            "county",
            "precinct",
            pl.col("contest").str.to_uppercase().str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("choice").str.replace_all(r"\s+", " ").str.strip_chars(),
            pl.col("party").fill_null("").str.strip_chars(),
            pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
            pl.col("vote_for").cast(pl.Int16),
        ).filter(pl.col("choice").is_not_null())
        labels: list[dict[str, object]] = []
        for county, contest in frame.select("county", "contest").unique().iter_rows():
            fips, short = counties[re.sub(r"[^A-Z]", "", county.upper())]
            party = PARTY_SUFFIX.search(contest)
            name = PARTY_SUFFIX.sub("", contest)
            seats = re.search(r"\((\d+) SEATS?\)", name)
            name = re.sub(r"\s*\(\d+ SEATS?\)", "", name)
            name = re.sub(r"\s*-?\s*\(?\bUN-?EX\w*\b.*$", "", name)
            remainder = re.search(r"\bUN-?EX", contest) is not None
            if re.search(r"REFERENDUM|STRUCTURE|METHOD|RUNOFF|BONDS?\b|ELECTION$", name):
                continue
            code, word = fips, None
            name = re.sub(rf"^{re.escape(short.upper())}( COUNTY)?( -)? ", "", name)
            special = SPECIAL.fullmatch(name)
            if special:
                kind = "hospital" if special[2] == "HOSPITAL" else "sanitary"
                labels.append(
                    {
                        "county": county,
                        "contest": contest,
                        "code": f"{fips}:{kind}:{slug(special[1].replace('REIGELWOOD', 'RIEGELWOOD'))}",
                        "office": "special",
                        "seat": None,
                        "remainder": remainder,
                        "scope": party[1] if party else None,
                        "seats": int(seats[1]) if seats else None,
                        "word": special[1].replace("REIGELWOOD", "RIEGELWOOD").title()
                        + (
                            " Hospital"
                            if kind == "hospital"
                            else f" Sanitary {(special[3] or '').title().replace(' And ', ' and ')} District".replace(
                                "  ", " "
                            )
                        ),
                    }
                )
                continue
            office = next(
                (
                    (key, found[1])
                    for key, (pattern, _) in OFFICES.items()
                    if (found := re.fullmatch(rf"(?:{pattern})\b\s*-?\s*(.*)", name))
                ),
                None,
            )
            if office is not None and office[1].startswith("OF "):
                continue
            prefix = re.match(r"(?:CITY|TOWN|VILLAGE) OF (.+)", name)
            if prefix:
                words = prefix[1].split(" ")
                town = next(
                    (
                        (towns[key], " ".join(words[size:]))
                        for size in range(len(words), 0, -1)
                        if (key := re.sub(r"[^A-Z]", "", " ".join(words[:size]))) in towns
                    ),
                    None,
                )
                found = MUNICIPAL.fullmatch(town[1]) if town else None
                if town is None or found is None:
                    continue
                groups = found.groups()
                code, word = town[0], next(group for group in groups[:-1] if group)
                office = ("mayor" if word == "MAYOR" else "council", groups[-1])
            if office is None:
                continue
            seat = re.sub(r"\bTWP\b", "TOWNSHIP", re.sub(r"\bDIST\b", "DISTRICT", office[1]))
            seat = re.sub(r"\b0+(\d)", r"\1", seat).strip(" -")
            seat = re.sub(
                r"\b([A-Z]+)\b",
                lambda word: word[1] if re.fullmatch(r"[IVX]+|[A-Z]", word[1]) else word[1].capitalize(),
                seat,
            )
            seat = re.sub(r"^(Members?|Seats?)$", "", re.sub(r"(?i)^at[- ]large$", "At-Large", seat)) or None
            if seat in ("Chair", "Chairman") and f"{office[0]}-chair" in OFFICES:
                office, seat = (f"{office[0]}-chair", ""), None
            labels.append(
                {
                    "county": county,
                    "contest": contest,
                    "code": code,
                    "office": office[0],
                    "seat": seat,
                    "remainder": remainder,
                    "scope": party[1] if party else None,
                    "seats": int(seats[1]) if seats else None,
                    "word": next((board for board in BOARDS if word and word.startswith(board)), None),
                }
            )
        frame = frame.join(pl.DataFrame(labels, infer_schema_length=None), on=["county", "contest"])
        widths = dict(
            frame.group_by("code", "contest")
            .agg(pl.struct("county", "precinct").n_unique())
            .group_by("code")
            .agg(pl.col("county").max())
            .iter_rows()
        )
        for (code, contest), rows in frame.group_by("code", "contest"):
            first = rows.row(0, named=True)
            contests.append(
                {
                    "day": day,
                    "code": code,
                    "contest": contest,
                    "office": first["office"],
                    "seat": first["seat"],
                    "remainder": first["remainder"],
                    "word": first["word"],
                    "district": rows.select(pl.struct("county", "precinct").n_unique()).item() < 0.9 * widths[code],
                    "scope": first["scope"],
                    "magnitude": rows["vote_for"].max()
                    or first["seats"]
                    or (
                        1
                        if first["office"] in SINGLE
                        or first["office"].endswith("-chair")
                        or re.match(r"(?:District|Ward|Seat|Post|Precinct) \w+$", first["seat"] or "")
                        else None
                    ),
                }
            )
            results.append(
                rows.group_by("choice", "party", "county", "precinct")
                .agg(pl.col("votes").sum())
                .with_columns(
                    day=pl.lit(day),
                    code=pl.lit(code),
                    contest=pl.lit(contest),
                    unit=pl.when(pl.col("precinct").fill_null("").str.strip_chars() != "").then(
                        pl.concat_str(
                            pl.lit(f"{day.year}:NC"),
                            pl.col("county").map_elements(
                                lambda county: counties[re.sub(r"[^A-Z]", "", county.upper())][0],
                                return_dtype=pl.String,
                            ),
                            pl.col("county").str.to_uppercase().str.strip_chars(),
                            pl.col("precinct").str.strip_chars(),
                            separator=":",
                        )
                    ),
                )
                .drop("county", "precinct")
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(contests, infer_schema_length=None)
    merged = pl.concat(results)
    homes = (
        merged.filter(pl.col("code").str.contains(":"))
        .group_by("day", "contest", "code")
        .agg(pl.col("votes").sum())
        .with_columns(name=pl.col("code").str.split_exact(":", 1).struct.field("field_1"))
        .sort("votes", "code", descending=[True, False])
        .with_columns(home=pl.col("code").first().over("day", "contest", "name"))
        .select("day", "contest", "code", "home")
    )
    merged = (
        merged.join(homes, on=["day", "contest", "code"], how="left")
        .with_columns(code=pl.coalesce("home", "code"))
        .drop("home")
    )
    frame = (
        frame.join(homes, on=["day", "contest", "code"], how="left")
        .with_columns(code=pl.coalesce("home", "code"))
        .drop("home")
        .unique(["day", "code", "contest"], keep="first", maintain_order=True)
    )
    duplicated = frame.filter(pl.struct("day", "code", "office", "seat", "remainder", "scope").is_duplicated())
    if duplicated.height:
        raise ValueError(f"contests that share a key: {duplicated.rows()[:10]}")
    frame.write_parquet(PARSED / "contests.parquet")
    merged.write_parquet(PARSED / "results.parquet")
    places.write_parquet(PARSED / "places.parquet")


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    results = pl.read_parquet(PARSED / "results.parquet")
    places = pl.read_parquet(PARSED / "places.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina's county, municipal, and school board elections, 2008–2025",
            polity="US-NC",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        for day in DAYS:
            cs.artifact(raw.cached(NCSBE, FILES.format(day=day)), source)
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        cs.artifact(raw.cached(SOURCE, PLACES), census)
        us = cs.polity("US")
        state = cs.polity("US-NC")
        polities = {
            code: (polity, text, path, "county")
            for code, polity, text, path in conn.execute(
                """select c.code, t.polity_id, n.text, s.path from area a
                   join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join slug s on s.entity_id = p.id
                   where a.scheme_code = 'us.county' and c.code like '37%'"""
            ).fetchall()
        }
        foreign = {
            table: dict(
                conn.execute(
                    f"""select s.path, s.entity_id from slug s join {table} t on t.id = s.entity_id
                        join changeset c on c.id = t.changeset_id
                        where s.path like 'us/nc/%%' and c.pipeline <> %s""",
                    [SOURCE],
                ).fetchall()
            )
            for table in ("election", "contest")
        }
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/nc/%'").fetchall()}
        for code, text, kind, geom, point in places.iter_rows():
            polity = cs.key("us.polity", f"place:{code}")
            path = f"us/nc/{slug(text)}"
            if path in taken and polity in cs.minted:
                raise ValueError(f"{text} would take {path}")
            polities[code] = (polity, text, path, kind)
            cs.add("polity", id=polity, kind="municipality", iso_code=None)
            name(polity, text)
            cs.add("polity_parent", polity_id=polity, parent_id=state)
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area("us.place", code)
            cs.add("area", id=area, scheme_code="us.place")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", "37"))
            name(area, f"{text}, NC")
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
                id=cs.unit("us.place", code),
                polity_id=polity,
                scheme_code="us.place",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        cs.add(
            "boundary_set",
            scheme_code="us.place",
            vintage="2020",
            valid_from=None,
            valid_to=None,
            as_of=date(2020, 1, 1),
        )
        systems = {"fptp": cs.key("us.electoral_system", "fptp"), "block": cs.key("us.electoral_system", "block")}
        cs.add("electoral_system", id=systems["block"], polity_id=us, rules={"formula": "block"}, rules_version=1)
        name(systems["block"], "Block vote")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {systems["fptp"], *parties.values()}:
            raise ValueError("the fptp system and the parties must already exist")
        cs.add("scheme", code="us.local_district", polity_id=us, kind="electoral_district", rank=3, noun="district")
        cs.add(
            "scheme", code="us.special_district", polity_id=us, kind="administrative", rank=3, noun="special district"
        )
        for code, text in contests.filter(pl.col("office") == "special").select("code", "word").unique().iter_rows():
            polity = cs.key("us.polity", f"special:{code}")
            path = f"{polities[code[:5]][2]}/{slug(text)}"
            if path in taken and polity in cs.minted:
                raise ValueError(f"{text} would take {path}")
            polities[code] = (polity, text, path, "district")
            cs.add("polity", id=polity, kind="special_district", iso_code=None)
            name(polity, text)
            cs.add("polity_parent", polity_id=polity, parent_id=polities[code[:5]][0])
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area("us.special_district", code)
            cs.add("area", id=area, scheme_code="us.special_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.county", code[:5]))
            name(area, f"{text}, NC")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "reporting_unit",
                id=cs.unit("us.special_district", code),
                polity_id=polity,
                scheme_code="us.special_district",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )

        owners: dict[tuple[str, str], UUID] = {}
        elections: dict[tuple[date, str], UUID] = {}
        persons: dict[UUID, str] = {}
        districts: set[str] = set()
        grouped = {keys: rows for keys, rows in results.group_by("day", "code", "contest")}
        precincts: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.precinct' and code like '20__:NC:%%'"
            ).fetchall()
        )
        parents: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '37%%'"
            ).fetchall()
        }
        sets = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = %s", [f"{SOURCE}.result_set"]
            ).fetchall()
        }
        present = {
            (day, code, office, seat, remainder, scope)
            for day, code, office, seat, remainder, scope in contests.select(
                "day", "code", "office", "seat", "remainder", "scope"
            ).iter_rows()
        }
        minted: dict[str, date] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        for row in contests.sort("day", "code", "office", "seat", descending=[True, False, False, False]).iter_rows(
            named=True
        ):
            day, code, office = row["day"], row["code"], row["office"]
            polity, place, path, kind = polities[code]
            town = len(code) == 7
            scheme = "us.special_district" if office == "special" else "us.place" if town else "us.county"
            if office == "special":
                title = f"{place} Board of {'Trustees' if place.endswith('Hospital') else 'Directors'}"
            elif office == "mayor":
                title = f"Mayor of {place}"
            elif office == "council":
                title = f"{place} {BOARDS[row['word']].format(kind=kind)}"
            elif office == "soil-and-water":
                title = f"{place.removesuffix(' County')} {OFFICES[office][1]}"
            else:
                title = f"{place} {OFFICES[office][1]}"
            general = day.year % 2 == 0
            kind_of = (
                ("general" if general else "local")
                if day.month == 11
                else "runoff"
                if day in SECOND
                else "special"
                if day in SPECIAL_DAYS
                else "primary"
            )
            label = {"general": "general", "local": "municipal", "runoff": "primary runoff"}.get(kind_of, kind_of)
            contest_path = (
                f"{path}/{day}-{kind_of}/{office}"
                + (f"-{slug(row['seat'])}" if row["seat"] else "")
                + ("-unexpired" if row["remainder"] else "")
                + (f"-{row['scope'].lower()}" if row["scope"] else "")
            )
            if contest_path in foreign["contest"]:
                continue
            if (code, office) not in owners:
                owner = owners[(code, office)] = cs.key(
                    "us.body" if office in BODIES else "us.office", f"NC:{code}:{office}"
                )
                if office in BODIES:
                    cs.add("body", id=owner, polity_id=polity, kind="council")
                else:
                    cs.add("office", id=owner, polity_id=polity, kind="mayor" if office == "mayor" else "other")
                name(owner, title)
            if not general and (day, "37") not in elections:
                umbrella = elections[(day, "37")] = cs.key("us.election", f"{day}:NC")
                cs.add(
                    "election",
                    id=umbrella,
                    polity_id=state,
                    kind="local" if kind_of == "primary" else kind_of,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(umbrella, f"{day.year} North Carolina municipal {label} elections")
                cs.add("slug", path=f"us/nc/{day}-{'local' if kind_of == 'primary' else kind_of}", entity_id=umbrella)
            if (day, code) not in elections and f"{path}/{day}-{kind_of}" in foreign["election"]:
                elections[(day, code)] = foreign["election"][f"{path}/{day}-{kind_of}"]
            if (day, code) not in elections:
                election = elections[(day, code)] = cs.key("us.election", f"{day}:NC:{code}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=cs.key("us.election", f"{day}:NC") if day.month == 11 or not general else None,
                    kind=kind_of,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} {place} {label} election")
                cs.add("slug", path=f"{path}/{day}-{kind_of}", entity_id=election)
            election = elections[(day, code)]
            whole = area = cs.area(scheme, code)
            unit = cs.unit(scheme, code)
            seat = row["seat"]
            if row["district"]:
                district = f"37:{code}:{office}:{slug(seat or 'whole')}"
                area = cs.area("us.local_district", district)
                unit = cs.unit("us.local_district", district)
                if district not in districts:
                    districts.add(district)
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=district, valid_from=None)
                    cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                    name(area, f"{title}, {seat}" if seat else title)
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
            contest_key = (
                f"{day}:NC:{code}:{office}"
                + (f":{slug(seat)}" if seat else "")
                + (":remainder" if row["remainder"] else "")
                + (f":{row['scope'].lower()}" if row["scope"] else "")
            )
            contest = cs.key("us.contest", contest_key)
            magnitude = row["magnitude"]
            first_day = SECOND.get(day)
            previous_round = (
                cs.key("us.contest", contest_key.replace(str(day), str(first_day), 1))
                if first_day is not None and (first_day, code, office, seat, row["remainder"], row["scope"]) in present
                else None
            )
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if office in BODIES else "office",
                office_id=None if office in BODIES else owners[(code, office)],
                body_id=owners[(code, office)] if office in BODIES else None,
                district_area_id=area if row["district"] else whole,
                magnitude=magnitude,
                round_number=2 if first_day else 1,
                previous_round_id=previous_round,
                party_scope_id=parties.get(row["scope"]),
                electoral_system_id=None if magnitude is None else systems["block" if magnitude > 1 else "fptp"],
                default_result_kind="counted",
                term="remainder" if row["remainder"] else "full",
            )
            name(
                contest,
                (f"{day.year} {place} mayoral election" if office == "mayor" else f"{day.year} {title} election")
                + (f", {seat}" if seat else "")
                + (" (unexpired term)" if row["remainder"] else "")
                + (f" ({SCOPES[row['scope']]} primary)" if row["scope"] else ""),
            )
            cs.add(
                "slug",
                path=contest_path,
                entity_id=contest,
            )
            if row["district"]:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="caveat",
                    body_md=DISTRICT.format(whole=kind.lower() if town else "county"),
                )
            previous = None
            result_set = cs.key(f"{SOURCE}.result_set", contest_key) if town or contest_key in sets else None
            if not town:
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
                        source_id=source,
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
                source_id=source,
                is_default=True,
                supersedes_id=previous,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)

            counted = grouped[(day, code, row["contest"])].with_columns(
                writein=pl.col("choice").str.contains(r"(?i)write-?\s*in")
            )
            merged_choices: dict[UUID, str] = {}
            canon = {
                choice: merged_choices.setdefault(cs.key(f"{SOURCE}.person", f"{code}:{slug(choice)}"), choice)
                for choice in counted.filter(~pl.col("writein"))["choice"].unique().sort()
            }
            counted = counted.with_columns(pl.col("choice").replace(canon))
            lines = (
                counted.group_by(
                    pl.when("writein").then(pl.lit("Write-ins")).otherwise("choice").alias("choice"), "writein"
                )
                .agg(pl.col("party").first(), pl.col("votes").sum())
                .sort("votes", "choice", descending=[True, False])
            )
            options: dict[str, UUID] = {}
            rank = 0
            for choice, writein, party, votes in lines.iter_rows():
                if writein:
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                else:
                    rank += 1
                    person = cs.key(f"{SOURCE}.person", f"{code}:{slug(choice)}")
                    if person not in persons:
                        persons[person] = choice
                        cs.add("person", id=person)
                        name(person, choice)
                    option = cs.key("us.option", f"{contest_key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(party),
                        ballot_label=choice,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=choice,
                        is_independent=party == "UNA",
                    )
                    if magnitude is not None and rank <= magnitude and kind_of in ("general", "local", "special"):
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=None)
                options[choice] = option
                if result_set in cs.minted and town:
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
            if result_set in cs.minted and not town:
                cells: dict[tuple[UUID, UUID], int] = {}
                for choice, writein, place, votes in counted.select("choice", "writein", "unit", "votes").iter_rows():
                    target = unit
                    if place is not None:
                        target = precincts.get(place) or cs.unit("us.precinct", place)
                        if place not in precincts:
                            minted.setdefault(place, day)
                        if row["district"] or office == "special":
                            memberships.add((target, area, day))
                    cell = (target, options["Write-ins" if writein else choice])
                    cells[cell] = cells.get(cell, 0) + (votes or 0)
                for (target, option), votes in cells.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=target,
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
        for place, day in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", place),
                polity_id=state,
                scheme_code="us.precinct",
                code=place,
                parent_id=parents[place.split(":")[2]][0],
                area_relation="within",
                area_id=parents[place.split(":")[2]][1],
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
        return cs.write()
