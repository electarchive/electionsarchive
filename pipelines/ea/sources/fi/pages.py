"""The Ministry of Justice results service's HTML pages for the elections before its CSV files began (2003 to 2009).

Every page is windows-1252 HTML. An election's `s/tulos/lasktila.html` lists the pages: `tulos_kokomaa.html` for the
whole country, `vptulos_<constituency>.html` for each constituency, and `kutulos_<municipality>.html` for each
municipality, with `ehd`-prefixed twins that hold the votes of each candidate for the elections that have them. The
`s/aanaktiivisuus/aanestys1.htm` page holds the voters, the advance voters, and the election-day voters of every area.
"""

import html
import json
import re
import time
from dataclasses import dataclass
from datetime import date
from functools import cache
from html.parser import HTMLParser

import httpx

from ea import raw

SOURCE = "fi.vaalit"
BASE = "https://tulospalvelu.vaalit.fi"
STATFIN = "https://pxdata.stat.fi/PxWeb/api/v1/fi/StatFin_Passiivi"


@dataclass(frozen=True)
class Page:
    """One election's page folder on the results service."""

    folder: str
    day: date
    kind: str
    round: int = 1
    regional: bool = False
    table: str = ""


KVAA_2008 = "kvaa/680_kvaa_2008_2009-11-02_tau_144_fi.px"
PAGES = [
    Page("E2003", date(2003, 3, 16), "E", table="evaa/001_oi57_statfin_tulos1_2003_fi.px"),
    Page("EP2004", date(2004, 6, 13), "EP", table="euvaa/640_euvaa_2004_2009-04-28_tau_139_fi.px"),
    Page("K2004", date(2004, 10, 24), "K", table="kvaa/050_kvaa_2004_tau_111_fi.px"),
    Page("M2004", date(2004, 10, 24), "M", regional=True, table="kvaa/050_kvaa_2004_tau_111_fi.px"),
    Page("TP2006K1", date(2006, 1, 15), "P", table="pvaa/120_pvaa_tau_103_fi.px"),
    Page("TP2006K2", date(2006, 1, 29), "P", 2, table="pvaa/120_pvaa_tau_103_fi.px"),
    Page("E2007", date(2007, 3, 18), "E", table="evaa/001_oi57_statfin_tulos1_2007_fi.px"),
    Page("K2008", date(2008, 10, 26), "K", table=KVAA_2008),
    Page("M2008", date(2008, 10, 26), "M", regional=True, table=KVAA_2008),
    Page("EP2009", date(2009, 6, 7), "EP", table="euvaa/640_euvaa_2009_2014-05-26_tau_139.px"),
    Page("K2008U", date(2009, 9, 6), "K", table=KVAA_2008),
    Page("K2009LOV", date(2009, 10, 25), "K", table=KVAA_2008),
]
STATFIN_TABLES = {
    "evaa/001_oi57_statfin_tulos1_2003_fi.px": "E2003",
    "evaa/001_oi57_statfin_tulos1_2007_fi.px": "E2007",
    "pvaa/120_pvaa_tau_103_fi.px": "TP2006K1",
    "kvaa/050_kvaa_2004_tau_111_fi.px": "K2004",
    "kvaa/680_kvaa_2008_2009-11-02_tau_144_fi.px": "K2008",
    "euvaa/640_euvaa_2004_2009-04-28_tau_139_fi.px": "EP2004",
    "euvaa/640_euvaa_2009_2014-05-26_tau_139.px": "EP2009",
}


def candidate_tables(folder: str) -> list[str]:
    """Statistics Finland's tables that print each candidate's selection: the 2007 parliamentary election's candidate
    tables, one per constituency, and the 2008 municipal election's candidate tables, one per constituency but Åland."""
    if folder == "E2007":
        return [f"evaa/oi57_statfin_ehdok{n:02d}_2007_fi.px" for n in range(1, 16)]
    if folder == "K2008":
        return [
            f"kvaa/{n}_kvaa_2008_{'2012-10-12' if n == 330 else '2009-11-02'}_tau_{108 + (n - 210) // 10}_fi.px"
            for n in range(210, 360, 10)
            if n != 250
        ]
    return []


