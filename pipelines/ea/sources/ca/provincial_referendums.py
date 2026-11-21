"""Provincial referendums and plebiscites in Quebec (1980, 1995), British Columbia (2005, 2009, 2011, 2018), and Prince
Edward Island (2005, 2016), by electoral district, from each province's chief electoral officer."""

import html
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl
import xlrd

from ea import package, raw
from ea.package import Package
from ea.sources.licenses import UNLICENSED

SOURCE = "ca.provincial_referendums"
AREAS = Path(__file__).with_name("provincial_referendum_areas.tsv")
CANADA = "01a13fb6-ea9b-72f8-8170-25b12e45cb98"
QUEBEC = "01a13fb6-eab7-7501-a0f9-4903a58f045b"
BRITISH_COLUMBIA = "01a13fb6-eab7-7501-a0f9-4908fbc129f2"
PEI = "01a13fb6-eab7-7501-a0f9-4900c883e563"

QC_PAGE = "https://www.electionsquebec.qc.ca/en/results-and-statistics/"
QC_1980 = f"{QC_PAGE}referendum-on-the-1980-sovereignty-association-proposal-for-quebec/"
QC_1995 = f"{QC_PAGE}1995-referendum-on-quebecs-accession-to-sovereignty/"
BC_RPT = "https://elections.bc.ca/docs/rpt"
BC_2005_XLS = f"{BC_RPT}/2005RefResults/2005-Referendum-on-Electoral-Reform-data.xls"
BC_2005_REPORT = f"{BC_RPT}/2005-CEOreportRefOnElectoralReform.pdf"
BC_2009_XLS = f"{BC_RPT}/2009Ref/2009Ref-Results-Excel.xls"
BC_2009_REPORT = f"{BC_RPT}/2009Ref/2009-Ref-SOV.pdf"
BC_2011_REPORT = f"{BC_RPT}/2011-CEO-HST-Referendum-Report.pdf"
BC_2018_REPORT = f"{BC_RPT}/2018-CEO-2018-Referendum-Report.pdf"
PEI_FILES = "https://www.electionspei.ca/sites/www.electionspei.ca/files"
PEI_2005 = f"{PEI_FILES}/epei_MMPRplebis_1.pdf"
PEI_2016 = f"{PEI_FILES}/elec_demrefpleb.pdf"
FILES = {
    QC_1980: ".html",
    QC_1995: ".html",
    BC_2005_XLS: ".xls",
    BC_2005_REPORT: ".pdf",
    BC_2009_XLS: ".xls",
    BC_2009_REPORT: ".pdf",
    BC_2011_REPORT: ".pdf",
    BC_2018_REPORT: ".pdf",
    PEI_2005: ".pdf",
    PEI_2016: ".pdf",
}
# Names the authorities print in a short or misspelled form, as the district is named elsewhere.
ALIASES = {"Brôme-Missi": "Brome-Missisquoi", "Cariboo-Chilcoltin": "Cariboo-Chilcotin"}
VOTES, REGISTERED, BALLOTS, VALID, INVALID, EXHAUSTED = 1, 2, 3, 4, 5, 21


def number(text: str) -> int:
    return int(re.sub(r"[^\d]", "", text))


def fold(name: str) -> str:
    text = unicodedata.normalize("NFKD", name.replace("—", "-").replace("–", "-"))
    text = (
        "".join(c for c in text if not unicodedata.combining(c)).lower().replace("saint", "st").replace("sainte", "ste")
    )
    return re.sub(r"[^a-z0-9]+", "", text)


def cells(row: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
        for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL)
    ]


def quebec(url: str) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """The province's summary and each electoral division's counts from Élections Québec's results page."""
    text = raw.cached(SOURCE, url).path.read_text(errors="ignore")
    rows = [cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.DOTALL)]
    summary = {r[0]: number(r[1]) for r in rows if len(r) == 2 and re.fullmatch(r"[\d,]+", r[1])}
    total = {
        "registered": summary["Number of registered electors"],
        "yes": summary["Number of votes for the Yes option"],
        "no": summary["Number of votes for the No option"],
        "valid": summary["Number of valid ballots"],
        "invalid": summary["Number of rejected ballots"],
        "ballots": summary["Number of votes cast"],
    }
    divisions: dict[str, dict[str, int]] = {}
    for r in rows:
        if len(r) == 5 and re.match(r"[\d,\s]+\(", r[1]):
            divisions.setdefault(r[0], {}).update(yes=number(r[1].split("(")[0]), no=number(r[2].split("(")[0]))
        elif len(r) == 4 and re.fullmatch(r"[\d,\s]+", r[1]) and re.fullmatch(r"[\d,\s]+", r[3]):
            divisions.setdefault(r[0], {}).update(ballots=number(r[1]), registered=number(r[3]))
        elif len(r) == 3 and re.match(r"[\d,\s]+\(", r[1]):
            divisions.setdefault(r[0], {}).update(valid=number(r[1].split("(")[0]), invalid=number(r[2].split("(")[0]))
    return total, {ALIASES.get(k, k): v for k, v in divisions.items()}


