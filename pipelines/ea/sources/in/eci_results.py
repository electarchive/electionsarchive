"""Election Commission of India: the 2024 Lok Sabha general election and the state assembly general elections of 2022
and 2023 that the Commission's statistical-report workbooks do not cover (`in.eci` holds those of 2023 to 2026), by
constituency, from the results site's own pages.

The Commission's site (results.eci.gov.in) keeps only the current poll live and its other hosts refuse scripted
requests, so each page is read from an archived capture of the original page and cited by its original URL, with the
capture's time as its retrieval time. Two pages carry a constituency's count:

- `Constituencywise<code><n>.htm` prints every candidate's EVM votes, postal votes, and total votes, with the printed
  total of each column;
- `candidateswise-<code><n>.htm` prints each candidate's total votes and the status the Returning Officer gave it
  (won or lost while the count is final, leading or trailing while it runs).

A capture can be taken in the middle of the count. A constituency is stored only when its capture is final: its
Constituencywise page says every round is counted, or its candidateswise page shows a declared winner and no
candidate leading or trailing, or its page was last updated after the evening of the counting day. A constituency
whose captures are all interim is left out and named in the package note. The site says that the final data of each
constituency is shared in Form 20, so every set is stored at the stage `official` as the site printed it, not
`certified`.

Constituencies are areas of two schemes of this pipeline (`in.eci_pc`, `in.eci_ac`) without shapes. Parties are
keyed by the name the site prints, and people by their place in the constituency's list, because the site prints no
identifiers.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from html import unescape
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA

SOURCE = "in.eci_results"
INDIA = str(uuid5(package.NAMESPACE, "polity\x1fIN"))
HOST = "https://results.eci.gov.in"
INBOX = DATA / "inbox" / SOURCE
PC_SCHEME = "in.eci_pc"
AC_SCHEME = "in.eci_ac"
VOTES, REGISTERED, BALLOTS, VALID = 1, 2, 3, 4
ALL, ELECTION_DAY, POSTAL = 0, 1, 3
EVENING = time(21, 0)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "results")
FPTP = key("electoral_system", "fptp")


@dataclass(frozen=True)
class Poll:
    """One election of the pipeline: the folder of the site that holds its pages, the site's code of the state (none
    for the Lok Sabha), the polling days, the counting day, and the number of seats."""

    name: str
    folder: str
    code: str | None
    iso: str | None
    state: str
    days: tuple[date, ...]
    counted: date
    seats: int
    delimitation: date = date(2008, 2, 19)


POLLS = [
    Poll(
        "lok-sabha-2024",
        "PcResultGenJune2024",
        None,
        None,
        "India",
        tuple(date(2024, m, d) for m, d in ((4, 19), (4, 26), (5, 7), (5, 13), (5, 20), (5, 25), (6, 1))),
        date(2024, 6, 4),
        543,
    ),
    Poll("ga-2022", "ResultAcGenMar2022", "S05", "GA", "Goa", (date(2022, 2, 14),), date(2022, 3, 10), 40),
    Poll(
        "mn-2022",
        "ResultAcGenMar2022",
        "S14",
        "MN",
        "Manipur",
        (date(2022, 2, 28), date(2022, 3, 5)),
        date(2022, 3, 10),
        60,
        date(2008, 2, 19),
    ),
    Poll("pb-2022", "ResultAcGenMar2022", "S19", "PB", "Punjab", (date(2022, 2, 20),), date(2022, 3, 10), 117),
    Poll(
        "up-2022",
        "ResultAcGenMar2022",
        "S24",
        "UP",
        "Uttar Pradesh",
        tuple(date(2022, m, d) for m, d in ((2, 10), (2, 14), (2, 20), (2, 23), (2, 27), (3, 3), (3, 7))),
        date(2022, 3, 10),
        403,
    ),
    Poll("uk-2022", "ResultAcGenMar2022", "S28", "UK", "Uttarakhand", (date(2022, 2, 14),), date(2022, 3, 10), 70),
    Poll(
        "gj-2022",
        "ResultAcGenDec2022",
        "S06",
        "GJ",
        "Gujarat",
        (date(2022, 12, 1), date(2022, 12, 5)),
        date(2022, 12, 8),
        182,
    ),
    Poll(
        "hp-2022", "ResultAcGenDec2022", "S08", "HP", "Himachal Pradesh", (date(2022, 11, 12),), date(2022, 12, 8), 68
    ),
    Poll("ka-2023", "ResultAcGenMay2023", "S10", "KA", "Karnataka", (date(2023, 5, 10),), date(2023, 5, 13), 224),
    Poll("ts-2023", "AcResultGenDecNew2023", "S29", "TS", "Telangana", (date(2023, 11, 30),), date(2023, 12, 3), 119),
]
BY_NAME = {p.name: p for p in POLLS}
FOLDERS = sorted({p.folder for p in POLLS})
PAGE = re.compile(
    r"https://results\.eci\.gov\.in/(?P<folder>\w+)/(?P<kind>Constituencywise|candidateswise-)(?P<code>[SU]\d\d)"
    r"(?P<seat>\d+)\.htm$"
)
SUMMARY = re.compile(r"https://results\.eci\.gov\.in/(?P<folder>\w+)/partywiseresult-(?P<code>[SU]\d\d)\.htm$")
MARK = re.compile(r"(?m)^<!-- ===== SOURCE: (\S+) \(wayback capture (\d{14})\) ===== -->\n")


# ---- the raw files -------------------------------------------------------------------------------------------------


def split(bundle: Path) -> list[tuple[str, datetime, bytes]]:
    """The pages of one saved bundle: each is the capture of one original page, headed by a marker comment."""
    text = bundle.read_bytes().decode("utf-8", errors="replace")
    marks = list(MARK.finditer(text))
    pages = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        stamp = datetime.strptime(mark.group(2), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        pages.append((mark.group(1), stamp, text[mark.end() : end].rstrip("\n").encode()))
    return pages


def wanted(url: str) -> re.Match[str] | None:
    found = PAGE.match(url)
    if found is None or found.group("folder") not in FOLDERS:
        return None
    states = {p.code for p in POLLS if p.folder == found.group("folder")}
    return found if found.group("code") in states or None in states else None


def summary(url: str) -> re.Match[str] | None:
    found = SUMMARY.match(url)
    if found is None or found.group("folder") not in FOLDERS:
        return None
    states = {p.code for p in POLLS if p.folder == found.group("folder")}
    return found if found.group("code") in states or None in states else None


def fetch() -> None:
    """Store each constituency page of a saved bundle under its original URL, with the capture's time. The bundles
    are the archived pages saved one file per folder and page type under `data/inbox/in.eci_results/`."""
    files = sorted(INBOX.glob("*.html"))
    if not files:
        raise FileNotFoundError(f"{INBOX} holds no saved bundles; the site and its archive refuse scripted requests")
    for bundle in files:
        items: dict[datetime, list[tuple[str, bytes]]] = {}
        for url, stamp, body in split(bundle):
            if wanted(url) or summary(url):
                items.setdefault(stamp, []).append((url, body))
        for stamp, pages in items.items():
            raw.store_many(SOURCE, pages, suffix=".htm", retrieved_at=stamp, media_type="text/html")


# ---- reading the pages ---------------------------------------------------------------------------------------------


@dataclass
class Row:
    name: str
    party: str
    evm: int | None
    postal: int | None
    total: int
    status: str | None = None


@dataclass
class Page:
    url: str
    kind: str
    code: str
    seat: int
    captured: datetime
    updated: datetime | None
    title: str
    rows: list[Row]
    printed: tuple[int, int, int] | None = None
    round: tuple[int, int] | None = None
    sha256: str = ""

    @property
    def total(self) -> int:
        return sum(r.total for r in self.rows)


CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL)
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)
TAG = re.compile(r"<[^>]+>")
CARD = re.compile(
    r"<div class='status (?P<status>\w+)'>.*?<div>(?P<votes>\d+) <span>\(\s*(?P<margin>[+-]?\s*\d+)\s*\)</span>.*?"
    r"<h5>(?P<name>.*?)</h5>\s*<h6>(?P<party>.*?)</h6>",
    re.DOTALL,
)
UPDATED = re.compile(r"Last Updated at\s*(\d\d?:\d\d\s*[AaPp][Mm])\s*On\s*(\d\d/\d\d/\d{4})")
ROUND = re.compile(r"Status[^<]{0,30}Round[^<]{0,5}[:,]?\s*(?:<[^>]*>\s*)*(\d+)\s*(?:<[^>]*>\s*)*/\s*(\d+)", re.DOTALL)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(TAG.sub(" ", text)).replace("\xa0", " ")).strip()


def integer(text: str) -> int | None:
    digits = text.replace(",", "").strip()
    return int(digits) if digits.isdigit() else None


NUMBERED = re.compile(r"^(\d+)\s*-\s*(.+)$")
RESERVED = re.compile(r"\s*\((SC|ST)\)")
STATE = re.compile(r"\s*\((?!SC\)|ST\))[^()]+\)\s*$")


ARROW = re.compile(r"\s*-->.*$")


def heading(html: str) -> str:
    """The constituency's heading as the page prints it, with the state's name last when it prints one. The Lok
    Sabha pages follow the heading with an arrow and the count's status (`--> won`), which is not part of it."""
    tokens = [clean(t) for t in re.split(r"<[^>]+>", re.sub(r"(?s)<script.*?</script>", "", html))]
    tokens = [t for t in tokens if t]
    for index, token in enumerate(tokens):
        if token in ("Parliamentary Constituency", "Assembly Constituency"):
            out = []
            for follow in tokens[index + 1 : index + 5]:
                if follow.startswith(("Status", "S.N.")):
                    break
                out.append(follow)
            return ARROW.sub("", " ".join(out))
        if token == "Roundwise-All Candidates" and index + 1 < len(tokens) and "-" in tokens[index + 1]:
            return tokens[index + 1]
    return ""


