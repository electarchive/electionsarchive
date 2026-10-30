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

SOURCE = "us.co_tabor_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
MANUAL = DATA / "inbox" / "manual" / "MF-376"
COUNTIES = {
    "08059": (
        "https://www.jeffco.us/DocumentCenter/View/59081/2026-General-TABOR-Booklet-PDF",
        None,
        "Jefferson County Clerk and Recorder",
    ),
    "08069": (
        "https://www.larimer.gov/sites/default/files/tabor-notice-07-final.pdf",
        "CO-Larimer/tabor-notice-07-final.pdf",
        "Larimer County Clerk and Recorder",
    ),
    "08041": (
        "https://epc-assets.elpasoco.com/wp-content/uploads/sites/5/2026-General-Election-TABOR-Accessible.pdf",
        None,
        "El Paso County Clerk and Recorder",
    ),
    "08005": (
        "https://www.arapahoeco.gov/Clerk%20and%20Recorder/ArapahoeVotes/Documents/TABOR%20Notice%202026.pdf?t=202609291813040",
        None,
        "Arapahoe County Clerk and Recorder",
    ),
    "08077": (
        "https://www.mesacounty.us/sites/default/files/2026-09/2026%20General%20Election%20TABOR%20Booklet%20%5BADA%5D_0.pdf",
        None,
        "Mesa County Clerk and Recorder",
    ),
    "08035": (
        "https://www.douglas.co.us/documents/2026-general-election-ballot-issue-notices-booklet.pdf",
        "CO-Douglas/2026-general-election-ballot-issue-notices-booklet.pdf",
        "Douglas County Clerk and Recorder",
    ),
}
TITLE = "TABOR notices, November 3, 2026 general election"
ENTITY_END = re.compile(r"\s*(?:«\s*)?NOTICE OF ELECTION")


