import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda
from ea.store import DATA, connect

SOURCE = "us.tx_harris_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
INBOX = DATA / "inbox" / "manual" / "tx-harris"
URL = "https://www.harrisvotes.com/Voter/View-information-for-upcoming-Elections"
PUBLISHER = "Harris County Clerk"
TITLE = "Sample ballot, November 3, 2026 general and special elections"


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
        return
    except LookupError:
        pass
    raw.store(
        SOURCE,
        URL,
        (INBOX / "hsb.pdf").read_bytes(),
        suffix=".pdf",
        retrieved_at=datetime.fromtimestamp((INBOX / "hsb.pdf").stat().st_mtime, UTC),
        media_type="application/pdf",
    )


def parse() -> None:
    import subprocess

    text = subprocess.run(
        ["pdftotext", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    haystack = re.sub(r"\s+", " ", text)
    rows = []
    for item in json.loads((INBOX / "harris_props.json").read_text()):
        wording = re.sub(r"\s+", " ", item["wording"] or "").strip()
        if not wording or wording not in haystack:
            print("unverified:", item["entity"], item["label"])
            continue
        rows.append({"entity": item["entity"], "label": item["label"], "wording": wording})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "questions.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "questions.parquet")
    with connect() as conn:
        found: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        names: dict[str, list[str]] = {}
        for scheme, code, polity, area, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, a.id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.place', 'us.school_district') and left(c.code, 2) = '48'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            names.setdefault(ceda._city(text) if scheme == "us.place" else ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/tx/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Harris County, Texas city and school district propositions on the November 3, 2026 ballot, with official wording",
            polity="US-TX",
        )
        system = cs.key("us.electoral_system", "referendum")
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL)
        cs.artifact(raw.cached(SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:TX")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            entity = row["entity"]
            options = names.get(ceda._city(entity) if entity.startswith("City of") else ceda._core(entity), [])
            if len(options) != 1:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[options[0]]
            election = cs.key("us.election", f"{DAY}:TX:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="scheduled",
                )
                cs.add(
                    "name",
                    entity_id=election,
                    text=f"{DAY.year} {place} general election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            label = row["label"]
            contest_key = f"{DAY}:TX:{code}:measure:{slug(label)}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{slug(label)}"
            if slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            slugs[contest_path] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            cs.add(
                "name",
                entity_id=contest,
                text=f"{DAY.year} {place}: {label}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            for position, answer in enumerate(("For", "Against")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{answer.lower()}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add("citation", entity_id=contest, source_id=source)
            cs.add(
                "ballot_question_text",
                contest_id=contest,
                kind="text",
                lang="en",
                body=row["wording"],
                url=URL,
                source_id=source,
            )
        print(f"{kept} of {frame.height} propositions")
        return cs.write()