def candidate_query(folder: str, table: str) -> dict[str, object]:
    everything = {"filter": "all", "values": ["*"]}
    measures = {"filter": "item", "values": ["Sar1", "Sar3", "Sar5" if folder == "E2007" else "Val"]}
    query = [
        {"code": "Ehdokas", "selection": everything},
        {"code": "Äänestystiedot", "selection": measures},
    ]
    if folder == "E2007":
        # Only the constituency's own row, because the table is also cut by every voting district
        number = re.search(r"ehdok(\d\d)_", table)
        assert number is not None
        query.append({"code": "Alue", "selection": {"filter": "item", "values": [f"VP{number[1]}"]}})
    return {
        "query": query,
        "response": {"format": "json-stat2"},
    }


def page_url(page: Page, name: str) -> str:
    return f"{BASE}/{page.folder}/s/tulos/{name}"


def turnout_url(page: Page) -> str:
    return f"{BASE}/{page.folder}/s/aanaktiivisuus/aanestys1.htm"


def statfin_url(table: str) -> str:
    return f"{STATFIN}/{table}"


def text(artifact: raw.Artifact) -> str:
    return artifact.path.read_bytes().decode("cp1252")


def get(page: Page, name: str) -> str:
    return text(raw.cached(SOURCE, page_url(page, name)))


def links(content: str, prefix: str) -> list[str]:
    """The distinct page names a page links to, in order, that start with the prefix."""
    return list(dict.fromkeys(re.findall(rf'href="({prefix}[a-z0-9_]*\.html)"', content, re.IGNORECASE)))


def fetch_table(table: str, body: dict[str, object] | None) -> None:
    """One StatFin call, paced and retried, since the API refuses a client that sends too many in a short while."""
    for attempt in range(6):
        try:
            raw.fetch(SOURCE, statfin_url(table), suffix=".json", body=body)
            break
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 403 or attempt == 5:
                raise
        time.sleep(15 * (attempt + 1))
    time.sleep(1)


def fetch() -> None:
    for page in PAGES:
        raw.fetch(SOURCE, page_url(page, "lasktila.html"), suffix=".html")
        raw.fetch(SOURCE, turnout_url(page), suffix=".html")
    for table in STATFIN_TABLES:
        raw.fetch(SOURCE, statfin_url(table), suffix=".json")
    urls: list[str] = []
    for page in PAGES:
        index = get(page, "lasktila.html")
        names = [
            *links(index, "tulos_kokomaa"),
            *links(index, "vptulos_"),
            *links(index, "kutulos_"),
        ]
        urls += [page_url(page, name) for name in names]
    raw.fetch_many(SOURCE, urls, suffix=".html", workers=12)
    for page in PAGES:
        for table in candidate_tables(page.folder):
            fetch_table(table, None)
            fetch_table(table, candidate_query(page.folder, table))
    candidate: list[str] = []
    for page in PAGES:
        index = get(page, "lasktila.html")
        sample = next(iter(links(index, "kutulos_")), None)
        if sample is None:
            continue
        if links(get(page, sample), "ehdkutulos_"):
            candidate += [page_url(page, "ehd" + name) for name in links(index, "kutulos_")]
            candidate += [page_url(page, "ehd" + name) for name in links(index, "vptulos_")]
            candidate += [page_url(page, "ehdtulos_kokomaa.html")]
    found = raw.fetch_many(SOURCE, candidate, suffix=".html", workers=12)
    missing = [url for url in candidate if url not in found]
    print(f"{len(candidate) - len(missing)} candidate pages; {len(missing)} do not exist")


# ---------------------------------------------------------------------------------------------------------------------
# Parsing


class _Rows(HTMLParser):
    """Table rows of a page whose cells and rows are often left unclosed."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.cell: list[str] | None = None
        self.skip = 0

    def _cell(self) -> None:
        if self.cell is not None:
            self.row.append(" ".join("".join(self.cell).replace("\xa0", " ").split()))
        self.cell = None

    def _row(self) -> None:
        self._cell()
        if self.row:
            self.rows.append(self.row)
        self.row = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skip += 1
        elif tag == "tr":
            self._row()
        elif tag in ("td", "th"):
            self._cell()
            self.cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skip -= 1
        elif tag in ("tr", "table"):
            self._row()

    def handle_data(self, data: str) -> None:
        if self.cell is not None and not self.skip:
            self.cell.append(data)


def cells(content: str) -> list[list[str]]:
    """Each table row of a page as its cells' text, with tags dropped and whitespace collapsed."""
    parser = _Rows()
    parser.feed(content)
    parser.close()
    parser._row()
    return parser.rows


