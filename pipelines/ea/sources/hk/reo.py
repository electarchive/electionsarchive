"""Hong Kong elections published by the Registration and Electoral Office.

Legislative Council general elections (geographical constituencies by counting station, functional and Election
Committee constituencies as one total each), District Council ordinary elections (constituencies by counting
station, district committee constituencies as one total each), and Chief Executive elections.
"""

import json
import re
import string
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from uuid import uuid5

import fastexcel

from ea import package, raw
from ea.package import Package

SOURCE = "hk.reo"
HOST = "https://www.elections.gov.hk"
HONG_KONG = str(uuid5(package.NAMESPACE, "polity\x1fHK"))
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
PAGE = f"{HOST}/index.html"

LEGCO_FC = {
    2021: [
        "a",
        "af",
        "b",
        "c",
        "ca",
        "de",
        "f",
        "g",
        "h",
        "hyk",
        "in",
        "k",
        "l",
        "m",
        "n",
        "p",
        "q",
        "r",
        "s",
        "t",
        "tr",
        "u",
        "v",
        "w",
        "x",
        "y",
        "z",
        "zn",
    ],
    2025: [
        "acc",
        "af",
        "aspl",
        "cat",
        "chn",
        "com1",
        "com2",
        "com3",
        "edu",
        "eng",
        "fin",
        "fs",
        "hyk",
        "ie",
        "ind1",
        "ind2",
        "insu",
        "labr",
        "leg",
        "mhs",
        "rec",
        "spcp",
        "sw",
        "tg",
        "ti",
        "tour",
        "tran",
        "wr",
    ],
}
DISTRICTS_2019 = [
    "central_western",
    "wan_chai",
    "eastern",
    "southern",
    "yau_tsim_mong",
    "sham_shui_po",
    "kowloon_city",
    "wong_tai_sin",
    "kwun_tong",
    "tsuen_wan",
    "tuen_mun",
    "yuen_long",
    "north",
    "tai_po",
    "sai_kung",
    "sha_tin",
    "kwai_tsing",
    "islands",
]
LETTERS_2019 = ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "Q", "R", "S", "T"]
DISTRICT_LETTERS_2023 = ["a", "b", "c", "d", "e", "f", "g", "h", "j", "k", "l", "m", "n", "p", "q", "r", "s", "t"]
GC_COUNT = 10


def _get(url: str, suffix: str = ".html") -> raw.Artifact:
    return raw.fetch(SOURCE, url, suffix=suffix)


def _own(url: str) -> raw.Artifact:
    return raw.cached(SOURCE, url)


def legco_urls(year: int) -> dict[str, list[str]]:
    base = f"{HOST}/legco{year}"
    if year == 2021:
        gc = [f"{base}/eng/results_lc{n}.html" for n in range(1, GC_COUNT + 1)]
        ps = [f"{base}/eng/results_lc{n}_ps.html" for n in range(1, GC_COUNT + 1)]
        js = [f"{base}/js/LC{n}votecount.js" for n in range(1, GC_COUNT + 1)]
        ecc = [f"{base}/eng/rs_ecc.html"]
        fc = [f"{base}/eng/rs_fc_{code}.html" for code in LEGCO_FC[year]]
    else:
        gc = [f"{base}/eng/results_gc_lc{n}.html" for n in range(1, GC_COUNT + 1)]
        ps = [f"{base}/eng/results_gc_lc{n}_ps.html" for n in range(1, GC_COUNT + 1)]
        js = [f"{base}/js/election.results/lc{n}_vote_count.js" for n in range(1, GC_COUNT + 1)]
        ecc = [f"{base}/eng/results_ecc.html"]
        fc = [f"{base}/eng/results_fc_{code}.html" for code in LEGCO_FC[year]]
    return {"gc": gc, "ps": ps, "js": js, "ecc": ecc, "fc": fc}


def dc2023_codes() -> list[str]:
    page = _get(f"{HOST}/dc2023/eng/rs_dcgc.html")
    html = page.path.read_text(encoding="utf-8-sig")
    return sorted(set(re.findall(r"rs_dcgc_([a-z]\d+)\.html", html)))


