import io
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = range(2000, 2015, 2)
TOTALS = r"(?i)^\s*(county\s+|grand\s+)?totals?:?\s*$"
COLUMNS = {
    "vote": "votes",
    "total_votes": "votes",
    "precint": "precinct",
    "precinct name": "precinct",
    "election_district": "precinct",
}
COUNTIES = {
    ("TX", "galvestone"): "galveston",
    ("IN", "koskiusko"): "kosciusko",
    ("WA", "kittias"): "kittitas",
    ("MS", "lafayaette"): "lafayette",
    ("MS", "chicksaw"): "chickasaw",
    ("WV", "glimer"): "gilmer",
    ("ID", "lemhicontinued"): "lemhi",
    ("MO", "kansascity"): "jackson",
    ("IL", "cityofchicago"): "cook",
    ("IL", "cityofrockford"): "winnebago",
    ("IL", "cityofpeoria"): "peoria",
    ("IL", "cityofaurora"): "kane",
    ("IL", "cityofbloomington"): "mclean",
    ("IL", "cityofgalesburg"): "knox",
    ("IL", "cityofeaststlouis"): "stclair",
    ("IL", "cityofdanville"): "vermilion",
}
MICHIGAN = "2022/20221108__mi__general__precinct.csv"
PLACE_WORDS = r"\b(charter|township|twp|precinc\w*|pct|city|village|of|the)\b"
PLACE_KINDS = ("city", "township|twp", "village")
MI_OFFICES = {
    "Governor": "governor",
    "Secretary of State": "secretary-of-state",
    "Attorney General": "attorney-general",
    "Proposal 22-3": "question:county:proposal-22-3",
}


def picks() -> list[tuple[str, int, str]]:
    chosen: list[tuple[str, int, str]] = []
    for state, commit in openelections.COMMITS.items():
        tree = json.loads(
            raw.cached(
                openelections.SOURCE, openelections.TREE.format(state=state.lower(), commit=commit)
            ).path.read_text()
        )
        found: dict[int, dict[bool, list[str]]] = defaultdict(lambda: defaultdict(list))
        for entry in tree["tree"]:
            matched = re.fullmatch(
                rf"(\d{{4}})/(?:counties/)?(\d{{4}})11\d{{2}}__{state.lower()}__general(__[a-z_]+)?__precinct\.csv",
                entry["path"],
            )
            if entry["type"] == "blob" and matched and int(matched[2]) in YEARS and matched[1] == matched[2]:
                found[int(matched[2])][matched[3] is None].append(entry["path"])
        for year, files in sorted(found.items()):
            chosen.extend((state, year, path) for path in sorted(files[True] or files[False]))
    return [*chosen, ("MI", 2022, MICHIGAN)]


def url(state: str, path: str) -> str:
    return openelections.REPOSITORY.format(state=state.lower(), commit=openelections.COMMITS[state], path=path)