def name_of(title: str) -> tuple[int | None, str, str | None]:
    """The number, the English name, and the reservation a heading gives; the state's name is dropped."""
    number = None
    found = NUMBERED.match(title)
    if found:
        number, title = int(found.group(1)), found.group(2)
        title = STATE.sub("", title)
    else:
        trailing = re.search(r"\s*-\s*(\d+)$", title)
        if trailing:
            number, title = int(trailing.group(1)), title[: trailing.start()]
        title = title.split("-", 1)[-1].strip()
    reserved = RESERVED.search(title)
    return number, RESERVED.sub("", title).strip(), reserved.group(1) if reserved else None


def read(url: str, content: bytes, captured: datetime, sha256: str = "") -> Page:
    found = PAGE.match(url)
    if found is None:
        raise ValueError(f"{url} is not a constituency page")
    html = content.decode("utf-8", errors="replace")
    stamp = UPDATED.search(html)
    updated = datetime.strptime(f"{stamp.group(2)} {stamp.group(1).upper()}", "%d/%m/%Y %I:%M %p") if stamp else None
    rounds = ROUND.search(html)
    title = ""
    title = heading(html)
    page = Page(
        url,
        found.group("kind"),
        found.group("code"),
        int(found.group("seat")),
        captured,
        updated,
        title,
        [],
        round=(int(rounds.group(1)), int(rounds.group(2))) if rounds else None,
        sha256=sha256,
    )
    if page.kind == "candidateswise-":
        for card in CARD.finditer(html):
            page.rows.append(
                Row(
                    clean(card.group("name")),
                    clean(card.group("party")),
                    None,
                    None,
                    int(card.group("votes")),
                    card.group("status").lower(),
                )
            )
        return page
    for tr in ROW.findall(html):
        cells = [clean(c) for c in CELL.findall(tr)]
        if len(cells) == 7 and cells[0].isdigit() and all(c == "-" or integer(c) is not None for c in cells[3:6]):
            evm, postal, total = (None if c == "-" else int(c) for c in cells[3:6])
            page.rows.append(Row(cells[1], cells[2], evm, postal, total or 0))
        elif "Total" in cells[:2]:
            numbers = [0 if c == "-" else integer(c) for c in cells[1:] if c]
            if len(numbers) >= 3 and None not in numbers:
                page.printed = (int(numbers[-3] or 0), int(numbers[-2] or 0), int(numbers[-1] or 0))
    return page


