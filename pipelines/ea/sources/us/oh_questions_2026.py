import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda
from ea.store import DATA, connect

SOURCE = "us.oh_questions_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
FILE = DATA / "inbox" / "manual" / "MF-367" / "OH" / "2026-11-general-qandi.xlsx"
URL = "https://www.ohiosos.gov/elections/elections-calendar-and-results/"
PUBLISHER = "Ohio Secretary of State"
TITLE = "November 3, 2026 General Election: questions and issues"
SCHEMES = {"Township": "us.town", "Village": "us.place", "City": "us.place", "County": "us.county", "School District": "us.school_district"}


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
        return
    except LookupError:
        pass
    raw.store(
        SOURCE,
        URL,
        FILE.read_bytes(),
        suffix=".xlsx",
        retrieved_at=datetime.fromtimestamp(FILE.stat().st_mtime, UTC),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def clean(value: object) -> str | None:
    text = re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""
    return None if text in ("", "N/A", "None") else text


def describe(row: dict[str, object]) -> str:
    kind = clean(row["Question Type"]) or "Question"
    change = clean(row["Description"])
    head = f"{kind}, {change.lower()}" if change else kind
    details = []
    if (mills := clean(row["Millage"])) is not None:
        details.append(f"{mills} mills")
    if (percent := clean(row["Percent"])) is not None:
        details.append(f"{float(percent) * 100:g} percent")
    if (amount := clean(row["Dollar Amount"])) is not None:
        details.append(f"${amount}")
    if (years := clean(row["Length of Levy in Years or Continuing Period of Time (cpt)"])) is not None:
        details.append("continuing" if years.lower() == "cpt" else f"{years} years")
    if (start := clean(row["Commencing Year or Effective Date"])) is not None:
        details.append(f"beginning {start[:4]}")
    text = head + (": " + ", ".join(details) if details else "")
    purpose = clean(row["Purpose"])
    return f"{text}. Purpose: {purpose}." if purpose else f"{text}."


def parse() -> None:
    frame = pl.read_excel(raw.cached(SOURCE, URL).path, sheet_name="Issues Summary", infer_schema_length=0)
    rows = []
    for row in frame.iter_rows(named=True):
        kind = clean(row["Subdivision Type"])
        name = clean(row["Subdivision Name"])
        if kind not in SCHEMES or name is None or clean(row["County"]) is None:
            continue
        rows.append(
            {
                "county": clean(row["County"]),
                "kind": kind,
                "name": name,
                "text": describe(row),
                "label": clean(row["Purpose"]) or clean(row["Question Type"]) or "Question",
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    out = pl.DataFrame(rows)
    out.write_parquet(PARSED / "questions.parquet")
    print(out.group_by("kind").len().sort("kind").rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "questions.parquet")
    with connect() as conn:
        counties: dict[str, str] = {}
        towns: dict[tuple[str, str], str] = {}
        places: dict[str, list[str]] = {}
        schools: dict[str, list[str]] = {}
        found: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        for scheme, code, polity, area, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, a.id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.town', 'us.school_district')
                 and left(c.code, 2) = '39'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            if scheme == "us.county":
                counties[ceda._core(text)] = code
            elif scheme == "us.town":
                towns[(code[:5], ceda._core(text))] = key
            elif scheme == "us.place":
                places.setdefault(ceda._city(text), []).append(key)
            else:
                schools.setdefault(ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/oh/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio township, village, city, county, and school district questions on the November 3, 2026 ballot, from the Secretary of State's issue summaries",
            polity="US-OH",
        )
        system = cs.key("us.electoral_system", "referendum")
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL)
        cs.artifact(raw.cached(SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:OH")
        elections: set[UUID] = set()
        counts: dict[str, int] = {}
        kept = 0
        for row in frame.sort("kind", "county", "name", "label").iter_rows(named=True):
            name = row["name"]
            if row["kind"] == "County":
                code = counties.get(ceda._core(f"{name} County"))
                key = f"us.county:{code}" if code is not None else None
            elif row["kind"] == "Township":
                county = counties.get(ceda._core(f"{row['county']} County"))
                key = towns.get((county, ceda._core(name if "Township" in name else f"{name} Township"))) if county else None
            elif row["kind"] == "School District":
                options = schools.get(ceda._core(name), [])
                key = options[0] if len(options) == 1 else None
            else:
                options = places.get(ceda._city(name), [])
                key = options[0] if len(options) == 1 else None
            if key is None or key not in found:
                print("no polity:", row["kind"], row["county"], name)
                continue
            kept += 1
            polity, area, place, path, code = found[key]
            election = cs.key("us.election", f"{DAY}:OH:{code}")
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
            counts[key] = counts.get(key, 0) + 1
            stem = slug(label)
            contest_key = f"{DAY}:OH:{code}:question:{stem}-{counts[key]}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{stem}-{counts[key]}"
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
                    id=cs.key("us.option", f"{contest_key}:{answer.lower()}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add("note", entity_id=contest, kind="context", body_md=row["text"])
            cs.add("citation", entity_id=contest, source_id=source)
        print(f"{kept} of {frame.height} questions")
        return cs.write()
