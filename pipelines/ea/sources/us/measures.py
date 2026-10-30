import io
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import elstats
from ea.sources.us.county_changes import ct_regions
from ea.store import DATA, connect

SOURCE = "us.measures"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LAST = 2026
STATEWIDE = {"CO": 83, "CT": 1, "ID": 19, "NH": 1, "NM": 4624, "NY": 1, "VT": 1, "VA": 1}
MA_LIST = f"https://electionstats.state.ma.us/ballot_questions/search/year_from:1800/year_to:{LAST}"
MA_LISTS = (MA_LIST, MA_LIST.replace("year_from:1800", "year_from:1983").replace(f"year_to:{LAST}", "year_to:2001"))
MA_DOWNLOAD = "https://electionstats.state.ma.us/ballot_questions/download/{id}/precincts_include:0/"
MA_ROW = re.compile(
    r'<tr id="bq-id-(\d+)"(?:(?!bq-id-).)*?<td class="year first">(\d{4})</td>\s*<td class="number">([^<]*)</td>'
    r'(?:(?!bq-id-).)*?<td class="bq_types">([^<]*)</td>\s*<td class="bq_location">\s*Statewide\s*</td>'
    r'(?:(?!bq-id-).)*?<tr class="[^"]*is_winner[^"]*">\s*<td class="candidate"><div class="name">([^<]*)</div>',
    re.DOTALL,
)
STATES = {
    "CO": "Colorado",
    "CT": "Connecticut",
    "ID": "Idaho",
    "MA": "Massachusetts",
    "NH": "New Hampshire",
    "NM": "New Mexico",
    "NY": "New York",
    "VT": "Vermont",
    "VA": "Virginia",
}
MEASURES = {
    "YES": 1,
    "NO": 1,
    "REGISTERED VOTERS": 0,
    "TOTAL BALLOTS CAST": 0,
    "OVERVOTES": 5,
    "VOID": 5,
    "BLANK/VOID": 5,
    "BLANK/VOID/MISCELLANEOUS": 5,
    "BLANK/VOID/SCATTERING": 5,
    "BLANK": 6,
    "BLANKS": 6,
    "BLANK VOTES": 6,
    "UNDERVOTES": 6,
    "TOTAL VOTES CAST": 0,
    "TOTAL VOTES": 0,
    "SCATTERING": 0,
    "WRITE-INS": 0,
    "BLANK ENROLLMENT": 0,
}
FORMER = {
    ("VA", "nansemond"): "51800",
    ("VA", "princessanne"): "51810",
    ("VA", "elizabethcity"): "51650",
    ("VA", "southnorfolkcity"): "51550",
    ("VA", "warwick"): "51700",
    ("VA", "warwickcity"): "51700",
    ("VT", "essexjunction"): "50007",
    ("CT", "saybrook"): "09007",
    ("CT", "chatham"): "09007",
    ("CT", "huntington"): "09001",
    ("CT", "southlyme"): "09011",
}
DATES = {
    ("VA", date(1970, 7, 14)): date(1970, 11, 3),
    ("CO", date(2005, 11, 1)): date(2005, 11, 1),
    ("CO", date(2011, 11, 1)): date(2011, 11, 1),
}
REDATED = {("CO", date(2005, 11, 1)): date(2005, 11, 8), ("CO", date(2011, 11, 1)): date(2011, 11, 8)}
METHOD = (
    "County totals are the sums of the city, town, or county rows in the state's election statistics archive. "
    "The archive marks the leading answer in each county but not statewide, so the answer shown as winning is the one "
    "that cleared the state's threshold for this kind of question: more Yes than No votes{threshold}."
)
THRESHOLDS = {
    "NH": (2 / 3, ", with Yes needing two-thirds of the votes cast on the question"),
    "CO": (0.55, ", with Yes needing 55 percent of the votes cast on the question"),
}
NEW_MEXICO = (
    "Amendments to New Mexico's elective franchise and education sections need three-quarters of the votes statewide "
    "and two-thirds in every county. This one drew between half and three-quarters Yes, so no winner is shown until "
    "the state's canvass is read."
)
MOVED = (
    "The archive dates these returns July 14, 1970. Virginia voted on its new constitution and the three amendments "
    "on November 3, 1970, and the totals match that canvass, so they are filed under that day."
)
MA_METHOD = "Totals are the city and town rows of the Secretary of the Commonwealth's archive, summed by county."


