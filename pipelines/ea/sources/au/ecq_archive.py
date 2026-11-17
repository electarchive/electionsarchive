"""The Electoral Commission of Queensland's archive of results from 2004 to 2018, which it publishes as HTML pages."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser

from ea import raw
from ea.sources.au.ecq import (
    ARCHIVE,
    SOURCE,
    Booth,
    Cand,
    Contest,
    Event,
    Scope,
)

INDEX = f"{ARCHIVE}"
SKIPPED: list[str] = []
CODES = {
    "ALP": "Australian Labor Party (State of Queensland)",
    "LNP": "Liberal National Party of Queensland",
    "ONP": "Pauline Hanson's One Nation Queensland Division",
    "GRN": "Queensland Greens",
    "KAP": "Katter's Australian Party (KAP)",
}


class Rows(HTMLParser):
    """Every table row of a page, as the text of its cells; a tick image becomes [tick] and a break a newline."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.stack: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.stack.append([])
        elif tag in ("td", "th") and self.stack:
            self.stack[-1].append("")
        elif tag == "br" and self.stack and self.stack[-1]:
            self.stack[-1][-1] += "\n"
        elif tag == "img" and self.stack and self.stack[-1]:
            source = dict(attrs).get("src") or ""
            if source.startswith("tick"):
                self.stack[-1][-1] += "\n[tick]\n"

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self.stack:
            self.rows.append(self.stack.pop())

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1]:
            self.stack[-1][-1] += data


def read(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="cp1252", errors="replace")


def rows(url: str) -> list[list[str]]:
    parser = Rows()
    parser.feed(read(url))
    return [[c.replace("\xa0", " ").strip() for c in row] for row in parser.rows]


