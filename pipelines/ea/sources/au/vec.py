import difflib
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
from functools import cache
from html import unescape
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote
from uuid import uuid5

import fastexcel
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "au.vec"
ROOT = "https://www.vec.vic.gov.au"
RESULTS = f"{ROOT}/results/state-election-results"
AUSTRALIA = str(uuid5(package.NAMESPACE, "polity\x1fAU"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fAUS"))
NO_PARTY = {"", "Independent"}


class Year(NamedTuple):
    year: int
    day: date
    vintage: str
    page: str
    sheet: str | None
    district_prefix: str = ""
    fpv_prefix: str | None = None
    council: bool = True


BLOB = "https://itsitecoreblobvecprd01.blob.core.windows.net/public-files/historical-results"
YEARS = {
    2006: Year(
        2006,
        date(2006, 11, 25),
        "2005",
        f"{BLOB}/state2006/state2006resultsummary.html",
        None,
        "state2006result",
        "state2006fpvbyvotingcentre",
        False,
    ),
    2010: Year(
        2010,
        date(2010, 11, 27),
        "2005",
        f"{BLOB}/state2010/state2010resultsummary.html",
        None,
        "state2010result",
        "state2010fpvbyvotingcentre",
        False,
    ),
    2014: Year(
        2014,
        date(2014, 11, 29),
        "2013",
        f"{BLOB}/state2014/summary.html",
        None,
        "",
        "fpvbyvotingcentre",
        False,
    ),
    2018: Year(2018, date(2018, 11, 24), "2013", f"{BLOB}/state2018/summary.html", None),
    2022: Year(
        2022,
        date(2022, 11, 26),
        "2021",
        f"{RESULTS}/2022-state-election-results",
        f"{ROOT}/-/media/c4665365795d431e811d0ce818e373d4.xlsx",
    ),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


VEC = key("source", SOURCE)
ASSEMBLY = key("body", "legislative-assembly")
ASSEMBLY_SYSTEM = key("electoral_system", "legislative-assembly", "irv")
COUNCIL = key("body", "legislative-council")
COUNCIL_SYSTEM = key("electoral_system", "legislative-council", "stv")
VICTORIA = area("au-vic.state", "VIC")

ASSEMBLY_METHOD = (
    "First preference votes per voting centre, as the Victorian Electoral Commission publishes them in its results by "
    "voting centre spreadsheet. Ordinary votes are reported per election-day voting centre and per early voting "
    "centre; absent, postal, provisional, marked-as-voted, and other early votes are reported once per district and "
    "are stored as one unit each. The elected member and the two candidates left after the distribution of "
    "preferences come from each district's results page; the later counts of the distribution are not stored here. "
    "Where a district was recounted, the commission publishes no figures per voting centre, so that district's totals "
    "are stored as one unit. Enrolment at the close of rolls is stored on a separate unit per district."
)
COUNCIL_METHOD = (
    "First preference votes per voting centre, as the Victorian Electoral Commission publishes them in its region votes "
    "received spreadsheet. A voter either numbers candidates below the line or marks one group above the line, and "
    "the commission counts an above-the-line vote for the group's first-listed candidate, as the first preference "
    "totals on its region results page do. The two are stored together, so each candidate's votes at a voting centre "
    "are the group's above-the-line votes plus, for the group's first-listed candidate, that candidate's own "
    "below-the-line votes. Ordinary votes are reported per election-day voting centre and per early voting centre; "
    "absent, postal, provisional, marked-as-voted, and other early votes are reported once per Legislative Assembly "
    "district and are stored as one unit each. The five elected members come from each region's results page; the "
    "counts of the distribution of preferences are not stored here. Enrolment at the close of rolls is stored on a "
    "separate unit per region."
)
SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        ASSEMBLY_SYSTEM,
        date(1911, 1, 1),
        None,
        {"formula": "irv"},
        (
            "Each district elects one member to the Legislative Assembly by full preferential voting: voters must "
            "number every box, and the candidate with the fewest votes is excluded and their ballots passed on at "
            "full value until one candidate holds an absolute majority."
        ),
    ),
    (
        COUNCIL_SYSTEM,
        date(2006, 1, 1),
        None,
        {"formula": "stv", "quota": "droop", "surplus": "weighted_inclusive_gregory"},
        (
            "Each of eight regions elects five members to the Legislative Council by the single transferable vote "
            "with a Droop quota. A voter numbers at least five candidates below the line, or marks one group above "
            "the line, which follows the preference order the group lodged with the commission as its group voting "
            "ticket. A surplus passes on every ballot paper at a reduced value (the inclusive Gregory method). Group "
            "voting tickets were in use at the 2006 to 2022 elections."
        ),
    ),
]


# Fetching -------------------------------------------------------------------------------------------------------


def district_slugs(year: int) -> list[str]:
    config = YEARS[year]
    text = raw.cached(SOURCE, config.page).path.read_text(encoding="utf-8")
    if config.sheet is None:
        return sorted(set(re.findall(rf'href="{config.district_prefix}([a-z-]+district)\.html"', text)))
    return sorted(set(re.findall(r"/results-by-district/([a-z0-9-]+)-district-results", text)))


def district_url(year: int, slug: str) -> str:
    config = YEARS[year]
    if config.sheet is None:
        return f"{config.page.rsplit('/', 1)[0]}/{config.district_prefix}{slug}.html"
    return f"{config.page}/results-by-district/{slug}-district-results"


def sheet_url(year: int, slug: str) -> str:
    config = YEARS[year]
    if config.fpv_prefix is not None:
        return f"{config.page.rsplit('/', 1)[0]}/{config.fpv_prefix}{slug}.html"
    return f"{config.page.rsplit('/', 1)[0]}/files/{slug}.xlsx"


def urls(year: int) -> list[str]:
    config = YEARS[year]
    found = [config.page]
    if config.sheet:
        found.append(config.sheet)
    else:
        found += [sheet_url(year, slug) for slug in district_slugs(year)]
    return found


def region_slugs(year: int) -> list[str]:
    config = YEARS[year]
    if not config.council:
        return []
    text = raw.cached(SOURCE, config.page).path.read_text(encoding="utf-8")
    if config.sheet is None:
        return sorted(set(re.findall(r'href="([a-z-]+region)\.html"', text)))
    return sorted(set(re.findall(r"/results-by-region/([a-z0-9-]+)-region-results", text)))


def region_url(year: int, slug: str) -> str:
    config = YEARS[year]
    if config.sheet is None:
        return f"{config.page.rsplit('/', 1)[0]}/{slug}.html"
    return f"{config.page}/results-by-region/{slug}-region-results"


def received_url(year: int, slug: str) -> str:
    """The region's votes received spreadsheet: the 2018 name follows the page's, and the 2022 page links its own."""
    if YEARS[year].sheet is None:
        return f"{region_url(year, slug).rsplit('/', 1)[0]}/files/{slug}votesreceived.xls"
    html = raw.cached(SOURCE, region_url(year, slug)).path.read_text(encoding="utf-8")
    link = re.search(r'id="votesReceivedLink" href="([^"]+)"', html)
    if not link:
        raise ValueError(f"{year} {slug}: the region page links no votes received spreadsheet")
    return quote(unescape(link.group(1)), safe=":/")


def region_urls(year: int) -> list[str]:
    slugs = region_slugs(year)
    return [region_url(year, slug) for slug in slugs] + [received_url(year, slug) for slug in slugs]


def fetch() -> None:
    for year, config in YEARS.items():
        raw.fetch(SOURCE, config.page, suffix=".html")
        if config.sheet:
            raw.fetch(SOURCE, config.sheet, suffix=".xlsx")
        slugs = district_slugs(year)
        raw.fetch_many(SOURCE, [district_url(year, slug) for slug in slugs], suffix=".html")
        if not config.sheet:
            suffix = ".html" if config.fpv_prefix is not None else ".xlsx"
            raw.fetch_many(SOURCE, [sheet_url(year, slug) for slug in slugs], suffix=suffix)
        regions = region_slugs(year)
        raw.fetch_many(SOURCE, [region_url(year, slug) for slug in regions], suffix=".html")
        raw.fetch_many(SOURCE, [received_url(year, slug) for slug in regions], suffix=".xls")
    from . import vec_by

    vec_by.fetch()


# Parsing --------------------------------------------------------------------------------------------------------


class Row(NamedTuple):
    district: str
    place: str
    vote_type: str
    candidate: str
    order: int
    party: str
    votes: int


class Page(NamedTuple):
    district: str
    elected: str
    enrolment: int
    formal: int
    informal: int
    first: list[tuple[str, str, int]]
    final: list[tuple[str, str, int]]


def tokens(name: str) -> frozenset[str]:
    return frozenset(re.sub(r"[^a-z ]", "", name.lower().replace(",", " ").replace("-", " ")).split())


def match(name: str, options: dict[frozenset[str], str]) -> str:
    """Match a name on a results page to a candidate name in the spreadsheet; the spreadsheet holds a few misspellings."""
    exact = options.get(tokens(name))
    if exact:
        return exact
    flat = "".join(sorted(tokens(name)))
    scored = sorted(
        ((difflib.SequenceMatcher(None, flat, "".join(sorted(t))).ratio(), c) for t, c in options.items()),
        reverse=True,
    )
    if scored[0][0] < 0.85 or (len(scored) > 1 and scored[1][0] > scored[0][0] - 0.1):
        raise ValueError(f"{name!r} matches no candidate in {sorted(options.values())}")
    return scored[0][1]


def person_name(page_name: str) -> str:
    surname, _, given = page_name.partition(", ")
    words = [
        word.title()
        if word.isupper()
        else re.sub(r"^([A-Z][a-z]+)([A-Z]+)$", lambda m: m.group(1) + m.group(2).title(), word)
        for word in surname.split(" ")
    ]
    return f"{given} {' '.join(words)}".strip()


def _number(text: str) -> int:
    return int(text.replace(",", ""))


def _text(cell: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", cell))).strip()


def legacy_sheet(year: int, slug: str) -> tuple[str, int, int, int, list[tuple[str, str, int]], list[Row]]:
    """One district's spreadsheet: its name, enrolment, formal and informal votes, candidate totals, and voting centre rows."""
    return parse_legacy_sheet(raw.cached(SOURCE, sheet_url(year, slug)).path)


def parse_legacy_sheet(path: Path) -> tuple[str, int, int, int, list[tuple[str, str, int]], list[Row]]:
    grid = fastexcel.read_excel(str(path)).load_sheet(0, header_row=None, dtypes="string").to_polars().rows()
    cells = [[(c or "").strip() for c in row] for row in grid]
    name = re.sub(r"\s*District.*", "", cells[3][0]).strip()
    head = {row[0].rstrip(":"): _number(row[2]) for row in cells[4:9] if row[0].endswith(":")}
    labels = next(row for row in cells if row[1] == "" and any("\n" in c for c in row))
    columns = [i for i, c in enumerate(labels) if i > 2 and c]
    candidates: dict[int, tuple[str, str]] = {}
    for i in columns:
        text, _, party = labels[i].partition("\n")
        candidates[i] = (text.strip(), party.strip())
    out: list[Row] = []
    totals: list[tuple[str, str, int]] = []
    state = "ordinary"
    for row in cells:
        place = row[1]
        if not place or place.startswith(("Percentage", "Total Declaration")) or place == "Total Ordinary Votes":
            state = "declaration" if place == "Total Ordinary Votes" else state
            continue
        if place == "TOTAL ALL VOTE TYPES":
            totals = [(candidates[i][0], candidates[i][1], _number(row[i])) for i in columns if i in candidates]
            break
        for order, i in enumerate(columns, 1):
            votes = _number(row[i])
            label, party = candidates[i]
            who = label.split("\n")[0]
            if who.startswith("Total Votes"):
                who = "Total Votes"
            out.append(Row(name, place, state, who, order, party, votes))
    return name, head["ENROLMENT"], head["FORMAL VOTES"], head["INFORMAL VOTES"], totals, out


def _legend(html: str) -> dict[str, str]:
    """Party abbreviations a district page defines in its note, and the registered names they stand for."""
    note = re.search(r'<span class="bold-text red-text">Note:.*?</div>', html, flags=re.DOTALL)
    if not note:
        return {}
    pairs = re.findall(
        r'<span\s+class="bold-text">(.*?)=\s*</span>(.*?)(?=<span|</div>)', note.group(0), flags=re.DOTALL
    )
    return {_text(short): _text(full).rstrip(",").strip() for short, full in pairs}


def _district_name(html: str) -> str:
    heading = re.search(r"<h2>(.*?)</h2>", html, flags=re.DOTALL)
    named = re.match(r"(?:.*?: )?(.+?) District", _text(heading.group(1))) if heading else None
    if not named:
        raise ValueError("the page names no district")
    return named.group(1)


def html_page(year: int, slug: str) -> tuple[Page, list[Row]]:
    """A district page and its first preferences by voting centre page, which the 2006 to 2014 elections publish as HTML."""
    html = raw.cached(SOURCE, district_url(year, slug)).path.read_text(encoding="utf-8")
    sheet = raw.cached(SOURCE, sheet_url(year, slug)).path.read_text(encoding="utf-8")
    return parse_html_page(f"{year} {slug}", html, sheet)


def parse_html_page(label: str, html: str, sheet: str) -> tuple[Page, list[Row]]:
    """Parse a district page and its voting centre page, given as HTML."""
    body = html[html.index("<body") :]
    text = _text(re.sub(r"<script.*?</script>|<style.*?</style>", "", body, flags=re.DOTALL))
    elected = re.search(r'<span class="bold-text">([^<]+)</span>', body)
    enrolment = re.search(r"Total Enrolment(?: as at close of rolls)?:? ([\d,]+)", text, flags=re.IGNORECASE)
    formal = re.search(r"Formal Votes: ([\d,]+)", text, flags=re.IGNORECASE)
    informal = re.search(r"Informal Votes: ([\d,]+)", text, flags=re.IGNORECASE)
    first_table = re.search(r"1st pref votes.*?</table>", body, flags=re.DOTALL)
    distribution = re.search(r"Votes after distribution.*?</table>", body, flags=re.DOTALL)
    if not (elected and enrolment and formal and informal and first_table):
        raise ValueError(f"{label}: the district page has an unexpected layout")

    def table(source: str) -> list[list[str]]:
        found = [
            [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.DOTALL)]
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", source, flags=re.DOTALL)
        ]
        return [cells for cells in found if len(cells) == 4]

    legend = _legend(body)
    first = [(cells[0], legend.get(cells[1], cells[1]), _number(cells[2])) for cells in table(first_table.group(0))]
    final = [(cells[0], "", _number(cells[2])) for cells in table(distribution.group(0))] if distribution else []
    name = _district_name(body)
    detail = Page(
        name,
        elected.group(1),
        _number(enrolment.group(1)),
        _number(formal.group(1)),
        _number(informal.group(1)),
        first,
        final,
    )

    if _district_name(sheet) != name:
        raise ValueError(f"{label}: the voting centre page is for another district")
    if "not available where a recount took place" in sheet:
        # The commission publishes no voting centre figures for a recounted district: store its totals as one unit.
        total = [Row(name, "All Votes", "declaration", n, i, p, v) for i, (n, p, v) in enumerate(first, 1)]
        total.append(Row(name, "All Votes", "declaration", "Informal Votes", len(first) + 1, "", detail.informal))
        total.append(
            Row(name, "All Votes", "declaration", "Total Votes", len(first) + 2, "", detail.formal + detail.informal)
        )
        return detail, total
    heads = [
        _text(th)
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", sheet, flags=re.DOTALL)
        if not re.search(r"<td[^>]*>[^<]*\d", tr)
        for th in re.findall(r"<th[^>]*>(.*?)</th>", tr, flags=re.DOTALL)
        if ", " in _text(th)
    ]
    if [tokens(h) for h in heads] != [tokens(n) for n, _, _ in first]:
        raise ValueError(f"{label}: the candidates on the voting centre page differ from the district page")
    out: list[Row] = []
    state = "ordinary"
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", sheet, flags=re.DOTALL):
        labels = [_text(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", tr, flags=re.DOTALL)]
        cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.DOTALL)]
        if len(labels) != 1 or len(cells) != len(first) + 2 or labels[0].startswith(("Percentage", "Voting")):
            continue
        place = labels[0]
        if place == "Total":
            break
        if place.startswith("Ordinary Votes Total"):
            state = "declaration"
            continue
        place = DECLARATION.get(place, place)
        numbers = [_number(c) for c in cells]
        for order, ((_, party, _), who, votes) in enumerate(zip(first, heads, numbers, strict=False), 1):
            out.append(Row(name, place, state, who, order, party, votes))
        out.append(Row(name, place, state, "Informal Votes", len(first) + 1, "", numbers[-2]))
        out.append(Row(name, place, state, "Total Votes", len(first) + 2, "", numbers[-1]))
    if not out:
        raise ValueError(f"{label}: no voting centre rows")
    return detail, out


DECLARATION = {
    "Postal Votes": "Postal Vote",
    "Early Votes": "Early Vote",
    "Marked As Voted Votes": "Marked As Voted",
    "Provisional Votes": "Provisional",
    "Absent Votes": "Absent",
    "Declaration Votes": "Declaration",
    "All Votes Votes": "All Votes",
}


def page(year: int, slug: str) -> Page:
    if YEARS[year].fpv_prefix is not None:
        return html_page(year, slug)[0]
    if YEARS[year].sheet is None:
        name, enrolment, formal, informal, totals, _ = legacy_sheet(year, slug)
        html = raw.cached(SOURCE, district_url(year, slug)).path.read_text(encoding="utf-8")
        elected = re.search(r'<span class="bold-text">([^<]+)</span>', html)
        distribution = re.search(r"Results after distribution of preferences.*?</table>", html, flags=re.DOTALL)
        if not elected:
            raise ValueError(f"{year} {slug}: the district page has an unexpected layout")
        final = (
            [
                (_text(cells[0]), "", _number(_text(cells[2])))
                for cells in (
                    re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.DOTALL)
                    for tr in re.findall(r"<tr>(.*?)</tr>", distribution.group(0), flags=re.DOTALL)
                )
                if len(cells) == 4
            ]
            if distribution
            else []
        )
        first = [(n, p, v) for n, p, v in totals if not n.startswith(("Informal", "Total"))]
        return Page(name, elected.group(1), enrolment, formal, informal, first, final)
    html = raw.cached(SOURCE, district_url(year, slug)).path.read_text(encoding="utf-8")
    return parse_main_page(f"{year} {slug}", slug.replace("-", " "), html)


