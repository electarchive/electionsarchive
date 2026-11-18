"""Serbia's National Assembly elections of 1992, 1993, 1997 and 2000, from the Republic Electoral Commission's archive.

The archive holds these elections only as scans. For 1992, 1993 and 1997 the Commission's report of the overall
results prints, for every list, its votes and seats in each constituency (izborna jedinica) and in the Republic: nine
constituencies in 1992 and 1993, twenty-nine in 1997. For 2000, when the whole Republic was one constituency, the
report prints each list's national votes and seats. Every figure was read from the page images, each list's
constituency votes and seats checked against the national total the report prints for it, and each constituency's
seats against the seats it prints for the constituency; the transcription, with each figure's page and bounding box,
lives in ``rik_poslanici.json`` beside this module.
"""

import json
import urllib.parse
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.rs import rik, rik_predsednik

DATA_FILE = Path(__file__).with_name("rik_poslanici.json")
DIGITIZED = rik_predsednik.DIGITIZED
COUNTRY = rik_predsednik.COUNTRY
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = (
    rik.VOTES,
    rik.REGISTERED,
    rik.CAST,
    rik.VALID,
    rik.INVALID,
    rik.VOTERS,
)
SYSTEM = rik.key("electoral_system", "parliament-constituencies")
SYSTEM_RULES: dict[str, object] = {
    "formula": "dhondt",
    "threshold": {"level": "list", "scope": "contest", "percent": 5},
    "lists": {"order": "closed", "vacancy": "next_on_list"},
}
SYSTEM_NOTE = (
    "From 1992 to 1997 the 250 members of the National Assembly were elected in multi-member constituencies, nine in "
    "1992 and 1993 and twenty-nine in 1997, each allocating its seats by D'Hondt among the lists that won at least 5% "
    "of the votes cast in the constituency. Lists were closed, and the list holder assigned the seats to candidates."
)


def _url(path: str) -> str:
    return f"{rik.ARCHIVE}/poslanici/{urllib.parse.quote(path)}"


URLS = {
    "1992": _url("1992/1992 izvestaj o ukupnim rezultatima np.pdf"),
    "1993": _url("1993/1993 izvestaj o ukupnim rezultatima np.pdf"),
    "1997": _url("1997/2. 1997 izvestaj o ukupnim rezultatima np.pdf"),
    "2000": _url("2000/2. izvestaj o ukupnim rezultatima 2000.pdf"),
}

NINE = ("Београд", "Зрењанин", "Крагујевац", "Лесковац", "Ниш", "Нови Сад", "Приштина", "Смедерево", "Ужице")
TWENTY_NINE = (
    "Палилула",
    "Вождовац",
    "Нови Београд",
    "Чукарица",
    "Суботица",
    "Зрењанин",
    "Панчево",
    "Сомбор",
    "Нови Сад",
    "Врбас",
    "Сремска Митровица",
    "Ваљево",
    "Шабац",
    "Смедерево",
    "Пожаревац",
    "Крагујевац",
    "Јагодина",
    "Зајечар",
    "Ужице",
    "Чачак",
    "Краљево",
    "Крушевац",
    "Ниш",
    "Прокупље",
    "Лесковац",
    "Врање",
    "Косовска Митровица",
    "Приштина",
    "Пећ",
)


@dataclass(frozen=True)
class Election:
    year: str
    day: date
    last: date
    native: str
    constituencies: tuple[str, ...]
    seats: tuple[int, ...]  # seats of each constituency, as the report prints them
    declared: dict[int, int]  # the Republic's figures, as the report prints them
    note: str