def fetch() -> None:
    for url, file, _ in COUNTIES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            if file is None:
                content = subprocess.run(["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True).stdout
                retrieved = datetime.now(UTC)
            else:
                path = MANUAL / file
                content = path.read_bytes()
                retrieved = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            raw.store(SOURCE, url, content, suffix=".pdf", retrieved_at=retrieved, media_type="application/pdf")


def titled(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text.title().replace(" Of ", " of ").replace(" Re-", " RE-") if text.isupper() else text


def question(lines: list[str], start: int) -> str:
    body = []
    for follow in lines[start : start + 90]:
        if re.match(r"^\s*(Fiscal Information|□)", follow):
            break
        if follow.strip():
            body.append(follow.strip())
        if follow.rstrip().endswith("?"):
            break
    return re.sub(r"\s+", " ", " ".join(body))


def jeffco_rows(lines: list[str]) -> list[dict[str, str]]:
    rows = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(.+?) ((?:Ballot|Referred) (?:Issue|Question|Measure))\s+(\w+)\s*$", line)
        if match is None or match[1].startswith("Summary of"):
            continue
        rows.append({"entity": match[1], "label": f"{match[2]} {match[3]}", "wording": question(lines, index + 1)})
    return rows


def elpaso_rows(lines: list[str]) -> list[dict[str, str]]:
    rows = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(.+?) ((?:Ballot|Referred) (?:Issue|Question|Measure))\s+(\w+)\s*$", line)
        if match is None or not any(previous.strip().startswith("Election Hours") for previous in lines[max(index - 5, 0) : index]):
            continue
        entity = f"{lines[index - 1].strip()} {match[1]}" if match[1].startswith("(") else match[1]
        rows.append({"entity": entity, "label": f"{match[2]} {match[3]}", "wording": question(lines, index + 1)})
    return rows


ARAPAHOE_ISSUES = (
    ("Arapahoe County School District No. 6", "4A"),
    ("Bennett Fire Protection District", "7F"),
    ("Bennett Fire Protection District", "7G"),
    ("Bennett School District No. 29J", "5A"),
    ("City of Aurora", "3A"),
    ("City of Aurora", "3B"),
    ("City of Aurora", "3C"),
    ("City of Centennial", "2A"),
    ("City of Sheridan", "2D"),
    ("Hills at Cherry Creek Metropolitan District", "6A"),
)
ARAPAHOE_END = re.compile(r"^\s*(FISCAL INFORMATION|Total District|Actual Historical|Summary of Written)")


def arapahoe_rows(lines: list[str]) -> list[dict[str, str]]:
    rows = []
    cursor = next(index for index, line in enumerate(lines) if "Your Voting Options" in line and "...." not in line)
    for entity, issue in ARAPAHOE_ISSUES:
        cursor = next(index for index in range(cursor, len(lines)) if re.match(rf"^\s*(.+ )?BALLOT ISSUE {issue}\b", lines[index]))
        start = next(index for index in range(cursor, cursor + 20) if lines[index].lstrip().startswith("SHALL"))
        end = next(index for index in range(start, start + 80) if ARAPAHOE_END.match(lines[index]))
        wording = re.sub(r"\s+", " ", " ".join(line.strip() for line in lines[start:end] if line.strip() and not line.strip().isdigit() and line.strip().upper() != entity.upper())).strip()
        rows.append({"entity": entity, "label": f"Ballot Issue {issue}", "wording": wording})
    return rows


def mesa_rows(lines: list[str]) -> list[dict[str, str]]:
    rows = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(.+?) BALLOT ISSUE\s+(\w+)\s*$", line)
        if match is None or not any(previous.strip().startswith("Election Hours") for previous in lines[max(index - 8, 0) : index]):
            continue
        rows.append({"entity": titled(match[1]).replace("Palsade", "Palisade").replace("Orchard Mesa Pool", "City of Grand Junction"), "label": f"Ballot Issue {match[2]}", "wording": question(lines, index + 1)})
    return rows


def larimer_rows(lines: list[str]) -> list[dict[str, str]]:
    rows = []
    for index, line in enumerate(lines):
        if "TO ALL REGISTERED VOTERS" not in line:
            continue
        header = " ".join(follow.strip() for follow in lines[index : index + 2])
        entity = ENTITY_END.split(header.split("«", 1)[1].replace("«", " "))[0]
        entity = re.sub(r"\s+", " ", entity).strip()
        mark = next(i for i in range(index, index + 12) if lines[i].strip().startswith("BALLOT ISSUE"))
        label = next(follow.strip() for follow in lines[mark + 1 : mark + 8] if follow.strip() and "Election Date" not in follow)
        start = next(i for i in range(mark, mark + 20) if re.match(r"^\s*Telephone", lines[i])) + 1
        rows.append({"entity": re.sub(r", Colorado$", "", titled(entity)), "label": f"Ballot Issue {label}", "wording": question(lines, start)})
    return rows


def douglas_rows(lines: list[str]) -> list[dict[str, str]]:
    rows = []
    entity = ""
    for index, line in enumerate(lines):
        if line.startswith("Election Date"):
            entity = next(
                previous.strip()
                for previous in reversed(lines[index - 4 : index])
                if previous.strip() and not previous.strip().endswith(("MEASURE", "DEBT", "TAXES", "TAXES AND INCREASE DEBT"))
                and "Counties" not in previous
            )
        if "Title and Text" not in line:
            continue
        heading = line.split(":", 1)[1].strip() or lines[index + 1].strip()
        label = re.search(r"(?i)ballot issue (?:no\. )?(\w+)", heading) or re.search(r"Ballot Issue (\w+) Title", line)
        start = index + 1
        if re.search(r"(?i)ballot issue", heading) or heading.upper().startswith("REFERRED"):
            start += 0 if line.split(":", 1)[1].strip() else 1
        while not lines[start].strip().startswith("SHALL") and "SHALL" not in lines[start]:
            start += 1
        rows.append({"entity": titled(entity), "label": f"Ballot Issue {label[1].upper()}", "wording": question(lines, start)})
    return rows


PARSERS = {"08059": jeffco_rows, "08077": mesa_rows, "08005": arapahoe_rows, "08041": elpaso_rows, "08069": larimer_rows, "08035": douglas_rows}


def parse() -> None:
    rows = []
    for county, (url, _, _) in COUNTIES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        for row in PARSERS[county](text.replace("\f", "\n").split("\n")):
            rows.append({**row, "county": county, "url": url})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows).unique(["entity", "label"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height)


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
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district') and left(c.code, 2) = '08'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            names.setdefault(ceda._city(text) if scheme == "us.place" else ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/co/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Jefferson County, Colorado tax and debt questions on the November 3, 2026 ballot, with official wording",
            polity="US-CO",
        )
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for county, (url, _, publisher) in COUNTIES.items():
            sources[county] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[county])
        parent = cs.key("us.election", f"{DAY}:CO")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            entity = row["entity"]
            county_key = f"us.county:{row['county']}"
            source = sources[row["county"]]
            if re.match(r"(?i)(city|town) of ", entity):
                options = names.get(ceda._city(re.sub(r"(?i)^(city|town) of ", "", entity)), [])
            elif county_key in found and entity == found[county_key][2]:
                options = [county_key]
            elif "school district" in entity.lower():
                options = names.get(ceda._core(entity), [])
            else:
                options = []
            option = options[0] if len(options) == 1 else None
            if option is None and not options and f"special:{row['county']}:{entity}" in found:
                option = f"special:{row['county']}:{entity}"
            elif option is None and not options and county_key in found:
                county, county_area, _, county_path, county_code = found[county_key]
                minted = govmint.special(conn, cs, "CO", (county, county_area, county_path, county_code), entity, slugs)
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:CO:{code}")
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
            contest_key = f"{DAY}:CO:{code}:measure:{slug(label)}"
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
                url=row["url"],
                source_id=source,
            )
        print(f"{kept} of {frame.height} referenda")
        return cs.write()