def parse_main_page(label: str, name: str, html: str) -> Page:
    """A district page in the layout the commission has used since 2022, whose district is named `name`."""
    body = html[html.index("<main") :]
    text = _text(re.sub(r"<script.*?</script>|<style.*?</style>", "", body, flags=re.DOTALL))
    elected = re.search(r"Elected member (.+?) (?:Votes counted|Total enrolment)", text)
    enrolment = re.search(r"Total enrol(?:ment as at close of rolls|led voters): ([\d,]+)", text)
    formal = re.search(r"Formal votes: ([\d,]+)", text)
    informal = re.search(r"Informal votes: ([\d,]+)", text)
    tables = re.findall(r"<table.*?</table>", body, flags=re.DOTALL)
    if not (elected and enrolment and formal and informal) or len(tables) < 2:
        raise ValueError(f"{label}: the district page has an unexpected layout")

    def rows(table: str) -> list[tuple[str, str, int]]:
        out = []
        for tr in re.findall(r"<tr>(.*?)</tr>", table, flags=re.DOTALL):
            cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.DOTALL)]
            if len(cells) == 4:
                out.append((cells[0], cells[1], _number(cells[2])))
        return out

    return Page(
        name,
        elected.group(1),
        _number(enrolment.group(1)),
        _number(formal.group(1)),
        _number(informal.group(1)),
        rows(tables[0]),
        rows(tables[1]),
    )


