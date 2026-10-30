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

SOURCE = "us.mo_measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
MANUAL = DATA / "inbox" / "manual" / "MF-376"
TITLE = "Official ballot issues, November 3, 2026 general election"
COUNTIES = {
    "29077": (
        "https://vote.greenecountymo.gov/wp-content/uploads/2026/09/Candidates_and_Ballot_Issues_Composite_November_2026.pdf",
        "MO-Greene/Candidates_and_Ballot_Issues_Composite_November_2026.pdf",
        "Greene County Clerk",
    ),
    "29209": (
        "https://stonecountyclerk.com/media/uploads/downloads/Stone_Publication_110326.pdf",
        "MO-Stone/Stone_Publication_110326.pdf",
        "Stone County Clerk",
    ),
    "29213": (
        "https://media.taneycounty.org/pdf.php?id=5758",
        "MO-Taney/2026.09.16.-.2026.11.03.-.Clerk.-GE.Comp.Ballot.pdf",
        "Taney County Clerk",
    ),
    "29225": (
        "https://webstercountymo.gov/wp-content/uploads/2026/09/Webster-Publication-110326.pdf",
        "MO-Webster/Webster-Publication-110326.pdf",
        "Webster County Clerk",
    ),
}
LOCAL = [
    ("29209", "Stone County Public Water Supply District No. 1", "Question", "Shall Stone County Public Water Supply District No. 1 be dissolved, its water system and related assets sold and transferred, and Missouri-American Water Company become the provider of water service to the customers formerly served by the District?"),
    ("29209", "Village of Indian Point", "Question", "Shall the Village of Indian Point impose a city sales tax of one half of one percent (0.5%) for public safety purposes and public improvements?"),
    ("29213", "City of Rockaway Beach", "Question", "Shall the City of Rockaway Beach, Missouri impose a local use tax at the same rate as the total local sales tax rate, currently 2 percent (2%), provided that if the local sales tax rate is reduced or raised by voter approval, the local use tax rate shall also be reduced or raised by the same action? A use tax return shall not be required to be filed by persons whose purchases from out-of-state vendors do not in total exceed two thousand dollars in any calendar year."),
    ("29213", "Village of Taneyville", "Question", "Shall the Village of Taneyville, Missouri, impose a property tax levy of Fifty Cents ($0.50) per One Hundred Dollars ($100.00) of assessed valuation upon all taxable real property within the Village for the sole purpose of funding road maintenance and repair, and related transportation infrastructure improvements? The Tax shall remain in effect for a period of five (5) years and shall expire November 7, 2031 unless renewed or extended by approval of the voters. The Village shall be authorized to submit the tax for renewal or extension prior to its expiration."),
    ("29213", "Western Taney County Fire Protection District", "Question 1", "Shall the number of members of the board of directors of the Western Taney County Fire Protection District be increased to five members?"),
    ("29225", "Webster County", "Question One", "Shall the County of Webster continue to impose a countywide sales tax at the rate of one-half of one percent for a period of ten years from the date on which such renewed tax is first imposed for the purpose of continuing the fund established for the improvement and construction of roads and bridges, including the purchase of machinery and materials?"),
]


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


def parse() -> None:
    url = COUNTIES["29077"][0]
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    rows = []
    for match in re.finditer(r"\n\s+Question (\d)\n\s*\n(Shall .*?\?)\n", text, re.S):
        rows.append(("29077", "City of Springfield", f"Question {match[1]}", re.sub(r"\s+", " ", match[2])))
    rows.extend(LOCAL)
    frame = pl.DataFrame(
        [
            {"county": county, "entity": entity, "label": label, "wording": wording, "url": COUNTIES[county][0]}
            for county, entity, label, wording in rows
        ]
    )
    PARSED.mkdir(parents=True, exist_ok=True)
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
               where a.scheme_code in ('us.county', 'us.place', 'us.school_district') and left(c.code, 2) = '29'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            names.setdefault(ceda._city(text) if scheme == "us.place" else ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/mo/%%'").fetchall())
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri county and local questions on the November 3, 2026 ballot, with official wording",
            polity="US-MO",
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
            if re.match(r"(?i)(city|town|village) of ", entity):
                options = names.get(ceda._city(re.sub(r"(?i)^(city|town|village) of ", "", entity)), [])
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
                minted = govmint.special(conn, cs, "MO", (county, county_area, county_path, county_code), entity, slugs)
                if minted is not None:
                    option = f"special:{row['county']}:{entity}"
                    found[option] = minted
            if option is None:
                print("no polity:", entity, row["label"])
                continue
            kept += 1
            polity, area, place, path, code = found[option]
            election = cs.key("us.election", f"{DAY}:MO:{code}")
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
            contest_key = f"{DAY}:MO:{code}:measure:{slug(label)}"
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
