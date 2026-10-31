import html
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

SOURCE = "us.il_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
MANUAL = DATA / "inbox" / "manual" / "MF-376"
TITLE = "Referenda questions, November 3, 2026 general election"
COUNTIES = {
    "17201": (
        "https://wincoil.gov/media/attachments/2026/08/27/questions-nov-3-2026-2.pdf",
        None,
        "Winnebago County Clerk",
        ".pdf",
    ),
    "17031": (
        "https://www.cookcountyclerkil.gov/elections/current-election-referenda",
        "IL-Cook/il-cook-2026-11-03-referenda.html",
        "Cook County Clerk",
        ".html",
    ),
    "17113": (
        "https://www.mcleancountyil.gov/1049/Referenda",
        "IL-McLean/il-mclean-2026-11-03-referenda.html",
        "McLean County Clerk",
        ".html",
    ),
    "17111": (
        "https://www.mchenrycountyil.gov/home/showpublisheddocument/20260824-public-notice-of-referendum",
        "IL-McHenry/20260824 PUBLIC NOTICE OF REFERENDUM.pdf",
        "McHenry County Clerk",
        ".pdf",
    ),
}
MCHENRY_LABELS = (
    ("Home Rule", "Advisory Question: Home Rule and County Executive"),
    ("Zoning", "Advisory Question: Local Zoning Authority"),
    ("Limitation Law", "Increase Limiting Rate"),
    ("Pension", "New Tax Rate for Pension Fund"),
    ("Bonds", "Issue Bonds"),
)
SKIPPED = {("17111", "Community Unit School District #300")}


