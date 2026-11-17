"""Armenia's proportional community council elections from the Central Electoral Commission (elections.am).

Yerevan has elected its council of elders by party list since 2009, Gyumri and Vanadzor since 2016, and every
community of more than 4,000 voters since the 2021 amendments. The Commission lists each poll on its local election
page. For each poll it publishes a results spreadsheet by polling station (an "ElectionResult" export, with the
electors, participants, invalid ballots, and one column per list), a spreadsheet of the polling stations with their
region, community, and settlement, and a results page with the community's printed totals, the seats each list won,
and the members elected.
"""

import functools
import html
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import uuid5

import fastexcel
import httpx
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.am import cec

SOURCE = "am.local_pr"
SITE = "https://www.elections.am"
LIST_URL = f"{SITE}/Elections/LocalProportional"
ARMENIA = cec.ARMENIA
VOTES, REGISTERED, INVALID, SEATS, VOTERS = 1, 2, 5, 16, 14
ELECTION_TYPE = "7"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
YEREVAN = "Երևան"
STATION = re.compile(r"\d+/\d+")
SUFFIX = re.compile(r"\s*\(\d{4}-\d{4}\)\s*$")
METHOD = (
    "Counted by the Central Electoral Commission per polling station, as published in its results spreadsheet for the "
    "poll. Registered voters are the electors in the polling station's main and supplementary lists, voters are the "
    "participants in the vote, and invalid ballots are as printed. The Commission does not print a count of valid "
    "ballots by station, and its own accounting of ballots at a station can differ from the participants by a stated "
    "inaccuracy. The community's printed totals, the seats each list won, and the members elected are stored as "
    "declared on the poll's results page."
)


@dataclass(frozen=True)
class Poll:
    id: int  # the Commission's election number
    day: date
    marz: str
    community: str
    label: str  # as listed

    @property
    def name(self) -> str:
        return f"{self.marz}:{self.community}"


def clean(text: object) -> str:
    return " ".join(str(text if text is not None else "").replace("\xa0", " ").split())


def export_url(poll: Poll) -> str:
    return f"{SITE}/File/ElectionResult?electionId={poll.id}"


def stations_url(poll: Poll) -> str:
    return f"{SITE}/File/SubDistrictsToExcel?electionId={poll.id}"


def page_url(poll: Poll) -> str:
    return f"{LIST_URL}#electionId={poll.id}"


# Fetching.


def form(client: httpx.Client, **fields: str) -> httpx.Response:
    """The list page or a poll's page, which the Commission returns only for a form post."""
    page = client.get(LIST_URL).text
    token = re.search(r'__RequestVerificationToken" type="hidden" value="([^"]+)"', page)
    if token is None:
        raise LookupError(f"{LIST_URL} has no form")
    response = client.post(
        LIST_URL,
        data={
            "__RequestVerificationToken": token.group(1),
            "Culture": "hy",
            "Input.ElectionTypeId": ELECTION_TYPE,
            "Input.ElectionId": "0",
            **fields,
        },
    )
    response.raise_for_status()
    return response


def client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=120)


def listing() -> raw.Artifact:
    url = f"{LIST_URL}#polls"
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    with client() as http:
        response = form(http, **{"ElectionPaging.PageSize": "5000", "ElectionPaging.PageNumber": "1"})
    return raw.store(
        SOURCE,
        url,
        response.content,
        suffix=".html",
        retrieved_at=datetime.now(UTC),
        media_type=response.headers.get("content-type", "text/html"),
    )


