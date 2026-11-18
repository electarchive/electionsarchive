"""Palestine's first local elections of 2004-2005 from the Central Elections Commission (elections.ps).

The Higher Committee for Local Elections ran four rounds in 2004 and 2005, and the Commission publishes each as PDF
tables by local authority. The first two rounds (23 December 2004 and 27 January 2005, then 5 May 2005) elected the
councils by block vote in majority districts under Law no. 5 of 1996: the tables name each council's winners with
their votes, and the winners only. The third (29 September 2005) and fourth (15 December 2005) rounds elected the
councils from closed lists by the Sainte-Laguë method under Law no. 10 of 2005: the tables give each list's votes
and seats, and for the fourth round the winners by list. The registration and polling statistics give each
authority's registered voters and ballots cast for three of the rounds. Every figure keeps its page and box.
"""

import difflib
import itertools
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from uuid import uuid5

import pymupdf

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.ps import cec_local
from ea.sources.ps import cec_local05_tables as tables
from ea.sources.ps.cec_local_tables import Box, Cell, number

SOURCE = "ps.cec_local05"
SITE = "https://www.elections.ps"
PALESTINE = cec_local.PALESTINE
COUNTRY = cec_local.COUNTRY
VOTES, REGISTERED, CAST = 1, 2, 3
PDF = f"{SITE}/Portals/0/pdf"
FILES = {
    "page": f"{SITE}/tabid/168/language/en-US/Default.aspx",
    "round1": f"{PDF}/results_election2004-2005.pdf",
    "round2": f"{PDF}/resultsSecond.pdf",
    "round3": f"{PDF}/resultsThird.pdf",
    "round4": f"{PDF}/resultsFourth.pdf",
    "winners4": f"{PDF}/WinningcandidatesFourth.pdf",
    "stats1": f"{PDF}/localauthoritiesregistrationpollingstatistics.pdf",
    "stats2": f"{PDF}/localAuthoritiesStatisticsSecond.pdf",
    "stats4": f"{PDF}/localElectionsStatisticsFourth.pdf",
}
# the election days: the first round's West Bank and Gaza days, and one day for each later round
DAYS = {
    "1w": date(2004, 12, 23),
    "1g": date(2005, 1, 27),
    "2": date(2005, 5, 5),
    "3": date(2005, 9, 29),
    "4": date(2005, 12, 15),
}
# the Commission's governorates, by the letters of the name its tables print, and the area each is placed on
DISTRICTS = {
    "jerusalem": ("Jerusalem", "PS-JEM"),
    "jenin": ("Jenin", "PS-JEN"),
    "tulkarem": ("Tulkarm", "PS-TKM"),
    "tubas": ("Tubas", "PS-TBS"),
    "nablus": ("Nablus", "PS-NBS"),
    "qalqilya": ("Qalqilya", "PS-QQA"),
    "salfit": ("Salfit", "PS-SLT"),
    "ramallah": ("Ramallah and al-Bireh", "PS-RBH"),
    "ramallahalbireh": ("Ramallah and al-Bireh", "PS-RBH"),
    "jericho": ("Jericho", "PS-JRH"),
    "bethlehem": ("Bethlehem", "PS-BTH"),
    "hebron": ("Hebron", "PS-HBN"),
    "gaza": ("Gaza", "PS-GZA"),
    "northgaza": ("North Gaza", "PS-NGZ"),
    "deiralbalah": ("Deir al-Balah", "PS-DEB"),
    "middlegaza": ("Deir al-Balah", "PS-DEB"),
    "khanyounis": ("Khan Yunis", "PS-KYS"),
    "rafah": ("Rafah", "PS-RFH"),
}
GAZA = {"Gaza", "North Gaza", "Deir al-Balah", "Khan Yunis", "Rafah"}
# the parties the third and fourth round's tables name, by the way the tables print them
PARTIES = {
    "fatah": ("Fatah", {"Fatah"}),
    "hamas": ("Hamas", {"Hamas"}),
    "dflp": ("Democratic Front for the Liberation of Palestine (DFLP)", {"DFLP"}),
    "pflp": ("Popular Front for the Liberation of Palestine (PFLP)", {"PFLP"}),
    "ppp": ("Palestinian People's Party", {"The People's Party"}),
    "apf": ("Arab Palestinian Front", {"The Arab Palestinian Front", "Arab Palest. Front"}),
    "pni": ("Palestinian National Initiative", {"The National Palestinian Initiative", "National Palest. Init."}),
    "ppsf": ("Palestinian Popular Struggle Front", {"The Palestinian Popular Struggle Front", "PPSF"}),
    "fida": (
        "Palestinian Democratic Union (Fida)",
        {"The palestinian democratic Union (Fida)", "The Palestinian democratic Union (Fida)"},
    ),
}
BY_NAME = {name: tag for tag, (_, names) in PARTIES.items() for name in names}
THRESHOLD = 8
BLOCK_NOTE = (
    "Local Council Elections Law no. 5 of 1996 divides a locality into electoral districts that elect its council "
    "by block vote: each voter chooses as many candidates as the district has seats, and the candidates with the "
    "most votes win. The law reserves seats for women, so a woman can win through the quota with fewer votes than "
    "a man who loses."
)
LIST_NOTE = (
    "Local Council Elections Law no. 10 of 2005 elects each council from closed lists by proportional "
    "representation. A list that gets at least 8 percent of the valid votes shares the seats by the Sainte-Laguë "
    "method, dividing its votes by 1, 3, 5, and so on, and its seats go to its candidates in the order of the list."
)
BLOCK_METHOD = (
    "Counted by the Higher Committee for Local Elections, as published by the Central Elections Commission, for one "
    "local authority. The table names the council's winners with their votes and the electoral list each ran on, "
    "and it names no one who lost, so the votes of the candidates who did not win are not published. The Commission "
    "marks each winner as having won the competition or through the women's quota. No ballots counted as valid, "
    "invalid, or blank are published, so none is stored. Where the Commission's registration and polling "
    "statistics name the authority, its registered voters and the voters who took part are stored."
)
LIST_METHOD = (
    "Counted by the Higher Committee for Local Elections, as published by the Central Elections Commission, for one "
    "local authority. Each voter chose one closed list. The table gives each list's votes and the seats it won, "
    "with the number of seats on the council. No ballots counted as valid, invalid, or blank are published, so none "
    "is stored. Seats are stored as declared."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC_SOURCE = key("source", "cec")
SYSTEM_BLOCK = key("electoral_system", "block")
SYSTEM_LIST = key("electoral_system", "list")


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".aspx" if url.endswith(".aspx") else ".pdf")
    raw.fetch(cec_local.GEOBOUNDARIES, cec_local.GEO_URL, suffix=".geojson")


