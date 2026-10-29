import re
import subprocess
from pathlib import Path
from uuid import uuid5

from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.nyc_ballot_text"
VERSION = "1"
INBOX = DATA / "inbox" / "nyc_ballot"
CRC = "New York City Charter Revision Commission"
RJC = "New York City Racial Justice Commission"
CHARTER = "https://www.nyc.gov/assets/charter/downloads/pdf"
PROPOSAL = "proposal-{}"
ADOPTED = r"Shall this proposal be adopted\?"
EDITIONS = (
    (
        "2018-11-06",
        "2018.pdf",
        f"{CHARTER}/final-report-20180904.pdf",
        CRC,
        "Final Report of the 2018 New York City Charter Revision Commission, September 4, 2018 (ballot proposals)",
        ADOPTED,
        (
            ("Question # 1: Campaign Finance", "campaign-finance"),
            ("Question # 2: Civic Engagement Commission", "civic-engagement-commission"),
            ("Question #3: Community Boards", "community-boards"),
        ),
    ),
    (
        "2019-11-05",
        "2019.pdf",
        f"{CHARTER}/reports-ballot-issues/ballot-questions.pdf",
        CRC,
        "2019 Ballot Questions, New York City Charter Revision Commission",
        ADOPTED,
        (
            ("QUESTION #1: ELECTIONS", "elections"),
            ("QUESTION #2: CIVILIAN COMPLAINT REVIEW BOARD", "civilian-complaint-review-board"),
            ("QUESTION #3: ETHICS AND GOVERNANCE", "ethics-and-governance"),
            ("QUESTION #4: CITY BUDGET", "city-budget"),
            ("QUESTION #5: LAND USE", "land-use"),
        ),
    ),
    (
        "2022-11-08",
        "2022.pdf",
        "https://racialjustice.cityofnewyork.us/wp-content/uploads/2022/10/Ballot-Questions-Final_Ballot-Questions-English_FINAL.pdf",
        RJC,
        "2022 Ballot Questions, New York City Racial Justice Commission",
        ADOPTED,
        (
            ("Ballot Question #1: Add a Statement of Values to Guide Government", "add-a-statement-of-values-to-guide-government"),
            ("Ballot Question #2: Establish a Racial Equity Office, Plan, and Commission", "establish-a-racial-equity-office-plan-and-commis"),
            ("Ballot Question #3: Measure the True Cost of Living", "measure-the-true-cost-of-living"),
        ),
    ),
    (
        "2024-11-05",
        "2024.pdf",
        f"{CHARTER}/reports-ballot-issues/Ballot-Questions-FINAL.pdf",
        CRC,
        "2024 Ballot Questions, New York City Charter Revision Commission",
        r"leaves laws unchanged\.",
        (
            ("Ballot Question 2 Cleaning Public Property", "cleaning-public-property"),
            (
                "Ballot Question 3 Additional Estimates of the Cost of Proposed Laws and Updates to Budget Deadlines",
                "additional-estimates-of-the-cost-of-proposed-law",
            ),
            ("Ballot Question 4 More Notice and Time Before Votes on Public Safety Legislation", "more-notice-and-time-before-votes-on-public-safe"),
            ("Ballot Question 5 Capital Planning", "capital-planning"),
            (
                "Ballot Question 6 Minority and Women-Owned Business Enterprises (MWBEs), Film Permits, and Archive Review Boards",
                "minority-and-women-owned-business-enterprises-mw",
            ),
        ),
    ),
    (
        "2025-11-04",
        "2025.pdf",
        f"{CHARTER}/2025/appendix-a-adopted-questions.pdf",
        CRC,
        "Appendix A, Adopted Questions, 2025 New York City Charter Revision Commission",
        r"(?:“No” leaves [^.]*\.)",
        (
            ("Question 2 Proposed Charter Amendment: Fast Track Affordable Housing to Build More Affordable Housing Across the City", "proposed-charter-amendment-fast-track-affordable"),
            ("Question 3 Proposed Charter Amendment: Simplify Review of Modest Housing and Infrastructure Projects", "proposed-charter-amendment-simplify-review-of-mo"),
            (
                "Question 4 Proposed Charter Amendment: Establish an Affordable Housing Appeals Board with Council, Borough, and Citywide Representation",
                "proposed-charter-amendment-establish-an-affordab",
            ),
            ("Question 5 Proposed Charter Amendment: Create a Digital City Map to Modernize City Operations", "proposed-charter-amendment-create-a-digital-city"),
            (
                "Question 6 Proposed Charter Amendment: Move Local Elections to Presidential Election Years to Increase Voter Participation",
                "proposed-charter-amendment-move-local-elections",
            ),
        ),
    ),
)


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Official wording of New York City charter ballot questions 2018 to 2025, from the commissions' published ballot questions",
            polity="US",
        )
        contests = {
            value: entity
            for value, entity in conn.execute(
                "select x.value, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id where x.scheme = 'us.contest'"
            )
        }
        placed = 0
        for day, pdf, url, publisher, title, end, questions in EDITIONS:
            text = " ".join(
                subprocess.run(["pdftotext", str(INBOX / pdf), "-"], capture_output=True, text=True, check=True).stdout.split()
            )
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add("source", id=source, kind="official_body", publisher=publisher, title=title, url=url, license="Public record")
            for heading, name in questions:
                head = re.search(r"\s+".join(re.escape(word) for word in heading.split()), text)
                if head is None:
                    raise ValueError(f"{day}: no heading {heading!r}")
                tail = re.search(end, text[head.end() :])
                if tail is None:
                    raise ValueError(f"{day}: no end after {heading!r}")
                body = text[head.end() : head.end() + tail.end()].strip()
                heading_title = re.sub(r"^(?:Ballot )?Question\s*#?\s*\d+:?\s*", "", heading, flags=re.IGNORECASE)
                wording = f"{heading_title.title() if heading_title.isupper() else heading_title}: {body}"
                contest = contests.get(f"{day}:new-york-city:{PROPOSAL.format(name)}")
                if contest is None:
                    print(f"{day} {name}: no contest, wording not placed")
                    continue
                contest = cs.survivors.get(contest, contest)
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1ftext\x1fen\x1f{source}\x1f{url}"),
                    contest_id=contest,
                    kind="text",
                    lang="en",
                    body=wording,
                    url=url,
                    source_id=source,
                    locator={"heading": heading},
                )
                placed += 1
        print(f"{placed} wordings placed")
        return cs.write()