@functools.cache
def polls() -> list[Poll]:
    text = listing().path.read_text(encoding="utf-8")
    out: list[Poll] = []
    for row in re.findall(r"<tr>(.*?)</tr>", text, re.DOTALL):
        found = re.search(r"OpenElection\((\d+)\)", row)
        if found is None:
            continue
        cells = [
            clean(html.unescape(re.sub(r"<[^>]+>", " ", cell)))
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        ]
        cells = [cell for cell in cells if cell]
        day, label = datetime.strptime(cells[0], "%d.%m.%Y").date(), cells[1]
        marz, _, rest = label.partition(" - ")
        if not rest:
            marz, rest = YEREVAN, label
        out.append(Poll(int(found.group(1)), day, marz, SUFFIX.sub("", rest), label))
    return sorted(out, key=lambda poll: (poll.day, poll.marz, poll.community, poll.id))


def page(poll: Poll) -> raw.Artifact:
    url = page_url(poll)
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        pass
    id = str(poll.id)
    with client() as http:
        response = form(
            http,
            **{
                "Input.ElectionId": id,
                "Current.ElectionId": id,
                "Input.RegionCode": "",
                "Input.CommunityCode": "",
                "Input.SubDistrictId": "",
                "Input.Tab1": "active-tab",
            },
        )
    return raw.store(
        SOURCE,
        url,
        response.content,
        suffix=".html",
        retrieved_at=datetime.now(UTC),
        media_type=response.headers.get("content-type", "text/html"),
    )


def fetch() -> None:
    listing()
    for poll in polls():
        raw.fetch(SOURCE, export_url(poll), suffix=".xlsx")
        raw.fetch(SOURCE, stations_url(poll), suffix=".xlsx")
        page(poll)
    cec_places()


def cec_places() -> None:
    """The national elections' polling station lists, which name the communities the archive already holds."""
    for e in cec.ELECTIONS:
        raw.fetch(cec.SOURCE, cec.stations_url(e), suffix=".xlsx")


# Reading the spreadsheets.


@functools.cache
def sheet(url: str) -> list[list[str]]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = book.load_sheet_by_idx(0, header_row=None, dtypes="string").to_polars()
    return [[clean(cell) for cell in row] for row in frame.rows()]


def number(text: str) -> int | None:
    if text in ("", "-"):
        return None
    try:
        return int(float(text.replace(" ", "").replace(",", ".")))
    except ValueError:
        return None


def code(text: str) -> str:
    """A polling station's number, as '<commission>/<station>' without leading zeros."""
    return "/".join(str(int(part)) for part in text.split("/"))


@dataclass
class Station:
    code: str
    registered: int | None
    voters: int | None
    invalid: int | None
    options: dict[str, int]


@dataclass
class Table:
    stations: dict[str, Station]
    order: list[str]  # the list names, in column order


@functools.cache
def export(poll: Poll) -> Table:
    rows = sheet(export_url(poll))
    head = next((i for i, row in enumerate(rows) if row[0] == "ՏԸՀ N"), None)
    if head is None:
        return Table({}, [])
    names = rows[head]
    registered = names.index("Ընտրողների ընդհանուր թիվը")
    voters = names.index("Քվեարկության մասնակիցների ընդհանուր թիվը")
    invalid = next(i for i, name in enumerate(names) if name.startswith("Անվավեր"))
    end = next(i for i, name in enumerate(names) if name.startswith("Անճշտություն"))
    options = list(range(invalid + 1, end))
    stations: dict[str, Station] = {}
    for row in rows[head + 1 :]:
        if not STATION.fullmatch(row[1]):
            continue
        at = code(row[1])
        if at in stations:
            raise ValueError(f"{poll.id}: polling station {at} appears twice")
        votes = {names[i]: v for i in options if (v := number(row[i])) is not None}
        stations[at] = Station(at, number(row[registered]), number(row[voters]), number(row[invalid]), votes)
    return Table(stations, [names[i] for i in options])


@functools.cache
def places(poll: Poll) -> dict[str, tuple[str, str]]:
    """Each polling station's (region, community) in the poll's list of stations."""
    out: dict[str, tuple[str, str]] = {}
    for row in sheet(stations_url(poll))[1:]:
        if len(row) > 4 and STATION.fullmatch(row[4]):
            out[code(row[4])] = (row[0], row[1])
    return out