def pages(poll: Poll) -> list[Page]:
    found: list[Page] = []
    for url in sorted(raw.keys(SOURCE)):
        match = wanted(url)
        if match is None or match.group("folder") != poll.folder:
            continue
        if poll.code is not None and match.group("code") != poll.code:
            continue
        artifact = raw.cached(SOURCE, url)
        found.append(read(url, artifact.path.read_bytes(), artifact.retrieved_at, artifact.sha256))
    return found


def final(page: Page, poll: Poll) -> bool:
    """Whether a capture shows the finished count: every round counted, a declared winner with no candidate still
    leading or trailing, or a last update after the evening of the counting day."""
    if page.kind == "candidateswise-":
        states = Counter(r.status for r in page.rows)
        return bool(page.rows) and set(states) <= {"won", "lost"} and states["won"] >= 1
    if page.round is not None:
        return page.round[0] == page.round[1]
    if page.updated is None:
        return False
    return page.updated.date() > poll.counted or (
        page.updated.date() == poll.counted and page.updated.time() >= EVENING
    )


@dataclass
class Seat:
    """One constituency as the pipeline stores it: the page that gives its candidates, the finished candidateswise
    page that settled the winner and the totals (when there is one), and what it cannot take from the site."""

    poll: Poll
    number: int
    name: str
    reserved: str | None
    page: Page
    cards: Page | None
    winner: int
    notes: list[str] = field(default_factory=list)