def packages() -> list[str]:
    return [f"{cec_local.SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS.values())]


def parse() -> None:
    cec_local.geo().write()
    registry().write()
    for tag in DAYS:
        election(tag).write()


def emit() -> None:
    package.run("load", packages())


def path(name: str) -> Path:
    return raw.cached(SOURCE, FILES[name]).path


# --- the Commission's tables ----------------------------------------------------------------------------------------

CANDIDATES = (
    "district",
    "no",
    "authority",
    "candidate",
    "gender",
    "votes",
    "list",
    "result",
)
LISTS = ("district", "no", "authority", "seats", "listno", "list", "symbol", "party", "votes", "listseats")
SPECS = {
    "round1": tables.Spec(
        (100, 173, 225, 321, 396, 470, 544, 618, 692),
        CANDIDATES,
        3,
        "votes",
        top=130,
        row_rule_x=321,
        block_rule_x=100,
    ),
    "round2": tables.Spec(
        (18, 86, 102, 209, 388, 429, 466, 632, 732), CANDIDATES, 3, "votes", row_rule_x=209, block_rule_x=18
    ),
    "round3": tables.Spec(
        (18, 78, 100, 202, 232, 255, 508, 545, 703, 734, 769),
        LISTS,
        4,
        "votes",
        header="Symbol",
        row_rule_x=233,
        block_rule_x=19,
    ),
    "round4": tables.Spec(
        (36, 91, 107, 183, 225, 249, 504, 529, 629, 668, 716),
        LISTS,
        4,
        "votes",
        header="party",
        row_rule_x=226,
        block_rule_x=37,
    ),
    "winners4": tables.Spec(
        (39, 121, 143, 215, 259, 281, 488, 530, 701, 750),
        ("district", "no", "authority", "seats", "listno", "list", "order", "candidate", "gender"),
        4,
        "order",
        row_rule_x=260,
        block_rule_x=40,
    ),
}
STATISTICS = ("district", "no", "authority", "rm", "rf", "rt", "vm", "vf", "vt")
STATS = {
    "stats1": tables.Spec(
        (54, 175, 194, 374, 433, 492, 552, 611, 670, 729),
        STATISTICS,
        1,
        "no",
        "Males",
        rows="anchors",
        block_rule_x=55,
    ),
    "stats2": tables.Spec(
        (54, 182, 206, 351, 404, 463, 514, 565, 627, 690),
        STATISTICS,
        1,
        "no",
        "Female",
        rows="anchors",
        block_rule_x=55,
    ),
    "stats4": tables.Spec(
        (54, 175, 201, 345, 405, 464, 524, 584, 644, 703),
        STATISTICS,
        1,
        "no",
        "Female",
        rows="anchors",
        block_rule_x=55,
    ),
}
# the national totals the statistics print: registered males, females, total; then voters males, females, total
TOTALS = {
    "stats1": (115377, 115360, 230737, 76727, 69164, 145891),
    "stats2": (160084, 155181, 315265, 108422, 101792, 210214),
    "stats4": (77927, 69854, 147781, 57837, 50200, 108037),
}
# the localities each round's page counts, and the sizes the councils of the tables add up to
LOCALITIES = {"1": 36, "2": 82, "3": 104, "4": 40}
RESULT = re.compile(
    r"\b(winner by competing|woman quota|uncontested winner|winner by lot|lost by lot|competition winner"
    r"|by competing|quota)$",
    re.IGNORECASE,
)


def tidy(text: str) -> str:
    """A name as the tables print it, with the spaces after hyphens and the underscores of the printing removed."""
    return re.sub(r"\s+", " ", re.sub(r"-\s+", "-", text.replace("_", " "))).strip()


def similar(a: str, b: str) -> float:
    letters = [re.sub(r"[^a-z]", "", text.lower()) for text in (a, b)]
    return difflib.SequenceMatcher(None, letters[0], letters[1]).ratio()


def district(text: str) -> tuple[str, str]:
    return DISTRICTS[re.sub(r"[^a-z]", "", text.lower())]


def count(text: str) -> int:
    return int(re.sub(r"[^0-9]", "", text) or 0)


@dataclass(frozen=True)
class Candidate:
    name: str
    gender: str
    votes: Cell
    list_name: str
    result: str  # competition, quota, uncontested, lot, or lost_by_lot


@dataclass(frozen=True)
class ListRow:
    number: int
    name: str
    symbol: str
    party: str
    votes: Cell
    seats: Cell


@dataclass(frozen=True)
class Winner:
    list_number: int
    order: int
    name: str
    gender: str


@dataclass
class Authority:
    round: str
    district: str
    code: str
    number: int
    name: str
    seats: int | None = None
    candidates: list[Candidate] | None = None
    lists: list[ListRow] | None = None
    winners: list[Winner] | None = None
    registered: Cell | None = None
    cast: Cell | None = None
    stats: str | None = None

    @property
    def ident(self) -> str:
        return f"{self.round}:{self.number}"

    @property
    def day(self) -> date:
        if self.round == "1":
            return DAYS["1g" if self.district in GAZA else "1w"]
        return DAYS[self.round]


def _result(row: dict[str, Cell]) -> str:
    match = RESULT.search(f"{row['list'].text} {row['result'].text}".strip())
    text = (match.group(1) if match else row["result"].text).lower()
    if "lost" in text:
        return "lost_by_lot"
    if "lot" in text:
        return "lot"
    if "quota" in text:
        return "quota"
    if "uncontested" in text:
        return "uncontested"
    if "compet" in text or text == "winner":
        return "competition"
    raise ValueError(f"unknown result {row['result'].text!r}")


def _votes(row: dict[str, Cell]) -> int:
    return count(row["votes"].text)


def _vote_score(rows: list[dict[str, Cell]], i: int) -> int:
    """A candidate with more votes than the one before starts another authority's table."""
    return 3 if 0 < i < len(rows) and _votes(rows[i]) > _votes(rows[i - 1]) else 0


def _lost(row: dict[str, Cell]) -> bool:
    return _result(row) == "lost_by_lot"


def _block_fit(rows: list[dict[str, Cell]], labels: dict[str, Cell], low: int, high: int) -> int:
    """Councils have 7, 9, 11, 13, or 15 members, so a cut that gives another size is unlikely."""
    return 0 if sum(not _lost(r) for r in rows[low:high]) in {7, 9, 11, 13, 15} else -25


def _list_score(rows: list[dict[str, Cell]], i: int) -> int:
    """A list numbered below the one before starts another authority's table."""
    return 3 if 0 < i < len(rows) and count(rows[i]["listno"].text) < count(rows[i - 1]["listno"].text) else 0


def _seat_fit(rows: list[dict[str, Cell]], labels: dict[str, Cell], low: int, high: int) -> int:
    seats = count(labels["seats"].text)
    won = sum(count(r["listseats"].text) for r in rows[low:high])
    return 0 if not seats or won == seats else -1000


def _order_score(rows: list[dict[str, Cell]], i: int) -> int:
    return 3 if 0 < i < len(rows) and count(rows[i]["order"].text) == 1 else -5


def _winner_fit(rows: list[dict[str, Cell]], labels: dict[str, Cell], low: int, high: int) -> int:
    return 0 if high - low == count(labels["seats"].text) else -1000


def _authority(round_: str, group: tables.Group) -> Authority:
    label = group.labels
    name, code = district(label["district"].text)
    return Authority(round_, name, code, count(label["no"].text), tidy(label["authority"].text))


@cache
def candidate_rounds(name: str) -> list[Authority]:
    """The authorities of the first two rounds with their winners, from the Commission's candidate tables."""
    groups = tables.groups(tables.read(path(name), SPECS[name]), _vote_score, _block_fit)
    out = []
    for group in groups:
        authority = _authority(name[-1], group)
        authority.candidates = [
            Candidate(
                tidy(row["candidate"].text),
                row["gender"].text.lower(),
                row["votes"],
                tidy(row["list"].text.removesuffix(" winner").removesuffix(" Woman")),
                _result(row),
            )
            for row in group.rows
        ]
        out.append(authority)
    return out


def _list_row(row: dict[str, Cell]) -> ListRow:
    return ListRow(
        count(row["listno"].text),
        tidy(row["list"].text),
        row["symbol"].text,
        tidy(row["party"].text),
        row["votes"],
        row["listseats"],
    )


@cache
def list_rounds(name: str) -> list[Authority]:
    """The authorities of the last two rounds with their lists, from the Commission's list tables."""
    groups = tables.groups(tables.read(path(name), SPECS[name]), _list_score, _seat_fit)
    out = []
    for group in groups:
        authority = _authority(name[-1], group)
        authority.seats = count(group.labels["seats"].text) or None
        authority.lists = [_list_row(row) for row in group.rows]
        out.append(authority)
    return out


@cache
def winners_of_round4() -> dict[int, list[Winner]]:
    """The fourth round's winners by authority number (the authority numbers of the results table)."""
    groups = tables.groups(tables.read(path("winners4"), SPECS["winners4"]), _order_score, _winner_fit, anchor="order")
    results = list_rounds("round4")
    pairs = sorted(
        (
            (similar(authority.name, tidy(group.labels["authority"].text)), index, group)
            for index, authority in enumerate(results)
            for group in groups
            if district(group.labels["district"].text)[0] == authority.district
        ),
        key=lambda pair: -pair[0],
    )
    out: dict[int, list[Winner]] = {}
    taken: set[int] = set()
    for score, index, group in pairs:
        if index in out or id(group) in taken:
            continue
        if score < 0.5:
            raise ValueError(f"the winners of {group.labels['authority'].text!r} match no authority")
        out[index] = [
            Winner(count(r["listno"].text), count(r["order"].text), tidy(r["candidate"].text), r["gender"].text.lower())
            for r in group.rows
        ]
        taken.add(id(group))
    if len(out) != len(results) or len(taken) != len(groups):
        raise ValueError("the fourth round's winners do not match its authorities one to one")
    return {results[index].number: winners for index, winners in out.items()}


@cache
def statistics(name: str) -> dict[int, dict[str, Cell]]:
    """Each authority's registered voters and voters by the statistics' own numbers, and the national total row."""
    out: dict[int, dict[str, Cell]] = {}
    for block in tables.read(path(name), STATS[name]):
        for row in block.rows:
            out[count(row["no"].text)] = row
    for number_, row in out.items():
        registered = count(row["rm"].text) + count(row["rf"].text)
        voters = count(row["vm"].text) + count(row["vf"].text)
        if registered != count(row["rt"].text) or voters != count(row["vt"].text):
            raise ValueError(f"{name}: the males and females of authority {number_} do not add up")
    return out


def _total_sums(name: str) -> tuple[int, ...]:
    rows = statistics(name).values()
    return tuple(sum(count(row[column].text) for row in rows) for column in ("rm", "rf", "rt", "vm", "vf", "vt"))


# the statistics' numbers of the second round's Gaza authorities, which the results number differently; Al-Braij (80)
# is in the results table and not in the statistics
STATS_OF_ROUND2: dict[int, int | None] = {77: 77, 78: 79, 79: 78, 80: None, 81: 80, 82: 81, 83: 82}


@cache
def authorities() -> dict[str, list[Authority]]:
    """Every authority of each round, with the statistics and winners joined to it and checked."""
    out = {
        "1": candidate_rounds("round1"),
        "2": candidate_rounds("round2"),
        "3": list_rounds("round3"),
        "4": list_rounds("round4"),
    }
    for round_, name in (("1", "stats1"), ("2", "stats2"), ("4", "stats4")):
        stats = statistics(name)
        used: set[int] = set()
        for authority in out[round_]:
            at = STATS_OF_ROUND2.get(authority.number, authority.number) if round_ == "2" else authority.number
            if at is None:
                continue
            # the tables number a few neighbors in a different order, so the nearest number with a like name
            near = [n for n in stats if n not in used and abs(n - at) <= 2]
            best = max(
                near,
                key=lambda n: similar(authority.name, tidy(stats[n]["authority"].text)) - 0.05 * abs(n - at),
                default=None,
            )
            if best is None or similar(authority.name, tidy(stats[best]["authority"].text)) < 0.3:
                raise ValueError(f"{name}: no statistics row for {authority.name!r}")
            used.add(best)
            authority.registered, authority.cast = stats[best]["rt"], stats[best]["vt"]
            authority.stats = name
        if used != set(stats):
            raise ValueError(f"{name}: the statistics name authorities the results lack: {set(stats) - used}")
    return out


def verify() -> dict[str, str]:
    """Checks the tables against the Commission's own counts and totals; returns how each round compared."""
    found = authorities()
    report: dict[str, str] = {}
    sizes = {
        "1": len(found["1"]),
        "2": len(found["2"]),
        "3": len(found["3"]),
        "4": len(found["4"]),
    }
    if sizes["1"] != LOCALITIES["1"] or sizes["3"] != LOCALITIES["3"] or sizes["4"] != LOCALITIES["4"]:
        raise ValueError(f"the localities differ from the Commission's page: {sizes}")
    if sizes["2"] != LOCALITIES["2"] + 1:
        raise ValueError(f"the second round names {sizes['2']} localities")
    for round_, name in (("1", "stats1"), ("2", "stats2"), ("4", "stats4")):
        total = _total_sums(name)
        if total != TOTALS[name]:
            raise ValueError(f"{name}: the authorities add up to {total}, not the printed {TOTALS[name]}")
        report[round_] = (
            f"{len(statistics(name))} authorities' registered voters ({total[2]:,}) and voters ({total[5]:,}) add up "
            "to the statistics' printed totals"
        )
    for authority in found["3"] + found["4"]:
        assert authority.lists is not None
        won = sum(count(row.seats.text) for row in authority.lists)
        if authority.seats and won != authority.seats:
            raise ValueError(f"{authority.name}: {won} seats for a council of {authority.seats}")
    winners = winners_of_round4()
    for authority in found["4"]:
        assert authority.lists is not None
        seen = Counter(w.list_number for w in winners[authority.number])
        for row in authority.lists:
            if seen[row.number] != count(row.seats.text):
                raise ValueError(f"{authority.name}: list {row.number} has {seen[row.number]} winners")
        if set(seen) - {row.number for row in authority.lists}:
            raise ValueError(f"{authority.name}: winners on a list the results lack")
    for round_ in ("1", "2"):
        for authority in found[round_]:
            assert authority.candidates is not None
            kept = [c for c in authority.candidates if c.result != "lost_by_lot"]
            if len(kept) not in {7, 9, 11, 13, 15}:
                report[f"{round_}:size"] = report.get(f"{round_}:size", "") + f"{authority.name} {len(kept)}; "
            ordered = [count(c.votes.text) for c in authority.candidates]
            if any(b > a for a, b in itertools.pairwise(ordered)):
                raise ValueError(f"{authority.name}: the winners' votes do not run in order")
    return report


# --- packages -------------------------------------------------------------------------------------------------------


def governorate(code: str) -> str:
    return cec_local.key("area", "ps.governorate", code)


def body(authority: Authority) -> str:
    return key("body", authority.district, re.sub(r"[^a-z]", "", authority.name.lower()))


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Central Elections Commission as the source of the local elections of 2004-2005, the electoral systems "
        "of the two laws, the parties the lists name, and the councils the contests fill",
        depends=[f"{cec_local.SOURCE}/geo"],
    )
    p.external |= {PALESTINE, COUNTRY}
    p.add(
        "source",
        id=CEC_SOURCE,
        kind="official_body",
        publisher="لجنة الانتخابات المركزية - فلسطين (Central Elections Commission, Palestine)",
        title="Results of the local elections of 2004-2005 published by the Central Elections Commission",
        url=FILES["page"],
        license="Official publication; reuse terms are not stated",
        attribution="Central Elections Commission, Palestine",
    )
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), CEC_SOURCE)
    for id, rules, note in (
        (SYSTEM_BLOCK, {"formula": "block"}, BLOCK_NOTE),
        (
            SYSTEM_LIST,
            {"formula": "sainte_lague", "threshold": {"percent": THRESHOLD, "level": "list", "scope": "contest"}},
            LIST_NOTE,
        ),
    ):
        p.add("electoral_system", id=id, polity_id=PALESTINE, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=note, lang="en")
    for tag, (english, _) in PARTIES.items():
        id = key("party", tag)
        p.add("party", id=id, kind="party", polity_id=PALESTINE)
        p.name(id, english, "en", "english")
        p.add("external_id", entity_id=id, scheme="ps.cec_local05.party", value=tag)
    seen: set[str] = set()
    for round_ in authorities().values():
        for authority in round_:
            id = body(authority)
            if id not in seen:
                seen.add(id)
                p.add("body", id=id, polity_id=PALESTINE, kind="council")
                p.name(id, f"{authority.name} local council", "en", "english")
    return p


