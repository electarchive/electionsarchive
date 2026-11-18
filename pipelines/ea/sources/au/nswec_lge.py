"""NSW local government elections of 2012, 2016, and 2017, councillor first preferences by voting venue.

The commission's results pages for each election publish, for every councillor contest (a council, or each ward of a
council with wards), a report of the first preferences at each polling place, pre-poll venue, and declaration vote
type. The 2016 and 2017 pages are one HTML page per venue; the 2012 pages are one PDF per contest with a page per venue.
Each contest also has a report of the total votes of each candidate and of the formal and informal ballots, which the
venue figures are checked against, and a summary page that names the councillors declared elected."""

import html
import re
import subprocess
from datetime import date
from typing import NamedTuple

from ea import raw
from ea.package import Package
from ea.sources.au.nswec import (
    AUSTRALIA,
    COUNCIL_SYSTEM,
    COUNTRY,
    NSW,
    NSWEC,
    PAST,
    SOURCE,
    Values,
    area,
    key,
    local_party_code,
    unit_kind,
)
from ea.sources.au.nswec_lg2008 import norm, number, proper, slug

DAYS = {2012: date(2012, 9, 8), 2016: date(2016, 9, 10), 2017: date(2017, 9, 9)}
INDEX = {
    2012: f"{PAST}/LGE2012/lge-index.htm",
    2016: f"{PAST}/LGE2016/lge-index.htm",
    2017: f"{PAST}/LGE2017/results/index.htm",
}
OTHER_CONTESTS = {"mayoral", "poll", "referendum", "mayor"}

METHOD = (
    "First preference votes for the councillor election of one council, or of one ward of a council with wards, "
    "from the NSW Electoral Commission's results pages for the %s New South Wales local government elections. "
    "The commission publishes the votes at each polling place, pre-poll venue, and declaration vote type, and the "
    "figures are stored for each of them. A voter marks a group above the line or candidates below the line: each "
    "group's above-the-line votes are stored on the group's list option and each candidate's first preferences on the "
    "candidate's option, so the two together add up to the formal votes. Where the contest has no groups, only "
    "candidates are counted. The commission's summary page declares the candidates elected, which are stored as "
    "outcomes; the distribution of preferences is not stored here. The set is the commission's final count."
)
TACIT_NOTE = (
    "The candidates nominated did not outnumber the vacancies, so each was declared elected unopposed and no votes "
    "were cast."
)


def base(year: int) -> str:
    return f"{PAST}/LGE{year}"