def fetch() -> None:
    for state, _, path in picks():
        raw.fetch(SOURCE, url(state, path), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    for (state, typo), plain in COUNTIES.items():
        fips[state][typo] = fips[state][plain]
    frames = []
    for state, year, path in picks():
        text = raw.cached(SOURCE, url(state, path)).path.read_bytes()
        frame = pl.read_csv(
            io.BytesIO(text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")),
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
        renamed: dict[str, str] = {}
        for column in frame.columns:
            name = COLUMNS.get(column.strip().lower(), column.strip().lower())
            if name not in renamed.values():
                renamed[column] = name
        frame = frame.rename(renamed)
        if "votes" not in frame.columns and "total" in frame.columns:
            frame = frame.rename({"total": "votes"})
        if "candidate" not in frame.columns and {"first_name", "last_name"} <= set(frame.columns):
            frame = frame.with_columns(
                candidate=pl.concat_str("first_name", "last_name", separator=" ", ignore_nulls=True)
            )
        named = re.search(r"__general__([a-z_]+)__precinct", path)
        if "county" not in frame.columns and named:
            frame = frame.with_columns(county=pl.lit(named[1].replace("_", " ")))
        if not {"county", "precinct", "office", "candidate", "votes"} <= set(frame.columns):
            print(f"{path}: columns {frame.columns}, skipped")
            continue
        frames.append(
            frame.select(
                state=pl.lit(state),
                year=pl.lit(year),
                path=pl.lit(path),
                county=pl.col("county").fill_null("").str.strip_chars(),
                fips=pl.col("county")
                .fill_null("")
                .str.to_lowercase()
                .str.replace_all(r"saint ", "st ")
                .str.replace_all("&", "and")
                .str.replace_all(
                    r"\b(city and county of|county of|county|parish|borough|census area|municipality)\b", ""
                )
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(fips.get(state, {}), default=None),
                precinct=pl.col("precinct").str.strip_chars().str.replace_all(r"\s+", " "),
                office=pl.col("office").fill_null("").str.strip_chars(),
                district=(
                    pl.col("district") if "district" in frame.columns else pl.lit(None, pl.String)
                ).str.strip_chars(),
                party=(pl.col("party") if "party" in frame.columns else pl.lit(None, pl.String)).str.strip_chars(),
                candidate=pl.col("candidate").fill_null("").str.strip_chars(),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    frame = pl.concat(frames).filter(
        pl.col("votes").is_not_null(), pl.col("precinct").is_not_null(), pl.col("precinct") != ""
    )
    lost = frame.filter(pl.col("fips").is_null())
    if lost.height:
        print(
            lost.group_by("state", "year", "county").agg(pl.col("votes").sum()).sort("votes", descending=True).head(40)
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("fips").is_not_null(), ~pl.col("precinct").str.contains(TOTALS)).write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    office = pl.col("office").str.to_lowercase()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="President, U.S. Senate, and U.S. House general elections by precinct 2000–2014, and Michigan's "
            "2022 statewide offices, legislature, and Proposal 22-3 by precinct, from OpenElections",
            polity="US",
        )
        filled = {
            key
            for (key,) in conn.execute(
                """select cx.value from external_id cx join result_set rs on rs.contest_id = cx.entity_id
                   where cx.scheme = 'us.contest' and cx.value ~ '^20(0[0-9]|1[0-4])-11-'
                     and cx.value ~ ':(president|senate|house)(:|$)' and rs.changeset_id not in
                       (select id from changeset where pipeline = %s)
                     and exists (select 1 from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = rs.k and u.scheme_code = 'us.precinct')""",
                [SOURCE],
            ).fetchall()
        }
        medsl_units = [
            code
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.precinct' and code like '2022:MI:%%'"
            ).fetchall()
        ]
        plain: dict[tuple[str, str], list[str]] = defaultdict(list)
        cores: dict[str, list[tuple[str, tuple[bool, ...], str]]] = defaultdict(list)
        for code in medsl_units:
            parts = code.split(":", 4)
            name = parts[4].lower()
            plain[(parts[2], re.sub(r"[^a-z0-9]", "", name))].append(code)
            cores[parts[2]].append(
                (
                    re.sub(r"[^a-z0-9]", "", re.sub(PLACE_WORDS, "", name)),
                    tuple(bool(re.search(word, name)) for word in PLACE_KINDS),
                    code,
                )
            )
        units: dict[tuple[str, str], str] = {}
        michigan = frame.filter(pl.col("year") == 2022)
        for fips, county, precinct in michigan.select("fips", "county", "precinct").unique().iter_rows():
            name = precinct.lower()
            exact = plain.get((fips, re.sub(r"[^a-z0-9]", "", name)), [])
            core = re.sub(r"[^a-z0-9]", "", re.sub(PLACE_WORDS, "", name))
            kind = tuple(bool(re.search(word, name)) for word in PLACE_KINDS)
            near = [
                code
                for known, known_kind, code in cores[fips]
                if known_kind == kind
                and re.sub(r"\D", "", known) == re.sub(r"\D", "", core)
                and SequenceMatcher(None, known, core).ratio() >= 0.8
            ]
            units[(fips, precinct)] = (
                exact[0]
                if len(exact) == 1
                else near[0]
                if len(near) == 1
                else f"2022:MI:{fips}:{county.upper()}:{precinct}"
            )
        shared = (
            michigan.with_columns(
                code=pl.struct("fips", "precinct").map_elements(
                    lambda row: units[(row["fips"], row["precinct"])], return_dtype=pl.String
                )
            )
            .group_by("office", "district", "code", "candidate")
            .agg(pl.col("precinct").n_unique())
            .filter(pl.col("precinct") > 1)
        )
        if shared.height:
            raise ValueError(f"precinct names give one candidate twice in a unit: {shared.head(5)}")
        lines = pl.concat(
            [
                frame.filter(pl.col("year") < 2016)
                .with_columns(
                    chamber=pl.when(office.str.contains(r"^(u\.?s\.? )?president|^presidential electors"))
                    .then(pl.lit("president"))
                    .when(office.str.contains(openelections.SENATE.pattern) & ~office.str.contains("state"))
                    .then(pl.lit("senate"))
                    .when(office.str.contains(openelections.HOUSE.pattern) & ~office.str.contains(r"\bstate\b"))
                    .then(pl.lit("house"))
                )
                .select(
                    "year",
                    "chamber",
                    "state",
                    "fips",
                    "candidate",
                    "votes",
                    special=office.str.contains("special|unexpired"),
                    district=pl.when(pl.col("chamber") == "house").then(
                        pl.coalesce(
                            pl.col("district").fill_null("").str.extract(r"(\d+)"), office.str.extract(r"(\d+)")
                        ).cast(pl.Int16, strict=False)
                    ),
                    code=pl.concat_str("year", "state", "fips", pl.lit(""), "precinct", separator=":"),
                ),
                michigan.select(
                    "year",
                    chamber=pl.when(pl.col("office").is_in(["State Senate", "State House"]))
                    .then(
                        pl.concat_str(
                            pl.col("office").str.slice(6).str.to_lowercase(),
                            pl.col("district").str.extract(r"(\d+)").cast(pl.Int16).cast(pl.String),
                            pl.lit("g"),
                            separator=":",
                        )
                    )
                    .otherwise(pl.col("office").replace_strict(MI_OFFICES, default=None)),
                    state="state",
                    fips="fips",
                    candidate="candidate",
                    votes="votes",
                    special=pl.lit(False),
                    district=pl.lit(None, pl.Int16),
                    code=pl.struct("fips", "precinct").map_elements(
                        lambda row: units[(row["fips"], row["precinct"])], return_dtype=pl.String
                    ),
                ),
            ]
        )
        returns = (
            lines.filter(pl.col("chamber").is_not_null())
            .with_columns(
                day=pl.col("year").map_elements(
                    lambda year: date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7), return_dtype=pl.Date
                ),
                candidate=pl.col("candidate").str.to_uppercase(),
            )
            .filter(~pl.col("candidate").str.contains(openelections.NOT_COUNTED), pl.col("candidate") != "")
            .with_columns(
                measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
                writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
            )
            .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
            .filter(pl.col("measure") > 0)
            .group_by("year", "chamber", "day", "special", "state", "district", "code", "fips", "candidate", "measure")
            .agg(pl.col("writein").any(), pl.col("votes").sum())
            .with_columns(kind=pl.lit("precinct"), source="state")
        )
        keyed = returns.with_columns(
            key=pl.concat_str(
                pl.col("day").cast(pl.String),
                "state",
                "chamber",
                pl.col("district").cast(pl.String),
                separator=":",
                ignore_nulls=True,
            )
        )
        print(
            keyed.filter(pl.col("key").is_in(list(filled)))
            .group_by("state", "year")
            .agg(pl.col("key").n_unique())
            .sort("state", "year")
        )
        returns = keyed.filter(~pl.col("key").is_in(list(filled))).drop("key")
        sources: dict[str, UUID] = {}
        for state, _, path in picks():
            title = f"{state} precinct-level results"
            if state not in sources:
                sources[state] = cs.source("OpenElections", title)
                if sources[state] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[state],
                        kind="community",
                        publisher="OpenElections",
                        title=title,
                        url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                        license="No license stated: facts published with attribution, raw files not redistributed",
                    )
            cs.artifact(raw.cached(SOURCE, url(state, path)), sources[state])
        medsl.attach(cs, conn, returns, sources)
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        existing = set(medsl_units)
        minted = (
            returns.filter(~pl.col("code").is_in(list(existing)))
            .group_by("code")
            .agg(pl.col("state").first(), pl.col("fips").first(), pl.col("day").min())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)))
        )
        cs.frame(
            "reporting_unit",
            minted.select(
                "id",
                polity_id=pl.col("state").map_elements(
                    lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                ),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("fips").replace_strict({code: str(unit) for code, (unit, _) in counties.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("fips").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{minted.height} new precincts, {len(existing & set(returns['code']))} michigan units reused")
        return cs.write()