def layout(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True, text=True
    ).stdout


def blank(value: object) -> int:
    return 0 if value in ("", "-") else int(float(str(value)))


def british_columbia(url: str, sheets_start: int = 1) -> dict[str, dict[str, int]]:
    """Each electoral district's grand totals from an Elections BC voting-area workbook: the first option (the
    existing system or Yes), the second (the proposal), valid and rejected ballots, and registered voters."""
    book = xlrd.open_workbook(raw.cached(SOURCE, url).path)
    first = book.sheet_by_index(0)
    codes: dict[str, str] = {}
    for r in range(first.nrows):
        a, b = (str(v).strip() for v in first.row_values(r)[:2])
        if a and b and a != "Electoral District":
            code, name = (a, b) if len(a) <= 3 and a.isupper() else (b, a)
            codes[code] = name
    out: dict[str, dict[str, int]] = {}
    for sheet in book.sheets()[1:]:
        name = str(sheet.row_values(0)[0]).strip() or codes[sheet.name.split("-")[-1]]
        found: dict[str, int] = {}
        for r in range(sheet.nrows):
            row = sheet.row_values(r)
            label = str(row[0]).strip().lower()
            if label == "grand totals":
                found.update(
                    first=blank(row[1]),
                    second=blank(row[2]),
                    valid=blank(row[3]),
                    invalid=blank(row[4]),
                    registered=blank(row[5]),
                )
            elif label == "registered voters who voted":
                found["ballots"] = blank(row[1])
        out[ALIASES.get(name, name)] = found
    return out


def hst() -> dict[str, dict[str, int]]:
    """Each electoral district of the 2011 HST referendum from the Chief Electoral Officer's report."""
    text = layout(BC_2011_REPORT)
    out: dict[str, dict[str, int]] = {}
    pattern = re.compile(
        r"^\s{5,}([A-Z][A-Za-z.' -]+?)\s+([\d,]+)\s+[\d.]+%\s+([\d,]+)\s+[\d.]+%\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+%\s*$",
        re.MULTILINE,
    )
    for m in pattern.finditer(text):
        yes, no, valid, rejected, voted, registered = (number(m.group(i)) for i in range(2, 8))
        out[m.group(1).strip()] = {
            "first": yes,
            "second": no,
            "valid": valid,
            "invalid": rejected,
            "ballots": voted,
            "registered": registered,
        }
    return out


def tail(line: str, size: int) -> tuple[list[str], list[str]] | None:
    """The line's last `size` tokens, if each is a count, a percentage, or a dash, and the tokens before them."""
    tokens = line.split()
    if len(tokens) < size:
        return None
    last = tokens[-size:]
    if not all(re.fullmatch(r"[\d,]+|[\d.]+%|-", t) for t in last):
        return None
    return tokens[:-size], last


def reform_2018() -> dict[str, dict[str, int]]:
    """Each electoral district's counts on both questions of the 2018 referendum, and the provincial total under the
    name "Provincial total". The report prints the first round of the second question beside the first question and
    the second round on the facing page; the rows pair up in order."""
    out: dict[str, dict[str, int]] = {}
    names: list[str] = []
    paired = 0
    in_right = False
    for line in layout(BC_2018_REPORT).splitlines():
        if "Round 2 votes" in line:
            in_right = True
            paired = 0
            continue
        if "Round 1 votes" in line:
            in_right = False
            names = []
            continue
        if not in_right:
            found = tail(line, 10)
            if found is None or not found[0]:
                continue
            head, t = found
            if head[-1] == "Provincial" or head[-2:] == ["Provincial", "total"]:
                name = "Provincial total"
            elif re.fullmatch(r"[A-Z]{3}", head[-1]) and len(head) > 1:
                name = " ".join(head[:-1])
            else:
                continue
            names.append(name)
            out[name] = {
                "fptp": number(t[0]),
                "pr": number(t[2]),
                "valid1": number(t[4]),
                "invalid1": number(t[5]),
                "dmp1": number(t[6]),
                "mmp1": number(t[8]),
            }
        else:
            found = tail(line, 12)
            if found is None or found[0]:
                continue
            t = found[1]
            name = names[paired]
            paired += 1
            out[name].update(
                rup1=number(t[0]),
                valid2=number(t[2]),
                invalid2=number(t[3]),
                dmp2=number(t[4]),
                mmp2=number(t[6]),
                continuing=number(t[10]),
                exhausted=number(t[11]),
            )
    return out


