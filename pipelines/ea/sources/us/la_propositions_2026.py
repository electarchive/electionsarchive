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
from ea.sources.us import ceda
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


FUNCTIONS = (
    (r"fire", "fire-protection"),
    (r"hospital", "hospital"),
    (r"ambulance", "ambulance"),
    (r"drainage", "drainage"),
    (r"road lighting", "lighting"),
    (r"road", "road"),
    (r"cemetery", "cemetery"),
    (r"recreation", "park"),
    (r"water", "water"),
    (r"sales tax", "sales-tax"),
    (r"crime", "improvement"),
    (r"school", "school"),
    (r"economic|industrial", "economic-development"),
)


def display_name(label: str) -> str:
    text = re.sub(r"\s*\(.*$|\s+Prop\.? No\.? \d+ of \d+$", "", label)
    text = re.sub(r"\bDist\.", "District", text)
    text = re.sub(r"\bNumber\b", "No.", text)
    text = re.sub(r"\bPrev\.", "Prevention", text)
    text = re.sub(r"\bNeigh\.", "Neighborhood", text)
    text = re.sub(r"\bImp\.", "Improvement", text)
    text = re.sub(r"\bSub\.", "Subdivision", text)
    text = re.sub(r"\bDev\.", "Development", text)
    return re.sub(r"\s+", " ", text).strip()


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
    display = display_name(row["label"])
    function = next((value for pattern, value in FUNCTIONS if re.search(pattern, display, re.IGNORECASE)), "district")
    code = f"{county_code}:{function}:{slug(display)}"
    key = f"us.special_district:{code}"
    if key in found:
        return [key]
    path = f"{county_path}/{slug(display)}"
    if path in slugs:
        existing = conn.execute(
            """select t.polity_id, t.area_id, c.code from polity_territory t
               join area_code c on c.area_id = t.area_id where t.polity_id = %s limit 1""",
            [slugs[path]],
        ).fetchone()
        if existing is None:
            return []
        found[key] = (existing[0], existing[1], display, path, existing[2])
        return [key]
    polity = cs.key("us.polity", f"special:{code}")
    slugs[path] = polity
    area = cs.area("us.special_district", code)
    found[key] = (polity, area, display, path, code)
    if polity in cs.minted:
        cs.add("polity", id=polity, kind="special_district", iso_code=None)
        cs.add("name", entity_id=polity, text=display, lang="en", kind="official", is_preferred=True)
        cs.add("polity_parent", polity_id=polity, parent_id=county)
        cs.add("slug", path=path, entity_id=polity)
        cs.add("area", id=area, scheme_code="us.special_district")
        cs.add("area_code", area_id=area, code=code, valid_from=None)
        cs.add("area_parent", area_id=area, parent_id=county_area)
        cs.add("name", entity_id=area, text=f"{display}, LA", lang="en", kind="official", is_preferred=True)
        cs.add("polity_territory", polity_id=polity, area_id=area)
        cs.add(
            "reporting_unit",
            id=cs.unit("us.special_district", code),
            polity_id=polity,
            scheme_code="us.special_district",
            code=code,
            area_relation="equals",
            area_id=area,
            kind="standard",
            valid_from=None,
        )
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