def read(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf-8-sig", errors="replace")


def text_of(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).replace("\xa0", " ").strip()


def cells(page: str) -> list[list[str]]:
    """The text of the cells of each row of each table of a page."""
    return [
        [text_of(c) for c in re.findall(r"(?s)<t[dh][^>]*>(.*?)</t[dh]>", tr)]
        for tr in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", page)
    ]


class Entry(NamedTuple):
    group: str  # the group's letter, or "" for an ungrouped candidate
    kind: str  # "group" or "candidate"
    label: str  # the candidate's name as printed, or the group's name (often empty)


class Count(NamedTuple):
    entries: list[Entry]
    votes: list[int]
    formal: int
    informal: int


class Venue(NamedTuple):
    name: str
    count: Count


class Reported(NamedTuple):
    entries: list[Entry]
    votes: list[int]  # the contest's total of each entry
    formal: int
    informal: int


class Contest(NamedTuple):
    year: int
    council: str
    ward: str | None
    code: str  # unique within the election
    vacancies: int
    elected: list[str]
    entries: list[Entry]
    venues: list[Venue]
    reported: Reported | None
    urls: list[str]


# Fetching, 2016 and 2017 --------------------------------------------------------------------------------------------


def council_pages(year: int) -> list[str]:
    seen = {"results", "schedule", "status", "home.htm", "lge-index.htm"}
    found = []
    for m in re.finditer(rf'href="(?:{re.escape(PAST)})?/LGE{year}/([a-z0-9-]+)/index\.htm"', read(INDEX[year])):
        if m[1] not in seen:
            seen.add(m[1])
            found.append(f"{base(year)}/{m[1]}/index.htm")
    return found


def html_bases(year: int, council: str) -> list[str]:
    """The folders of the councillor contests of a council: its own, or one for each ward."""
    page = read(council)
    slug_ = council.rsplit("/", 2)[1]
    folders = set()
    for m in re.finditer(rf'href="/LGE{year}/{slug_}/((?:[^"/]+/)?)(?:summary|index)\.htm"', page):
        folders.add(m[1])
    folders.discard("")
    return [f"{base(year)}/{slug_}/{f}" for f in sorted(folders) if f.rstrip("/") not in OTHER_CONTESTS]


def venue_links(folder: str) -> list[tuple[str, str]]:
    found = []
    for m in re.finditer(
        r'(?s)<a[^>]*href="(/LGE\d+/[^"]*?/pp_[^"/]+\.htm)"[^>]*>(.*?)</a>', read(f"{folder}pp_list.htm")
    ):
        found.append((f"{PAST}{m[1]}", text_of(m[2])))
    return found


REPORTS = ("summary", "final-results", "pp_list", "by_vote_type_and_formality", "fp_by_grp_and_candidate_by_vote_type")


def fetch_html(year: int) -> list[str]:
    """Fetch every page of every councillor contest of a 2016 or 2017 election and return the folders."""
    raw.fetch(SOURCE, INDEX[year], suffix=".htm")
    councils = council_pages(year)
    if len(raw.fetch_many(SOURCE, councils, suffix=".htm")) != len(councils):
        raise RuntimeError("a council page could not be fetched")
    folders = [f for c in councils for f in html_bases(year, c)]
    pages = [f"{f}{r}.htm" for f in folders for r in REPORTS]
    got = raw.fetch_many(SOURCE, pages, suffix=".htm")
    folders = [f for f in folders if f"{f}summary.htm" in got]
    venues = sorted({u for f in folders if f"{f}pp_list.htm" in got for u, _ in venue_links(f)})
    if len(raw.fetch_many(SOURCE, venues, suffix=".htm")) != len(venues):
        raise RuntimeError("a venue page could not be fetched")
    return folders


# Reading, 2016 and 2017 ----------------------------------------------------------------------------------------------


class Summary(NamedTuple):
    council: str
    ward: str | None
    vacancies: int | None  # None when the contest was uncontested
    elected: list[str]


def html_summary(url: str) -> Summary:
    page = read(url)
    head = re.search(r"<h1>([^<]+)</h1>\s*<h2>\s*Councillor Contest", page)
    if head is None:
        raise ValueError(f"{url}: no contest heading")
    council, _, ward = text_of(head.group(1)).partition(" - ")
    elected = [text_of(m) for m in re.findall(r'<span class="candidate-name">(.*?)</span>', page)]
    seats = re.search(
        r"There (?:was|were|are|is)\s+([\d,]+)\s+Councillors?\s+to be elected from\s+([\d,]+)", text_of(page)
    )
    if seats is None and "The election was uncontested" not in page:
        raise ValueError(f"{url}: no vacancies")
    return Summary(council, ward or None, number(seats.group(1)) if seats else None, elected)


def sort_key(entries: list[Entry]) -> list[tuple[str, str, str]]:
    return [(e.group, e.kind, norm(e.label) if e.kind == "candidate" else "") for e in entries]


def parse_rows(rows: list[list[str]], offset: int = 0) -> tuple[list[Entry], list[list[int]], dict[str, list[int]]]:
    """The entries of a report that lists groups and candidates, with the figures after the `offset`+2 label cells,
    and the footer rows by label."""
    entries: list[Entry] = []
    figures: list[list[int]] = []
    footer: dict[str, list[int]] = {}
    group = ""
    grouped = any(r and r[0] == "Group" for r in rows)
    for r in rows:
        if not grouped:
            r = ["", *r]  # a contest with no groups has no group column
        if len(r) < 3:
            continue
        letter, label, nums = r[0], r[1], r[2:]
        values = [number(x) if x else 0 for x in nums if not x or re.fullmatch(r"[\d,]+", x)]
        if letter and re.fullmatch(r"[A-Z]{1,3}", letter):
            group = letter
            entries.append(Entry(group, "group", label))
            figures.append(values)
        elif label == "UNGROUPED CANDIDATES":
            group = ""
        elif label == "Group Total":
            continue
        elif label.startswith(("Total", "Informal")):
            footer[label] = values
        elif label and values:
            entries.append(Entry(group, "candidate", label))
            figures.append(values)
    return entries, figures, footer


def html_venue(url: str) -> Count:
    entries, figures, footer = parse_rows(cells(read(url)))
    formal, informal = footer["Total Formal Votes"][0], footer["Informal Ballot Papers"][0]
    return Count(entries, [f[0] for f in figures], formal, informal)


def html_reported(folder: str) -> Reported:
    formality = cells(read(f"{folder}by_vote_type_and_formality.htm"))
    total = next(r for r in formality if r and r[0] == "Total Votes")
    entries, figures, _ = parse_rows(cells(read(f"{folder}fp_by_grp_and_candidate_by_vote_type.htm")))
    if any(f[0] + f[1] != f[2] for f in figures):
        raise ValueError(f"{folder}: ordinary and declaration votes do not add up")
    return Reported(entries, [f[2] for f in figures], number(total[1]), number(total[2]))


def code_of(year: int, folder: str) -> str:
    return folder.removeprefix(f"{base(year)}/").rstrip("/").replace("/", ":")


def html_contest(year: int, folder: str) -> Contest:
    summary = html_summary(f"{folder}summary.htm")
    urls = [f"{folder}summary.htm"]
    code = code_of(year, folder)
    try:
        links = venue_links(folder)
    except LookupError:
        links = []
    if summary.vacancies is None or not links:
        if summary.vacancies is not None or not summary.elected:
            raise ValueError(f"{folder}: a contested election with no venues")
        return Contest(
            year, summary.council, summary.ward, code, len(summary.elected), summary.elected, [], [], None, urls
        )
    urls.append(f"{folder}pp_list.htm")
    venues: list[Venue] = []
    for url, name in links:
        urls.append(url)
        venues.append(Venue(name, html_venue(url)))
    entries = venues[0].count.entries
    for v in venues:
        if sort_key(v.count.entries) != sort_key(entries):
            raise ValueError(f"{folder}: {v.name} lists different candidates")
    urls += [f"{folder}by_vote_type_and_formality.htm", f"{folder}fp_by_grp_and_candidate_by_vote_type.htm"]
    reported = html_reported(folder)
    if sort_key(reported.entries) != sort_key(entries):
        raise ValueError(f"{folder}: the totals report lists different candidates")
    return Contest(
        year,
        summary.council,
        summary.ward,
        code,
        max(summary.vacancies, len(summary.elected)),  # one summary page misprints 11 vacancies as 1
        summary.elected,
        entries,
        venues,
        reported,
        urls,
    )


# Fetching, 2012 ----------------------------------------------------------------------------------------------------


def contest_pages_2012(council: str) -> list[str]:
    slug_ = council.rsplit("/", 2)[1]
    found = []
    for m in re.finditer(rf'href="/LGE2012/{slug_}/([^"/]+)/summary/index\.htm"', read(council)):
        if m[1] not in OTHER_CONTESTS:
            found.append(f"{base(2012)}/{slug_}/{m[1]}/summary/index.htm")
    return list(dict.fromkeys(found))


PDF_LINK = re.compile(r'href="(/LGE2012/Results/[^"]+?/(0[348]) - [^"/]+\.pdf)"')


def pdf_links(final: str) -> dict[str, str]:
    return {m[2]: PAST + m[1].replace(" ", "%20") for m in PDF_LINK.finditer(read(final))}


def final_page(summary: str) -> str:
    return summary.replace("/summary/index.htm", "/final-results/index.htm")


def fetch_pdf(year: int) -> list[str]:
    raw.fetch(SOURCE, INDEX[year], suffix=".htm")
    councils = council_pages(year)
    if len(raw.fetch_many(SOURCE, councils, suffix=".htm")) != len(councils):
        raise RuntimeError("a council page could not be fetched")
    summaries = [s for c in councils for s in contest_pages_2012(c)]
    got = raw.fetch_many(SOURCE, [x for s in summaries for x in (s, final_page(s))], suffix=".htm")
    summaries = [s for s in summaries if s in got]
    files = sorted({u for s in summaries if final_page(s) in got for u in pdf_links(final_page(s)).values()})
    if len(raw.fetch_many(SOURCE, files, suffix=".pdf")) != len(files):
        raise RuntimeError("a report could not be fetched")
    return summaries


# Reading, 2012 -----------------------------------------------------------------------------------------------------


def pdf_lines(url: str) -> list[str]:
    path = raw.cached(SOURCE, url).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout
    return out.decode("utf-8", errors="replace").splitlines()


SKIP = ("Event:", "Work Location:", "Area:", "Date of Election", "Date Generated", "Group ", "First Preference")
INTEGER = re.compile(r"[\d,]+")


def pdf_entries(lines: list[str]) -> tuple[list[Entry], list[int], dict[str, int]]:
    """The groups and candidates of a report page or pages, with the votes of each, and the footer figures."""
    entries: list[Entry] = []
    votes: list[int] = []
    footer: dict[str, int] = {}
    group = ""
    for line in lines:
        if not line.strip() or line.startswith(SKIP) or line.strip().startswith(SKIP[:5]):
            continue
        fields = re.split(r"\s{2,}", line.strip())
        label = fields[0]
        total = re.match(r"(FORMAL|INFORMAL|TOTAL) VOTES:?\s*([\d,]+)", line.strip())
        if total:
            footer[total[1]] = number(total[2])
            continue
        if label in ("Group Total", "UNGROUPED CANDIDATES"):
            if label == "UNGROUPED CANDIDATES":
                group = ""
            continue
        whole = [f for f in fields[1:] if INTEGER.fullmatch(f)]
        if not whole and not (len(fields) == 1 and INTEGER.fullmatch(label)):
            continue
        if line[0] != " " and re.fullmatch(r"[A-Z]{1,3}", label):
            group = label
            title = (
                fields[1]
                if len(fields) > 1 and not INTEGER.fullmatch(fields[1]) and not re.fullmatch(r"[\d.]+", fields[1])
                else ""
            )
            entries.append(Entry(group, "group", title))
        else:
            entries.append(Entry(group, "candidate", label))
        votes.append(number(whole[-1]))
    return entries, votes, footer


def pdf_venues(lines: list[str]) -> list[Venue]:
    chunks: list[tuple[str, list[str]]] = []
    for line in lines:
        m = re.search(r"Polling Place / Dec Type:\s*(.+?)\s*$", line)
        if m:
            chunks.append((m[1], []))
        elif chunks:
            chunks[-1][1].append(line)
    venues = []
    seen: dict[str, int] = {}
    for name, body in chunks:
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name} ({seen[name]})"  # the report cuts long venue names, so two venues can print alike
        entries, votes, footer = pdf_entries(body)
        venues.append(Venue(name, Count(entries, votes, footer["FORMAL"], footer["INFORMAL"])))
    return venues


