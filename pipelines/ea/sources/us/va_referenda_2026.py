import html
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

SOURCE = "us.va_referenda_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
URL = "https://www.elections.virginia.gov/casting-a-ballot/candidate-list/november-2026-proposed-constitutional-amendments/"
PUBLISHER = "Virginia Department of Elections"
TITLE = "Local referendums to be voted on at the November 3, 2026 election"


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        content = subprocess.run(
            ["curl", "-sSL", "-m", "60", "-A", "Mozilla/5.0", URL], capture_output=True, check=True
        ).stdout
        raw.store(SOURCE, URL, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")


def parse() -> None:
    page = raw.cached(SOURCE, URL).path.read_text()
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.DOTALL):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        ]
        if len(cells) >= 6 and cells[3] in ("Locality", "District"):
            rows.append(
                {
                    "locality": cells[0],
                    "area": cells[3],
                    "label": cells[4].title() if cells[4].isupper() else cells[4],
                    "wording": re.sub(r"^(?i:question\s*\d*:\s*)", "", cells[5]).strip(),
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "referenda.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "referenda.parquet")
    with connect() as conn:
        found: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        names: dict[str, str] = {}
        for scheme, code, polity, area, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, a.id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place') and left(c.code, 2) = '51'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            if scheme == "us.county":
                names[ceda._core(re.sub(r"(?i) city, VA$", " city", text))] = key
            else:
                names.setdefault(f"town {ceda._core(text)}", key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/va/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Virginia local referendums on the November 3, 2026 ballot, with official wording",
            polity="US-VA",
        )
        system = cs.key("us.electoral_system", "referendum")
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL)
        cs.artifact(raw.cached(SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:VA")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            if row["area"] == "District":
                town = re.search(r"Town of ([A-Z][A-Za-z ]+?)(?:,| at | be |\?| to )", row["wording"])
                option = names.get(f"town {ceda._core(town[1])}") if town else None
            else:
                option = names.get(ceda._core(row["locality"].replace("&", "and").replace(" CITY", " city")))
            if option is None:
                print("no polity:", row["locality"], row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:VA:{code}")
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
            contest_key = f"{DAY}:VA:{code}:measure:{slug(label)}"
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
            for position, answer in enumerate(("Yes", "No")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{slug(answer)}"),
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
        print(f"{kept} of {frame.height} referenda")
        return cs.write()