def sheet_rows(year: int) -> list[Row]:
    config = YEARS[year]
    path = raw.cached(SOURCE, str(config.sheet)).path
    frame = fastexcel.read_excel(str(path)).load_sheet(0, header_row=0).to_polars()
    out = []
    for r in frame.iter_rows(named=True):
        out.append(
            Row(
                str(r["Electorate"]),
                str(r["Polling place"]),
                str(r["Vote type new"]),
                str(r["Candidate name"]),
                int(r["Ballot order"]),
                str(r["Party name"]),
                int(r["Votes"]),
            )
        )
    return out


def rows(year: int) -> list[Row]:
    if YEARS[year].fpv_prefix is not None:
        return [r for slug in district_slugs(year) for r in html_page(year, slug)[1]]
    if YEARS[year].sheet is None:
        found: list[Row] = []
        for slug in district_slugs(year):
            found += legacy_sheet(year, slug)[5]
        return found
    return sheet_rows(year)


def parties(years: list[int]) -> dict[str, str]:
    names: dict[str, str] = {}
    for year in years:
        for r in rows(year):
            if r.party not in NO_PARTY and r.candidate not in ("Total Votes", "Informal Votes"):
                code = party_key(r.party)
                if code not in names or names[code].isupper():
                    names[code] = r.party
        for name in council_parties(year):
            if name not in NO_PARTY:
                code = party_key(name)
                if code not in names or names[code].isupper():
                    names[code] = name
    return names


