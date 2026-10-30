import html
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda
from ea.store import DATA, connect

SOURCE = "us.wa_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
KING = "https://info.kingcounty.gov/kcelections/Vote/contests/ballotmeasures.aspx?eid=55"
PUBLISHER = "King County Elections"
TITLE = "Ballot measures, November 3, 2026 General Election"
BROWSER = {"User-Agent": "Mozilla/5.0"}


def download(url: str) -> raw.Artifact:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    response = httpx.get(url, headers=BROWSER, follow_redirects=True, timeout=120)
    response.raise_for_status()
    return raw.store(
        SOURCE,
        url,
        response.content,
        suffix=".html",
        retrieved_at=datetime.now(UTC),
        media_type=response.headers.get("content-type", ""),
    )


def details() -> list[str]:
    listing = download(KING).path.read_text(encoding="utf-8", errors="replace")
    return sorted(
        {
            f"https://info.kingcounty.gov/kcelections/Vote/contests/ballotmeasures.aspx?lang=en-US&cid={cid}&groupname={group}&eid=55"
            for cid, group in re.findall(r"ballotmeasures\.aspx\?lang=en-US&amp;cid=(\d+)&amp;groupname=(\w+)", listing)
        }
    )


def fetch() -> None:
    download(KING)
    for url in details():
        download(url)


def parse() -> None:
    rows = []
    for url in details():
        page = download(url).path.read_text(encoding="utf-8", errors="replace")
        body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", page, flags=re.S)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))
        text = text[text.find("General Election ", text.find("Ballot measures v1")) :]
        for place, number, title, wording in re.findall(
            r"General Election (.+?) (Proposition No\. \d+) (.+?) (The .+?|Shall .+?|Should .+?|This .+?)\s+(?:Yes No|Explanatory statement)\b",
            text,
        ):
            rows.append({"url": url, "place": place, "number": number, "title": title, "text": wording})
        if not any(row["url"] == url for row in rows):
            print("no wording:", url)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height, "measures")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        found: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        names: dict[str, str] = {}
        for scheme, code, polity, area, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, a.id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district', 'special_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.place', 'us.school_district', 'us.special_district') and c.code like '53%%'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            names[ceda._core(text) if scheme != "us.place" else ceda._city(text)] = key
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/wa/%%'").fetchall())
        known = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.election' and value like %s", [f"{DAY}:WA:%"]
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Washington local ballot measures on the November 3, 2026 ballot, with official wording",
            polity="US-WA",
        )
        system = cs.key("us.electoral_system", "referendum")
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=KING)
        cs.artifact(raw.cached(SOURCE, KING), source)
        parent = cs.key("us.election", f"{DAY}:WA")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            place = row["place"]
            key = names.get(
                ceda._city(place)
                if place.startswith("City of")
                else ceda._core(re.sub(r"\s+No\.?\s*\d+$", "", place))
            )
            if key is None:
                print("no polity:", place, row["number"])
                continue
            kept += 1
            polity, area, name, path, code = found[key]
            election_key = f"{DAY}:WA:{code}"
            if key.startswith("us.school_district") and election_key not in known:
                election_key = f"{DAY}:WA:sd{code}"
            election = cs.key("us.election", election_key)
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
                cs.add("name", entity_id=election, text=f"{DAY.year} {name} general election", lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            label = row["number"]
            contest_key = f"{DAY}:WA:{code}:measure:{slug(label)}"
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
            cs.add("name", entity_id=contest, text=f"{DAY.year} {name}: {label}", lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            cs.add("note", entity_id=contest, kind="context", body_md=row["title"])
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
                url=row["url"],
                source_id=source,
            )
        print(f"{kept} of {frame.height} measures")
        return cs.write()
