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
from ea.sources.us import medsl, medsl_state, openelections
from ea.sources.us.klarner import LOWER
from ea.store import DATA, connect

SOURCE = "us.oe_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = range(2000, 2015)
TOTALS = r"(?i)^\s*(county\s+|grand\s+|election\s+)?totals?:?\s*$"
TALLIES = r"(?i)^(number of precincts|registered voters|times (blank|over|under) voted|total ?votes|under ?votes?|over ?votes?)\b"
COLUMNS = {
    "vote": "votes",
    "total_votes": "votes",
    "precint": "precinct",
    "precinct name": "precinct",
    "election_district": "precinct",
    "precinct_name": "precinct",
    "precinctname": "precinct",
    "race": "office",
    "contest_name": "office",
    "contesttitle": "office",
    "choice_name": "candidate",
    "candidate_name": "candidate",
    "count": "votes",
    "vote_total": "votes",
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
STATEWIDE = {
    r"governor(\s*(and|&|/)\s*(lieutenant|lt\.?) governor)?": ("governor",),
    r"(lieutenant|lt\.?) governor": ("lieutenant-governor",),
    r"attorney general": ("attorney-general",),
    r"secretary of (the )?state": ("secretary-of-state", "secretary-of-the-state"),
    r"(state )?treasurer|treasurer of state": ("treasurer",),
    r"(state )?auditor|auditor of state": ("auditor",),
    r"(state )?comptroller|comptroller of public accounts": ("comptroller",),
    r"(state )?controller": ("controller",),
}
REDONE = frozenset(
    {
        "2012-11-06:TX:house:4",
        "2012-11-06:UT:president",
        "2012-11-06:UT:senate",
        "2012-11-06:UT:governor",
        "2012-11-06:UT:house:2",
        "2012-11-06:UT:house:4",
    }
)
PENNSYLVANIA = {
    "USP": "President",
    "USS": "U.S. Senate",
    "USC": "U.S. House",
    "GOV": "Governor",
    "ATT": "Attorney General",
    "AUD": "Auditor General",
    "TRE": "State Treasurer",
    "STS": "State Senate",
    "STH": "State House",
}
VERMONT = {
    "add": "addison",
    "ben": "bennington",
    "cal": "caledonia",
    "cht": "chittenden",
    "ess-orl": "essexorleans",
    "frk": "franklin",
    "gri": "grandisle",
    "lam": "lamoille",
    "ora": "orange",
    "rut": "rutland",
    "was": "washington",
    "whm": "windham",
    "wnd": "windsor",
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
        towns = {
            (state, re.sub(r"[^a-z]", "", town.lower())): county
            for state, county, town in conn.execute(
                """select distinct split_part(code, ':', 2), split_part(code, ':', 3), split_part(code, ':', 4)
                   from reporting_unit where scheme_code = 'us.precinct' and code ~ '^20(16|18|20):(MA|CT):[0-9]{5}:'"""
            ).fetchall()
        }
    for (state, typo), plain in COUNTIES.items():
        fips[state][typo] = fips[state][plain]
    frames = []
    for state, year, path in picks():
        text = raw.cached(SOURCE, url(state, path)).path.read_bytes()
        frame = pl.read_csv(
            io.BytesIO(text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")),
            infer_schema_length=0,
            truncate_ragged_lines=True,
            has_header=state != "PA",
        )
        if state == "PA":
            frame = frame.select(
                county=(
                    pl.lit("42") + (pl.col("column_2").cast(pl.Int32) * 2 - 1).cast(pl.String).str.zfill(3)
                ).replace_strict({code: name for name, code in fips["PA"].items()}),
                precinct=pl.concat_str(
                    "column_3", "column_20", "column_22", "column_24", separator=" ", ignore_nulls=True
                ),
                office=pl.col("column_8").replace_strict(PENNSYLVANIA, default=""),
                district=pl.col("column_5"),
                party=pl.col("column_9"),
                candidate=pl.when(pl.col("column_11").is_null() & (pl.col("column_9") == "OTH"))
                .then(pl.lit("Write-ins"))
                .otherwise(
                    pl.concat_str("column_12", "column_13", "column_11", "column_14", separator=" ", ignore_nulls=True)
                ),
                votes="column_15",
            )
        renamed: dict[str, str] = {}
        for column in frame.columns:
            name = COLUMNS.get(column.strip().lower(), column.strip().lower())
            if name not in renamed.values():
                renamed[column] = name
        frame = frame.rename(renamed)
        if "votes" not in frame.columns and "total" in frame.columns:
            frame = frame.rename({"total": "votes"})
        if "votes" not in frame.columns and {"poll", "abs"} <= set(frame.columns):
            frame = frame.with_columns(
                votes=pl.sum_horizontal(
                    pl.col(column).str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False)
                    for column in ("poll", "abs", "edr", "prov", "unknown")
                    if column in frame.columns
                ).cast(pl.String)
            )
        for column in ("vote_type", "votetype"):
            if column in frame.columns:
                frame = frame.filter(pl.col(column).str.to_lowercase() != "total")
        if "candidate" not in frame.columns and {"first_name", "last_name"} <= set(frame.columns):
            frame = frame.with_columns(
                candidate=pl.concat_str("first_name", "last_name", separator=" ", ignore_nulls=True)
            )
        named = re.search(r"__general__([a-z_]+)__precinct", path)
        if "county" not in frame.columns and named:
            frame = frame.with_columns(county=pl.lit(named[1].replace("_", " ")))
        if "county" not in frame.columns and "town" in frame.columns:
            frame = frame.with_columns(
                county=pl.col("town")
                .str.to_lowercase()
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(
                    {
                        town: next(name for name, code in fips[state].items() if code == county)
                        for (known, town), county in towns.items()
                        if known == state
                    },
                    default=None,
                ),
                precinct=pl.concat_str(
                    "town",
                    *(["ward"] if "ward" in frame.columns else []),
                    "precinct",
                    separator=" ",
                    ignore_nulls=True,
                ),
            )
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
        pl.col("votes").is_not_null(),
        pl.col("precinct").is_not_null(),
        pl.col("precinct") != "",
        ~pl.col("candidate").str.contains(TALLIES),
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
    frame = pl.read_parquet(PARSED / "lines.parquet").with_columns(
        day=pl.when(pl.col("year") % 2 == 1)
        .then(pl.col("path").str.extract(r"(\d{8})__").str.to_date("%Y%m%d"))
        .otherwise(
            pl.col("year").map_elements(
                lambda year: date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7), return_dtype=pl.Date
            )
        )
    )
    office = pl.col("office").str.to_lowercase()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="President, U.S. Senate, U.S. House, governor, statewide office, and state legislative general "
            "elections by precinct 2000–2014, and Michigan's 2022 statewide offices, legislature, and Proposal 22-3 by "
            "precinct, from OpenElections",
            polity="US",
        )
        filled = {
            key
            for (key,) in conn.execute(
                """select cx.value from external_id cx join result_set rs on rs.contest_id = cx.entity_id
                   where cx.scheme = 'us.contest' and cx.value ~ '^20(0[0-9]|1[0-4])-11-' and rs.changeset_id not in
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
        keys = {
            key
            for (key,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^20(0[0-9]|1[0-4])-11-'"
            ).fetchall()
        }
        seats: list[tuple[str, date, str, str | None, str]] = []
        for state, day, name, district in (
            frame.filter(pl.col("year") < 2016).select("state", "day", "office", "district").unique().iter_rows()
        ):
            text = re.sub(r"\s+", " ", name.lower()).strip()
            if re.search(
                r"\b(u\.?\s?s\.?|united states|congress\w*|county|city|party|levy|referendum|measure|amendment|board"
                r"|commission\w*|court|judge|regents?|school|recorder|clerk)\b",
                text,
            ):
                continue
            special = bool(re.search(r"special|unexpired|vacancy", f"{text} {district or ''}".lower()))
            plain_office = re.sub(r"\s*\((unexpired|special)[^)]*\)|\s+state ?wide$|\s+(special|unexpired)$", "", text)
            names = next((names for pattern, names in STATEWIDE.items() if re.fullmatch(pattern, plain_office)), None)
            upper = re.search(r"\bsenat", text)
            statehood = re.search(r"\bstate\b|assembly|delegate|legislat", text) or re.fullmatch(
                r"rep \d+-\d|repres \d+ dist", text
            )
            if names:
                tried = [
                    f"{day}:{state}:{office_name}{suffix}"
                    for office_name in names
                    for suffix in ((":special", "") if special else ("", ":special"))
                ]
            elif upper and not statehood and not district and not re.search(r"\d", text):
                tried = [f"{day}:{state}:senate"]
            elif (upper and (statehood or district)) or (
                statehood and re.search(r"\b(house|rep\w*|assembly|delegates?|legislature)\b", text)
            ):
                body = (
                    "legislature"
                    if state == "NE"
                    else "senate"
                    if upper
                    else re.sub(
                        r"[^a-z]+",
                        "-",
                        LOWER.get(state, "House of Representatives")
                        .removeprefix("State ")
                        .removesuffix(" of Representatives")
                        .lower(),
                    )
                )
                seat = (district or "").lower().strip()
                if not seat:
                    found = re.search(r"(\d+(?:\s*-+\s*\d|[a-z]\b)?)", text)
                    seat = found[1] if found else ""
                seat = re.sub(r"\.0$|\s*(unexpired term|vacancy)$", "", seat)
                seat = re.sub(r"^(\d+)(st|nd|rd|th) (representative|senatorial) district$", r"\1", seat)
                seat = re.sub(r"^(ad|sd|hd|d)0*(?=\d)", "", seat)
                post = None
                if found_post := re.fullmatch(r"0*(\d+)\s*-+\s*(\d)", seat):
                    seat, post = found_post[1], found_post[2]
                elif found_post := re.search(r"\bpos(?:ition|\.)?\s*(\d)", text):
                    post = found_post[1]
                if not seat:
                    continue
                code = VERMONT[seat] if state == "VT" and seat in VERMONT else medsl_state._district(state, seat)[0]
                base = f"{day}:{state}:{body}:{code}" + (f":{post}" if post else "")
                wanted, other = ("gs", "g") if special else ("g", "gs")
                tried = [f"{base}:{wanted}", f"{base}:{other}"]
                if not post:
                    for suffix in (wanted, other):
                        posted = [
                            f"{base}:{number}:{suffix}" for number in range(1, 5) if f"{base}:{number}:{suffix}" in keys
                        ]
                        tried.extend(posted if len(posted) == 1 else [])
            else:
                continue
            key = next((key for key in tried if key in keys), None)
            if key is not None:
                seats.append((state, day, name, district, key.split(":", 2)[2]))
        seated = pl.DataFrame(
            seats,
            schema={"state": pl.String, "day": pl.Date, "office": pl.String, "district": pl.String, "seat": pl.String},
            orient="row",
        )
        print(f"{len(seats)} state office and legislative lines map to {seated['seat'].n_unique()} contest keys")
        lines = pl.concat(
            [
                frame.filter(pl.col("year") < 2016)
                .join(seated, on=["state", "day", "office", "district"], how="left", nulls_equal=True)
                .with_columns(
                    chamber=pl.when(office.str.contains(r"^(u\.?s\.? )?president|^presidential electors"))
                    .then(pl.lit("president"))
                    .when(office.str.contains(openelections.SENATE.pattern) & ~office.str.contains("state"))
                    .then(pl.lit("senate"))
                    .when(office.str.contains(openelections.HOUSE.pattern) & ~office.str.contains(r"\bstate\b"))
                    .then(pl.lit("house"))
                    .otherwise("seat")
                )
                .select(
                    "year",
                    "day",
                    "chamber",
                    "state",
                    "fips",
                    "candidate",
                    "votes",
                    special=office.str.contains("special|unexpired") & pl.col("seat").is_null(),
                    district=pl.when(pl.col("chamber") == "house").then(
                        pl.coalesce(
                            pl.col("district").fill_null("").str.extract(r"(\d+)"), office.str.extract(r"(\d+)")
                        ).cast(pl.Int16, strict=False)
                    ),
                    code=pl.concat_str("year", "state", "fips", pl.lit(""), "precinct", separator=":"),
                ),
                michigan.select(
                    "year",
                    "day",
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
            .with_columns(candidate=pl.col("candidate").str.to_uppercase())
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
        medsl.attach(cs, conn, returns, sources, REDONE)
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        existing = set(medsl_units)
        links = pl.concat(
            pl.read_parquet(DATA / "parsed" / source / "links.parquet")
            for source in ("us.cook_precincts", "us.swdb_precincts")
        )
        shaped = dict(
            links.join(
                pl.DataFrame(
                    conn.execute(
                        """select c.code, c.area_id::text from area_code c join area a on a.id = c.area_id
                           where a.scheme_code = 'us.precinct' and c.code = any(%s)""",
                        [links["area"].unique().to_list()],
                    ).fetchall(),
                    schema=["area", "id"],
                    orient="row",
                ),
                on="area",
            )
            .select("code", "id")
            .iter_rows()
        )
        minted = (
            returns.filter(~pl.col("code").is_in(list(existing)))
            .group_by("code")
            .agg(pl.col("state").first(), pl.col("fips").first(), pl.col("day").min())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)) | pl.col("code").is_in(list(shaped)))
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
                area_relation=pl.when(pl.col("code").is_in(list(shaped)))
                .then(pl.lit("equals"))
                .otherwise(pl.lit("within")),
                area_id=pl.col("code").replace_strict(
                    shaped,
                    default=pl.col("fips").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                ),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{minted.height} new precincts, {len(existing & set(returns['code']))} michigan units reused")
        return cs.write()
