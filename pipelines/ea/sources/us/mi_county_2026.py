import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import civmint, govmint
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
    "26099": (
        "https://macombgovdocs.org/clerk/pdfs/elect-2026-11-03-proposals.pdf",
        "macomb-county-mi/elect-2026-11-03-proposals.pdf",
        "Macomb County Clerk",
    ),
    "26125": (
        "https://www.oakgov.com/media/12731/download",
        "../MF-376/MI-Oakland/11032026-proposal-list.pdf",
        "Oakland County Clerk",
    ),
    "26163": (
        "https://www.waynecounty.com/documents/clerk/official-proposal-list_november-3-2026.pdf",
        "../MF-376/MI-Wayne/official-proposal-list_november-3-2026-2_1.pdf",
        "Wayne County Clerk",
    ),
    "26139": (
        "https://www.miottawa.org/Elections/proposalFile-226.pdf",
        "../MF-376/MI-Ottawa/proposalFile-226.pdf",
        "Ottawa County Clerk",
    ),
}
PAGED = {"26099"}
TITLE = "Official candidates and proposals list, November 3, 2026 general election"
CENTERED = re.compile(r"^\s{40,}\S")
NOISE = re.compile(
    r"^\s*(Updated \d|.*November 3, 2026 General Election\s+Page|Official Candidates and Proposals List|November 3, 2026 General Election\s*$)"
)
SECTIONS = {
    "County Proposals",
    "City/Township Proposals",
    "Local School District",
    "Local School Districts",
    "Other Proposals",
    "Library Proposals",
}


LABELISH = re.compile(r"Millage|Amendment|Proposal|Proposed|Bond|Charter|Question|Renewal|Counties of")


def tidy(name: str) -> str:
    return re.sub(r"^(.+?) Charter Township$", r"Charter Township of \1", name)


def oakland_new(section: str, head: str) -> bool:
    if section in {"City", "Village"}:
        return LABELISH.search(head) is None
    if section == "Township":
        return re.fullmatch(r"[A-Z][a-z]+(?: [A-Z][a-z]+)? Township", head) is not None
    if section == "Local School District":
        return re.search(r"(Schools|School District)$", head) is not None and LABELISH.search(head) is None
    return True


def ottawa_new(section: str, head: str) -> bool:
    return (
        re.search(r"^(City|Village) of |Township$|Schools?$|School District$|County$|District$|Authority$", head)
        is not None
    )


def centered(
    county: str,
    url: str,
    text: str,
    start: str,
    prefixes: dict[str, str],
    noise: re.Pattern[str],
    threshold: int,
    is_new,
) -> list[dict[str, str]]:
    lines = text.replace("\f", "\n").split("\n")
    begin = next(index for index, line in enumerate(lines) if line.strip() == start)
    rows = []
    section = ""
    jurisdiction = ""
    heads: list[str] = []
    body: list[str] = []

    def flush() -> None:
        nonlocal jurisdiction, heads, body
        if heads and body:
            if is_new(section, heads[0]) or not jurisdiction:
                jurisdiction, labels = heads[0], heads[1:]
            else:
                labels = heads
            rows.append(
                {
                    "county": county,
                    "jurisdiction": tidy(prefixes.get(section, "") + jurisdiction),
                    "label": " – ".join(labels),
                    "wording": re.sub(r"\s+", " ", " ".join(body)).strip(),
                    "url": url,
                }
            )
        heads, body = [], []

    for line in lines[begin:]:
        if not line.strip() or noise.match(line):
            continue
        label = line.strip()
        if re.match(rf"^\s{{{threshold},}}\S", line):
            if label in prefixes or label.endswith(" Proposals"):
                flush()
                section = label
                continue
            if body:
                flush()
            heads.append(label)
        else:
            body.append(label)
    flush()
    return rows


def oakland(county: str, url: str, text: str) -> list[dict[str, str]]:
    noise = re.compile(
        r"^\s*(Updated \d|Oakland County\s*$|Official Proposal List|November 3, 2026 General Election\s*$|County of Oakland\s*$|State of Michigan\s*$)"
    )
    prefixes = {
        "Authority": "",
        "City": "City of ",
        "Township": "",
        "Village": "Village of ",
        "Community College": "",
        "Local School District": "",
    }
    rows = centered(county, url, text, "Authority", prefixes, noise, 40, oakland_new)
    for row in rows:
        if row["jurisdiction"] == "Millage Proposal":
            row["jurisdiction"] = "Southeast Michigan Public Historical Museum Authority"
            row["label"] = "Millage Proposal"
    for row in rows:
        row["label"] = row["label"] or row["jurisdiction"]
    return rows


