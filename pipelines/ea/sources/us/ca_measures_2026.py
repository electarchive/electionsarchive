import html
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda
from ea.store import DATA, connect

SOURCE = "us.ca_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
LOS_ANGELES = "https://content.lavote.gov/docs/rrcc/documents/measures-appearing-on-the-ballot---november-3-2026-rev-8-14-2026-v-4.pdf"
SAN_FRANCISCO = "https://api.sf.gov/documents/61682/Ballot_Questions_November_2026.pdf"
SAN_BERNARDINO = "https://elections.sbcounty.gov/elections/2026/1103/measures/"
SOURCES = {
    SAN_BERNARDINO: ("06071", "San Bernardino County Registrar of Voters", "Measures on the November 3, 2026 ballot, notices of measures"),
    LOS_ANGELES: ("06037", "Los Angeles County Registrar-Recorder/County Clerk", "Measures appearing on the ballot, November 3, 2026"),
    SAN_FRANCISCO: ("06075", "San Francisco Office of the City Attorney", "Ballot questions for the November 3, 2026 election"),
}


def notices() -> list[tuple[str, str, str, str]]:
    listing = raw.cached(SOURCE, SAN_BERNARDINO).path.read_text(encoding="utf-8", errors="replace")
    found = []
    for row in listing.split("<tr>")[2:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        link = re.search(r'href="([^"]+)"', cells[0] + (cells[1] if len(cells) > 1 else "")) if cells else None
        if link is None or len(cells) < 6:
            continue
        plain = [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in cells]
        found.append((plain[0], link[1], plain[1], plain[5]))
    return found


def download(url: str) -> None:
    try:
        raw.cached(SOURCE, url)
        return
    except LookupError:
        pass
    response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=120)
    response.raise_for_status()
    raw.store(
        SOURCE,
        url,
        response.content,
        suffix=".html" if url == SAN_BERNARDINO else ".pdf",
        retrieved_at=datetime.now(UTC),
        media_type=response.headers.get("content-type", ""),
    )


def fetch() -> None:
    for url in SOURCES:
        download(url)
    for _, notice, _, _ in notices():
        download(notice)


def parse() -> None:
    rows = []

    def pdf(url: str, layout: bool = True) -> str:
        return subprocess.run(
            ["pdftotext", *(["-layout"] if layout else []), str(raw.cached(SOURCE, url).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    place, threshold, current = None, None, None
    for line in pdf(LOS_ANGELES).splitlines():
        if found := re.match(
            r"^\s+(LOS ANGELES COUNTY|(?:CITY|TOWN) OF [A-Z .'&-]+|[A-Z][A-Z .'&-]*(?:DISTRICT|COLLEGE)[A-Z .'&-]*?)\s+–\s+\(\d+\)\s*$",
            line,
        ):
            place, current = found[1], None
        elif found := re.match(r"^\s+VOTE PASSAGE\s+–\s+(.*)$", line):
            threshold = found[1].strip()
        elif found := re.match(r"^([A-Z0-9]{1,4})\s{2,}(\S.*)$", line):
            current = {
                "url": LOS_ANGELES,
                "place": place,
                "letter": found[1],
                "threshold": threshold,
                "lines": [found[2].strip()],
            }
            rows.append(current)
        elif re.match(r"^\s+Supporters:|.*\|Page|.*Continued$", line):
            current = None
        elif current is not None and line.strip():
            current["lines"].append(line.strip())
    for letter, notice, place, threshold in notices():
        if "/Notice_" in notice:
            found = re.search(
                r"Measure\s+[“\"]?[^”\"\n]*[”\"]?\s*\n\s*(.+?)\s*\n\s*[A-Z ]+ YES", pdf(notice), re.S
            )
        else:
            found = re.search(
                r"Ballot Question\.\s*That the .*?following question:\s*(.+?)\s*\n\s*(?:YES|NO)\b",
                pdf(notice, False),
                re.S,
            )
            if found is not None:
                found = re.search(
                    r"(.+)", re.sub(r"RESOLUTION NO\. [\w-]+\s*PAGE \d+", "", found[1]), re.S
                )
        if found is None:
            print("no wording:", place, letter)
            continue
        rows.append(
            {
                "url": SAN_BERNARDINO,
                "place": place.upper(),
                "letter": letter,
                "threshold": f"{threshold} of the votes cast",
                "lines": [found[1].strip("“”\" ")],
            }
        )
    text = re.sub(r"\s+", " ", pdf(SAN_FRANCISCO))
    for title, question in re.findall(r"Proposition entitled “([^”]+)”: “([^”]+)”", text):
        rows.append(
            {
                "url": SAN_FRANCISCO,
                "place": "CITY AND COUNTY OF SAN FRANCISCO",
                "letter": None,
                "threshold": None,
                "lines": [f"{title}. {question}"],
            }
        )
    frame = pl.DataFrame(
        [
            {key: value for key, value in row.items() if key != "lines"}
            | {"text": re.sub(r"\s+", " ", " ".join(row["lines"])).strip()}
            for row in rows
        ]
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.group_by("url").len().rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        found: dict[str, tuple[str, UUID, UUID, str, str, str]] = {}
        cities: dict[str, str] = {}
        schools: dict[str, list[str]] = {}
        for scheme, code, polity, area, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, a.id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district') and c.code like '06%%'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (scheme, polity, area, text, path, code)
            if scheme == "us.place":
                for part in re.findall(r"[^()]+", text):
                    cities[ceda._city(part)] = key
            elif scheme == "us.school_district":
                schools.setdefault(ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/ca/%%'").fetchall())
        known = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.election' and value like %s", [f"{DAY}:CA:%"]
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California county, city, and school district measures on the November 3, 2026 ballot, with official wording",
            polity="US-CA",
        )
        system = cs.key("us.electoral_system", "referendum")
        parent = cs.key("us.election", f"{DAY}:CA")
        sources: dict[str, UUID] = {}
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            place = str(row["place"])
            county, publisher, title = SOURCES[row["url"]]
            titled = place.title().replace("Of ", "of ")
            if place == "LOS ANGELES COUNTY":
                key = f"us.county:{county}"
            elif place.startswith(("CITY OF", "TOWN OF", "CITY AND COUNTY")):
                key = cities.get(ceda._city(place.removeprefix("CITY AND COUNTY OF ")), f"us.county:{county}")
            else:
                options = schools.get(ceda._core(place), [])
                key = options[0] if len(options) == 1 else None
            if key is None or key not in found:
                print("no polity:", place, row["letter"])
                continue
            kept += 1
            scheme, polity, area, name, path, code = found[key]
            if row["url"] not in sources:
                source = sources[row["url"]] = cs.source(publisher, title)
                cs.add("source", id=source, kind="official_body", publisher=publisher, title=title, url=row["url"])
                cs.artifact(raw.cached(SOURCE, row["url"]), source)
            source = sources[row["url"]]
            election_key = f"{DAY}:CA:{code}"
            if scheme == "us.school_district" and election_key not in known:
                election_key = f"{DAY}:CA:sd{code}"
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
            heading = row["text"].split(". ")[0][:60] if row["letter"] is None else None
            label = f"Measure {row['letter']}" if row["letter"] else heading
            contest_key = f"{DAY}:CA:{code}:measure:{slug(label)}"
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
            if row["threshold"]:
                cs.add("note", entity_id=contest, kind="context", body_md=f"Passage requires: {row['threshold']}.")
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
