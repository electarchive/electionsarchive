import json
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda
from ea.store import DATA, connect

SOURCE = "us.az_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
INBOX = DATA / "inbox" / "manual" / "az-cities"
URL = "https://www.azcleanelections.gov/november-3-2026/maricopa"
PUBLISHERS = {
    "Chandler": "City of Chandler City Clerk",
    "Paradise Valley": "Town of Paradise Valley Town Clerk",
}
TITLE = "Publicity pamphlet, November 3, 2026 general election"


def items() -> list[dict[str, str | list[str]]]:
    return json.loads((INBOX / "items.json").read_text())


def pamphlet(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, check=True).stdout


def fetch() -> None:
    for url in sorted({str(item["url"]) for item in items()}):
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        content = subprocess.run(
            ["curl", "-sSL", "-m", "180", "-A", "Mozilla/5.0", url], capture_output=True, check=True
        ).stdout
        raw.store(
            SOURCE,
            url,
            content,
            suffix=".pdf",
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf",
        )


def parse() -> None:
    haystacks: dict[str, str] = {}
    rows = []
    for item in items():
        url = str(item["url"])
        if url not in haystacks:
            haystacks[url] = re.sub(r"\s+", " ", pamphlet(url)).upper()
        wording = re.sub(r"\s+", " ", str(item["text"])).strip()
        if not wording or wording.upper() not in haystacks[url]:
            print("unverified:", item["city"], item["title"])
            continue
        rows.append(
            {
                "city": item["city"],
                "label": item["title"],
                "wording": wording,
                "options": "|".join(item["options"]),
                "url": url,
            }
        )
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
               join polity p on p.id = t.polity_id and p.kind = 'municipality'
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code = 'us.place' and left(c.code, 2) = '04'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            names.setdefault(ceda._city(text) if scheme == "us.place" else ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/az/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Arizona city and town measures on the November 3, 2026 ballot, with official wording",
            polity="US-AZ",
        )
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for row in frame.iter_rows(named=True):
            if row["url"] not in sources:
                publisher = PUBLISHERS[row["city"]]
                source = sources[row["url"]] = cs.source(publisher, TITLE)
                cs.add("source", id=source, kind="official_body", publisher=publisher, title=TITLE, url=row["url"])
                cs.artifact(raw.cached(SOURCE, row["url"]), source)
        parent = cs.key("us.election", f"{DAY}:AZ")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            options = names.get(ceda._city(row["city"]), [])
            if len(options) != 1:
                print("no polity:", row["city"], row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[options[0]]
            election = cs.key("us.election", f"{DAY}:AZ:{code}")
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
            label = row["label"]
            contest_key = f"{DAY}:AZ:{code}:measure:{slug(label)}"
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
            for position, answer in enumerate(row["options"].split("|")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{slug(answer)}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer.title(),
                )
            cs.add("citation", entity_id=contest, source_id=sources[row["url"]])
            cs.add(
                "ballot_question_text",
                contest_id=contest,
                kind="text",
                lang="en",
                body=row["wording"],
                url=row["url"],
                source_id=sources[row["url"]],
            )
        print(f"{kept} of {frame.height} propositions")
        return cs.write()
