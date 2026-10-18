import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl_statewide
from ea.store import DATA, connect

SOURCE = "us.mn_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MANUAL = Path.home() / "Downloads" / "ea-manual" / "minnesota"
MEDIA = "https://www.sos.state.mn.us/media/"
FILES = {
    2002: "1548/2002_general_results.xls",
    2004: "1688/2004_general_results.xls",
    2006: "1694/2006_general_results.xls",
    2008: "1695/2008_general_results.xls",
    2010: "1696/2010_general_results_final.xls",
}
PLACES = {2002: ["MCD NAME", "WD", "PRECINCT NAME"], 2006: ["MCDName", "WD", "Precinct Name"], 2010: ["Precinct Name"]}
COUNTY = {2002: "CC", 2006: "County_ID", 2010: "CountyID"}
R, DFL, IP, GP = "REPUBLICAN", "DEMOCRATIC-FARMER-LABOR", "INDEPENDENCE", "GREEN"
CANDIDATES = {
    (2002, "Secretary of State"): [
        ("SOSGP", "ANDREW S. KOEBRICK", GP, 67404),
        ("SOSIP", "DEAN ALGER", IP, 104799),
        ("SOSR", "MARY KIFFMEYER", R, 1040739),
        ("SOSDFL", 'HUBERT H. "BUCK" HUMPHREY', DFL, 974045),
        ("SOSWI", None, None, None),
    ],
    (2002, "Auditor"): [
        ("AudGP", "DAVE BERGER", GP, 78611),
        ("AudIP", "DAVE HUTCHESON", IP, 164532),
        ("AudR", "PATRICIA ANDERSON AWADA", R, 956104),
        ("AudDFL", "CAROL JOHNSON", DFL, 941129),
        ("AudWI", None, None, None),
    ],
    (2002, "Attorney General"): [
        ("AGIP", "DALE NATHAN", IP, 96817),
        ("AGR", "TOM KELLY", R, 894654),
        ("AGDFL", "MIKE HATCH", DFL, 1197362),
        ("AGWI", None, None, None),
    ],
    (2006, "Secretary of State"): [
        ("SOSIP", "JOEL SPOONHEIM", IP, 64489),
        ("SOSR", "MARY KIFFMEYER", R, 943989),
        ("SOSDFL", "MARK RITCHIE", DFL, 1049431),
        ("SOSTOT", "BRUCE KENNEDY", "FOR INDEPENDENT VOTERS", 78522),
        ("SOSWI", None, None, 1211),
    ],
    (2006, "Auditor"): [
        ("STAUDIP", "LUCY GEROLD", IP, 97076),
        ("STAUDR", "PATRICIA ANDERSON", R, 866041),
        ("STAUDDFL", "REBECCA OTTO", DFL, 1094439),
        ("STAUDTOT", "DAVE BERGER", GP, 49131),
        ("STAUDWI", None, None, 1316),
    ],
    (2006, "Attorney General"): [
        ("AttGenIP", "JOHN JAMES", IP, 86032),
        ("AttGenR", "JEFF JOHNSON", R, 865465),
        ("AttGenDFL", "LORI SWANSON", DFL, 1131473),
        ("AttGenTOT", "PAPA JOHN KOLSTAD", GP, 41000),
        ("AttGenWI", None, None, 1238),
    ],
    (2010, "Secretary of State"): [
        ("SOSIP", "JUAL CARLSON", IP, 105675),
        ("SOSR", "DAN SEVERSON", R, 928913),
        ("SOSDFL", "MARK RITCHIE", DFL, 999382),
        ("SOSWI", None, None, 1395),
    ],
    (2010, "Auditor"): [
        ("STAUDR", 'PATRICIA "PAT" ANDERSON', R, 956339),
        ("STAUDDFL", "REBECCA OTTO", DFL, 981822),
        ("STAUDGP", "ANNIE YOUNG", GP, 54154),
        ("STAUDGR", "KENNY KALLIGHER", "GRASSROOTS", 35548),
        ("STAUDWI", None, None, 1224),
    ],
    (2010, "Attorney General"): [
        ("ATGENIP", "BILL DAHN", IP, 102865),
        ("ATGENR", "CHRIS BARDEN", R, 839033),
        ("ATGENDFL", "LORI SWANSON", DFL, 1075536),
        ("ATGENTRP", "DAVID HOCH", "RESOURCE", 14040),
        ("ATGENWI", None, None, 1613),
    ],
}
CLAY = {"Secretary of State": 9368, "Auditor": 9643, "Attorney General": 10512}
WAYBACK = "https://web.archive.org/web/{stamp}id_/https://officialdocuments.sos.mn.gov/Files/GetDocument/{document}"
REPORTS = {
    2006: WAYBACK.format(stamp="20250318233302", document=78135),
    2010: WAYBACK.format(stamp="20250318232333", document=103107),
}
LEGISLATIVE = "https://www.lrl.mn.gov/docs/2013/other/130566.pdf"
DECLARATIONS = {
    2002: (LEGISLATIVE, {"Secretary of State": [26, 328], "Auditor": [26, 328], "Attorney General": [26, 328]}),
    2006: (REPORTS[2006], {"Secretary of State": [1, 74], "Auditor": [1, 78], "Attorney General": [1, 82]}),
    2010: (REPORTS[2010], {"Secretary of State": [67], "Auditor": [70, 74], "Attorney General": [77, 80]}),
}
SOURCES = {
    "results": (
        "Minnesota Secretary of State",
        "Precinct results spreadsheets",
        "https://www.sos.state.mn.us/elections-voting/election-results/",
    ),
    "board": (
        "Minnesota State Canvassing Board",
        "State canvassing board reports",
        "https://officialdocuments.sos.mn.gov/",
    ),
    "manual": ("Minnesota Secretary of State", "Minnesota Legislative Manual 2003–2004", LEGISLATIVE),
}
METHOD = (
    "Precinct votes are sourced from the Minnesota Secretary of State's precinct results spreadsheets. County totals "
    "are the sums of these precincts. Each candidate's statewide total matches the State Canvassing Board's report "
    "(2006 and 2010) or the 2003–2004 Legislative Manual (2002); the Manual does not list write-ins. Because the 2006 "
    "spreadsheet lacks a column for minor-party candidates, their precinct votes are calculated by subtracting all "
    "other candidate columns from the spreadsheet's total column."
)
CLAY_NOTE = (
    "The 2006 spreadsheet records one more vote for the DFL candidate in Clay County than the State Canvassing Board's "
    "report. County totals follow the report, but these precinct votes follow the spreadsheet, resulting in one extra "
    "vote for that candidate."
)

