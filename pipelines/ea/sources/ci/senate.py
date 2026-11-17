"""Côte d'Ivoire: the Senate elections of 2023 from the Commission Électorale Indépendante.

Two thirds of the senators are elected by indirect suffrage, two per region and per autonomous district, by closed
winner-take-all lists of two candidates. The commission publishes the polls of 16 September and 23 December 2023 as
one PDF table: the left block gives each circumscription's electors and ballots, the right block its lists (a
dossier number, a party label, a score, and the two candidates, one printed above the score line and one below).
The blocks are laid out by position, so the parser reads word coordinates from the text layer and gives each
circumscription the run of lists whose votes equal its own printed valid votes.

Each poll is one package, `ci.cei/senate-<date>`, with one contest per circumscription. The Guémon region voted on
23 December 2023; the other 32 circumscriptions voted on 16 September 2023.
"""

import functools
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date

from ea import package, raw
from ea.package import Package
from ea.sources.ci import regional
from ea.sources.ci.cei import FILES, IVORY_COAST, SENATE, SOURCE, SYSTEM_LIST, key
from ea.sources.ci.regional import ENTITIES, WORD, Word, fix, fold, integers, lines

NAME = "esen2023"
URL = FILES[NAME]
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
LEFT = 680.0  # the left block (names and figures of a circumscription) ends here, in points
SCORE_X = 1000.0  # a score starts right of this point
HEADER_Y = 175.0  # words above this point are headings (page 1 also prints the national row below it)
PERCENT = re.compile(r"^\d{1,3},\d{1,2}%$")
FIGURE = re.compile(r"^\d{1,3},\d+%?$")  # one list prints its share as a fraction, `0,24226804`, without a sign
SEX = {"M", "F"}
DISTRICTS = {"DISTRICTAUTONOMEDABIDJAN": ("A1", "Abidjan"), "DISTRICTAUTONOMEDEYAMOUSSOUKRO": ("A2", "Yamoussoukro")}
SEPTEMBER, DECEMBER = date(2023, 9, 16), date(2023, 12, 23)
DAYS = {"GUEMON": DECEMBER}  # every other circumscription voted on the first day
PLANS = [SEPTEMBER, DECEMBER]
AREA_NAMES = {
    **{fold(name): (code, name) for code, name in regional.NAMES.items()},
}


@dataclass
class Candidate:
    name: str
    sex: str


@dataclass
class Listing:
    page: int
    y: float
    dossier: str
    party: str
    score: int
    percent: float
    result: str
    members: list[Candidate] = field(default_factory=list)


@dataclass
class Circumscription:
    code: str
    name: str
    figures: dict[str, int]
    day: date
    page: int
    y: float
    lists: list[Listing] = field(default_factory=list)


def share(text: str) -> float:
    """A printed share as a percentage."""
    value = float(text.rstrip("%").replace(",", "."))
    return value if text.endswith("%") else value * 100


@functools.cache
def words() -> tuple[Word, ...]:
    path = raw.cached(SOURCE, URL).path
    out = subprocess.run(["pdftotext", "-bbox", str(path), "-"], check=True, capture_output=True, text=True).stdout
    found: list[Word] = []
    page = 0
    for line in out.splitlines():
        if "<page " in line:
            page += 1
        match = WORD.search(line)
        if match:
            text = match[5]
            for entity, char in ENTITIES.items():
                text = text.replace(entity, char)
            found.append(Word(page, float(match[1]), float(match[2]), float(match[3]), text))
    return tuple(found)


def heading_bottom() -> dict[int, float]:
    """Where each page's column headings end: the lowest word of the heading block."""
    bottom: dict[int, float] = {}
    for word in words():
        if word.text in regional.HEADER and word.y0 < 200:
            bottom[word.page] = max(bottom.get(word.page, 0), word.y0 + 5)
    return bottom


