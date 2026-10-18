import json
import re
import subprocess
import zipfile
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

import fastexcel
import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import (
    al_sos,
    ca_sos,
    elstats,
    fl_sos,
    la_sos,
    me_canvass,
    measure_precincts,
    mo_sos,
    ncsbe,
    wa_local,
)
from ea.store import DATA, connect

SOURCE = "us.measure_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ANSWERS = {
    "YES": ("YES", "YES"),
    "NO": ("NO", "YES"),
    "APPROVED": ("YES", "APPROVED"),
    "REJECTED": ("NO", "APPROVED"),
    "MAINTAINED": ("YES", "MAINTAINED"),
    "REPEALED": ("NO", "MAINTAINED"),
    "FOR": ("YES", "FOR"),
    "AGAINST": ("NO", "FOR"),
}
TAG = (
    r"(?i:(initiative|referendum|advisory vote|resolution|amendment|proposition|question|measure)"
    r"(?:\s+measure)?(?:\s+to the legislature)?\s*(?:no\.?|#|number\s+\w+\s+\()?)\s*(\d+|[A-Za-z])\b"
)
WAYBACK = "https://web.archive.org/web/{}id_/{}"
UTAH = {
    date(2014, 11, 4): (
        "20151220071902",
        "http://elections.utah.gov/Media/Default/2014%20Election/2014%20Final%20General%20Canvass%20Report.xlsx",
    ),
    date(2016, 11, 8): (
        "20230530231104",
        "https://elections.utah.gov/Media/Default/2016%20Election/2016%20General%20Election%20-%20Statewide%20Canvass.xlsx",
    ),
    date(2018, 11, 6): (
        "2025",
        "https://elections.utah.gov/Media/Default/2018%20Election/2018%20General%20Election%20Canvass.xlsx",
    ),
    date(2020, 11, 3): (
        "2025",
        "https://vote.utah.gov/wp-content/uploads/2021/02/2020-General-Election-Statewide-Canvass.xlsx",
    ),
}
OREGON = {
    date(2012, 11, 6): ("20150430200315", "results-11-2012.pdf"),
    date(2014, 11, 4): ("20150908230107", "results-2014-general-election.pdf"),
    date(2016, 11, 8): ("20170510104052", "november-2016-results.pdf"),
    date(2018, 1, 23): ("20190503173837", "january-2018-results.pdf"),
    date(2018, 11, 6): ("20190912090619", "november-2018-official-results.pdf"),
    date(2020, 11, 3): ("20210124071153", "november-general-2020.pdf"),
}
MISSOURI = {
    date(2012, 8, 7): "2012PrimaryElection.pdf",
    date(2012, 11, 6): "2012GeneralElection.pdf",
    date(2014, 8, 5): "2014PrimaryElection.pdf",
    date(2014, 11, 4): "2014GeneralElection.pdf",
    date(2016, 11, 8): "ActualResults-November82016-GeneralElection.pdf",
    date(2018, 8, 7): "ActualResults-August72018.pdf",
    date(2018, 11, 6): "ActualResults-November62018-FINAL.pdf",
    date(2020, 8, 4): "ActualResults-August42020.pdf",
    date(2020, 11, 3): "ActualResults-November32020.pdf",
    date(2024, 8, 6): "ActualResults-August62024.pdf",
    date(2024, 11, 5): "ActualResults-November52024.pdf",
}
ALABAMA = {
    date(2012, 9, 18): "voter-pdfs/2012/scae-results-certified-2012-10-09.pdf",
    date(2014, 11, 4): "voter-pdfs/2014/2014GeneralResults-WithoutWriteIn.pdf",
    date(2016, 11, 8): "voter-pdfs/2016/2016-Official-General-Election-Results-Certified-2016-11-29.pdf",
    date(2018, 11, 6): "voter-pdfs/2018/2018-Official-General-Election-Results-Certified-2018-11-27.pdf",
    date(2020, 11, 3): "election-2020/Final%20Canvass%20of%20Results-Merged.pdf",
    date(2022, 11, 8): "election-data/2022-11/Final%20Canvass%20of%20Results%20%28canvassed%20by%20state%20canvassing"
    "%20board%2011-28-2022%29.pdf",
}
ARIZONA = {
    date(2014, 11, 4): ("20141202160733", "http://www.azsos.gov/election/2014/General/Canvass2014GE.pdf"),
    date(2020, 11, 3): ("20231207175657", "https://azsos.gov/sites/default/files/2020_General_State_Canvass.pdf"),
}
GEORGIA = {
    date(2012, 11, 6): (
        "20190306082429",
        "http://results.enr.clarityelections.com/GA/42277/113204/reports/detailxml.zip",
    ),
    date(2018, 11, 6): (
        "20210105145724",
        "https://results.enr.clarityelections.com/GA/91639/222278/reports/detailxml.zip",
    ),
    date(2020, 11, 3): (
        "20210107083007",
        "https://results.enr.clarityelections.com//GA//105369/271927/reports/detailxml.zip",
    ),
}
MONTANA = {
    date(2012, 11, 6): ("20201219202556", "https://sosmt.gov/wp-content/uploads/attachments/2012_General_Canvass.pdf"),
    date(2014, 11, 4): (
        "20211103045454",
        "https://sosmt.gov/wp-content/uploads/attachments/2014-General-Official-Statewide-Canvass.pdf",
    ),
    date(2016, 11, 8): (
        "20181113075535",
        "https://sosmt.gov/wp-content/uploads/attachments/2016GeneralStatewideCanvass.pdf",
    ),
    date(2018, 11, 6): ("20190101181853", "https://sosmt.gov/wp-content/uploads/2018GeneralReportStateCanvass.pdf"),
    date(2020, 11, 3): ("20201203074647", "https://sosmt.gov/wp-content/uploads/State_Canvass_Report.pdf"),
    date(2022, 11, 8): ("20230104151335", "https://sosmt.gov/docs/769/results/49081/2022general-statecanvassreport"),
    date(2024, 11, 5): (
        "20241203210537",
        "https://sosmt.gov/docs/31/post-election/66775/2024-general-election-report-state-canvass",
    ),
}
MONTANA_KINDS = {
    "LEGISLATIVE REFERENDUM": "LR",
    "INITIATIVE REFERENDUM": "IR",
    "CONSTITUTIONAL INITIATIVE": "CI",
    "CONSTITUTIONAL AMENDMENT": "C",
    "INITIATIVE": "I",
}
WYOMING = {
    date(2016, 11, 8): "2016/Results/General/2016_Wyoming_General_Election_Results.pdf",
    date(2020, 11, 3): "2020/Results/General/2020_General_Statewide_Constitutional_Amendment_Summary.pdf",
    date(2022, 11, 8): "2022/Results/General/2022_General_Statewide_Constitutional_Amendment_Summary.pdf",
    date(2024, 11, 5): "2024/Results/General/2024_General_Statewide_Constitutional_Amendment_Summary.pdf",
}
NORTH_CAROLINA = {
    date(2018, 11, 6): (
        "2018-11-27/Canvass/Supplement_11_30_2018/State%20Composite%20Abstract%20Report%20-%20County.pdf",
        "2018-11-27/Canvass/Supplement_11_30_2018/State%20Composite%20Abstract%20Report%20-%20Contest.pdf",
    ),
    date(2024, 11, 5): (
        "2024-11-26/Canvass/Composite_Statewide_Abstract_Report.pdf",
        "2024-11-26/Canvass/State_Composite_Abstract_Report-Contest.pdf",
    ),
}
NAMED_QUESTIONS = {
    "PROTECT RIGHT TO HUNT AND FISH": "North Carolina Right to Hunt and Fish Amendment",
    "STRENGTHENING VICTIMS RIGHTS": "North Carolina Marsy's Law Crime Victims Rights Amendment",
    "MAXIMUM INCOME TAX RATE OF 7.0%": "North Carolina Income Tax Cap Amendment",
    "REQUIRE PHOTO ID TO VOTE": "North Carolina Voter ID Amendment",
    "NONPARTISAN JUDICIAL MERIT COMMISSION": "North Carolina Judicial Selection for Midterm Vacancies Amendment",
    "BIPARTISAN BOARD OF ETHICS AND ELECTIONS": "North Carolina Legislative Appointments to Elections Board Amendment",
    "Constitution Of Alabama Of 2022": "Constitution of Alabama of 2022",
}
CALIFORNIA = {
    date(2012, 6, 5): "2012-primary/123-ballot-measures-formatted.xls",
    date(2012, 11, 6): "2012-general/15-ballot-measures.xls",
    date(2014, 6, 3): "2014-primary/xls/107-ballot%20measures.xls",
    date(2014, 11, 4): "2014-general/xls/88-ballot-measures.xls",
    date(2016, 6, 7): "2016-primary/130-state-ballot-measures-formatted.xls",
    date(2016, 11, 8): "2016-general/sov/65-ballot-measures-formatted.xls",
    date(2018, 6, 5): "2018-primary/sov/csv-ballot-measures.xls",
    date(2018, 11, 6): "2018-general/sov/92-ballot-measures.xls",
    date(2020, 3, 3): "2020-primary/sov/170-state-ballot-measures-formatted.xls",
    date(2020, 11, 3): "2020-general/sov/58-ballot-measures.xlsx",
    date(2024, 3, 5): "2024-primary/sov/csv-ballot-measures.xlsx",
    date(2022, 11, 8): "2022-general/sov/csv-ballotmeasures.xlsx",
    date(2024, 11, 5): "2024-general/sov/csv-ballot-measures.xlsx",
}
NCSBE = "https://s3.amazonaws.com/dl.ncsbe.gov/State_Board_Meeting_Docs/"
METHOD = (
    "{level} totals from {document}, the state's certified results. They replace {replaced} as this question's "
    "default.{advisory}"
)
REPLACED = {
    "Wikipedia": "the totals transcribed from the English Wikipedia",
    "MIT Election Data and Science Lab": "MEDSL's totals summed from its precinct returns",
    "OpenElections": "OpenElections' totals summed from its precinct returns",
}
KANSAS_CITY = (
    " Kansas City, its own election authority, is reported apart from the Jackson, Clay, Platte, and Cass county "
    "authorities and is credited to the city."
)
OVERSEAS = " Overseas and military (UOCAVA) ballots, which Maine tabulates statewide, are a unit of their own."
ADVISORY = " As in the question's existing options, the export's Maintained is recorded as Yes and Repealed as No."
DOCUMENTS = {
    "AL": (
        al_sos.PUBLISHER,
        "Certified results of the State Canvassing Board",
        "https://www.sos.alabama.gov/alabama-votes/voter/election-information",
        "the Alabama State Canvassing Board's certified results",
    ),
    "WA": (
        wa_local.PUBLISHER,
        wa_local.TITLE,
        "https://results.vote.wa.gov/results/",
        "the Washington Secretary of State's county results export",
    ),
    "AZ": (
        "Arizona Secretary of State",
        "State of Arizona official canvass",
        "https://azsos.gov/elections/results-data",
        "the Arizona Secretary of State's official canvass",
    ),
    "FL": (
        fl_sos.PUBLISHER,
        fl_sos.TITLE,
        "https://results.elections.myflorida.com/",
        "the Florida Division of Elections' official results extract",
    ),
    "UT": (
        "Utah Lieutenant Governor's Office of Elections",
        "Official canvass by county",
        "https://vote.utah.gov",
        "the Utah Lieutenant Governor's statewide canvass workbook",
    ),
    "OR": (
        "Oregon Secretary of State",
        "Official results, abstract of votes",
        "https://sos.oregon.gov/elections/Pages/electionhistory.aspx",
        "the Oregon Secretary of State's abstract of votes",
    ),
    "GA": (
        "Georgia Secretary of State",
        "Official election results",
        "https://results.enr.clarityelections.com/GA/",
        "the Georgia Secretary of State's certified results by county",
    ),
    "ME": (
        me_canvass.PUBLISHER,
        "Referendum tabulations",
        "https://www.maine.gov/sos/cec/elec/results/",
        "the Maine Secretary of State's referendum tabulation",
    ),
    "NC": (
        ncsbe.PUBLISHER,
        "State composite abstract report",
        "https://www.ncsbe.gov/results-data",
        "the North Carolina State Board of Elections' state composite abstract",
    ),
    "LA": (
        la_sos.PUBLISHER,
        la_sos.TITLE,
        "https://voterportal.sos.la.gov/graphical",
        "the Louisiana Secretary of State's official results",
    ),
    "MT": (
        "Montana Secretary of State",
        "Statewide general election canvass",
        "https://sosmt.gov/elections/results/",
        "the Montana Secretary of State's statewide canvass",
    ),
    "CA": (
        ca_sos.PUBLISHER,
        ca_sos.TITLE,
        "https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
        "the California Secretary of State's Statement of Vote",
    ),
    "WY": (
        "Wyoming Secretary of State",
        "Statewide constitutional amendments official summary",
        "https://sos.wyo.gov/Elections/State/ElectionResults.aspx",
        "the Wyoming Secretary of State's official summary of the constitutional amendments",
    ),
    "MO": (
        mo_sos.PUBLISHER,
        mo_sos.TITLE,
        "https://www.sos.mo.gov/elections/resultsandstats",
        "the Missouri Secretary of State's official results",
    ),
}