ELECTIONS = [
    Election(
        "1992",
        date(1992, 12, 20),
        date(1993, 1, 3),
        "Избори за народне посланике у Народну скупштину Републике Србије 1992.",
        NINE,
        (46, 28, 29, 25, 24, 28, 24, 22, 24),
        {REGISTERED: 6774995, VOTERS: 4723711, INVALID: 275861, VALID: 4437906},
        (
            "Voting took place at 9,741 of 9,916 polling stations; it was repeated at some on 27 December 1992 and 3 "
            "January 1993. The report prints no count of ballots cast, so none is stored. The lists' national votes "
            "printed in the report add up to 4,437,104, 802 fewer than the valid ballots. Two lists ran in one "
            "constituency only and the report prints a national total that differs from that constituency's figure: "
            "the group of citizens of Slavko Salontaji has 1,191 in the Republic and 1,991 in Niš, and the bloc of "
            "workers and communists of Yugoslavia 1,516 in the Republic and 1,510 in Novi Sad; the constituency "
            "figures are stored, and the national totals noted here. The group of citizens „Veselin i sin” has 591 "
            "votes in the Republic and a dash in every constituency, so no constituency figure is stored for it."
        ),
    ),
    Election(
        "1993",
        date(1993, 12, 19),
        date(1994, 1, 5),
        "Избори за народне посланике у Народну скупштину Републике Србије 1993.",
        NINE,
        (46, 28, 29, 25, 24, 28, 24, 22, 24),
        {REGISTERED: 7010389, VOTERS: 4300440, CAST: 4297568, INVALID: 171824, VALID: 4125609},
        (
            "Held on 19 December 1993, with voting repeated at some polling stations on 26 December 1993 and 5 "
            "January 1994. Ballots cast are the ballots used. The lists' national votes printed in the report add up "
            "to 4,125,576, 33 fewer than the valid ballots, and the valid and invalid ballots add up to 135 fewer "
            "than the ballots used; all are stored as printed."
        ),
    ),
    Election(
        "1997",
        date(1997, 9, 21),
        date(1997, 10, 5),
        "Избори за народне посланике у Народну скупштину Републике Србије 1997.",
        TWENTY_NINE,
        (10, 11, 12, 12, 6, 11, 9, 6, 9, 6, 8, 7, 8, 6, 7, 8, 7, 9, 9, 7, 8, 8, 11, 6, 7, 10, 7, 11, 14),
        {REGISTERED: 7210386, VOTERS: 4139080, CAST: 4136716, INVALID: 164307, VALID: 3972468},
        (
            "Held on 21 September 1997, with voting repeated at some polling stations on 28 September and 5 October "
            "1997. Ballots cast are the ballots used; the valid and invalid ballots add up to 59 more than the "
            "ballots used, and both are stored as printed. The lists' votes add up to the valid ballots. The scan "
            "cuts off the right edge of the last column on some pages; those figures were read from what remains "
            "and checked against the lists' national totals and the constituencies' seats."
        ),
    ),
]

# The 2000 election: one constituency, the whole Republic.
LISTS_2000 = [
    ("СРПСКА РАДИКАЛНА СТРАНКА - др ВОЈИСЛАВ ШЕШЕЉ", 322615, 23),
    ("„СРПСКИ ПОКРЕТ ОБНОВЕ - ВУК ДРАШКОВИЋ” - ВУК ДРАШКОВИЋ", 141401, 0),
    ("СОЦИЈАЛИСТИЧКА ПАРТИЈА СРБИЈЕ - СЛОБОДАН МИЛОШЕВИЋ", 516326, 37),
    (
        (
            "ДЕМОКРАТСКА ОПОЗИЦИЈА СРБИЈЕ - др ВОЈИСЛАВ КОШТУНИЦА (Демократска странка, Демократска странка Србије, "
            "Социјалдемократија, Грађански савез Србије, Демохришћанска странка Србије, Нова Србија, Покрет за "
            "демократску Србију, Лига социјалдемократа Војводине, Реформска демократска странка Војводине, Коалиција "
            "Војводина, Савез војвођанских Мађара, Демократска алтернатива, Демократски центар, Нова демократија, "
            "Социјалдемократска унија, Санџачка демократска партија, Лига за Шумадију, Српски покрет отпора - "
            "Демократски покрет)"
        ),
        2404758,
        176,
    ),
    ("СТРАНКА СРПСКОГ ЈЕДИНСТВА - професор БОРИСЛАВ ПЕЛЕВИЋ", 200052, 14),
    ("ДЕМОКРАТСКА СОЦИЈАЛИСТИЧКА ПАРТИЈА - МИЛОРАД ВУЧЕЛИЋ", 31973, 0),
    ("ЈУГОСЛОВЕНСКА ЛЕВИЦА - ЈУЛ", 14324, 0),
    ("СРПСКА СОЦИЈАЛ-ДЕМОКРАТСКА ПАРТИЈА - ЗОРАН ЛИЛИЋ", 29400, 0),
]
DECLARED_2000 = {REGISTERED: 6508856, VOTERS: 3752170, CAST: 3750649, INVALID: 89800, VALID: 3660849}