PEI_DISTRICTS = (
    "Souris-Elmira", "Morell-Fortune Bay", "Georgetown-Baldwin's Road", "Montague-Kilmuir", "Murray River-Gaspereaux",
    "Belfast-Pownal Bay", "Glen Stewart-Bellevue Cove", "Tracadie-Fort Augustus", "Stanhope-East Royalty",
    "Sherwood-Hillsborough", "Parkdale-Belvedere", "Charlottetown-Kings Square", "Charlottetown-Rochford Square",
    "Charlottetown-Spring Park", "Winsloe-West Royalty", "North River-Rice Point", "Crapaud-Hazel Grove",
    "Park Corner-Oyster Bed", "Borden-Kinkora", "Kensington-Malpeque", "Wilmot-Summerside", "St. Eleanors-Summerside",
    "Cascumpec-Grand River", "Evangeline-Miscouche", "West Point-Bloomfield", "Alberton-Miminegash", "Tignish-DeBlois",
)  # fmt: skip
# The 2005 plebiscite's result for each district, as the Chief Electoral Officer's table prints it (scanned page 6 of
# his report): No, Yes, and rejected ballots, read from the page image and checked against its printed totals.
PEI_2005_ROWS = (
    (785, 194, 3), (899, 262, 2), (769, 210, 2), (653, 289, 2), (651, 208, 4), (786, 384, 4), (1098, 870, 2),
    (785, 388, 2), (825, 608, 2), (706, 549, 1), (833, 751, 4), (415, 542, 12), (510, 582, 5), (884, 708, 9),
    (940, 705, 6), (918, 629, 3), (899, 636, 5), (926, 672, 6), (714, 359, 0), (885, 510, 3), (630, 490, 5),
    (550, 360, 1), (725, 199, 2), (484, 316, 6), (821, 108, 2), (680, 125, 2), (744, 96, 1),
)  # fmt: skip
PEI_2005_TOTAL = (20515, 11750, 96)
PEI_OPTIONS = {
    "First-Past-The-Post (the current system)": "fptp",
    "Mixed Member Proportional Representation": "mmp",
    "Dual Member Proportional Representation": "dmp",
    "Preferential Voting": "pref",
    "First-Past-The-Post Plus Leaders": "fptp_leaders",
    "Exhausted votes": "exhausted",
}


@dataclass
class District:
    registered: int = 0
    valid: int = 0
    # count round -> option -> votes, and for each round whether any option's line carried a transfer.
    rounds: dict[int, dict[str, int]] = field(default_factory=dict)
    transfers: dict[int, bool] = field(default_factory=dict)


def pei_2016() -> dict[int, District]:
    """Each district's registered voters, valid votes, and every round of the count, from Appendix I of the interim
    report on the 2016 plebiscite."""
    districts: dict[int, District] = {}
    current: District | None = None
    count_round = 0
    for line in layout(PEI_2016).splitlines():
        if re.match(r"\s*APPENDIX II\s*$", line):
            break
        m = re.search(r"RESULTS FOR DISTRICT (\d+)", line)
        if m:
            current = districts[int(m.group(1))] = District()
            count_round = 0
            continue
        if current is None:
            continue
        m = re.search(r"Registered voters:\s*(\d+)\s+Valid votes cast:\s*(\d+)", line, re.IGNORECASE)
        if m:
            current.registered, current.valid = int(m.group(1)), int(m.group(2))
            continue
        m = re.match(r"\s*ROUND\s*(\d+)\s*$", line)
        if m:
            count_round = int(m.group(1))
            current.rounds[count_round] = {}
            current.transfers[count_round] = False
            continue
        m = re.match(r"\s*(.+?)\s{2,}(\d+)(\s+\(\+\d+\))?\s+votes\s*$", line)
        if m and count_round and m.group(1).strip() in PEI_OPTIONS:
            current.rounds[count_round][PEI_OPTIONS[m.group(1).strip()]] = int(m.group(2))
            current.transfers[count_round] |= bool(m.group(3))
    for d in districts.values():
        # The page repeats the last round as a summary with no transfers: drop it.
        for k in [k for k in d.rounds if k > 1 and not d.transfers[k]]:
            del d.rounds[k]
    return districts


