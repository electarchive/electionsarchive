"""Japan's House of Representatives general elections of 1996 (41st) and 2000 (42nd), by prefecture.

The Ministry of Home Affairs announced each result in a news release, which the Ministry of Internal Affairs and
Communications later reposted as a PDF. Those PDFs are gone from the live site, so they are read from the National Diet
Library's WARP capture. Each release gives every prefecture's electors and voters for the single-member districts and
for proportional representation, and nationwide party totals: candidates, seats in each tier, and votes in each tier.
The ministry never published district or block results for these two elections online; its 結果調 books for them are
in print only. So each election is one package, `jp.mic_release/hor-<year>`, with a nationwide single-member tally and
a nationwide proportional tally, built on `jp.mic_release/registry`, which holds the parties. Prefecture units point at
the prefecture areas and polities that `jp.mlit` loaded.
"""

import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import product
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.jp.mic import ATTRIBUTION, LICENSE, LICENSE_URL
from ea.sources.jp.mlit import PREFECTURES

SOURCE = "jp.mic_release"
ORIGIN = "http://www.soumu.go.jp/menu_news/s-news/daijinkanbou/{}.pdf"
WARP = "https://warp.ndl.go.jp/{collection}/{stamp}id_/{url}"
JAPAN = str(uuid5(package.NAMESPACE, "polity\x1fJP"))
NATION = str(uuid5(package.NAMESPACE, "area:jp.nation@None\x1fJP"))
REGISTERED, VALID, VOTERS = 2, 4, 14
VOTES = 1


@dataclass(frozen=True)
class Release:
    number: int
    held: date
    file: str
    collection: str
    stamp: str
    smd: int
    pr: int
    blocks: int
    publisher: str


RELEASES = {
    1996: Release(41, date(1996, 10, 20), "961022", "20091026", "20091022013917", 300, 200, 11, "自治省"),
    2000: Release(42, date(2000, 6, 25), "000625", "20091026", "20091022013825", 300, 180, 11, "自治省"),
}
PARTIES = {
    "自由民主党": "Liberal Democratic Party",
    "新進党": "New Frontier Party",
    "民主党": "Democratic Party of Japan",
    "日本共産党": "Japanese Communist Party",
    "社会民主党": "Social Democratic Party",
    "新党さきがけ": "New Party Sakigake",
    "民主改革連合": "Democratic Reform League",
    "公明党": "Komeito",
    "保守党": "New Conservative Party",
    "自由党": "Liberal Party",
    "無所属の会": "Independents' Club",
    "改革クラブ": "Reform Club",
    "政党自由連合": "Liberal League",
    "社会党": "New Socialist Party",
}
OTHERS = {"諸派・無所属": "Minor parties and independents", "諸派": "Minor parties"}
SHORT = {"神奈川": "神奈川県", "和歌山": "和歌山県", "鹿児島": "鹿児島県"}
SMD_METHOD = (
    "The nationwide tally of the {smd} single-member districts, from the Ministry of Home Affairs' release of the "
    "result. Electors are those on the roll on election day and voters are those who voted, as the release prints "
    "them for each prefecture. Seats are each party's district winners. The release prints each party's district "
    "votes only nationwide and with the fractions that split ballots for namesakes leave (按分票), which the archive "
    "cannot store as counts; they are listed in a note instead. The ministry did not publish district results for "
    "this election online, so the {smd} district contests are not held here."
)
PR_METHOD = (
    "The nationwide tally of the {blocks} proportional blocks ({seats} seats), from the Ministry of Home Affairs' "
    "release of the result. Electors are those on the roll on election day and voters are those who voted, as the "
    "release prints them for each prefecture. Each party's votes and seats are the nationwide totals the release "
    "prints. The ministry did not publish block or prefecture party votes for this election online, so the blocks are "
    "not held here as contests."
)
ABROAD_NOTE = (
    "Voters abroad first voted in this election, for proportional representation only. The release says its "
    "proportional figures include them, and its prefecture rows add up to its nationwide totals, so they are counted "
    "within the prefectures."
)
DISTRICTS_NOTE = (
    "District-level results for this election are not published online: the ministry's release gives prefecture "
    "turnout and nationwide party totals only, and its 結果調 book for this election exists in print only."
)


