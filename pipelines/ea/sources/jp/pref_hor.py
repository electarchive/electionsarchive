"""House of Representatives general elections by single-member district, from prefectural election commissions.

The Ministry of Home Affairs published only prefecture turnout and nationwide party totals online for the 1996 and
2000 general elections (`jp.mic_release`), and its 結果調 books for them are in print only. Some prefectural election
commissions still publish their own count of those elections. Chiba's commission posts the 2000 election's 開票調 (the
count of each single-member district by municipality, the candidates' votes with the fractions that splitting
namesake ballots leaves, and invalid ballots) and 投票調 (electors and voters by municipality) as Excel workbooks.

Each prefecture and election is one package, `jp.pref_hor/<prefecture>-<year>`. It adds one contest for each of the
prefecture's districts to the election that `jp.mic_release` holds, with the district's candidates as options and the
municipalities (and the parts of cities the districts split) as reporting units. The parties and people are this
pipeline's own.
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import uuid5

import xlrd

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.jp import mic_release

SOURCE = "jp.pref_hor"
JAPAN = mic_release.JAPAN
REGISTERED, BALLOTS, VALID, INVALID, VOTERS = 2, 3, 4, 5, 14
VOTES = 1
SCHEME = "jp.counting_district"


@dataclass(frozen=True)
class Count:
    prefecture: int
    native: str
    english: str
    year: int
    districts: int
    publisher: str
    page: str
    votes: str
    turnout: str
    title: str
    license: str
    license_url: str
    attribution: str
    seats: dict[str, int]


CHIBA = "https://www.pref.chiba.lg.jp/senkan/kakokekka/kako"
COUNTS = {
    "chiba-2000": Count(
        12,
        "千葉県",
        "Chiba",
        2000,
        12,
        "千葉県選挙管理委員会",
        f"{CHIBA}/h12-0625.html",
        f"{CHIBA}/documents/shugisk.xls",
        f"{CHIBA}/documents/shugist.xls",
        "平成12年6月25日執行衆議院議員総選挙 小選挙区選挙 開票調・投票調",
        "Copyright Chiba Prefecture (site terms); counts published as facts with attribution",
        "https://www.pref.chiba.lg.jp/homepage/about-site/link.html",
        "出典：千葉県選挙管理委員会",
        {"自由民主党": 7, "民主党": 5},
    ),
}
PARTIES = {
    "自民党": ("自由民主党", "Liberal Democratic Party"),
    "民主党": ("民主党", "Democratic Party of Japan"),
    "共産党": ("日本共産党", "Japanese Communist Party"),
    "公明党": ("公明党", "Komeito"),
    "社民党": ("社会民主党", "Social Democratic Party"),
    "自由党": ("自由党", "Liberal Party"),
    "保守党": ("保守党", "New Conservative Party"),
    "自由連合": ("政党自由連合", "Liberal League"),
}
INDEPENDENT = "無所属"
METHOD = (
    "The {native} Election Commission's count (開票調) of the district by municipality, with each city that the "
    "district shares with another district counted for its own part. A candidate's votes keep the fractions that "
    "splitting ballots for namesakes leaves (按分票), as the commission prints them under each row. Valid ballots are "
    "ballots cast less invalid ballots; they exceed the candidates' votes by the thousandths that splitting drops "
    "(按分切捨て). Electors and voters come from the commission's turnout table (投票調); voters less ballots cast are "
    "ballots taken home or otherwise not returned. The commission does not mark the winner; the archive records the "
    "candidate with the most votes, and the winners' parties match the seats the commission lists for the prefecture."
)


def packages() -> list[str]:
    return [f"{SOURCE}/{key}" for key in COUNTS]


def fetch() -> None:
    for count in COUNTS.values():
        raw.fetch(SOURCE, count.page, suffix=".html")
        for url in (count.votes, count.turnout):
            raw.fetch(SOURCE, url, suffix=".xls", check=_xls)


def _xls(path: object) -> None:
    with open(str(path), "rb") as file:
        head = file.read(8)
        if head != bytes.fromhex("d0cf11e0a1b11ae1") and head[:2] != bytes.fromhex("0904"):
            raise ValueError(f"{path}: not an Excel workbook")


def parse() -> None:
    for key in COUNTS:
        build(key).write()


def emit() -> None:
    package.run("load", packages())


def grid(path: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    for sheet in xlrd.open_workbook(str(path)).sheets():
        for at in range(sheet.nrows):
            cells = [unicodedata.normalize("NFKC", str(value)).strip() for value in sheet.row_values(at)]
            rows.append(cells + [""] * (sheet.ncols - len(cells)))
    return rows


def number(text: str) -> int:
    if not re.fullmatch(r"\d{1,3}(,\d{3})*", text):
        raise ValueError(f"{text!r} is not a count")
    return int(text.replace(",", ""))


def fraction(text: str) -> Decimal:
    if text in ("", "."):
        return Decimal(0)
    if not re.fullmatch(r"\.\d{3}", text):
        raise ValueError(f"{text!r} is not a fraction")
    return Decimal(text)


@dataclass(frozen=True)
class Candidate:
    name: str
    party: str


@dataclass(frozen=True)
class Row:
    place: str
    votes: list[Decimal]
    valid: int
    invalid: int
    cast: int
    unreturned: int


@dataclass
class District:
    number: int
    candidates: list[Candidate]
    rows: list[Row]
    total: Row | None = None


SUBTOTAL = re.compile(r"^(?:市計|町村計|.+郡計)$")
CLOSE = re.compile(r"^(\d+)区計$")


def districts(path: Path, label: str) -> dict[int, District]:
    """Read the 開票調: one block per district, a header of candidates, then a row and a fractions row per place."""
    rows = grid(path)
    out: dict[int, District] = {}
    current: District | None = None
    total_at = cut_at = other_at = invalid_at = cast_at = kept_at = 0
    index = 0
    while index < len(rows):
        row = rows[index]
        head = next((re.fullmatch(r"\(選挙区 ?(\d+)\)", cell) for cell in row if cell.startswith("(選挙区")), None)
        if head is not None:
            surnames, given, parties = rows[index + 2], rows[index + 3], rows[index + 4]
            if surnames[0] != "姓" or given[0] != "名" or not parties[0].startswith("市区町村名"):
                raise ValueError(f"{label}: unexpected header at row {index}")
            total_at = surnames.index("計")
            cut_at, other_at, invalid_at, cast_at, kept_at = (total_at + step for step in (2, 3, 4, 5, 6))
            numbers = rows[index + 1]
            if (
                not numbers[other_at].startswith("いずれの候")
                or numbers[invalid_at] != "無効投票数"
                or surnames[cast_at] != "投票総数"
                or not surnames[kept_at].startswith("持帰り")
            ):
                raise ValueError(f"{label}: unexpected column heads at row {index}")
            candidates = [
                Candidate(f"{surnames[at].strip()} {given[at].strip()}", parties[at])
                for at in range(1, total_at)
                if surnames[at]
            ]
            if any(not surnames[at] for at in range(1, len(candidates) + 1)):
                raise ValueError(f"{label}: gap among candidates at row {index}")
            seat = int(head[1])
            if seat in out:
                if out[seat].candidates != candidates:
                    raise ValueError(f"{label}: district {seat} repeats with other candidates")
                current = out[seat]
            else:
                current = out[seat] = District(seat, candidates, [])
            index += 5
            continue
        place = row[0]
        if current is None or not place or place in ("o", "*", "#", "."):
            index += 1
            continue
        n = len(current.candidates)
        if any(row[at] for at in range(1 + n, total_at)) or row[other_at]:
            raise ValueError(f"{label}: {place} has figures outside the candidates' columns")
        below = rows[index + 1] if index + 1 < len(rows) else []
        parts = below[: total_at + 1] if below and below[0] in ("o", "*", "#") else [""] * (total_at + 1)
        votes = [number(row[1 + at]) + fraction(parts[1 + at]) for at in range(n)]
        printed = number(row[total_at]) + fraction(parts[total_at])
        if sum(votes) != printed:
            raise ValueError(f"{label}: {place} candidates' votes do not add up to {printed}")
        exact = printed + fraction(row[cut_at])
        if exact != exact.to_integral_value():
            raise ValueError(f"{label}: {place} votes and dropped fractions add up to {exact}")
        invalid, cast = number(row[invalid_at] or "0"), number(row[cast_at])
        if int(exact) + invalid != cast:
            raise ValueError(f"{label}: {place} valid and invalid ballots do not add up to ballots cast")
        entry = Row(place, votes, int(exact), invalid, cast, number(row[kept_at] or "0"))
        closing = CLOSE.fullmatch(place)
        if closing is not None:
            if int(closing[1]) != current.number:
                raise ValueError(f"{label}: district {current.number} closes as {place}")
            current.total = entry
        elif not SUBTOTAL.fullmatch(place):
            current.rows.append(entry)
        index += 1
    for seat, district in out.items():
        if district.total is None:
            raise ValueError(f"{label}: district {seat} has no total")
        summed = [sum(row.votes[at] for row in district.rows) for at in range(len(district.candidates))]
        if summed != district.total.votes or sum(row.cast for row in district.rows) != district.total.cast:
            raise ValueError(f"{label}: district {seat}'s places do not add up to its total")
    return out


def turnout(path: Path, label: str) -> dict[tuple[int, str], tuple[int, int]]:
    """Read the 投票調: electors and voters by place, each district's places closed by its total row."""
    out: dict[tuple[int, str], tuple[int, int]] = {}
    pending: list[tuple[str, int, int]] = []
    for row in grid(path):
        place = row[0].lstrip("* ").strip()
        if not place or not re.fullmatch(r"\d{1,3}(,\d{3})*", row[3] if len(row) > 3 else ""):
            continue
        registered, voters = number(row[3]), number(row[6])
        if number(row[1]) + number(row[2]) != registered or number(row[4]) + number(row[5]) != voters:
            raise ValueError(f"{label}: {place} men and women do not add up")
        closing = CLOSE.fullmatch(place)
        if closing is not None:
            seat = int(closing[1])
            if (sum(r for _, r, _ in pending), sum(v for _, _, v in pending)) != (registered, voters):
                raise ValueError(f"{label}: district {seat}'s places do not add up to its total")
            out |= {(seat, name): (r, v) for name, r, v in pending}
            out[seat, ""] = (registered, voters)
            pending = []
        elif not SUBTOTAL.fullmatch(place) and not row[0].startswith("*"):
            pending.append((place, registered, voters))
    if pending:
        raise ValueError(f"{label}: places after the last district total")
    return out


