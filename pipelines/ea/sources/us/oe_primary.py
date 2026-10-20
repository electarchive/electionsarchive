import io
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import klarner, medsl, medsl_state, oe_precincts, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_primary"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
NAME = re.compile(r"(?:.*/)?(\d{8})__([a-zA-Z]{2})__(.+)\.csv")
KINDS = {
    "primary",
    "special",
    "runoff",
    "primary_runoff",
    "president",
    "presidential",
    "senate",
    "house",
    "congressional",
    "congressional_district",
    "federal",
    "democratic",
    "democrat",
    "republican",
    "libertarian",
    "green",
}
SKIPPED = {
    "general",
    "general_runoff",
    "governor",
    "state_house",
    "state_senate",
    "house_of_delegates",
    "turnout",
    "headers",
    "town",
    "attorney_general",
    "auditor",
    "commissioner_of_agriculture",
    "secretary_of_state",
    "state_attorney_general",
    "iowa_secretary_of_agriculture",
    "treasurer",
}
LEVELS = {"county": 0, "state": 1, "precinct": 2, "ward": 3, "split": 4}
PARTIES = {
    "d": "democratic",
    "dem": "democratic",
    "democrat": "democratic",
    "dfl": "democratic",
    "r": "republican",
    "rep": "republican",
    "gop": "republican",
    "l": "libertarian",
    "lib": "libertarian",
    "lbt": "libertarian",
    "lpf": "libertarian",
    "g": "green",
    "grn": "green",
    "gre": "green",
    "con": "constitution",
    "cst": "constitution",
    "ind": "independence",
    "wf": "working-families",
    "wfp": "working-families",
    "pf": "peace-and-freedom",
    "ai": "american-independent",
    "aip": "american-independent",
}
ALIASES = {
    "officename": "office",
    "contest_name": "office",
    "ballotname": "candidate",
    "choice_name": "candidate",
    "partycode": "party",
    "party_name": "party",
    "total_votes": "votes",
    "vote_total": "votes",
    "precint": "precinct",
    "precinct name": "precinct",
    "precinct_name": "precinct",
    "precinctname": "precinct",
    "election_district": "precinct",
}
TARGET = (
    r"^\d{4}-\d\d-\d\d:[A-Z]{2}:(senate|president|house:\d+)(:special)?:[a-z][a-z-]*$|^\d{4}-\d\d-\d\d:[A-Z]{2}:house:\d+$"
    r"|^201[4-6]-\d\d-\d\d:[A-Z]{2}:(house|senate|assembly|general-assembly|house-of-delegates|legislature):[^:]+(:\d)?"
    r":[a-z0-9]*p(fsettled|funsettled|fpartunsettled|f|runoff)?$"
)
BODIES = r"house|senate|assembly|general-assembly|house-of-delegates|legislature"
SEATED = re.compile(
    rf"(\d{{4}}-\d\d-\d\d):([A-Z]{{2}}):({BODIES}):([^:]+)(?::(\d))?:([a-z0-9]*p(?:fsettled|funsettled|fpartunsettled|f|runoff)?)"
)
ETYPES = {
    "democratic": "dp",
    "republican": "rp",
    "libertarian": "liberp",
    "green": "greenp",
    "independence": "indepp",
    "conservative": "conservativep",
    "working-families": "workp",
    "constitution": "constp",
    "progressive": "progp",
}
LEGISLATIVE = (
    r"^(state\s+(house|senat\w*|rep\w*|assembly\w*|delegate\w*|legislat\w*)|(state\s+)?(general\s+)?assembly(man|member)?\b"
    r"|house of delegates|(member of )?(the )?house of representatives|state legislat\w*)"
)
KEY = re.compile(r"(\d{4}-\d\d-\d\d):([A-Z]{2}):((?:senate|president|house:\d+)(?::special)?)(?::([a-z][a-z-]*))?")