# ---- choosing what each constituency stores ------------------------------------------------------------------------

NOTA = "NOTA"


def tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def state_of(title: str) -> str | None:
    found = re.search(r"\(([^()]+)\)\s*$", title)
    return None if found is None or found.group(1) in ("SC", "ST") else found.group(1)


def comparable(page: Page) -> set[tuple[str, int]]:
    return {(tidy(r.name), r.total) for r in page.rows if tidy(r.name) != NOTA}


def choose(poll: Poll) -> tuple[dict[tuple[str, int], Seat], list[tuple[str, int, str]]]:
    """The constituencies a poll stores, and for each one left out the reason."""
    by: dict[tuple[str, int], dict[str, Page]] = {}
    for page in pages(poll):
        by.setdefault((page.code, page.seat), {})[page.kind] = page
    seats: dict[tuple[str, int], Seat] = {}
    left: list[tuple[str, int, str]] = []
    for (code, number), pair in sorted(by.items()):
        table = pair.get("Constituencywise")
        cards = pair.get("candidateswise-")
        done = cards if cards is not None and final(cards, poll) else None
        notes: list[str] = []
        if table is not None and table.rows and done is not None and comparable(table) != comparable(done):
            notes.append(CARDS_ONLY)
            table = None
        if table is not None and table.rows and (done is not None or final(table, poll)):
            page = table
        elif done is not None:
            page = done
        else:
            left.append((code, number, "every capture of it was taken while the count was running"))
            continue
        names = [r for r in page.rows if tidy(r.name) != NOTA]
        if done is not None:
            winners = [i for i, r in enumerate(page.rows) if any(_same(c, r) and c.status == "won" for c in done.rows)]
        else:
            top = max(r.total for r in names)
            winners = [i for i, r in enumerate(page.rows) if r.total == top and tidy(r.name) != NOTA]
        if len(winners) != 1:
            left.append((code, number, "the page names no single winner"))
            continue
        titled = page if name_of(page.title)[1] else (done or page)
        _, name, reserved = name_of(titled.title)
        seats[(code, number)] = Seat(poll, number, name, reserved, page, done, winners[0], notes)
    return seats, left


CARDS_ONLY = (
    "The table page of this constituency was captured before its count ended, so the votes come from the candidate "
    "cards, which print each candidate's total only and leave out the votes for none of the above."
)


def _same(card: Row, row: Row) -> bool:
    return tidy(card.name) == tidy(row.name) and card.total == row.total


# ---- the packages --------------------------------------------------------------------------------------------------