def area_ids() -> dict[tuple[str, str], str]:
    return {
        (key, name): area for key, name, area in (line.split("\t") for line in AREAS.read_text().splitlines() if line)
    }


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


@dataclass
class Figures:
    """One unit's figures for one question: the turnout measures and each option's votes by count round."""

    registered: int | None
    ballots: int | None
    valid: int | None
    invalid: int | None
    options: dict[str, dict[int, int]]


def vote_counts(
    registered: int | None,
    ballots: int | None,
    valid: int | None,
    invalid: int | None,
    **options: dict[int, int],
) -> Figures:
    return Figures(registered, ballots, valid, invalid, options)


class Question:
    def __init__(
        self,
        label: str,
        text: str | None,
        options: list[tuple[str, str]],
        winner: str,
        lost: tuple[str, ...] = (),
        rules: dict[str, object] | None = None,
        kind: str = "referendum",
    ) -> None:
        self.label, self.text, self.options, self.winner, self.lost = label, text, options, winner, lost
        self.rules = rules or {"formula": "referendum"}
        self.kind = kind


class Vote:
    def __init__(
        self,
        slug: str,
        province: str,
        code: str,
        day: date,
        name: str,
        native: tuple[str, str] | None,
        questions: list[Question],
        sources: list[tuple[str, str, str, str]],
        files: list[str],
        note: str,
    ) -> None:
        self.slug, self.province, self.code, self.day, self.name, self.native = slug, province, code, day, name, native
        self.questions, self.sources, self.files, self.note = questions, sources, files, note
        # question index -> unit name -> figures; unit "" is the whole province.
        self.values: list[dict[str, Figures]] = [{} for _ in questions]
        self.districts: list[str] = []
        self.located = True
        self.start = day


QC_SOURCE = (
    "Élections Québec",
    "Results and statistics: Quebec referendums",
    QC_PAGE,
    "©Chief Electoral Officer of Québec; results published for public information",
)
BC_SOURCE = (
    "Elections BC",
    "Reports of the Chief Electoral Officer and voting-area results",
    "https://elections.bc.ca/resources/reports/",
    "Elections BC Open Data Licence",
)
PEI_SOURCE = (
    "Elections PEI",
    "Reports of the Chief Electoral Officer",
    "https://www.electionspei.ca/resources/ceo-election-reports",
    UNLICENSED,
)
QC_1980_TEXT = (
    "The Government of Québec has made public its proposal to negotiate a new agreement with the rest of Canada, "
    "based on the equality of nations; this agreement would enable Québec to acquire the exclusive power to make its "
    "laws, levy its taxes and establish relations abroad – in other words, sovereignty – and at the same time, to "
    "maintain with Canada an economic association including a common currency; no change in political status "
    "resulting from these negotiations will be effected without approval by the people through another referendum; "
    "on these terms, do you give the Government of Québec the mandate to negotiate the proposed agreement between "
    "Québec and Canada?"
)
QC_1995_TEXT = (
    "Do you agree that Québec should become sovereign, after having formally offered Canada a new economic and "
    "political partnership under the bill respecting the future of Québec and the agreement signed on June 12, 1995?"
)
YES_NO = [("yes", "Yes"), ("no", "No")]


def quebec_vote(day: date, url: str, text: str, fr: str, en: str, summary: str) -> Vote:
    total, divisions = quebec(url)
    question = Question("Referendum question", text, YES_NO, "no")
    vote = Vote(
        f"qc-{day.year}",
        QUEBEC,
        "qc",
        day,
        en,
        (fr, "fr"),
        [question],
        [QC_SOURCE],
        [url],
        "Counts by electoral division as Élections Québec prints them on its results page for the referendum: Yes and "
        "No votes, valid and rejected ballots, votes cast, and registered electors. Each division's Yes and No add "
        "to its valid ballots, valid and rejected ballots add to its votes cast, and the divisions add to the "
        f"province's printed totals. {summary} The authority publishes no result by polling station in the files the "
        "archive could reach.",
    )
    for name, v in divisions.items():
        vote.values[0][name] = vote_counts(
            v["registered"], v["ballots"], v["valid"], v["invalid"], yes={0: v["yes"]}, no={0: v["no"]}
        )
    vote.values[0][""] = vote_counts(
        total["registered"],
        total["ballots"],
        total["valid"],
        total["invalid"],
        yes={0: total["yes"]},
        no={0: total["no"]},
    )
    vote.districts = sorted(divisions)
    return vote