def replaceable(conn) -> list[tuple[str, date]]:
    return conn.execute(
        """select distinct substr(p.iso_code, 4), e.date_from from contest c
           join election e on e.id = c.election_id join polity p on p.id = e.polity_id
           join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
           join result_set rs on rs.contest_id = c.id and rs.is_default join source s on s.id = rs.source_id
           where c.kind = 'ballot_question' and s.publisher = any(%s) and substr(p.iso_code, 4) = any(%s)
             and e.date_from between '2000-01-01' and '2025-12-31'
           order by 1, 2""",
        [list(REPLACED), list(DOCUMENTS)],
    ).fetchall()


def link(state: str, day: date) -> tuple[str, str]:
    if state == "UT":
        return SOURCE, WAYBACK.format(*UTAH[day]) if day in UTAH else ""
    if state == "GA":
        return SOURCE, WAYBACK.format(*GEORGIA[day]) if day in GEORGIA else ""
    if state == "AZ":
        return SOURCE, WAYBACK.format(*ARIZONA[day]) if day in ARIZONA else ""
    if state == "MT":
        return SOURCE, WAYBACK.format(*MONTANA[day]) if day in MONTANA else ""
    if state == "WY":
        return SOURCE, "https://sos.wyo.gov/Elections/Docs/" + WYOMING[day] if day in WYOMING else ""
    if state == "LA":
        return SOURCE, la_sos.blob(day, "Votes_Multiparish.htm")
    if state == "NC":
        return SOURCE, NCSBE + NORTH_CAROLINA[day][0] if day in NORTH_CAROLINA else ""
    if state == "AL":
        return SOURCE, "https://www.sos.alabama.gov/sites/default/files/" + ALABAMA[day] if day in ALABAMA else ""
    if state == "ME":
        return measure_precincts.SOURCE, (
            measure_precincts.ME_WAYBACK.format(*measure_precincts.ME_FILES[str(day)])
            if str(day) in measure_precincts.ME_FILES
            else ""
        )
    if state == "CA":
        return SOURCE, ca_sos.SOV + CALIFORNIA[day] if day in CALIFORNIA else ""
    if state == "MO":
        return SOURCE, mo_sos.SITE + MISSOURI[day] if day in MISSOURI else ""
    if state == "OR":
        return SOURCE, (
            WAYBACK.format(OREGON[day][0], "https://sos.oregon.gov/elections/Documents/results/" + OREGON[day][1])
            if day in OREGON
            else ""
        )
    owner, url = (
        (wa_local.SOURCE, wa_local.PRECINCTS.format(day=day).replace("_allstateprecincts.csv", "_AllCounties.csv"))
        if state == "WA"
        else (fl_sos.SOURCE, fl_sos.EXTRACT.format(day=day))
    )
    index = json.loads((DATA / "raw" / owner / "index.json").read_text())
    return (owner if url in index else SOURCE), url