def ottawa(county: str, url: str, text: str) -> list[dict[str, str]]:
    noise = re.compile(r"^\s*(\d+/\d+/\d{4}|Official List of Proposals)")
    rows = centered(county, url, text, "County Proposals", {}, noise, 60, ottawa_new)
    for row in rows:
        row["label"] = row["label"] or row["jurisdiction"]
    return rows


def wayne(county: str, url: str, text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.replace("\f", "\n").split("\n") if line.strip()]
    lines = [
        line for line in lines if not re.match(r"(\d+/\d+/\d{4} \d|11/03/2026 - STATE GENERAL|WAYNE \(82\))", line)
    ]
    blocks: list[tuple[str, list[str]]] = []
    for line in lines:
        if re.fullmatch(r"[A-Z0-9 /&,.'’()-]+ PROPOSALS?", line) and line != "PROPOSAL":
            blocks.append((line.rsplit(" PROPOSAL", 1)[0].split(" / ")[-1].title().replace(" Of ", " of "), []))
        elif blocks:
            blocks[-1][1].append(line)
    rows = []
    for jurisdiction, block in blocks:
        kept = [line for line in block if not re.fullmatch(r"(County of Wayne|State of Michigan)", line, re.IGNORECASE)]
        kept = [line for line in kept if line.lower() != jurisdiction.lower()]
        parts: list[dict[str, list[str] | str]] = []
        for index, line in enumerate(kept):
            before = kept[index - 1] if index else ""
            if not parts or (len(line) < 100 and not re.search(r"[.?:,;]$", line) and re.search(r"[.?]$", before)):
                parts.append({"label": line, "body": []})
            else:
                parts[-1]["body"].append(line)
        for part in parts:
            label = str(part["label"])
            body = list(part["body"])
            if label.endswith(" for the") and body:
                label = f"{label} {body[0].split(' which ')[0]}"
            rows.append(
                {
                    "county": county,
                    "jurisdiction": tidy(jurisdiction),
                    "label": label.title() if label.isupper() else label,
                    "wording": re.sub(r"\s+", " ", " ".join(body)).strip(),
                    "url": url,
                }
            )
    return rows


PARSERS = {"26125": oakland, "26163": wayne, "26139": ottawa}


def fetch() -> None:
    for url, file, _ in COUNTIES.values():
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


def paged_rows(county: str, url: str, text: str) -> list[dict[str, str]]:
    rows = []
    for page in text.split("\f"):
        lines = [line.strip() for line in page.split("\n") if line.strip()]
        if len(lines) < 3 or lines[0] == "STATE":
            continue
        if lines[0].startswith("("):
            rows[-1]["wording"] += " " + " ".join(lines)
            continue
        jurisdiction = lines[0]
        rest = lines[1:]
        if rest[0] == "COUNTY OF MACOMB":
            rest = rest[2:]
        rows.append(
            {
                "county": county,
                "jurisdiction": jurisdiction.title() if jurisdiction.isupper() else jurisdiction,
                "label": rest[0].title() if rest[0].isupper() else rest[0],
                "wording": re.sub(r"\s+", " ", " ".join(rest[1:])).strip(),
                "url": url,
            }
        )
    return rows


def parse() -> None:
    rows = []
    for county, (url, _, _) in COUNTIES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        if county in PARSERS:
            rows.extend(PARSERS[county](county, url, text))
            continue
        if county in PAGED:
            rows.extend(paged_rows(county, url, text))
            continue
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
    frame = pl.DataFrame(rows).unique(["jurisdiction", "label"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "questions.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "questions.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Michigan county, city, township, and school district proposals on the November 3, 2026 ballot, with official wording",
            polity="US-MI",
        )
        civil = civmint.Civil(conn, cs, "26", "MI")
        found = civil.found
        slugs = civil.slugs
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
            option = None
            county_key = f"us.county:{row['county']}"
            if county_key in found and jurisdiction.startswith(found[county_key][2]):
                option = county_key
            elif jurisdiction.lower().startswith(("city of", "village of")):
                option = civil.place(re.sub(r"(?i)^(city|village) of ", "", jurisdiction))
            elif re.search(r"(?i)township", jurisdiction):
                option = civil.town(re.sub(r"(?i)^(charter township|township) of (.+)$", r"\2 Township", jurisdiction), row["county"])
            elif re.search(r"(?i)schools?\b", jurisdiction) and not re.search(r"(?i)college|agency", jurisdiction):
                option = civil.school(jurisdiction)
            if option is None and county_key in found and re.search(r"(?i)authority|district|college|library|agency|parks", jurisdiction):
                county_polity, county_area, _, county_path, _ = found[county_key]
                minted = govmint.special(conn, cs, "MI", (county_polity, county_area, county_path, row["county"]), jurisdiction, slugs)
                if minted is not None:
                    option = f"us.special_district:{minted[4]}"
                    found[option] = minted
            options = [] if option is None else [option]
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