@functools.cache
def held() -> list[Poll]:
    return [poll for poll in polls() if export(poll).stations]


# The poll's page.


@dataclass
class Row:
    abbreviation: str
    votes: int
    seats: int
    elected: int
    members: list[str]


@dataclass
class Page:
    totals: dict[str, int]  # printed label -> figure
    rows: list[Row]


def won(data: Page) -> list[int]:
    """The seats each list won: its mandates where the page prints them, else the members it lists as elected."""
    if any(row.seats for row in data.rows):
        return [row.seats for row in data.rows]
    return [row.elected for row in data.rows]


def cells(text: str, pattern: str = r"<t[dh][^>]*>(.*?)</t[dh]>") -> list[str]:
    return [clean(html.unescape(re.sub(r"<[^>]+>", " ", cell))) for cell in re.findall(pattern, text, re.DOTALL)]


def person(text: str) -> str:
    """A member's name as the page prints it, with a name set wholly in capitals written in title case."""
    name = clean(text)
    return " ".join(part.capitalize() for part in name.split()) if name == name.upper() else name


@functools.cache
def results(poll: Poll) -> Page:
    text = page(poll).path.read_text(encoding="utf-8")
    start = text.index('class="election-result"')
    totals: dict[str, int] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text[start : text.index("</table>", start)], re.DOTALL):
        found = cells(row)
        if len(found) >= 2 and (figure := number(found[1])) is not None:
            totals.setdefault(found[0], figure)
    final = text.index('class="election-result-final"')
    end = text.find('id="Tab', final)
    body = text[final : end if end > 0 else len(text)]
    rows: list[Row] = []
    for block in re.split(r'<tr[^>]*class="main-row"', body)[1:]:
        main, _, sub = block.partition('class="sub-row"')
        head = cells(main.split("</tr>")[0])
        figures = [c for c in head if c]
        members = [
            person(" ".join(spans))
            for part in re.findall(r"<tr>\s*<td>(.*?)</td>\s*</tr>", sub, re.DOTALL)
            if (spans := [clean(html.unescape(s)) for s in re.findall(r"<span>(.*?)</span>", part, re.DOTALL)])
        ]
        rows.append(
            Row(
                figures[0],
                number(figures[1]) or 0,
                number(figures[3]) or 0,
                number(figures[4]) or 0,
                members,
            )
        )
    return Page(totals, rows)


def against(name: str) -> bool:
    """The column that counts the ballots marked against every list."""
    return name.startswith("Դեմ")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).upper()
    return " ".join(re.sub(r"[«»\"“”'`´՛]", " ", text).split())


def summary() -> None:
    sums: Counter[str] = Counter()
    for poll in held():
        table = export(poll)
        data = results(poll)
        registered = sum(s.registered or 0 for s in table.stations.values())
        voters = sum(s.voters or 0 for s in table.stations.values())
        invalid = sum(s.invalid or 0 for s in table.stations.values())
        folded = {fold(label): value for label, value in data.totals.items()}
        print(
            poll.id,
            poll.day,
            poll.label,
            len(table.stations),
            len(table.order),
            len(data.rows),
            (registered, data.totals.get("Ընտրողների ընդհանուր թիվը")),
            (voters, data.totals.get("Քվեարկության մասնակիցների ընդհանուր թիվը")),
            (invalid, data.totals.get("Անվավեր քվեաթերթիկների թիվը")),
            (sum(row.seats for row in data.rows), sum(row.elected for row in data.rows)),
            sum(len(row.members) for row in data.rows),
        )
        for name in table.order:
            here = sum(s.options.get(name, 0) for s in table.stations.values())
            if folded.get(fold(name)) != here:
                sums["option mismatch"] += 1
                print("   option", name, here, folded.get(fold(name)))
    print(dict(sums))


# Names, in Latin letters where the Commission prints only Armenian.