def figures(numbers: list[int]) -> dict[str, int]:
    stations, _, _, registered, _, _, cast, invalid, valid, blank = numbers
    return {
        "stations": stations,
        "registered": registered,
        "cast": cast,
        "invalid": invalid,
        "valid": valid,
        "blank": blank,
    }


def left_rows() -> list[tuple[str, str, list[int], date, int, float]]:
    """Each circumscription's name and printed figures, with where its row sits."""
    top = heading_bottom()
    left = [w for w in words() if w.x0 < LEFT and w.y0 > top.get(w.page, 0) and w.text != "Page"]
    rows = lines(left)
    found: list[tuple[str, str, list[int], date, int, float]] = []
    labels: list[tuple[int, float, str]] = []
    for row in rows:
        tokens = ["0" if t == "-" else t for t in integers(row)]
        numbers = [int(t) for t in tokens if t.isdigit()]
        text = [t for t in tokens if not t.isdigit() and not PERCENT.match(t)]
        if row[0].text.upper().startswith("TOTAL"):
            continue
        if len(numbers) >= 10:
            found.append((fix(" ".join(text)), "", numbers[-10:], SEPTEMBER, row[0].page, row[0].y0))
        elif tokens and "Page" not in tokens:
            labels.append((row[0].page, row[0].y0, fix(" ".join(tokens))))
    out = []
    for name, _, numbers, _, page, y in found:
        near = sorted((yy, text) for p, yy, text in labels if p == page and abs(yy - y) < 20 and text)
        extra = [text for yy, text in near if yy != y and not re.fullmatch(r"\d{2}/\d{2}/\d{4}", text)]
        day = SEPTEMBER
        if any(re.fullmatch(r"23/12/2023", text) for _, text in near):
            day = DECEMBER
        full = " ".join([name, *extra]) if not name else name
        if not name:
            full = " ".join(extra)
        out.append((full, "", numbers, day, page, y))
    return out


def listings() -> list[Listing]:
    top = heading_bottom()
    right = [w for w in words() if w.x0 >= LEFT and w.y0 > top.get(w.page, 0) and w.text != "Page" and w.y0 < 790]
    found: list[Listing] = []
    names: list[tuple[int, float, Candidate]] = []
    for line in lines(right, 1.2):
        score_line = [w for w in line if w.x0 >= 690]
        percent = [w for w in line if FIGURE.match(w.text) and w.x0 >= 1070]
        if percent and any(w.text.startswith("S-") for w in line):
            dossier = next((w.text for w in line if w.text.startswith("S-")), "")
            party = " ".join(w.text for w in line if 725 <= w.x0 < 800)
            digits = [w for w in score_line if w.text.isdigit() and 1040 <= w.x0 < 1075]
            tokens = integers(digits)
            score = int(tokens[-1])
            result = " ".join(w.text for w in line if w.x0 >= 1120)
            found.append(
                Listing(
                    line[0].page,
                    line[0].y0,
                    dossier,
                    fix(party),
                    score,
                    share(percent[0].text),
                    result,
                )
            )
        sex = [w for w in line if w.x0 < 830 and w.x0 >= 800 and w.text in SEX]
        if sex:
            name = fix(" ".join(w.text for w in line if w.x0 >= 830 and w.x0 < SCORE_X))
            names.append((line[0].page, line[0].y0, Candidate(name, sex[0].text)))
    # names alternate around the score line: one before it, one after it
    order = sorted([(item.page, item.y, "list", item) for item in found] + [(p, y, "name", c) for p, y, c in names])
    for index, (_, _, kind, item) in enumerate(order):
        if kind != "list":
            continue
        assert isinstance(item, Listing)
        before, after = order[index - 1], order[index + 1]
        if before[2] != "name" or after[2] != "name":
            raise ValueError(f"list {item.dossier} is not between two candidate lines")
        assert isinstance(before[3], Candidate) and isinstance(after[3], Candidate)
        item.members = [before[3], after[3]]
    return found