def sums(rows: dict[str, dict[str, int]], keys: tuple[str, ...]) -> dict[str, int]:
    return {k: sum(v.get(k, 0) for v in rows.values()) for k in keys}


def bc_workbook_vote(
    slug: str,
    day: date,
    url: str,
    report: str,
    name: str,
    text: str | None,
    options: list[tuple[str, str]],
    rules: dict[str, object],
    lost: tuple[str, ...],
    winner: str,
    note: str,
) -> Vote:
    rows = british_columbia(url)
    question = Question("Referendum question", text, options, winner, lost, rules)
    vote = Vote(slug, BRITISH_COLUMBIA, "bc", day, name, None, [question], [BC_SOURCE], [url, report], note)
    first, second = options[0][0], options[1][0]
    for district, v in rows.items():
        vote.values[0][district] = vote_counts(
            v["registered"],
            v["ballots"],
            v["valid"],
            v["invalid"],
            **{first: {0: v["first"]}, second: {0: v["second"]}},
        )
    t = sums(rows, ("first", "second", "valid", "invalid", "registered", "ballots"))
    vote.values[0][""] = vote_counts(
        t["registered"], t["ballots"], t["valid"], t["invalid"], **{first: {0: t["first"]}, second: {0: t["second"]}}
    )
    vote.districts = sorted(rows)
    return vote


def hst_vote() -> Vote:
    rows = hst()
    total = rows.pop("Totals")
    question = Question(
        "Referendum question",
        "Are you in favour of extinguishing the HST (Harmonized Sales Tax) and reinstating the PST (Provincial Sales "
        "Tax) in conjunction with the GST (Goods and Services Tax)?",
        YES_NO,
        "yes",
    )
    vote = Vote(
        "bc-2011",
        BRITISH_COLUMBIA,
        "bc",
        date(2011, 8, 26),
        "2011 British Columbia HST referendum",
        None,
        [question],
        [BC_SOURCE],
        [BC_2011_REPORT],
        "A vote by mail held from June 13 to August 26, 2011. Counts by electoral district as the Chief Electoral "
        "Officer's report prints them: Yes and No votes, valid votes, rejected ballots, ballots considered, and the "
        "voters registered. The districts add to the provincial totals the report prints. The date is the day the "
        "voting packages were due; the vote ran over the preceding ten weeks.",
    )
    for district, v in [*rows.items(), ("", total)]:
        vote.values[0][district] = vote_counts(
            v["registered"], v["ballots"], v["valid"], v["invalid"], yes={0: v["first"]}, no={0: v["second"]}
        )
    vote.districts = sorted(rows)
    vote.start = date(2011, 6, 13)
    return vote


def reform_vote() -> Vote:
    rows = reform_2018()
    total = rows.pop("Provincial total")
    q1 = Question(
        "Question 1",
        "Which system should British Columbia use for provincial elections?",
        [
            ("fptp", "The current First Past the Post voting system"),
            ("pr", "A proportional representation voting system"),
        ],
        "fptp",
    )
    q2 = Question(
        "Question 2",
        "If British Columbia adopts a proportional representation voting system, which of the following voting systems do you prefer?",
        [
            ("dmp", "Dual Member Proportional (DMP)"),
            ("mmp", "Mixed Member Proportional (MMP)"),
            ("rup", "Rural-Urban Proportional (RUP)"),
        ],
        "mmp",
    )
    vote = Vote(
        "bc-2018",
        BRITISH_COLUMBIA,
        "bc",
        date(2018, 11, 30),
        "2018 British Columbia referendum on electoral reform",
        None,
        [q1, q2],
        [BC_SOURCE],
        [BC_2018_REPORT],
        "A vote by mail held from October 22 to November 30, 2018, counted on December 2018. Counts by electoral "
        "district as the Chief Electoral Officer's report prints them. Question 1 is a single choice. Question 2 "
        "ranks the three proportional systems: first preferences are count round 1, and the second round, after the "
        "elimination of Rural-Urban Proportional, is count round 2, with the ballots exhausted after round one. "
        "Invalid ballots are those that gave no valid marking on that question; the districts' figures leave out the "
        "2,461 ballots rejected as a whole, which the report prints only for the province.",
    )
    for district, v in [*rows.items(), ("", total)]:
        vote.values[0][district] = vote_counts(
            None, None, v["valid1"], v["invalid1"], fptp={0: v["fptp"]}, pr={0: v["pr"]}
        )
        vote.values[1][district] = vote_counts(
            None, None, v["valid2"], v["invalid2"],
            dmp={1: v["dmp1"], 2: v["dmp2"]}, mmp={1: v["mmp1"], 2: v["mmp2"]}, rup={1: v["rup1"]},
            exhausted={2: v["exhausted"]},
        )  # fmt: skip
    vote.districts = sorted(rows)
    vote.start = date(2018, 10, 22)
    return vote