LATIN = {
    "ա": "a", "բ": "b", "գ": "g", "դ": "d", "ե": "e", "զ": "z", "է": "e", "ը": "y", "թ": "t", "ժ": "zh",
    "ի": "i", "լ": "l", "խ": "kh", "ծ": "ts", "կ": "k", "հ": "h", "ձ": "dz", "ղ": "gh", "ճ": "ch", "մ": "m",
    "յ": "y", "ն": "n", "շ": "sh", "ո": "o", "չ": "ch", "պ": "p", "ջ": "j", "ռ": "r", "ս": "s", "վ": "v",
    "տ": "t", "ր": "r", "ց": "ts", "ւ": "v", "փ": "p", "ք": "k", "օ": "o", "ֆ": "f", "և": "ev",
}  # fmt: skip


def latin(text: str) -> str:
    """A place's name in Latin letters: the usual English spellings, such as Yerevan, Gyumri, and Vanadzor."""
    words = []
    for word in text.lower().split():
        out = ""
        i = 0
        while i < len(word):
            letter = word[i]
            if word[i : i + 2] == "ու":
                out += "u"
                i += 2
                continue
            if i == 0 and letter == "ե":
                out += "ye"
            elif i == 0 and letter == "ո":
                out += "vo"
            else:
                out += LATIN.get(letter, letter)
            i += 1
        words.append(out.capitalize())
    return " ".join(words)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", latin(text).lower()).strip("-")


# Places.


@functools.cache
def known() -> frozenset[tuple[str, str]]:
    """The (region, community) pairs the national elections' polling station lists already name."""
    out: set[tuple[str, str]] = set()
    for e in cec.ELECTIONS:
        for row in cec.sheet(cec.stations_url(e))[1:]:
            if cec.STATION.fullmatch(row[4]):
                out.add((row[0], row[1]))
    return frozenset(out)


def community(marz: str, name: str) -> str:
    """The archive's area for a community: the one the national elections hold, else a new one."""
    return (
        cec.area("am.community", f"{marz}:{name}")
        if (marz, name) in known()
        else key("area", "am.community", f"{marz}:{name}")
    )


@functools.cache
def communities() -> list[tuple[str, str]]:
    out = {(poll.marz, poll.community) for poll in held()}
    for poll in held():
        out |= set(places(poll).values())
    return sorted(out)


def package_key(day: date) -> str:
    return f"{SOURCE}/{day}"


def days() -> list[date]:
    return sorted({poll.day for poll in held()})


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(day) for day in days())]


def parse() -> None:
    geo().write()
    registry().write()
    for day in days():
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The communities that held proportional community council elections, and the communities named in the "
        "Central Electoral Commission's lists of polling stations for them",
        depends=[f"{cec.SOURCE}/geo"],
    )
    p.external |= {ARMENIA}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Հայաստանի Հանրապետության կենտրոնական ընտրական հանձնաժողով",
        title="Local elections: proportional community council polls, results by polling station",
        url=LIST_URL,
        license="Official results of the Republic of Armenia, published for public use; reuse with the source "
        "acknowledged",
        license_url=f"{SITE}/",
        attribution="Central Electoral Commission of the Republic of Armenia, elections.am",
    )
    p.artifact(listing(), SOURCE_ID)
    for marz, name in communities():
        if (marz, name) in known():
            continue
        if marz not in cec.MARZ:
            raise ValueError(f"region {marz!r} is not one of Armenia's 11")
        id = community(marz, name)
        p.add("area", id=id, scheme_code="am.community")
        p.add("area_code", area_id=id, code=f"{marz}:{name}")
        p.add("area_parent", area_id=id, parent_id=cec.area("am.marz", marz))
        p.name(id, name, "hy", "official")
        p.name(id, latin(name), "en", "english", False)
    return p


# Parties and councils.


def party(name: str) -> str:
    return key("party", fold(name))


def body(marz: str, name: str) -> str:
    return key("body", f"{marz}:{name}")


