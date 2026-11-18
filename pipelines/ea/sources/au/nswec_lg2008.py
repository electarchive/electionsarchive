"""NSW local government elections of 13 September 2008, councillor first preferences.

The commission's results pages for the election publish, for each council (or each ward of a council with wards), a
summary page and a comma-delimited file of the count. A council with a later count has two files: the election-night
file (votes by polling place) and the post-election-night file (the check count plus declaration votes, the count that
the declaration of the poll starts from). The summary page prints each count as a column, with the member of each
position declared elected."""

import html
import re
from collections import defaultdict
from datetime import date
from typing import NamedTuple

from ea import raw
from ea.package import Package
from ea.sources.au.nswec import (
    AUSTRALIA,
    COUNCIL_SYSTEM,
    COUNTRY,
    NO_PARTY,
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

DAY = date(2008, 9, 13)
BASE = f"{PAST}/LGE2008/"
INDEX = f"{BASE}lgeindex.htm"
PATH = f"au/nsw/{DAY}-local"
VOTE_TYPES = ("Dec. Inst/Pre-Poll", "Postal", "Section/Silent")

METHOD = (
    "First preference votes for the councillor election of one council, or of one ward of a council with wards, "
    "from the NSW Electoral Commission's results pages for the 13 September 2008 local government elections. %s "
    "A voter marks a group above the line or candidates below the line: each group's above-the-line votes are "
    "stored on the group's list option and each candidate's first preferences on the candidate's option, so the "
    "two together add up to the formal votes. Where the contest has no groups, only candidates are counted. The "
    "commission's summary page declares the candidates elected, which are stored as outcomes; the distribution of "
    "preferences is not stored here."
)
NIGHT_SOLE = (
    "The set is the election-night count, votes by polling place, which is the only count the commission publishes "
    "for the contest: its summary page has one count column, headed Election Night, and no postal, "
    "declared-institution, pre-poll, or section votes. The set is therefore stored as preliminary."
)
NIGHT_EARLIER = (
    "The set is the election-night count, votes by polling place, headed Election Night Results. The contest's "
    "count after election night is in its official set, which is the default for the contest."
)
FINAL_NOTE = (
    "The set is the commission's count after election night, headed Post - Election Night Results: the check count "
    "of the ordinary votes taken at each polling place plus the declaration votes (declared-institution and "
    "pre-poll, postal, and section or silent votes), which the commission reports once per contest. The declaration "
    "of the poll starts from this count."
)
TACIT_NOTE = (
    "The candidates nominated did not outnumber the vacancies, so each was declared elected unopposed on election "
    "day and no votes were cast."
)


def read(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf-8")


# Fetching --------------------------------------------------------------------------------------------------------


def absolute(href: str) -> str:
    return BASE + html.unescape(href)


def council_names() -> list[str]:
    names = re.findall(r'href="result\.([^."]+)\.html"', read(INDEX))
    return list(dict.fromkeys(names))


def council_url(name: str) -> str:
    return f"{BASE}result.{name}.html"


WARD_PAGE = re.compile(r'href="(result\.[^".]+\.council[0-9a-f]{4}\.html\?wardid=(\w+))">\s*([^<]+?)\s*<')
UNIT_PAGE = re.compile(
    r'href="(result\.[^".]+\.council\.(summary|electionnight|postelectionnight)[0-9a-f]{4}\.html\?'
    r'(?:wardid=(\w+)(?:&amp;|&)?)?)"'
)
CSV_LINK = re.compile(r'href="(result\.[^"]+\.csv(?:post)?[0-9a-f]{4}\.csv\?[^"]*)"')


def ward_pages(name: str) -> dict[str, tuple[str, str]]:
    """Each ward's id with the page of the ward and its name, from a council page that lists its wards."""
    return {m[2]: (absolute(m[1]), html.unescape(m[3])) for m in WARD_PAGE.finditer(read(council_url(name)))}


def unit_pages(page: str) -> dict[tuple[str, str], str]:
    return {(m[3] or "", m[2]): absolute(m[1]) for m in UNIT_PAGE.finditer(read(page))}


def fetch() -> None:
    raw.fetch(SOURCE, INDEX, suffix=".html")
    names = council_names()
    got = raw.fetch_many(SOURCE, [council_url(n) for n in names], suffix=".html")
    if len(got) != len(names):
        raise RuntimeError("a council page could not be fetched")
    wards = {w[0] for n in names for w in ward_pages(n).values()}
    if wards:
        raw.fetch_many(SOURCE, sorted(wards), suffix=".html")
    pages: list[str] = []
    for name in names:
        for page in [council_url(name), *(w[0] for w in ward_pages(name).values())]:
            pages += unit_pages(page).values()
    pages = sorted(set(pages))
    got = raw.fetch_many(SOURCE, pages, suffix=".html")
    if len(got) != len(pages):
        raise RuntimeError("a results page could not be fetched")
    files = sorted(
        {absolute(m[1]) for url in pages if "council.summary" not in url for m in CSV_LINK.finditer(read(url))}
    )
    got = raw.fetch_many(SOURCE, files, suffix=".csv")
    if len(got) != len(files):
        raise RuntimeError("a results file could not be fetched")


# Reading ---------------------------------------------------------------------------------------------------------


def number(text: str) -> int:
    return int(text.replace(",", ""))


def cells(page: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", page):
        rows.append(
            [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).replace("\xa0", " ").strip()
                for c in re.findall(r"(?s)<t[dh](?:[^>]*[^/>])?>(.*?)</t[dh]>|<t[dh][^>]*/>", tr)
                for c in [c]
            ]
        )
    return rows


def norm(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


class Row(NamedTuple):
    group: str
    label: str
    party: str
    votes: list[int | None]


class Summary(NamedTuple):
    council: str
    ward: str | None
    status: str
    vacancies: int | None
    stages: int
    rows: list[Row]  # votes: the first preferences of each count column
    formal: list[int]
    informal: list[int]
    elected: list[str]
    parties: dict[str, str]


def summary(url: str) -> Summary | None:
    page = read(url)
    crumbs = re.search(r'(?s)<div id="breadcrumbs">(.*?)</div>', page)
    if crumbs is None:
        raise ValueError(f"{url}: no breadcrumbs")
    parts = [re.sub(r"\s+", " ", html.unescape(p)).strip() for p in crumbs.group(1).split("&gt;")]
    if parts[-1] != "Summary" or parts[1] != "Council":
        raise ValueError(f"{url}: unexpected breadcrumbs {parts}")
    council, ward = parts[0], (parts[2] if len(parts) == 4 else None)
    if "Election is Deferred" in page:
        return None
    status = re.search(r"\(Status:\s*([^)]+?)\s*\)", page)
    vacancies = re.search(r"([\d,]+) candidates?\s+contesting\s+([\d,]+)\s+vacanc", page)
    if status is None or vacancies is None:
        raise ValueError(f"{url}: no status or vacancies")
    rows: list[Row] = []
    formal: list[int] = []
    informal: list[int] = []
    group = ""
    stages: int | None = None
    for r in cells(page):
        if not r or r[0] == "%":
            continue
        if r[0] in ("Candidate", "Group"):
            stages = len(r) - (3 if r[0] == "Group" else 2)
            continue
        if stages is None:
            continue
        if r[0] == "Total Formal Votes Counted":
            formal = [number(x) for x in r[1:] if re.fullmatch(r"[\d,]+", x)]
            continue
        if r[0] in ("Informal Votes", "Total Informal Votes"):
            informal = [number(x) for x in r[1:] if re.fullmatch(r"[\d,]+", x)]
            continue
        if r[0].startswith(("Total", "Progressive", "Election", "Check")):
            continue
        tail = r[2:]
        hit = re.fullmatch(r"([A-Z]+)\s*Group Votes", r[0])
        if len(r) > 2 and r[1] == "Group Votes" and re.fullmatch(r"[A-Z]+", r[0]):
            hit = re.fullmatch(r"([A-Z]+)(Group Votes)", r[0] + r[1])
            tail = r[3:]
        if hit:
            group = hit.group(1)
            rows.append(
                Row(
                    group, "Group Votes", "", [number(tail[2 * i + 1]) if tail[2 * i + 1] else 0 for i in range(stages)]
                )
            )
            continue
        if r[0] == "Ungrouped":
            group = ""
            continue
        if len(r) == 3 and not r[1] and not r[2]:
            continue  # the name of a group of an uncontested ward
        votes = [number(tail[2 * i + 1]) if len(tail) > 2 * i + 1 and tail[2 * i + 1] else None for i in range(stages)]
        rows.append(Row(group, r[0], r[1], votes))
    elected = [
        re.sub(r"\s+", " ", html.unescape(m)).strip()
        for m in re.findall(
            r"<p>([^<]+?)\s+(?:was\s+)?declared\s+elected(?:\s+to\s+position\s+\d+)?\s+on\s+\d+\s+\w+\s+\d{4}\s*</p>",
            page,
        )
    ]
    parties = {
        html.unescape(b).strip(): html.unescape(a).strip()
        for a, b in re.findall(r"displayPartyName\('([^']*)'\);\">([^<]+)</a>", page)
        if not re.fullmatch(r"[A-Z]", b.strip())
    }
    return Summary(
        council,
        ward,
        status.group(1),
        number(vacancies.group(2)),
        stages or 0,
        rows,
        formal,
        informal,
        elected,
        parties,
    )


class Table(NamedTuple):
    columns: list[str]
    rows: list[Row]  # votes: one per column, then the total
    formal: list[int]  # one per column, then the total
    informal: list[int | None]  # None where the file leaves a declaration vote type's figure blank
    cast: list[int | None]


def whole(values: list[int | None]) -> list[int]:
    return [v or 0 for v in values]


def csv_table(url: str) -> Table:
    import csv
    import io

    lines = list(csv.reader(io.StringIO(read(url))))
    if lines[1][0].strip() not in ("Election Night Results", "Post - Election Night Results"):
        raise ValueError(f"{url}: unexpected file {lines[1]}")
    head = lines[2]
    end = head.index("%")
    columns = head[3 : end - 1]
    if head[end - 1] not in ("Votes", "Total") or len(set(columns)) != len(columns):
        raise ValueError(f"{url}: unexpected header {head}")
    rows: list[Row] = []
    footer: dict[str, list[int | None]] = {}
    group = ""
    for line in lines[3:]:
        line = [*line, *[""] * (end + 1 - len(line))]
        label = line[1].strip()
        if line[0] == "Ungrouped":
            group = ""
            continue
        votes = [int(x) if x.strip() else None for x in line[3:end]]
        if not label and line[0].strip():
            group = line[0].strip()  # a group that shows no above-the-line votes
            rows.append(Row(group, "Group Votes", "", votes))
            continue
        if not label:
            continue
        if label.startswith(("Total", "Informal", "Other")):
            footer[label] = votes
            continue
        if label.endswith("Group Votes"):
            group = line[0].strip()
            rows.append(Row(group, "Group Votes", line[2].strip(), votes))
        else:
            rows.append(Row(group, label, line[2].strip(), votes))
    if any(footer.get("Other Ballots", [])):
        raise ValueError(f"{url}: other ballots")
    return Table(
        columns, rows, whole(footer["Total Formal Votes"]), footer["Informal Votes"], footer["Total Votes Polled"]
    )


ROW_NAMES = {
    "Dec Inst/Pre-Poll Votes": "Dec. Inst/Pre-Poll",
    "Postal Votes": "Postal",
    "Section/Silent Votes": "Section/Silent",
}


def html_table(url: str) -> Table | None:
    """The table of a count page for a contest without groups: a row for each polling place and declaration vote
    type, a column for each candidate."""
    rows = cells(read(url))
    if not rows or rows[0][0] != "Polling Place":
        return None
    head = rows[0]
    labels: list[tuple[str, str]] = []
    for h in head[1:-3]:
        hit = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", h)
        labels.append((hit.group(1), hit.group(2)) if hit else (h, ""))
    columns: list[str] = []
    data: list[list[int]] = []
    total: list[int] | None = None
    ordinary: list[int] | None = None
    for r in rows[1:]:
        name = r[0]
        if name.startswith("%"):
            continue
        values = [number(x) for x in r[1:]]
        if name == "Total":
            total = values
        elif name == "Total Votes Counted":
            total = ordinary = values
        elif name == "Total Ordinary Votes":
            ordinary = values
        else:
            columns.append(ROW_NAMES.get(name, name))
            data.append(values)
    if total is None or ordinary is None or len(set(columns)) != len(columns):
        raise ValueError(f"{url}: unexpected table")
    shown = [name for name in columns if name not in VOTE_TYPES]
    if [
        sum(d[j] for d, name in zip(data, columns, strict=True) if name in shown) for j in range(len(total))
    ] != ordinary:
        raise ValueError(f"{url}: polling places do not add up to the ordinary votes")
    n = len(labels)
    if [sum(d[j] for d in data) for j in range(len(total))] != total:
        raise ValueError(f"{url}: rows do not add up to the total")
    out = [Row("", label, party, [d[j] for d in data] + [total[j]]) for j, (label, party) in enumerate(labels)]
    return Table(
        columns,
        out,
        [d[n] for d in data] + [total[n]],
        [d[n + 1] for d in data] + [total[n + 1]],
        [d[n + 2] for d in data] + [total[n + 2]],
    )


def itemized(t: Table) -> bool:
    """Whether the file prints candidate figures for every column that has votes."""
    return all(
        any(r.votes[i] is not None for r in t.rows) or t.formal[i] == 0
        for i, name in enumerate(t.columns)
        if name not in VOTE_TYPES
    )


def named(page: Table, file: Table) -> Table:
    """The page's figures, in the file's order of columns, with the candidates' names from the file."""
    if sorted(page.columns) != sorted(file.columns) or len(page.rows) != len(file.rows):
        raise ValueError("the page and the file disagree on their columns or candidates")
    order = [page.columns.index(c) for c in file.columns] + [len(page.columns)]
    rows = []
    for a, b in zip(page.rows, file.rows, strict=True):
        if norm(a.label) != norm(b.label.partition(",")[0]) or a.votes[-1] != b.votes[-1]:
            raise ValueError(f"{a.label} differs between the page and the file")
        rows.append(Row(b.group, b.label, b.party, [a.votes[i] for i in order]))
    pick = [*order]
    return Table(
        file.columns,
        rows,
        [page.formal[i] for i in pick],
        [page.informal[i] for i in pick],
        [page.cast[i] for i in pick],
    )


class Unit(NamedTuple):
    council: str
    ward: str  # the commission's ward id, or "" for a council without wards
    summary: str
    night: str | None
    post: str | None
    night_file: str | None
    post_file: str | None


def units() -> list[Unit]:
    found: list[Unit] = []
    for name in council_names():
        wards = ward_pages(name)
        pages: dict[tuple[str, str], str] = {}
        for page in [council_url(name), *(w[0] for w in wards.values())]:
            pages.update(unit_pages(page))
        for ward in sorted({w for w, _ in pages}):
            of = {kind: url for (w, kind), url in pages.items() if w == ward}
            files = {}
            for kind in ("electionnight", "postelectionnight"):
                if kind in of:
                    links = [absolute(m[1]) for m in CSV_LINK.finditer(read(of[kind]))]
                    if len(links) > 1:
                        raise ValueError(f"{of[kind]}: {len(links)} files")
                    if links:
                        files[kind] = links[0]
            found.append(
                Unit(
                    name,
                    ward,
                    of["summary"],
                    of.get("electionnight"),
                    of.get("postelectionnight"),
                    files.get("electionnight"),
                    files.get("postelectionnight"),
                )
            )
    return found


class Contest(NamedTuple):
    unit: Unit
    summary: Summary
    night: Table | None
    post: Table | None


def contests() -> list[Contest]:
    found: list[Contest] = []
    for u in units():
        s = summary(u.summary)
        if s is None:
            continue
        night = post = None
        if u.night is not None:
            night = csv_table(u.night_file) if u.night_file else html_table(u.night)
        if u.post is not None:
            post = csv_table(u.post_file) if u.post_file else html_table(u.post)
            if u.post_file and post is not None and not itemized(post):
                page = html_table(u.post)
                if page is None:
                    raise ValueError(f"{u.post}: no table for a file that omits polling places")
                post = named(page, post)
        if s.stages and night is None and post is None:
            raise ValueError(f"{u.summary}: the contest has a count but no table")
        found.append(Contest(u, s, night, post))
    return found


def deferred() -> list[str]:
    return [u.council for u in units() if summary(u.summary) is None]


# Names -----------------------------------------------------------------------------------------------------------


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def proper(text: str) -> str:
    out = []
    for word in text.split():
        if word.isupper():
            word = word.title()
        elif re.fullmatch(r"Mc[A-Z]+", word):
            word = "Mc" + word[2:].title()
        out.append(word)
    return " ".join(out)


def person(label: str, declared: str | None) -> str:
    surname, comma, given = label.partition(", ")
    if comma:
        return f"{given.strip().title()} {proper(surname)}"
    if declared is not None and norm(declared).endswith(norm(label)):
        tokens = declared.split()
        words = len(label.split())
        return f"{' '.join(tokens[:-words]).title()} {proper(label)}".strip()
    return proper(label)


def party_codes(items: list[Contest]) -> dict[tuple[str, str], tuple[str, str]]:
    """Each party label a count prints, with the full name a summary page gives it, mapped to the code and name of
    the party in the archive. A label that two summary pages expand differently gets a party for each name."""
    names: dict[str, set[str]] = defaultdict(set)
    for c in items:
        for abbr, name in c.summary.parties.items():
            names[abbr].add(name)
    found: dict[tuple[str, str], tuple[str, str]] = {}
    for c in items:
        for r in c.summary.rows:
            if r.label == "Group Votes" or r.party in NO_PARTY:
                continue
            options = names.get(r.party, set()) or {r.party}
            for name in options:
                code = local_party_code(r.party) if len(options) == 1 else local_party_code(f"{r.party} {name}")
                found[(r.party, name)] = (code, name)
    return found


def party_of(c: Contest, label: str, codes: dict[tuple[str, str], tuple[str, str]]) -> str | None:
    if label in NO_PARTY:
        return None
    name = c.summary.parties.get(label, label)
    return key("party", "lg2008", codes[(label, name)][0])


# Packages --------------------------------------------------------------------------------------------------------


def contest_label(c: Contest) -> str:
    return c.summary.council if c.summary.ward is None else f"{c.summary.council}, {c.summary.ward}"


def council_of(c: Contest) -> str:
    return slug(c.summary.council)


def contest_code(c: Contest) -> str:
    return c.unit.council if not c.unit.ward else f"{c.unit.council}:{c.unit.ward}"


def council_area(slug_: str) -> str:
    return area("au-nsw.council", f"2008:{slug_}")


def ward_area(slug_: str, ward: str) -> str:
    return area("au-nsw.ward", f"2008:{slug_}:{ward}")


def contest_area(c: Contest) -> str:
    return council_area(council_of(c)) if not c.unit.ward else ward_area(council_of(c), c.unit.ward)


def geo(items: list[Contest]) -> Package:
    councils = {council_of(c): c.summary.council for c in items}
    wards = {(council_of(c), c.unit.ward): c.summary.ward for c in items if c.unit.ward}
    p = Package(
        f"{SOURCE}/geo-lg2008",
        SOURCE,
        f"The {len(councils)} councils and {len(wards)} wards of the 13 September 2008 New South Wales local "
        "government elections, as the NSW Electoral Commission names them",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {AUSTRALIA, COUNTRY}
    p.artifact(raw.cached(SOURCE, INDEX), NSWEC)
    for name in council_names():
        p.artifact(raw.cached(SOURCE, council_url(name)), NSWEC)
        for page, _ in ward_pages(name).values():
            p.artifact(raw.cached(SOURCE, page), NSWEC)
    p.add(
        "scheme",
        code="au-nsw.ward",
        polity_id=AUSTRALIA,
        kind="electoral_district",
        rank=3,
        noun="ward",
        code_system=None,
    )
    for scheme in ("au-nsw.council", "au-nsw.ward"):
        p.add("boundary_set", id=key("boundary_set", scheme, 2008), scheme_code=scheme, vintage="2008", as_of=DAY)
    for slug_, name in sorted(councils.items()):
        id = council_area(slug_)
        p.add("area", id=id, scheme_code="au-nsw.council")
        p.add("area_code", area_id=id, code=f"2008:{slug_}")
        p.add("area_parent", area_id=id, parent_id=NSW)
        p.name(id, name, "en", "official")
    for (slug_, ward), name in sorted(wards.items()):
        id = ward_area(slug_, ward)
        p.add("area", id=id, scheme_code="au-nsw.ward")
        p.add("area_code", area_id=id, code=f"2008:{slug_}:{ward}")
        p.add("area_parent", area_id=id, parent_id=council_area(slug_))
        p.name(id, f"{councils[slug_]}, {name}", "en", "official")
    return p


def registry(items: list[Contest]) -> Package:
    codes = party_codes(items)
    councils = {council_of(c): c.summary.council for c in items}
    parties = sorted(set(codes.values()))
    p = Package(
        f"{SOURCE}/registry-lg2008",
        SOURCE,
        f"The {len(councils)} councils and {len(parties)} parties of the 13 September 2008 New South Wales local "
        "government elections, as the NSW Electoral Commission names them",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/geo-lg2008"],
    )
    p.external |= {AUSTRALIA}
    for slug_, name in sorted(councils.items()):
        id = key("body", "council", f"2008:{slug_}")
        p.add("body", id=id, polity_id=AUSTRALIA, kind="council")
        p.name(id, name, "en", "official")
    for code, name in parties:
        id = key("party", "lg2008", code)
        p.add("party", id=id, kind="party", polity_id=AUSTRALIA)
        p.name(id, name, "en", "official")
    return p


def same_candidates(rows: list[Row], other: list[Row]) -> bool:
    return len(rows) == len(other) and all(
        (a.label == "Group Votes") == (b.label == "Group Votes")
        and (a.label == "Group Votes" or norm(a.label.partition(",")[0]) == norm(b.label.partition(",")[0]))
        for a, b in zip(rows, other, strict=True)
    )


def elected_rows(c: Contest, rows: list[Row]) -> dict[int, str]:
    """The index of each candidate row the summary page declares elected, with the name it prints."""
    found: dict[int, str] = {}
    for name in c.summary.elected:
        hits = [
            i
            for i, r in enumerate(rows)
            if r.label != "Group Votes"
            and i not in found
            and (
                norm(r.label.partition(", ")[2] + r.label.partition(", ")[0]) == norm(name)
                or norm(name).endswith(norm(r.label))
            )
        ]
        if len(hits) != 1:
            raise ValueError(f"{contest_label(c)}: {name} matches {len(hits)} candidates")
        found[hits[0]] = name
    return found


def election(items: list[Contest]) -> Package:
    codes = party_codes(items)
    p = Package(
        f"{SOURCE}/{DAY}-lg2008",
        SOURCE,
        "The 13 September 2008 New South Wales local government elections, councillor first preferences by polling "
        "place, from the NSW Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{SOURCE}/geo-lg2008", f"{SOURCE}/registry-lg2008"],
    )
    p.external |= {AUSTRALIA}
    election_id = key("election", DAY, "lg2008")
    p.add(
        "election",
        id=election_id,
        polity_id=AUSTRALIA,
        kind="local",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.name(election_id, "2008 New South Wales local government elections", "en", "official")
    p.add("slug", path=PATH, entity_id=election_id, is_current=True)
    values = Values()
    gaps: list[str] = []
    for c in sorted(items, key=contest_label):
        s, code, slug_ = c.summary, contest_code(c), council_of(c)
        for url in (c.unit.summary, c.unit.night, c.unit.post, c.unit.night_file, c.unit.post_file):
            if url is not None:
                p.artifact(raw.cached(SOURCE, url), NSWEC)
        tacit = c.night is None and c.post is None
        contest = key("contest", DAY, "lg2008", code)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=key("body", "council", f"2008:{slug_}"),
            district_area_id=contest_area(c),
            magnitude=s.vacancies,
            round_number=1,
            electoral_system_id=COUNCIL_SYSTEM,
            default_result_kind="tacit" if tacit else "counted",
        )
        p.name(contest, f"{contest_label(c)} councillors", "en", "official")
        path = f"{PATH}/council-{slug_}" + (f"/ward-{slug(s.ward)}" if s.ward else "")
        p.add("slug", path=path, entity_id=contest, is_current=True)
        base = c.post or c.night
        rows = s.rows if base is None else base.rows
        if not same_candidates(rows, s.rows):
            raise ValueError(f"{contest_label(c)}: the summary page and the count list different candidates")
        elected = elected_rows(c, rows)
        group_option: dict[str, str] = {}
        group_party: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            if r.label != "Group Votes" and r.group and r.party not in NO_PARTY:
                group_party[r.group].add(r.party)
        options: list[str] = []
        for i, r in enumerate(rows, 1):
            if r.label == "Group Votes":
                id = group_option[r.group] = key("option", DAY, "lg2008", code, "group", r.group)
                labels = group_party[r.group]
                pid = party_of(c, next(iter(labels)), codes) if len(labels) == 1 else None
                p.add(
                    "contest_option",
                    id=id,
                    contest_id=contest,
                    kind="list",
                    party_id=pid,
                    ballot_position=i,
                    ballot_label=f"Group {r.group}",
                )
                options.append(id)
                continue
            id = key("option", DAY, "lg2008", code, "candidate", i, r.label)
            pid = party_of(c, r.party, codes)
            p.add(
                "contest_option",
                id=id,
                contest_id=contest,
                kind="candidate",
                parent_option_id=group_option.get(r.group) if r.group else None,
                party_id=pid,
                ballot_position=i,
                ballot_label=r.label,
            )
            who = key("person", DAY, "lg2008", code, i, r.label)
            p.add("person", id=who)
            p.name(who, person(r.label, elected.get(i - 1)), "en", "official")
            p.add(
                "candidacy",
                id=key("candidacy", DAY, "lg2008", code, i, r.label),
                person_id=who,
                option_id=id,
                role="head",
                ballot_name=r.label,
                member_of_party_id=pid,
                is_independent=pid is None,
            )
            options.append(id)
        sets: list[tuple[str, Table | None, int]] = []
        if tacit:
            sets.append(("official", None, 0))
        elif c.post is not None and c.night is not None:
            sets += [("preliminary", c.night, 0), ("official", c.post, s.stages - 1)]
        elif c.post is not None:
            sets.append(("official", c.post, s.stages - 1))
        else:
            sets.append(("preliminary", c.night, 0))
        unit_ids: dict[str, str] = {}

        def unit(name: str) -> str:
            if name not in unit_ids:
                kind = unit_kind("", name)
                id = unit_ids[name] = key("unit", DAY, "lg2008", code, name)
                p.add(
                    "reporting_unit",
                    id=id,
                    polity_id=AUSTRALIA,
                    scheme_code="au-nsw.council_venue",
                    code=f"{DAY}:2008:{code}:{name}",
                    area_relation="within",
                    area_id=contest_area(c),
                    kind=kind,
                )
                p.name(id, name if kind == "standard" else f"{contest_label(c)}: {name}", "en", "official")
            return unit_ids[name]

        for stage, t, column in sets:
            result_set = key("result_set", contest, stage)
            p.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage=stage,
                kind="tacit" if t is None else "counted",
                source_id=NSWEC,
                status="active",
                is_default=True,
                completeness=None if t is None else 1.0,
            )
            how = (
                TACIT_NOTE
                if t is None
                else FINAL_NOTE
                if stage == "official"
                else NIGHT_EARLIER
                if c.post is not None
                else NIGHT_SOLE
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD % how,
                lang="en",
            )
            if stage == "official" or c.post is None:
                for i in sorted(elected):
                    p.add("outcome", result_set_id=result_set, option_id=options[i], kind="elected")
            if t is None:
                continue
            for option, sr in zip(options, s.rows, strict=True):
                if sr.votes[column] is not None:
                    p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=sr.votes[column])
            p.add("declared", result_set_id=result_set, option_id=None, measure=4, value=s.formal[column])
            p.add("declared", result_set_id=result_set, option_id=None, measure=5, value=s.informal[column])
            blank = [n for n, x in zip(t.columns, t.informal, strict=False) if x is None]
            if blank and sum(x or 0 for x in t.informal[:-1]) != s.informal[column]:
                gaps.append(f"{contest_label(c)} ({stage}: {', '.join(blank)})")
            for i, name in enumerate(t.columns):
                id = unit(name)
                for option, r in zip(options, t.rows, strict=True):
                    values.add(result_set, id, option, 1, r.votes[i] or 0)
                values.add(result_set, id, None, 4, t.formal[i])
                for measure, shown in ((5, t.informal[i]), (3, t.cast[i])):
                    if shown is not None:
                        values.add(result_set, id, None, measure, shown)
    p.frame("result_value", values.frame())
    if gaps:
        p.accept(
            "accounting.declared",
            "The commission's file leaves the informal and total ballots of a declaration vote type blank, in "
            + "; ".join(gaps)
            + ". The informal ballots of those units therefore fall short of the informal total its summary page "
            "declares, by the informal ballots of those declaration votes; the formal votes and every candidate's "
            "votes match the declared totals.",
        )
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo-lg2008", f"{SOURCE}/registry-lg2008", f"{SOURCE}/{DAY}-lg2008"]


def parse() -> None:
    items = contests()
    geo(items).write()
    registry(items).write()
    election(items).write()