def fetch() -> None:
    with connect() as conn:
        dates = replaceable(conn)
    official = {
        entry["ElectionDate"]
        for entry in json.loads(raw.fetch(SOURCE, la_sos.DATES, suffix=".json").path.read_text())["Dates"]["Date"]
        if entry["ResultsOfficial"] == "1"
    }
    for state, day in dates:
        if state == "LA":
            if f"{day:%m/%d/%Y}" not in official:
                continue
            races = json.loads(
                raw.fetch(SOURCE, la_sos.blob(day, "RacesCandidates_Multiparish.htm"), suffix=".json").path.read_text()
            )
            for race in la_sos.many((races.get("Races") or {}).get("Race")):
                if race["GeneralTitle"] == "Constitutional Amendment" and race.get("IsMultiParish") == "1":
                    raw.fetch(SOURCE, la_sos.blob(day, f"VotesRaceByParish/Votes_{race['ID']}.htm"), suffix=".json")
            raw.fetch(SOURCE, la_sos.blob(day, "Votes_Multiparish.htm"), suffix=".json")
            continue
        source, url = link(state, day)
        if source == SOURCE and url:
            for each in [url, NCSBE + NORTH_CAROLINA[day][1]] if state == "NC" else [url]:
                try:
                    raw.fetch(
                        SOURCE,
                        each,
                        suffix="." + each.rsplit(".", 1)[1]
                        if state == "CA"
                        else {"WA": ".csv", "FL": ".tsv", "UT": ".xlsx", "GA": ".zip"}.get(state, ".pdf"),
                    )
                except httpx.HTTPStatusError as error:
                    print(f"{state} {day}: {error}")