def pdf_formality(lines: list[str]) -> tuple[int, int]:
    for line in lines:
        fields = re.split(r"\s{2,}", line.strip())
        if fields[0] == "TOTAL" and len(fields) >= 4:
            return number(fields[1]), number(fields[2])
    raise ValueError("no total of formal and informal votes")


def pdf_summary(url: str) -> tuple[Summary, int]:
    page = read(url)
    head = re.search(r'<h1 id="lga_name">([^<]+)</h1>', page)
    seats = re.search(
        r"There (?:was|were|are|is)\s+([\d,]+)\s+Councillors?\s+to be elected from\s+([\d,]+)", text_of(page)
    )
    if head is None or seats is None:
        raise ValueError(f"{url}: no contest heading or vacancies")
    council, _, ward = text_of(head.group(1)).partition(" - ")
    elected = [text_of(m) for m in re.findall(r'<span class="candidate_name">(.*?)</span>', page)]
    return Summary(council, ward or None, number(seats.group(1)), elected), number(seats.group(2))


def pdf_contest(year: int, summary_url: str) -> Contest:
    summary, candidates = pdf_summary(summary_url)
    code = summary_url.removeprefix(f"{base(year)}/").removesuffix("/summary/index.htm").replace("/", ":")
    urls = [summary_url]
    final = final_page(summary_url)
    try:
        links = pdf_links(final)
    except LookupError:
        links = {}
    if not links:
        if candidates > (summary.vacancies or 0) or len(summary.elected) != candidates:
            raise ValueError(f"{summary_url}: a contested election with no reports")
        return Contest(
            year, summary.council, summary.ward, code, len(summary.elected), summary.elected, [], [], None, urls
        )
    urls += [final, *links.values()]
    venues = pdf_venues(pdf_lines(links["08"]))
    entries = venues[0].count.entries
    for v in venues:
        if sort_key(v.count.entries) != sort_key(entries):
            raise ValueError(f"{summary_url}: {v.name} lists different candidates")
    names = [v.name for v in venues]
    if len(set(names)) != len(names):
        raise ValueError(f"{summary_url}: repeated venue names")
    rentries, rvotes, _ = pdf_entries(pdf_lines(links["03"]))
    formal, informal = pdf_formality(pdf_lines(links["04"]))
    reported = Reported(rentries, rvotes, formal, informal)
    return Contest(
        year,
        summary.council,
        summary.ward,
        code,
        summary.vacancies or 0,
        summary.elected,
        entries,
        venues,
        reported,
        urls,
    )


