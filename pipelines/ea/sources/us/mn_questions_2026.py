import csv
import io
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import mn_local
from ea.store import DATA, connect

SOURCE = "us.mn_questions_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
URL = "https://electionresultsfiles.sos.mn.gov/20261103/BallotQuestions.txt"
TITLE = "Ballot questions, November 3, 2026"


def fetch() -> None:
    import httpx

    try:
        raw.cached(SOURCE, URL)
        return
    except LookupError:
        pass
    response = httpx.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
    response.raise_for_status()
    raw.store(
        SOURCE,
        URL,
        response.content,
        suffix=".txt",
        retrieved_at=datetime.now(UTC),
        media_type=response.headers.get("content-type", ""),
    )


def parse() -> None:
    leas = {
        row["state_leaid"].removeprefix("MN-"): row["leaid"]
        for row in json.loads(raw.cached(mn_local.SOURCE, mn_local.DIRECTORY).path.read_text())["results"]
    }
    rows = []
    for county, number, mcd, school, title, headline, text in csv.reader(
        io.StringIO(raw.cached(SOURCE, URL).path.read_text(encoding="utf-8", errors="replace")), delimiter=";"
    ):
        title = re.sub(r"\s+", " ", title).strip()
        fips = f"27{int(county) * 2 - 1:03d}"
        if title.startswith("CITY QUESTION"):
            scheme, code = "us.place", f"27{mcd}"
        elif title.startswith("TOWNSHIP QUESTION"):
            scheme, code = "us.town", f"{fips}{mcd}"
        elif title.startswith("COUNTY QUESTION"):
            scheme, code = "us.county", fips
        elif title.startswith("SCHOOL DISTRICT QUESTION"):
            match = re.search(r"\((ISD|SSD) ?#\s*(\d+)\)", title.upper())
            code = match and leas.get(f"{mn_local.TYPES[match[1]]}{int(match[2]):04d}")
            scheme = "us.school_district"
            if code is None:
                print("no school district:", title)
                continue
        else:
            continue
        rows.append(
            {
                "scheme": scheme,
                "code": code,
                "title": title,
                "stem": re.sub(r"\s*\([^)]*\)", "", title).strip(),
                "headline": re.sub(r"\s+", " ", headline).strip(),
                "text": re.sub(r"\s+", " ", text).strip(),
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["scheme", "code", "title"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "questions.parquet")
    print(frame.group_by("scheme").len().sort("scheme").rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "questions.parquet")
    with connect() as conn:
        polities: dict[str, tuple[UUID, UUID, str, str]] = {}
        for scheme, code, polity, area, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, a.id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.town', 'us.school_district')
                 and left(c.code, 2) = '27'"""
        ).fetchall():
            polities[f"{scheme}:{code}"] = (polity, area, text, path)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/mn/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota city, township, county, and school district ballot questions on the November 3, 2026 ballot, with official wording",
            polity="US",
        )
        system = cs.key("us.electoral_system", "referendum")
        source = cs.source(mn_local.PUBLISHER, TITLE)
        cs.add(
            "source", id=source, kind="official_body", publisher=mn_local.PUBLISHER, title=TITLE, url=URL
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:MN")
        elections: set[UUID] = set()
        skipped = 0
        for row in frame.sort("scheme", "code", "title").iter_rows(named=True):
            if f"{row['scheme']}:{row['code']}" not in polities:
                skipped += 1
                print("no polity:", row["scheme"], row["code"], row["title"])
                continue
            polity, area, place, path = polities[f"{row['scheme']}:{row['code']}"]
            election = cs.key("us.election", f"{DAY}:MN:{row['code']}")
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
                cs.add("name", entity_id=election, text=f"{DAY.year} {place} general election", lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            label = re.sub(r"\b([A-Z][A-Z'’-]+)\b", lambda m: m[1].title() if len(m[1]) > 1 else m[1], row["stem"])
            contest_key = f"{DAY}:MN:{row['code']}:question:{slug(label)}"
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
            cs.add("name", entity_id=contest, text=f"{DAY.year} {place}: {label}", lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            if row["headline"]:
                cs.add("note", entity_id=contest, kind="context", body_md=row["headline"])
            for position, answer in enumerate(("Yes", "No")):
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
                body=row["text"],
                url=URL,
                source_id=source,
            )
        print(f"{frame.height - skipped} questions, {skipped} without a polity")
        return cs.write()