DERIVED = (
    "The 2006 precinct spreadsheet does not include a column for {name}. These precinct votes are omitted from this "
    "set and appear only in the county totals."
)


def fetch() -> None:
    for path in FILES.values():
        try:
            raw.cached(SOURCE, MEDIA + path)
        except LookupError:
            local = MANUAL / path.rpartition("/")[2]
            raw.store(
                SOURCE,
                MEDIA + path,
                local.read_bytes(),
                suffix=".xls",
                retrieved_at=datetime.fromtimestamp(local.stat().st_mtime, UTC),
                media_type="application/vnd.ms-excel",
            )
    for url in [*REPORTS.values(), LEGISLATIVE]:
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    rows: list[tuple[int, str, str, str, str | None, str | None, bool, int]] = []
    for year, places in PLACES.items():
        cells = pl.read_excel(
            raw.cached(SOURCE, MEDIA + FILES[year]).path,
            sheet_name="Results",
            engine="calamine",
            has_header=False,
            infer_schema_length=0,
        ).rows()
        start = 2 if year == 2002 else 0
        header = [re.sub(r"\s+", " ", str(cell)).strip() for cell in cells[start]]
        column = {name: index for index, name in enumerate(header)}
        for row in cells[start + 1 :]:
            if row[0] is None or row[0] == cells[start][0] or str(row[0]).upper().startswith("TOTAL"):
                continue
            county = "27" + str(int(row[column[COUNTY[year]]]) * 2 - 1).zfill(3)
            precinct = re.sub(
                r"\s+", " ", " ".join(str(row[column[name]]) for name in places if row[column[name]] is not None)
            ).strip()
            for (when, office), candidates in CANDIDATES.items():
                if when != year:
                    continue
                for field, name, party, _ in candidates:
                    votes = int(row[column[field]] or 0)
                    if field.endswith("TOT"):
                        votes -= sum(int(row[column[other]] or 0) for other, *_ in candidates if other != field)
                        if votes < 0:
                            raise ValueError(f"{year} {office} {precinct}: the total column is short of the others")
                    rows.append((year, office, county, precinct, name, party, name is None, votes))
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "title": pl.String,
            "county": pl.String,
            "precinct": pl.String,
            "candidate": pl.String,
            "party": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        candidate=pl.col("candidate").fill_null("WRITE-INS"),
        day=pl.col("year").map_elements(
            lambda year: date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7), return_dtype=pl.Date
        ),
    )
    if frame.select("year", "title", "county", "precinct", "candidate").is_duplicated().any():
        raise ValueError("two precinct rows share a county and name")
    returns = (
        frame.group_by("year", "day", "title", "county", "candidate", "party", "writein", maintain_order=True)
        .agg(pl.col("votes").sum())
        .with_columns(
            votes=pl.when(
                pl.col("year") == 2006,
                pl.col("county") == "27027",
                pl.col("party") == DFL,
            )
            .then(pl.col("title").replace_strict(CLAY, return_dtype=pl.Int64))
            .otherwise("votes")
        )
    )
    for (year, office), candidates in CANDIDATES.items():
        for _, name, _, official in candidates:
            counted = returns.filter(
                pl.col("year") == year, pl.col("title") == office, pl.col("candidate") == (name or "WRITE-INS")
            )["votes"].sum()
            if official is not None and counted != official:
                raise ValueError(f"{year} {office} {name}: {counted} counted, {official} official")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    returns = returns.select(
        "year",
        "day",
        state=pl.lit("MN"),
        title="title",
        post=pl.lit(None, pl.String),
        special=pl.lit(False),
        magnitude=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        code="county",
        county="county",
        candidate="candidate",
        party="party",
        writein="writein",
        measure=pl.lit(1, pl.Int16),
        votes="votes",
    )
    returns.write_parquet(PARSED / "returns.parquet")
    returns.group_by("year", "day", "state", "title", "post", "special", maintain_order=True).agg(
        pl.col("magnitude").max(),
        pl.col("county").filter(pl.col("votes") > 0).unique().sort().alias("present"),
        counties=pl.lit(87),
    ).write_parquet(PARSED / "races.parquet")
    print(returns.group_by("year", "title", "candidate").agg(pl.col("votes").sum()).sort("year", "title").rows())


