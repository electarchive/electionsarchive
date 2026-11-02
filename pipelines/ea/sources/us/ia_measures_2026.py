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

SOURCE = "us.ia_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
TITLE = "Public measures, November 3, 2026 general election"
HENRY = DATA / "inbox" / "y26" / "ia" / "counties" / "henry.pdf"
LINN = "19113"
SOURCES = {
    "linn": (
        "https://gis.linncountyiowa.gov/web-data/elections/year/2026/20261103GE/all.pdf",
        "Linn County Auditor",
        ".pdf",
    ),
    "johnson": (
        "https://www.johnsoncountyiowa.gov/november-3-2026-general-election",
        "Johnson County Auditor",
        ".html",
    ),
    "desmoines": (
        "https://dmcountyelections.iowa.gov/elections/info/2026_general_election_2026_11_03/",
        "Des Moines County Auditor",
        ".html",
    ),
    "jefferson": (
        "https://jeffersoncounty.iowa.gov/wp-content/uploads/notice-general-2026.pdf",
        "Jefferson County Auditor",
        ".pdf",
    ),
    "keokuk": (
        "https://keokukcounty.iowa.gov/files/public_notice/2026_general_election_56361.pdf",
        "Keokuk County Auditor",
        ".pdf",
    ),
    "butler": (
        "https://butlercounty.iowa.gov/images/docs/departments/elections/CANDIDATE%20FILINGS-to%20ADKINS-2026.pdf",
        "Butler County Auditor",
        ".pdf",
    ),
    "bremer": (
        "https://bremercountyelections.iowa.gov/files/candidate_list/2026_general_election_81883.pdf",
        "Bremer County Auditor",
        ".pdf",
    ),
    "paloalto": (
        "https://paloaltocounty.iowa.gov/wp-content/uploads/2026/09/2026-GENERAL-ELECTION-SAMPLE-BALLOTS.pdf",
        "Palo Alto County Auditor",
        ".pdf",
    ),
    "henry": (
        "https://cap.gmdsolutions.com/viewfile.aspx?id=3669F870D52E468B857E7B006B2DE581&dir=Objects",
        "Henry County Auditor",
        ".pdf",
    ),
}
LAYOUT = ("linn", "jefferson", "keokuk", "butler")
PAGES = ("johnson", "desmoines")
TOWNSHIP = (
    "Should the Palo Alto County Board of Supervisors fill the offices of Trustee and Clerk of {} Township by "
    "appointment as the terms of the office of the incumbent township officers expires?"
)
SCANNED = (
    (
        "paloalto",
        "A",
        "Emmetsburg Community School District",
        (
            "Shall the Board of Directors of the Emmetsburg Community School District in the County of Palo Alto, State "
            "of Iowa, be authorized to contract indebtedness and issue General Obligation Bonds in an amount not to "
            "exceed $25,000,000, to provide funds to build, furnish, and equip a new elementary school, including site "
            "acquisition and site improvements, and for partial demolition of the existing elementary school, and to "
            "repair and improve the remaining portion of the existing elementary building, including site improvements?"
        ),
    ),
    ("paloalto", "B", "Fairfield Township", TOWNSHIP.format("Fairfield")),
    ("paloalto", "C", "Silver Lake Township", TOWNSHIP.format("Silver Lake")),
    ("paloalto", "D", "West Bend Township", TOWNSHIP.format("West Bend")),
    (
        "paloalto",
        "HF",
        "North Union Community School District",
        (
            "Shall the Board of Directors of the North Union Community School District in the Counties of Emmet, "
            "Kossuth and Palo Alto, State of Iowa, be authorized to contract indebtedness and issue General Obligation "
            "Bonds in an amount not to exceed $12,000,000 to provide funds to build, furnish, and equip additions to "
            "the school building, including a gymnasium, multipurpose room, and related spaces, and to improve the site?"
        ),
    ),
    (
        "henry",
        "US",
        "Van Buren County Community School District",
        (
            "Shall the Board of Directors of the Van Buren County Community School District in the Counties of Van "
            "Buren, Davis, Henry and Lee, State of Iowa, be authorized to issue approximately $13,800,000 School "
            "Infrastructure Sales, Services and Use Tax Revenue and Refunding Bonds, which may be issued in one or more "
            "series over multiple fiscal years pursuant to Iowa Code Section 423F.2 and 423E.5, in the approximate "
            "amount of $13,800,000 for the purpose of providing funds to build, furnish, and equip an addition to the "
            "Keosauqua campus, including a secure entrance, classrooms, commons, and office space, and to improve the "
            "site; and to currently refund the outstanding School Infrastructure Sales, Services and Use Tax Revenue "
            "Bonds, Series 2022A, dated July 15, 2022, including costs of issuance and a debt service reserve fund if "
            "required by the purchaser; with any bond proceeds remaining after completion of this project used for other "
            "school infrastructure projects as authorized by the School District's Revenue Purpose Statement?"
        ),
    ),
)
SHALL = re.compile(r"Public Measure ([A-Z]{1,3})\b[^?]{0,140}?(Shall\b[^?]+\?)")
BREMER = re.compile(r"District - ([A-Z]{1,3})\s+(Shall\b[^?]+\?)")