def dc2023_urls() -> dict[str, list[str]]:
    base = f"{HOST}/dc2023"
    codes = dc2023_codes()
    return {
        "gc": [f"{base}/eng/rs_dcgc_{code}.html" for code in codes],
        "ps": [f"{base}/eng/rs_dcgc_{code}_ps.html" for code in codes],
        "js": [f"{base}/js/votecount/{code.upper()}votecount.js" for code in codes],
        "dcc": [f"{base}/eng/rs_dcc_{letter}.html" for letter in DISTRICT_LETTERS_2023],
    }


def dc2019_urls() -> dict[str, list[str]]:
    base = f"{HOST}/dc2019"
    return {
        "district": [f"{base}/eng/results_{name}.html" for name in DISTRICTS_2019],
        "sheet": [f"{base}/pdf/counting_results_{letter}.xls" for letter in LETTERS_2019],
    }


CE_URLS = {"2017-03-26": f"{HOST}/ce2017/eng/results.html", "2022-05-08": f"{HOST}/ce2022/eng/results.html"}


def fetch() -> None:
    for year in LEGCO_FC:
        for kind, urls in legco_urls(year).items():
            for url in urls:
                _get(url, ".js" if kind == "js" else ".html")
    for kind, urls in dc2023_urls().items():
        for url in urls:
            _get(url, ".js" if kind == "js" else ".html")
    for kind, urls in dc2019_urls().items():
        for url in urls:
            _get(url, ".xls" if kind == "sheet" else ".html")
    for url in CE_URLS.values():
        _get(url)


def clean(text: object) -> str:
    return " ".join(unescape(str(text or "")).replace("\xa0", " ").split())


def number(text: str) -> int | None:
    found = re.fullmatch(r"\*{0,2}([\d,]+)\*{0,2}( Note)?", clean(text))
    return int(found.group(1).replace(",", "")) if found else None


def page(url: str) -> str:
    return _own(url).path.read_text(encoding="utf-8-sig")


def table_rows(html: str) -> list[list[str]]:
    html = re.sub(r"(?s)<!--.*?-->", "", html)
    out: list[list[str]] = []
    for tr in re.split(r"(?i)<tr\b", html)[1:]:  # some pages leave rows unclosed
        row = [clean(re.sub(r"(?s)<[^>]+>", " ", cell)) for cell in re.findall(r"(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>", tr)]
        if row:
            out.append(row)
    return out


def heading(html: str) -> str:
    """The first non-empty second-level heading of a results page, with its line breaks as spaces."""
    html = re.sub(r"(?s)<!--.*?-->", "", html)
    for block in re.findall(r"(?is)<h2\b[^>]*>(.*?)</h2>", html):
        text = clean(re.sub(r"(?s)<[^>]+>", " ", block))
        if text:
            return text
    raise ValueError("no heading")


@dataclass
class Candidate:
    label: str
    name: str
    votes: int | None
    elected: bool = False


@dataclass
class Contest:
    """One constituency: its candidates, its declared totals, and its counting stations when the source has them."""

    code: str
    name: str
    candidates: list[Candidate]
    valid: int | None = None
    stations: dict[str, str] = field(default_factory=dict)
    votes: dict[str, dict[str, int]] = field(default_factory=dict)
    names_zh: dict[str, str] = field(default_factory=dict)
    zh: str = ""
    main: set[str] = field(default_factory=set)


def _json_after(text: str, start: str) -> object:
    """The JSON value that follows `start` in a script; the source leaves trailing commas in some lists."""
    at = text.index(start) + len(start)
    while text[at] in " \t\r\n":
        at += 1
    depth = 0
    for end in range(at, len(text)):
        if text[end] in "[{":
            depth += 1
        elif text[end] in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(re.sub(r",\s*([\]}])", r"\1", text[at : end + 1]))
    raise ValueError(f"unterminated value after {start!r}")


def _object(value: object) -> dict[str, object]:
    if isinstance(value, list) and value:
        value = value[0]
    if not isinstance(value, dict):
        raise TypeError(f"expected an object, found {type(value).__name__}")
    return {str(k): v for k, v in value.items()}


