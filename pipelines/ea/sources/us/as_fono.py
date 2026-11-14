import csv
import re
import subprocess
import unicodedata
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import LiteralString
from uuid import UUID

from psycopg import Connection

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.as_precincts import CLOUD, PUBLISHER
from ea.store import connect

SOURCE = "us.as_fono"
VERSION = "1"
DATA = Path(__file__).with_name("as_fono.tsv")
WAYBACK_2014 = "https://web.archive.org/web/20141223173827id_/http://www.americansamoaelectionoffice.org/results/2014%20General%20Electionfficial%20Results.pdf"
SHEETS = {
    "2014": (
        WAYBACK_2014,
        "Official results of the General Election 2014 (House of Representatives, Delegate, and referendum)",
    ),
    "2016": (
        "https://web.archive.org/web/20230320130102id_/https://www.aselectionoffice.gov/results/General%20Election%202016%20RESULTS_REPRESENTATIVES.pdf",
        "Unofficial results, 2016 general election, House of Representatives",
    ),
    "2020": (
        CLOUD + "2020/11/12144434/2020-GENERAL-ELECTIONOFFICIAL-RESULTS-HOUSE-OF-REPRESENTATIVE.pdf",
        "2020 general election official results, House of Representatives",
    ),
    "2022a": (
        CLOUD + "2022/11/16202220/Midterm-Election-2022-Official-Results-1.pdf",
        "Official results of the 2022 Midterm Election, part 1 (certification, Delegate, and House districts 13 to 17)",
    ),
    "2022b": (
        CLOUD + "2022/11/16222206/Midterm-Election-2022-Official-Results-2.pdf",
        "Official results of the 2022 Midterm Election, part 2 (House districts 1 to 12 and referendum)",
    ),
    "2022ballot": (
        CLOUD + "2022/09/09233026/Official-2022-Referendum-Ballot-Legal-Size-Sample-.docx.pdf",
        "Official referendum ballot, November 8, 2022 midterm election (sample)",
    ),
    "2024p1": (
        CLOUD + "2024/11/06063750/2024-house-of-representatives-tally_page_1.webp",
        "2024 general election unofficial results, House of Representatives, page 1",
    ),
    "2024p2": (
        CLOUD + "2024/11/06063804/2024-house-of-representatives-tally_page_2.webp",
        "2024 general election unofficial results, House of Representatives, page 2",
    ),
    "2024p3": (
        CLOUD + "2024/11/06063816/2024-house-of-representatives-tally_page_3.webp",
        "2024 general election unofficial results, House of Representatives, page 3",
    ),
    "2024ref": (
        CLOUD + "2024/11/06063828/2024-referendum-tally.webp",
        "2024 general election unofficial results, referendum",
    ),
}
DAYS = {
    "2014-11-04": date(2014, 11, 4),
    "2016-11-08": date(2016, 11, 8),
    "2020-11-03": date(2020, 11, 3),
    "2022-11-08": date(2022, 11, 8),
    "2024-11-05": date(2024, 11, 5),
}
SEATS = {1: 2, 12: 2, 15: 2}
PAGES = {
    "2014-11-04": lambda d: ("2014", 2 if d <= 8 else 3 if d <= 16 else 4),
    "2016-11-08": lambda d: ("2016", 1 if d <= 6 else 2 if d <= 13 else 3),
    "2020-11-03": lambda d: ("2020", 1 if d <= 7 else 2 if d <= 14 else 3),
    "2022-11-08": lambda d: ("2022b", 1) if d <= 12 else ("2022a", 3),
    "2024-11-05": lambda d: (f"2024p{1 if d <= 6 else 2 if d <= 14 else 3}", None),
}
QUESTIONS = {
    ("2014-11-04", 1): (
        "2014",
        6,
        "Veto override amendment",
        "Amend the veto override process?",
    ),
    ("2024-11-05", 1): (
        "2024ref",
        None,
        "Veto override amendment",
        None,
    ),
}
AMENDMENTS_2022 = {
    1: "The Governor shall appoint the Chief Justice and Associate Justices subject to confirmation by the Legislature.",
    2: "The Secretary of Interior shall not have the power to change the decisions of the High Court of American Samoa.",
    3: "The Secretary of Interior shall not have the power to change the Legislature’s override of the Governor’s Veto.",
    4: "This amendment shall add one (1) seat to Representative District–Ituau and, add one (1) seat to Representative District–Tualauta. The total number of Representatives would increase from 20 to 22.",
    5: "This amendment shall include “Malaeimi” in Representative District No. 12 – Ituau.",
    6: "This amendment will give the delegate from Swains Island the right to vote in the House of Representatives.",
    7: "This amendment will restore five (5) Senators to Manu’a. The total number of Senators would increase from 18 to 20.",
    8: "This amendment shall include an impeachment process for the Governor and Lt. Governor.",
    9: "This amendment shall change the name Government of American Samoa to American Samoa Government.",
    10: "This amendment shall change “Ma’uputasi” to “Ma’oputasi.”",
    11: "This amendment shall change “Leasina” to “Leasina ma Aitulagi.”",
}
TITLES_2022 = {
    1: "Appointment of justices",
    2: "High Court decisions",
    3: "Veto override",
    4: "House seats for Ituau and Tualauta",
    5: "Malaeimi in District 12",
    6: "Swains Island delegate's vote",
    7: "Senate seats for Manu’a",
    8: "Impeachment of the Governor and Lieutenant Governor",
    9: "Name of the American Samoa Government",
    10: "Ma’oputasi spelling",
    11: "Leasina ma Aitulagi",
}
for number, title in TITLES_2022.items():
    QUESTIONS["2022-11-08", number] = ("2022b", 2, f"Amendment {number}: {title}", AMENDMENTS_2022[number])