def url(tenant: str, district: int) -> str:
    search = {
        "global": {"years": {"from": 1700, "to": LAST}},
        "ballotQuestions": {"text": "", "types": [], "number": "", "divisions": [district]},
        "specialElectionsOnly": False,
        "voterStats": False,
        "stages": [],
    }
    return elstats.SEARCH.format(tenant=tenant) + urlencode({"search": json.dumps(search, separators=(",", ":"))})


def election_day(day: date) -> date:
    if day.month != 11:
        return day
    return date(day.year, 11, 2) + timedelta(days=(1 - date(day.year, 11, 2).weekday()) % 7)


def ma_rows(fetch_listings: bool = False) -> list[tuple[str, ...]]:
    rows: dict[str, tuple[str, ...]] = {}
    for listing in MA_LISTS:
        artifact = raw.fetch(SOURCE, listing, suffix=".html") if fetch_listings else raw.cached(SOURCE, listing)
        for row in MA_ROW.findall(artifact.path.read_text()):
            rows.setdefault(row[0], row)
    return list(rows.values())


def fetch() -> None:
    for state, district in STATEWIDE.items():
        raw.fetch(SOURCE, url(elstats.TENANTS[state][0], district), suffix=".csv")
    for id, *_ in ma_rows(fetch_listings=True):
        raw.fetch(SOURCE, MA_DOWNLOAD.format(id=id), suffix=".csv")


