import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_statewide, openelections
from ea.store import DATA, connect

SOURCE = "us.wi_judicial"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
LEGACY = "https://elections.wi.gov/sites/default/files/legacy/"
DOCUMENTS = "https://elections.wi.gov/sites/default/files/documents/"
WAYBACK = "https://web.archive.org/web/2014if_/http://gab.wi.gov/sites/default/files/"
SUPREME = "Justice of the Wisconsin Supreme Court"
APPEALS = "Wisconsin Court of Appeals Judge"
# (election day, report URL, summary URL for years whose column headers are printed vertically)
FILES: list[tuple[date, str, str | None]] = [
    (date(2001, 4, 3), LEGACY + "2001_SpringElection_CountyResults.pdf", LEGACY + "2001_SpringElection_SummaryResults.pdf"),
    (date(2002, 4, 2), LEGACY + "elec02_cbc.pdf", LEGACY + "elec02_results.pdf"),
    (date(2003, 4, 1), LEGACY + "elec03_cbc.pdf", LEGACY + "elec03_results.pdf"),
    (date(2004, 4, 6), LEGACY + "cxc%25204_06_04.pdf", None),
    (date(2005, 4, 5), LEGACY + "2005_SpringElection_SupCt_CountyReturns.pdf", None),
    (date(2005, 4, 5), LEGACY + "2005_SpringElection_CtofAppeals_CountyReturns.pdf", None),
    (date(2006, 4, 4), LEGACY + "2006_SpringElection_SupCt_CountyReturns.pdf", None),
    (date(2006, 4, 4), LEGACY + "2006_SpringElection_CtofAppeals_CountyReturns.pdf", None),
    (date(2007, 4, 3), LEGACY + "2007_SpringElection_SupCt_CountyReturns.pdf", None),
    (date(2008, 4, 1), LEGACY + "2008_SpringElection_SupCt_CountyReturns.pdf", None),
    (date(2008, 4, 1), LEGACY + "2008_SpringElection_CtofAppeals_CountyReturns.pdf", None),
    (date(2009, 4, 7), LEGACY + "2009_SpringElection_SupCt_CountyReturns.pdf", None),
    (date(2009, 4, 7), LEGACY + "2009_SpringElection_CtofAppeals-1_CountyReturns.pdf", None),
    (date(2010, 4, 6), LEGACY + "spring%2520elec_cxc_appeals.pdf", None),
    (date(2011, 4, 5), WAYBACK + "County%20results_supreme%20recount.pdf", None),
    (date(2011, 4, 5), WAYBACK + "County%20results_4.5.11_appeals.pdf", None),
    (date(2013, 4, 2), LEGACY + "County%2520Results_4.2.13%2520Spring%2520Election_Justice%2520Supreme%2520Court.pdf", None),
    (date(2013, 4, 2), LEGACY + "County%2520Results_4.2.13%2520Spring%2520Election_Court%2520of%2520Appeals.pdf", None),
    (date(2014, 4, 1), LEGACY + "Results%2520by%2520County%2520Report.%25204.1.14%2520Spring%2520Election-Appeals.pdf", None),
    (date(2015, 4, 7), LEGACY + "County%2520Results%25204.7.15%2520Spring%2520Election.pdf", None),
    (date(2016, 4, 5), LEGACY + "Supreme%2520Court%2520County%2520by%2520County%2520AMENDED%2520Report-Brown%2520Co.pdf", None),
    (date(2016, 4, 5), LEGACY + "County%2520by%2520County%2520Report-Court%2520of%2520Appeals.pdf", None),
    (date(2017, 4, 4), LEGACY + "2017%2520Spring%2520Election-Supreme%2520Court-County%2520Report.pdf", None),
    (date(2017, 4, 4), LEGACY + "2017%2520Spring%2520Election-Court%2520of%2520Appeals-County%2520Report.pdf", None),
    (date(2018, 4, 3), LEGACY + "County%2520Report-4.3.18%2520Spring%2520Election-Supreme%2520Court.pdf", None),
    (date(2018, 4, 3), LEGACY + "County%2520Report-4.3.18%2520Spring%2520Election-Court%2520of%2520Appeals.pdf", None),
    (date(2019, 4, 2), LEGACY + "Spring%2520Election%25204.2.19-CxC%2520Report-Supeme%2520Court%2520.pdf", None),
    (date(2019, 4, 2), LEGACY + "Spring%2520Election%25204.2.19-CxC%2520Report-Court%2520of%2520Appeals.pdf", None),
    (date(2020, 4, 7), LEGACY + "County%2520by%2520County%2520Report_Supreme%2520Court.pdf", None),
    (date(2020, 4, 7), LEGACY + "County%2520by%2520County%2520Report_Court%2520of%2520Appeals.pdf", None),
    (date(2021, 4, 6), LEGACY + "County%2520by%2520County%2520Report-4.6.21%2520Spring%2520Election-Court%2520of%2520Appeals.pdf", None),
    (date(2022, 4, 5), DOCUMENTS + "2022%20Spring%20Election%20Court%20of%20Appeals%20County%20by%20County%20Results_0.pdf", None),
    (date(2023, 2, 21), DOCUMENTS + "County%20by%20County%20Report_Spring%20Primary_Supreme%20Court_2_21_23.pdf", None),
    (date(2023, 4, 4), DOCUMENTS + "County%20by%20County%20Report_SCOWIS.pdf", None),
    (date(2023, 4, 4), DOCUMENTS + "County%20by%20County%20Report_Court%20of%20Appeals.pdf", None),
]
SPELLED = {(date(2010, 4, 6), "District 4"): ["Edward E. Leineweber", "Brian Blanchard"]}
METHOD = (
    "County totals are the rows of the Wisconsin Elections Commission's county-by-county canvass report for the "
    "spring election (the Government Accountability Board's or the State Elections Board's report before 2016). "
    "Each candidate's county rows add up to the report's printed office total. Votes for people not on the ballot are "
    "the report's scattering column."
)
OFFICE = re.compile(
    r"^\s*(JUSTICE OF THE SUPREME COURT|Supreme Court|COURT OF APPEALS(?: JUDGE)?[,\s-]*DISTRICT\s+(\d)|Court of Appeals,?\s+District\s+(\d))\b",
    re.I,
)
NUMBER = re.compile(r"\d[\d,]*")
PARTY = re.compile(r"^(NP|NON|NPA|IND)\s+", re.I)