def fetch() -> None:
    for url, file, _, suffix in COUNTIES.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            if file is None:
                content = subprocess.run(
                    ["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True
                ).stdout
                retrieved = datetime.now(UTC)
            else:
                path = MANUAL / file
                content = path.read_bytes()
                retrieved = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            raw.store(
                SOURCE,
                url,
                content,
                suffix=suffix,
                retrieved_at=retrieved,
                media_type="application/pdf" if suffix == ".pdf" else "text/html",
            )


SCHOOL = re.compile(
    r"((?:[A-Z][\w\-]+ )+?(?:Combined |Community Unit |Township High |Elementary |Unit |Community High )?(?:School|Community College) District (?:Number|No\.) ?\d+\w*)"
)


def full_name(entity: str, wording: str) -> str:
    if not re.search(r"School District|CUSD|College District", entity):
        return entity
    match = SCHOOL.search(wording)
    return re.sub(r"^(Shall|The) ", "", match[1]) if match is not None else entity


def pdf_text(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout


def page_text(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text()


def cook_rows() -> list[dict[str, str]]:
    cells = re.findall(
        r'<span translate="no">(.*?)</span></div><div[^>]*grow="3">(.*?)</div></div>',
        page_text(COUNTIES["17031"][0]),
        re.DOTALL,
    )
    rows = []
    for head, wording in cells:
        entity, label = html.unescape(head).split(", ", 1)
        rows.append({"entity": entity, "label": label, "wording": re.sub(r"\s+", " ", html.unescape(wording)).strip()})
    return rows


def mclean_rows() -> list[dict[str, str]]:
    page = page_text(COUNTIES["17113"][0])
    heads = re.findall(r'data-tabname="([^"]+)"', page)
    wordings = re.findall(r"<p>(.+?)</p><ul[^>]*><li><strong>YES", page, re.DOTALL)
    rows = []
    for head, wording in zip(heads, wordings, strict=True):
        entity, label = html.unescape(head).split(" - ", 1)
        rows.append(
            {
                "entity": entity.strip(),
                "label": label.strip(),
                "wording": re.sub(r"\s+", " ", html.unescape(wording)).strip().strip('"“”'),
            }
        )
    return rows


def mchenry_rows() -> list[dict[str, str]]:
    rows = []
    entity = ""
    text = pdf_text(COUNTIES["17111"][0]).split("SUBDIVISIONS:", 1)[1]
    for block in re.split(r"\n\s*\n", text):
        block = re.sub(r"\s+", " ", block).strip()
        if not block or block.startswith(("I hereby", "Dated")):
            continue
        if block.isupper():
            entity = block.title().replace("Mchenry", "McHenry").replace("Of ", "of ")
        elif not rows or rows[-1]["entity"] != entity or rows[-1]["wording"]:
            if re.match(r"^\d\.|^\(\d\)", block):
                rows[-1]["wording"] += " " + block
            else:
                rows.append({"entity": entity, "label": block, "wording": ""})
        else:
            rows[-1]["wording"] = block
    for row in rows:
        row["label"] = next(label for key, label in MCHENRY_LABELS if key in row["label"])
        row["wording"] = row["wording"].strip('"')
    return rows


def parse() -> None:
    text = pdf_text(COUNTIES["17201"][0])
    text = re.sub(r"(?m)^(\s*)(YES|NO)\s*$", "", text)
    text = re.sub(r"(?m)\s{20,}(YES|NO)\s*$", "", text)
    parts = re.split(
        r"(?m)^\s*(NON-BINDING ADVISORY PUBLIC QUESTION CONCERNING[^\n]*(?:\n\s+[A-Z ]+(?=\n))?|PROPOSITION TO INCREASE THE PROPERTY TAX LIMITING RATE)\s*$",
        text,
    )
    rows = []
    for head, body in zip(parts[1::2], parts[2::2], strict=True):
        quote = re.search(r"[“\"](.+?\?)[”\"]", body, re.DOTALL)
        wording = re.sub(r"\s+", " ", quote[1]).replace("NON- ", "NON-").strip()
        if head.startswith("PROPOSITION"):
            wording = re.sub(r"\s+", " ", body.split("I hereby certify")[0]).strip()
            entity = "Pecatonica Fire Protection District"
            label = "Proposition to Increase the Property Tax Limiting Rate"
        else:
            entity = "Winnebago County"
            label = (
                re.sub(r"\s+", " ", head.replace("NON-BINDING ADVISORY PUBLIC QUESTION CONCERNING", "")).strip().title()
            )
            label = f"Advisory Question: {label}"
        wording = re.sub(r"\s+", " ", re.sub(r"(?<=\s)(YES|NO)(?=\s)", " ", wording)).strip()
        rows.append({"entity": entity, "label": label, "wording": wording})
    tagged = [{**row, "county": "17201"} for row in rows]
    for county, extra in (("17031", cook_rows()), ("17113", mclean_rows()), ("17111", mchenry_rows())):
        tagged.extend({**row, "county": county} for row in extra if (county, row["entity"]) not in SKIPPED)
    for row in tagged:
        row["entity"] = full_name(row["entity"], row["wording"])
        row["url"] = COUNTIES[row["county"]][0]
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(tagged).unique(["county", "entity", "label"], keep="first", maintain_order=True)
    frame.write_parquet(PARSED / "measures.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois county, municipal, school, and special district referenda (Winnebago, Cook, McLean, McHenry) on the November 3, 2026 ballot, with official wording",
            polity="US-IL",
        )
        civil = civmint.Civil(conn, cs, "17", "IL")
        found = civil.found
        system = cs.key("us.electoral_system", "referendum")
        sources: dict[str, UUID] = {}
        for county, (url, _, publisher, _) in COUNTIES.items():
            sources[county] = cs.source(publisher, TITLE)
            cs.add("source", id=sources[county], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[county])
        parent = cs.key("us.election", f"{DAY}:IL")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            entity = row["entity"]
            county_key = f"us.county:{row['county']}"
            source = sources[row["county"]]
            option = None
            if re.match(r"(?i)(city|town|village) of ", entity):
                option = civil.place(re.sub(r"(?i)^(city|town|village) of ", "", entity))
            elif county_key in found and entity == found[county_key][2]:
                option = county_key
            elif entity.endswith(" Township"):
                option = civil.town(entity, row["county"][:5])
            elif re.search(r"School District|CUSD", entity):
                option = civil.school(entity)
            elif f"special:{row['county']}:{entity}" in found:
                option = f"special:{row['county']}:{entity}"
            elif county_key in found:
                county, county_area, _, county_path, county_code = found[county_key]
                minted = govmint.special(
                    conn, cs, "IL", (county, county_area, county_path, county_code), entity, civil.slugs
                )
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
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
            contest_key = f"{DAY}:IL:{code}:measure:{slug(label)}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{slug(label)}"
            if civil.slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            civil.slugs[contest_path] = contest
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
                url=row["url"],
                source_id=source,
            )
        print(f"{kept} of {frame.height} referenda")
        return cs.write()