def plain(url: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", read(url), flags=re.DOTALL)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def heading(url: str) -> list[str]:
    """The parts of a page's main heading, which reads like '2017 State General Election - Algester - District Summary'."""
    m = re.search(r"id=\"content_size\">\s*<h3>(.*?)</h3>", read(url), flags=re.DOTALL)
    return [one_line(unescape(re.sub(r"<[^>]+>", "", p))) for p in (m.group(1) if m else "").split(" - ")]


def number(text: str) -> int | None:
    text = text.replace(",", "").replace("\xa0", "").strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


def one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# Discovery ------------------------------------------------------------------------------------------------------


@dataclass
class Entry:
    section: str
    title: str
    href: str

    @property
    def base(self) -> str:
        href = self.href.replace("\\", "/")
        return f"{ARCHIVE}/{href.rsplit('/', 1)[0]}"

    @property
    def stub(self) -> str:
        href = self.href.replace("\\", "/")
        if href.endswith("groupIndex.html"):
            return href.split("/")[-2]
        return href.split("/results")[0].split("/")[-1] if "/results" in href else href.split("/")[-2]


def entries() -> list[Entry]:
    text = read(INDEX)
    found: list[Entry] = []
    section = ""
    pattern = (
        r"<strong><font>(.*?)</font></strong>|<td width='38%'>(.*?)</td><td width='30%'>(?:&nbsp;)?<a href='([^']+)'>"
    )
    for m in re.finditer(pattern, text[text.find("ContentPanel") :]):
        if m.group(1):
            section = unescape(m.group(1)).strip()
        else:
            found.append(Entry(section, one_line(unescape(re.sub(r"<[^>]+>", "", m.group(2)))), m.group(3).strip()))
    return found


def is_state(entry: Entry) -> bool:
    return entry.href.startswith("state/") and "Referendum" not in entry.section


def district_links(url: str) -> list[int]:
    return sorted({int(n) for n in re.findall(r"""href=["']district(\d+)\.html""", read(url))})


def is_poll(entry: Entry) -> bool:
    return entry.section == "Polls"


def is_referendum(entry: Entry) -> bool:
    return entry.href.startswith("state/") and "Referendum" in entry.section


def poll_number(entry: Entry) -> int:
    m = re.search(r"district(\d+)\.html", entry.href)
    if not m:
        raise ValueError(f"{entry.href}: not a poll page")
    return int(m.group(1))


def fetch() -> None:
    raw.fetch(SOURCE, INDEX, suffix=".html")
    polls = [e for e in entries() if is_poll(e)]
    raw.fetch_many(
        SOURCE,
        [
            u
            for e in polls
            for u in (
                f"{e.base}/district{poll_number(e)}.html",
                f"{e.base}/booth{poll_number(e)}.html",
                f"{e.base.rsplit('/results', 1)[0]}/index.html",
            )
        ],
        suffix=".html",
    )
    state = [e for e in entries() if e.href.startswith("state/")]
    raw.fetch_many(SOURCE, [f"{e.base}/summary.html" for e in state], suffix=".html")
    urls: list[str] = []
    for e in state:
        for n in district_links(f"{e.base}/summary.html"):
            urls += [f"{e.base}/district{n}.html", f"{e.base}/booth{n}.html"]
        urls.append(f"{e.base.rsplit('/results', 1)[0]}/index.html")
    got = raw.fetch_many(SOURCE, urls, suffix=".html")
    missing = [u for u in urls if u not in got]
    if missing:
        print(f"{len(missing)} archive pages did not answer, first {missing[:3]}", flush=True)
    local_fetch()


# State elections ------------------------------------------------------------------------------------------------


def title_of(url: str) -> tuple[str, date]:
    """The election name and day its 'All You Need To Know' page gives."""
    m = re.search(r"id=\"content_size\">\s*<h3>(.*?)</h3>", read(url), flags=re.DOTALL)
    head = one_line(unescape(re.sub(r"<[^>]+>", "", m.group(1)))) if m else ""
    name, _, day = head.rpartition(" : ")
    return name.strip(), datetime.strptime(day.strip(), "%d %B %Y").date()


def party_names(summary: str) -> dict[str, str]:
    """The names a summary page gives to the party codes on its ballot."""
    found: dict[str, str] = {}
    for r in rows(summary):
        if len(r) == 3 and re.fullmatch(r"[A-Z]{2,6}", r[1]) and r[0] and r[0] != r[1]:
            found[r[1]] = one_line(r[0])
    return found


def header_cands(
    header: list[str], first: int, count: int, names: dict[str, str] | None = None
) -> tuple[list[Cand], set[int]]:
    cands: list[Cand] = []
    ticked: set[int] = set()
    for i, cell in enumerate(header[first : first + count], 1):
        lines = [l.strip() for l in re.split(r"\n+", cell.replace("&nbsp;", " ")) if l.strip()]
        elected = "[tick]" in lines
        lines = [l for l in lines if l != "[tick]"]
        name = " ".join(lines[:2]) if len(lines) >= 2 else lines[0]
        code = lines[2] if len(lines) >= 3 else ""
        cands.append(Cand(name, i, party=CODES.get(code) or (names or {}).get(code) or code, code=code))
        if elected:
            ticked.add(i)
    return cands, ticked


def booth_page(url: str, names: dict[str, str]) -> tuple[list[Cand], set[int], list[Booth], Scope, int]:
    table = rows(url)
    head = next(
        i for i, r in enumerate(table) if len(r) > 6 and r[0].rstrip(".") == "No" and r[1].startswith("Polling")
    )
    k = len(table[head]) - 6
    cands, ticked = header_cands(table[head], 2, k, names)
    booths: list[Booth] = []
    totals = Scope("", 0, None, "", "")
    distributions = 0
    final: dict[int, int] = {}
    in_distribution = False
    for r in table[head + 1 :]:
        if not r:
            continue
        label = one_line(r[0])
        if label == "First Preference Votes" and len(r) == 2 * k + 5:
            in_distribution = True
            continue
        if in_distribution:
            if label.endswith("Excluded"):
                distributions += 1
            elif label.endswith(" Total") and label != "Total" and len(r) == 2 * k + 5:
                final = {i + 1: n for i in range(k) if (n := number(r[1 + 2 * i])) is not None}
            continue
        if label == "Total First Preference Votes":
            vals = r[1:]
            totals.primary = {i + 1: number(vals[2 * i]) or 0 for i in range(k)}
            totals.formal = number(vals[2 * k])
            totals.informal = number(vals[2 * k + 1])
            totals.total = number(vals[2 * k + 3])
            continue
        if len(r) == 2 * k + 7 and number(r[0]) is not None and number(r[2]) is not None:
            no, name, vals = r[0], one_line(r[1]), r[2:]
            ident = f"{number(no)}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"
            code = "PB"
        elif len(r) == 2 * k + 6 and number(r[1]) is not None and not re.search(r"Total", label):
            name, vals = label, r[1:]
            ident = "D:" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            code = "DV1" if name.lower().startswith("postal") else "DV2"
        else:
            continue
        if len(name) == 0:
            continue
        b = Booth(
            ident,
            name,
            code,
            "",
            ballots=number(vals[2 * k + 3]),
            formal=number(vals[2 * k]),
            informal=number(vals[2 * k + 1]),
            primary={i + 1: number(vals[2 * i]) or 0 for i in range(k)},
        )
        booths.append(b)
    totals.final = final
    return cands, ticked, booths, totals, distributions


def state_event(entry: Entry) -> Event:
    summary = f"{entry.base}/summary.html"
    index = f"{entry.base.rsplit('/results', 1)[0]}/index.html"
    name, day = title_of(index)
    contests: list[Contest] = []
    urls = [summary, index]
    names = party_names(summary)
    for n in district_links(summary):
        district_url, booth_url = f"{entry.base}/district{n}.html", f"{entry.base}/booth{n}.html"
        text = plain(district_url)
        place = heading(district_url)[-2]
        enrolment = re.search(r"Electors at Close of Roll:\s*([\d,]+)", text)
        cands, ticked, booths, totals, k = booth_page(booth_url, names)
        totals.name = place
        totals.number = n
        totals.enrolment = number(enrolment.group(1)) if enrolment else None
        totals.booths = booths
        year = day.year
        contests.append(
            Contest(
                "assembly",
                place,
                elect=1,
                system="Compulsory Preferential" if year >= 2017 else "Optional Preferential",
                cands=cands,
                declared=ticked,
                scopes=[totals],
                distributions=k,
                totals=totals,
            )
        )
        urls += [district_url, booth_url]
    kind = "State General" if "General" in name else "State By-election"
    return Event(entry.stub, name, day, kind, entry.stub, contests, urls)


def referendum_event(entry: Entry) -> Event:
    summary = f"{entry.base}/summary.html"
    index = f"{entry.base.rsplit('/results', 1)[0]}/index.html"
    name, day = title_of(index)
    table = {r[0]: r for r in rows(summary) if len(r) >= 2}
    enrolled = re.search(r"Total Enrolled\s+([\d,]+)", plain(summary))
    question = re.search(r"(Question 1 - [^|]+?)\s+Response", plain(summary))
    scope = Scope(
        "Queensland",
        1,
        number(enrolled.group(1)) if enrolled else None,
        "",
        "",
        formal=(number(table["YES"][1]) or 0) + (number(table["NO"][1]) or 0),
        informal=number(table["Informal"][1]),
        total=number(table["Total Votes"][1]),
        primary={1: number(table["YES"][1]) or 0, 2: number(table["NO"][1]) or 0},
    )
    wording = re.search(r"(A Bill .*?\.) Election Timetable", plain(index))
    contest = Contest(
        "question",
        question.group(1).split(" - ", 1)[1] if question else "Referendum",
        cands=[Cand("YES", 1), Cand("NO", 2)],
        scopes=[scope],
        totals=scope,
        question=one_line(wording.group(1)) if wording else "",
    )
    scope.booths = []
    return Event(entry.stub, name, day, "State Referendum", entry.stub, [contest], [summary, index])


def poll_event(entry: Entry) -> Event:
    n = poll_number(entry)
    district_url, booth_url = f"{entry.base}/district{n}.html", f"{entry.base}/booth{n}.html"
    index = f"{entry.base.rsplit('/results', 1)[0]}/index.html"
    _, day = title_of(index)
    text = plain(district_url)
    place = heading(district_url)[-2]
    enrolment = re.search(r"Electors at Close of Roll:\s*([\d,]+)", text)
    cands, _, booths, totals, _ = booth_page(booth_url, {})
    totals.name, totals.number, totals.booths = place, n, booths
    totals.enrolment = number(enrolment.group(1)) if enrolment else None
    wording = re.search(r"Question 1 - .*? Question (Should .*?\?)", plain(index))
    contest = Contest(
        "question",
        place,
        council=place,
        council_type="Council - Undivided",
        cands=cands,
        scopes=[totals],
        totals=totals,
        question=wording.group(1) if wording else "",
    )
    return Event(entry.stub, entry.title, day, "Local Poll", entry.stub, [contest], [district_url, booth_url, index])


def archive_events() -> list[Event]:
    found = [state_event(e) for e in entries() if is_state(e)]
    found += [referendum_event(e) for e in entries() if is_referendum(e)]
    found += [poll_event(e) for e in entries() if is_poll(e)]
    for e in entries():
        made = group_event(e) if is_group(e) else local_by_event(e) if is_local_by(e) else None
        if made is not None:
            found.append(made)
    return found


# Local government -----------------------------------------------------------------------------------------------


@dataclass
class Base:
    """One contest's pages: a council's mayoral or councillor results, or a by-election's results."""

    council: str
    kind: str  # mayor or councillor
    url: str  # the folder, ending in /results/... without a trailing slash

    @property
    def summary(self) -> str:
        return f"{self.url}/summary.html"


def is_group(entry: Entry) -> bool:
    return entry.href.endswith("groupIndex.html")


def is_local_by(entry: Entry) -> bool:
    return entry.href.startswith("local/") and not is_poll(entry) and not is_group(entry)


def group_bases(entry: Entry) -> list[Base]:
    """Every council's mayoral and councillor result folders listed on an election group's index page."""
    found = []
    text = read(f"{entry.base}/groupIndex.html")
    for m in re.finditer(
        r"<td width=25% align=left>(.*?)</td><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td>", text
    ):
        council = one_line(unescape(re.sub(r"<[^>]+>", "", m.group(1))))
        for cell in (m.group(2), m.group(3)):
            link = re.search(r"href='([^']+)/summary\.html'", cell)
            if link:
                kind = "mayor" if "mayoral" in link.group(1).lower() else "councillor"
                found.append(Base(council, kind, f"{entry.base}/{link.group(1).replace(chr(92), '/')}"))
    return found


def by_base(entry: Entry) -> Base:
    council = entry.section.split(" - ")[0].strip()
    return Base(council, "mayor" if "Mayoral" in entry.section else "councillor", entry.base)


def local_fetch() -> None:
    items = entries()
    groups = [e for e in items if is_group(e)]
    raw.fetch_many(SOURCE, [f"{e.base}/groupIndex.html" for e in groups], suffix=".html")
    bases = [b for e in groups for b in group_bases(e)] + [by_base(e) for e in items if is_local_by(e)]
    raw.fetch_many(SOURCE, [b.summary for b in bases], suffix=".html")
    urls: list[str] = []
    for b in bases:
        if (ref := raw.keys(SOURCE)) and b.summary not in ref:
            continue
        for n in district_links(b.summary):
            urls += [f"{b.url}/district{n}.html", f"{b.url}/booth{n}.html"]
        folder = b.url.rsplit("/results", 1)[0]
        urls.append(f"{folder}/index.html")
    got = raw.fetch_many(SOURCE, urls, suffix=".html")
    missing = [u for u in urls if u not in got]
    if missing:
        print(f"{len(missing)} local archive pages did not answer, first {missing[:3]}", flush=True)


def slug_of(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def contest_totals(summary: str, cands: list[Cand]) -> Scope:
    """A mayoral count's totals for the whole council, from the first table of its summary page."""
    totals = Scope("", 0, None, "", "")
    k = len(cands)
    for r in rows(summary):
        if r and one_line(r[0]) == "Total First Preference Votes" and len(r) == 2 * k + 6:
            totals.primary = {i + 1: number(r[1 + 2 * i]) or 0 for i in range(k)}
            totals.formal, totals.informal, totals.total = (
                number(r[1 + 2 * k]),
                number(r[2 + 2 * k]),
                number(r[4 + 2 * k]),
            )
            return totals
    by_name = {one_line(c.ballot).lower().replace(",", ""): c.order for c in cands}
    for r in rows(summary):
        if not r:
            continue
        label = one_line(r[0].replace("[tick]", ""))
        key = label.lower().replace(",", "")
        values = [n for n in (number(c) for c in r[1:]) if n is not None]
        if key in by_name and values and by_name[key] not in totals.primary:
            totals.primary[by_name[key]] = values[0]
        elif label == "Total Formal Votes" and values and totals.formal is None:
            totals.formal = values[0]
        elif label == "Total Informal" and values and totals.informal is None:
            totals.informal = values[0]
        elif label == "Total Votes" and values and totals.total is None:
            totals.total = values[0]
    table = rows(summary)
    if not totals.primary:
        for r in table:
            if r and one_line(r[0]) == "First Preference Votes" and len(r) >= 2 * len(cands) + 1:
                totals.primary = {i + 1: number(r[1 + 2 * i]) or 0 for i in range(len(cands))}
                break
    if totals.formal is None:
        for r in reversed(table):
            if len(r) == 7 and one_line(r[0]) == "Total" and number(r[1]) is not None:
                totals.formal, totals.informal, totals.total = number(r[1]), number(r[3]), number(r[5])
                break
    return totals


def place_of(url: str) -> tuple[str, bool]:
    """The division, ward, or council a district page is for, and whether the page is for an undivided council."""
    parts = heading(url)
    return (parts[-2], "Undivided" in parts[-1]) if len(parts) >= 2 else ("", False)


def declared_table(summary: str) -> dict[str, list[str]]:
    """The elected candidates a summary page lists under each division, ward, or council."""
    found: dict[str, list[str]] = {}
    on = False
    place = ""
    for r in rows(summary):
        if len(r) >= 3 and r[0] == "No." and "Elected" in r[2]:
            on = True
            continue
        if not on:
            continue
        if "Total Declared" in " ".join(r):
            break
        if len(r) >= 3 and number(r[0]) is not None:
            place = one_line(r[1])
            found.setdefault(place, []).append(one_line(r[2]))
        elif len(r) >= 3 and r[0] == "" and r[1] == "" and r[2]:
            found.setdefault(place, []).append(one_line(r[2]))
    return found


def unopposed_mayor(summary: str) -> str | None:
    table = rows(summary)
    for r in table:
        if r and one_line(r[0]).endswith(" elected unopposed"):
            return one_line(r[0]).removesuffix(" elected unopposed")
    if "Elected Unopposed" in plain(summary):
        for r in table:
            label = one_line(r[0].replace("[tick]", "")) if r else ""
            if re.match(r"^[A-Z][A-Z'\-]+(?: [A-Z'\-]+)*, ", label):
                return label
    return None


def local_contests(base: Base, names: dict[str, str]) -> list[Contest]:
    found: list[Contest] = []
    council = re.sub(r"\s+Council$", "", base.council)
    scopes: list[Scope] = []
    cands: list[Cand] = []
    ticked: set[int] = set()
    undivided = False
    declared = declared_table(base.summary)
    for n in district_links(base.summary):
        district, booth = f"{base.url}/district{n}.html", f"{base.url}/booth{n}.html"
        if booth not in raw.keys(SOURCE) or district not in raw.keys(SOURCE):
            continue
        place, undiv = place_of(district)
        undivided = undivided or undiv
        text = plain(district)
        enrolment = re.search(r"Electors at Close of Roll:\s*([\d,]+)", text)
        name = place if place.lower().startswith(council.lower()) else f"{council} {place}"
        kind_of_council = "Council - Undivided" if undiv else "Council - Divided"
        if "Elected Unopposed" in text:
            if base.kind == "councillor" and declared.get(place):
                who = [Cand(w, i) for i, w in enumerate(declared[place], 1)]
                scope = Scope(name, n, number(enrolment.group(1)) if enrolment else None, "", "")
                found.append(
                    Contest(
                        "councillor",
                        name,
                        council=council,
                        council_type=kind_of_council,
                        elect=len(who),
                        system="First Past the Post" if undiv or len(who) > 1 else "Optional Preferential",
                        cands=who,
                        declared={c.order for c in who},
                        scopes=[scope],
                        totals=scope,
                    )
                )
            continue
        try:
            cs, tick, booths, totals, k = booth_page(booth, names)
        except StopIteration, IndexError, ValueError:
            SKIPPED.append(booth)
            continue
        totals.name, totals.number, totals.booths = name, n, booths
        totals.enrolment = number(enrolment.group(1)) if enrolment else None
        totals.system = "Optional Preferential"
        if base.kind == "councillor":
            found.append(
                Contest(
                    "councillor",
                    name,
                    council=council,
                    council_type=kind_of_council,
                    elect=max(1, len(tick)),
                    system="First Past the Post" if undiv or len(tick) > 1 else "Optional Preferential",
                    cands=cs,
                    declared=tick,
                    scopes=[totals],
                    distributions=k,
                    totals=totals,
                )
            )
        else:
            scopes.append(totals)
            cands, ticked = cs, tick
    if base.kind == "mayor" and scopes:
        total = contest_totals(base.summary, cands)
        total.name, total.enrolment = council, sum(s.enrolment or 0 for s in scopes) or None
        merged: dict[int, int] = {}
        for s in scopes:
            for order, votes in s.final.items():
                merged[order] = merged.get(order, 0) + votes
        total.final = merged
        if not total.primary:
            # Four 2013 councils' summary pages print no council-wide table, so the totals are the divisions' sums.
            for s in scopes:
                for order, votes in s.primary.items():
                    total.primary[order] = total.primary.get(order, 0) + votes
            total.formal = sum(s.formal or 0 for s in scopes)
            total.informal = sum(s.informal or 0 for s in scopes)
            total.total = sum(s.total or 0 for s in scopes)
        found.append(
            Contest(
                "mayor",
                council,
                council=council,
                council_type="Council - Undivided" if undivided else "Council - Divided",
                elect=1,
                system="Optional Preferential",
                cands=cands,
                declared=ticked,
                scopes=scopes,
                distributions=1 if merged else 0,
                totals=total,
            )
        )
    elif base.kind == "mayor" and (winner := unopposed_mayor(base.summary)):
        found.append(
            Contest(
                "mayor",
                council,
                council=council,
                council_type="Council - Undivided" if undivided else "Council - Divided",
                elect=1,
                system="Optional Preferential",
                cands=[Cand(winner, 1)],
                declared={1},
            )
        )
    return found


def local_urls(base: Base) -> list[str]:
    urls = [base.summary]
    for n in district_links(base.summary):
        urls += [f"{base.url}/district{n}.html", f"{base.url}/booth{n}.html"]
    return [u for u in urls if u in raw.keys(SOURCE)]


def group_event(entry: Entry) -> Event | None:
    contests: list[Contest] = []
    urls = [f"{entry.base}/groupIndex.html"]
    day: date | None = None
    for base in group_bases(entry):
        if base.summary not in raw.keys(SOURCE):
            continue
        names = party_names(base.summary)
        contests += local_contests(base, names)
        urls += local_urls(base)
        index = f"{base.url.rsplit('/results', 1)[0]}/index.html"
        if day is None and index in raw.keys(SOURCE):
            urls.append(index)
            day = title_of(index)[1]
    if day is None or not contests:
        return None
    return Event(entry.stub, entry.title, day, "Local Quadrennial", entry.stub, contests, urls)


def local_by_event(entry: Entry) -> Event | None:
    base = by_base(entry)
    if base.summary not in raw.keys(SOURCE):
        return None
    index = f"{base.url.rsplit('/results', 1)[0]}/index.html"
    if index not in raw.keys(SOURCE):
        return None
    contests = local_contests(base, party_names(base.summary))
    if not contests:
        return None
    _, day = title_of(index)
    kind = "Local Mayoral By-election" if base.kind == "mayor" else "Local Councillor By-election"
    return Event(entry.stub, entry.title, day, kind, entry.stub, contests, [*local_urls(base), index])
