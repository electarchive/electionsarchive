import io
import json
import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.oe_primary"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
NAME = re.compile(r"(?:.*/)?(\d{8})__([a-z]{2})__(.+)\.csv")
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
}
TARGET = r"^\d{4}-\d\d-\d\d:[A-Z]{2}:(senate|president|house:\d+)(:special)?:[a-z][a-z-]*$|^\d{4}-\d\d-\d\d:[A-Z]{2}:house:\d+$"
KEY = re.compile(r"(\d{4}-\d\d-\d\d):([A-Z]{2}):((?:senate|president|house:\d+)(?::special)?)(?::([a-z][a-z-]*))?")


def picks() -> dict[tuple[str, str], list[str]]:
    with connect() as conn:
        open_keys = {
            value
            for (value,) in conn.execute(
                """select e.value from external_id e where e.scheme = 'us.contest' and e.value ~ %s
                   and not exists (select 1 from result_set rs join source s on s.id = rs.source_id
                                   where rs.contest_id = e.entity_id and s.publisher <> 'Federal Election Commission')""",
                [TARGET],
            ).fetchall()
        }
    days = {(key[11:13], key[:10].replace("-", "")) for key in open_keys}
    chosen: dict[tuple[str, str], list[str]] = {}
    for state, commit in openelections.COMMITS.items():
        tree = json.loads(
            raw.cached(
                openelections.SOURCE, openelections.TREE.format(state=state.lower(), commit=commit)
            ).path.read_text()
        )
        best: dict[str, tuple[int, dict[str, str]]] = {}
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
            rank = LEVELS[level]
            name = entry["path"].rsplit("/", 1)[-1]
            if found[1] not in best or rank < best[found[1]][0]:
                best[found[1]] = (rank, {name: entry["path"]})
            elif rank == best[found[1]][0]:
                best[found[1]][1].setdefault(name, entry["path"])
        for day, (_, paths) in best.items():
            chosen[(state, day)] = sorted(paths.values())
    return chosen


def fetch() -> None:
    for (state, _), paths in picks().items():
        for path in paths:
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
        for value, contest in conn.execute(
            "select value, entity_id from external_id where scheme = 'us.contest' and value ~ %s order by value",
            [TARGET],
        ).fetchall():
            found = KEY.fullmatch(value)
            if found is None:
                continue
            day, state, chamber, party = found.groups()
            parties.setdefault((day, state, chamber), {})[party or ""] = str(contest)
    frames = []
    for (state, stamp), paths in picks().items():
        day = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
        for path in paths:
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
                        & ~office.str.contains(r"\bstate\b")
                    )
                    .then(pl.lit("house")),
                    district=pl.coalesce(district.str.extract(r"(\d+)"), office.str.extract(r"(\d+)")).cast(
                        pl.Int16, strict=False
                    ),
                    party=party.fill_null("").str.to_lowercase().str.strip_chars().str.replace(r"\s+party$", ""),
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
                    candidate=pl.col("candidate").fill_null("").str.to_uppercase().str.strip_chars(),
                    votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
                    path=pl.lit(path),
                ).filter(pl.col("office").is_not_null())
            )
    frame = pl.concat(frames).filter(
        pl.col("votes").is_not_null() & ~pl.col("candidate").str.contains(openelections.NOT_COUNTED)
    )
    frame = frame.with_columns(district=pl.when(pl.col("office") == "house").then("district"))
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
    totals = frame.group_by("state", "day", "chamber", "seat", "code", "path").agg(pl.col("votes").sum())
    frame = frame.join(
        totals.sort("votes", "path", descending=True)
        .unique(["state", "day", "chamber", "seat", "code"], keep="first", maintain_order=True)
        .select("state", "day", "chamber", "seat", "code", "path"),
        on=["state", "day", "chamber", "seat", "code", "path"],
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
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("chamber", "day", "state", "seat", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .rename({"seat": "district"})
        .with_columns(
            year=pl.col("day").dt.year(),
            special=pl.lit(False),
            kind=pl.lit("county"),
            source="state",
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House, Senate, and presidential primaries and runoffs by county from OpenElections",
            polity="US",
        )
        sources = {}
        for state in frame["state"].unique().sort():
            title = f"{state} primary results by county"
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
        for (state, _), paths in picks().items():
            if state in sources:
                for path in paths:
                    cs.artifact(
                        raw.cached(
                            SOURCE,
                            openelections.REPOSITORY.format(
                                state=state.lower(), commit=openelections.COMMITS[state], path=path
                            ),
                        ),
                        sources[state],
                    )
        medsl.attach(cs, conn, frame, sources)
        return cs.write()