ALIASES = {
    "australian-labor-party": "australian-labor-party-victorian-branch",
    "australian-greens": "the-greens",
    "the-australian-greens-victoria": "the-greens",
    "liberal-party-of-australia-victorian-division": "liberal",
    "national-party-of-australia-victoria": "the-nationals",
    "australian-sex-party-victoria": "sex-party",
    "democratic-labor-party-dlp-of-australia": "labour-dlp",
    "democratic-labour-party-dlp": "labour-dlp",
    "family-first-victoria-inc": "family-first-victoria",
    "family-first": "family-first-victoria",
    "socialist-alliance-victoria": "socialist-alliance",
    "country-alliance": "australian-country-alliance",
}


def party_key(name: str) -> str:
    code = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return ALIASES.get(code, code)


def party(name: str) -> str:
    return key("party", party_key(name))


# Legislative Council ----------------------------------------------------------------------------------------------


class Region(NamedTuple):
    name: str
    elected: list[str]
    enrolment: int
    formal: int
    informal: int
    first: list[tuple[str, str, int]]


class Cell(NamedTuple):
    district: str
    place: str
    votes: dict[str, int]
    formal: int
    informal: int
    total: int


class Sheet(NamedTuple):
    name: str
    order: list[str]
    groups: dict[str, str]
    cells: list[Cell]
    formal: int