CAPTURES = "https://web.archive.org/web/{stamp}id_/{url}"
ACCESS = (
    "The Commission's results site keeps only the current poll live; each page was read from the archive's capture of "
    "the original page and is cited by its original URL."
)
METHOD = (
    "Counted by the Returning Officer of the constituency and printed on the Election Commission of India's results "
    "site: each candidate's votes on the voting machines (stored as election-day votes) and by post, and the votes "
    "for none of the above (NOTA) as an option of their own. The site says that the final data of each constituency "
    "is shared in Form 20, so this set is stored as printed at the stage `official`. The winner is the candidate the "
    "site marks as having won, or, where only the table page is held, the candidate with the most votes."
)
# Election paths that another pipeline's election holds: in/2024-general belongs to the 2024 general election that
# in.form20_wb mints for its polling-station sets. Until that pipeline attaches its contests to this election, as
# in.form20_s3waas does, this election carries no path of its own; its contests keep theirs.
HELD_PATHS = {"in/2024-general"}
RESERVATIONS = {
    "SC": "constituency reserved for Scheduled Castes",
    "ST": "constituency reserved for Scheduled Tribes",
}


def package_key(poll: Poll) -> str:
    return f"{SOURCE}/{poll.name}"


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(p) for p in POLLS)]


def parse() -> None:
    chosen = selections()
    geo(chosen).write()
    registry(chosen).write()
    for poll in POLLS:
        election(poll, *chosen[poll.name]).write()


def emit() -> None:
    package.run("load", packages())


_SELECTIONS: dict[str, tuple[dict[tuple[str, int], Seat], list[tuple[str, int, str]]]] = {}


def selections() -> dict[str, tuple[dict[tuple[str, int], Seat], list[tuple[str, int, str]]]]:
    if not _SELECTIONS:
        _SELECTIONS.update({poll.name: choose(poll) for poll in POLLS})
    return _SELECTIONS


def state_polity(code: str) -> str:
    return key("polity", code)


def area(poll: Poll, code: str, number: int) -> str:
    return key("area", PC_SCHEME if poll.code is None else AC_SCHEME, code, number)


def body(poll: Poll) -> str:
    return key("body", "lok-sabha" if poll.code is None else f"assembly:{poll.code}")


def party(name: str) -> str:
    return key("party", tidy(name))


def source_rows(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Election Commission of India",
        title="Results of general elections (results.eci.gov.in)",
        url=HOST + "/",
        attribution="Election Commission of India",
        notes=ACCESS,
    )


def geo(chosen: dict[str, tuple[dict[tuple[str, int], Seat], list[tuple[str, int, str]]]]) -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The states whose assembly elections the results site's pages give, and the parliamentary and assembly "
        "constituencies of those pages, without shapes",
    )
    p.external.add(INDIA)
    source_rows(p)
    p.add(
        "scheme",
        code=PC_SCHEME,
        polity_id=INDIA,
        kind="electoral_district",
        rank=2,
        noun="parliamentary constituency",
        kinds=RESERVATIONS,
    )
    p.add(
        "scheme",
        code=AC_SCHEME,
        polity_id=INDIA,
        kind="electoral_district",
        rank=3,
        noun="assembly constituency",
        kinds=RESERVATIONS,
    )
    for poll in POLLS:
        if poll.code is not None:
            id = state_polity(poll.code)
            p.add("polity", id=id, kind="federal_unit")
            p.add("polity_parent", polity_id=id, parent_id=INDIA)
            p.name(id, poll.state, "en", "official")
        for (code, number), seat in sorted(chosen[poll.name][0].items()):
            id = area(poll, code, number)
            p.add("area", id=id, scheme_code=PC_SCHEME if poll.code is None else AC_SCHEME)
            p.add("area_code", area_id=id, code=f"{code}-{number}")
            if seat.reserved:
                p.add("area_kind", area_id=id, kind=seat.reserved.lower())
            p.name(id, seat.name, "en", "official")
    return p