def title(content: str) -> str:
    """The area a result page is about, from its heading."""
    match = re.search(r"(?is)<div class=otsikko>(.*?)</div>", content)
    if match is None:
        raise ValueError("no heading")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).split())


def integer(value: str) -> int:
    cleaned = value.replace(" ", "").replace("\xa0", "")
    if not re.fullmatch(r"[+-]?\d+", cleaned):
        raise ValueError(f"not a count: {value!r}")
    return int(cleaned)


def is_count(value: str) -> bool:
    return re.fullmatch(r"\d[\d ]*", value) is not None


def percent(value: str) -> float | None:
    cleaned = value.replace(",", ".").replace(" ", "")
    return float(cleaned) if re.fullmatch(r"-?\d+(\.\d+)?", cleaned) else None


@dataclass(frozen=True)
class Row:
    """One party list, or one presidential candidate, on a result page."""

    name: str
    votes: int
    advance: int | None
    day: int | None
    seats: int | None


@dataclass(frozen=True)
class Result:
    """A party-level or presidential result page."""

    area: str
    rows: list[Row]
    valid: int | None
    invalid: int | None
    cast: int | None
    turnout: float | None
    domestic: float | None
    updated: str


def stamp(content: str) -> str:
    match = re.search(r"Sivu päivitetty\s+(\d+\.\d+\.\d{4})\s+Klo\s+(\d+:\d{2}:\d{2})", content)
    return f"{match.group(1)} {match.group(2)}" if match else ""


def _header(rows: list[list[str]]) -> tuple[float | None, float | None]:
    """The total and domestic turnout percentages that some pages print above the table."""
    for index, row in enumerate(rows[:-1]):
        if row and row[0].startswith("Kokonais") and len(rows[index + 1]) == 2:
            return percent(rows[index + 1][0]), percent(rows[index + 1][1])
    return None, None


def _total(rest: list[str]) -> int | None:
    """The all-ballots count of a totals row: the sixth cell of a presidential page, the fourth of a party page."""
    if len(rest) >= 5 and is_count(rest[0]) and is_count(rest[4]):
        return integer(rest[4])
    if len(rest) >= 4 and is_count(rest[3]):
        return integer(rest[3])
    return None


def parse_result(content: str) -> Result:
    """Read a result page: party lists with their votes and seats, or presidential candidates with their votes."""
    rows: list[Row] = []
    totals: list[int] = []
    invalid: int | None = None
    table = cells(content)
    for row in table:
        label, rest = row[0], row[1:]
        if label == "Mitättömät äänet":
            invalid = _total(rest)
            continue
        if label == "Yhteensä":
            count = _total(rest)
            if count is not None:
                totals.append(count)
            continue
        parsed = _row(label, rest)
        if parsed is not None:
            rows.append(parsed)
    turnout, domestic = _header(table)
    valid = totals[0] if totals else None
    cast = totals[1] if invalid is not None and len(totals) > 1 else None
    return Result(title(content), rows, valid, invalid, cast, turnout, domestic, stamp(content))


def _row(label: str, rest: list[str]) -> Row | None:
    if not label or label[0].isdigit() or len(rest) not in (6, 8):
        return None
    # A presidential candidate: advance count and share, election-day count and share, total count and share.
    if len(rest) == 6 and is_count(rest[0]) and is_count(rest[2]) and is_count(rest[4]):
        return Row(label, integer(rest[4]), integer(rest[0]), integer(rest[2]), None)
    # A party list: share, two changes in share, votes, two changes in votes, and for a district its seats and change.
    if percent(rest[0]) is not None and "," in rest[0] + "," and is_count(rest[3]):
        return Row(label, integer(rest[3]), None, None, integer(rest[6]) if len(rest) == 8 else None)
    return None


@dataclass(frozen=True)
class Candidate:
    number: int
    name: str
    party: str
    votes: int
    advance: int
    day: int