def _source_pages(p: Package, name: str) -> dict[int, str]:
    artifact = raw.cached(SOURCE, FILES[name])
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{CEC_SOURCE}\x1f{artifact.storage_key}"))
    pages: dict[int, str] = {}
    document = pymupdf.open(artifact.path)
    for number_ in range(1, document.page_count + 1):
        page = document[number_ - 1]
        pages[number_] = key("page", name, number_)
        p.add(
            "source_page",
            id=pages[number_],
            artifact_id=artifact_id,
            page_number=number_,
            image_key=f"pages/{SOURCE}/{artifact.path.stem}/{number_}.png",
            width=round(page.rect.width),
            height=round(page.rect.height),
        )
    return pages


def _put(
    p: Package,
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    cell: Cell,
    pages: dict[int, str],
) -> int:
    value = number(cell.text)
    box: Box = cell.box
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
    p.add(
        "value_locator",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        page_id=pages[box.page],
        source_id=CEC_SOURCE,
        x0=box.x0,
        y0=box.y0,
        x1=box.x1,
        y1=box.y1,
    )
    return value


def _party_of(text: str) -> str | None:
    tag = BY_NAME.get(text)
    return key("party", tag) if tag else None


def _unit(p: Package, authority: Authority) -> str:
    unit = key("unit", authority.ident)
    day = authority.day
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=PALESTINE,
        scheme_code="ps.local_authority",
        code=f"{day}:{authority.district}:{authority.name}",
        area_relation="within",
        area_id=governorate(authority.code),
        kind="standard",
        valid_from=day,
        valid_to=day + timedelta(days=1),
    )
    p.name(unit, authority.name, "en", "english")
    return unit


