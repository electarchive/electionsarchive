import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl_state
from ea.store import DATA, connect

SOURCE = "us.medsl_measures"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2024, 11, 5)
ARCHIVES = {"CO", "CT", "ID", "MA", "NH", "NM", "NY", "VT", "VA", "IN"}
RECODED = {"46113": "46102", "36000": "2938000"}
AFFIRMATIVE = re.compile(r"^(YES|FOR|APPROVE)\b")
NEGATIVE = re.compile(r"^(NO|AGAINST|REJECT)\b")
SPOILED = {"OVERVOTES": 5, "OVER VOTES": 5, "UNDERVOTES": 6, "UNDER VOTES": 6}
SKIPPED = {"TOTAL VOTES CAST", "CAST VOTES", "WRITE-IN", "VOTE FOR 1"}
INITIATIVES = {
    "AK": r"BALLOT MEASURE",
    "AR": r"ISSUE 2$",
    "AZ": r"PROPOSITION 1(39|40)$",
    "CA": r"PROPOSITION 3[2-6]$",
    "DC": r"INITIATIVE",
    "FL": r"AMENDMENT NO [34]:",
    "ME": r"QUESTION 1$",
    "MO": r"AMENDMENT [23]$|PROPOSITION A$",
    "MT": r"^CI-",
    "ND": r"CANNABIS|PROHIBITING ALL POLITICAL SUBDIVISIONS",
    "OH": r"ISSUE 1$",
    "OR": r"MEASURE 11[89]",
    "SD": r"AMENDMENT [GH]:|INITIATED MEASURE",
}
SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
METHOD = (
    "County totals are the sums of the precinct rows in MEDSL's 2024 precinct returns, which compile each state's "
    "certified canvass. The answer shown as winning is the one that cleared the state's threshold for this kind of "
    "question: more Yes than No votes{threshold}."
)
THRESHOLDS = {
    "FL": (0.6, ", with Yes needing 60 percent of the votes cast on the amendment"),
    "WY": (0.5, ", with Yes needing a majority of everyone voting in the election, so blank ballots count against it"),
}
ARIZONA = "The file has no rows for Santa Cruz County, so its votes are missing from these totals."


def fetch() -> None:
    medsl_state.fetch()