def packages() -> list[str]:
    return ["jp.mic_release/registry", *(f"jp.mic_release/hor-{year}" for year in RELEASES)]


def origin(release: Release) -> str:
    return ORIGIN.format(release.file)


def capture(release: Release) -> str:
    return WARP.format(collection=release.collection, stamp=release.stamp, url=origin(release))


def fetch() -> None:
    for release in RELEASES.values():
        raw.fetch(SOURCE, capture(release), suffix=".pdf", check=_pdf)


def _pdf(path: object) -> None:
    with open(str(path), "rb") as file:
        if file.read(5) != b"%PDF-":
            raise ValueError(f"{path}: not a PDF")


def parse() -> None:
    registry().write()
    for year in RELEASES:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def party(name: str) -> str:
    return key("party", name)


def prefecture_area(code: int) -> str:
    return str(uuid5(package.NAMESPACE, f"area:jp.prefecture@None\x1f{code:02d}"))


def prefecture_polity(code: int) -> str:
    return str(uuid5(package.NAMESPACE, f"polity\x1fJP-{code:02d}"))


def text(release: Release) -> list[str]:
    path = raw.cached(SOURCE, capture(release)).path
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    return out.splitlines()


def squeeze(line: str) -> str:
    return re.sub(r"\s+", "", line)


def section(lines: list[str], start: str, end: str | None) -> list[str]:
    plain = [squeeze(unicodedata.normalize("NFKC", line)) for line in lines]
    begin = max(i for i, line in enumerate(plain) if start in line)
    if end is None:
        return lines[begin + 1 :]
    stop = next(i for i, line in enumerate(plain) if i > begin and end in line)
    return lines[begin + 1 : stop]


def columns(line: str) -> list[int]:
    """The display column where each figure on a layout line ends, counting full-width characters as two."""
    ends, at, inside = [], 0, False
    for char in line:
        at += 2 if unicodedata.east_asian_width(char) in "FW" else 1
        digit = unicodedata.normalize("NFKC", char).isdigit()
        if digit:
            if inside:
                ends[-1] = at
            else:
                ends.append(at)
        inside = digit
    return ends


ROW = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,})\s+(\d{1,3}(?:,\d{3})+)\s+(\d+\.\d\d)")


@dataclass(frozen=True)
class Turnout:
    rows: list[tuple[int, int, int]]
    total: tuple[int, int]


def turnout(block: list[str], label: str) -> Turnout:
    rows: list[tuple[int, int, int]] = []
    total: tuple[int, int] | None = None
    names = [name for name, _ in PREFECTURES]
    for line in block:
        found = ROW.search(line)
        if found is None:
            continue
        registered, voters = int(found[1].replace(",", "")), int(found[2].replace(",", ""))
        rate = Decimal(found[3])
        if abs(Decimal(100 * voters) / registered - rate) > Decimal("0.006"):
            raise ValueError(f"{label}: {line!r} prints a turnout its counts do not give")
        head = squeeze(line[: found.start()])
        if "合計" in head:
            total = (registered, voters)
            break
        code = len(rows) + 1
        name = re.sub(r"^\d+", "", head)
        name = SHORT.get(name, name)
        if name and name != names[code - 1]:
            raise ValueError(f"{label}: row {code} names {name}, expected {names[code - 1]}")
        rows.append((code, registered, voters))
    if total is None or len(rows) != len(PREFECTURES):
        raise ValueError(f"{label}: found {len(rows)} prefecture rows and total {total}")
    return Turnout(rows, total)


NUMBER = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d+)?")