def held(conn: Connection, precinct: bool) -> set[str]:
    return {
        value
        for (value,) in conn.execute(
            """select e.value from external_id e where e.scheme = 'us.contest' and e.value ~ %s
               and exists (select 1 from result_set rs join source s on s.id = rs.source_id
                           where rs.contest_id = e.entity_id and s.publisher <> 'Federal Election Commission'
                             and (not %s or exists (select 1 from result_value v join reporting_unit u on u.k = v.unit_k
                                                    where v.result_set_k = rs.k and u.scheme_code = 'us.precinct')))""",
            [TARGET, precinct],
        ).fetchall()
    }


def picks() -> dict[tuple[str, str], dict[str, list[str]]]:
    with connect() as conn:
        filled = held(conn, True)
        days = {
            (value[11:13], value[:10].replace("-", ""))
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ %s", [TARGET]
            ).fetchall()
            if value not in filled
        }
    chosen: dict[tuple[str, str], dict[str, list[str]]] = {}
    for state, commit in openelections.COMMITS.items():
        tree = json.loads(
            raw.cached(
                openelections.SOURCE, openelections.TREE.format(state=state.lower(), commit=commit)
            ).path.read_text()
        )
        best: dict[str, dict[str, tuple[int, dict[str, str]]]] = {}
        for entry in tree["tree"]:
            found = NAME.fullmatch(entry["path"])
            if entry["type"] != "blob" or found is None or (state, found[1]) not in days:
                continue
            tokens = found[3].split("__")
            if SKIPPED & set(tokens) or "primary" not in found[3] and "runoff" not in found[3]:
                continue
            named = {token.removesuffix("_precinct") for token in tokens} - KINDS - {"precinct", "ward", "county"}
            level = (
                "split"
                if named
                else "county"
                if tokens[-1] == "county"
                else "ward"
                if tokens[-1] == "ward"
                else "precinct"
                if tokens[-1].endswith("precinct")
                else "state"
            )
            name = entry["path"].rsplit("/", 1)[-1]
            grains = [("county", LEVELS[level])]
            if tokens[-1].endswith("precinct"):
                grains.append(("precinct", int(level == "split")))
            for grain, rank in grains:
                known = best.setdefault(found[1], {})
                if grain not in known or rank < known[grain][0]:
                    known[grain] = (rank, {name: entry["path"]})
                elif rank == known[grain][0]:
                    known[grain][1].setdefault(name, entry["path"])
        for day, known in best.items():
            chosen[(state, day)] = {grain: sorted(paths.values()) for grain, (_, paths) in known.items()}
    return chosen


