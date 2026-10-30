import html
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl
from psycopg import Connection

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda, govmint
from ea.store import DATA, connect

SOURCE = "us.la_propositions_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
ELECTION = 344
BASE = "https://voterportal.sos.la.gov"
INDEX = f"{BASE}/PropositionText/PropositionText"
DETAIL = f"{BASE}/PropositionText/PropositionText/Detail?referendumId="
URL = INDEX
PUBLISHER = "Louisiana Secretary of State"
TITLE = "Proposition text, November 3, 2026"
PARISHES = re.compile(r"<option value=\"(\d+)\">([A-Z][A-Z. ]*[A-Z.])(?:\s*-\s*\d+)?\s*</option>")
HEADERS = {"User-Agent": "Mozilla/5.0"}


def store(url: str, content: bytes) -> None:
    raw.store(SOURCE, url, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")


def cached(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text()


def fetch() -> None:
    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client:
        index = client.get(INDEX)
        index.raise_for_status()
        store(INDEX, index.content)
        parishes = PARISHES.findall(index.text)
        for parish, _ in parishes:
            url = f"{INDEX}?parish={parish}"
            page = client.post(f"{BASE}/PropositionText", data={"ElectionId": ELECTION, "ParishId": parish})
            page.raise_for_status()
            store(url, page.content)
            for referendum in sorted(set(re.findall(r"referendumId=(\d+)", page.text))):
                detail = client.get(f"{DETAIL}{referendum}")
                detail.raise_for_status()
                store(f"{DETAIL}{referendum}", detail.content)


def parse() -> None:
    index = cached(INDEX)
    rows: list[dict[str, str]] = []
    for parish, name in PARISHES.findall(index):
        page = cached(f"{INDEX}?parish={parish}")
        for referendum, label in re.findall(r'referendumId=(\d+)">([^<]*)', page):
            text = html.unescape(re.sub(r"<[^>]*>", "\n", cached(f"{DETAIL}{referendum}")))
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            start = lines.index("back to proposition list") + 1
            body = lines[start:]
            body = body[: body.index("back to proposition list")]
            rows.append(
                {
                    "parish": name.strip().title(),
                    "referendum": referendum,
                    "label": html.unescape(label).strip(),
                    "title": body[0],
                    "text": " ".join(body[1:]),
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique("referendum", keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "propositions.parquet")
    print(frame.height)


def mint_special(
    conn: Connection,
    cs: Changeset,
    found: dict[str, tuple[UUID, UUID, str, str, str]],
    names: dict[str, list[str]],
    row: dict[str, str],
    slugs: dict[str, UUID],
) -> list[str]:
    parish = names.get(ceda._core(f"{row['parish']} Parish"), [])
    if len(parish) != 1:
        return []
    county, county_area, _, county_path, county_code = found[parish[0]]
    display = govmint.display_name(row["label"])
    special = govmint.special(conn, cs, "LA", (county, county_area, county_path, county_code), display, slugs)
    if special is None:
        return []
    key = f"us.special_district:{special[4]}"
    found[key] = special
    return [key]


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "propositions.parquet")
    with connect() as conn:
        found: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        names: dict[str, list[str]] = {}
        for scheme, code, polity, area, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, a.id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place') and left(c.code, 2) = '22'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            names.setdefault(ceda._city(text) if scheme == "us.place" else ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/la/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Louisiana parish and municipal propositions on the November 3, 2026 ballot, with official wording",
            polity="US-LA",
        )
        system = cs.key("us.electoral_system", "referendum")
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL)
        cs.artifact(raw.cached(SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:LA")
        elections: set[UUID] = set()
        kept = 0
        seen: set[tuple[str, str]] = set()
        for row in frame.iter_rows(named=True):
            entity = re.sub(r"\s*\(.*$", "", row["label"])
            if entity.startswith(("City of", "Town of", "Village of")):
                options = names.get(ceda._city(re.sub(r"^(City|Town|Village) of ", "", entity)), [])
            elif entity.startswith(("Parishwide", "PW ")):
                options = names.get(ceda._core(f"{row['parish']} Parish"), [])
            else:
                options = mint_special(conn, cs, found, names, row, slugs)
            options = [option for option in options if option in found]
            if len(options) != 1:
                print("no polity:", row["parish"], row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[options[0]]
            election = cs.key("us.election", f"{DAY}:LA:{code}")
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
            tag = slug(row["title"])
            if (code, tag) in seen:
                tag = f"{tag}-{row['referendum']}"
            seen.add((code, tag))
            contest_key = f"{DAY}:LA:{code}:measure:{tag}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{tag}"
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
            cs.add("name", entity_id=contest, text=f"{DAY.year} {place}: {row["title"]}", lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
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
                body=re.sub(r"\s+", " ", row["text"]).strip(),
                url=f"{DETAIL}{row['referendum']}",
                source_id=source,
            )
        print(f"{kept} of {frame.height} referendums")
        return cs.write()