def fetch() -> None:
    for _, report, summary in FILES:
        raw.fetch(SOURCE, report, suffix=".pdf")
        if summary is not None:
            raw.fetch(SOURCE, summary, suffix=".pdf")


def offices(url: str, plain: dict[str, str]) -> list[dict]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    found: list[dict] = []
    current = None
    for line in text.replace("\f", "\n").split("\n"):
        match = OFFICE.match(line)
        if match and not NUMBER.search(line[: match.end()].replace(match.group(2) or match.group(3) or "", "")):
            district = match.group(2) or match.group(3)
            title = "Supreme Court" if district is None else f"District {district}"
            if current is None or current["post"] != title or current["done"]:
                current = {"post": title, "rows": [], "header": [], "totals": None, "done": False, "centers": None}
                found.append(current)
            current["heading"] = True
            continue
        if current is None or current["done"] or not line.strip():
            continue
        numbers = list(NUMBER.finditer(line))
        key = re.sub(r"[^a-z]", "", re.sub(r"\d[\d,]*.*$", "", re.sub(r"(?i)\s+county\b", "", line.strip())).lower())
        if re.match(r"(?i)\s*office totals", line):
            current["totals"] = [int(n.group().replace(",", "")) for n in numbers]
            current["done"] = True
        elif numbers and key in plain:
            if current["centers"] is None:
                current["centers"] = [(n.start() + n.end()) / 2 for n in numbers]
            current["rows"].append((plain[key], [int(n.group().replace(",", "")) for n in numbers]))
            current["heading"] = False
        elif current.get("heading") and not numbers and current["centers"] is None:
            current["header"].append(line)
    return found