def labels(line: str, label: str) -> list[str]:
    """Split a row of party names, which the layout separates by spaces that also fall inside some names."""
    known = {*PARTIES, *OTHERS, "合計"}
    out, pending = [], ""
    for token in line.split():
        pending += token
        if pending in known:
            out.append(pending)
            pending = ""
    if pending:
        raise ValueError(f"{label}: cannot read party names from {line!r}")
    return out


def party_votes(block: list[str], label: str) -> list[tuple[str, Decimal]]:
    names: list[str] = []
    values: list[Decimal] = []
    for line in block:
        flat = line.strip()
        if flat.startswith(("区分", "区 分")):
            names += labels(re.sub(r"^区\s?分", "", flat), label)
        elif flat.startswith(("得票数", "得 票 数")) or (flat and NUMBER.fullmatch(flat)):
            values += [Decimal(value.replace(",", "")) for value in NUMBER.findall(flat)]
    if len(names) != len(values) or names[-1] != "合計":
        raise ValueError(f"{label}: {len(names)} party names and {len(values)} figures")
    pairs = list(zip(names, values, strict=True))
    if sum(value for _, value in pairs[:-1]) != pairs[-1][1]:
        raise ValueError(f"{label}: party votes do not add up to the printed total")
    return pairs[:-1]


def groups(numbers: list[int]) -> list[tuple[tuple[int, int, int], tuple[int, ...]]]:
    """Split a row of the seats table into its single-member, proportional, and total columns.

    The table prints 前, 元, 新, and 計 for each tier and leaves zeros blank, so a row's figures are each tier's nonzero
    parts followed by its sum, and a tier with no seats prints nothing."""
    found = []
    for sizes in product(range(5), repeat=3):
        if sum(sizes) != len(numbers) or 1 in sizes:
            continue
        cut, totals, fits = 0, [], True
        for size in sizes:
            part = numbers[cut : cut + size]
            cut += size
            if size and sum(part[:-1]) != part[-1]:
                fits = False
            totals.append(part[-1] if size else 0)
        if fits and totals[0] + totals[1] == totals[2]:
            found.append(((totals[0], totals[1], totals[2]), sizes))
    return found


def seats(block: list[str], label: str) -> dict[str, tuple[int, int]]:
    """Read each party's seats in each tier; where the figures alone fit more than one split, the columns decide."""
    out: dict[str, tuple[int, int]] = {}
    total = next(line for line in block if squeeze(unicodedata.normalize("NFKC", line)).startswith("合計"))
    grid = columns(total)
    if len(grid) != 12:
        raise ValueError(f"{label}: the seats table's total row has {len(grid)} figures")
    for raw_line in block:
        line = unicodedata.normalize("NFKC", raw_line)
        flat = line.strip()
        head = re.match(r"^([^\d(（]+?)\s*((?:\d+\s*)*)$", flat)
        if head is None:
            continue
        name = squeeze(head[1])
        if name not in PARTIES and name not in OTHERS and name != "合計":
            continue
        numbers = [int(n) for n in head[2].split()]
        if not numbers:
            out[name] = (0, 0)
            continue
        options = groups(numbers)
        if len(options) > 1:
            ends = columns(raw_line)

            def cost(sizes: tuple[int, ...]) -> int:
                tier = [index for index, size in enumerate(sizes) for _ in range(size)]
                return sum(
                    min(abs(end - column) for column in grid[4 * group : 4 * group + 4])
                    for end, group in zip(ends, tier, strict=True)
                )

            costs = sorted((cost(sizes), totals) for totals, sizes in options)
            if costs[0][0] == costs[1][0]:
                raise ValueError(f"{label}: cannot read seats of {name} from {numbers}")
            options = [(costs[0][1], ())]
        if not options:
            raise ValueError(f"{label}: cannot read seats of {name} from {numbers}")
        out[name] = options[0][0][:2]
    return out


@dataclass(frozen=True)
class Result:
    smd: Turnout
    pr: Turnout
    smd_votes: list[tuple[str, Decimal]]
    pr_votes: list[tuple[str, Decimal]]
    seats: dict[str, tuple[int, int]]


