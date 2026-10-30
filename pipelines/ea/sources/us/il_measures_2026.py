import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda, govmint
from ea.store import DATA, connect

SOURCE = "us.il_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
URL = "https://wincoil.gov/media/attachments/2026/08/27/questions-nov-3-2026-2.pdf"
PUBLISHER = "Winnebago County Clerk"
TITLE = "Referenda questions, November 3, 2026 general election"
COUNTY = "us.county:17201"


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        content = subprocess.run(["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", URL], capture_output=True, check=True).stdout
        raw.store(SOURCE, URL, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    text = re.sub(r"(?m)^(\s*)(YES|NO)\s*$", "", text)
    text = re.sub(r"(?m)\s{20,}(YES|NO)\s*$", "", text)
    parts = re.split(r"(?m)^\s*(NON-BINDING ADVISORY PUBLIC QUESTION CONCERNING[^\n]*(?:\n\s+[A-Z ]+(?=\n))?|PROPOSITION TO INCREASE THE PROPERTY TAX LIMITING RATE)\s*$", text)
    rows = []
    for head, body in zip(parts[1::2], parts[2::2], strict=True):
        quote = re.search(r"[“\"](.+?\?)[”\"]", body, re.S)
        wording = re.sub(r"\s+", " ", quote[1]).replace("NON- ", "NON-").strip()
        if head.startswith("PROPOSITION"):
            wording = re.sub(r"\s+", " ", body.split("I hereby certify")[0]).strip()
            entity = "Pecatonica Fire Protection District"
            label = "Proposition to Increase the Property Tax Limiting Rate"
        else:
            entity = "Winnebago County"
            label = re.sub(r"\s+", " ", head.replace("NON-BINDING ADVISORY PUBLIC QUESTION CONCERNING", "")).strip().title()
            label = f"Advisory Question: {label}"
        wording = re.sub(r"\s+", " ", re.sub(r"(?<=\s)(YES|NO)(?=\s)", " ", wording)).strip()
        rows.append({"entity": entity, "label": label, "wording": wording})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
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
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district') and left(c.code, 2) = '17'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            names.setdefault(ceda._city(text) if scheme == "us.place" else ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/il/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Winnebago County, Illinois advisory questions and property tax proposition on the November 3, 2026 ballot, with official wording",
            polity="US-IL",
        )
        system = cs.key("us.electoral_system", "referendum")
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL)
        cs.artifact(raw.cached(SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:IL")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            entity = row["entity"]
            if re.match(r"(?i)(city|town) of ", entity):
                options = names.get(ceda._city(re.sub(r"(?i)^(city|town) of ", "", entity)), [])
            elif "school district" in entity.lower():
                options = names.get(ceda._core(entity), [])
            else:
                options = []
            option = options[0] if len(options) == 1 else None
            if option is None and not options and f"special:{entity}" in found:
                option = f"special:{entity}"
            elif option is None and not options and COUNTY in found:
                county, county_area, _, county_path, county_code = found[COUNTY]
                minted = govmint.special(conn, cs, "IL", (county, county_area, county_path, county_code), entity, slugs)
                if minted is not None:
                    option = f"special:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:IL:{code}")
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
            contest_key = f"{DAY}:IL:{code}:measure:{slug(label)}"
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
