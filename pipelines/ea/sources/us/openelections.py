import io
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl
from ea.store import DATA, Connection, connect

SOURCE = "us.openelections"
VERSION = "4"
PARSED = DATA / "parsed" / SOURCE
GEORGIA = "https://raw.githubusercontent.com/openelections/openelections-data-ga/"
FILES = {
    date(2020, 11, 3): "330d0aa36a4aec2c93920bd93bd4b3a5482fc5aa/2020/20201103__ga__general.csv",
    date(2021, 1, 5): "96ed3beaa91ee59b93d4dd384a1a06d5004552bf/2021/20210105__ga__runoff.csv",
}
OFFICES = {
    "President": "president",
    "U.S. Senate": "senate",
    "U.S. Senate (Special)": "senate:special",
    "U.S. House": "house",
}
CHANNELS = {"election_day_votes": 1, "advanced_votes": 2, "absentee_by_mail_votes": 3, "provisional_votes": 5}
TYPOS = {"Matt Lierberman": "Matt Lieberman"}
REPOSITORY = "https://raw.githubusercontent.com/openelections/openelections-data-{state}/{commit}/{path}"
TREE = "https://api.github.com/repos/openelections/openelections-data-{state}/git/trees/{commit}?recursive=1"
COMMITS = {
    "AK": "4ad2e17a3d89ded4de9e2fd1120db0876b36c5f4",
    "AL": "923836fc071f4e194687294f916f2f1c24bd414a",
    "AR": "834ccef3cdcf64c8a381811e4d3ad02ec72ba336",
    "AZ": "bf14331521f62f74cb17848ab11ca3dfd27f4f82",
    "CA": "84c2cbb08a268c683800b513726a1ac7dbf08dac",
    "CO": "07a30813f00e9a435fe742aa6903773ffaed9f70",
    "CT": "f948d0a1ce36871de4a1d696612ab2a268fd0758",
    "DE": "a2f49b3c57253914c75d1306ab452d354174bd1f",
    "GA": "36b7453793848c02d8f41601d9304a66ad828dd0",
    "HI": "56655e716b7752e2ffe0acb2b85c09e70807c4fa",
    "IA": "9ec8f4e142b5159f67b6aa447a583b63e249fd12",
    "ID": "f950e6ac1c5e571d7bae0d1b01ddbb1606dcf126",
    "IL": "1fa096f60367a0fe22dc380cc9799abd5228b89c",
    "IN": "b130b78cd7b55e57e766623772424f60c0e640bd",
    "KY": "025799258505414d62830ea75fd34b58cb2ee59b",
    "KS": "11acd83f630a1404cfa507671e3ccf579b2887d8",
    "LA": "dd8cf041685b466f0d32fab0ba1c3421f4fec2a5",
    "MA": "2bdb08c234c07f042925eacdad602326a72ecd94",
    "MI": "2103114f669c2bb25f19792bf5ca290300381396",
    "MN": "589a76681b2b1a8c23162a90fcc225eb066e23f5",
    "MO": "f158f5ace1f2e6b609dcbea86f573b6a41d2f848",
    "MS": "6c8141527db2a2d456fc1194bc9365f109832983",
    "MT": "c4cb83807a02e64abeea671847cd5c2c67c05499",
    "ND": "c25145fbab08f849e6eeec319f516d7916533ea1",
    "NE": "93780198e06ed291f58fa6eab7b2d458ca3487cc",
    "NJ": "81ce9f0b7f554e0bd1655a4083b34a633967f840",
    "NM": "5b2e975c707bf1938f4ddb52ace5992eb437c093",
    "NV": "109e5c7d6be95f15c6bad5194b415ebcdf75d7fc",
    "NY": "49a61af92f2784874b1d7b405be8beaecdb4c02f",
    "OH": "c98f2f7f32c8f1b35bff344ed9b784d0eff31c8d",
    "OK": "76320f6588780002f8c0beb4acd641cf019b0489",
    "OR": "76c478184965392b18afeaf8ab0746e5f401a2e9",
    "PA": "e79aeb525b4bb4e834c21bab0699fdbf9b6b2e9a",
    "RI": "977565f9d7cf7a32d3a2a454282b5fd291cef291",
    "SC": "5e05b67e15f3614a20d31144df83248235cba6f2",
    "SD": "9e01b9aea628e9ae17ada6656b649daf319fd212",
    "TN": "4cb8cc5d0bb74b45dc05ad03c80c41b246301c1c",
    "TX": "c7185327aa987ecc23fefdde0ee53f5238f5da70",
    "UT": "a023ab4d87c4a93f0913f505c6fcbad1a585db57",
    "VT": "d7605acb83071648ea78bbdf2cf9cee7c5b4ed56",
    "WA": "5ad56d3900dfe3678b827a6125e9c446fd32628b",
    "WI": "f344a0ee7effcea4cf835750a3584a7fbb754b6d",
    "WV": "49d32c279d0e02d740c8af4fcb5b5eee380fbed4",
    "WY": "d4d430672e8823beeb7dbc9274362992739155c3",
}
MISSPELLED = {
    ("MI", "gdtraverse"): "grandtraverse",
    ("IN", "laport"): "laporte",
    ("AL", "radolph"): "randolph",
    ("KY", "breckenridge"): "breckinridge",
    ("NY", "genessee"): "genesee",
    ("NM", "doaana"): "donaana",
    ("MS", "jeffersondav"): "jeffersondavis",
    ("SD", "shannon"): "oglalalakota",
    ("SD", "butet"): "butte",
    ("SD", "potetr"): "potter",
    ("SD", "melletet"): "mellette",
    ("SD", "bufaflo"): "buffalo",
    ("WA", "wahkiukum"): "wahkiakum",
    ("MS", "launderdale"): "lauderdale",
}
LEVELS = {"__county": 0, "__county-level": 0, "": 1, "__precinct": 2, "__ward": 3}
MISFILED = {"2010/20101102__ri__general__town.csv"}
ALIASES = {"officename": "office", "ballotname": "candidate", "total_votes": "votes"}
SENATE = re.compile(r"\b(u\.?\s?s\.?|united states)\s*senat")
HOUSE = re.compile(r"\b(u\.?\s?s\.?|united states)\s*(house|rep)|congress")
NOT_COUNTED = (
    r"^(TOTAL|TIMES (CAST|COUNTED)|BALLOTS CAST|REGISTERED|BVS|BLANKS?\W.*(VOID|SCATTER))|UNDER ?VOTES|OVER ?VOTES"
)