PEI_TEXT_2005 = (
    "Should Prince Edward Island change to the Mixed Member Proportional system as presented by the Commission on "
    "PEI's Electoral Future?"
)


def pei_2005_vote() -> Vote:
    if tuple(sum(r[i] for r in PEI_2005_ROWS) for i in range(3)) != PEI_2005_TOTAL:
        raise ValueError("the transcribed 2005 plebiscite districts do not add to the printed totals")
    question = Question(
        "Plebiscite question",
        PEI_TEXT_2005,
        [("no", "No"), ("yes", "Yes")],
        "no",
        ("yes",),
        {"formula": "referendum", "majority": {"share": 0.6}},
        "plebiscite",
    )
    vote = Vote(
        "pe-2005",
        PEI,
        "pe",
        date(2005, 11, 28),
        "2005 Prince Edward Island plebiscite on the mixed member proportional system",
        None,
        [question],
        [PEI_SOURCE],
        [PEI_2005],
        "No and Yes votes and rejected ballots by electoral district, as the table on page 6 of the Chief Electoral "
        "Officer's report prints them. The report is a scan; the figures were read from the page image and the "
        "districts add to the table's printed totals (20,515 No, 11,750 Yes, 96 rejected). The plebiscite needed at "
        "least 60% of valid votes province-wide and a majority in at least 60% of the 27 districts (the Plebiscites "
        "Act regulations); Yes won 36.4%. The report also prints each polling place's count, which this build does "
        "not hold.",
    )
    vote.located = False
    for district, (no, yes, rejected) in zip(PEI_DISTRICTS, PEI_2005_ROWS, strict=True):
        vote.values[0][district] = vote_counts(None, no + yes + rejected, no + yes, rejected, no={0: no}, yes={0: yes})
    n, y, r = PEI_2005_TOTAL
    vote.values[0][""] = vote_counts(None, n + y + r, n + y, r, no={0: n}, yes={0: y})
    vote.districts = list(PEI_DISTRICTS)
    return vote


PEI_2016_PROVINCE = {
    1: {"fptp": 11567, "mmp": 10757, "dmp": 7951, "pref": 3944, "fptp_leaders": 2821},
    2: {"fptp": 13108, "mmp": 11153, "dmp": 8224, "pref": 4216, "exhausted": 339},
    3: {"fptp": 14466, "mmp": 12780, "dmp": 8948, "exhausted": 846},
    4: {"mmp": 19418, "fptp": 15869, "exhausted": 1753},
}
PEI_2016_OPTIONS = [
    ("fptp", "First-Past-The-Post (the current system)"),
    ("mmp", "Mixed Member Proportional Representation"),
    ("dmp", "Dual Member Proportional Representation"),
    ("pref", "Preferential Voting"),
    ("fptp_leaders", "First-Past-The-Post Plus Leaders"),
]


def pei_2016_vote() -> Vote:
    rows = pei_2016()
    question = Question("Plebiscite question", None, PEI_2016_OPTIONS, "mmp", (), None, "plebiscite")
    vote = Vote(
        "pe-2016",
        PEI,
        "pe",
        date(2016, 11, 7),
        "2016 Prince Edward Island plebiscite on democratic renewal",
        None,
        [question],
        [PEI_SOURCE],
        [PEI_2016],
        "A preferential ballot among five electoral systems, cast by internet, telephone, or paper from October 29 to "
        "November 7, 2016. Each district's count is the report's Appendix I: first preferences are count round 1, "
        "and each later round is the running total after the lowest option was eliminated and its ballots "
        "transferred, so a district stops at the round where an option held a majority and the rounds differ from "
        "district to district. The provincial unit holds the rounds the report prints for the whole province. The "
        "report's provincial turnout line says 37,354 voters while its ballot-ranking table and the districts add "
        "to 37,040 valid ballots, with 317 rejected; the valid and rejected counts are stored and the ballots cast "
        "are not. Preferences by age are not stored.",
    )
    vote.located = False
    for number_, v in rows.items():
        name = PEI_DISTRICTS[number_ - 1]
        figures: dict[str, dict[int, int]] = {}
        for r, counts in v.rounds.items():
            for option, n in counts.items():
                figures.setdefault(option, {})[r] = n
        vote.values[0][name] = vote_counts(v.registered, None, v.valid, None, **figures)
    figures = {}
    for r, counts in PEI_2016_PROVINCE.items():
        for option, n in counts.items():
            figures.setdefault(option, {})[r] = n
    vote.values[0][""] = vote_counts(102464, None, 37040, 317, **figures)
    vote.districts = list(PEI_DISTRICTS)
    vote.start = date(2016, 10, 29)
    return vote