def summary_names(url: str) -> dict[str, dict[int, str]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    result: dict[str, dict[int, str]] = {}
    office = None
    for line in text.split("\n"):
        heading = re.match(r"^\s*Office\s*:?\s+(.+?)\s{2,}Total Vote", line)
        if heading:
            office = heading.group(1).strip()
            result[office] = {}
            continue
        entry = re.match(r"^\s*(?:Winner\s+)?([\d,]+)\s+[\d.]+\s*%\s+\w+\s+\d+\s+(.+?)\s*$", line)
        if entry and office is not None:
            result[office][int(entry.group(1).replace(",", ""))] = entry.group(2)
    return result


def header_names(entry: dict) -> list[str]:
    centers = entry["centers"]
    parts: list[list[str]] = [[] for _ in centers]
    for line in entry["header"]:
        if re.search(r"Report|Page|Canvass|Wisconsin|Run:", line):
            continue
        for match in re.finditer(r"\S+(?: \S+)*", line):
            fragment = re.sub(r"\s+", " ", re.sub(r"(?i)\bscattering\b", "", match.group())).strip()
            if not fragment or re.fullmatch(r"(?i)(NP|NON|NPA)( (NP|NON|NPA))*|Total( Votes)?( Cast)?|County|Votes Cast", fragment):
                continue
            middle = (match.start() + match.end()) / 2
            parts[min(range(len(centers)), key=lambda index: abs(centers[index] - middle))].append(fragment)
    return [PARTY.sub("", " ".join(part)).strip() for part in parts]


def parse() -> None:
    with connect() as conn:
        plain = openelections.counties(conn)["WI"]
    codes = {re.sub(r"[^a-z]", "", name.lower()): name for name in plain}
    returns: list[dict] = []
    races: list[dict] = []
    for day, report, summary in FILES:
        printed = summary_names(summary) if summary is not None else {}
        for entry in offices(report, codes):
            if entry["totals"] is None or entry["centers"] is None:
                raise ValueError(f"{day} {entry['post']}: no office totals")
            width = len(entry["centers"])
            sums = [sum(row[1][index] for row in entry["rows"]) for index in range(width)]
            if sums != entry["totals"] or any(len(row[1]) != width for row in entry["rows"]):
                raise ValueError(f"{day} {entry['post']}: county rows do not add up to the office total")
            if summary is not None:
                label = "Supreme Court" if entry["post"] == "Supreme Court" else f"Court of Appeals, {entry['post']}"
                lookup = {re.sub(r"\s+", " ", office): votes for office, votes in printed.items()}[label]
                candidates = [lookup[total] for total in entry["totals"][1:-1]]
            else:
                candidates = SPELLED.get((day, entry["post"])) or header_names(entry)[1:-1]
            if len(candidates) != width - 2 or not all(candidates):
                raise ValueError(f"{day} {entry['post']}: candidates {candidates}")
            title = SUPREME if entry["post"] == "Supreme Court" else APPEALS
            post = None if entry["post"] == "Supreme Court" else entry["post"]
            contest = {"year": day.year, "day": day, "state": "WI", "title": title, "post": post, "special": False}
            for county, cells in entry["rows"]:
                for index, votes in enumerate(cells[1:], start=1):
                    scattering = index == width - 1
                    returns.append(
                        {
                            **contest,
                            "magnitude": 1,
                            "kind": "county",
                            "code": "county",
                            "county": plain[county],
                            "candidate": "SCATTERING" if scattering else candidates[index - 1].upper(),
                            "party": None,
                            "writein": scattering,
                            "measure": 1,
                            "votes": votes,
                        }
                    )
            races.append(
                {
                    **contest,
                    "magnitude": 1,
                    "present": sorted({plain[county] for county, cells in entry["rows"] if cells[0] > 0}),
                    "counties": len(entry["rows"]),
                }
            )
            print(day, entry["post"], candidates, entry["totals"][1:-1], len(entry["rows"]))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(returns, infer_schema_length=None).with_columns(
        pl.col("year").cast(pl.Int32),
        pl.col("magnitude").cast(pl.Int16),
        pl.col("measure").cast(pl.Int16),
        pl.col("party").cast(pl.String),
        pl.col("post").cast(pl.String),
    ).write_parquet(PARSED / "returns.parquet")
    pl.DataFrame(races, infer_schema_length=None).with_columns(
        pl.col("year").cast(pl.Int32), pl.col("magnitude").cast(pl.Int16), pl.col("post").cast(pl.String)
    ).write_parquet(PARSED / "races.parquet")


def emit() -> Path:
    def cite(cs: Changeset) -> dict[str, UUID]:
        title = "County-by-county canvass reports for the spring elections"
        source = cs.source("Wisconsin Elections Commission", title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher="Wisconsin Elections Commission",
                title=title,
                url="https://elections.wi.gov/elections/election-results/results-all",
                license="Public record",
            )
        for _, report, summary in FILES:
            cs.artifact(raw.cached(SOURCE, report), source)
            if summary is not None:
                cs.artifact(raw.cached(SOURCE, summary), source)
        return {"WI": source}

    return medsl_statewide.emit(
        pipeline=SOURCE,
        version=VERSION,
        parsed=PARSED,
        summary="Wisconsin's spring elections for the Supreme Court and the Court of Appeals by county",
        cite=cite,
        method=METHOD,
        close="The leading candidates were within half a percent in these totals, so no outcome is recorded from them.",
        absent="The source has no rows for {counties} in this race, so those votes are missing from these totals.",
        declared={
            "2011-04-05:WI:justice-of-the-wisconsin-supreme-court": (
                "David T. Prosser, Jr.",
                "These are the totals after the statewide recount, which the Government Accountability Board "
                "certified on May 23, 2011; Prosser led by 7,004 votes, which is 0.47 percent of the votes cast.",
            ),
            "2019-04-02:WI:justice-of-the-wisconsin-supreme-court": (
                "Brian Hagedorn",
                "Hagedorn led by 5,981 votes, which is 0.49 percent of the votes cast; the Wisconsin Elections Commission "
                "certified the result without a recount.",
            ),
        },
        season="spring election",
    )