def parse_candidates(content: str) -> tuple[str, list[Candidate]]:
    """Read a candidate page: each candidate's name and advance, election-day, and total votes.

    Most pages also print the candidate's number and party abbreviation. The European Parliament page of 2009
    prints neither, and its candidates are numbered by their place on the page."""
    found: list[Candidate] = []
    for row in cells(content):
        if len(row) == 9 and row[0].isdigit() and is_count(row[3]) and is_count(row[5]) and is_count(row[7]):
            found.append(Candidate(int(row[0]), row[1], row[2], integer(row[7]), integer(row[3]), integer(row[5])))
        elif (
            len(row) == 7
            and row[0]
            and row[0] not in ("Yhteensä", "Mitättömät äänet")
            and not is_count(row[0])
            and is_count(row[1])
            and is_count(row[3])
            and is_count(row[5])
            and all(percent(row[i]) is not None for i in (2, 4, 6))
        ):
            found.append(Candidate(len(found) + 1, row[0], "", integer(row[5]), integer(row[1]), integer(row[3])))
    return title(content), found


@dataclass(frozen=True)
class Turnout:
    area: str
    registered: int
    advance: int
    day: int
    cast: int


def parse_turnout(content: str) -> list[Turnout]:
    """Read the turnout page: for each area its voters, advance voters, election-day voters, and total voters."""
    out: list[Turnout] = []
    for row in cells(content):
        counts = [value for value in row[1:] if is_count(value)]
        if len(row) == 8 and len(counts) >= 4 and not row[0][:1].isdigit() and is_count(row[1]):
            out.append(Turnout(row[0], integer(row[1]), integer(row[2]), integer(row[4]), integer(row[6])))
    return out


@cache
def statfin(table: str) -> dict[str, str]:
    """The municipality codes and names a Statistics Finland table lists, as {name: code}."""
    artifact = raw.cached(SOURCE, statfin_url(table))
    meta = json.loads(artifact.path.read_text())
    area = meta["variables"][0]
    out: dict[str, str] = {}
    for value, label in zip(area["values"], area["valueTexts"], strict=True):
        match = re.fullmatch(r"(\d{3})\s+(\D.*)", label)
        if match and not re.fullmatch(r"\d{3} \d{3}\w", label):
            out[" ".join(match.group(2).split())] = match.group(1)
    return out


CONSTITUENCIES = {
    "Helsingin vaalipiiri": "HEL",
    "Uudenmaan vaalipiiri": "UUS",
    "Varsinais-Suomen vaalipiiri": "VAR",
    "Satakunnan vaalipiiri": "SAT",
    "Ahvenanmaan maakunnan vaalipiiri": "AHV",
    "Hämeen vaalipiiri": "HÄM",
    "Pirkanmaan vaalipiiri": "PIR",
    "Kymen vaalipiiri": "KYM",
    "Etelä-Savon vaalipiiri": "ESA",
    "Pohjois-Savon vaalipiiri": "PSA",
    "Pohjois-Karjalan vaalipiiri": "KAR",
    "Vaasan vaalipiiri": "VAA",
    "Keski-Suomen vaalipiiri": "KES",
    "Oulun vaalipiiri": "OUL",
    "Lapin vaalipiiri": "LAP",
}
KAINUU = ("18", "Kainuu")
REPEATED = "K2009U"


def clean_title(name: str) -> str:
    return name.removesuffix(" Ehdokaskohtaiset tulokset").strip()


def repeated(result: Result) -> bool:
    """A page of the 2008 municipal elections that holds the repeat election's results in three municipalities."""
    return REPEATED in result.area


def municipality(page: Page, name: str) -> str:
    """The municipality number of a name as Statistics Finland's table for the election lists it."""
    for table in dict.fromkeys([page.table, KVAA_2008, *STATFIN_TABLES]):
        code = statfin(table).get(name)
        if code:
            return code
    raise LookupError(f"{page.folder}: no municipality number for {name!r}")


@dataclass
class Unit:
    """A municipality's result pages."""

    slug: str
    name: str
    code: str
    result: Result
    candidates: list[Candidate] | None
    turnout: Turnout | None


@dataclass
class Group:
    """A constituency (or the region of a regional election) with the municipalities it holds."""

    slug: str | None
    name: str
    result: Result | None
    candidates: list[Candidate] | None
    turnout: Turnout | None
    units: list[Unit]