METHOD = (
    "Digitized from the Republic Electoral Commission's report of the overall results of the election, published in "
    "its archive as a scan. The report prints, for every list, its votes and seats in each constituency and in the "
    "Republic, and the registered voters, voters, and ballots for the Republic only. Each figure was read from the "
    "page image and keeps its page and bounding box; each list's constituency votes and seats were checked against "
    "the national totals the report prints for it, and each constituency's seats against the seats the report prints "
    "for the constituency. A dash in the report, for a list that did not run in a constituency, is stored as no "
    "value. Lists are numbered in the report's order, by seats and then votes, not by their place on the ballot."
)
METHOD_2000 = (
    "Digitized from the Republic Electoral Commission's report of the overall results of the election, adopted on 12 "
    "January 2001 and published in its archive as a scan. The whole Republic was one constituency; the report prints "
    "each list's votes and seats and the Republic's registered voters, voters, and ballots. Ballots cast are the "
    "ballots used. The lists' votes add up to the valid ballots and their seats to 250. Voting was repeated at some "
    "polling stations on 10 January 2001. Lists are numbered as the report numbers them."
)


@cache
def data() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text())


def slug(year: str) -> str:
    day = {e.year: e.day for e in ELECTIONS}.get(year, date(2000, 12, 23))
    return f"{day}-parliament"


SYSTEMS = f"{rik.SOURCE}/systems-1992"


def packages() -> list[str]:
    return [SYSTEMS, *(f"{rik.SOURCE}/{slug(y)}" for y in ("1992", "1993", "1997", "2000"))]


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(rik.SOURCE, url, suffix=".pdf")


def parse() -> None:
    systems().write()
    for e in ELECTIONS:
        build(e).write()
    build_2000().write()


def systems() -> Package:
    """The electoral system of the constituency elections of 1992 to 1997, shared by their packages."""
    p = Package(
        SYSTEMS,
        rik.SOURCE,
        "The electoral system of Serbia's parliamentary elections of 1992, 1993 and 1997, held in constituencies",
        depends=[f"{rik.SOURCE}/registry"],
    )
    p.external.add(rik.SERBIA)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=rik.SERBIA,
        valid_from=date(1992, 12, 20),
        valid_to=date(2000, 12, 22),
        rules=SYSTEM_RULES,
        rules_version=1,
    )
    p.add("note", id=rik.key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


def _election(p: Package, s: str, day: date, last: date, title: str, native: str) -> tuple[str, str]:
    election = rik.key("election", s)
    path = f"rs/{day}-general"
    p.add("election", id=election, polity_id=rik.SERBIA, kind="general", date_from=day, date_to=last, status="held")
    p.name(election, title, "en", "official")
    p.name(election, native, "sr", "native", False)
    p.add("slug", path=path, entity_id=election, is_current=True)
    return election, path


def _national(p: Package, s: str, election: str, day: date, declared: dict[int, int], note: str) -> None:
    """The Republic's registered voters, voters, and ballots, which the report prints for no constituency."""
    result_set = rik.key("result_set", s, "republic")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=None,
        stage="official",
        kind="counted",
        source_id=rik.SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=rik.key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
    unit = rik_predsednik._unit(p, f"{day}:np:total", "aggregate", COUNTRY, "Република Србија")
    rows = [(unit, None, measure, value) for measure, value in declared.items()]
    _values(p, result_set, rows, [])


def _values(
    p: Package,
    result_set: str,
    rows: list[tuple[str, str | None, int, int]],
    locators: list[tuple[str, str | None, int, str, float, float, float, float]],
) -> None:
    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
            schema=["unit_id", "option_id", "measure", "value"],
            orient="row",
            schema_overrides={"option_id": pl.String},
        ).with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    if locators:
        p.frame(
            "value_locator",
            pl.DataFrame(
                locators,
                schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
                orient="row",
                schema_overrides={"option_id": pl.String},
            ).with_columns(
                result_set_id=pl.lit(result_set),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                source_id=pl.lit(None, pl.String),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )


def _pages(p: Package, url: str, used: set[int]) -> dict[int, str]:
    stored = raw.cached(rik.SOURCE, url)
    artifact = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{rik.SOURCE_ID}\x1f{stored.storage_key}"))
    pages: dict[int, str] = {}
    for number, (width, height) in sorted(rik_predsednik._sizes(stored.path).items()):
        if number not in used:
            continue
        pages[number] = p.id("page", stored.sha256, number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{rik.SOURCE}/{stored.sha256}-{number}.png",
            width=round(width * 100 / 72),
            height=round(height * 100 / 72),
        )
    return pages


def build(e: Election) -> Package:
    s = slug(e.year)
    lists: list[dict[str, Any]] = data()[e.year]["lists"]
    p = Package(
        f"{rik.SOURCE}/{s}",
        rik.SOURCE,
        f"The {e.year} Serbian parliamentary election by constituency, digitized from the Republic Electoral "
        "Commission's report of the overall results",
        depends=[f"{rik.SOURCE}/geo", f"{rik.SOURCE}/registry", SYSTEMS],
    )
    p.external |= {rik.SERBIA, COUNTRY}
    p.artifact(raw.cached(rik.SOURCE, URLS[e.year]), rik.SOURCE_ID)
    title = f"{e.year} Serbian parliamentary election"
    election, path = _election(p, s, e.day, e.last, title, e.native)
    _national(p, s, election, e.day, e.declared, METHOD + " " + e.note)
    used = {int(cell[2][0]) for row in lists for cell in row["cells"].values()}
    pages = _pages(p, URLS[e.year], used)
    scale = 100 / 72
    for number, (place, seats) in enumerate(zip(e.constituencies, e.seats, strict=True), 1):
        c = str(number)
        contest = rik.key("contest", s, number)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            office_id=None,
            body_id=rik.BODIES["parliament"],
            magnitude=seats,
            round_number=1,
            previous_round_id=None,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest, f"{title}, constituency {number} ({place})", "en", "official")
        p.name(contest, f"Изборна јединица {number}. {place}", "sr", "native", False)
        p.add("slug", path=f"{path}/constituency-{number}", entity_id=contest, is_current=True)
        result_set = rik.key("result_set", s, number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=rik.SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note", id=rik.key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en"
        )
        unit = rik_predsednik._unit(
            p, f"{e.day}:ij:{number}", "aggregate", COUNTRY, f"Изборна јединица {number}. {place}"
        )
        rows: list[tuple[str, str | None, int, int]] = []
        locators: list[tuple[str, str | None, int, str, float, float, float, float]] = []
        total = 0
        for row in lists:
            if c not in row["cells"]:
                continue
            votes, won, box = row["cells"][c]
            option = rik.key("option", s, number, row["n"])
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                ballot_position=None,
                ballot_label=row["name"],
                party_id=None,
            )
            if won:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=won)
                total += won
            rows.append((unit, option, VOTES, votes))
            page, x0, y0, x1, y1 = box
            locators.append((unit, option, VOTES, pages[int(page)], x0 * scale, y0 * scale, x1 * scale, y1 * scale))
        if total != seats:
            raise ValueError(f"{s}: constituency {number} has {total} seats against {seats} printed")
        _values(p, result_set, rows, locators)
    if CAST in e.declared and e.declared[VALID] + e.declared[INVALID] != e.declared[CAST]:
        p.accept(
            "accounting.ballots_sum",
            f"The report prints {e.declared[VALID]:,} valid and {e.declared[INVALID]:,} invalid ballots for the "
            f"Republic, which do not add up to the {e.declared[CAST]:,} ballots used it prints; all are stored as "
            "printed.",
        )
    return p