@cache
def region_page(year: int, slug: str) -> Region:
    html = raw.cached(SOURCE, region_url(year, slug)).path.read_text(encoding="utf-8")
    body = html[html.index("<main") :] if "<main" in html else html
    text = _text(re.sub(r"<script.*?</script>|<style.*?</style>", "", body, flags=re.DOTALL))
    enrolment = re.search(r"Total enrolment as at close of rolls:? ([\d,]+)", text, flags=re.IGNORECASE)
    formal = re.search(r"Formal votes:? ([\d,]+)", text, flags=re.IGNORECASE)
    informal = re.search(r"Informal votes:? ([\d,]+)", text, flags=re.IGNORECASE)
    first: list[tuple[str, str, int]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, flags=re.DOTALL):
        cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.DOTALL)]
        if len(cells) == 4 and re.fullmatch(r"[\d,]+", cells[2]) and cells[3].endswith("%"):
            first.append((cells[0], cells[1], _number(cells[2])))
    parts = re.split(r"\d+(?:st|nd|rd|th) elected:", text)[1:]
    elected = []
    for part in parts:
        named = [n for n, _, _ in first if part.strip().startswith(n)]
        if not named:
            raise ValueError(f"{year} {slug}: elected member in {part[:60]!r} matches no candidate")
        elected.append(max(named, key=len))
    if not (enrolment and formal and informal and first and len(elected) == 5):
        raise ValueError(f"{year} {slug}: the region page has an unexpected layout")
    return Region(
        slug,
        elected,
        _number(enrolment.group(1)),
        _number(formal.group(1)),
        _number(informal.group(1)),
        first,
    )


@cache
def received(year: int, slug: str) -> Sheet:
    """One region's votes received spreadsheet: a block per district with a row per voting centre, and a column per
    group's above-the-line votes and per candidate's below-the-line votes."""
    path = raw.cached(SOURCE, received_url(year, slug)).path
    grid = fastexcel.read_excel(str(path)).load_sheet(0, header_row=None, dtypes="string").to_polars().rows()
    cells = [[(c or "").strip() for c in row] for row in grid]
    name = next(row[0] for row in cells[:12] if row[0].endswith("Region"))
    order: list[str] = []
    groups: dict[str, str] = {}
    atl: dict[str, int] = {}
    by_candidate: dict[str, int] = {}
    ends: dict[str, int] = {}
    out: list[Cell] = []
    district = ""
    region_total = 0
    for row in cells:
        label = row[0]
        if label.endswith(" District") and any(c.startswith("Group") for c in row):
            district = label.removesuffix(" District")
            atl, by_candidate, ends = {}, {}, {}
            for i, head in enumerate(row):
                if i < 3 or not head:
                    continue
                lines = [line.strip() for line in head.split("\n") if line.strip()]
                if lines[0].startswith("Group") and lines[-1] == "ATL":
                    atl[lines[0]] = i
                elif lines[0].startswith("Group") and lines[-1] != "TOTAL":
                    by_candidate[lines[-1]] = i
                    if lines[-1] not in groups:
                        groups[lines[-1]] = lines[0]
                        order.append(lines[-1])
                elif lines[0] == "Ungrouped":
                    by_candidate[lines[1]] = i
                    if lines[1] not in groups:
                        groups[lines[1]] = ""
                        order.append(lines[1])
                elif lines[0].startswith("Total"):
                    ends[lines[0]] = i
            continue
        if label.upper() == "REGION TOTAL":
            region_total = _number(row[ends["Total Formal"]] or "0")
        if not district or not label or "TOTAL" in label.upper():
            continue
        if not row[ends["Total Votes"]]:
            continue
        leads: dict[str, str] = {}
        for candidate in order:
            leads.setdefault(groups[candidate], candidate)
        votes = {c: _number(row[i] or "0") for c, i in by_candidate.items()}
        for group, i in atl.items():
            lead = leads[group]
            votes[lead] = votes.get(lead, 0) + _number(row[i] or "0")
        out.append(
            Cell(
                district,
                label,
                votes,
                _number(row[ends["Total Formal"]] or "0"),
                _number(row[ends["Total Informal"]] or "0"),
                _number(row[ends["Total Votes"]] or "0"),
            )
        )
    return Sheet(name, order, groups, out, region_total)