def party_kind(name: str) -> str:
    return "bloc" if "ԴԱՇԻՆՔ" in fold(name) and "ԴԱՇՆԱԿՑՈՒԹՅՈՒՆ" not in fold(name) else "party"


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The community councils, and the parties and alliances on the ballots of their proportional elections, "
        "from the Central Electoral Commission",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {ARMENIA}
    for marz, name in sorted({(poll.marz, poll.community) for poll in held()}):
        id = body(marz, name)
        p.add("body", id=id, polity_id=ARMENIA, kind="council")
        p.name(id, f"{latin(name)} Community Council", "en", "english")
        p.name(id, f"{name} համայնքի ավագանի", "hy", "official", False)
    names: dict[str, str] = {}
    for poll in held():
        for name in export(poll).order:
            if against(name):
                continue
            names.setdefault(party(name), clean(name))
    for id, name in sorted(names.items()):
        p.add("party", id=id, kind=party_kind(name), polity_id=ARMENIA)
        p.add("external_id", entity_id=id, scheme="am.local_pr.party", value=fold(name))
        p.name(id, name, "hy", "official")
    return p


# One package per election day.


class Facts:
    def __init__(self) -> None:
        self.columns: dict[str, list[object]] = {
            name: [] for name in ("result_set_id", "unit_id", "option_id", "measure", "value")
        }

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
        if value is None:
            return
        for column, item in zip(self.columns.values(), (result_set, unit, option, measure, value), strict=True):
            column.append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def declared_totals(poll: Poll, table: Table, data: Page) -> dict[str, int]:
    """The community's printed totals, keyed 'registered', 'voters', 'invalid', and 'option:<list>'."""
    out: dict[str, int] = {}
    folded = {fold(name): name for name in table.order}
    for label, value in data.totals.items():
        if label == "Ընտրողների ընդհանուր թիվը":
            out["registered"] = value
        elif label == "Քվեարկության մասնակիցների ընդհանուր թիվը" or label.startswith("Գրանցված և քվեաթերթիկ ստացած"):
            out.setdefault("voters", value)
        elif label == "Անվավեր քվեաթերթիկների թիվը":
            out["invalid"] = value
        elif fold(label) in folded:
            out[f"option:{folded[fold(label)]}"] = value
    return out