def registry(chosen: dict[str, tuple[dict[tuple[str, int], Seat], list[tuple[str, int, str]]]]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Lok Sabha, the assemblies of the states of these elections, their electoral system, and the parties "
        "the results site names",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(INDIA)
    p.add("electoral_system", id=FPTP, polity_id=INDIA, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="Each constituency elects one member by plurality. Voters may choose none of the above (NOTA); those "
        "votes are counted and published but do not affect who is elected.",
        lang="en",
    )
    for poll in POLLS:
        id = body(poll)
        if id in {r["id"] for r in p.rows.get("body", [])}:
            continue
        if poll.code is None:
            p.add("body", id=id, polity_id=INDIA, kind="lower_house")
            p.name(id, "Lok Sabha", "en", "official")
            p.name(id, "लोक सभा", "hi", "native", False)
        else:
            p.add("body", id=id, polity_id=state_polity(poll.code), kind="unicameral")
            p.name(id, f"{poll.state} Legislative Assembly", "en", "official")
    names = sorted(
        {
            tidy(r.party)
            for _, (seats, _) in chosen.items()
            for s in seats.values()
            for r in s.page.rows
            if tidy(r.party) not in ("Independent", "None of the Above", "")
        }
    )
    for name in names:
        p.add("party", id=party(name), kind="party", polity_id=INDIA)
        p.name(party(name), name, "en", "official")
    return p


def put(p: Package, result_set: str, unit: str, option: str | None, channel: int, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=channel,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def election(poll: Poll, seats: dict[tuple[str, int], Seat], left: list[tuple[str, int, str]]) -> Package:
    first, last = poll.days[0], poll.days[-1]
    lower = poll.code is None
    what = "Lok Sabha" if lower else f"{poll.state} Legislative Assembly"
    p = Package(
        package_key(poll),
        SOURCE,
        f"The {first:%Y} general election to the {what} by constituency, from the Election Commission of India's "
        "results site",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(INDIA)
    id = key("election", poll.name)
    path = (
        f"in/{first:%Y}-general" if lower else f"in/{poll.iso.lower() if poll.iso else ''}/{first.isoformat()}-assembly"
    )
    p.add(
        "election",
        id=id,
        polity_id=INDIA if lower else state_polity(poll.code or ""),
        kind="general",
        date_from=first,
        date_to=last,
        date_precision="day",
        status="held",
    )
    if path not in HELD_PATHS:
        p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{first:%Y} Indian general election" if lower else f"{first:%Y} {what} election", "en", "official")
    gaps = f" {len(left):,} of the {poll.seats} constituencies are not stored: " if left else ""
    p.add(
        "note",
        id=key("note", id),
        entity_id=id,
        kind="caveat" if left else "methodology",
        body_md=f"{ACCESS}{gaps}{'every capture of them was taken while the count was running.' if left else ''}",
        lang="en",
    )
    artifacts: set[str] = set()
    for (code, number), seat in sorted(seats.items()):
        tag = f"{poll.name}:{code}:{number}"
        district = area(poll, code, number)
        unit = key("unit", tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=INDIA if lower else state_polity(poll.code or ""),
            scheme_code=PC_SCHEME if lower else AC_SCHEME,
            code=tag,
            area_relation="equals",
            area_id=district,
            kind="standard",
            valid_from=first,
        )
        p.name(unit, f"{number} {seat.name}", "en", "official")
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="body_seats",
            body_id=body(poll),
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="counted",
        )
        title = seat.name if not lower else f"{seat.name}, {chosen_state(seat)}"
        p.name(contest, title if lower else f"{first:%Y} {what} election, {seat.name}", "en", "official")
        # A Lok Sabha contest's path carries the state's name after the constituency's, as it has since it was first
        # published.
        tail = slug(f"{seat.name} {chosen_state(seat)}" if lower else seat.name)
        p.add("slug", path=f"{path}/{code.lower()}-{number}-{tail}", entity_id=contest, is_current=True)
        result_set = key("result_set", tag)
        used = [seat.page] + ([seat.cards] if seat.cards is not None and seat.cards is not seat.page else [])
        stamp = max(page.captured for page in used)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
            as_of=stamp,
        )
        body_md = METHOD + "".join(f" {n}" for n in seat.notes)
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=body_md, lang="en")
        for page in used:
            if page.url not in artifacts:
                artifacts.add(page.url)
                p.artifact(raw.cached(SOURCE, page.url), SOURCE_ID)
        split_votes = seat.page.kind == "Constituencywise"
        for index, row in enumerate(seat.page.rows, start=1):
            option = key("option", tag, index)
            name = tidy(row.name)
            if name == NOTA:
                p.add("contest_option", id=option, contest_id=contest, kind="none_of_the_above", ballot_label="NOTA")
            else:
                solo = tidy(row.party) == "Independent"
                person = key("person", tag, index)
                p.add("person", id=person)
                p.name(person, name, "en", "official")
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None if solo else party(row.party),
                    ballot_position=index,
                    ballot_label=name,
                )
                p.add(
                    "candidacy",
                    id=key("candidacy", tag, index),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    member_of_party_id=None if solo else party(row.party),
                    is_independent=solo,
                )
                if index - 1 == seat.winner:
                    p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            if split_votes:
                for channel, count in ((ELECTION_DAY, row.evm), (POSTAL, row.postal)):
                    if count is not None:
                        put(p, result_set, unit, option, channel, VOTES, count)
            else:
                put(p, result_set, unit, option, ALL, VOTES, row.total)
        if split_votes:
            for channel, counts in (
                (ELECTION_DAY, [r.evm for r in seat.page.rows]),
                (POSTAL, [r.postal for r in seat.page.rows]),
            ):
                if any(c is not None for c in counts):
                    put(p, result_set, unit, None, channel, VALID, sum(c or 0 for c in counts))
    return p