# Names -------------------------------------------------------------------------------------------------------------


def surname_token(word: str) -> bool:
    return bool(re.fullmatch(r"[A-Z'\-]+|Mc[A-Z]+|Mac[A-Z]{3,}", word))


def split_ballot(label: str) -> tuple[str, str]:
    """The surname and given names of a ballot name, which prints the surname first in capitals."""
    words = label.split()
    n = 0
    while n < len(words) - 1 and surname_token(words[n]):
        n += 1
    n = max(n, 1)
    return " ".join(words[:n]), " ".join(words[n:])


def split_declared(name: str) -> tuple[str, str]:
    """The surname and given names of a name the summary page prints, given names first and the surname in capitals."""
    words = re.sub(r"\s*\([^)]*\)\s*$", "", name).split()
    n = len(words)
    while n > 1 and surname_token(words[n - 1]):
        n -= 1
    if n == len(words):
        n = len(words) - 1
    return " ".join(words[n:]), " ".join(words[:n])


def person_name(surname: str, given: str) -> str:
    return f"{given} {proper(surname)}".strip()


def elected_rows(entries: list[Entry], elected: list[str], where: str) -> dict[int, str]:
    """The index of each candidate the summary page declares elected, with the name it prints."""
    found: dict[int, str] = {}
    for name in elected:
        surname, given = split_declared(name)
        options = [
            i
            for i, e in enumerate(entries)
            if e.kind == "candidate" and norm(split_ballot(e.label)[0]) == norm(surname)
        ]
        first = norm(given.split()[0]) if given else ""
        same = [
            i
            for i in options
            if norm(split_ballot(entries[i].label)[1].split()[0] if split_ballot(entries[i].label)[1] else "") == first
        ]
        hits = [
            i
            for i in (same or [i for i in options if norm(split_ballot(entries[i].label)[1])[:1] == first[:1]])
            if i not in found
        ]
        if len(hits) != 1:
            raise ValueError(f"{where}: {name} matches {len(hits)} candidates")
        found[hits[0]] = name
    return found