BC_2005_RULES: dict[str, object] = {"formula": "referendum", "majority": {"share": 0.6}}


def votes() -> list[Vote]:
    return [
        quebec_vote(
            date(1980, 5, 20),
            QC_1980,
            QC_1980_TEXT,
            "Référendum de 1980 sur la souveraineté-association",
            "1980 Québec referendum on sovereignty-association",
            "The No option won 59.56% of valid ballots.",
        ),
        quebec_vote(
            date(1995, 10, 30),
            QC_1995,
            QC_1995_TEXT,
            "Référendum de 1995 sur la souveraineté du Québec",
            "1995 Québec referendum on sovereignty",
            "The No option won 50.58% of valid ballots.",
        ),
        bc_workbook_vote(
            "bc-2005",
            date(2005, 5, 17),
            BC_2005_XLS,
            BC_2005_REPORT,
            "2005 British Columbia referendum on electoral reform",
            "Should British Columbia change to the BC-STV electoral system as recommended by the Citizens' Assembly on "
            "Electoral Reform?",
            YES_NO,
            BC_2005_RULES,
            ("yes",),
            "no",
            "Counts by electoral district from the Chief Electoral Officer's workbook of results by voting area: the "
            "grand total of each district's sheet. The districts add to the provincial figures the report prints "
            "(982,364 Yes, 720,368 No, 46,607 rejected, 2,845,284 registered). The proposal needed 60% of valid votes "
            "province-wide and a majority in at least 48 of the 79 districts; Yes won 57.69% and 77 districts. The "
            "workbook also holds each voting area's count, which this build does not.",
        ),
        bc_workbook_vote(
            "bc-2009",
            date(2009, 5, 12),
            BC_2009_XLS,
            BC_2009_REPORT,
            "2009 British Columbia referendum on electoral reform",
            "Should British Columbia change to the BC-STV electoral system?",
            [("fptp", "First Past the Post"), ("stv", "BC-STV")],
            BC_2005_RULES,
            ("stv",),
            "fptp",
            "Counts by electoral district from the Chief Electoral Officer's workbook of results by voting area: the "
            "grand total of each district's sheet, held on the day of the general election. The districts add to the "
            "provincial figures the Statement of Votes prints (623,420 BC-STV of 1,594,770 valid votes). BC-STV "
            "needed 60% of valid votes province-wide and a majority in at least 51 of the 85 districts and met "
            "neither. The workbook also holds each voting area's count, which this build does not.",
        ),
        hst_vote(),
        reform_vote(),
        pei_2005_vote(),
        pei_2016_vote(),
    ]


def packages() -> list[str]:
    return [
        f"{SOURCE}/{slug}"
        for slug in ("qc-1980", "qc-1995", "bc-2005", "bc-2009", "bc-2011", "bc-2018", "pe-2005", "pe-2016")
    ]


def fetch() -> None:
    for url, suffix in FILES.items():
        raw.fetch(SOURCE, url, suffix=suffix)


def parse() -> None:
    for vote in votes():
        build(vote).write()


def emit() -> None:
    package.run("load", packages())