def assign(circs: list[Circumscription], found: list[Listing]) -> None:
    """Give each circumscription, in table order, the run of lists whose votes equal its own valid votes."""
    count, total = len(circs), len(found)
    targets = [c.figures["valid"] - c.figures["blank"] for c in circs]
    infinity = float("inf")

    def at(page: int, y: float) -> float:
        return page * 1000 + y

    best = [[infinity] * (total + 1) for _ in range(count + 1)]
    back = [[0] * (total + 1) for _ in range(count + 1)]
    best[0][0] = 0.0
    for i in range(1, count + 1):
        for j in range(i, total + 1):
            for k in range(i - 1, j):
                if best[i - 1][k] == infinity:
                    continue
                chosen = found[k:j]
                centre = sum(at(c.page, c.y) for c in chosen) / len(chosen)
                cost = best[i - 1][k] + 1000 * abs(sum(c.score for c in chosen) - targets[i - 1])
                cost += abs(centre - at(circs[i - 1].page, circs[i - 1].y))
                if cost < best[i][j]:
                    best[i][j], back[i][j] = cost, k
    j = total
    for i in range(count, 0, -1):
        k = back[i][j]
        circs[i - 1].lists = found[k:j]
        j = k


@functools.cache
def parse_table() -> tuple[Circumscription, ...]:
    circs: list[Circumscription] = []
    for name, _, numbers, day, page, y in left_rows():
        folded = fold(name)
        if folded in DISTRICTS:
            code, short = DISTRICTS[folded]
        else:
            code, short = AREA_NAMES[folded]
        circs.append(Circumscription(code, short, figures(numbers), DAYS.get(fold(short), SEPTEMBER), page, y))
        del day
    assign(circs, listings())
    return tuple(circs)


def printed_total() -> dict[str, int]:
    """The national row, as printed: bureaus, registered, cast, invalid, valid, blank, and the lists' votes."""
    label = next(w for w in words() if w.page == 1 and w.text.upper().startswith("TOTAL/"))
    near = [w for w in words() if w.page == 1 and abs(w.y0 - label.y0) < 3]
    tokens = ["0" if t == "-" else t for t in integers(lines([w for w in near if w.x0 < LEFT], 3)[0])]
    numbers = [int(t) for t in tokens if t.isdigit()]
    out = figures([numbers[0], 0, 0, numbers[3], 0, 0, numbers[6], numbers[7], numbers[8], numbers[9]])
    right = [w for w in near if 1040 <= w.x0 < 1075 and w.text.isdigit()]
    out["listed"] = int("".join(w.text for w in sorted(right, key=lambda w: w.x0)))
    return out


def packages() -> list[str]:
    return ["ci.cei/registry", *(f"ci.cei/senate-{day}" for day in PLANS)]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    from ea.sources.ci import cei

    cei.registry().write()
    for day in PLANS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def party_label(text: str) -> str | None:
    label = {"PP-ACI": "PPA-CI"}.get(" ".join(text.upper().split()), " ".join(text.upper().split()))
    return None if label in {"INDEPENDANT", ""} else label


def party_labels() -> list[str]:
    return sorted({label for c in parse_table() for item in c.lists if (label := party_label(item.party))})


def put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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


METHOD = (
    "Counted by the Commission Électorale Indépendante per circumscription, as printed in its results table. Each "
    "of the 31 regions and the two autonomous districts elects two senators, by indirect suffrage, from closed "
    "lists of two candidates; the list with the most votes wins both seats. Registered voters are the electors of "
    "the senatorial college, ballots cast are the voters, and invalid ballots are the null ballots. Blank ballots "
    "are counted apart from the votes for lists; the printed percentages divide the valid votes and the blank "
    "ballots together."
)