def read(year: int) -> Result:
    release = RELEASES[year]
    raw_lines = text(release)
    lines = [unicodedata.normalize("NFKC", line) for line in raw_lines]
    label = f"hor {year}"
    smd = turnout(
        section(lines, "投票結果(小選挙区)", "投票結果(比例代表)" if year == 2000 else "総選挙党派別候補者"), label
    )
    pr = turnout(section(lines, "投票結果(比例代表)", None), label)
    votes_start = "総選挙党派別得票数及び得票率"
    votes = section(lines, votes_start, "総選挙後の党派別議員数" if year == 1996 else "第5表")
    smd_votes = party_votes(section(votes, "(1)小選挙区", "(2)比例代表"), f"{label} single-member votes")
    pr_votes = party_votes(section(votes, "(2)比例代表", None), f"{label} proportional votes")
    table = section(raw_lines, "総選挙党派別当選者数", votes_start)
    won = seats(table, label)
    total = won.pop("合計")
    if total != (release.smd, release.pr):
        raise ValueError(f"{label}: the seats table totals {total}")
    if (sum(s for s, _ in won.values()), sum(p for _, p in won.values())) != total:
        raise ValueError(f"{label}: party seats do not add up to {total}")
    return Result(smd, pr, smd_votes, pr_votes, won)


def source_id(year: int) -> str:
    return key("source", RELEASES[year].file)


def registry() -> Package:
    p = Package(
        "jp.mic_release/registry",
        SOURCE,
        "The parties named in the Ministry of Home Affairs' releases of the 1996 and 2000 House of Representatives "
        "general election results",
    )
    p.external.add(JAPAN)
    named: set[str] = set()
    for year in RELEASES:
        result = read(year)
        named |= {name for name, _ in [*result.smd_votes, *result.pr_votes]} | set(result.seats)
    for name in sorted(named & set(PARTIES), key=list(PARTIES).index):
        id = party(name)
        p.add("party", id=id, kind="party", polity_id=JAPAN)
        p.name(id, name, "ja", "official")
        p.name(id, PARTIES[name], "en", "official", False)
    return p