def build(vote: Vote) -> Package:
    areas = area_ids()
    p = Package(f"{SOURCE}/{vote.slug}", SOURCE, f"{vote.name}, by electoral district")
    p.external.add(vote.province)
    province_name = {"qc": "Quebec", "bc": "British Columbia", "pe": "Prince Edward Island"}[vote.code]
    province_area = areas[("province", province_name)]
    p.external.add(province_area)
    source_ids = []
    for publisher, title, url, license_ in vote.sources:
        source = key("source", vote.slug, publisher)
        source_ids.append(source)
        p.add(
            "source",
            id=source,
            kind="official_body",
            publisher=publisher,
            title=title,
            url=url,
            license=license_,
            attribution=publisher,
        )
    for url in vote.files:
        p.artifact(raw.cached(SOURCE, url), source_ids[0])
    source = source_ids[0]

    election = p.id("election", vote.slug)
    p.add(
        "election",
        id=election,
        polity_id=vote.province,
        kind="referendum",
        date_from=vote.start,
        date_to=vote.day,
        date_precision="day",
        status="held",
    )
    p.name(election, vote.name, "en", "official")
    if vote.native:
        p.name(election, vote.native[0], vote.native[1], "native", False)
    base = f"ca/{vote.code}/{vote.day}-referendum"
    p.add("slug", path=base, entity_id=election, is_current=True)

    scheme_province = "ca.referendum_province"
    scheme_district = f"ca.{vote.code}_referendum_district"
    p.add(
        "scheme",
        code=scheme_province,
        polity_id=vote.province,
        kind="administrative",
        rank=1,
        noun="province",
        code_system=None,
    )
    p.add(
        "scheme",
        code=scheme_district,
        polity_id=vote.province,
        kind="electoral_district",
        rank=2,
        noun="electoral district",
        code_system=None,
    )
    province_unit = p.id("unit", vote.slug, "province")
    p.add(
        "reporting_unit",
        id=province_unit,
        polity_id=vote.province,
        scheme_code=scheme_province,
        code=f"{vote.slug}:province",
        area_relation="equals",
        area_id=province_area,
        kind="aggregate",
    )
    units = {"": province_unit}
    for district in vote.districts:
        unit = p.id("unit", vote.slug, district)
        units[district] = unit
        area = areas.get((vote.slug, district)) if vote.located else None
        if area:
            p.external.add(area)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=vote.province,
            scheme_code=scheme_district,
            code=f"{vote.slug}:{fold(district)}",
            parent_id=province_unit,
            area_relation="equals" if area else "unlocated",
            area_id=area,
            kind="standard",
        )

    added: set[str] = set()
    for index, question in enumerate(vote.questions):
        rules_key = str(sorted(question.rules.items()))
        system = key("electoral_system", vote.slug, rules_key)
        if system not in added:
            added.add(system)
            p.add("electoral_system", id=system, polity_id=vote.province, rules=question.rules, rules_version=1)
        contest = p.id("contest", vote.slug, index)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            electoral_system_id=system,
            round_number=1,
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type=question.kind)
        p.name(contest, question.label if len(vote.questions) > 1 else vote.name, "en", "official")
        p.add(
            "slug",
            path=f"{base}/question-{index + 1}" if len(vote.questions) > 1 else f"{base}/question",
            entity_id=contest,
            is_current=True,
        )
        if question.text:
            p.add(
                "ballot_question_text",
                id=p.id("question_text", vote.slug, index),
                contest_id=contest,
                kind="text",
                lang="en",
                body=question.text,
                url=vote.files[0],
                source_id=source,
            )
        options = {}
        for position, (option_key, label) in enumerate(question.options, 1):
            option = p.id("option", vote.slug, index, option_key)
            options[option_key] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        result_set = p.id("result_set", vote.slug, index)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=vote.note, lang="en"
        )
        p.add("outcome", result_set_id=result_set, option_id=options[question.winner], kind="won")
        for lost in question.lost:
            p.add("outcome", result_set_id=result_set, option_id=options[lost], kind="lost")
        rows: list[tuple[str, str | None, int, int, int]] = []
        for unit_name, figures in vote.values[index].items():
            unit = units[unit_name]
            for column, measure in (
                ("registered", REGISTERED),
                ("ballots", BALLOTS),
                ("valid", VALID),
                ("invalid", INVALID),
            ):
                value = getattr(figures, column)
                if value is not None:
                    rows.append((unit, None, measure, 0, value))
            for option_key, option in options.items():
                counts = figures.options.get(option_key)
                if counts:
                    for count_round, value in counts.items():
                        rows.append((unit, option, VOTES, count_round, value))
            exhausted = figures.options.get("exhausted")
            if exhausted:
                for count_round, value in exhausted.items():
                    rows.append((unit, None, EXHAUSTED, count_round, value))
        p.frame(
            "result_value",
            pl.DataFrame(
                rows, schema=["unit_id", "option_id", "measure", "count_round", "value"], orient="row"
            ).with_columns(
                result_set_id=pl.lit(result_set),
                channel=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.col("count_round").cast(pl.Int16),
            ),
        )
    return p