def election(day: date) -> Package:
    polls_here = [poll for poll in held() if poll.day == day]
    p = Package(
        package_key(day),
        SOURCE,
        f"The proportional community council elections of {day} by polling station from the Central Electoral "
        "Commission",
        depends=[f"{cec.SOURCE}/geo", f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ARMENIA}
    election_id = key("election", day)
    english = f"Armenian community council elections, {MONTHS[day.month - 1]} {day.day}, {day.year}"
    p.add("election", id=election_id, polity_id=ARMENIA, kind="local", date_from=day, date_to=day, status="held")
    p.name(election_id, english, "en", "official")
    p.name(election_id, f"Համայնքի ավագանու ընտրություններ, {day:%d.%m.%Y}", "hy", "native", False)
    path = f"am/{day}-community-councils"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    values = Facts()
    accounting = {"registered": REGISTERED, "voters": VOTERS, "invalid": INVALID}
    seen_units: set[str] = set()
    shortfalls: list[str] = []
    for poll in polls_here:
        table, data = export(poll), results(poll)
        p.artifact(raw.cached(SOURCE, export_url(poll)), SOURCE_ID)
        p.artifact(page(poll), SOURCE_ID)
        p.artifact(raw.cached(SOURCE, stations_url(poll)), SOURCE_ID)
        contest = key("contest", poll.id)
        district = community(poll.marz, poll.community)
        seats = sum(won(data))
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=body(poll.marz, poll.community),
            district_area_id=district,
            magnitude=seats or None,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(
            contest,
            f"{latin(poll.community)} community council election, {english.split(', ', 1)[1]}",
            "en",
            "official",
        )
        p.name(
            contest, f"{poll.community} համայնքի ավագանու ընտրություններ, {poll.day:%d.%m.%Y}", "hy", "native", False
        )
        p.add("slug", path=f"{path}/{slug(poll.community)}", entity_id=contest, is_current=True)
        result_set = key("result_set", poll.id)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        options: dict[str, str] = {}
        for position, name in enumerate(table.order, 1):
            option = options[name] = key("option", poll.id, fold(name))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="none_of_the_above" if against(name) else "list",
                party_id=None if against(name) else party(name),
                ballot_position=position,
                ballot_label=clean(name),
            )
        declared = declared_totals(poll, table, data)
        for station in table.stations.values():
            unit = key("unit", day, station.code)
            if unit in seen_units:
                raise ValueError(f"{day}: polling station {station.code} is in two polls")
            seen_units.add(unit)
            marz, name = places(poll).get(station.code, (poll.marz, poll.community))
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ARMENIA,
                scheme_code="am.precinct",
                code=f"{day}:{station.code}",
                area_relation="within",
                area_id=community(marz, name),
                kind="standard",
            )
            values.add(result_set, unit, None, REGISTERED, station.registered)
            values.add(result_set, unit, None, VOTERS, station.voters)
            values.add(result_set, unit, None, INVALID, station.invalid)
            for list_name, option in options.items():
                values.add(result_set, unit, option, VOTES, station.options.get(list_name))
        for name, measure in accounting.items():
            if name in declared:
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=declared[name])
        for list_name, option in options.items():
            if f"option:{list_name}" in declared:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=option,
                    measure=VOTES,
                    value=declared[f"option:{list_name}"],
                )
        if reason := outcomes(p, poll, result_set, contest, options, table, data):
            shortfalls.append(reason)
        over = [
            (station.code, station.registered, station.voters)
            for station in table.stations.values()
            if station.registered is not None and station.voters is not None and station.voters > station.registered
        ]
        if over:
            listed = ", ".join(f"{c} ({v} participants, {r} registered)" for c, r, v in over)
            reason = f"The Commission's own results table prints more participants than registered voters at {listed}"
            p.accepted["accounting.turnout"] = (p.accepted.get("accounting.turnout", "") + "; " + reason).strip("; ")
    if shortfalls:
        p.accept(
            "outcomes.list_members",
            "The Commission's results page lists fewer members as elected than the mandates it prints for the list: "
            + "; ".join(shortfalls),
        )
    p.frame("result_value", values.frame())
    print(f"{package_key(day)}: {len(polls_here)} polls, {len(seen_units)} stations", flush=True)
    return p


def outcomes(
    p: Package, poll: Poll, result_set: str, contest: str, options: dict[str, str], table: Table, data: Page
) -> str | None:
    """The seats each list won and the members elected, as the poll's page prints them. The page prints its lists by
    abbreviation, so each is matched to the export's column that holds the same votes in total."""
    totals = {
        name: sum(s.options.get(name, 0) for s in table.stations.values()) for name in table.order if not against(name)
    }
    free = list(totals)
    matched: list[str] = []
    for row in data.rows:
        found = next((name for name in free if totals[name] == row.votes), None)
        if found is None:
            raise ValueError(
                f"{poll.id}: no list in the export has the {row.votes} votes the page prints for {row.abbreviation}"
            )
        free.remove(found)
        matched.append(found)
    short: list[str] = []
    for list_name, row, seats in zip(matched, data.rows, won(data), strict=True):
        if len(row.members) != seats:
            short.append(f"{poll.community} {poll.day} ({len(row.members)} of {seats} seats for {clean(list_name)})")
        option = options[list_name]
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="won" if seats else "lost",
            seats=seats,
        )
        for n, member in enumerate(row.members, 1):
            person = key("person", poll.id, option, n)
            candidacy = key("candidacy", poll.id, option, n)
            p.add("person", id=person)
            p.name(person, member, "hy", "official")
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                ballot_name=member,
            )
            p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
    if short:
        return "; ".join(short)
    return None
