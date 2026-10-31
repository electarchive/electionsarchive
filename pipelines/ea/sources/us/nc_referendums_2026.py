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

SOURCE = "us.nc_referendums_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
URL = "https://s3.amazonaws.com/dl.ncsbe.gov/Elections/2026/Candidate%20Filing/referendums_20261103.pdf"
PUBLISHER = "North Carolina State Board of Elections"
TITLE = "Referendum choices list, November 3, 2026"
CHOICE = re.compile(r"^(For|Against|Yes|No)\s+(.*)$")
TITLE_WORDS = re.compile(r"REFERENDUM|ELECTION|BONDS")
NOISE = re.compile(r"CONT_CAND_rpt|^\s*\w{3} \d{2}, 20\d\d \d|BOARD OF ELECTIONS|^CHOICE\s+DESCRIPTION|^\s*$")


def fetch() -> None:
    import httpx

    try:
        raw.cached(SOURCE, URL)
        return
    except LookupError:
        pass
    response = httpx.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=120, follow_redirects=True)
    response.raise_for_status()
    raw.store(
        SOURCE,
        URL,
        response.content,
        suffix=".pdf",
        retrieved_at=datetime.now(UTC),
        media_type="application/pdf",
    )


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-raw", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.replace("\f", "\n").split("\n"):
        line = line.strip()
        if NOISE.search(line):
            continue
        choice = CHOICE.match(line)
        if choice is not None:
            if current is not None and f"{choice[1]}|" not in current["options"]:
                current["options"] += choice[1] + "|"
                if current["text"] == "":
                    current["text"] = choice[2]
            continue
        if line == line.upper() and TITLE_WORDS.search(line):
            current = (
                None if line.startswith("CONSTITUTIONAL AMENDMENT") else {"title": line, "options": "", "text": ""}
            )
            if current is not None:
                rows.append(current)
        elif current is not None and current["options"].count("|") == 1 and not current["options"].endswith("Against|"):
            current["text"] += " " + line
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).filter(pl.col("text") != "").unique("title", keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "referendums.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "referendums.parquet")
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
               where a.scheme_code in ('us.county', 'us.place') and left(c.code, 2) = '37'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            names.setdefault(ceda._city(text) if scheme == "us.place" else ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/nc/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Carolina county and municipal referendums on the November 3, 2026 ballot, with official wording",
            polity="US-NC",
        )
        system = cs.key("us.electoral_system", "referendum")
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL)
        cs.artifact(raw.cached(SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:NC")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            title = row["title"]
            match = re.match(
                r"(.+? COUNTY|(?:CITY|TOWN|VILLAGE) OF [A-Z .'-]+?|[A-Z]+) (?=[A-Z ]*(?:BOND|LOCAL SALES|MALT|MIXED|UNFORTIFIED|ABC|BOARD|CHARTER|REFERENDUM|ELECTION))",
                title,
            )
            entity = match[1] if match else ""
            label = title.removeprefix(entity).strip().title() if entity else title.title()
            if entity.startswith(("CITY OF", "TOWN OF", "VILLAGE OF")):
                words = re.sub(r"^(CITY|TOWN|VILLAGE) OF ", "", title).split()
                options = next(
                    (
                        names[ceda._city(" ".join(words[:count]))]
                        for count in range(len(words), 0, -1)
                        if ceda._city(" ".join(words[:count])) in names
                    ),
                    [],
                )
            else:
                options = names.get(ceda._core(entity if entity.endswith("COUNTY") else f"{entity} COUNTY"), [])
            options = [option for option in options if option in found]
            if len(options) != 1:
                print("no polity:", title)
                continue
            kept += 1
            polity, area, place, path, code = found[options[0]]
            election = cs.key("us.election", f"{DAY}:NC:{code}")
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
            contest_key = f"{DAY}:NC:{code}:measure:{slug(label)}"
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
            for position, answer in enumerate(a for a in row["options"].split("|") if a):
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
                url=URL,
                source_id=source,
            )
        print(f"{kept} of {frame.height} referendums")
        return cs.write()