def fetch() -> None:
    for key, (url, _, suffix) in SOURCES.items():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            if key == "henry":
                content = HENRY.read_bytes()
                retrieved = datetime.fromtimestamp(HENRY.stat().st_mtime, UTC)
            else:
                content = subprocess.run(
                    ["curl", "-sSL", "-m", "120", "-A", "Mozilla/5.0", url], capture_output=True, check=True
                ).stdout
                retrieved = datetime.now(UTC)
            raw.store(
                SOURCE,
                url,
                content,
                suffix=suffix,
                retrieved_at=retrieved,
                media_type="application/pdf" if suffix == ".pdf" else "text/html",
            )


def source_text(key: str) -> str:
    path = raw.cached(SOURCE, SOURCES[key][0]).path
    if path.suffix == ".pdf":
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
    else:
        text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", path.read_text())
        text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    return re.sub(r"\s+", " ", text)


def entity_of(wording: str) -> str:
    patterns = (
        (r"^Shall the City of ([^,]+),", "City of {}"),
        (r"^Shall the County of ([^,]+),", "{} County"),
        (r"^Shall the Board of Directors of ?the ?(.+? School District) in", "{}"),
        (r"^Shall (.+? Community College) \(", "{}"),
    )
    for pattern, template in patterns:
        match = re.search(pattern, wording)
        if match is not None:
            return template.format(match[1])
    raise ValueError(wording)


def topic(entity: str, wording: str) -> str:
    if "Township" in entity:
        return "Appoint Township Trustee and Clerk"
    if "revenue" in wording.lower() and "bonds" in wording.lower() and "School Infrastructure" in wording:
        return "School Infrastructure Revenue Bonds"
    return "General Obligation Bonds" if "bonds" in wording.lower() else "Question"


def parse() -> None:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    cited: dict[tuple[str, str], list[str]] = {}

    def add(key: str, code: str, wording: str, entity: str | None = None) -> None:
        wording = re.sub(r"\s+", " ", wording).strip()
        entity = entity or entity_of(wording)
        rows.setdefault(
            (code, entity),
            {
                "code": code,
                "entity": entity,
                "label": f"Public Measure {code}",
                "topic": topic(entity, wording),
                "wording": wording,
                "source": key,
                "url": SOURCES[key][0],
            },
        )
        cited.setdefault((code, entity), []).append(key)

    for key in (*LAYOUT, *PAGES):
        for match in SHALL.finditer(source_text(key)):
            add(key, match[1], match[2])
    for match in BREMER.finditer(source_text("bremer")):
        add("bremer", match[1], match[2])
    for key, code, entity, wording in SCANNED:
        add(key, code, wording, entity)
    frame = [{**row, "sources": ",".join(dict.fromkeys(cited[key]))} for key, row in rows.items()]
    PARSED.mkdir(parents=True, exist_ok=True)
    result = pl.DataFrame(frame)
    result.write_parquet(PARSED / "measures.parquet")
    print(result.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa city, county, school, community college, and township public measures on the November 3, 2026 ballot, with official wording from county auditors' notices and sample ballots",
            polity="US-IA",
        )
        civil = civmint.Civil(conn, cs, "19", "IA")
        found = civil.found
        system = cs.key("us.electoral_system", "referendum")
        source_ids: dict[str, UUID] = {}
        for key, (url, publisher, _) in SOURCES.items():
            source_ids[key] = cs.source(publisher, TITLE)
            cs.add("source", id=source_ids[key], kind="official_body", publisher=publisher, title=TITLE, url=url)
            cs.artifact(raw.cached(SOURCE, url), source_ids[key])
        parent = cs.key("us.election", f"{DAY}:IA")
        elections: set[UUID] = set()
        kept = 0
        for row in frame.iter_rows(named=True):
            entity = row["entity"]
            option = None
            if entity.startswith("City of "):
                option = civil.place(entity.removeprefix("City of "))
            elif entity.endswith(" County"):
                option = next(
                    (key for key, value in found.items() if key.startswith("us.county:") and value[2] == entity), None
                )
            elif entity.endswith(" Township"):
                option = civil.town(entity, "19147")
            elif "School District" in entity:
                option = civil.school(entity)
            elif entity == "Kirkwood Community College":
                county_key = f"us.county:{LINN}"
                if county_key in found:
                    county, county_area, _, county_path, county_code = found[county_key]
                    minted = govmint.special(
                        conn, cs, "IA", (county, county_area, county_path, county_code), entity, civil.slugs, "college"
                    )
                    if minted is not None:
                        option = f"special:{LINN}:{entity}"
                        found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:IA:{code}")
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
            label = f"{row['label']}: {row['topic']}"
            contest_key = f"{DAY}:IA:{code}:measure:{slug(row['label'])}"
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
            for key in row["sources"].split(","):
                cs.add("citation", entity_id=contest, source_id=source_ids[key])
            cs.add(
                "ballot_question_text",
                contest_id=contest,
                kind="text",
                lang="en",
                body=row["wording"],
                url=row["url"],
                source_id=source_ids[row["source"]],
            )
        print(f"{kept} of {frame.height} measures")
        return cs.write()
