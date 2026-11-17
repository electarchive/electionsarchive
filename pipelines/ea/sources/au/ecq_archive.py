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
        return self.href.replace("\\", "/").split("/")[1] if "/" in self.href else self.href


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
            elif label == "District Total" and len(r) == 2 * k + 5:
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
            ident = f"{number(no)}"
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
        m = re.search(r"^(.*?) - (.+?) - District Summary", text.split("Election Results Index")[0].strip())
        place = one_line(m.group(2)) if m else ""
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
    m = re.search(r" - ([^-]+?) - Poll Area Summary", text.split("Election Results Index")[0])
    place = one_line(m.group(1)) if m else ""
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
    return found