HOUSE_NOTE = {
    "2014-11-04": "Votes for each candidate by representative district from the Election Office's official results of November 11, 2014, with its OFFICIAL WINNER marks stored as the outcomes. The district totals match the polling booth sheets in the same file. Districts 9 and 14 were under challenge when the results were certified and the sheet prints no count for them.",
    "2016-11-08": "Votes for each candidate by representative district from the Election Office's unofficial results sheet, verified by the Chief Election Officer. The sheet marks no winners, so no outcomes are stored. The copy comes from the Office's site as archived by the Wayback Machine.",
    "2020-11-03": "Votes for each candidate by representative district from the Election Office's official results, with its Winner marks stored as the outcomes. The Office's own site doesn't post results; this copy comes from KHJ News's Talanei site.",
    "2022-11-08": "Votes for each candidate by representative district from the Election Office's official results certified on November 15, 2022, with its Elected marks stored as the outcomes. The Office's own site doesn't post results; this copy comes from KHJ News's Talanei site.",
    "2024-11-05": "Votes for each candidate by representative district from the Election Office's unofficial election-night sheets, with the highlighted names stored as the outcomes (District 10's winner is highlighted without the ELECTED mark). The Office certified the results on November 12, 2024 after no challenges were filed, but the certified House sheet is not online; this copy comes from KHJ News's Talanei site.",
}
QUESTION_NOTE = {
    "2014-11-04": "Yes and No votes by polling district from the Election Office's official results; the central office row holds absentee ballots and Swains Island. The sheet marks No as the official winner.",
    "2022-11-08": "Yes and No votes by representative district and Swains Island from the Election Office's official referendum results certified on November 15, 2022. The printed territory totals are 17 ballots larger than the sum of the rows for every amendment, so the sheet leaves 17 ballots, probably absentee, out of its rows; the outcome follows the printed totals. Approved amendments also need the approval of the U.S. Congress before they take effect.",
    "2024-11-05": "Yes (amend) and No (do not amend) votes by representative district and Swains Island from the Election Office's unofficial election-night sheet; the rows sum to the printed totals. The Office certified the results on November 12, 2024 after no challenges were filed.",
}