def emit() -> Path:
    def sources(cs: Changeset) -> dict[str, UUID]:
        found = {}
        for name, (publisher, title, url) in SOURCES.items():
            found[name] = cs.source(publisher, title)
            if found[name] in cs.minted:
                cs.add(
                    "source",
                    id=found[name],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, MEDIA + path), found["results"])
        for url in REPORTS.values():
            cs.artifact(raw.cached(SOURCE, url), found["board"])
        cs.artifact(raw.cached(SOURCE, LEGISLATIVE), found["manual"])
        return found

    races = pl.read_parquet(PARSED / "races.parquet")
    precincts = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        keys = {
            value
            for (value,) in conn.execute("select value from external_id where scheme = %s", [f"{SOURCE}.result_set"])
        }
        if len(keys) < races.height:
            return medsl_statewide.emit(
                pipeline=SOURCE,
                version=VERSION,
                parsed=PARSED,
                summary="Minnesota's secretary of state, auditor, and attorney general elections 2002–2010 by county "
                "from the Secretary of State's precinct results spreadsheets",
                cite=lambda cs: {"MN": sources(cs)["results"]},
                method=METHOD,
            )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's secretary of state, auditor, and attorney general elections 2002–2010 by precinct "
            "from the Secretary of State's precinct results spreadsheets, with the canvass declarations cited",
            polity="US",
        )
        found = sources(cs)
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '27%'"
            )
        }
        units = {
            code: unit
            for code, unit in conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.precinct' and code ~ '^20(02|06|10):MN:'"
            )
        }
        cited = {
            (str(entity), str(source))
            for entity, source in conn.execute("select entity_id, source_id from citation").fetchall()
        }
        values = []
        for (year, day, office), rows in precincts.group_by("year", "day", "title", maintain_order=True):
            key = f"{day}:MN:{slug(str(office))}"
            ((contest, election, default),) = conn.execute(
                """select c.id, c.election_id, rs.id from external_id x join contest c on c.id = x.entity_id
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   where x.scheme = 'us.contest' and x.value = %s""",
                [key],
            ).fetchall()
            url, pages = DECLARATIONS[int(year)]
            source = found["manual" if year == 2002 else "board"]
            if (str(default), str(source)) not in cited:
                locator = {"pages": pages[str(office)]} | (
                    {} if year == 2002 else {"document": int(url.rpartition("/")[2])}
                )
                cs.add("citation", entity_id=default, source_id=source, locator=locator)
            derived = [
                name
                for field, name, _, _ in CANDIDATES[(int(year), str(office))]
                if field.endswith("TOT") and name is not None
            ]
            label = f"{key}:precinct" + (":2" if derived else "")
            if label in keys:
                continue
            options = {
                ("WRITE-INS" if kind == "write_in" else label.upper()): option
                for option, label, kind in conn.execute(
                    "select id, ballot_label, kind from contest_option where contest_id = %s and parent_option_id is null",
                    [contest],
                ).fetchall()
            }
            missing = set(rows["candidate"]) - set(options)
            if missing:
                raise ValueError(f"{key}: no option for {missing}")
            replaced = None
            if derived and f"{key}:precinct" in keys:
                replaced = cs.key(f"{SOURCE}.result_set", f"{key}:precinct")
                cs.add(
                    "result_set",
                    id=replaced,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="superseded",
                    source_id=found["results"],
                    is_default=False,
                    supersedes_id=None,
                    completeness=1,
                )
                for option, kind, seats in conn.execute(
                    "select option_id, kind, seats from outcome where result_set_id = %s", [replaced]
                ).fetchall():
                    cs.add("outcome", result_set_id=replaced, option_id=option, kind=kind, seats=seats)
            counted = rows.filter(~pl.col("candidate").is_in(derived))
            result_set = cs.key(f"{SOURCE}.result_set", label)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=found["results"],
                is_default=False,
                supersedes_id=replaced,
                completeness=round(int(counted["votes"].sum()) / int(rows["votes"].sum()), 4),
            )
            for option, kind, seats in conn.execute(
                "select option_id, kind, seats from outcome where result_set_id = %s", [default]
            ).fetchall():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not derived:
                cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            if year == 2006:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=CLAY_NOTE)
            for name in derived:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=DERIVED.format(name=name.title()))
            for county, precinct in rows.select("county", "precinct").unique().iter_rows():
                code = f"{year}:MN:{county}::{precinct}"
                if code in units:
                    continue
                units[code] = cs.unit("us.precinct", code)
                cs.add(
                    "reporting_unit",
                    id=units[code],
                    polity_id=cs.polity("US-MN"),
                    scheme_code="us.precinct",
                    code=code,
                    parent_id=counties[county][0],
                    area_relation="within",
                    area_id=counties[county][1],
                    kind="standard",
                    valid_from=day,
                    valid_to=day + timedelta(days=1),
                )
            values.append(
                counted.filter(pl.col("votes") > 0).select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.concat_str(
                        pl.col("year").cast(pl.String), pl.lit(":MN:"), "county", pl.lit("::"), "precinct"
                    ).replace_strict({code: str(unit) for code, unit in units.items()}),
                    option_id=pl.col("candidate").replace_strict(
                        {name: str(option) for name, option in options.items()}
                    ),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="votes",
                )
            )
        print(f"{len(values)} precinct sets")
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
