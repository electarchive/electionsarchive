import io
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl
import shapefile
from pyproj import CRS

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.vest"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
ACCESS = "https://dataverse.harvard.edu/api/access/datafile/"
DATASET = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/{doi}"
YEARS = {2016: ("NH5S2I", date(2016, 11, 8)), 2018: ("UBKYRU", date(2018, 11, 6)), 2020: ("K7760H", date(2020, 11, 3))}
STATE_FILE = re.compile(r"^([a-z]{2})(?:_\d{4})?\.zip$")
VOTE = re.compile(r"^([GS])(\d\d)([A-Z0-9]{3})([A-Z])([A-Z0-9]{2,3})$")
PUBLISHER = "Voting and Election Science Team"
GEORGIA = "2020-11-03:GA:president"
OFFICES = {
    "PRE": ("president",),
    "USS": ("senate", "senate:special"),
    "GOV": ("governor", "governor:special"),
    "LTG": ("lieutenant-governor",),
    "ATG": ("attorney-general",),
    "SOS": ("secretary-of-state", "secretary-of-the-commonwealth", "secretary-of-the-state"),
    "S0S": ("secretary-of-state",),
    "TRE": ("treasurer",),
    "AUD": ("auditor", "auditor-general", "public-auditor"),
    "CON": ("controller", "comptroller"),
    "AGR": ("commissioner-of-agriculture",),
    "INS": ("commissioner-of-insurance", "insurance-commissioner"),
    "LAB": ("commissioner-of-labor",),
    "LND": ("commissioner-of-public-lands",),
    "COC": ("corporation-commissioner",),
    "PUC": ("public-utilities-commissioner",),
    "SPI": ("superintendent-of-public-instruction",),
    "RGT": ("regent-of-the-university-of-colorado",),
    "HAL": ("house:0",),
    "DEL": ("house:0",),
}
NAMES = (
    "PRECINCT_N",
    "PRECINCTNA",
    "PRECNAME",
    "PCTNAME",
    "WP_NAME",
    "ENR_DESC",
    "NAME20",
    "NAME18",
    "NAME16",
    "NAME10",
    "NAMELSAD",
    "NAME",
    "LABEL",
    "MUNINAME",
    "PRECINCT",
    "SRPREC",
    "PREC",
    "PCT_CEB",
    "PRECINCTNU",
    "P16",
    "P18",
    "PRCCODE",
    "ED_KEY",
)
PARTIES = {"R": "republican", "D": "democratic", "L": "libertarian", "G": "green", "C": "constitution"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
METHOD = (
    "The Voting and Election Science Team compiled these precinct returns from the state's and counties' official "
    "results and joined them to precinct shapes. Where absentee, early, or provisional ballots were reported only "
    "countywide, VEST distributed them among the county's precincts in proportion to each precinct's reported vote, "
    "so those precinct figures are estimates. Each state's sources and adjustments are listed in VEST's "
    "documentation file."
)


def plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def files(year: int) -> dict[str, str]:
    listing = json.loads(raw.cached(SOURCE, DATASET.format(doi=YEARS[year][0])).path.read_text())
    return {
        match[1].upper() if match else "documentation": str(entry["dataFile"]["id"])
        for entry in listing["data"]["latestVersion"]["files"]
        if (match := STATE_FILE.match(entry["dataFile"]["filename"]))
        or entry["dataFile"]["filename"] == "documentation.txt"
    }


def fetch() -> None:
    for year, (doi, _) in YEARS.items():
        raw.fetch(SOURCE, DATASET.format(doi=doi), suffix=".json")
        for state, file in files(year).items():
            raw.fetch(SOURCE, ACCESS + file, suffix=".txt" if state == "documentation" else ".zip")


def read(file: str) -> tuple[list[str], list[tuple[dict[str, object], object]], int]:
    with zipfile.ZipFile(raw.cached(SOURCE, ACCESS + file).path) as archive:
        parts = {
            name.rsplit(".", 1)[1].lower(): archive.read(name)
            for name in archive.namelist()
            if name.rsplit(".", 1)[-1].lower() in ("shp", "shx", "dbf", "prj") and not name.startswith("__MACOSX")
        }
    srid = CRS(parts["prj"].decode("latin-1")).to_epsg(min_confidence=20)
    assert srid is not None
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    fields = [field[0].upper() for field in reader.fields[1:]]
    rows: list[tuple[dict[str, object], object]] = [
        (
            dict(zip(fields, list(record), strict=True)),
            None if shape.shapeType == shapefile.NULL or not shape.points else shape.__geo_interface__,
        )
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
        if record is not None and shape is not None
    ]
    return fields, rows, srid


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    fields, rows, srid = read(files(2020)["GA"])
    shapes = pl.DataFrame(
        [
            (
                f"13{record['FIPS2']}",
                str(record["PRECINCT_I"]),
                str(record["PRECINCT_N"]),
                int(str(record["G20PRERTRU"])),
                int(str(record["G20PREDBID"])),
                json.dumps(geometry),
            )
            for record, geometry in rows
            if geometry is not None
        ],
        schema=["county", "id", "name", "rep", "dem", "geojson"],
        orient="row",
    ).with_row_index("polygon")
    with connect() as conn:
        units = pl.DataFrame(
            conn.execute(
                """select u.code, left(u.code, 5),
                          sum(v.value) filter (where p.text = 'Republican Party'),
                          sum(v.value) filter (where p.text = 'Democratic Party')
                   from external_id x join result_set r on r.contest_id = x.entity_id
                   join result_value v on v.result_set_k = r.k and v.measure = 1
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct' and u.code like '13%%'
                   join contest_option o on o.k = v.option_k
                   join name p on p.entity_id = o.party_id and p.is_preferred and p.lang = 'en'
                   where x.scheme = 'us.contest' and x.value = %s
                   group by u.code""",
                [GEORGIA],
            ).fetchall(),
            schema={"code": pl.String, "county": pl.String, "rep": pl.Int64, "dem": pl.Int64},
            orient="row",
        ).with_columns(precinct=pl.col("code").str.slice(6))
    counted = shapes.join(units, on=["county", "rep", "dem"]).filter(
        (pl.col("rep") + pl.col("dem") > 0) & (pl.len().over("county", "rep", "dem") == 1)
    )
    keyed = units.with_columns(key=pl.col("precinct").map_elements(plain, return_dtype=pl.String))
    named = pl.concat(
        [
            shapes.with_columns(key=pl.col(column).map_elements(plain, return_dtype=pl.String)).join(
                keyed, on=["county", "key"], suffix="_unit"
            )
            for column in ("name", "id")
        ]
    ).unique(["polygon", "code"])
    matched = (
        pl.concat(
            [
                counted.select("polygon", "code", pl.lit("votes").alias("by")),
                named.select("polygon", "code", pl.lit("name").alias("by")),
            ]
        )
        .unique(["polygon", "code"], keep="first", maintain_order=True)
        .filter(pl.len().over("polygon") == 1, pl.len().over("code") == 1)
    )
    print(
        f"Georgia 2020: {matched.height} of {units.height} precincts matched to {shapes.height} shapes "
        f"({matched.filter(pl.col('by') == 'votes').height} by their presidential votes)"
    )
    found = shapes.join(matched, on="polygon")
    with connect() as conn:
        pl.DataFrame(
            conn.execute(
                """select code, name, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select code, name, st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(geojson), %s), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[], %s::text[]) as f(code, name, geojson)) shapes""",
                [srid, found["code"].to_list(), found["name"].to_list(), found["geojson"].to_list()],
            ).fetchall(),
            schema=["code", "name", "geom", "point"],
            orient="row",
        ).write_parquet(PARSED / "georgia.parquet")

    for year in YEARS:
        votes = []
        with connect() as conn:
            conn.execute("create temp table shape (state text, n int, name text, srid int, geojson text)")
            for state, file in files(year).items():
                if state == "documentation" or (year == 2020 and state == "GA"):
                    continue
                fields, rows, srid = read(file)
                columns = [
                    field
                    for field in fields
                    if (match := VOTE.match(field))
                    and match[2] == str(year)[2:]
                    and (match[3] in OFFICES or re.fullmatch(r"H\d\d", match[3]))
                ]
                name = next((field for field in NAMES if field in fields), None)
                empty = 0
                with conn.cursor().copy("copy shape (state, n, name, srid, geojson) from stdin") as copy:
                    for n, (record, geometry) in enumerate(rows, 1):
                        if geometry is None:
                            empty += 1
                            continue
                        text = "" if name is None else str(record[name] or "").strip()
                        copy.write_row((state, n, text, srid, json.dumps(geometry)))
                        votes += [(state, n, column, round(float(str(record[column] or 0)))) for column in columns]
                print(
                    f"{year} {state}: {len(rows)} shapes, {empty} without geometry, {len(columns)} vote columns",
                    flush=True,
                )
            conn.execute(
                """create temp table county as
                   select u.code, left(u.code, 2) as fips, g.geom from reporting_unit u
                   join area_geometry g on g.area_id = u.area_id where u.scheme_code = 'us.county'"""
            )
            conn.execute("create index on county using gist (geom)")
            conn.execute(
                """create temp table shaped as
                   select state, n, name, fips, geom, st_pointonsurface(geom) as point
                   from (select s.state, s.n, s.name, u.code as fips, st_multi(st_collectionextract(st_makevalid(
                                st_simplifypreservetopology(st_transform(st_setsrid(st_geomfromgeojson(s.geojson),
                                s.srid), 4326), 0.00001)), 3)) as geom
                         from shape s
                         join polity p on p.iso_code = 'US-' || s.state
                         join polity_territory t on t.polity_id = p.id
                         join reporting_unit u on u.area_id = t.area_id and u.scheme_code = 'us.state') shapes
                   where not st_isempty(geom)"""
            )
            pl.DataFrame(
                conn.execute(
                    """select p.state, p.n, p.name, coalesce(
                                (select c.code from county c where c.fips = p.fips and st_intersects(c.geom, p.point)
                                 limit 1),
                                (select c.code from county c where c.fips = p.fips order by c.geom <-> p.point limit 1)),
                              encode(st_asewkb(p.geom), 'hex'), encode(st_asewkb(p.point), 'hex')
                       from shaped p"""
                ).fetchall(),
                schema={
                    "state": pl.String,
                    "n": pl.Int64,
                    "name": pl.String,
                    "county": pl.String,
                    "geom": pl.String,
                    "point": pl.String,
                },
                orient="row",
            ).write_parquet(PARSED / f"precincts_{year}.parquet")
        pl.DataFrame(
            votes, schema={"state": pl.String, "n": pl.Int64, "column": pl.String, "votes": pl.Int64}, orient="row"
        ).write_parquet(PARSED / f"votes_{year}.parquet")


def emit() -> Path:
    georgia = pl.read_parquet(PARSED / "georgia.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Precinct results and shapes for 2016, 2018, and 2020 from the Voting and Election Science Team",
            polity="US",
        )
        sources: dict[int, UUID] = {}
        for year, (doi, _) in YEARS.items():
            title = f"{year} Precinct-Level Election Results"
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="academic",
                publisher=PUBLISHER,
                title=title,
                url=f"https://doi.org/10.7910/DVN/{doi}",
                license="CC BY 4.0",
                license_url="https://creativecommons.org/licenses/by/4.0/",
            )
            cs.artifact(raw.cached(SOURCE, DATASET.format(doi=doi)), sources[year])
            for file in files(year).values():
                cs.artifact(raw.cached(SOURCE, ACCESS + file), sources[year])
        counties = {
            code: (area, unit, text.rsplit(",", 1)[0])
            for code, area, unit, text in conn.execute(
                """select u.code, u.area_id, u.id, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'official' and n.is_preferred
                   where u.scheme_code = 'us.county'"""
            ).fetchall()
        }
        contests: dict[str, UUID] = dict(
            conn.execute(
                """select x.value, x.entity_id from external_id x
                   where x.scheme = 'us.contest' and left(x.value, 10) = any(%s)""",
                [[str(day) for _, day in YEARS.values()]],
            ).fetchall()
        )
        options: dict[UUID, list[tuple[UUID, str, list[str], str]]] = {}
        for contest, option, kind, words, party in conn.execute(
            """select o.contest_id, o.id, o.kind, concat_ws(' ', n.text, o.ballot_label), coalesce(p.text, '')
               from contest_option o
               left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               left join name n on n.entity_id = cy.person_id and n.is_preferred
               left join name p on p.entity_id = o.party_id and p.is_preferred and p.lang = 'en'
               where o.contest_id = any(%s) and o.parent_option_id is null""",
            [list(contests.values())],
        ).fetchall():
            options.setdefault(contest, []).append(
                (
                    option,
                    kind,
                    [word for word in slug(re.sub(r"['’]", "", words)).split("-") if word not in SUFFIXES],
                    party.lower(),
                )
            )
        official: dict[UUID, tuple[UUID, int]] = {}
        for contest, election, total in conn.execute(
            """select distinct on (c.id) c.id, c.election_id, sum(v.value)
               from contest c join result_set rs on rs.contest_id = c.id and rs.is_default
               join result_value v on v.result_set_k = rs.k and v.measure = 1
               join reporting_unit u on u.k = v.unit_k
               where c.id = any(%s)
               group by c.id, c.election_id, u.scheme_code
               order by c.id, array_position(array['us.state', 'us.cd', 'us.county']::text[], u.scheme_code)""",
            [list(contests.values())],
        ).fetchall():
            official[contest] = (election, total)

        day = YEARS[2020][1]
        cs.add(
            "boundary_set",
            scheme_code="us.precinct",
            vintage="2020",
            valid_from=day,
            valid_to=None,
            as_of=day,
        )
        areas: list[tuple[UUID, str, UUID, str]] = []
        for code, text, geom, point in georgia.select("code", "name", "geom", "point").iter_rows():
            area = cs.area("us.precinct", code)
            areas.append(
                (
                    area,
                    code,
                    counties[code[:5]][0],
                    f"{re.sub(r'\w+', lambda word: word[0] if re.search(r'\d', word[0]) else word[0].title(), text)}, {counties[code[:5]][2]}",
                )
            )
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=day,
                valid_to=day + timedelta(days=1),
                geom=geom,
                label_point=point,
                quality="academic",
                source_id=sources[2020],
            )

        report = []
        for year, (_, day) in YEARS.items():
            precincts = pl.read_parquet(PARSED / f"precincts_{year}.parquet")
            votes = pl.read_parquet(PARSED / f"votes_{year}.parquet")
            if year != 2020:
                cs.add(
                    "boundary_set",
                    scheme_code="us.precinct",
                    vintage=str(year),
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                    as_of=day,
                )
            units: dict[tuple[str, int], UUID] = {}
            for state, n, text, county in precincts.select("state", "n", "name", "county").iter_rows():
                code = f"{year}:{state}:{n}"
                county_area, _, county_name = counties[county]
                label = (
                    re.sub(r"\w+", lambda word: word[0] if re.search(r"\d", word[0]) else word[0].title(), text)
                    if text.isupper()
                    else text
                )
                label = f"Precinct {label or n}" if label.isdigit() or not label else label
                areas.append((cs.area("us.precinct", code), code, county_area, f"{label}, {county_name}"))
                units[(state, n)] = cs.unit("us.precinct", code)
            keys = pl.concat_str("state", pl.lit(":"), "n")
            cs.frame(
                "reporting_unit",
                precincts.select(
                    id=keys.replace_strict({f"{state}:{n}": str(id) for (state, n), id in units.items()}),
                    polity_id=pl.concat_str(pl.lit("US-"), "state").replace_strict(
                        {f"US-{state}": str(cs.polity(f"US-{state}")) for state in precincts["state"].unique()}
                    ),
                    scheme_code=pl.lit("us.precinct"),
                    code=pl.concat_str(pl.lit(f"{year}:"), "state", pl.lit(":"), "n"),
                    parent_id=pl.col("county").replace_strict(
                        {code: str(unit) for code, (_, unit, _) in counties.items()}
                    ),
                    area_relation=pl.lit("equals"),
                    area_id=keys.replace_strict(
                        {code.split(":", 1)[1]: str(area) for area, code, _, _ in areas if code.startswith(f"{year}:")}
                    ),
                    kind=pl.lit("standard"),
                    valid_from=pl.lit(day),
                    valid_to=pl.lit(day + timedelta(days=1)),
                ),
            )
            cs.frame(
                "area_geometry",
                precincts.select(
                    area_id=keys.replace_strict(
                        {code.split(":", 1)[1]: str(area) for area, code, _, _ in areas if code.startswith(f"{year}:")}
                    ),
                    valid_from=pl.lit(day),
                    valid_to=pl.lit(day + timedelta(days=1)),
                    geom="geom",
                    label_point="point",
                    quality=pl.lit("academic"),
                    source_id=pl.lit(str(sources[year])),
                ),
            )

            columns: dict[str, tuple[str, UUID]] = {}
            for state, column in votes.select("state", "column").unique().sort("state", "column").iter_rows():
                match = VOTE.match(column)
                assert match is not None
                prefix, _, office, party, surname = match.groups()
                suffixes = (
                    (f"house:{int(office[1:])}",)
                    if re.fullmatch(r"H\d\d", office)
                    else OFFICES[office][1:]
                    if prefix == "S" and office in ("USS", "GOV")
                    else OFFICES[office]
                )
                key = next(
                    (f"{day}:{state}:{suffix}" for suffix in suffixes if f"{day}:{state}:{suffix}" in contests), None
                )
                if key is None or (state == "DC" and office == "HAL"):
                    continue
                surname = surname.lower()
                candidates = options.get(contests[key], [])
                others = next(
                    (
                        option
                        for option, kind, words, _ in candidates
                        if kind == "others" or words in (["scattered"], ["write", "ins"], ["write", "in"])
                    ),
                    None,
                )
                scores = sorted(
                    (
                        (
                            name > 0,
                            not (
                                party in PARTIES
                                and PARTIES[party] not in label
                                and any(text in label for text in PARTIES.values())
                            ),
                            name,
                            PARTIES.get(party, "-") in label,
                            option,
                        )
                        for option, _, words, label in candidates
                        if option != others
                        for name in [
                            3
                            if words[-1:] and words[-1].startswith(surname)
                            else 2
                            if any(word.startswith(surname) for word in words)
                            else 0
                        ]
                    ),
                    key=lambda score: score[:4],
                    reverse=True,
                )
                if party in "OW" and surname in ("wri", "oth", "th"):
                    chosen = others
                elif scores and scores[0][0] and (len(scores) == 1 or scores[0][:4] != scores[1][:4]):
                    chosen = scores[0][4]
                elif not scores or not scores[0][0]:
                    chosen = others
                else:
                    chosen = None
                if chosen is None:
                    print(f"{year} {state}: {column} matches no single option of {key}")
                else:
                    columns[f"{state}:{column}"] = (key, chosen)
            mapped = (
                votes.with_columns(column=pl.concat_str("state", pl.lit(":"), "column"))
                .filter(pl.col("column").is_in(list(columns)))
                .join(precincts.select("state", "n", "county"), on=["state", "n"])
                .with_columns(
                    contest=pl.col("column").replace_strict({column: key for column, (key, _) in columns.items()}),
                    option=pl.col("column").replace_strict(
                        {column: str(option) for column, (_, option) in columns.items()}
                    ),
                )
            )
            result_sets: dict[str, UUID] = {}
            for key, total in mapped.group_by("contest").agg(pl.col("votes").sum()).sort("contest").iter_rows():
                election, expected = official.get(contests[key], (None, None))
                if election is None:
                    (election,) = conn.execute(
                        "select election_id from contest where id = %s", [contests[key]]
                    ).fetchone() or (None,)
                result_sets[key] = cs.key(f"{SOURCE}.result_set", key)
                cs.add(
                    "result_set",
                    id=result_sets[key],
                    election_id=election,
                    contest_id=contests[key],
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=sources[year],
                    is_default=False,
                    completeness=round(total / expected, 4) if expected else None,
                )
                cs.add("note", entity_id=result_sets[key], kind="methodology", body_md=METHOD)
                report.append((key, total, expected))
            minted = [key for key, result_set in result_sets.items() if result_set in cs.minted]
            cs.frame(
                "result_value",
                mapped.filter(pl.col("contest").is_in(minted))
                .group_by("contest", "state", "n", "option")
                .agg(pl.col("votes").sum())
                .select(
                    result_set_id=pl.col("contest").replace_strict({key: str(id) for key, id in result_sets.items()}),
                    unit_id=pl.concat_str("state", pl.lit(":"), "n").replace_strict(
                        {f"{state}:{n}": str(id) for (state, n), id in units.items()}
                    ),
                    option_id="option",
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="votes",
                ),
            )
            mapped.group_by("contest", "county", "option").agg(pl.col("votes").sum()).write_parquet(
                PARSED / f"counties_{year}.parquet"
            )

        ids = [str(area) for area, _, _, _ in areas]
        cs.frame("area", pl.DataFrame({"id": ids, "scheme_code": "us.precinct"}))
        cs.frame(
            "area_code",
            pl.DataFrame({"area_id": ids, "code": [code for _, code, _, _ in areas]}).with_columns(
                valid_from=pl.lit(None, pl.Date)
            ),
        )
        cs.frame(
            "area_parent",
            pl.DataFrame({"area_id": ids, "parent_id": [str(parent) for _, _, parent, _ in areas]}).with_columns(
                valid_from=pl.lit(None, pl.Date)
            ),
        )
        cs.frame(
            "name",
            pl.DataFrame({"entity_id": ids, "text": [text for _, _, _, text in areas]})
            .join(
                pl.DataFrame(
                    conn.execute(
                        """select n.id::text, n.entity_id::text, n.text from name n join changeset c on c.id = n.changeset_id
                           where c.pipeline = %s and n.kind = 'official' and n.lang = 'en'""",
                        [SOURCE],
                    ).fetchall(),
                    schema={"id": pl.String, "entity_id": pl.String, "old": pl.String},
                    orient="row",
                ),
                on="entity_id",
                how="left",
            )
            .sort(pl.col("old") == pl.col("text"), descending=True, nulls_last=True)
            .with_columns(first=pl.int_range(pl.len()).over("entity_id") == 0)
            .select(
                "id",
                "entity_id",
                text=pl.when("first").then("text").otherwise("old"),
                is_preferred="first",
                lang=pl.lit("en"),
                kind=pl.lit("official"),
            ),
        )
        pl.DataFrame(report, schema=["contest", "vest", "official"], orient="row").write_parquet(
            PARSED / "reconciliation.parquet"
        )
        return cs.write()
