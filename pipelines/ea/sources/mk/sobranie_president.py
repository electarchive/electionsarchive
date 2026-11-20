"""The election of the President of the Socialist Republic of Macedonia by the Assembly on 27 January 1991.

The Assembly publishes the stenographic notes of each plenary session on sobranie.mk. The notes of the Fifth session
record the vote: the voting commission's report gives the deputies who voted, the votes for the one candidate, and the
invalid ballots (printed as a word). The 1991 Constitution made the office directly elected, so this was the last
election of its kind.

The same sitting then voted twice on the Vice-President of the Republic, proposed by the President. Neither ballot
reached two thirds of all deputies, so nobody was elected. Each ballot is stored as its own round contest. The Sixth session, on 1 February 1991, repeated the whole procedure: its
one ballot elected Ljupco Georgievski with 88 of the 118 deputies who voted, and is stored as a further contest.
"""

import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "mk.sobranie_president"
MACEDONIA = str(uuid5(package.NAMESPACE, "polity\x1fMK"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fMKD"))
SCHEME = "mk.nation"
HOME = "https://www.sobranie.mk/"
NOTES = HOME + "WBStorage/Files/05sed27jan91god.pdf"
SIXTH = HOME + "WBStorage/Files/06sed1fev91god.pdf"
VOTES, BALLOTS, VALID, INVALID = 1, 3, 4, 5
FLAG_DERIVED = 1
DAY = date(1991, 1, 27)
REPEAT = date(1991, 2, 1)
WINNER = "Киро Глигоров"
DEPUTY = "Љупчо Ѓеорѓиевски"
ASSEMBLY_SIZE = 120
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


ASSEMBLY = key("source", "sobranie")
PRESIDENCY = key("office", "president")
VICE = key("office", "vice_president")
SYSTEM = key("electoral_system", "president")


@dataclass(frozen=True)
class Count:
    field: str  # a candidate's name, `took_part`, or `invalid`
    value: int
    pattern: str  # over the page text; the first group is the number as printed


COUNTS = (
    Count("took_part", 119, r"од вкупно\s+(119)\s+пратеници\s+кои\s+гласеле"),
    Count(WINNER, 114, r"Киро\s+Глигоров\s+гласале\s+(114)\s+пратеници"),
    Count("invalid", 5, r"Неважечки\s+гласачки\s+ливчиња\s+имаше\s+(пет)"),
)
DEPUTY_ANCHOR = "Минуваме на точката два"
DEPUTY_ROUNDS = (
    (
        Count("took_part", 119, r"од\s+вкупно\s+(119)\s+пратеници\s+кои\s+гласале"),
        Count(DEPUTY, 78, r"гласале\s+(78)\s+пратеници"),
        Count("invalid", 41, r"Неважечки\s+гласачки\s+ливчиња\s+има\s+(41)"),
    ),
    (
        Count("took_part", 118, r"од\s+вкупно\s+(118)\s+пратеници\s+кои\s+гласале"),
        Count(DEPUTY, 73, r"гласале\s+(73)\s+пратеници"),
        Count("invalid", 45, r"Неважечки\s+гласачки\s+ливчиња\s+има\s+(45)"),
    ),
)
REPEAT_ANCHOR = "Врз основа на резултатите од гласањето"
REPEAT_ROUND = (
    Count("took_part", 118, r"од\s+вкупно\s+од\s+(118)\s+пратеници\s+кои\s+гласале"),
    Count(DEPUTY, 88, r"гласале\s+(88)\s+пратеници"),
    Count("invalid", 30, r"Неважечки\s+ливчиња\s+има\s+(30)"),
)
REPEAT_NEEDLES = (
    "избран за потпретседател на претседателот на социјалистичка република македонија",
    "доби двотретинско мнозинство",
)
NEEDLES = (
    "избран за претседател на социјалистичка република македонија",
    "двотретинско мнозинство",
    "петата седница на собранието",
    "не е избран за потпретседател",
    "гласањето се повторува",
)


@dataclass
class Page:
    width: float
    height: float
    words: list[tuple[int, int, float, float, float, float]] = field(default_factory=list)


def fetch() -> None:
    raw.fetch(SOURCE, NOTES, suffix=".pdf")
    raw.fetch(SOURCE, SIXTH, suffix=".pdf")


def read(url: str = NOTES) -> tuple[str, list[tuple[int, int, int]], list[Page]]:
    out = subprocess.run(
        ["pdftotext", "-bbox", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True
    ).stdout
    root = ET.fromstring(re.sub(rb"<!DOCTYPE[^>]*>", b"", out))
    ns = "{http://www.w3.org/1999/xhtml}"
    pages: list[Page] = []
    text = ""
    spans: list[tuple[int, int, int]] = []
    for number, page in enumerate(root.iter(f"{ns}page")):
        sizes = Page(float(page.get("width", "0")), float(page.get("height", "0")))
        pages.append(sizes)
        for word in page.iter(f"{ns}word"):
            start = len(text)
            text += (word.text or "") + " "
            spans.append((start, len(text) - 1, number))
            sizes.words.append(
                (
                    start,
                    len(text) - 1,
                    float(word.get("xMin", "0")),
                    float(word.get("yMin", "0")),
                    float(word.get("xMax", "0")),
                    float(word.get("yMax", "0")),
                )
            )
    return text, spans, pages


def locate(
    counts: tuple[Count, ...] = COUNTS,
    anchor: str = "Комисијата за гласање го поднесува",
    after: int = 0,
    url: str = NOTES,
) -> tuple[dict[str, tuple[int, float, float, float, float]], int]:
    """Each printed count's page (from 1) and normalized box, found in order in the notes from `anchor`, with the
    text position the last count ends at."""
    text, spans, pages = read(url)
    found: dict[str, tuple[int, float, float, float, float]] = {}
    position = text.index(anchor, after)
    for count in counts:
        match = re.compile(count.pattern, re.IGNORECASE).search(text, position)
        if match is None:
            raise ValueError(f"the notes do not print {count.field} {count.value}")
        shown = match[1]
        if (int(shown) if shown.isdigit() else {"пет": 5}[shown.lower()]) != count.value:
            raise ValueError(f"{count.field}: the notes print {shown}, not {count.value}")
        position = match.end(1)
        at = match.start(1)
        number = next(n for start, end, n in spans if start <= at < end)
        page = pages[number]
        word = next(w for w in page.words if w[0] == at)
        found[count.field] = (
            number + 1,
            word[2] / page.width,
            word[3] / page.height,
            word[4] / page.width,
            word[5] / page.height,
        )
    return found, position


def body(url: str) -> str:
    out = subprocess.run(["pdftotext", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True).stdout
    return " ".join(out.decode("utf-8", "replace").split()).lower()


def check() -> None:
    for url, needles in ((NOTES, NEEDLES), (SIXTH, REPEAT_NEEDLES)):
        text = body(url)
        for needle in needles:
            if needle not in text:
                raise ValueError(f"the notes do not contain {needle!r}")
    votes = {c.field: c.value for c in REPEAT_ROUND}
    if votes[DEPUTY] + votes["invalid"] != votes["took_part"]:
        raise ValueError("the votes and invalid ballots do not add up to the deputies who voted")
    if votes[DEPUTY] * 3 < ASSEMBLY_SIZE * 2:
        raise ValueError("the deputy candidate lacks two thirds of all deputies")
    votes = {c.field: c.value for c in COUNTS}
    if votes[WINNER] + votes["invalid"] != votes["took_part"]:
        raise ValueError("the votes and invalid ballots do not add up to the deputies who voted")
    if votes[WINNER] * 3 < ASSEMBLY_SIZE * 2:
        raise ValueError("the winner lacks two thirds of all deputies")
    for counts in DEPUTY_ROUNDS:
        votes = {c.field: c.value for c in counts}
        if votes[DEPUTY] + votes["invalid"] != votes["took_part"]:
            raise ValueError("the votes and invalid ballots do not add up to the deputies who voted")
        if votes[DEPUTY] * 3 >= ASSEMBLY_SIZE * 2:
            raise ValueError("the deputy candidate has two thirds of all deputies, so was elected")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/{DAY}"]


def parse() -> None:
    check()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The presidency of the Socialist Republic of Macedonia and the electoral system of its election by the Assembly",
    )
    p.external.add(MACEDONIA)
    p.add("scheme", code=SCHEME, polity_id=MACEDONIA, kind="administrative", rank=1, noun="nation", code_system=None)
    p.add("office", id=PRESIDENCY, polity_id=MACEDONIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Republic of Macedonia", "en", "official")
    p.name(PRESIDENCY, "Претседател на Република Македонија", "mk", "official", False)
    p.add("office", id=VICE, polity_id=MACEDONIA, kind="other")
    p.name(VICE, "Vice-President of the Republic of Macedonia", "en", "official")
    p.name(VICE, "Потпретседател на Република Македонија", "mk", "official", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=MACEDONIA,
        valid_to=date(1991, 11, 17),
        rules={"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 2 / 3}]},
        rules_version=1,
    )
    p.name(SYSTEM, "Election of the President of the Republic by the Assembly", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="The Assembly elected the President of the Republic by secret ballot, by a two-thirds majority of the "
        "votes of all its deputies, from candidates proposed by a group of deputies. It elected the Vice-President "
        "the same way, in the sitting that elected the President, on the President's proposal; a ballot without two "
        "thirds was repeated. The 1991 Constitution replaced this with direct election.",
        lang="en",
    )
    return p


def election() -> Package:
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The election of the President of the Socialist Republic of Macedonia by the Assembly on 27 January 1991 and "
        "of its Vice-President by 1 February 1991, from the stenographic notes of the Assembly's Fifth and Sixth sessions",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {MACEDONIA, COUNTRY}
    p.add(
        "source",
        id=ASSEMBLY,
        kind="official_body",
        publisher="Собрание на Република Северна Македонија",
        title="Stenographic notes of the plenary sessions of the Assembly",
        url=HOME,
        license="No license stated; published by the Assembly for public use",
        attribution="Собрание на Република Северна Македонија",
    )
    stored = raw.cached(SOURCE, NOTES)
    artifact = p.artifact(stored, ASSEMBLY)
    sixth = raw.cached(SOURCE, SIXTH)
    sixth_artifact = p.artifact(sixth, ASSEMBLY)
    id = key("election", DAY)
    path = f"mk/{DAY}-president"
    p.add("election", id=id, polity_id=MACEDONIA, kind="indirect", date_from=DAY, date_to=REPEAT, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, "1991 Macedonian presidential election", "en", "official")
    p.name(id, "Избор на претседател на Република Македонија, 1991", "mk", "native", False)
    unit = key("unit", DAY)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=MACEDONIA,
        scheme_code=SCHEME,
        code="MK",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    boxes, end = locate()
    deputy_boxes: list[dict[str, tuple[int, float, float, float, float]]] = []
    for counts in DEPUTY_ROUNDS:
        found, end = locate(counts, DEPUTY_ANCHOR if not deputy_boxes else "", end)
        deputy_boxes.append(found)
    pages: dict[int, str] = {}
    for number, *_rest in [*boxes.values(), *(b for each in deputy_boxes for b in each.values())]:
        if number not in pages:
            pages[number] = key("page", DAY, number)
            width, height = render(stored.path, stored.sha256, number)
            p.add(
                "source_page",
                id=pages[number],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{stored.sha256}/{number}.png",
                width=width,
                height=height,
            )
    contest = key("contest", DAY)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, "1991 presidential election", "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", DAY)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=ASSEMBLY,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md="The result as the Assembly's voting commission reported it in the plenary session of 27 January "
        "1991, from the stenographic notes. Kiro Gligorov was the only candidate, proposed by a group of 54 deputies. "
        "The Assembly's president opened the session with 118 deputies present; the commission counts 119 deputies "
        "who voted, 114 votes for the candidate, and five invalid ballots, which is more than the two thirds of all "
        "120 deputies that the law requires.",
        lang="en",
    )
    option = key("option", DAY, WINNER)
    p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_position=1, ballot_label=WINNER)
    person = key("person", WINNER)
    p.add(
        "candidacy",
        id=key("candidacy", DAY, WINNER),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=WINNER,
        is_independent=False,
    )
    p.add("person", id=person)
    p.name(person, WINNER, "mk", "official")
    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=None, kind="elected", seats=1)
    rows: list[tuple[str, str, str | None, int, int, int]] = []
    locators: list[dict[str, object]] = []
    for count in COUNTS:
        if count.field == "took_part":
            option_id, measure = None, BALLOTS
        elif count.field == "invalid":
            option_id, measure = None, INVALID
        else:
            option_id, measure = option, VOTES
        rows.append((result_set, unit, option_id, measure, count.value, 0))
        p.add("declared", result_set_id=result_set, option_id=option_id, measure=measure, value=count.value)
        page, x0, y0, x1, y1 = boxes[count.field]
        locators.append(
            {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option_id,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "page_id": pages[page],
                "source_id": None,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
            }
        )
    rows.append((result_set, unit, None, VALID, COUNTS[1].value, FLAG_DERIVED))
    deputy = key("person", DEPUTY)
    p.add("person", id=deputy)
    p.name(deputy, DEPUTY, "mk", "official")
    p.name(deputy, "Ljupco Georgievski", "en", "official", False)
    previous: str | None = None
    for number, (counts, found) in enumerate(zip(DEPUTY_ROUNDS, deputy_boxes, strict=True), start=1):
        vice = key("contest", DAY, "vice", number)
        p.add(
            "contest",
            id=vice,
            election_id=id,
            kind="office",
            office_id=VICE,
            magnitude=1,
            round_number=number,
            previous_round_id=previous,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        previous = vice
        p.name(vice, f"1991 vice-presidential election, ballot {number}", "en", "official")
        p.add("slug", path=f"{path}/vice-president/ballot-{number}", entity_id=vice, is_current=True)
        vice_set = key("result_set", DAY, "vice", number)
        p.add(
            "result_set",
            id=vice_set,
            election_id=id,
            contest_id=vice,
            stage="official",
            kind="counted",
            source_id=ASSEMBLY,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        voted, for_, invalid = (c.value for c in counts)
        p.add(
            "note",
            id=key("note", vice_set),
            entity_id=vice_set,
            kind="methodology",
            body_md=f"Ballot {number} of the Assembly's vote on the Vice-President, as its president announced the "
            f"voting commission's result in the plenary session of 27 January 1991, from the stenographic notes. "
            f"Ljupco Georgievski was the only candidate, proposed by the President of the Republic and by a deputy "
            f"of the Macedonian party. Of {voted} deputies who voted, {for_} voted for him and {invalid} ballots "
            f"were invalid, short of the two thirds of all 120 deputies that the law requires, so nobody was elected"
            f"{' and the vote was repeated' if number == 1 else ' and the whole procedure was to be repeated'}.",
            lang="en",
        )
        vice_option = key("option", DAY, "vice", number, DEPUTY)
        p.add(
            "contest_option",
            id=vice_option,
            contest_id=vice,
            kind="candidate",
            ballot_position=1,
            ballot_label=DEPUTY,
        )
        p.add(
            "candidacy",
            id=key("candidacy", DAY, "vice", number, DEPUTY),
            person_id=deputy,
            option_id=vice_option,
            role="head",
            ballot_name=DEPUTY,
            is_independent=False,
        )
        for count in counts:
            if count.field == "took_part":
                option_id, measure = None, BALLOTS
            elif count.field == "invalid":
                option_id, measure = None, INVALID
            else:
                option_id, measure = vice_option, VOTES
            rows.append((vice_set, unit, option_id, measure, count.value, 0))
            p.add("declared", result_set_id=vice_set, option_id=option_id, measure=measure, value=count.value)
            page, x0, y0, x1, y1 = found[count.field]
            locators.append(
                {
                    "result_set_id": vice_set,
                    "unit_id": unit,
                    "option_id": option_id,
                    "channel": 0,
                    "measure": measure,
                    "count_round": 0,
                    "page_id": pages[page],
                    "source_id": None,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                }
            )
        rows.append((vice_set, unit, None, VALID, for_, FLAG_DERIVED))
    repeat_boxes, _ = locate(REPEAT_ROUND, REPEAT_ANCHOR, 0, SIXTH)
    repeat_pages: dict[int, str] = {}
    for number, *_rest in repeat_boxes.values():
        if number not in repeat_pages:
            repeat_pages[number] = key("page", REPEAT, number)
            width, height = render(sixth.path, sixth.sha256, number)
            p.add(
                "source_page",
                id=repeat_pages[number],
                artifact_id=sixth_artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{sixth.sha256}/{number}.png",
                width=width,
                height=height,
            )
    again = key("contest", DAY, "vice", "repeat")
    p.add(
        "contest",
        id=again,
        election_id=id,
        kind="office",
        office_id=VICE,
        magnitude=1,
        round_number=1,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(again, "1991 vice-presidential election, repeated procedure", "en", "official")
    p.add("slug", path=f"{path}/vice-president/repeated-procedure", entity_id=again, is_current=True)
    again_set = key("result_set", DAY, "vice", "repeat")
    p.add(
        "result_set",
        id=again_set,
        election_id=id,
        contest_id=again,
        stage="official",
        kind="counted",
        source_id=ASSEMBLY,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    voted, for_, invalid = (c.value for c in REPEAT_ROUND)
    p.add(
        "note",
        id=key("note", again_set),
        entity_id=again_set,
        kind="methodology",
        body_md="The Assembly repeated its whole procedure for the Vice-President in its plenary session of 1 February "
        "1991, after the two failed ballots of 27 January, from the stenographic notes of the Sixth session. "
        f"Ljupco Georgievski was the only candidate again. Of {voted} deputies who voted, {for_} voted for him and "
        f"{invalid} ballots were invalid. That is more than the two thirds of all 120 deputies that the law "
        "requires, so he was elected.",
        lang="en",
    )
    again_option = key("option", DAY, "vice", "repeat", DEPUTY)
    p.add(
        "contest_option",
        id=again_option,
        contest_id=again,
        kind="candidate",
        ballot_position=1,
        ballot_label=DEPUTY,
    )
    again_candidacy = key("candidacy", DAY, "vice", "repeat", DEPUTY)
    p.add(
        "candidacy",
        id=again_candidacy,
        person_id=deputy,
        option_id=again_option,
        role="head",
        ballot_name=DEPUTY,
        is_independent=False,
    )
    p.add("outcome", result_set_id=again_set, option_id=again_option, candidacy_id=None, kind="elected", seats=1)
    for count in REPEAT_ROUND:
        if count.field == "took_part":
            option_id, measure = None, BALLOTS
        elif count.field == "invalid":
            option_id, measure = None, INVALID
        else:
            option_id, measure = again_option, VOTES
        rows.append((again_set, unit, option_id, measure, count.value, 0))
        p.add("declared", result_set_id=again_set, option_id=option_id, measure=measure, value=count.value)
        page, x0, y0, x1, y1 = repeat_boxes[count.field]
        locators.append(
            {
                "result_set_id": again_set,
                "unit_id": unit,
                "option_id": option_id,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "page_id": repeat_pages[page],
                "source_id": None,
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
            }
        )
    rows.append((again_set, unit, None, VALID, for_, FLAG_DERIVED))
    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "flags": pl.Int16,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "page_id": pl.String,
                "source_id": pl.String,
                "x0": pl.Float64,
                "y0": pl.Float64,
                "x1": pl.Float64,
                "y1": pl.Float64,
            },
        ),
    )
    return p


def render(path: Path, sha256: str, number: int) -> tuple[int, int]:
    """Render a page to `data/pages/...` for upload and return its size in pixels."""
    target = package.DATA / "pages" / SOURCE / sha256
    target.mkdir(parents=True, exist_ok=True)
    stem = target / str(number)
    subprocess.run(
        [
            "pdftoppm",
            "-r",
            "150",
            "-gray",
            "-png",
            "-f",
            str(number),
            "-l",
            str(number),
            "-singlefile",
            str(path),
            str(stem),
        ],
        check=True,
    )
    out = subprocess.run(["file", str(stem.with_suffix(".png"))], capture_output=True, text=True, check=True).stdout
    found = re.search(r"(\d+) x (\d+)", out)
    if found is None:
        raise ValueError(f"{stem}.png: no size")
    return int(found[1]), int(found[2])