def party_name(title: str) -> str:
    if not title.isupper():
        return title
    return re.sub(r"[A-Za-z][A-Za-z']*", lambda m: m.group(0).capitalize(), title)


# Packages ----------------------------------------------------------------------------------------------------------


def tag(year: int) -> str:
    return f"lg{year}"


def council_slug(c: Contest) -> str:
    return c.code.split(":")[0]


def contest_code(c: Contest) -> str:
    return c.code


def council_area(year: int, slug_: str) -> str:
    return area("au-nsw.council", f"{year}:{slug_}")


def ward_area(year: int, code: str) -> str:
    return area("au-nsw.ward", f"{year}:{code}")


def contest_area(c: Contest) -> str:
    return council_area(c.year, council_slug(c)) if c.ward is None else ward_area(c.year, c.code)


def contest_label(c: Contest) -> str:
    return c.council if c.ward is None else f"{c.council}, {c.ward}"


def parties(items: list[Contest]) -> dict[str, str]:
    found: dict[str, str] = {}
    for c in items:
        for e in c.entries:
            if e.kind == "group" and e.label:
                found.setdefault(local_party_code(e.label), party_name(e.label))
    return found


def geo(year: int, items: list[Contest]) -> Package:
    day = DAYS[year]
    councils = {council_slug(c): c.council for c in items}
    wards = {c.code: c for c in items if c.ward is not None}
    p = Package(
        f"{SOURCE}/geo-{tag(year)}",
        SOURCE,
        f"The {len(councils)} councils and {len(wards)} wards of the {day:%-d %B %Y} New South Wales local government "
        "elections, as the NSW Electoral Commission names them",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/geo-lg2008"],
    )
    p.external |= {AUSTRALIA, COUNTRY}
    p.artifact(raw.cached(SOURCE, INDEX[year]), NSWEC)
    for scheme in ("au-nsw.council", "au-nsw.ward"):
        p.add("boundary_set", id=key("boundary_set", scheme, year), scheme_code=scheme, vintage=str(year), as_of=day)
    for slug_, name in sorted(councils.items()):
        id = council_area(year, slug_)
        p.add("area", id=id, scheme_code="au-nsw.council")
        p.add("area_code", area_id=id, code=f"{year}:{slug_}")
        p.add("area_parent", area_id=id, parent_id=NSW)
        p.name(id, name, "en", "official")
    for code, c in sorted(wards.items()):
        id = ward_area(year, code)
        p.add("area", id=id, scheme_code="au-nsw.ward")
        p.add("area_code", area_id=id, code=f"{year}:{code}")
        p.add("area_parent", area_id=id, parent_id=council_area(year, council_slug(c)))
        p.name(id, f"{c.council}, {c.ward}", "en", "official")
    return p