def _contest(
    p: Package, election_id: str, authority: Authority, slug: str, kind: str, magnitude: int
) -> tuple[str, str]:
    contest = key("contest", authority.ident)
    result_set = key("result_set", authority.ident)
    block = kind == "body_seats"
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind=kind,
        body_id=body(authority),
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=SYSTEM_BLOCK if block else SYSTEM_LIST,
        default_result_kind="counted",
    )
    p.name(contest, f"{authority.name} local council, {authority.day.year}", "en", "official")
    p.add("slug", path=slug, entity_id=contest, is_current=True)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CEC_SOURCE,
        status="active",
        is_default=True,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=BLOCK_METHOD if block else LIST_METHOD,
        lang="en",
    )
    return contest, result_set


def _person(p: Package, tag: str, name: str, gender: str) -> str:
    person = key("person", tag)
    p.add("person", id=person, gender=gender if gender in ("male", "female") else None)
    p.name(person, name, "en", "official")
    return person


def _block_contest(
    p: Package, election_id: str, authority: Authority, slug: str, pages: dict[int, str], stats: dict[int, str] | None
) -> dict[str, int]:
    assert authority.candidates is not None
    winners = [c for c in authority.candidates if c.result != "lost_by_lot"]
    contest, result_set = _contest(p, election_id, authority, slug, "body_seats", len(winners))
    unit = _unit(p, authority)
    totals: dict[str, int] = Counter()
    if authority.registered and authority.cast and stats is not None:
        totals["registered"] += _put(p, result_set, unit, None, REGISTERED, authority.registered, stats)
        totals["cast"] += _put(p, result_set, unit, None, CAST, authority.cast, stats)
    lists: dict[str, str] = {}
    for index, candidate in enumerate(authority.candidates, 1):
        tag = f"{authority.ident}:{index}"
        person = _person(p, tag, candidate.name, candidate.gender)
        parent = None
        independent = candidate.list_name.lower() == "independent"
        if not independent:
            if candidate.list_name not in lists:
                lists[candidate.list_name] = key("option", authority.ident, "list", len(lists) + 1)
                p.add(
                    "contest_option",
                    id=lists[candidate.list_name],
                    contest_id=contest,
                    kind="list",
                    ballot_position=len(lists),
                    ballot_label=candidate.list_name,
                )
            parent = lists[candidate.list_name]
        option = key("option", tag)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            parent_option_id=parent,
            ballot_label=candidate.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate.name,
            is_independent=True if independent else None,
        )
        won = candidate.result != "lost_by_lot"
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if won else "lost")
        totals["votes"] += _put(p, result_set, unit, option, VOTES, candidate.votes, pages)
        note = {
            "quota": "The Commission marks this candidate as elected through the women's quota.",
            "lot": "The Commission marks this candidate as winning a tie for the last seat by lot.",
            "lost_by_lot": "The Commission marks this candidate as losing a tie for the last seat by lot.",
            "uncontested": "The Commission marks this candidate as an uncontested winner.",
        }.get(candidate.result)
        if note:
            p.add("note", id=key("note", option), entity_id=option, kind="context", body_md=note, lang="en")
        totals["winners"] += won
    return totals