def parse() -> None:
    with connect() as conn:
        counties: dict[str, dict[str, str]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515' order by u.code"""
        ).fetchall():
            names = counties.setdefault(iso.removeprefix("US-"), {})
            if int(code[2:]) >= 500:
                names[f"{elstats.plain(text)}city"] = code
            names.setdefault(elstats.plain(text), code)
    with zipfile.ZipFile(raw.cached(elstats.SOURCE, elstats.GAZETTEER).path) as archive:
        gazetteer = pl.read_csv(
            io.BytesIO(archive.read(archive.namelist()[0])), separator="\t", infer_schema=False
        ).filter(pl.col("USPS").is_in(list(STATES)))
    towns: dict[str, dict[str, str]] = {}
    for state, geoid, name in (
        gazetteer.sort(pl.col("NAME").str.contains(r" (gore|grant|location|purchase|UT)$"))
        .select("USPS", "GEOID", "NAME")
        .iter_rows()
    ):
        names = towns.setdefault(state, {})
        names.setdefault(elstats.plain(name), geoid[:5])
        names.setdefault(elstats.plain(re.sub(elstats.SUFFIXES, "", name.lower())), geoid[:5])
    for (state, name), code in [*elstats.FORMER.items(), *FORMER.items()]:
        counties.setdefault(state, {})[name] = code
        towns.setdefault(state, {})[name] = code

    regions = ct_regions()
    frames = []
    for state, district in STATEWIDE.items():
        frame = (
            pl.scan_csv(raw.cached(SOURCE, url(elstats.TENANTS[state][0], district)).path, infer_schema=False)
            .filter(
                pl.col("retention_candidate_id").is_null()
                & (pl.col("district_type") == "State")
                & pl.col("division_type").is_in(list(elstats.DIVISIONS))
                & ~pl.col("question_type").fill_null("").str.contains("Local|County/City")
            )
            .collect()
        )
        division = pl.col("division_name").fill_null("").str.replace(r"\s*\(.*\)$", "")
        frames.append(
            frame.select(
                state=pl.lit(state),
                question=pl.col("contest_id"),
                printed=pl.col("election_date").str.slice(0, 10).str.to_date(),
                stage=pl.col("election_type"),
                title=pl.col("question_text").fill_null("Statewide question").str.strip_chars(),
                type=pl.col("question_type").fill_null(""),
                winner=pl.lit(None, pl.String),
                division=pl.concat_str(pl.lit(f"{state}:"), division),
                code=pl.when(
                    (pl.lit(state) == "CT") & (pl.col("election_date").str.slice(0, 4).cast(pl.Int32) >= 2022)
                )
                .then(division.map_elements(elstats.plain, return_dtype=pl.String).replace_strict(regions, default=None))
                .when(pl.col("division_type") == "City/Town")
                .then(
                    pl.coalesce(
                        division.map_elements(elstats.plain, return_dtype=pl.String).replace_strict(
                            towns.get(state, {}), default=None
                        ),
                        division.str.to_lowercase()
                        .str.replace_all(elstats.SUFFIXES, "")
                        .map_elements(elstats.plain, return_dtype=pl.String)
                        .replace_strict(towns.get(state, {}), default=None),
                    )
                )
                .otherwise(
                    division.str.to_lowercase()
                    .str.replace_all(r"saint ", "st ")
                    .str.replace_all("&", "and")
                    .str.replace_all(r"\b(county|parish|borough)\b", "")
                    .str.replace_all(r"[^a-z]", "")
                    .replace_strict(counties[state], default=None)
                ),
                answer=pl.col("candidate_name").str.to_uppercase().str.strip_chars(),
                votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    seen: set[str] = set()
    for id, year, number, kind, winner in ma_rows():
        artifact = raw.cached(SOURCE, MA_DOWNLOAD.format(id=id))
        if artifact.sha256 in seen:
            continue
        seen.add(artifact.sha256)
        table = pl.read_csv(artifact.path, infer_schema=False)
        place = table.columns[0]
        town = (
            pl.col(place)
            .str.replace(r"^N\. ", "North ")
            .str.replace(r"^S\. ", "South ")
            .str.replace(r"^E\. ", "East ")
            .str.replace(r"^W\. ", "West ")
        )
        frames.append(
            table.filter(pl.col(place).is_not_null() & (pl.col(place) != "TOTALS"))
            .unpivot([column for column in table.columns[1:] if column and not column.startswith("_")], index=place)
            .select(
                state=pl.lit("MA"),
                question=pl.lit(f"ma:{id}"),
                printed=pl.lit(date(int(year), 11, 1)),
                stage=pl.lit("General"),
                title=pl.lit(f"Question {number.strip()}" if number.strip() else "Statewide question"),
                type=pl.lit(kind.strip()),
                winner=pl.lit(winner.strip().upper()),
                division=pl.concat_str(pl.lit("MA:"), place),
                code=town.map_elements(elstats.plain, return_dtype=pl.String).replace_strict(towns["MA"], default=None),
                answer=pl.col("variable").str.to_uppercase().str.strip_chars(),
                votes=pl.col("value").str.replace_all(",", "").cast(pl.Int64, strict=False),
            )
        )
    frame = pl.concat(frames).filter(pl.col("votes").is_not_null())
    unknown = frame.filter(~pl.col("answer").is_in(list(MEASURES)))["answer"].unique().to_list()
    if unknown:
        raise ValueError(f"unknown answers: {unknown}")
    measure = pl.col("answer").replace_strict(MEASURES, return_dtype=pl.Int16)
    frame = frame.filter(measure > 0).with_columns(
        answer=pl.when(pl.col("answer").is_in(["YES", "NO"]))
        .then("answer")
        .otherwise(measure.replace_strict({5: "VOID", 6: "BLANK"}, return_dtype=pl.String))
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        print(lost.group_by("division").agg(pl.col("votes").sum()).sort("votes", descending=True).head(40))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.with_columns(
            day=pl.struct("state", "printed").map_elements(
                lambda row: DATES.get((row["state"], row["printed"]), election_day(row["printed"])),
                return_dtype=pl.Date,
            ),
            matched=pl.col("code").is_not_null(),
        )
        .group_by(
            "state", "question", "printed", "day", "stage", "title", "type", "winner", "code", "matched", "answer"
        )
        .agg(pl.col("votes").sum())
        .filter(pl.col("answer").is_in(["YES", "NO"]) | (pl.col("votes").sum().over("state", "question", "answer") > 0))
        .write_parquet(PARSED / "measures.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot questions by county from the election archives of nine states, 1902–2026",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def note(entity: UUID, kind: str, body: str) -> None:
            cs.add("note", entity_id=entity, kind=kind, body_md=body)

        sources: dict[str, UUID] = {}
        for state, district in STATEWIDE.items():
            tenant, publisher, site = elstats.TENANTS[state]
            sources[state] = cs.source(publisher, elstats.TITLE)
            cs.add(
                "source",
                id=sources[state],
                kind="aggregator" if state in elstats.HELD else "official_body",
                publisher=publisher,
                title=elstats.TITLE,
                url=site,
                license=elstats.HELD.get(state, "Public record"),
            )
            cs.artifact(raw.cached(SOURCE, url(tenant, district)), sources[state])
        sources["MA"] = cs.source(elstats.MA_PUBLISHER, elstats.TITLE)
        cs.add(
            "source",
            id=sources["MA"],
            kind="official_body",
            publisher=elstats.MA_PUBLISHER,
            title=elstats.TITLE,
            url="https://electionstats.state.ma.us",
            license="Public record",
        )
        for listing in MA_LISTS:
            cs.artifact(raw.cached(SOURCE, listing), sources["MA"])
        seen: set[str] = set()
        for id, *_ in ma_rows():
            artifact = raw.cached(SOURCE, MA_DOWNLOAD.format(id=id))
            if artifact.sha256 not in seen:
                seen.add(artifact.sha256)
                cs.artifact(artifact, sources["MA"])

        system = cs.key("us.electoral_system", "referendum")
        cs.add(
            "electoral_system", id=system, polity_id=cs.polity("US"), rules={"formula": "referendum"}, rules_version=1
        )
        name(system, "Statewide ballot question")
        states = {
            abbreviation: area
            for abbreviation, area in conn.execute(
                """select substr(p.iso_code, 4), u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        elections: dict[str, tuple[UUID, str]] = {}
        totals = (
            frame.group_by("state", "question", "day")
            .agg(
                units=pl.col("code").filter(pl.col("answer").is_in(["YES", "NO"])).n_unique(),
                counted=pl.col("votes").filter(pl.col("matched") & pl.col("answer").is_in(["YES", "NO"])).sum(),
                valid=pl.col("votes").filter(pl.col("answer").is_in(["YES", "NO"])).sum(),
            )
            .with_columns(most=pl.col("units").max().over("state"))
        )
        kept = totals.filter((pl.col("units") >= pl.col("most") / 2) & (pl.col("valid") > 0))
        print(f"dropped {totals.height - kept.height} questions voted on in under half a state's counties")
        frame = frame.join(kept.select("state", "question", "day", "counted", "valid"), on=["state", "question", "day"])

        for (state, question, day), rows in frame.sort("state", "day", "question").group_by(
            "state", "question", "day", maintain_order=True
        ):
            first = rows.row(0, named=True)
            stage, title, kind, winner, matched, valid, printed = (
                first["stage"],
                first["title"],
                first["type"],
                first["winner"],
                first["counted"],
                first["valid"],
                first["printed"],
            )
            if "Primary" in stage:
                election_key, election_kind = f"{day}:{state}:primary", "primary"
            elif day.month == 11 or "General" in stage:
                election_key, election_kind = f"{day}:{state}", "general"
            else:
                election_key, election_kind = f"{day}:{state}:referendum", "referendum"
            if election_key not in elections:
                election = cs.key("us.election", election_key)
                path = f"us/{state.lower()}/{day}-{election_kind}"
                elections[election_key] = (election, path)
                if election_key not in known:
                    cs.add(
                        "election",
                        id=election,
                        polity_id=cs.polity(f"US-{state}"),
                        parent_id=cs.key("us.election", str(day)) if str(day) in known else None,
                        kind=election_kind,
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(
                        election,
                        f"{day.year} {STATES[state]} {election_kind} election"
                        if election_kind != "referendum"
                        else f"{STATES[state]} referendum of {day:%B} {day.day}, {day.year}",
                    )
                    cs.add("slug", path=path, entity_id=election)
            election, election_path = elections[election_key]

            contest_key = f"{day}:{state}:question:{question}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=states[state],
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            initiative = (
                kind in ("Initiative Petition", "Proposition")
                or (state == "CO" and re.match(r"(?i)(amendment|proposition)\s*\d", title) is not None)
            ) and "referendum" not in title.lower()
            cs.add(
                "ballot_question",
                contest_id=contest,
                type="initiative"
                if initiative
                else "plebiscite"
                if "Advisory" in kind or "Non-Binding" in kind
                else "optional_referendum"
                if "Repeal" in kind or (state == "ID" and kind == "Proposition")
                else "mandatory_referendum",
            )
            name(contest, title if state != "MA" else f"{day.year} Massachusetts {title}")
            base = f"{election_path}/{slug(title)[:60].rstrip('-') or 'question'}"
            path, n = base, 1
            while path in taken:
                n += 1
                path = f"{base}-{n}"
            taken.add(path)
            if contest in cs.minted:
                cs.add("slug", path=path, entity_id=contest)
            options = {}
            for position, label in enumerate(("Yes", "No")):
                options[label.upper()] = cs.key("us.option", f"{contest_key}:{label.lower()}")
                cs.add(
                    "contest_option",
                    id=options[label.upper()],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=label,
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            misdated = (
                conn.execute(
                    "select entity_id from external_id where scheme = %s and value = %s",
                    [f"{SOURCE}.result_set", f"{REDATED[(state, day)]}:{state}:question:{question}"],
                ).fetchone()
                if (state, day) in REDATED
                else None
            )
            if misdated is not None or state == "CT" and day.year >= 2022 and result_set not in cs.minted:
                previous = result_set if misdated is None else misdated[0]
                if misdated is None:
                    result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:regions")
                (replaced,) = conn.execute(
                    "select election_id, contest_id, stage, kind, source_id, completeness, supersedes_id from result_set where id = %s",
                    [previous],
                ).fetchall()
                cs.add(
                    "result_set",
                    id=previous,
                    election_id=replaced[0],
                    contest_id=replaced[1],
                    stage=replaced[2],
                    kind=replaced[3],
                    status="superseded",
                    source_id=replaced[4],
                    is_default=False,
                    completeness=replaced[5],
                    supersedes_id=replaced[6],
                )
                for option, outcome, seats in conn.execute(
                    "select option_id, kind, seats from outcome where result_set_id = %s", [previous]
                ).fetchall():
                    cs.add("outcome", result_set_id=previous, option_id=option, kind=outcome, seats=seats)
            else:
                previous = None
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
                completeness=round(matched / valid, 4),
                supersedes_id=previous,
            )
            for code, answer, votes in rows.filter(pl.col("matched")).select("code", "answer", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.county", code),
                    option_id=options.get(answer),
                    channel=0,
                    measure=MEASURES[answer],
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            if (state, printed) in DATES:
                note(contest, "caveat", MOVED)
            yes = rows.filter(pl.col("answer") == "YES")["votes"].sum()
            if state == "MA":
                note(result_set, "methodology", MA_METHOD)
                if winner in options:
                    cs.add("outcome", result_set_id=result_set, option_id=options[winner], kind="won")
                continue
            threshold, rule = (
                THRESHOLDS[state]
                if kind == "Constitutional Amendment" and (state == "NH" or (state == "CO" and day.year >= 2016))
                else (0.5, "")
            )
            note(result_set, "methodology", METHOD.format(threshold=rule))
            if state == "NM" and kind == "Constitutional Amendment" and 0.5 < yes / valid < 0.75:
                note(contest, "caveat", NEW_MEXICO)
                continue
            passed = yes / valid >= threshold if threshold > 0.5 else yes / valid > 0.5
            cs.add("outcome", result_set_id=result_set, option_id=options["YES" if passed else "NO"], kind="won")
        return cs.write()