def registry(year: int, items: list[Contest]) -> Package:
    day = DAYS[year]
    found = parties(items)
    councils = {council_slug(c): c.council for c in items}
    p = Package(
        f"{SOURCE}/registry-{tag(year)}",
        SOURCE,
        f"The {len(councils)} councils and {len(found)} named groups of the {day:%-d %B %Y} New South Wales local "
        "government elections, as the NSW Electoral Commission names them",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/geo-{tag(year)}"],
    )
    p.external |= {AUSTRALIA}
    for slug_, name in sorted(councils.items()):
        id = key("body", "council", f"{year}:{slug_}")
        p.add("body", id=id, polity_id=AUSTRALIA, kind="council")
        p.name(id, name, "en", "official")
    for code, name in sorted(found.items()):
        id = key("party", tag(year), code)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, name, "en", "official")
    return p


def election(year: int, items: list[Contest]) -> Package:
    day, t = DAYS[year], tag(year)
    path = f"au/nsw/{day}-local"
    p = Package(
        f"{SOURCE}/{day}-{t}",
        SOURCE,
        f"The {day:%-d %B %Y} New South Wales local government elections, councillor first preferences by voting "
        "venue, from the NSW Electoral Commission",
        depends=[
            f"{SOURCE}/geo",
            f"{SOURCE}/registry",
            f"{SOURCE}/geo-{t}",
            f"{SOURCE}/registry-{t}",
        ],
    )
    p.external |= {AUSTRALIA}
    election_id = key("election", day, t)
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="local",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{year} New South Wales local government elections", "en", "official")
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values = Values()
    for c in sorted(items, key=contest_label):
        code, slug_ = c.code, council_slug(c)
        for url in c.urls:
            p.artifact(raw.cached(SOURCE, url), NSWEC)
        tacit = not c.venues
        contest = key("contest", day, t, code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=key("body", "council", f"{year}:{slug_}"),
            district_area_id=contest_area(c),
            magnitude=c.vacancies,
            round_number=1,
            electoral_system_id=COUNCIL_SYSTEM,
            default_result_kind="tacit" if tacit else "counted",
        )
        p.name(contest, f"{contest_label(c)} councillors", "en", "official")
        slug_path = f"{path}/council-{slug_}" + (f"/ward-{slug(c.ward)}" if c.ward else "")
        p.add("slug", path=slug_path, entity_id=contest, is_current=True)
        entries = c.entries or [Entry("", "candidate", name) for name in c.elected]
        elected = {} if tacit else elected_rows(entries, c.elected, contest_label(c))
        group_option: dict[str, str] = {}
        group_party: dict[str, str | None] = {}
        for e in entries:
            if e.kind == "group":
                group_party[e.group] = key("party", t, local_party_code(e.label)) if e.label else None
        options: list[str] = []
        for i, e in enumerate(entries, 1):
            if e.kind == "group":
                id = group_option[e.group] = key("option", day, t, code, "group", e.group)
                p.add(
                    "contest_option",
                    id=id,
                    contest_id=contest,
                    kind="list",
                    party_id=group_party[e.group],
                    ballot_position=i,
                    ballot_label=f"Group {e.group}",
                )
                options.append(id)
                continue
            id = key("option", day, t, code, "candidate", i, e.label)
            pid = group_party.get(e.group) if e.group else None
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="candidate",
                parent_option_id=group_option.get(e.group) if e.group else None,
                party_id=pid,
                ballot_position=i,
                ballot_label=e.label,
            )
            who = key("person", day, t, code, i, e.label)
            surname, given = split_declared(e.label) if tacit else split_ballot(e.label)
            p.add("person", id=who)
            p.name(who, person_name(surname, given), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", day, t, code, i, e.label),
                person_id=who,
                option_id=id,
                role="head",
                ballot_name=e.label,
                member_of_party_id=pid,
                is_independent=pid is None,
            )
            options.append(id)
        result_set = key("result_set", contest, "official")
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="tacit" if tacit else "counted",
            source_id=NSWEC,
            status="active",
            is_default=True,
            completeness=None if tacit else 1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=TACIT_NOTE if tacit else METHOD % f"{day:%-d %B %Y}",
            lang="en",
        )
        for i in sorted(range(len(options)) if tacit else elected):
            p.add("outcome", result_set_id=result_set, option_id=options[i], kind="elected")
        if tacit or c.reported is None:
            continue
        for option, votes in zip(options, c.reported.votes, strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=votes)
        p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=c.reported.formal)
        p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=c.reported.informal)
        for v in c.venues:
            kind = unit_kind("", v.name)
            id = key("unit", day, t, code, v.name)
            p.add(
                "reporting_unit",
                id=id,
                polity_id=AUSTRALIA,
                scheme_code="au-nsw.council_venue",
                code=f"{day}:{year}:{code}:{v.name}",
                area_relation="within",
                area_id=contest_area(c),
                kind=kind,
            )
            p.name(id, v.name if kind == "standard" else f"{contest_label(c)}: {v.name}", "en", "official")
            for option, votes in zip(options, v.count.votes, strict=True):
                values.add(result_set, id, option, 1, votes)
            values.add(result_set, id, None, 4, v.count.formal)
            values.add(result_set, id, None, 5, v.count.informal)
            values.add(result_set, id, None, 3, v.count.formal + v.count.informal)
    p.frame("result_value", values.frame())
    return p


# Running -----------------------------------------------------------------------------------------------------------


def fetch() -> None:
    fetch_pdf(2012)
    fetch_html(2016)
    fetch_html(2017)


def contests(year: int) -> list[Contest]:
    if year == 2012:
        pages = [s for c in council_pages(year) for s in contest_pages_2012(c)]
        return [pdf_contest(year, s) for s in pages if s in raw.keys(SOURCE)]
    found = []
    for c in council_pages(year):
        for folder in html_bases(year, c):
            if f"{folder}summary.htm" in raw.keys(SOURCE):
                found.append(html_contest(year, folder))
    return found


def packages() -> list[str]:
    return [
        f"{SOURCE}/{kind}-{tag(y)}" if kind != "election" else f"{SOURCE}/{DAYS[y]}-{tag(y)}"
        for y in sorted(DAYS)
        for kind in ("geo", "registry", "election")
    ]


def parse() -> None:
    for year in sorted(DAYS):
        items = contests(year)
        geo(year, items).write()
        registry(year, items).write()
        election(year, items).write()