def _list_contest(
    p: Package,
    election_id: str,
    authority: Authority,
    slug: str,
    pages: dict[int, str],
    stats: dict[int, str] | None,
    winners: list[Winner] | None,
) -> dict[str, int]:
    assert authority.lists is not None
    won = sum(count(row.seats.text) for row in authority.lists)
    contest, result_set = _contest(p, election_id, authority, slug, "list_allocation", authority.seats or won)
    unit = _unit(p, authority)
    totals: dict[str, int] = Counter()
    if authority.registered and authority.cast and stats is not None:
        totals["registered"] += _put(p, result_set, unit, None, REGISTERED, authority.registered, stats)
        totals["cast"] += _put(p, result_set, unit, None, CAST, authority.cast, stats)
    if authority.seats is None:
        p.add(
            "note",
            id=key("note", contest, "size"),
            entity_id=contest,
            kind="caveat",
            body_md=(
                "The Commission's table prints no number of council seats for this authority; the contest's "
                "seats are the seats its lists won, as printed."
            ),
            lang="en",
        )
    options: dict[int, str] = {}
    for position, row in enumerate(authority.lists, 1):
        option = options[row.number] = key("option", authority.ident, row.number)
        party = _party_of(row.party)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party,
            ballot_position=position,
            ballot_label=row.name,
        )
        if row.party and party is None:
            p.add(
                "note",
                id=key("note", option),
                entity_id=option,
                kind="context",
                body_md=f"The Commission records the list's political party as: {row.party}.",
                lang="en",
            )
        seats = count(row.seats.text)
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if seats else "lost", seats=seats)
        totals["votes"] += _put(p, result_set, unit, option, VOTES, row.votes, pages)
        totals["seats"] += seats
    for winner in winners or []:
        tag = f"{authority.ident}:{winner.list_number}:{winner.order}"
        person = _person(p, tag, winner.name, winner.gender)
        parent = options[winner.list_number]
        option = key("option", tag)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            parent_option_id=parent,
            ballot_position=winner.order,
            ballot_label=winner.name,
        )
        candidacy = key("candidacy", tag)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=parent,
            role="list_member",
            list_position=winner.order,
            ballot_name=winner.name,
        )
        p.add("outcome", result_set_id=result_set, option_id=parent, candidacy_id=candidacy, kind="elected")
        totals["winners"] += 1
    return totals