def election(year: int) -> Package:
    release = RELEASES[year]
    result = read(year)
    held = release.held.isoformat()
    p = Package(
        f"jp.mic_release/hor-{year}",
        SOURCE,
        f"The {release.number}th House of Representatives general election of {release.held:%-d %B %Y}: electors "
        "and voters by prefecture, and nationwide party votes and seats in each tier, from the Ministry of Home "
        "Affairs' release",
        depends=["jp.mic_release/registry"],
    )
    p.external |= {JAPAN, NATION, "jp.nation", "jp.prefecture"}
    source = source_id(year)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=release.publisher,
        title=f"第{release.number}回衆議院議員総選挙結果",
        url=origin(release),
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution=ATTRIBUTION,
        notes=f"Reposted by the Ministry of Internal Affairs and Communications from the Ministry of Home Affairs' "
        f"release; read from the National Diet Library's WARP capture of {release.stamp[:8]} ({capture(release)}), "
        f"because the live page is gone.",
    )
    p.artifact(raw.cached(SOURCE, capture(release)), source)

    election = key("election", held)
    p.add(
        "election",
        id=election,
        polity_id=JAPAN,
        kind="general",
        date_from=release.held,
        date_to=release.held,
        status="held",
    )
    p.name(election, f"{release.held.year} Japanese general election", "en", "official")
    p.name(election, f"第{release.number}回衆議院議員総選挙", "ja", "native", False)
    path = f"jp/{held}-general"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add("note", id=p.id("note", election), entity_id=election, kind="caveat", body_md=DISTRICTS_NOTE, lang="en")

    nation = key("unit", held, "JP")
    p.add(
        "reporting_unit",
        id=nation,
        polity_id=JAPAN,
        scheme_code="jp.nation",
        code=f"{held}:JP",
        area_relation="equals",
        area_id=NATION,
        kind="standard",
    )
    units: dict[int, str] = {}
    for code in range(1, len(PREFECTURES) + 1):
        unit = units[code] = key("unit", held, code)
        area, polity = prefecture_area(code), prefecture_polity(code)
        p.external |= {area, polity}
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=polity,
            scheme_code="jp.prefecture",
            code=f"{held}:{code:02d}",
            area_relation="equals",
            area_id=area,
            kind="standard",
        )

    def option(contest: str, tier: str, name: str, position: int) -> str:
        id = key("option", held, tier, name)
        p.add(
            "contest_option",
            id=id,
            contest_id=contest,
            kind="others" if name in OTHERS else "list",
            party_id=party(name) if name in PARTIES else None,
            ballot_position=position,
            ballot_label=name,
        )
        return id

    def counts(result_set: str, table: Turnout) -> None:
        for code, registered, voters in table.rows:
            for measure, value in ((REGISTERED, registered), (VOTERS, voters)):
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=units[code],
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        for measure, value in zip((REGISTERED, VOTERS), table.total, strict=True):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)

    tiers = (
        ("smd", "Single-member districts", "小選挙区", "body_seats", release.smd, result.smd, result.smd_votes, 0),
        ("pr", "Proportional representation", "比例代表", "list_allocation", release.pr, result.pr, result.pr_votes, 1),
    )
    for tier, english, native, kind, magnitude, table, votes, column in tiers:
        contest = key("contest", held, tier)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind=kind,
            district_area_id=NATION,
            magnitude=magnitude,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, f"{english} (nationwide tally)", "en", "official")
        p.name(contest, f"{native}(全国集計)", "ja", "native", False)
        p.add("slug", path=f"{path}/{changeset.slug(english)}", entity_id=contest, is_current=True)
        result_set = key("result_set", held, tier)
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
        method = (SMD_METHOD if tier == "smd" else PR_METHOD).format(
            smd=release.smd, blocks=release.blocks, seats=release.pr
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        if (sum(row[1] for row in table.rows), sum(row[2] for row in table.rows)) != table.total:
            raise ValueError(f"hor {year} {tier}: prefecture rows do not add up to the nationwide total")
        counts(result_set, table)
        names = [name for name, _ in votes]
        names += [name for name in result.seats if name not in names and result.seats[name][column]]
        options = {name: option(contest, tier, name, position) for position, name in enumerate(names, 1)}
        for name, id in options.items():
            won = result.seats.get(name, (0, 0))[column]
            if won:
                p.add("outcome", result_set_id=result_set, option_id=id, kind="won", seats=won)
        if tier == "pr":
            for name, value in votes:
                if value != value.to_integral_value():
                    raise ValueError(f"hor {year}: proportional votes of {name} are fractional")
                p.add("declared", result_set_id=result_set, option_id=options[name], measure=VOTES, value=int(value))
            valid = int(sum(value for _, value in votes))
            p.add("declared", result_set_id=result_set, option_id=None, measure=VALID, value=valid)
            for option_id, measure, value in [
                *((options[name], VOTES, int(value)) for name, value in votes),
                (None, VALID, valid),
            ]:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=nation,
                    option_id=option_id,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
            if year >= 2000:
                p.add(
                    "note",
                    id=p.id("note", result_set, "abroad"),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=ABROAD_NOTE,
                    lang="en",
                )
        else:
            lines = "\n".join(f"| {name} | {value:,} |" for name, value in votes)
            total = sum(value for _, value in votes)
            p.add(
                "note",
                id=p.id("note", result_set, "votes"),
                entity_id=result_set,
                kind="context",
                body_md=f"Nationwide single-member district votes by party, as the release prints them:\n\n"
                f"| Party | Votes |\n| --- | ---: |\n{lines}\n| 合計 | {total:,} |",
                lang="en",
            )
    return p