def build(day: date) -> Package:
    circs = [c for c in parse_table() if c.day == day]
    p = Package(
        f"ci.cei/senate-{day}",
        SOURCE,
        f"The Ivorian Senate election of {day} in {len(circs)} circumscription{'s' if len(circs) > 1 else ''}, from "
        "the Commission Électorale Indépendante's results table",
        depends=["ci.cei/registry"],
    )
    p.external.add(IVORY_COAST)
    p.uses(raw.cached(SOURCE, URL))
    election = key("election", "senate", day)
    p.add(
        "election",
        id=election,
        polity_id=IVORY_COAST,
        kind="indirect",
        date_from=day,
        date_to=day,
        status="held",
    )
    path = f"ci/{day}-senate"
    p.add("slug", path=path, entity_id=election, is_current=True)
    when = f"{day.day} {day.strftime('%B %Y')}"
    main = day == SEPTEMBER
    p.name(
        election, "2023 Ivorian Senate election" if main else f"2023 Ivorian Senate poll of {when}", "en", "official"
    )
    p.name(
        election,
        "Élection des sénateurs de 2023"
        if main
        else f"Élection des sénateurs, scrutin du {day.day:02d}/{day.month:02d}/{day.year}",
        "fr",
        "native",
        False,
    )
    off: list[str] = []
    for circ in circs:
        code, name, f = circ.code, circ.name, circ.figures
        contest = key("contest", "senate", day, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=SENATE,
            magnitude=2,
            round_number=1,
            electoral_system_id=SYSTEM_LIST,
            default_result_kind="counted",
        )
        label = name if code.startswith("A") is False else f"{name} autonomous district"
        p.name(contest, f"2023 Ivorian Senate election, {label}", "en", "official")
        p.name(contest, f"Sénat, circonscription de {name}", "fr", "native", False)
        p.add("slug", path=f"{path}/{fold(name).lower()}", entity_id=contest, is_current=True)
        unit = key("unit", "senate", day, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=IVORY_COAST,
            scheme_code="ci.circonscription",
            code=f"senate-{day}:{code}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, name, "fr", "official")
        result_set = key("result_set", "senate", day, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=key("source", "cei"),
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        put(p, result_set, unit, None, REGISTERED, f["registered"])
        put(p, result_set, unit, None, CAST, f["cast"])
        put(p, result_set, unit, None, INVALID, f["invalid"])
        put(p, result_set, unit, None, BLANK, f["blank"])
        put(p, result_set, unit, None, VALID, f["valid"] - f["blank"])
        if f["cast"] != f["invalid"] + f["valid"]:
            off.append(f"{name} ({f['cast']:,} cast, {f['invalid']:,} null, {f['valid']:,} valid)")
        for position, item in enumerate(circ.lists, 1):
            label_ = party_label(item.party)
            party = key("party", label_) if label_ else None
            option = key("option", "senate", day, code, position)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party,
                ballot_position=position,
                ballot_label=item.members[0].name,
            )
            if item.dossier:
                p.add("external_id", entity_id=option, scheme="ci.cei.dossier", value=item.dossier)
            put(p, result_set, unit, option, VOTES, item.score)
            elected = bool(item.result)
            if elected:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=2)
            else:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="lost")
            for place, member in enumerate(item.members, 1):
                person = key("person", "senate", day, code, position, place)
                p.add("person", id=person)
                p.name(person, member.name, "fr", "official")
                candidacy = key("candidacy", "senate", day, code, position, place)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    list_position=place,
                    ballot_name=member.name,
                    member_of_party_id=party,
                    is_independent=party is None,
                )
                if elected:
                    p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
    if off:
        p.accept(
            "accounting.ballots_sum",
            "the table's ballots cast differ from its null plus valid ballots in "
            + "; ".join(off)
            + "; stored as printed",
        )
    return p


def totals() -> list[str]:
    """Compare the table's printed national row with the sum of its circumscriptions, for the run report."""
    circs = parse_table()
    printed = printed_total()
    sums = {
        k: sum(c.figures[k] for c in circs) for k in ("stations", "registered", "cast", "invalid", "valid", "blank")
    }
    sums["listed"] = sum(item.score for c in circs for item in c.lists)
    diffs = {k: sums[k] - v for k, v in printed.items() if sums[k] != v}
    return [f"{len(circs)} circumscriptions; {'equal' if not diffs else f'differs {diffs}'}"]