def fetch() -> None:
    for url, _ in SHEETS.values():
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(["curl", "-skL", "-m", "120", url], capture_output=True, check=True).stdout
            suffix = Path(url).suffix
            media = {".pdf": "application/pdf", ".webp": "image/webp"}[suffix]
            raw.store(SOURCE, url, body, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media)


def norm(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[’'.,\"]", "", text).replace("-", " ").split())


def display(name: str) -> str:
    if re.search(r"[a-z]", name.replace("EdD", "").replace("Dr.", "").replace("Jr.", "")):
        return name

    def word(token: str) -> str:
        if token in ("EdD", "JR.", "JR", "DR.", "SR."):
            return {"JR.": "Jr.", "JR": "Jr.", "DR.": "Dr.", "SR.": "Sr."}.get(token, token)
        out = "-".join(re.sub(r"^(\W*\w)(.*)$", lambda m: m[1] + m[2].lower(), part) for part in token.split("-"))
        return re.sub(r"^Mc(\w)", lambda m: "Mc" + m[1].upper(), out)

    return " ".join(word(token) for token in name.replace(",", "").split())


def one(conn: Connection, sql: LiteralString, params: list[object] | None = None) -> tuple:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise LookupError(sql)
    return row


def emit() -> Path:
    rows = list(csv.DictReader(DATA.open(encoding="utf-8"), delimiter="\t"))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="American Samoa House of Representatives elections 2014, 2016, 2020, 2022, and 2024 by representative district, and the 2014, 2022, and 2024 constitutional amendment referendums by district, from the Election Office's results sheets",
            polity="US-AS",
        )
        (state,) = one(conn, "select id from polity where iso_code = 'US-AS'")
        parent, territory = one(
            conn, "select id, area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state]
        )
        held = {
            int(m[1]): (value, body, area)
            for value, body, area in conn.execute(
                """select x.value, c.body_id, c.district_area_id from external_id x join contest c on c.id = x.entity_id
                   where x.scheme = 'us.contest' and x.value like '2026-11-03:AS:60:house:district-%'"""
            ).fetchall()
            if (m := re.search(r"district-(\d+)-", value))
        }
        if sorted(held) != list(range(1, 18)):
            raise ValueError(f"2026 House districts held: {sorted(held)}")
        known: dict[str, UUID] = {}
        for person, text in conn.execute(
            """select distinct cy.person_id, n.text from external_id x join contest_option co on co.contest_id = x.entity_id
               join candidacy cy on cy.option_id = co.id join name n on n.entity_id = cy.person_id
               where x.scheme = 'us.contest' and x.value like '2026-11-03:AS:60:house:%'"""
        ).fetchall():
            known.setdefault(norm(text), person)
        elections = {
            day: one(
                conn,
                """select c.election_id from contest c join external_id x on x.entity_id = c.id
                   and x.scheme = 'us.contest' and x.value = %s""",
                [f"{day}:AS:house:0"],
            )[0]
            for day in DAYS
        }
        sources: dict[str, UUID] = {}
        for key, (url, title) in SHEETS.items():
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, url), source)
            sources[key] = source
        units: dict[str, UUID] = {}

        def unit(day: str, code: str) -> UUID:
            full = f"{day}:AS:{code}"
            if full not in units:
                units[full] = cs.unit("us.precinct", full)
                if units[full] in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=units[full],
                        polity_id=state,
                        scheme_code="us.precinct",
                        code=full,
                        parent_id=parent,
                        area_relation="within",
                        area_id=territory,
                        kind="standard",
                        valid_from=DAYS[day],
                        valid_to=DAYS[day] + timedelta(days=1),
                    )
            return units[full]

        house: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
        questions: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            (house if row["kind"] == "house" else questions)[row["day"], int(row["contest"])].append(row)

        for (day, number), entries in sorted(house.items()):
            value, body, area = held[number]
            tail = value.split(":house:")[1]
            contest_key = f"{day}:AS:60:house:{tail}"
            seats = SEATS.get(number, 1)
            winners = sum(int(r["elected"]) for r in entries)
            if winners != (0 if day == "2016-11-08" else seats):
                raise ValueError(f"{contest_key}: {winners} winners for {seats} seats")
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[day],
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                term="full",
                magnitude=seats,
                round_number=1,
                electoral_system_id=cs.key("us.electoral_system", "fptp" if seats == 1 else "block"),
                default_result_kind="counted",
            )
            (label,) = one(
                conn,
                """select n.text from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   and x.value = %s join name n on n.entity_id = c.id and n.is_preferred""",
                [value],
            )
            label = label.removeprefix("2026 ").removesuffix(" election")
            if day < "2026" and number == 17:
                label = label.replace("Aitulagi/Leasina", "Leasina")
            cs.add(
                "name",
                entity_id=contest,
                text=f"{day[:4]} {label} election",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=f"us/as/{day}-general/house-{tail}", entity_id=contest)
            sheet, page = PAGES[day](number)
            cs.add("citation", entity_id=contest, source_id=sources[sheet], locator={"page": page} if page else None)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[day],
                contest_id=contest,
                stage="preliminary" if day in ("2016-11-08", "2024-11-05") else "certified",
                kind="counted",
                status="active",
                source_id=sources[sheet],
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=HOUSE_NOTE[day])
            for row in entries:
                name = display(row["label"])
                token = norm(name)
                option = cs.key("us.option", f"{contest_key}:{slug(token)}")
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=name)
                person = known.get(token)
                if person is None:
                    person = cs.key(f"{SOURCE}.person", f"AS:{slug(token)}")
                    known[token] = person
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest_key}:{slug(token)}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    is_independent=False,
                )
                if row["elected"] == "1":
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit(day, f"d{number:02d}"),
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=int(row["votes"]),
                )

        for (day, number), entries in sorted(questions.items()):
            sheet, page, title, text = QUESTIONS[day, number]
            designation = f"amendment-{number}" if day == "2022-11-08" else "veto-override-amendment"
            contest_key = f"{day}:AS:question:{designation}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[day],
                kind="ballot_question",
                district_area_id=territory,
                electoral_system_id=cs.key("us.electoral_system", "referendum"),
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            cs.add("name", entity_id=contest, text=title, lang="en", kind="official", is_preferred=True)
            cs.add("slug", path=f"us/as/{day}-general/{designation}", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=sources[sheet], locator={"page": page} if page else None)
            if text:
                ballot = "2022ballot" if day == "2022-11-08" else sheet
                cs.add(
                    "ballot_question_text",
                    contest_id=contest,
                    kind="text",
                    lang="en",
                    body=text,
                    url=SHEETS[ballot][0],
                    source_id=sources[ballot],
                    locator={"page": 1} if ballot == "2022ballot" else {"page": page},
                )
            options = {}
            for position, answer in enumerate(("Yes", "No"), start=1):
                options[answer] = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=answer,
                )
            printed = {r["label"]: int(r["votes"]) for r in entries if r["unit"] == "printed"}
            body_rows = [r for r in entries if r["unit"] != "printed"]
            sums = {answer: sum(int(r["votes"]) for r in body_rows if r["label"] == answer) for answer in options}
            gap = {answer: printed[answer] - sums[answer] for answer in options}
            expected = 17 if day == "2022-11-08" else 0
            if sum(gap.values()) != expected or min(gap.values()) < 0:
                raise ValueError(f"{contest_key}: rows short of the printed totals by {gap}")
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[day],
                contest_id=contest,
                stage="preliminary" if day == "2024-11-05" else "certified",
                kind="counted",
                status="active",
                source_id=sources[sheet],
                is_default=True,
                completeness=round(sum(sums.values()) / sum(printed.values()), 4),
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=QUESTION_NOTE[day])
            winner = max(options, key=lambda answer: printed[answer])
            cs.add("outcome", result_set_id=result_set, option_id=options[winner], kind="won", seats=None)
            for row in body_rows:
                code = row["unit"] if row["unit"] in ("central", "swains") else f"d{int(row['unit']):02d}"
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit(day, code),
                    option_id=options[row["label"]],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=int(row["votes"]),
                )
        return cs.write()