def region_name(year: int, slug: str) -> str:
    return received(year, slug).name


def council_parties(year: int) -> list[str]:
    return [party for slug in region_slugs(year) for _, party, _ in region_page(year, slug).first]


# Geo ------------------------------------------------------------------------------------------------------------


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=VEC,
        kind="official_body",
        publisher="Victorian Electoral Commission",
        title="Victorian state and local council election results",
        url=f"{ROOT}/results",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="© State of Victoria (Victorian Electoral Commission)",
    )


def districts(years: list[int]) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for year in years:
        for r in rows(year):
            found[(YEARS[year].vintage, r.district)] = r.district
        for slug in region_slugs(year):
            for cell in received(year, slug).cells:
                found[(YEARS[year].vintage, cell.district)] = cell.district
    return found


def regions(years: list[int]) -> dict[tuple[str, str], str]:
    return {
        (YEARS[year].vintage, region_name(year, slug)): region_name(year, slug)
        for year in years
        for slug in region_slugs(year)
    }


def geo(years: list[int], extra: dict[tuple[str, str], str] | None = None, files: list[str] | None = None) -> Package:
    """The geography of the elections in `years`, and of the by-elections, whose districts and files come as `extra`."""
    found = districts(years) | (extra or {})
    zones = regions(years)
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        f"Victoria, its {len(found)} Legislative Assembly districts, and its {len(zones)} Legislative Council "
        "regions, by boundary vintage, as the Victorian Electoral Commission names them in its state election results",
    )
    p.external |= {AUSTRALIA, COUNTRY}
    source_row(p)
    for year in years:
        for url in [*urls(year), *region_urls(year)]:
            p.artifact(raw.cached(SOURCE, url), VEC)
    for url in files or []:
        p.artifact(raw.cached(SOURCE, url), VEC)
    for code, rank, kind, noun in (
        ("au-vic.state", 1, "administrative", "state"),
        ("au-vic.region", 2, "electoral_district", "region"),
        ("au-vic.district", 2, "electoral_district", "district"),
        ("au-vic.voting_centre", 3, "precinct", "voting centre"),
    ):
        p.add("scheme", code=code, polity_id=AUSTRALIA, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=VICTORIA, scheme_code="au-vic.state")
    p.add("area_code", area_id=VICTORIA, code="VIC")
    p.add("area_parent", area_id=VICTORIA, parent_id=COUNTRY)
    p.name(VICTORIA, "Victoria", "en", "official")
    for scheme, members, held in (
        ("au-vic.district", found, years),
        ("au-vic.region", zones, [y for y in years if YEARS[y].council]),
    ):
        for vintage in sorted({v for v, _ in members}):
            p.add(
                "boundary_set",
                id=key("boundary_set", scheme, vintage),
                scheme_code=scheme,
                vintage=vintage,
                as_of=min(YEARS[y].day for y in held if YEARS[y].vintage == vintage),
            )
    for (vintage, name), label in sorted(zones.items()):
        id = area("au-vic.region", f"{vintage}:{name}")
        p.add("area", id=id, scheme_code="au-vic.region")
        p.add("area_code", area_id=id, code=f"{vintage}:{name}")
        p.add("area_parent", area_id=id, parent_id=VICTORIA)
        p.name(id, label, "en", "official")
    for (vintage, name), label in sorted(found.items()):
        id = area("au-vic.district", f"{vintage}:{name}")
        p.add("area", id=id, scheme_code="au-vic.district")
        p.add("area_code", area_id=id, code=f"{vintage}:{name}")
        p.add("area_parent", area_id=id, parent_id=VICTORIA)
        p.name(id, label, "en", "official")
    return p


# Registry -------------------------------------------------------------------------------------------------------


def registry(years: list[int], extra: dict[str, str] | None = None) -> Package:
    """The registry of the elections in `years`, with the parties of the by-elections, which come as `extra`."""
    names = parties(years)
    for code, name in (extra or {}).items():
        if code not in names or names[code].isupper():
            names[code] = name
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        f"Victoria's Legislative Assembly and Legislative Council, their electoral systems, and the {len(names)} "
        "registered parties on the Victorian Electoral Commission's results, with the source files every election "
        "package cites",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRALIA}
    p.add("body", id=ASSEMBLY, polity_id=AUSTRALIA, kind="lower_house")
    p.name(ASSEMBLY, "Victorian Legislative Assembly", "en", "official")
    p.add("body", id=COUNCIL, polity_id=AUSTRALIA, kind="upper_house")
    p.name(COUNCIL, "Victorian Legislative Council", "en", "official")
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=AUSTRALIA,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for code, name in sorted(names.items()):
        id = key("party", code)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, name.title().replace("'S ", "'s ") if name.isupper() else name, "en", "official")
    return p