def parse() -> None:
    with connect() as conn:
        dates = replaceable(conn)
        names: dict[str, dict[str, str]] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and p.iso_code = any(%s) order by u.code""",
            [[f"US-{state}" for state in DOCUMENTS]],
        ).fetchall():
            plain = elstats.plain(text)
            names.setdefault(iso.removeprefix("US-"), {}).setdefault(plain, code)
            if int(code[2:]) >= 500:
                names[iso.removeprefix("US-")][f"{plain}city"] = code
    names["MO"] |= {"kansascity": "2938000", "statewide": "29"}
    names["ME"] |= {"uocava": "ME:uocava", "statewide": "23"}
    names["MT"] |= {"lewisand": "30049"}
    frames = []
    for state, day in dates:
        source, url = link(state, day)
        try:
            path = raw.cached(source, url).path
        except LookupError:
            continue
        if state == "AL":
            records = []
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
            columns: list[tuple[str, str]] = []
            for number, page in enumerate(text.stdout.split("\f"), start=1):
                alone = None
                for line in page.splitlines():
                    if re.search(r"(?i)amendment|#", line):
                        alone = None
                        columns = [
                            ("Statewide", "Constitution Of Alabama Of 2022") if constitution else (kind, measure)
                            for constitution, kind, measure in re.findall(
                                r"(?i)(constitution of alabama of 2022)|(\w+)\s+(?:#|amendment\s+number\s+\w+\s+\(|"
                                r"amendment\s+no\.\s*)(\d+)",
                                line,
                            )
                        ]
                    elif not re.search(r"\d", line) and elstats.plain(line) in names["AL"]:
                        alone = line.strip()
                    elif (
                        columns
                        and (matched := re.fullmatch(r"\W*([A-Za-z][\w .']*?)\s{2,}(\d.*)", line))
                        and (elstats.plain(matched[1]) in names["AL"] or matched[1].upper() in ("TOTAL", "TOTALS"))
                    ):
                        county = alone if alone is not None and matched[1].upper() == "TOTAL" else matched[1]
                        alone = None
                        numbers = re.findall(r"\d[\d,]*", matched[2])
                        for index, (kind, measure) in enumerate(columns):
                            office = measure if measure.startswith("Constitution") else f"Amendment {measure}"
                            if "statewide" in kind.lower() and len(numbers) >= 2 * index + 2:
                                records.append((county, office, "YES", numbers[2 * index], number))
                                records.append((county, office, "NO", numbers[2 * index + 1], number))
            table = pl.DataFrame(
                records,
                schema={
                    "county": pl.String,
                    "office": pl.String,
                    "answer": pl.String,
                    "votes": pl.String,
                    "page": pl.Int64,
                },
                orient="row",
            ).with_columns(count=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False))
            total = pl.col("county").str.to_uppercase().is_in(["TOTAL", "TOTALS"])
            table = (
                table.filter(
                    (pl.col("count").filter(total).sum() == pl.col("count").filter(~total).sum()).over(
                        "office", "answer"
                    )
                    & total.any().over("office", "answer")
                    & pl.col("count").is_not_null().all().over("office", "answer")
                )
                .filter(~total)
                .drop("count")
            )
        elif state == "WY":
            records = []
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
            office = None
            seen = set()
            for number, page in enumerate(text.stdout.split("\f"), start=1):
                for line in page.splitlines():
                    if heading := re.fullmatch(r"\s*(Constitutional Amendment [A-Z])\s*", line):
                        office = None if heading[1] in seen else heading[1]
                    elif office and (
                        matched := re.fullmatch(r"([A-Z][A-Za-z ]*?)\s+([\d,]+)\s+([\d,]+)\s+[\d,]+\s+[\d,]+\s*", line)
                    ):
                        if elstats.plain(matched[1]) in names["WY"] or matched[1] == "Total":
                            for answer, votes in zip(("FOR", "AGAINST"), matched.group(2, 3), strict=True):
                                records.append((matched[1], office, answer, votes, number))
                        if matched[1] == "Total":
                            seen.add(office)
                            office = None
            table = pl.DataFrame(
                records,
                schema={
                    "county": pl.String,
                    "office": pl.String,
                    "answer": pl.String,
                    "votes": pl.String,
                    "page": pl.Int64,
                },
                orient="row",
            ).with_columns(count=pl.col("votes").str.replace_all(",", "").cast(pl.Int64))
            total = pl.col("county") == "Total"
            table = (
                table.filter(
                    (pl.col("count").filter(total).sum() == pl.col("count").filter(~total).sum()).over(
                        "office", "answer"
                    )
                    & (total.sum().over("office", "answer") == 1)
                )
                .filter(~total)
                .drop("count")
            )
        elif state == "MT":
            records = []
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
            for number, page in enumerate(text.stdout.split("\f"), start=1):
                joined = " ".join(page.split())
                if spelled := re.search(r"(" + "|".join(MONTANA_KINDS) + r") (?:NO\.|C|CI)\s*-?\s*(\d+)", joined):
                    found = spelled[1], spelled[2]
                elif coded := re.search(r"\b(LR|IR|CI|C|I)-(\d+) A ", joined):
                    found = {code: kind for kind, code in MONTANA_KINDS.items()}[coded[1]], coded[2]
                else:
                    continue
                answers = ("FOR", "AGAINST") if "AGAINST" in page else ("YES", "NO")
                for line in page.splitlines():
                    if (matched := re.fullmatch(r"([A-Z][A-Za-z &.]*?)\s+([\d,]+)\s+([\d,]+)\s*", line)) and (
                        elstats.plain(matched[1]) in names["MT"] or matched[1].upper() == "TOTAL"
                    ):
                        county = "Total" if matched[1].upper() == "TOTAL" else matched[1]
                        for answer, votes in zip(answers, matched.group(2, 3), strict=True):
                            records.append((county, f"{found[0]} NO. {found[1]}", answer, votes, number))
            table = pl.DataFrame(
                records,
                schema={
                    "county": pl.String,
                    "office": pl.String,
                    "answer": pl.String,
                    "votes": pl.String,
                    "page": pl.Int64,
                },
                orient="row",
            ).with_columns(count=pl.col("votes").str.replace_all(",", "").cast(pl.Int64))
            total = pl.col("county") == "Total"
            table = (
                table.filter(
                    (pl.col("count").filter(total).sum() == pl.col("count").filter(~total).sum()).over(
                        "office", "answer"
                    )
                    & (total.sum().over("office", "answer") == 1)
                )
                .filter(~total)
                .drop("count")
            )
        elif state == "LA":
            records = []
            races = json.loads(raw.cached(SOURCE, la_sos.blob(day, "RacesCandidates_Multiparish.htm")).path.read_text())
            statewide = {
                race["ID"]: {choice["ID"]: int(choice["VoteTotal"]) for choice in la_sos.many(race["Choice"])}
                for race in la_sos.many(json.loads(path.read_text())["Races"]["Race"])
            }
            for race in la_sos.many(races["Races"]["Race"]):
                if race["GeneralTitle"] != "Constitutional Amendment" or race.get("IsMultiParish") != "1":
                    continue
                each = la_sos.blob(day, f"VotesRaceByParish/Votes_{race['ID']}.htm")
                answers = {choice["ID"]: choice["Desc"] for choice in la_sos.many(race["Choice"])}
                parishes = la_sos.many(json.loads(raw.cached(SOURCE, each).path.read_text())["Parishes"]["Parish"])
                sums = {choice: 0 for choice in answers}
                for parish in parishes:
                    for choice in la_sos.many(parish["Choice"]):
                        sums[choice["ID"]] += int(choice["VoteTotal"])
                if sums != statewide.get(race["ID"]):
                    print(f"LA {day} {race['SpecificTitle']}: parishes do not add up to the statewide total, skipped")
                    continue
                for parish in parishes:
                    for choice in la_sos.many(parish["Choice"]):
                        records.append(
                            (
                                f"22{2 * int(parish['ParishValue']) - 1:03}",
                                race["SpecificTitle"],
                                answers[choice["ID"]],
                                choice["VoteTotal"],
                                each,
                            )
                        )
            table = pl.DataFrame(
                records,
                schema={
                    "fips": pl.String,
                    "office": pl.String,
                    "answer": pl.String,
                    "votes": pl.String,
                    "url": pl.String,
                },
                orient="row",
            )
        elif state == "NC":
            totals: dict[tuple[str, str], int] = {}
            office = ""
            contest = raw.cached(SOURCE, NCSBE + NORTH_CAROLINA[day][1]).path
            text = subprocess.run(
                ["pdftotext", "-layout", str(contest), "-"], capture_output=True, text=True, check=True
            )
            for line in text.stdout.splitlines():
                if matched := re.fullmatch(r"(?:(\S.*?)\s{2,})?\s*(For|Against)\s+([\d,]+)(?:\s+[\d,]+){4}\s*", line):
                    office = matched[1] or office
                    totals[office, matched[2]] = int(matched[3].replace(",", ""))
            records = []
            county = ""
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
            for number, page in enumerate(text.stdout.split("\f"), start=1):
                for line in page.splitlines():
                    if re.fullmatch(r"[A-Z][A-Z .]*", line[:14].strip()):
                        county = line[:14].strip()
                    if matched := re.fullmatch(
                        r"\s*(?:(\S.*?)\s{2,})?(For|Against)\s+([\d,]+)(?:\s+[\d,]+){4}\s*", line[14:]
                    ):
                        office = matched[1] or office
                        if (office, matched[2]) in totals:
                            records.append((county, office, matched[2], matched[3], number))
            table = pl.DataFrame(
                records,
                schema={
                    "county": pl.String,
                    "office": pl.String,
                    "answer": pl.String,
                    "votes": pl.String,
                    "page": pl.Int64,
                },
                orient="row",
            )
            sums = table.group_by("office", "answer").agg(pl.col("votes").str.replace_all(",", "").cast(pl.Int64).sum())
            for office, answer, votes in sums.iter_rows():
                if votes != totals[office, answer]:
                    print(
                        f"NC {day} {office} {answer}: counties add up to {votes}, not {totals[office, answer]}, skipped"
                    )
                    table = table.filter(pl.col("office") != office)
        elif state == "GA":
            records = []
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("detail.xml"))
            for contest in root.iter("Contest"):
                office = (contest.get("text") or "").split("<br>")[0]
                if not re.search(r"(?i)amendment|referendum", office):
                    continue
                for choice in contest.iter("Choice"):
                    counties: dict[str, int] = {}
                    for county in choice.iter("County"):
                        counties[county.get("name") or ""] = counties.get(county.get("name") or "", 0) + int(
                            county.get("votes") or 0
                        )
                    if sum(counties.values()) != int(choice.get("totalVotes") or -1):
                        print(f"GA {day} {office} {choice.get('text')}: counties do not add up to the total, skipped")
                        continue
                    for county, votes in counties.items():
                        records.append((county, office, choice.get("text"), str(votes)))
            table = pl.DataFrame(
                records,
                schema={"county": pl.String, "office": pl.String, "answer": pl.String, "votes": pl.String},
                orient="row",
            )
        elif state == "AZ":
            records = []
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
            for number, page in enumerate(text.stdout.split("\f"), start=1):
                counties: list[str] = []
                title = None
                for line in page.splitlines():
                    if re.match(r"\s+Apache\s+Cochise", line):
                        counties = re.split(r"\s{2,}", line.strip())
                    elif matched := re.match(r"PROPOSITION (\d+)\s", line):
                        title = f"Proposition {matched[1]}"
                    elif (
                        title and counties and (matched := re.fullmatch(r"\s*(YES|NO)\s*(\*?)\s+([\d,\s]+?)\s*", line))
                    ):
                        numbers = matched[3].split()
                        if len(numbers) != len(counties):
                            continue
                        for county, votes in zip(counties, numbers, strict=True):
                            records.append(
                                (county, title, matched[1], votes, number, matched[1] if matched[2] else None)
                            )
            table = pl.DataFrame(
                records,
                schema={
                    "county": pl.String,
                    "office": pl.String,
                    "answer": pl.String,
                    "votes": pl.String,
                    "page": pl.Int64,
                    "winner": pl.String,
                },
                orient="row",
            ).with_columns(
                winner=pl.col("winner").drop_nulls().first().over("office"),
                count=pl.col("votes").str.replace_all(",", "").cast(pl.Int64),
            )
            total = pl.col("county").str.to_uppercase() == "TOTAL"
            table = (
                table.filter(
                    (pl.col("count").filter(total).sum() == pl.col("count").filter(~total).sum()).over(
                        "office", "answer"
                    )
                    & total.any().over("office", "answer")
                )
                .filter(~total)
                .drop("count")
            )
        elif state == "ME":
            records = []
            reader = fastexcel.read_excel(path.read_bytes())
            sheet = next(
                frame
                for name in reader.sheet_names
                if (frame := reader.load_sheet(name, header_row=None).to_polars()).height >= 100
            )
            cells = [
                [str(cell).strip().upper() if cell is not None else "" for cell in row] for row in sheet.iter_rows()
            ]
            header = next(number for number, row in enumerate(cells) if "YES" in row)
            labels = cells[header]
            pairs = [
                (column, next(other for other in range(column + 1, len(labels)) if labels[other] == "NO"))
                for column, label in enumerate(labels)
                if label == "YES"
            ]
            current = None
            for row in cells[header + 1 :]:
                current = next((name for name in measure_precincts.ME_NAMES if row[0] == name[:3]), current)
                words = re.findall(r"[A-Z]+", " ".join(cell for cell in row[:3] if not re.fullmatch(r"[\d.]*", cell)))
                if "UOCAVA" not in words and not any(word.startswith("TOTAL") for word in words):
                    continue
                named = next(
                    (
                        name
                        for word in words
                        for name in measure_precincts.ME_NAMES
                        if word == name[:3] or SequenceMatcher(None, word, name).ratio() >= 0.85
                    ),
                    None,
                )
                if "UOCAVA" in words and "INCLUDES" not in words:
                    county = "UOCAVA"
                elif {"STATE", "STATEWIDE", "GRAND"} & set(words):
                    county = "STATEWIDE"
                else:
                    county = named or (current if words[0].startswith("TOTAL") else None)
                if county is None:
                    continue
                for number, (yes, no) in enumerate(pairs, start=1):
                    records.append((county, f"Question {number}", "YES", row[yes]))
                    records.append((county, f"Question {number}", "NO", row[no]))
            table = pl.DataFrame(
                records,
                schema={"county": pl.String, "office": pl.String, "answer": pl.String, "votes": pl.String},
                orient="row",
            ).with_columns(
                votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64).cast(pl.String),
                statewide=pl.col("county") == "STATEWIDE",
            )
            local = ~pl.col("statewide")
            table = (
                table.with_columns(
                    complete=(
                        (
                            pl.col("votes").cast(pl.Int64).filter(local).sum()
                            == pl.col("votes").cast(pl.Int64).filter(~local).sum()
                        )
                        & (pl.col("county").filter(local & (pl.col("county") != "UOCAVA")).n_unique() == 16)
                        & (pl.col("county").filter(local).n_unique() == local.sum())
                    ).over("office", "answer")
                )
                .filter(pl.col("statewide").sum().over("office", "answer") == 1)
                .filter(pl.when(pl.col("complete").all().over("office")).then(local).otherwise(~local))
                .drop("statewide", "complete")
            )
        elif state == "MO":
            records = []
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
            for number, page in enumerate(text.stdout.split("\f"), start=1):
                title = None
                lines = page.splitlines()
                for index, line in enumerate(lines):
                    if matched := re.fullmatch(r"\s*((?:Amendment|Proposition)\s+\w+)\s+(YES)\s+(NO)\s+Total\s*", line):
                        title = matched[1]
                    elif title and (matched := re.fullmatch(r"\s*(\S.*?)\s{2,}(\d+)\s+(\d+)\s+(\d+)\s*", line)):
                        if matched[1].strip().upper() == "TOTAL":
                            title = None
                            continue
                        records.append((matched[1], title, "YES", matched[2], number))
                        records.append((matched[1], title, "NO", matched[3], number))
                    elif matched := re.fullmatch(
                        r"\s*((?:Constitutional\s+)?(?:Amendment|Proposition)\s+\w+)\s+(?:Ballot Issue\s+Yes:|Yes Votes)"
                        r"\s+([\d,]+).*",
                        line,
                    ):
                        following = next((rest for rest in lines[index + 1 :] if rest.strip()), "")
                        if no := re.fullmatch(r"\s*(?:No:|No Votes)\s+([\d,]+).*", following):
                            records.append(("statewide", matched[1], "YES", matched[2], number))
                            records.append(("statewide", matched[1], "NO", no[1], number))
            table = pl.DataFrame(
                records,
                schema={
                    "county": pl.String,
                    "office": pl.String,
                    "answer": pl.String,
                    "votes": pl.String,
                    "page": pl.Int64,
                },
                orient="row",
            )
        elif state == "OR":
            records = []
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
            for number, page in enumerate(text.stdout.split("\f"), start=1):
                title = None
                answers = None
                for line in page.splitlines():
                    if matched := re.fullmatch(r"(?i)\s*(?:state\s+)?measure\s+(?:no\.\s*)?(\d+)\s*", line):
                        title, answers = f"Measure {matched[1]}", None
                    elif title and (
                        matched := re.fullmatch(
                            r"\s*County\s+(\*?)(\w+)\s+(\*?)(\w+)(?:\s+County\s+(\*?)(\w+)\s+(\*?)(\w+))?\s*", line
                        )
                    ):
                        groups = matched.groups()[4:] if matched[5] is not None else matched.groups()[:4]
                        winner = groups[1] if groups[0] else groups[3] if groups[2] else None
                        answers = (groups[1], groups[3], winner, matched[5] is not None)
                    elif (
                        title
                        and answers
                        and (
                            matched := re.fullmatch(
                                r"\s*(\S.*?)\s{2,}([\d,]+)\s+([\d,]+)(?:\s+(\S.*?)\s{2,}([\d,]+)\s+([\d,]+))?\s*", line
                            )
                        )
                        and (matched[4] is not None) == answers[3]
                    ):
                        county, yes, no = matched.groups()[3:] if answers[3] else matched.groups()[:3]
                        if county.strip().upper() == "TOTAL":
                            title = answers = None
                            continue
                        records.append((county, title, answers[0], yes, number, answers[2]))
                        records.append((county, title, answers[1], no, number, answers[2]))
            table = pl.DataFrame(
                records,
                schema={
                    "county": pl.String,
                    "office": pl.String,
                    "answer": pl.String,
                    "votes": pl.String,
                    "page": pl.Int64,
                    "winner": pl.String,
                },
                orient="row",
            )
        elif state == "CA":
            rows = fastexcel.read_excel(str(path)).load_sheet(0, header_row=None, dtypes="string").to_polars().rows()
            if rows[0][3:4] in (("COUNTY_NAME",), ("County Name",)):
                records = [
                    (row[3], row[5], answer, votes)
                    for row in rows[1:]
                    for answer, votes in zip(("YES", "NO"), row[7:9], strict=True)
                ]
            else:
                header = next(
                    i for i, row in enumerate(rows) if "YES" in [(cell or "").strip().upper() for cell in row]
                )
                records = []
                title = None
                for j in range(1, len(rows[0])):
                    title = rows[0][j] or title
                    if (rows[header][j] or "").strip().upper() not in ANSWERS or title is None:
                        continue
                    records += [(row[0], title, rows[header][j], row[j]) for row in rows[header + 1 :] if row[0]]
            table = pl.DataFrame(
                records,
                schema={"county": pl.String, "office": pl.String, "answer": pl.String, "votes": pl.String},
                orient="row",
            ).filter(pl.col("county").map_elements(elstats.plain, return_dtype=pl.String).is_in(list(names["CA"])))
        elif state == "UT":
            records = []
            reader = fastexcel.read_excel(str(path))
            for sheet in reader.sheet_names:
                if not re.search(r"(?i)amend|ballot question", sheet):
                    continue
                rows = reader.load_sheet_by_name(sheet, header_row=None).to_polars().cast(pl.String).rows()
                header = next(i for i, row in enumerate(rows) if (row[0] or "").strip().upper() == "COUNTY")
                title = None
                for j in range(1, len(rows[header])):
                    title = next((rows[i][j] for i in reversed(range(header)) if rows[i][j]), title)
                    if (rows[header][j] or "").strip().upper() not in ANSWERS or title is None:
                        continue
                    for row in rows[header + 1 :]:
                        if row[0] is None or row[0].strip().upper().startswith("TOTAL"):
                            break
                        records.append((row[0], title, rows[header][j], row[j]))
            table = pl.DataFrame(
                records,
                schema={"county": pl.String, "office": pl.String, "answer": pl.String, "votes": pl.String},
                orient="row",
            ).with_columns(votes=pl.col("votes").cast(pl.Float64, strict=False).cast(pl.Int64).cast(pl.String))
        elif state == "WA":
            table = (
                pl.read_csv(path, infer_schema=False, encoding="utf8-lossy")
                .filter(pl.col("JurisdictionName").is_in(["State Executive", "State Measures", "Advisory Votes"]))
                .select(
                    county=pl.col("County"),
                    office=pl.col("Race").str.replace(
                        r"^(Washington State |Advisory Votes |State Measures |Proposed Constitutional Amendments )", ""
                    ),
                    answer=pl.col("Candidate"),
                    votes=pl.col("Votes"),
                )
            )
        else:
            table = pl.read_csv(
                path, separator="\t", infer_schema=False, quote_char=None, encoding="utf8-lossy"
            ).select(
                county=pl.col("CountyName"),
                office=pl.col("RaceCode") + " " + pl.col("OfficeDesc"),
                answer=pl.col("CanNameLast"),
                votes=pl.col("CanVotes"),
            )
        frames.append(
            table.select(
                state=pl.lit(state),
                day=pl.lit(day),
                url=pl.col("url") if "url" in table.columns else pl.lit(url),
                office=pl.col("office").str.replace_all(r"\s+", " ").str.strip_chars(),
                fips=pl.col("fips")
                if "fips" in table.columns
                else pl.col("county")
                .map_elements(elstats.plain, return_dtype=pl.String)
                .replace_strict(names[state], default=None),
                label=pl.col("answer").str.strip_chars().str.extract(r"(?i)^(\w+)").str.to_uppercase(),
                votes=pl.col("votes").str.replace_all(",", "").cast(pl.Int64, strict=False),
                page=pl.col("page").cast(pl.Int64) if "page" in table.columns else pl.lit(None, pl.Int64),
                winner=pl.col("winner")
                .str.to_uppercase()
                .replace_strict({label: answer for label, (answer, _) in ANSWERS.items()}, default=None)
                if "winner" in table.columns
                else pl.lit(None, pl.String),
            ).filter(pl.col("label").is_in(list(ANSWERS)))
        )
    frame = pl.concat(frames).with_columns(
        answer=pl.col("label").replace_strict({label: answer for label, (answer, _) in ANSWERS.items()}),
        pair=pl.col("label").replace_strict({label: pair for label, (_, pair) in ANSWERS.items()}),
    )
    spread = (
        frame.drop_nulls("fips")
        .filter(pl.col("fips").str.len_chars() != 2)
        .group_by("state", "url")
        .agg(pl.col("fips").n_unique().alias("counties"))
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.join(spread, on=["state", "url"], how="left")
        .with_columns(page=pl.col("page").min().over("state", "day", "url", "office"))
        .filter(
            pl.col("answer").n_unique().over("state", "day", "office") == 2,
            pl.col("pair").n_unique().over("state", "day", "office") == 1,
            pl.col("fips").is_null().sum().over("state", "day", "office") == 0,
            (pl.col("fips").n_unique().over("state", "day", "office") == pl.col("counties"))
            | (pl.col("fips").str.len_chars() == 2).all().over("state", "day", "office"),
        )
        .group_by("state", "day", "url", "page", "winner", "office", "pair", "fips", "answer")
        .agg(pl.col("votes").sum())
        .sort("state", "day", "office", "fips", "answer")
        .write_parquet(PARSED / "canvass.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "canvass.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot questions' certified county totals from the states' canvasses, replacing "
            "Wikipedia's, MEDSL's, and OpenElections' totals as the default",
            polity="US",
        )
        wiki = pl.DataFrame(
            conn.execute(
                """select x.value, substr(p.iso_code, 4), e.date_from, rs.id::text, rs.election_id::text,
                          rs.contest_id::text, rs.stage, rs.kind, rs.source_id::text, rs.completeness::float8,
                          max(o.id::text) filter (where upper(o.ballot_label) in ('YES', 'FOR')),
                          max(o.id::text) filter (where upper(o.ballot_label) in ('NO', 'AGAINST')),
                          sum(v.value) filter (where upper(o.ballot_label) in ('YES', 'FOR'))::bigint,
                          sum(v.value) filter (where upper(o.ballot_label) in ('NO', 'AGAINST'))::bigint,
                          (select min(n.text) from name n where n.entity_id = c.id), s.publisher
                   from contest c
                   join election e on e.id = c.election_id join polity p on p.id = e.polity_id
                   join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join source s on s.id = rs.source_id and s.publisher = any(%s)
                   join result_value v on v.result_set_k = rs.k and v.measure = 1
                   join contest_option o on o.k = v.option_k
                   where c.kind = 'ballot_question' and substr(p.iso_code, 4) = any(%s)
                     and split_part(x.value, ':', 2) = substr(p.iso_code, 4)
                   group by c.id, s.publisher, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10""",
                [list(REPLACED), list(DOCUMENTS)],
            ).fetchall(),
            schema=[
                "key",
                "state",
                "day",
                "set",
                "election",
                "contest",
                "stage",
                "kind",
                "source",
                "completeness",
                "yes_option",
                "no_option",
                "wyes",
                "wno",
                "name",
                "publisher",
            ],
            orient="row",
        )
        questions = (
            frame.group_by("state", "day", "url", "page", "winner", "office", "pair")
            .agg(
                statewide=(pl.col("fips").str.len_chars() == 2).all(),
                kansas=(pl.col("fips") == "2938000").any(),
                overseas=(pl.col("fips") == "ME:uocava").any(),
                yes=pl.col("votes").filter(pl.col("answer") == "YES").sum(),
                no=pl.col("votes").filter(pl.col("answer") == "NO").sum(),
            )
            .with_columns(
                tag=pl.when(pl.col("state") == "FL")
                .then(pl.concat_str(pl.lit("amendment "), pl.col("office").str.extract(r"^A0*(\d+) ")))
                .when(pl.col("state") == "MT")
                .then(
                    pl.concat_str(
                        pl.col("office").str.extract(r"^(.*) NO\.").replace_strict(MONTANA_KINDS, default=None),
                        pl.lit("-"),
                        pl.col("office").str.extract(r"NO\. (\d+)$"),
                    )
                )
                .when(pl.col("state") == "LA")
                .then(pl.concat_str(pl.lit("amendment "), pl.col("office").str.extract(r"(?i)^CA No\. (\d+) ")))
                .otherwise(
                    pl.concat_str(
                        pl.col("office").str.extract(TAG, 1).str.to_lowercase(),
                        pl.lit(" "),
                        pl.col("office").str.extract(TAG, 2).str.to_lowercase(),
                    )
                ),
                ballot=pl.len().over("state", "day"),
            )
        )
        wiki = wiki.with_columns(
            wtag=pl.when(pl.col("state") == "MT")
            .then(
                pl.coalesce(
                    pl.col("name").str.extract(r"\b((?:LR|IR|CI|C|I)-\d+)\b"),
                    pl.concat_str(
                        pl.col("name")
                        .str.extract(r"(?i)^(" + "|".join(MONTANA_KINDS) + r") No\.")
                        .str.to_uppercase()
                        .replace_strict(MONTANA_KINDS, default=None),
                        pl.lit("-"),
                        pl.col("name").str.extract(r"(?i)No\. (\d+)"),
                    ),
                )
            )
            .otherwise(
                pl.concat_str(
                    pl.col("name").str.extract(TAG, 1).str.to_lowercase(),
                    pl.lit(" "),
                    pl.col("name").str.extract(TAG, 2).str.to_lowercase(),
                )
            ),
            wballot=pl.len().over("state", "day"),
        )
        pairs = (
            questions.join(wiki, on=["state", "day"])
            .filter(
                (pl.col("tag") == pl.col("wtag"))
                | (pl.col("office").replace_strict(NAMED_QUESTIONS, default=None) == pl.col("name"))
                | (pl.col("wtag").is_null() & (pl.col("ballot") == 1) & (pl.col("wballot") == 1))
            )
            .filter(pl.len().over("key") == 1, pl.len().over("state", "day", "office") == 1)
        )
        for row in pairs.filter((pl.col("yes") != pl.col("wyes")) | (pl.col("no") != pl.col("wno"))).iter_rows(
            named=True
        ):
            print(row["key"], row["yes"] - row["wyes"], row["no"] - row["wno"])
        print(wiki.join(pairs, on="key", how="anti").select("key").to_series().to_list())
        sources = {}
        for state, (publisher, title, url, _) in DOCUMENTS.items():
            sources[state] = cs.source(publisher, title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
        artifacts = {}
        for state, day, url in pairs.select("state", "day", "url").unique().iter_rows():
            artifacts[url] = cs.artifact(raw.cached(link(state, day)[0], url), sources[state])
        landed: dict[str, int] = {}
        for row in pairs.iter_rows(named=True):
            result_set = cs.key(f"{SOURCE}.result_set", f"{row['key']}:county")
            if result_set not in cs.minted:
                continue
            old = UUID(row["set"])
            outcomes = conn.execute(
                "select option_id, kind, seats from outcome where result_set_id = %s", [old]
            ).fetchall()
            options = {"YES": UUID(row["yes_option"]), "NO": UUID(row["no_option"])}
            if row["winner"] is not None and [option for option, kind, _ in outcomes if kind == "won"] != [
                options[row["winner"]]
            ]:
                print(
                    f"{row['key']}: the document marks {row['winner']} as passing, unlike the recorded outcome, skipped"
                )
                continue
            cs.add(
                "result_set",
                id=old,
                election_id=UUID(row["election"]),
                contest_id=UUID(row["contest"]),
                stage=row["stage"],
                kind=row["kind"],
                source_id=UUID(row["source"]),
                status="superseded",
                is_default=False,
                completeness=row["completeness"],
            )
            cs.add(
                "result_set",
                id=result_set,
                election_id=UUID(row["election"]),
                contest_id=UUID(row["contest"]),
                stage="certified",
                kind="counted",
                source_id=sources[row["state"]],
                status="active",
                is_default=True,
                supersedes_id=old,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD.format(
                    level="Statewide" if row["statewide"] else "County",
                    document=DOCUMENTS[row["state"]][3],
                    replaced=REPLACED[row["publisher"]],
                    advisory=ADVISORY
                    if row["pair"] == "MAINTAINED"
                    else KANSAS_CITY
                    if row["kansas"]
                    else OVERSEAS
                    if row["overseas"]
                    else "",
                ),
            )
            cs.add(
                "citation",
                entity_id=result_set,
                source_id=sources[row["state"]],
                locator={"url": row["url"], "artifact": str(artifacts[row["url"]]), "race": row["office"]}
                | ({} if row["page"] is None else {"page": row["page"]}),
            )
            for option, kind, seats in outcomes:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            for fips, answer, votes in (
                frame.filter(
                    pl.col("state") == row["state"], pl.col("day") == row["day"], pl.col("office") == row["office"]
                )
                .select("fips", "answer", "votes")
                .iter_rows()
            ):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit(
                        "us.aggregate" if ":" in fips else {2: "us.state", 5: "us.county", 7: "us.place"}[len(fips)],
                        fips,
                    ),
                    option_id=options[answer],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            landed[row["state"]] = landed.get(row["state"], 0) + 1
        print(f"{pairs.height} questions paired, {sorted(landed.items())} new defaults")
        return cs.write()