def build_2000() -> Package:
    day, s = date(2000, 12, 23), slug("2000")
    p = Package(
        f"{rik.SOURCE}/{s}",
        rik.SOURCE,
        "The 2000 Serbian parliamentary election, national totals digitized from the Republic Electoral Commission's "
        "report of the overall results",
        depends=[f"{rik.SOURCE}/geo", f"{rik.SOURCE}/registry"],
    )
    p.external |= {rik.SERBIA, COUNTRY}
    p.artifact(raw.cached(rik.SOURCE, URLS["2000"]), rik.SOURCE_ID)
    title = "2000 Serbian parliamentary election"
    native = "Избори за народне посланике у Народну скупштину Републике Србије 2000."
    election, path = _election(p, s, day, date(2001, 1, 10), title, native)
    contest = rik.key("contest", s)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        office_id=None,
        body_id=rik.BODIES["parliament"],
        magnitude=250,
        round_number=1,
        previous_round_id=None,
        electoral_system_id=rik.SYSTEM_OF["parliament"],
        default_result_kind="counted",
    )
    p.name(contest, title, "en", "official")
    p.add("slug", path=f"{path}/assembly", entity_id=contest, is_current=True)
    result_set = rik.key("result_set", s)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=rik.SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note", id=rik.key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD_2000, lang="en"
    )
    unit = rik_predsednik._unit(p, f"{day}:np:total", "aggregate", COUNTRY, "Република Србија")
    rows: list[tuple[str, str | None, int, int]] = [(unit, None, m, v) for m, v in DECLARED_2000.items()]
    for number, (label, votes, seats) in enumerate(LISTS_2000, 1):
        option = rik.key("option", s, number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            ballot_position=number,
            ballot_label=label,
            party_id=None,
        )
        if seats:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
        rows.append((unit, option, VOTES, votes))
    for measure, value in DECLARED_2000.items():
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    _values(p, result_set, rows, [])
    return p