def chosen_state(seat: Seat) -> str:
    return state_of(seat.page.title) or (state_of(seat.cards.title) if seat.cards else None) or ""


# ---- the totals the site prints ------------------------------------------------------------------------------------


def party_totals(url: str) -> dict[str, int] | None:
    """The seats each party won in one state, from its party-wise page, or None while the page shows a count that
    still runs (a leading candidate) or no count at all."""
    artifact = raw.cached(SOURCE, url)
    html = artifact.path.read_text(encoding="utf-8", errors="replace")
    tokens = [clean(t) for t in re.split(r"<[^>]+>", re.sub(r"(?s)<script.*?</script>", "", html))]
    tokens = [t for t in tokens if t]
    try:
        start = tokens.index("Leading", tokens.index("Party Wise Results")) + 2
    except ValueError:
        return None
    won: dict[str, int] = {}
    index = start
    while index + 3 < len(tokens) and tokens[index] != "Total":
        name, wins, leading = tokens[index], tokens[index + 1], tokens[index + 2]
        if not (wins.isdigit() and leading.isdigit() and tokens[index + 3].isdigit()):
            break
        if int(leading):
            return None
        won[name.rsplit(" - ", 1)[0].strip()] = int(wins)
        index += 4
    return won


def compare() -> dict[str, object]:
    """Compare each state's party seat counts with the party-wise page of the archived results, for the states of the
    Lok Sabha election whose constituencies are all stored and whose page shows a finished count."""
    poll = POLLS[0]
    seats, _ = choose(poll)
    out: dict[str, object] = {"states": {}, "matched": 0, "differing": []}
    codes = sorted({code for code, _ in seats})
    expected: dict[str, int] = {}
    for url in sorted(raw.keys(SOURCE)):
        found = summary(url)
        if found is not None and found.group("folder") == poll.folder:
            totals = party_totals(url)
            if totals is not None:
                expected[found.group("code")] = sum(totals.values())
    for code in codes:
        url = f"{HOST}/{poll.folder}/partywiseresult-{code}.htm"
        if url not in raw.keys(SOURCE):
            continue
        totals = party_totals(url)
        mine = Counter(tidy(s.page.rows[s.winner].party) for (c, _), s in seats.items() if c == code)
        if totals is None:
            continue
        if sum(totals.values()) != sum(mine.values()):
            continue
        same = {k: v for k, v in totals.items() if v} == dict(mine)
        out["states"][code] = {"seats": sum(mine.values()), "match": same}  # type: ignore[index]
        if same:
            out["matched"] = int(out["matched"]) + 1  # type: ignore[call-overload]
        else:
            out["differing"].append(code)  # type: ignore[attr-defined]
    return out