def _objects(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError(f"expected a list, found {type(value).__name__}")
    return [_object(v) for v in value]


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise TypeError(f"expected a count, found {value!r}")
    return int(value)


def aggregate(html: str) -> tuple[str, list[Candidate]]:
    """A page of candidates with their votes and whether each was elected, as the functional, Election Committee,
    and district committee constituencies publish them."""
    name = heading(html)
    found: list[Candidate] = []
    for row in table_rows(html):
        if len(row) >= 3 and re.fullmatch(r"[A-Z]{0,3}\d+", row[0]) and number(row[2]) is not None and row[1]:
            found.append(
                Candidate(row[0], row[1], number(row[2]), len(row) > 3 and row[3].lower().startswith("elected"))
            )
    if not found:
        raise ValueError(f"no candidates in {name}")
    return name, found


def stations_page(html: str) -> dict[str, tuple[str, bool]]:
    out: dict[str, tuple[str, bool]] = {}
    for row in table_rows(html):
        if len(row) < 3 or not all(cell in ("--", "") or number(cell) is not None for cell in row[2:]):
            continue
        found = re.fullmatch(r"(\*?)([A-Z][A-Z0-9]{3,5})", row[0])
        if found:
            out[found.group(2)] = (row[1].lstrip("*").strip(), bool(found.group(1)) or row[1].startswith("*"))
    return out


def gc(code_html: str, ps_html: str, js: str, label: str) -> Contest:
    """A geographical constituency from its summary page, its station page, and its vote-count script."""
    title = heading(code_html)
    found = re.match(r"\((\w+)\)\s*(.*)", title)
    if found is None:
        raise ValueError(f"unparsed heading {title!r}")
    code, name = found.group(1), found.group(2)
    names = {
        row[0]: row[1]
        for row in table_rows(code_html)
        if len(row) == 3 and re.fullmatch(r"\d+", row[0]) and not re.fullmatch(r"[-\d,]+", row[1])
    }
    if label == "2021":
        summary = _object(_json_after(js, "cadVoteCntLC" + code[2:] + " = "))
    elif label == "2025":
        summary = _object(_json_after(js, "window.allCadVoteCnt.push("))
    else:
        summary = _object(_json_after(js, "cadVoteCntByConst" + code + " = "))
    candidates = []
    for entry in _objects(summary["candidates"]):
        number_ = str(entry["list_num"])
        votes = _int(entry.get("total_count", entry.get("numOfVotesReceived")))
        elected = _int(entry.get("num_satisfy", entry.get("numSatisfy", 0))) > 0
        candidates.append(Candidate(number_, names[number_], votes, elected))
    valid = summary.get("psInterimCountResult", summary.get("numOfValidVotesCounted"))
    contest = Contest(code, name, candidates, _int(valid) if valid is not None else None)
    stations = stations_page(ps_html)
    contest.stations = {k: v[0] for k, v in stations.items()}
    contest.main = {k for k, v in stations.items() if v[1]}
    pairs: list[tuple[str, str, int]] = []
    raw_votes = _json_after(js, "var psVoteCnt = ")
    for row in _objects(raw_votes["candidates"] if isinstance(raw_votes, dict) else raw_votes):
        if "candidates" in row:
            station = str(row.get("station_code", row.get("stationCode")))
            pairs += [(station, str(c["list_num"]), _int(c["total_count"])) for c in _objects(row["candidates"])]
        else:
            pairs.append((str(row["ps_code"]), str(row["list_num"]), _int(row["total_count"])))
    for station, option, votes in pairs:
        contest.votes.setdefault(station, {})[option] = votes
    return contest


def dc2019() -> tuple[dict[str, str], dict[str, Contest]]:
    """The 2019 constituencies by code, with the district each belongs to by letter."""
    urls = dc2019_urls()
    contests: dict[str, Contest] = {}
    districts: dict[str, str] = {}
    for url in urls["district"]:
        html = page(url)
        named = re.search(r"<!--DC_Name-->(.*?)<!--DC_Name-->", html, re.DOTALL)
        if named is None:
            raise ValueError(f"no district name in {url}")
        name = clean(re.sub(r"<[^>]+>", " ", named.group(1)))
        current: Contest | None = None
        for row in table_rows(html):
            if len(row) == 5 and re.fullmatch(r"[A-Z]\d\d", row[0]):
                current = Contest(row[0], row[1], [])
                contests[row[0]] = current
                row = row[2:]
            if len(row) == 3 and current is not None and re.fullmatch(r"\d+", row[0]):
                votes = number(row[2])
                if votes is not None:
                    current.candidates.append(Candidate(row[0], row[1], votes, "*" in row[2]))
        letter = next(iter(contests))[0] if current is None else current.code[0]
        districts[letter] = name
    for url in urls["sheet"]:
        book = fastexcel.read_excel(str(_own(url).path))
        frame = book.load_sheet_by_idx(0, header_row=None, dtypes="string").to_polars()
        current = None
        for row in frame.rows():
            code = clean(row[3])
            if row[0] and re.fullmatch(r"[A-Z]\d\d", clean(row[0])):
                current = contests[clean(row[0])]
                current.zh = clean(row[1])
            if current is None or not re.fullmatch(r"[A-Z]{1,2}\d{4}", code):
                continue
            current.stations[code] = clean(row[5])
            current.names_zh[code] = clean(row[4])
            for index, cell in enumerate(row[6:], 1):
                value = number(cell or "")
                if cell is not None and clean(cell) != "--" and value is None:
                    raise ValueError(f"unreadable count {cell!r} at {code}")
                if value is not None:
                    current.votes.setdefault(code, {})[str(index)] = value
    return districts, contests


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


REO_SOURCE = key("source", "reo")
LEGCO = key("body", "legco")
CHIEF_EXECUTIVE = key("office", "chief-executive")
SNTV = key("electoral_system", "sntv")
FPTP = key("electoral_system", "fptp")
BLOCK = key("electoral_system", "block")
STATION_SCHEME = "hk.counting_station"
CONSTITUENCY_SCHEME = "hk.constituency"

LEGCO_DAYS = {2021: date(2021, 12, 19), 2025: date(2025, 12, 7)}
DC_DAYS = {2019: date(2019, 11, 24), 2023: date(2023, 12, 10)}
CE_DAYS = {"2017-03-26": date(2017, 3, 26), "2022-05-08": date(2022, 5, 8)}

METHOD = (
    "Votes are those the Registration and Electoral Office published for each counting station and constituency. "
    "Each elector voted for one candidate where a constituency elects two members, and for as many candidates as "
    "there are seats where the electorate is a committee. The office publishes votes received and the number of "
    "valid votes counted; it does not publish registered electors, ballots cast, or invalid ballots in these tables."
)


def all_urls() -> list[str]:
    urls: list[str] = []
    for year in LEGCO_DAYS:
        for group in legco_urls(year).values():
            urls += group
    for group in dc2023_urls().values():
        urls += group
    for group in dc2019_urls().values():
        urls += group
    return [*urls, *CE_URLS.values(), f"{HOST}/dc2023/eng/rs_dcgc.html"]


def packages() -> list[str]:
    days = [*(d.isoformat() for d in LEGCO_DAYS.values()), *(d.isoformat() for d in DC_DAYS.values()), *CE_DAYS]
    return ["hk.reo/registry", *(f"hk.reo/{day}" for day in sorted(days))]


def parse() -> None:
    registry().write()
    for name in packages()[1:]:
        build(name.split("/")[1]).write()


def emit() -> None:
    package.run("load", packages())


def pretty(text: str) -> str:
    return string.capwords(text.lower()) if text.isupper() else text


def registry() -> Package:
    p = Package(
        "hk.reo/registry",
        SOURCE,
        "Hong Kong's Legislative Council, District Councils, and Chief Executive office, with the electoral systems "
        "and unit schemes their election results use, from the Registration and Electoral Office",
    )
    p.external.add(HONG_KONG)
    p.add(
        "source",
        id=REO_SOURCE,
        kind="official_body",
        publisher="Registration and Electoral Office, Hong Kong",
        title="Election results published by the Registration and Electoral Office",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Registration and Electoral Office, Hong Kong Special Administrative Region Government",
    )
    for url in all_urls():
        p.artifact(_own(url), REO_SOURCE)
    p.add(
        "scheme", code=CONSTITUENCY_SCHEME, polity_id=HONG_KONG, kind="electoral_district", rank=1, noun="constituency"
    )
    p.add("scheme", code=STATION_SCHEME, polity_id=HONG_KONG, kind="precinct", rank=2, noun="counting station")
    p.add("body", id=LEGCO, polity_id=HONG_KONG, kind="unicameral")
    p.name(LEGCO, "Legislative Council of Hong Kong", "en", "official")
    p.name(LEGCO, "香港特別行政區立法會", "zh", "native", False)
    p.add("office", id=CHIEF_EXECUTIVE, polity_id=HONG_KONG, kind="head_of_government")
    p.name(CHIEF_EXECUTIVE, "Chief Executive of Hong Kong", "en", "official")
    p.name(CHIEF_EXECUTIVE, "香港特別行政區行政長官", "zh", "native", False)
    systems = (
        (
            SNTV,
            {"formula": "sntv"},
            (
                "Each elector votes for one candidate and the candidates with the most votes win the constituency's two seats."
            ),
        ),
        (FPTP, {"formula": "fptp"}, "The candidate with the most votes wins the single seat."),
        (
            BLOCK,
            {"formula": "block"},
            (
                "Each elector votes for up to as many candidates as there are seats, and the candidates with the most "
                "votes win."
            ),
        ),
    )
    for id, rules, text in systems:
        p.add("electoral_system", id=id, polity_id=HONG_KONG, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    districts, _ = dc2019()
    for letter, name in sorted(districts.items()):
        body = key("body", "dc", letter)
        p.add("body", id=body, polity_id=HONG_KONG, kind="council")
        p.name(body, f"{name} District Council", "en", "official")
    return p


def chief_executive(day: str) -> tuple[list[Candidate], str]:
    html = page(CE_URLS[day])
    text = clean(re.sub(r"(?s)<[^>]+>", " ", html))
    found: list[Candidate] = []
    for index, name, votes in re.findall(
        r"Number of votes given to Candidate(?: No\. (\d+))?, (?:Mr\.|Ms\.|Mrs\.|Dr\.) (.+?), is ([\d,]+) ?\.", text
    ):
        found.append(Candidate(index or "1", name, int(votes.replace(",", ""))))
    winner = re.search(r"declared on [\d\w ]+ that (?:Mr\.|Ms\.|Mrs\.|Dr\.) (.+?) was elected", text)
    if not found or winner is None:
        raise ValueError(f"unparsed Chief Executive result {day}")
    for candidate in found:
        candidate.elected = candidate.name == winner.group(1)
    return found, text


class Writer:
    """Stages one election's contests, with their options, candidacies, units, values, declared totals, and outcomes."""

    def __init__(self, p: Package, day: str, election: str) -> None:
        self.p = p
        self.day = day
        self.election = election
        self.units: dict[str, str] = {}

    def unit(self, code: str, scheme: str, kind: str, en: str, zh: str = "") -> str:
        if code in self.units:
            return self.units[code]
        unit = self.units[code] = key("unit", self.day, code)
        self.p.add(
            "reporting_unit",
            id=unit,
            polity_id=HONG_KONG,
            scheme_code=scheme,
            code=f"{self.day}:{code}",
            area_relation="unlocated",
            area_id=None,
            kind=kind,
        )
        self.p.name(unit, en, "en", "official")
        if zh:
            self.p.name(unit, zh, "zh", "native", False)
        return unit

    def contest(
        self,
        code: str,
        en: str,
        zh: str,
        slug: str,
        candidates: list[Candidate],
        seats: int,
        system: str,
        body: str | None = None,
        office: str | None = None,
        valid: int | None = None,
        stations: dict[str, str] | None = None,
        votes: dict[str, dict[str, int]] | None = None,
        zh_stations: dict[str, str] | None = None,
    ) -> None:
        p = self.p
        contest = key("contest", self.day, code)
        p.add(
            "contest",
            id=contest,
            election_id=self.election,
            kind="office" if office else "body_seats",
            office_id=office,
            body_id=body,
            magnitude=seats,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, en, "en", "official")
        if zh:
            p.name(contest, zh, "zh", "native", False)
        p.add("slug", path=f"{slug}", entity_id=contest, is_current=True)
        result_set = key("result_set", self.day, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=self.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=REO_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        options: dict[str, str] = {}
        for candidate in candidates:
            tag = f"{code}:{candidate.label}"
            person = key("person", self.day, tag)
            option = options[candidate.label] = key("option", self.day, tag)
            candidacy = key("candidacy", self.day, tag)
            p.add("person", id=person)
            p.name(person, candidate.name, "en", "official")
            position = int(re.sub(r"\D", "", candidate.label) or 0)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=candidate.name,
            )
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
            )
            if candidate.elected:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=candidate.votes)
        if votes is None:
            unit = self.unit(code, CONSTITUENCY_SCHEME, "aggregate", en, zh)
            for candidate in candidates:
                self.put(result_set, unit, options[candidate.label], candidate.votes or 0)
            return
        for station, counts in votes.items():
            unit = self.unit(
                station, STATION_SCHEME, "standard", (stations or {})[station], (zh_stations or {}).get(station, "")
            )
            for label, value in counts.items():
                self.put(result_set, unit, options[label], value)

    def put(self, result_set: str, unit: str, option: str, value: int) -> None:
        self.p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=VOTES,
            count_round=0,
            flags=0,
            value=value,
        )