def party(name: str) -> str:
    return str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fparty\x1f{name}"))


def build(key: str) -> Package:
    count = COUNTS[key]
    release = mic_release.RELEASES[count.year]
    held = release.held
    label = f"{SOURCE}/{key}"
    page = raw.cached(SOURCE, count.page)
    votes_file, turnout_file = raw.cached(SOURCE, count.votes), raw.cached(SOURCE, count.turnout)
    found = districts(votes_file.path, label)
    electors = turnout(turnout_file.path, label)
    if sorted(found) != list(range(1, count.districts + 1)):
        raise ValueError(f"{label}: districts {sorted(found)}")

    p = Package(
        label,
        SOURCE,
        f"{count.native} single-member districts in the {release.number}th House of Representatives general election "
        f"of {held:%-d %B %Y}, by municipality, from the prefectural election commission's count",
        depends=[f"{mic_release.SOURCE}/hor-{count.year}"],
    )
    area = mic_release.prefecture_area(count.prefecture)
    polity = mic_release.prefecture_polity(count.prefecture)
    p.external |= {JAPAN, area, polity, SCHEME}
    election = mic_release.key("election", held.isoformat())
    path = f"jp/{held.isoformat()}-general"

    source = p.id("source", count.publisher, count.title)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=count.publisher,
        title=count.title,
        url=count.page,
        license=count.license,
        license_url=count.license_url,
        attribution=count.attribution,
    )
    for artifact in (page, votes_file, turnout_file):
        p.artifact(artifact, source)

    used = sorted(
        {c.party for d in found.values() for c in d.candidates if c.party != INDEPENDENT}, key=list(PARTIES).index
    )
    for short in used:
        official, english = PARTIES[short]
        id = party(official)
        p.add("party", id=id, kind="party", polity_id=JAPAN)
        p.name(id, official, "ja", "official")
        p.name(id, english, "en", "official", False)

    won: dict[str, int] = {}
    for seat, district in sorted(found.items()):
        code = f"{count.prefecture:02d}{seat:02d}"
        contest = p.id("contest", held, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            magnitude=1,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"{count.english} {seat}", "en", "official")
        p.name(contest, f"{count.native}第{seat}区", "ja", "native", False)
        p.add("slug", path=f"{path}/{changeset.slug(count.english)}-{seat}", entity_id=contest, is_current=True)
        result_set = p.id("result_set", held, code)
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
            "note",
            id=p.id("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD.format(native=count.native.removesuffix("県")),
            lang="en",
        )
        options = []
        for position, candidate in enumerate(district.candidates, 1):
            option = p.id("option", held, code, position)
            options.append(option)
            member = None if candidate.party == INDEPENDENT else party(PARTIES[candidate.party][0])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=member,
                ballot_position=position,
                ballot_label=candidate.name,
            )
            person = p.id("person", held, code, candidate.name)
            p.add("person", id=person)
            p.name(person, candidate.name, "ja", "official")
            p.add(
                "candidacy",
                id=p.id("candidacy", held, code, position),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=candidate.name,
                member_of_party_id=member,
                is_independent=member is None,
            )
        total = district.total
        if total is None:
            raise ValueError(f"{label}: district {seat} has no total")
        ranked = sorted(range(len(options)), key=lambda at: total.votes[at], reverse=True)
        if total.votes[ranked[0]] == total.votes[ranked[1]]:
            raise ValueError(f"{label}: district {seat} is tied")
        if total.votes[ranked[0]] * 6 < total.valid:
            raise ValueError(f"{label}: district {seat}'s leader falls short of the legal threshold")
        p.add("outcome", result_set_id=result_set, option_id=options[ranked[0]], kind="elected", seats=1)
        leader = district.candidates[ranked[0]].party
        leader = PARTIES[leader][0] if leader in PARTIES else leader
        won[leader] = won.get(leader, 0) + 1

        registered, voters = electors[seat, ""]
        if voters != total.cast + total.unreturned:
            raise ValueError(f"{label}: district {seat}'s voters are not ballots cast plus ballots not returned")
        for option, value in zip(options, total.votes, strict=True):
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)
        for measure, value in (
            (REGISTERED, registered),
            (VOTERS, voters),
            (BALLOTS, total.cast),
            (VALID, total.valid),
            (INVALID, total.invalid),
        ):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)

        for row in district.rows:
            if (seat, row.place) not in electors:
                raise ValueError(f"{label}: district {seat}'s {row.place} has no turnout row")
            registered, voters = electors[seat, row.place]
            if voters != row.cast + row.unreturned:
                raise ValueError(f"{label}: {row.place}'s voters are not ballots cast plus ballots not returned")
            unit = p.id("unit", held, code, row.place)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=polity,
                scheme_code=SCHEME,
                code=f"{held.isoformat()}:{code}:{row.place}",
                area_relation="within",
                area_id=area,
                kind="standard",
            )
            p.name(unit, row.place, "ja", "official")
            figures: list[tuple[str | None, int, int | Decimal]] = [
                *((option, VOTES, value) for option, value in zip(options, row.votes, strict=True)),
                (None, REGISTERED, registered),
                (None, VOTERS, voters),
                (None, BALLOTS, row.cast),
                (None, VALID, row.valid),
                (None, INVALID, row.invalid),
            ]
            for option, measure, value in figures:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        places = {place for (number_, place) in electors if number_ == seat and place}
        if places != {row.place for row in district.rows}:
            raise ValueError(f"{label}: district {seat}'s places differ between the two tables")
    if won != count.seats:
        raise ValueError(f"{label}: the leaders' parties {won} differ from the seats the commission lists")
    if any(any(v != v.to_integral_value() for v in d.total.votes) for d in found.values() if d.total):
        p.accept(
            "accounting.votes_valid",
            "Where namesake ballots were split, the commission prints each candidate's votes to three decimals and "
            "drops the remaining thousandths (按分切捨て), so the candidates' votes fall short of valid ballots by that "
            "remainder.",
        )
    return p