def fetch() -> None:
    for (state, _), grains in picks().items():
        for path in sorted({path for paths in grains.values() for path in paths}):
            raw.fetch(
                SOURCE,
                openelections.REPOSITORY.format(state=state.lower(), commit=openelections.COMMITS[state], path=path),
                suffix=".csv",
            )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        fips = openelections.counties(conn)
        parties: dict[tuple[str, str, str], dict[str, str]] = {}
        legislative: dict[tuple[str, str, str, str, str | None], dict[str, str]] = {}
        for value, contest in conn.execute(
            "select value, entity_id from external_id where scheme = 'us.contest' and value ~ %s order by value",
            [TARGET],
        ).fetchall():
            if (seated := SEATED.fullmatch(value)) is not None:
                day, state, body, code, post, etype = seated.groups()
                legislative.setdefault((day, state, body, code, post), {})[etype] = value
                continue
            found = KEY.fullmatch(value)
            if found is None:
                continue
            day, state, chamber, party = found.groups()
            parties.setdefault((day, state, chamber), {})[party or ""] = str(contest)
        surnames: dict[str, set[str]] = defaultdict(set)
        for contest, label, person in conn.execute(
            """select o.contest_id, o.ballot_label, n.text from contest_option o
               left join candidacy cy on cy.option_id = o.id and cy.role = 'head'
               left join name n on n.entity_id = cy.person_id and n.is_preferred
               where o.contest_id = any(%s) and o.kind <> 'write_in'""",
            [[contest for known in parties.values() for contest in known.values()]],
        ).fetchall():
            for text in (label, person):
                if text:
                    surnames[str(contest)].update(slug(text).split("-"))
    frames = []
    for (state, stamp), grains in picks().items():
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        for grain, path in ((grain, path) for grain, paths in grains.items() for path in paths):
            text = raw.cached(
                SOURCE,
                openelections.REPOSITORY.format(state=state.lower(), commit=openelections.COMMITS[state], path=path),
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
            frame = frame.rename(
                {old: new for old, new in ALIASES.items() if old in frame.columns and new not in frame.columns}
            )
            if "candidate" not in frame.columns and {"first", "last"} <= set(frame.columns):
                frame = frame.with_columns(candidate=pl.concat_str("first", "last", separator=" ", ignore_nulls=True))
            if "votes" not in frame.columns and set(openelections.CHANNELS) <= set(frame.columns):
                frame = frame.with_columns(
                    votes=pl.sum_horizontal(
                        pl.col(column).str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False)
                        for column in openelections.CHANNELS
                    ).cast(pl.String)
                )
            tokens = NAME.fullmatch(path)[3].split("__")
            if "county" not in frame.columns and {"jurisdiction", "reporting_level"} <= set(frame.columns):
                frame = frame.filter(pl.col("reporting_level") == "county").rename({"jurisdiction": "county"})
            if "county" not in frame.columns:
                named = [
                    token
                    for token in (token.removesuffix("_precinct") for token in tokens)
                    if token not in KINDS | {"precinct", "ward", "county"}
                ]
                if len(named) != 1:
                    print(f"{path}: no county column, skipped")
                    continue
                frame = frame.with_columns(county=pl.lit(named[0].replace("_", " ")))
            if not {"office", "candidate", "votes"} <= set(frame.columns):
                print(f"{path}: no office, candidate, or votes column, skipped")
                continue
            named_party = next((token for token in tokens if token in ("democratic", "democrat", "republican")), None)
            party = (
                pl.lit(named_party)
                if named_party is not None
                else (pl.col("party") if "party" in frame.columns else pl.lit(None, pl.String))
            )
            office = pl.col("office").fill_null("").str.to_lowercase()
            district = (pl.col("district") if "district" in frame.columns else pl.lit(None, pl.String)).fill_null("")
            frames.append(
                frame.select(
                    state=pl.lit(state),
                    day=pl.lit(day),
                    office=pl.when(office.str.contains(r"^presiden"))
                    .then(pl.lit("president"))
                    .when(
                        (office.str.contains(openelections.SENATE.pattern) | (office == "senate") & (state == "WI"))
                        & ~office.str.contains("state")
                    )
                    .then(
                        pl.when(office.str.contains("special|unexpired"))
                        .then(pl.lit("senate:special"))
                        .otherwise(pl.lit("senate"))
                    )
                    .when(pl.lit("special" in tokens) & office.str.contains(r"^(u\.?s\.?\s*)?senat"))
                    .then(pl.lit("senate:special"))
                    .when(
                        (office.str.contains(openelections.HOUSE.pattern) | (office == "house") & (state == "WI"))
                        & ~office.str.contains(r"\bstate\b|proposition|amendment|question|referend|resolution")
                    )
                    .then(pl.lit("house"))
                    .when(
                        office.str.contains(LEGISLATIVE)
                        & ~office.str.contains(r"\bu\.?\s?s\.?\b|united states|congress")
                    )
                    .then(pl.lit("state")),
                    label=office,
                    line=district,
                    district=pl.coalesce(district.str.extract(r"(\d+)"), office.str.extract(r"(\d+)")).cast(
                        pl.Int16, strict=False
                    ),
                    party=party.fill_null("").str.to_lowercase().str.strip_chars().str.replace(r"\s+party$", ""),
                    code=pl.col("county")
                    .fill_null("")
                    .str.to_lowercase()
                    .str.replace_all(r"saint ", "st ")
                    .str.replace_all(r"\(part\)", "")
                    .str.replace_all("&", "and")
                    .str.replace_all(
                        r"\b(city and county of|county of|county|parish|borough|census area|municipality)\b", ""
                    )
                    .str.replace_all(r"[^a-z]", "")
                    .replace_strict(fips.get(state, {}), default=None),
                    county="county",
                    precinct=(pl.col("precinct") if "precinct" in frame.columns else pl.lit(None, pl.String))
                    .str.strip_chars()
                    .str.replace_all(r"\s+", " "),
                    grain=pl.lit(grain),
                    candidate=pl.col("candidate").fill_null("").str.to_uppercase().str.strip_chars(),
                    votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
                    path=pl.lit(path),
                ).filter(pl.col("office").is_not_null())
            )
    frame = pl.concat(frames).filter(
        pl.col("votes").is_not_null()
        & ~pl.col("candidate").str.contains(openelections.NOT_COUNTED)
        & ~pl.col("candidate").str.contains("PRECINCTS REPORTING")
    )
    frame = frame.with_columns(district=pl.when(pl.col("office") == "house").then("district"))
    guessed = []
    for state, day, office, district, candidate in (
        frame.filter((pl.col("party") == "") & pl.col("office").is_in(["house", "senate"]))
        .select("state", "day", "office", "district", "candidate")
        .unique()
        .iter_rows()
    ):
        words = [word for word in slug(candidate).split("-") if word not in {"jr", "sr", "ii", "iii", "iv"}]
        if not words:
            continue
        surname = words[0] if "," in candidate else words[-1]
        for chamber in (
            (office, f"{office}:special")
            if office == "senate"
            else (*([f"house:{district}"] if district is not None else []), "house:0")
        ):
            known = parties.get((day.isoformat(), state, chamber), {})
            named = {name for name, contest in known.items() if name and surname in surnames[contest]}
            if len(named) == 1:
                guessed.append((state, day, office, district, candidate, named.pop()))
                break
            if known:
                break
    frame = frame.join(
        pl.DataFrame(
            guessed,
            schema={
                "state": pl.String,
                "day": pl.Date,
                "office": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "guess": pl.String,
            },
            orient="row",
        ),
        on=["state", "day", "office", "district", "candidate"],
        how="left",
        nulls_equal=True,
    ).with_columns(party=pl.when(pl.col("party") == "").then(pl.coalesce("guess", "party")).otherwise("party"))
    resolved = []
    for state, day, office, district, party in (
        frame.select("state", "day", "office", "district", "party").unique().iter_rows()
    ):
        wanted = PARTIES.get(party, slug(party) if party else "")
        tried = (
            (office, f"{office}:special")
            if office == "senate"
            else (*([f"house:{district}"] if district is not None else []), "house:0")
            if office == "house"
            else (office,)
        )
        for chamber in tried:
            known = parties.get((day.isoformat(), state, chamber), {})
            matches = (
                known
                if office == "house" and set(known) == {""}
                else {wanted: known[wanted]}
                if wanted in known
                else {name: contest for name, contest in known.items() if wanted and wanted in name}
            )
            if len(set(matches.values())) == 1:
                named = min(matches)
                resolved.append(
                    {
                        "state": state,
                        "day": day,
                        "office": office,
                        "district": district,
                        "party": party,
                        "chamber": (f"house:{named}" if named else "house")
                        if office == "house"
                        else f"{chamber}:{named}",
                        "seat": int(chamber.split(":")[1]) if office == "house" else None,
                    }
                )
                break
    frame = frame.join(
        pl.DataFrame(
            resolved,
            schema={
                "state": pl.String,
                "day": pl.Date,
                "office": pl.String,
                "district": pl.Int16,
                "party": pl.String,
                "chamber": pl.String,
                "seat": pl.Int16,
            },
        ),
        on=["state", "day", "office", "district", "party"],
        how="left",
        nulls_equal=True,
    )
    seats = []
    for state, day, label, line, party in (
        frame.filter(pl.col("office") == "state", pl.col("grain") == "precinct")
        .select("state", "day", "label", "line", "party")
        .unique()
        .iter_rows()
    ):
        body = (
            "legislature"
            if state == "NE"
            else "senate"
            if re.search(r"senat", label)
            else slug(
                klarner.LOWER.get(state, "House of Representatives")
                .removeprefix("State ")
                .removesuffix(" of Representatives")
            )
        )
        text = re.sub(r"\.0$|^(district|dist\.?)\s*", "", line.strip().lower())
        if not text:
            found = re.search(r"(\d+)", label)
            text = found[1] if found else ""
        text = re.sub(r"^(ad|sd|hd|d)0*(?=\d)", "", text)
        post = None
        if found := re.search(r"\bpos(?:ition|\.)?\s*(\d)", label):
            post = found[1]
        if not text:
            continue
        code = medsl_state._district(state, text)[0]
        known = legislative.get((day.isoformat(), state, body, code, post), {})
        if not known and post is None:
            posted = [
                (number, held)
                for number in "1234"
                if (held := legislative.get((day.isoformat(), state, body, code, number)))
            ]
            if len(posted) == 1:
                post, known = posted[0]
        open_types = {etype for etype in known if re.match(r"(ttp|up)", etype)}
        prefix = ETYPES.get(PARTIES.get(party, slug(party) if party else ""))
        matches = (
            open_types
            if open_types and len(open_types) == len(known)
            else {
                etype
                for etype in known
                if prefix and re.fullmatch(rf"{prefix}(fsettled|funsettled|fpartunsettled|f)?", etype)
            }
        )
        if len(matches) == 1:
            seats.append(
                (state, day, label, line, party, f"{body}:{code}" + (f":{post}" if post else "") + f":{matches.pop()}")
            )
    frame = frame.join(
        pl.DataFrame(
            seats,
            schema={
                "state": pl.String,
                "day": pl.Date,
                "label": pl.String,
                "line": pl.String,
                "party": pl.String,
                "legislative": pl.String,
            },
            orient="row",
        ),
        on=["state", "day", "label", "line", "party"],
        how="left",
        nulls_equal=True,
    ).with_columns(
        chamber=pl.when(pl.col("office") == "state")
        .then(pl.when(pl.col("grain") == "precinct").then("legislative"))
        .otherwise("chamber")
    )
    totals = frame.group_by("grain", "state", "day", "chamber", "seat", "code", "path").agg(pl.col("votes").sum())
    frame = frame.join(
        totals.sort("votes", "path", descending=True)
        .unique(["grain", "state", "day", "chamber", "seat", "code"], keep="first", maintain_order=True)
        .select("grain", "state", "day", "chamber", "seat", "code", "path"),
        on=["grain", "state", "day", "chamber", "seat", "code", "path"],
        how="semi",
        nulls_equal=True,
    )
    dropped = frame.filter(pl.col("chamber").is_null())
    if dropped.height:
        print(
            dropped.group_by("state", "day", "office", "district", "party")
            .agg(pl.col("votes").sum())
            .sort("votes", descending=True)
            .head(40)
        )
    lost = frame.filter(pl.col("chamber").is_not_null() & pl.col("code").is_null())
    if lost.height:
        print(lost.group_by("state", "county").agg(pl.col("votes").sum()).sort("votes", descending=True).head(40))
    (
        frame.filter(pl.col("chamber").is_not_null() & pl.col("code").is_not_null())
        .filter(
            (pl.col("grain") == "county")
            | pl.col("precinct").is_not_null()
            & (pl.col("precinct") != "")
            & ~pl.col("precinct").str.contains(oe_precincts.TOTALS)
        )
        .with_columns(
            fips="code",
            code=pl.when(pl.col("grain") == "precinct")
            .then(pl.concat_str(pl.col("day").cast(pl.String), "state", "code", pl.lit(""), "precinct", separator=":"))
            .otherwise("code"),
            key=pl.concat_str(
                pl.col("day").cast(pl.String),
                "state",
                pl.when(pl.col("seat").is_not_null())
                .then(
                    pl.concat_str(
                        pl.lit("house:"), pl.col("seat").cast(pl.String), pl.col("chamber").str.strip_prefix("house")
                    )
                )
                .otherwise("chamber"),
                separator=":",
            ),
        )
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED))
            | pl.col("candidate").str.contains(r"WRITE|WNTE|^(MISC\.?|OTHER|SCATTERED)$"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("grain", "key", "chamber", "day", "state", "seat", "fips", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .rename({"seat": "district", "grain": "kind"})
        .with_columns(
            year=pl.col("day").dt.year(),
            special=pl.lit(False),
            source=pl.concat_str("state", pl.lit(":"), "kind"),
        )
        .write_parquet(PARSED / "lines.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House, Senate, and presidential primaries and runoffs by county and precinct from OpenElections",
            polity="US",
        )
        counties = frame.filter(pl.col("kind") == "county", ~pl.col("key").is_in(list(held(conn, False))))
        precincts = frame.filter(pl.col("kind") == "precinct", ~pl.col("key").is_in(list(held(conn, True))))
        sources = {}
        for source in pl.concat([counties, precincts])["source"].unique().sort():
            state, grain = source.split(":")
            title = f"{state} primary results by {grain}"
            sources[source] = cs.source("OpenElections", title)
            cs.add(
                "source",
                id=sources[source],
                kind="community",
                publisher="OpenElections",
                title=title,
                url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                license="No license stated: facts published with attribution, raw files not redistributed",
            )
        stored: set[tuple[str, str]] = set()
        for (state, _), grains in picks().items():
            for grain, paths in grains.items():
                if f"{state}:{grain}" in sources:
                    for path in paths:
                        artifact = raw.cached(
                            SOURCE,
                            openelections.REPOSITORY.format(
                                state=state.lower(), commit=openelections.COMMITS[state], path=path
                            ),
                        )
                        if (f"{state}:{grain}", artifact.storage_key) not in stored:
                            stored.add((f"{state}:{grain}", artifact.storage_key))
                            cs.artifact(artifact, sources[f"{state}:{grain}"])
        medsl.attach(cs, conn, counties, sources)
        medsl.attach(cs, conn, precincts, sources, grain="precinct")
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        units = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        minted = (
            precincts.group_by("code")
            .agg(pl.col("state", "fips", "day").first())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(
                pl.col("id").is_in(list(used)),
                pl.col("id").map_elements(lambda id: UUID(id) in cs.minted, return_dtype=pl.Boolean),
            )
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
                parent_id=pl.col("fips").replace_strict({code: str(unit) for code, (unit, _) in units.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("fips").replace_strict({code: str(area) for code, (_, area) in units.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{minted.height} new precincts")
        areas = dict(
            conn.execute(
                """select c.code, c.area_id::text from area_code c join area a on a.id = c.area_id
                   where a.scheme_code = 'us.precinct' and c.code ~ '^20[0-9]{2}-[0-9]{2}-[0-9]{2}:CA:'"""
            ).fetchall()
        )
        rows = []
        tally: dict[str, list[int]] = {}
        for row in conn.execute(
            """select u.id::text, u.polity_id::text, u.code, u.parent_id::text, u.kind::text, u.valid_from, u.valid_to
               from reporting_unit u join changeset c on c.id = u.changeset_id
               where c.pipeline = %s and u.scheme_code = 'us.precinct' and u.code ~ '^20[0-9-]+:CA:'""",
            [SOURCE],
        ).fetchall():
            day, _, county, _, name = row[2].split(":", 4)
            name = name.strip()
            area = next(
                (
                    areas[f"{day}:CA:{county}:{bare}"]
                    for bare in dict.fromkeys([name, re.sub(r"\.0$", "", name), re.sub(r"A$", "", name)])
                    if f"{day}:CA:{county}:{bare}" in areas
                ),
                None,
            )
            tally.setdefault(day, [0, 0])[1] += 1
            if area is not None:
                tally[day][0] += 1
                rows.append((*row, area))
        for day, (linked, total) in sorted(tally.items()):
            print(f"{day} California: {linked} of {total} precincts on the Statewide Database's shapes")
        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                rows,
                schema=["id", "polity_id", "code", "parent_id", "kind", "valid_from", "valid_to", "area_id"],
                orient="row",
            ).with_columns(scheme_code=pl.lit("us.precinct"), area_relation=pl.lit("equals")),
        )
        return cs.write()