def build(day: str) -> Package:
    first = date.fromisoformat(day)
    p = Package(
        f"hk.reo/{day}",
        SOURCE,
        f"Hong Kong elections of {first} at the finest unit the Registration and Electoral Office publishes",
        depends=["hk.reo/registry"],
    )
    p.external.add(HONG_KONG)
    if first in LEGCO_DAYS.values():
        _legco(p, day, first)
    elif first in DC_DAYS.values():
        _district_councils(p, day, first)
    else:
        _chief_executive(p, day, first)
    return p


def _election(p: Package, day: str, first: date, kind: str, en: str, zh: str, slug: str) -> str:
    election = key("election", day)
    p.add("election", id=election, polity_id=HONG_KONG, kind=kind, date_from=first, date_to=first, status="held")
    p.add("slug", path=slug, entity_id=election, is_current=True)
    p.name(election, en, "en", "official")
    p.name(election, zh, "zh", "native", False)
    return election


def _system(seats: int) -> str:
    return FPTP if seats == 1 else BLOCK


def _legco(p: Package, day: str, first: date) -> None:
    year = first.year
    urls = legco_urls(year)
    election = _election(
        p,
        day,
        first,
        "general",
        f"{year} Hong Kong Legislative Council general election",
        f"{year}年立法會換屆選舉",
        f"hk/{day}-legco",
    )
    w = Writer(p, day, election)
    for index in range(GC_COUNT):
        for url in (urls["gc"][index], urls["ps"][index], urls["js"][index]):
            p.uses(_own(url))
        c = gc(page(urls["gc"][index]), page(urls["ps"][index]), page(urls["js"][index]), str(year))
        stations = dict(c.stations)
        votes = dict(c.votes)
        if year == 2025:
            n = int(c.code[2:])
            renames = {"CMCS": f"MCS{n:02d}", "ZEC01": f"LC{n:03d}"}
            stations = {renames.get(k, k): v for k, v in stations.items()}
        w.contest(
            c.code.lower(),
            f"{year} Legislative Council general election, {pretty(c.name)} geographical constituency ({c.code})",
            "",
            f"hk/{day}-legco/{c.code.lower()}",
            c.candidates,
            sum(x.elected for x in c.candidates),
            SNTV,
            body=LEGCO,
            valid=c.valid,
            stations=stations,
            votes=votes,
        )
    for url in urls["ecc"] + urls["fc"]:
        p.uses(_own(url))
        name, candidates = aggregate(page(url))
        seats = sum(x.elected for x in candidates)
        slug = url.rsplit("/", 1)[1].removesuffix(".html")
        slug = re.sub(r"^(results|rs)_(fc_)?", "", slug)
        label = "ecc" if "ecc" in url else f"fc-{slug}"
        kind = "Election Committee constituency" if label == "ecc" else f"{pretty(name)} functional constituency"
        w.contest(
            label,
            f"{year} Legislative Council general election, {kind}",
            "",
            f"hk/{day}-legco/{label}",
            candidates,
            seats,
            BLOCK if label == "ecc" else _system(seats),
            body=LEGCO,
        )