def counties(conn: Connection) -> dict[str, dict[str, str]]:
    fips: dict[str, dict[str, str]] = {}
    for code, text, iso in conn.execute(
        """select u.code, n.text, p.iso_code from reporting_unit u
           join name n on n.entity_id = u.area_id and n.kind = 'short'
           join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
           join polity p on p.id = s.polity_id
           where u.scheme_code = 'us.county' and u.code <> '51515'
           order by u.code"""
    ).fetchall():
        names = fips.setdefault(iso.removeprefix("US-"), {})
        plain = re.sub(
            r"[^a-z]",
            "",
            unicodedata.normalize("NFKD", text).lower().replace("saint ", "st ").replace("&", "and"),
        )
        if int(code[2:]) >= 500:
            names[f"{plain}city"] = code
        names.setdefault(plain, code)
    for (state, typo), plain in MISSPELLED.items():
        fips[state][typo] = fips[state][plain]
    return fips


def picks() -> list[tuple[str, int, str]]:
    chosen: list[tuple[str, int, str]] = []
    for state, commit in COMMITS.items():
        tree = json.loads(raw.cached(SOURCE, TREE.format(state=state.lower(), commit=commit)).path.read_text())
        best: dict[int, tuple[int, list[str]]] = {}
        for entry in tree["tree"]:
            found = re.fullmatch(
                rf"(?:.*/)?(\d{{4}})11\d{{2}}__{state.lower()}__general(__county|__county-level|__precinct|__ward|__[a-z_]+__precinct|__[a-z_]+)?\.csv",
                entry["path"],
            )
            if entry["type"] != "blob" or found is None or entry["path"] in MISFILED:
                continue
            year = int(found[1])
            level = LEVELS.get(found[2] or "", 4 if (found[2] or "").endswith("__precinct") else 5)
            if 2000 <= year <= 2019 and (year % 2 == 1 or year <= 2014):
                if year not in best or level < best[year][0]:
                    best[year] = (level, [entry["path"]])
                elif level == best[year][0] >= 4:
                    best[year][1].append(entry["path"])
        chosen.extend((state, year, path) for year, (_, paths) in sorted(best.items()) for path in paths)
    return chosen


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, GEORGIA + path, suffix=".csv")
    for state, commit in COMMITS.items():
        raw.fetch(SOURCE, TREE.format(state=state.lower(), commit=commit), suffix=".json")
    for state, _, path in picks():
        raw.fetch(SOURCE, REPOSITORY.format(state=state.lower(), commit=COMMITS[state], path=path), suffix=".csv")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(
        pl.read_csv(raw.cached(SOURCE, GEORGIA + path).path, infer_schema_length=0)
        .filter(pl.col("office").is_in(list(OFFICES)))
        .select(
            pl.lit(day).alias("day"),
            pl.concat_str(
                pl.lit(f"{day}:GA:"),
                pl.col("office").replace_strict(OFFICES),
                pl.when(pl.col("office") == "U.S. House").then(pl.lit(":") + pl.col("district")).otherwise(pl.lit("")),
            ).alias("contest"),
            "county",
            "precinct",
            pl.col("candidate").replace(TYPOS),
            *(pl.col(column).cast(pl.Int64) for column in CHANNELS),
        )
        for day, path in FILES.items()
    ).write_parquet(PARSED / "georgia.parquet")

    with connect() as conn:
        fips = counties(conn)
    frames = []
    for state, year, path in picks():
        text = raw.cached(
            SOURCE, REPOSITORY.format(state=state.lower(), commit=COMMITS[state], path=path)
        ).path.read_bytes()
        frame = pl.read_csv(
            io.BytesIO(text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")),
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
        renamed: dict[str, str] = {}
        for column in frame.columns:
            if column.strip().lower() not in renamed.values():
                renamed[column] = column.strip().lower()
        frame = frame.rename(renamed)
        frame = frame.rename({a: b for a, b in ALIASES.items() if a in frame.columns and b not in frame.columns})
        if "votes" not in frame.columns and {"election_day_votes", "absentee_by_mail_votes"} <= set(frame.columns):
            frame = frame.with_columns(
                votes=pl.sum_horizontal(
                    pl.col(column).str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False)
                    for column in CHANNELS
                    if column in frame.columns
                ).cast(pl.String)
            )
        named = re.search(r"__general__([a-z_]+)\.csv$", path)
        if "county" not in frame.columns and named and named[1].replace("_", "") in fips.get(state, {}):
            frame = frame.with_columns(county=pl.lit(named[1].replace("_", " ")))
        if "county" not in frame.columns and {"jurisdiction", "reporting_level"} <= set(frame.columns):
            frame = frame.filter(pl.col("reporting_level") == "county").rename({"jurisdiction": "county"})
        if not {"county", "office", "candidate", "votes"} <= set(frame.columns):
            print(f"{path}: no county, office, candidate, or votes column, skipped")
            continue
        office = pl.col("office").fill_null("").str.to_lowercase().str.strip_chars()
        if state == "WI":
            office = office.replace({"house": "u.s. house", "senate": "u.s. senate"})
        chamber = (
            pl.when(office.str.contains(SENATE.pattern) & ~office.str.contains("state"))
            .then(pl.lit("senate"))
            .when(office.str.contains(HOUSE.pattern) & ~office.str.contains(r"\bstate\b"))
            .then(pl.lit("house"))
            .when(office.str.contains(r"^governor\b") & ~office.str.contains("council"))
            .then(pl.lit("governor"))
        )
        district = (pl.col("district") if "district" in frame.columns else pl.lit(None, pl.String)).fill_null("")
        frames.append(
            frame.with_columns(chamber=chamber)
            .filter(pl.col("chamber").is_not_null())
            .select(
                year=pl.lit(year),
                chamber="chamber",
                day=pl.lit(date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)),
                special=office.str.contains("special|unexpired")
                | district.str.to_lowercase().str.contains("unexpired"),
                state=pl.lit(state),
                district=pl.when(pl.col("chamber") == "house").then(
                    pl.coalesce(district.str.extract(r"(\d+)"), office.str.extract(r"(\d+)")).cast(
                        pl.Int16, strict=False
                    )
                ),
                code=pl.col("county")
                .fill_null("")
                .str.to_lowercase()
                .str.replace_all(r"saint ", "st ")
                .str.replace_all("&", "and")
                .str.replace_all(
                    r"\b(city and county of|county of|county|parish|borough|census area|municipality)\b", ""
                )
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(fips.get(state, {}), default=None),
                county="county",
                candidate=pl.col("candidate")
                .fill_null("")
                .str.to_uppercase()
                .str.strip_chars()
                .replace({"TRENT LOFT": "TRENT LOTT"}),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null() & ~pl.col("candidate").str.contains(NOT_COUNTED))
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        print(lost.group_by("state", "county").agg(pl.col("votes").sum()).sort("votes", descending=True).head(40))
    frame = (
        frame.filter(pl.col("code").is_not_null())
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source="state")
    )
    frame.write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "georgia.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia's 2020 federal general election and 2021 Senate runoffs by precinct",
            polity="US",
        )
        source = cs.source("OpenElections", "Georgia precinct-level results")
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="OpenElections",
            title="Georgia precinct-level results",
            url="https://github.com/openelections/openelections-data-ga",
            license=None,
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, GEORGIA + path), source)
        cs.add("scheme", code="us.precinct", polity_id=cs.polity("US"), kind="precinct", rank=3, noun="precinct")

        counties: dict[str, tuple[str, UUID, UUID]] = {
            text: (code, area, unit)
            for text, code, area, unit in conn.execute(
                """select n.text, u.code, u.area_id, u.id from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '13%'"""
            ).fetchall()
        }
        shaped: dict[str, UUID] = dict(
            conn.execute(
                """select c.code, c.area_id from area_code c join area a on a.id = c.area_id
                   where a.scheme_code = 'us.precinct'"""
            ).fetchall()
        )
        units: dict[str, UUID] = {}
        georgia = cs.polity("US-GA")
        for county, precinct in frame.select("county", "precinct").unique().sort("county", "precinct").iter_rows():
            fips, area, parent = counties[county]
            code = f"{fips}:{precinct}"
            units[code] = cs.unit("us.precinct", code)
            cs.add(
                "reporting_unit",
                id=units[code],
                polity_id=georgia,
                scheme_code="us.precinct",
                code=code,
                parent_id=parent,
                area_relation="equals" if code in shaped else "within",
                area_id=shaped.get(code, area),
                kind="standard",
                valid_from=min(FILES),
                valid_to=date(2021, 1, 6),
            )

        result_sets: dict[str, UUID] = {}
        options: dict[tuple[str, str], UUID] = {}
        for key in frame["contest"].unique().sort():
            contest = cs.key("us.contest", key)
            if contest in cs.minted:
                raise ValueError(f"{key}: the contest must already exist")
            election, official = conn.execute(
                """select c.election_id, sum(v.value) from contest c
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure = 4
                   where c.id = %s group by c.election_id""",
                [contest],
            ).fetchone() or (None, None)
            known = conn.execute(
                """select o.id, o.ballot_label, n.text from contest_option o
                   join candidacy cy on cy.option_id = o.id and cy.role = 'head'
                   join name n on n.entity_id = cy.person_id and n.is_preferred
                   where o.contest_id = %s and o.parent_option_id is null""",
                [contest],
            ).fetchall()
            lines = frame.filter(pl.col("contest") == key)
            for candidate in lines["candidate"].unique():
                words = {word for word in slug(candidate).split("-") if len(word) > 1}
                scores = sorted(
                    (
                        (len(words & {word for text in texts if text for word in slug(text).split("-")}), option)
                        for option, *texts in known
                    ),
                    key=lambda score: score[0],
                    reverse=True,
                )
                if not scores or scores[0][0] == 0 or (len(scores) > 1 and scores[0][0] == scores[1][0]):
                    raise ValueError(f"{key}: {candidate} matches no single option: {scores[:2]}")
                options[(key, candidate)] = scores[0][1]
            counted = lines.select(pl.sum_horizontal(*CHANNELS)).sum().item()
            result_sets[key] = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_sets[key],
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=round(counted / official, 4),
            )

        minted = [key for key, result_set in result_sets.items() if result_set in cs.minted]
        cs.frame(
            "result_value",
            frame.filter(pl.col("contest").is_in(minted))
            .unpivot(list(CHANNELS), index=["contest", "county", "precinct", "candidate"], variable_name="column")
            .select(
                result_set_id=pl.col("contest").replace_strict({key: str(id) for key, id in result_sets.items()}),
                unit_id=pl.concat_str(
                    pl.col("county").replace_strict({county: fips for county, (fips, _, _) in counties.items()}),
                    pl.lit(":"),
                    "precinct",
                ).replace_strict({code: str(id) for code, id in units.items()}),
                option_id=pl.concat_str("contest", pl.lit("\x1f"), "candidate").replace_strict(
                    {f"{key}\x1f{candidate}": str(id) for (key, candidate), id in options.items()}
                ),
                channel=pl.col("column").replace_strict(CHANNELS, return_dtype=pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value="value",
            ),
        )
        covered = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                   join changeset c on c.id = r.changeset_id
                   left join source s on s.id = r.source_id
                   where r.status = 'active' and c.pipeline <> %s
                     and coalesce(s.publisher, '') not in
                         ('Inter-university Consortium for Political and Social Research', 'Algara and Amlani')
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.town', 'us.precinct'))""",
                [SOURCE],
            ).fetchall()
        }
        base = pl.concat_str(
            pl.col("day").cast(pl.String),
            pl.lit(":"),
            "state",
            pl.lit(":"),
            "chamber",
            pl.when(pl.col("district").is_not_null()).then(pl.lit(":") + pl.col("district").cast(pl.String)),
            ignore_nulls=True,
        )
        counties_frame = pl.read_parquet(PARSED / "counties.parquet").filter(
            ~base.is_in(list(covered)),
            ~(base + ":special").is_in(list(covered)),
            ~pl.concat_str(pl.col("day").cast(pl.String), pl.lit(":"), "state", pl.lit(":house:0")).is_in(list(covered))
            | (pl.col("chamber") != "house"),
        )
        sources: dict[str, UUID] = {}
        for state in counties_frame["state"].unique().sort():
            title = f"{state} county-level results"
            sources[state] = cs.source("OpenElections", title)
            cs.add(
                "source",
                id=sources[state],
                kind="community",
                publisher="OpenElections",
                title=title,
                url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                license=None,
            )
        for state, _, path in picks():
            if state in sources:
                cs.artifact(
                    raw.cached(SOURCE, REPOSITORY.format(state=state.lower(), commit=COMMITS[state], path=path)),
                    sources[state],
                )
        statewide = pl.col("chamber") != "house"
        medsl.attach(cs, conn, counties_frame.filter(~statewide), sources, strict=True)
        medsl.attach(cs, conn, counties_frame.filter(statewide), sources, strict=True, extend=True)
        return cs.write()
