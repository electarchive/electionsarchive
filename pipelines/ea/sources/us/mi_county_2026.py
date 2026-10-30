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

SOURCE = "us.mi_county_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
MANUAL = DATA / "inbox" / "manual" / "MF-374"
COUNTIES = {
    "26081": (
        "https://www.kentcountymi.gov/DocumentCenter/View/8870/November-3-2026-Candidates-and-Proposals-PDF",
        "kent-county-mi/11032026 Candidates and Proposals_202610261659007429.pdf",
        "Kent County Clerk",
    ),
}
TITLE = "Official candidates and proposals list, November 3, 2026 general election"
CENTERED = re.compile(r"^\s{40,}\S")
NOISE = re.compile(r"^\s*(Updated \d|.*November 3, 2026 General Election\s+Page|Official Candidates and Proposals List|November 3, 2026 General Election\s*$)")
SECTIONS = {"County Proposals", "City/Township Proposals", "Local School District", "Local School Districts", "Other Proposals", "Library Proposals"}


def fetch() -> None:
    for county, (url, file, _) in COUNTIES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = MANUAL / file
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="application/pdf",
            )


def parse() -> None:
    rows = []
    for county, (url, _, _) in COUNTIES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        lines = [line for line in text.replace("\f", "\n").split("\n")]
        start = next(index for index, line in enumerate(lines) if line.strip() == "County Proposals")
        heads: list[str] = []
        body: list[str] = []
        for index in range(start, len(lines)):
            line = lines[index]
            if not line.strip() or NOISE.match(line):
                continue
            label = line.strip()
            if CENTERED.match(line):
                following = next((other.strip() for other in lines[index + 1 :] if other.strip()), "")
                if following.startswith("Official Candidates and Proposals List"):
                    continue
                if label in SECTIONS:
                    continue
                if body:
                    if heads:
                        rows.append(
                            {
                                "county": county,
                                "jurisdiction": heads[0],
                                "label": " – ".join(heads[1:]) or heads[0],
                                "wording": re.sub(r"\s+", " ", " ".join(body)).strip(),
                                "url": url,
                            }
                        )
                    heads, body = [], []
                heads.append(label)
            else:
                body.append(label)
        if body and heads:
            rows.append(
                {
                    "county": county,
                    "jurisdiction": heads[0],
                    "label": " – ".join(heads[1:]) or heads[0],
                    "wording": re.sub(r"\s+", " ", " ".join(body)).strip(),
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
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.town', 'us.school_district') and left(c.code, 2) = '26'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            names.setdefault(ceda._city(text) if scheme == "us.place" else ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/mi/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Michigan county, city, township, and school district proposals on the November 3, 2026 ballot, with official wording",
            polity="US-MI",
        )
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for county, (url, _, publisher) in COUNTIES.items():
            sources[county] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[county])
        parent = cs.key("us.election", f"{DAY}:MI")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            jurisdiction = row["jurisdiction"]
            if jurisdiction.startswith(("City of", "Village of")):
                options = names.get(ceda._city(re.sub(r"^(City|Village) of ", "", jurisdiction)), [])
            else:
                options = names.get(ceda._core(jurisdiction), [])
            options = [option for option in options if not option.startswith("us.town:") or option[8:13] == row["county"]]
            if len(options) != 1:
                print("no polity:", jurisdiction, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[options[0]]
            election = cs.key("us.election", f"{DAY}:MI:{code}")
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
            contest_key = f"{DAY}:MI:{code}:measure:{slug(label)}"
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
            for position, answer in enumerate(("Yes", "No")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{slug(answer)}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add("citation", entity_id=contest, source_id=sources[row["county"]])
            cs.add(
                "ballot_question_text",
                contest_id=contest,
                kind="text",
                lang="en",
                body=row["wording"],
                url=COUNTIES[row["county"]][0],
                source_id=sources[row["county"]],
            )
        print(f"{kept} of {frame.height} propositions")
        return cs.write()