def _district_councils(p: Package, day: str, first: date) -> None:
    year = first.year
    election = _election(
        p,
        day,
        first,
        "local",
        f"{year} Hong Kong District Council ordinary election",
        f"{year}年區議會選舉",
        f"hk/{day}-district-councils",
    )
    w = Writer(p, day, election)
    districts, contests19 = dc2019()
    if year == 2019:
        for url in dc2019_urls()["district"] + dc2019_urls()["sheet"]:
            p.uses(_own(url))
        for c in contests19.values():
            letter = c.code[0]
            w.contest(
                c.code.lower(),
                f"{year} {districts[letter]} District Council election, {pretty(c.name)} constituency ({c.code})",
                f"{year}年區議會選舉{c.zh}選區",
                f"hk/{day}-district-councils/{c.code.lower()}",
                c.candidates,
                sum(x.elected for x in c.candidates),
                _system(sum(x.elected for x in c.candidates)),
                body=key("body", "dc", letter),
                stations=c.stations,
                votes=c.votes,
                zh_stations=c.names_zh,
            )
        _accept_2019(p, contests19)
        return
    urls = dc2023_urls()
    for index in range(len(urls["gc"])):
        for url in (urls["gc"][index], urls["ps"][index], urls["js"][index]):
            p.uses(_own(url))
        c = gc(page(urls["gc"][index]), page(urls["ps"][index]), page(urls["js"][index]), "2023")
        seats = sum(x.elected for x in c.candidates)
        w.contest(
            c.code.lower(),
            f"{year} District Council ordinary election, {pretty(c.name)} geographical constituency ({c.code})",
            "",
            f"hk/{day}-district-councils/{c.code.lower()}",
            c.candidates,
            seats,
            SNTV if seats == 2 else _system(seats),
            body=key("body", "dc", c.code[0]),
            valid=c.valid,
            stations=c.stations,
            votes=c.votes,
        )
    for url in urls["dcc"]:
        p.uses(_own(url))
        name, candidates = aggregate(page(url))
        letter = url.rsplit("_", 1)[1][0].upper()
        seats = sum(x.elected for x in candidates)
        w.contest(
            f"dcc-{letter.lower()}",
            f"{year} District Council ordinary election, {name} district committee constituency",
            "",
            f"hk/{day}-district-councils/dcc-{letter.lower()}",
            candidates,
            seats,
            BLOCK if seats > 1 else FPTP,
            body=key("body", "dc", letter),
        )