def parse() -> None:
    frame = (
        pl.scan_csv(raw.cached(medsl_state.SOURCE, medsl_state.RETURNS).path, infer_schema=False)
        .filter(
            pl.col("dataverse") == "STATE",
            pl.col("stage") == "GEN",
            ~pl.col("state_po").is_in(list(ARCHIVES)),
            pl.col("district").fill_null("STATEWIDE").is_in(["STATEWIDE", "AT-LARGE"]),
            ~pl.col("office").str.contains("(?i)retain|retention|judge|court|justice"),
        )
        .with_columns(
            answers=pl.col("candidate").str.contains(AFFIRMATIVE.pattern).any().over("state_po", "office")
            & pl.col("candidate").str.contains(NEGATIVE.pattern).any().over("state_po", "office")
        )
        .filter(pl.col("answers"))
        .collect()
    )
    unknown = frame.filter(
        ~pl.col("candidate").str.contains(AFFIRMATIVE.pattern),
        ~pl.col("candidate").str.contains(NEGATIVE.pattern),
        ~pl.col("candidate").is_in([*SPOILED, *SKIPPED]),
    )["candidate"].unique()
    if unknown.len():
        raise ValueError(f"unknown answers: {unknown.to_list()}")
    precinct = ["state_po", "office", "county_fips", "jurisdiction_fips", "precinct"]
    frame = (
        frame.filter(~pl.col("candidate").is_in(list(SKIPPED)))
        .with_columns(total=(pl.col("mode") == "TOTAL").any().over(*precinct, "candidate"))
        .filter(pl.col("total") == (pl.col("mode") == "TOTAL"))
        .select(
            state="state_po",
            office="office",
            scheme=pl.when(pl.col("state_po") == "AK")
            .then(pl.lit("us.ak_house_district"))
            .when(pl.col("county_fips") == "36000")
            .then(pl.lit("us.place"))
            .otherwise(pl.lit("us.county")),
            code=pl.when(pl.col("state_po") == "AK")
            .then(pl.concat_str(pl.lit("2024:"), pl.col("jurisdiction_name").str.extract(r"(\d+)$").str.zfill(2)))
            .otherwise(pl.col("county_fips").replace(RECODED)),
            answer=pl.when(pl.col("candidate").str.contains(AFFIRMATIVE.pattern))
            .then(pl.lit("YES"))
            .when(pl.col("candidate").str.contains(NEGATIVE.pattern))
            .then(pl.lit("NO"))
            .otherwise("candidate"),
            label=pl.col("candidate"),
            votes=pl.col("votes").cast(pl.Float64, strict=False).round().cast(pl.Int64),
        )
    )
    missing = frame.filter(pl.col("code").is_null())
    if missing.height:
        print(missing.group_by("state").agg(pl.col("votes").sum()))
    labels = (
        frame.filter(pl.col("answer").is_in(["YES", "NO"]))
        .group_by("state", "office", "answer")
        .agg(pl.col("label").mode().first())
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.filter(pl.col("code").is_not_null())
        .filter(pl.col("code").n_unique().over("state", "office") * 2 >= pl.col("code").n_unique().over("state"))
        .group_by("state", "office", "scheme", "code", "answer")
        .agg(pl.col("votes").sum())
        .join(labels, on=["state", "office", "answer"], how="left")
        .sort("state", "office", "code", "answer")
        .write_parquet(PARSED / "measures.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot questions of 2024 by county from MEDSL's precinct returns",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def note(entity: UUID, kind: str, body: str) -> None:
            cs.add("note", entity_id=entity, kind=kind, body_md=body)

        source = cs.source(medsl_state.PUBLISHER, medsl_state.TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=medsl_state.PUBLISHER,
            title=medsl_state.TITLE,
            url="https://doi.org/10.7910/DVN/DODOBJ",
            license="CC0 1.0",
            license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        )
        for url in (medsl_state.RETURNS, medsl_state.CODEBOOK, medsl_state.README):
            cs.artifact(raw.cached(medsl_state.SOURCE, url), source)
        system = cs.key("us.electoral_system", "referendum")
        states = {
            abbreviation: area
            for abbreviation, area in conn.execute(
                """select substr(p.iso_code, 4), u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        paths = {
            value: path
            for value, path in conn.execute(
                """select x.value, s.path from external_id x join slug s on s.entity_id = x.entity_id and s.is_current
                   where x.scheme = 'us.election' and x.value like %s""",
                [f"{DAY}:%"],
            ).fetchall()
        }
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        held = {
            entity: path
            for entity, path in conn.execute(
                """select s.entity_id, s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.is_current and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        }

        for (state, office), rows in frame.group_by("state", "office", maintain_order=True):
            election_key = f"{DAY}:{state}"
            if election_key not in paths:
                raise ValueError(f"no {election_key} election")
            election = cs.key("us.election", election_key)
            words = office.lower().split()
            title = " ".join(
                word.upper()
                if any(character.isdigit() for character in word) or word in ("hjr", "ci")
                else word
                if index and word in SMALL
                else word.capitalize()
                for index, word in enumerate(words)
            )
            if len(title) > 80 and ":" in title:
                title = title.split(":")[0]
            contest_key = f"{DAY}:{state}:question:medsl:{slug(office)[:80]}"
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
            pattern = INITIATIVES.get(state)
            cs.add(
                "ballot_question",
                contest_id=contest,
                type="initiative" if pattern is not None and re.search(pattern, office) else "mandatory_referendum",
            )
            name(contest, title)
            if contest in cs.minted:
                path = held.get(contest)
                if path is None:
                    base = f"{paths[election_key]}/{slug(title)[:60].rstrip('-') or 'question'}"
                    path, n = base, 1
                    while path in taken:
                        n += 1
                        path = f"{base}-{n}"
                    taken.add(path)
                cs.add("slug", path=path, entity_id=contest)
            options = {}
            for position, answer in enumerate(("YES", "NO")):
                label = rows.filter(pl.col("answer") == answer)["label"].first()
                options[answer] = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=label.split()[0].capitalize(),
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
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
                completeness=None if state == "AZ" else 1,
            )
            for scheme, code, answer, votes in rows.select("scheme", "code", "answer", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit(scheme, code),
                    option_id=options.get(answer),
                    channel=0,
                    measure=1 if answer in options else SPOILED[answer],
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            if state == "AZ":
                note(result_set, "caveat", ARIZONA)
            threshold, rule = THRESHOLDS.get(state, (0.5, ""))
            note(result_set, "methodology", METHOD.format(threshold=rule))
            yes = rows.filter(pl.col("answer") == "YES")["votes"].sum()
            no = rows.filter(pl.col("answer") == "NO")["votes"].sum()
            voting = rows["votes"].sum() if state == "WY" else yes + no
            passed = yes / voting >= threshold if threshold > 0.5 else yes / voting > 0.5
            cs.add("outcome", result_set_id=result_set, option_id=options["YES" if passed else "NO"], kind="won")
        return cs.write()