@dataclass(frozen=True)
class Selection:
    """A candidate's number, votes, and selection as Statistics Finland's candidate table prints them: 1 for elected, 2
    for deputy, and 3 for neither."""

    number: int
    votes: int
    status: int
    name: str


def selections(folder: str) -> dict[tuple[str, int], Selection]:
    """Each candidate's selection by (constituency name or municipality number, candidate number)."""
    found: dict[tuple[str, int], Selection] = {}
    for table in candidate_tables(folder):
        meta = json.loads(raw.cached(SOURCE, statfin_url(table)).path.read_bytes())
        data = json.loads(raw.cached(SOURCE, statfin_url(table), candidate_query(folder, table)).path.read_bytes())
        constituency = meta["title"].rpartition(", ")[2]
        labels = data["dimension"]["Ehdokas"]["category"]["label"]
        candidates = list(data["dimension"]["Ehdokas"]["category"]["index"])
        measures = list(data["dimension"]["Äänestystiedot"]["category"]["index"])
        wide = len(measures)
        values = data["value"]
        if len(values) != len(candidates) * wide:
            raise ValueError(f"{table}: {len(values)} values for {len(candidates)} candidates")
        for i, code in enumerate(candidates):
            row = dict(zip(measures, values[i * wide : (i + 1) * wide], strict=True))
            status = row.get("Sar5", row.get("Val"))
            if status not in (1, 2, 3):
                raise ValueError(f"{table}: candidate {code} has selection {status!r}")
            area = constituency if folder == "E2007" else code[2:5]
            key = (area, row["Sar1"])
            if key in found:
                raise ValueError(f"{table}: candidate {key} appears twice")
            found[key] = Selection(row["Sar1"], row["Sar3"], status, labels[code].partition(" / ")[0])
    return found


@dataclass
class Election:
    page: Page
    national: Result | None
    candidates: list[Candidate] | None
    turnout: Turnout | None
    groups: list[Group]
    artifacts: list[raw.Artifact]


def _optional(page: Page, name: str) -> str | None:
    try:
        return get(page, name)
    except LookupError:
        return None


@cache
def load(folder: str) -> Election:
    """Everything an election's pages say, with the municipalities grouped by constituency."""
    page = next(p for p in PAGES if p.folder == folder)
    cited = [raw.cached(SOURCE, page_url(page, "lasktila.html")), raw.cached(SOURCE, turnout_url(page))]
    index = text(cited[0])
    turnout = {t.area: t for t in parse_turnout(text(cited[1]))}

    def fetch_page(name: str) -> str | None:
        try:
            artifact = raw.cached(SOURCE, page_url(page, name))
        except LookupError:
            return None
        cited.append(artifact)
        return text(artifact)

    def candidates(name: str) -> list[Candidate] | None:
        content = fetch_page("ehd" + name)
        return None if content is None else parse_candidates(content)[1]

    national_page = fetch_page("tulos_kokomaa.html")
    national = None if national_page is None else parse_result(national_page)
    groups: list[Group] = []
    current: Group | None = None
    for name in re.findall(r'href="((?:vptulos|kutulos)_[a-z0-9_]*\.html)"', index, re.IGNORECASE):
        content = fetch_page(name)
        if content is None:
            raise LookupError(f"{page.folder}: {name} is not cached")
        result = parse_result(content)
        if name.startswith("vptulos"):
            current = Group(
                name,
                clean_title(result.area),
                result,
                candidates(name),
                turnout.get(clean_title(result.area)),
                [],
            )
            groups.append(current)
            continue
        if repeated(result):
            continue
        if current is None:
            current = Group(None, "", None, None, None, [])
            groups.append(current)
        area = clean_title(result.area)
        current.units.append(Unit(name, area, municipality(page, area), result, candidates(name), turnout.get(area)))
    for table in candidate_tables(page.folder):
        cited += [
            raw.cached(SOURCE, statfin_url(table)),
            raw.cached(SOURCE, statfin_url(table), candidate_query(folder, table)),
        ]
    return Election(
        page,
        national,
        candidates("tulos_kokomaa.html"),
        turnout.get("Koko maa"),
        groups,
        cited,
    )