# Elections ------------------------------------------------------------------------------------------------------


class Values:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            c: [] for c in ("result_set_id", "unit_id", "option_id", "measure", "count_round", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
        for column, item in zip(self.columns, (result_set, unit, option, measure, 0, value), strict=True):
            self.columns[column].append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


AGGREGATE = {
    "Absent",
    "Provisional",
    "Marked As Voted",
    "Marked as Voted",
    "Other Early Votes",
    "Early Vote",
    "Declaration",
    "All Votes",
}


def _known(p_key: str) -> str | None:
    path = package.folder(f"{SOURCE}/registry") / "build" / "party.parquet"
    return p_key if path.exists() and p_key in set(pl.read_parquet(path)["id"]) else None


def election(year: int) -> Package:
    config = YEARS[year]
    day = config.day
    slugs = district_slugs(year)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} Victorian state election, Legislative Assembly"
        + (" and Legislative Council" if config.council else "")
        + " first preferences by voting centre, from the Victorian Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {AUSTRALIA}
    for url in urls(year):
        p.uses(raw.cached(SOURCE, url))
    for slug in slugs:
        p.uses(raw.cached(SOURCE, district_url(year, slug)))
    election_id = key("election", day)
    path = f"au/vic/{day}-general"
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{year} Victorian state election", "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values = Values()
    known = {id for id in (party(n) for n in parties([year]).values()) if _known(id)}
    by_district: dict[str, list[Row]] = defaultdict(list)
    for r in rows(year):
        by_district[r.district].append(r)
    pages = {tokens(detail.district): detail for detail in (page(year, slug) for slug in slugs)}
    units: dict[tuple[str, str], str] = {}

    def unit_of(district: str, place: str) -> str:
        """The voting centre's unit, shared by the district and region contests that report it."""
        if (district, place) not in units:
            id = units[(district, place)] = key("unit", day, district, place)
            kind = "postal_district" if place == "Postal Vote" else "aggregate" if place in AGGREGATE else "standard"
            p.add(
                "reporting_unit",
                id=id,
                polity_id=AUSTRALIA,
                scheme_code="au-vic.voting_centre",
                code=f"{day}:{district}:{place}",
                area_relation="within",
                area_id=area("au-vic.district", f"{config.vintage}:{district}"),
                kind=kind,
            )
            p.name(id, f"{district}: {place}" if kind != "standard" else place, "en", "official")
        return units[(district, place)]

    for name, district_rows in sorted(by_district.items()):
        detail = pages[tokens(name)]
        contest = key("contest", day, "assembly", name)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=ASSEMBLY,
            district_area_id=area("au-vic.district", f"{config.vintage}:{name}"),
            magnitude=1,
            round_number=1,
            electoral_system_id=ASSEMBLY_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, name, "en", "official")
        p.add("slug", path=f"{path}/assembly-{changeset.slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=VEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=ASSEMBLY_METHOD,
            lang="en",
        )
        options: dict[str, str] = {}
        parties_of: dict[str, str] = {}
        order: dict[str, int] = {}
        for r in district_rows:
            if r.candidate in ("Total Votes", "Informal Votes"):
                continue
            parties_of[r.candidate] = r.party
            order[r.candidate] = r.order
        by_tokens = {tokens(c): c for c in order}
        listed = {n: match(n, by_tokens) for n, _, _ in detail.first}
        names = {c: person_name(n) for n, c in listed.items()}
        for candidate in sorted(order, key=lambda c: order[c]):
            id = options[candidate] = key("option", day, name, candidate)
            label = candidate
            pid = party(parties_of[candidate]) if parties_of[candidate] not in NO_PARTY else None
            pid = pid if pid in known else None
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="candidate",
                party_id=pid,
                ballot_position=order[candidate],
                ballot_label=label,
            )
            person = key("person", day, name, candidate)
            p.add("person", id=person)
            p.name(person, names[candidate], "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, name, candidate),
                person_id=person,
                option_id=id,
                role="head",
                ballot_name=label,
                member_of_party_id=pid,
                is_independent=parties_of[candidate] in NO_PARTY,
            )
        named = [c for n, c in listed.items() if tokens(n) <= tokens(detail.elected)]
        if len(named) != 1:
            raise ValueError(f"{year} {name}: elected member {detail.elected!r} matches {named}")
        winner = named[0]
        final = {match(n, by_tokens) for n, _, _ in detail.final} or set(order)
        if winner not in final:
            raise ValueError(f"{year} {name}: elected member is not among the final candidates")
        for candidate, id in options.items():
            kind = "elected" if candidate == winner else "lost" if candidate in final else "eliminated"
            p.add("outcome", result_set_id=result_set, option_id=id, kind=kind)
        for n, _, votes in detail.first:
            p.add("declared", result_set_id=result_set, option_id=options[listed[n]], measure=1, value=votes)
        p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=detail.formal)
        p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=detail.informal)
        p.add("declared", result_set_id=result_set, option_id=None, measure=3, value=detail.formal + detail.informal)
        valid: Counter[str] = Counter()
        bad: dict[str, int] = {}
        total: dict[str, int] = {}
        for r in district_rows:
            unit = unit_of(name, r.place)
            if r.candidate == "Informal Votes":
                bad[unit] = r.votes
            elif r.candidate == "Total Votes":
                total[unit] = r.votes
            else:
                values.add(result_set, unit, options[r.candidate], 1, r.votes)
                valid[unit] += r.votes
        for unit, n in valid.items():
            values.add(result_set, unit, None, 4, n)
            values.add(result_set, unit, None, 5, bad[unit])
            values.add(result_set, unit, None, 3, total[unit])
        roll = key("unit", day, name, "roll")
        p.add(
            "reporting_unit",
            id=roll,
            polity_id=AUSTRALIA,
            scheme_code="au-vic.voting_centre",
            code=f"{day}:{name}:ROLL",
            area_relation="within",
            area_id=area("au-vic.district", f"{config.vintage}:{name}"),
            kind="aggregate",
        )
        p.name(roll, f"{name}: electoral roll", "en", "official")
        values.add(result_set, roll, None, 2, detail.enrolment)
    council(p, values, year, unit_of)
    p.frame("result_value", values.frame())
    return p