def _statistic_pages(p: Package, round_: str, authorities_: list[Authority]) -> dict[int, str] | None:
    names = {a.stats for a in authorities_ if a.stats}
    if not names:
        return None
    (name,) = names
    return _source_pages(p, name)


def election(tag: str) -> Package:
    day = DAYS[tag]
    round_ = tag[0]
    here = [a for a in authorities()[round_] if a.day == day]
    ordinal = {"1": "first", "2": "second", "3": "third", "4": "fourth"}[round_]
    where = {"1w": " (West Bank)", "1g": " (Gaza Strip)"}.get(tag, "")
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The Palestinian local elections of {day:%-d %B %Y} ({ordinal} round{where}): the councils of "
        f"{len(here)} local authorities, by authority, from the Central Elections Commission's tables",
        depends=[f"{cec_local.SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {PALESTINE, COUNTRY}
    for url in FILES.values():
        p.uses(raw.cached(SOURCE, url))
    pages = _source_pages(p, f"round{round_}")
    stats = _statistic_pages(p, round_, here)
    election_id = key("election", day)
    p.add("election", id=election_id, polity_id=PALESTINE, kind="local", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{day.year} Palestinian local elections, {ordinal} round{where}", "en", "official")
    prefix = f"ps/{day}-local"
    p.add("slug", path=prefix, entity_id=election_id, is_current=True)
    winners = winners_of_round4() if round_ == "4" else {}
    totals: Counter[str] = Counter()
    seen: Counter[str] = Counter()
    for authority in sorted(here, key=lambda a: (a.district, a.number)):
        seen[authority.district] += 1
        slug = f"{prefix}/{changeset.slug(authority.district)}-{seen[authority.district]:03d}"
        if authority.candidates is not None:
            totals.update(_block_contest(p, election_id, authority, slug, pages, stats))
        else:
            totals.update(_list_contest(p, election_id, authority, slug, pages, stats, winners.get(authority.number)))
        totals["authorities"] += 1
    p.add(
        "note",
        id=key("note", election_id, "totals"),
        entity_id=election_id,
        kind="caveat",
        body_md=_summary(tag, round_, totals),
        lang="en",
    )
    return p


def _summary(tag: str, round_: str, totals: Counter[str]) -> str:
    text = f"The tables hold {totals['authorities']} local authorities"
    if round_ in ("1", "2"):
        text += f" and {totals['winners']} winners with {totals['votes']:,} votes."
    else:
        text += f" and {totals['seats']:,} council seats with {totals['votes']:,} list votes."
    if totals["registered"]:
        text += (
            f" The Commission's statistics give {totals['registered']:,} registered voters and "
            f"{totals['cast']:,} voters who took part in them."
        )
    if tag in ("1w", "1g"):
        text += (
            " The Commission's page puts 22 localities on 23 December 2004 and 14 on 27 January 2005; its tables "
            "name 26 West Bank localities and 10 in the Gaza Strip, 36 in all."
        )
    if tag == "2":
        text += (
            " The Commission's page and statistics count 82 localities; its results table also names Al-Braij "
            "(Deir al-Balah), which the statistics leave out."
        )
    return text