def _accept_2019(p: Package, contests: dict[str, Contest]) -> None:
    mismatch = []
    for c in contests.values():
        sums: dict[str, int] = {}
        for counts in c.votes.values():
            for label, value in counts.items():
                sums[label] = sums.get(label, 0) + value
        if any(sums.get(x.label, 0) != x.votes for x in c.candidates):
            mismatch.append(c.code)
    if mismatch:
        p.accept(
            "accounting.declared",
            f"the constituency {', '.join(mismatch)} prints a candidate's votes as amended by a notice in the "
            "Government Gazette, so the printed figure differs from the sum of its counting-station figures; the "
            "station figures are stored as counted",
        )


def _chief_executive(p: Package, day: str, first: date) -> None:
    year = first.year
    candidates, _ = chief_executive(day)
    election = _election(
        p,
        day,
        first,
        "indirect",
        f"{year} Hong Kong Chief Executive election",
        f"{year}年行政長官選舉",
        f"hk/{day}-chief-executive",
    )
    p.uses(_own(CE_URLS[day]))
    Writer(p, day, election).contest(
        "chief-executive",
        f"{year} Hong Kong Chief Executive election",
        f"{year}年行政長官選舉",
        f"hk/{day}-chief-executive/chief-executive",
        candidates,
        1,
        FPTP,
        office=CHIEF_EXECUTIVE,
    )