def council(p: Package, values: Values, year: int, unit_of: Callable[[str, str], str]) -> None:
    config = YEARS[year]
    day = config.day
    election_id = key("election", day)
    path = f"au/vic/{day}-general"
    known = {id for id in (party(n) for n in parties([year]).values()) if _known(id)}
    for slug in region_slugs(year):
        p.uses(raw.cached(SOURCE, region_url(year, slug)))
        page_ = region_page(year, slug)
        sheet = received(year, slug)
        name = sheet.name
        zone = area("au-vic.region", f"{config.vintage}:{name}")
        contest = key("contest", day, "council", name)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=COUNCIL,
            district_area_id=zone,
            magnitude=5,
            round_number=1,
            electoral_system_id=COUNCIL_SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, name, "en", "official")
        p.add("slug", path=f"{path}/council-{changeset.slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", contest)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=VEC,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=COUNCIL_METHOD,
            lang="en",
        )
        by_tokens = {tokens(c): c for c in sheet.order}
        parties_of = {n: pt for n, pt, _ in page_.first}
        listed = {n: match(n, by_tokens) for n, _, _ in page_.first}
        if len(set(listed.values())) != len(listed) or set(listed.values()) != set(sheet.order):
            raise ValueError(f"{year} {name}: the region page and the votes received sheet list different candidates")
        winners = {match(n, by_tokens) for n in page_.elected}
        options: dict[str, str] = {}
        for position, candidate in enumerate(sheet.order, 1):
            id = options[candidate] = key("option", day, "council", name, candidate)
            label = next(n for n, c in listed.items() if c == candidate)
            party_name = parties_of[label]
            pid = party(party_name) if party_name not in NO_PARTY else None
            pid = pid if pid in known else None
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="candidate",
                party_id=pid,
                ballot_position=position,
                ballot_label=label,
            )
            person = key("person", day, "council", name, candidate)
            p.add("person", id=person)
            p.name(person, person_name(label), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, "council", name, candidate),
                person_id=person,
                option_id=id,
                role="head",
                ballot_name=label,
                member_of_party_id=pid,
                is_independent=party_name in NO_PARTY,
            )
            p.add("outcome", result_set_id=result_set, option_id=id, kind="elected" if candidate in winners else "lost")
        for n, _, votes in page_.first:
            p.add("declared", result_set_id=result_set, option_id=options[listed[n]], measure=1, value=votes)
        p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=page_.formal)
        p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=page_.informal)
        p.add("declared", result_set_id=result_set, option_id=None, measure=3, value=page_.formal + page_.informal)
        if sum(c.formal for c in sheet.cells) != sheet.formal:
            raise ValueError(f"{year} {name}: the voting centres do not add up to the region total")
        for cell in sheet.cells:
            unit = unit_of(cell.district, cell.place)
            for candidate, votes in cell.votes.items():
                values.add(result_set, unit, options[candidate], 1, votes)
            values.add(result_set, unit, None, 4, cell.formal)
            values.add(result_set, unit, None, 5, cell.informal)
            values.add(result_set, unit, None, 3, cell.total)
        roll = key("unit", day, name, "roll")
        p.add(
            "reporting_unit",
            id=roll,
            polity_id=AUSTRALIA,
            scheme_code="au-vic.voting_centre",
            code=f"{day}:{name}:ROLL",
            area_relation="within",
            area_id=zone,
            kind="aggregate",
        )
        p.name(roll, f"{name}: electoral roll", "en", "official")
        values.add(result_set, roll, None, 2, page_.enrolment)


def days() -> list[date]:
    return [config.day for config in YEARS.values()]


def packages() -> list[str]:
    from . import vec_by

    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *(f"{SOURCE}/{day}" for day in days()),
        *vec_by.packages(),
    ]


def parse() -> None:
    from . import vec_by

    years = sorted(YEARS)
    geo(years, vec_by.districts(), vec_by.files()).write()
    registry(years, vec_by.parties()).write()
    for year in years:
        election(year).write()
    for day in vec_by.days():
        vec_by.election(day).write()


def emit() -> None:
    package.run("load", packages())
