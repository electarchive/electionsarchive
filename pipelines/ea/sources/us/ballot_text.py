import re
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea.changeset import NAMESPACE, Changeset, slug
from ea.sources.us import ceda, ceda_measures, elstats, elstats_local_measures, measures, wikipedia_measures
from ea.store import connect

SOURCE = "us.ballot_text"
VERSION = "1"
WORDING = r"(?i)^(shall|should|do you|are you|would you|is it|for the|for constitutional|an act|an amendment|amendment to|every|whether)\b"
NUMBER = re.compile(r"(?i)\b(?:amendment|proposition|question|measure|issue|referendum|ballot measure|initiative)\s*(?:no\.?\s*)?(\d+[a-z]?)\b")
YEAR_LISTS = "https://en.wikipedia.org/wiki/{year}_United_States_ballot_measures"


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ballot question wording: Wikipedia's summaries of statewide measures and the ElectionStats archives' printed question text",
            polity="US",
        )
        contests = {
            value: entity
            for value, entity in conn.execute(
                """select x.value, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id
                   where x.scheme = 'us.contest'"""
            )
        }
        rows: dict[tuple[UUID, str, str, UUID, str | None], str | None] = {}

        def put(contest_key: str, kind: str, body: str | None, source: UUID, url: str | None) -> bool:
            contest = contests.get(contest_key)
            if contest is None:
                return False
            contest = cs.survivors.get(contest, contest)
            rows[(contest, kind, "en", source, url)] = body
            return True

        wikipedia = cs.source("Wikipedia", wikipedia_measures.TITLE)
        assert wikipedia not in cs.minted
        missed: list[tuple[str, object, str, str]] = []
        numbered = {}
        for key, text in conn.execute(
            """select x.value, n.text from external_id x join ballot_question q on q.contest_id = x.entity_id
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value like '%%:question:medsl:%%'"""
        ):
            if (found := NUMBER.search(text)) is not None:
                numbered.setdefault((key[:13], found[1].upper()), []).append(key)
        for state, day, measure, summary in pl.read_parquet(wikipedia_measures.PARSED / "summaries.parquet").iter_rows():
            printed = wikipedia_measures.DATES.get((state, measure), (day,))[0]
            if not put(
                f"{printed}:{state}:question:wikipedia:{slug(measure)[:80]}",
                "summary",
                summary,
                wikipedia,
                YEAR_LISTS.format(year=day.year),
            ):
                missed.append((state, day, measure, summary))
        matched = 0
        for state, day, measure, summary in missed:
            found = NUMBER.search(measure)
            keys = numbered.get((f"{day}:{state}", found[1].upper()), []) if found is not None else []
            if len(keys) == 1:
                matched += put(keys[0], "summary", summary, wikipedia, YEAR_LISTS.format(year=day.year))
        print(f"wikipedia: {len(rows)} summaries, {matched} of {len(missed)} off-key measures matched to MEDSL's by number")

        sources = {
            state: cs.source(elstats.TENANTS[state][1], elstats.TITLE) for state in ("CO", "CT", "ID", "NM", "NY", "VA")
        }
        assert not (set(sources.values()) & cs.minted)
        wording = (
            pl.read_parquet(measures.PARSED / "measures.parquet")
            .select("state", "question", "day", "title")
            .unique()
            .filter(pl.col("state").is_in(list(sources)))
            .filter(pl.col("title").str.contains(WORDING) | (pl.col("title").str.len_chars() >= 110))
        )
        found = 0
        for state, question, day, title in wording.iter_rows():
            base = elstats.TENANTS[state][2]
            found += put(
                f"{day}:{state}:question:{question}", "text", title, sources[state], f"{base}/elections/view/{question}"
            )
        print(f"elstats: {found} of {wording.height} wordings placed")

        local = (
            pl.read_parquet(elstats_local_measures.PARSED / "questions.parquet")
            .select("state", "contest", "title")
            .unique()
            .filter(pl.col("title").str.contains(WORDING) | (pl.col("title").str.len_chars() > 120))
        )
        found = 0
        for state, contest, title in local.iter_rows():
            if state in sources:
                found += put(
                    f"{state}:local:question:{contest}",
                    "text",
                    title,
                    sources[state],
                    f"{elstats.TENANTS[state][2]}/elections/view/{contest}",
                )
        print(f"elstats local: {found} of {local.height} wordings placed")

        california = cs.source(ceda.PUBLISHER, ceda.TITLE)
        assert california not in cs.minted
        found = 0
        for measure, question in (
            pl.read_parquet(ceda_measures.PARSED / "measures.parquet")
            .filter(pl.col("question").is_not_null())
            .select("measure", "question")
            .iter_rows()
        ):
            found += put(f"ceda:measure:{measure}", "text", question, california, None)
        print(f"ceda: {found} wordings placed")

        for (contest, kind, lang, source, url), body in rows.items():
            cs.add(
                "ballot_question_text",
                id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{kind}\x1f{lang}\x1f{source}\x1f{url}"),
                contest_id=contest,
                kind=kind,
                lang=lang,
                body=body,
                url=url,
                source_id=source,
            )
        return cs.write()
