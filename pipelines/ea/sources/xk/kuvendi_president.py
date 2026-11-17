"""Kosovo's presidential elections by the Assembly, from the Assembly's plenary minutes and decisions.

The Assembly of Kosovo publishes the minutes (procesverbal) of each plenary session, and a decision for each election.
The minutes print the result of every secret ballot as the voting commission read it out: the deputies who took part,
the votes for each candidate, and the invalid ballots. A ballot is repeated until a candidate has the required majority,
so an election can have up to three rounds on one day. The minutes of 2002 are a scan and the Assembly's file for 2021
holds only its decision, which names the winner and the round without the votes.
"""

import hashlib
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "xk.kuvendi_president"
KOSOVO = str(uuid5(package.NAMESPACE, "polity\x1fXK"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fKOS"))
SCHEME = "xk.nation"
HOME = "https://www.kuvendikosoves.org/"
OLD = "https://old.kuvendikosoves.org/common/docs/"
FETCHED = "EA_KUVENDI_FETCHED"
VOTES, BALLOTS, VALID, INVALID = 1, 3, 4, 5
FLAG_DERIVED, FLAG_DIGITIZED = 1, 8
_IDS = Package(SOURCE, SOURCE, "ids")


def key(kind: str, *parts: object) -> str:
    return _IDS.id(kind, *parts)


ASSEMBLY = key("source", "kuvendi")
PRESIDENCY = key("office", "president")
SYSTEM = key("electoral_system", "president")
SYSTEM_KK = key("electoral_system", "president_kk")

CONSTITUTION = date(2008, 6, 15)

MONTHS = {
    1: "janar",
    2: "shkurt",
    3: "mars",
    4: "prill",
    5: "maj",
    6: "qershor",
    7: "korrik",
    8: "gusht",
    9: "shtator",
    10: "tetor",
    11: "nëntor",
    12: "dhjetor",
}


@dataclass(frozen=True)
class Count:
    """One printed number: `field` is a candidate's name, or `against`, `abstained`, `invalid`, or `took_part`."""

    field: str
    value: int
    pattern: str  # a regular expression over the page text whose first group is the number


@dataclass(frozen=True)
class Round:
    counts: tuple[Count, ...]

    def get(self, name: str) -> int | None:
        return next((c.value for c in self.counts if c.field == name), None)


@dataclass(frozen=True)
class Election:
    day: date
    last: date
    winner: str
    rounds: tuple[Round, ...]  # empty when the Assembly's file records no votes
    winning_round: int
    minutes: str
    others: tuple[str, ...]
    needles: tuple[str, ...]  # phrases the file must contain, in lower case, once whitespace is collapsed
    note: str = ""
    constitutional: bool = True
    scan: bool = False


def printed(n: int, label: str) -> str:
    return rf"{label}[\s.\-]*[;:]?[\s.\-]*({n})\b"


def took(n: int) -> Count:
    return Count("took_part", n, r"(?:pjesë|votuan)\s+(" + str(n) + r")\s+deputetë")


def rounds_for_against(rows: list[tuple[int, int, int, int]], name: str) -> tuple[Round, ...]:
    return tuple(
        Round(
            (
                took(t),
                Count(name, p, printed(p, "Për") + r","),
                Count("against", a, printed(a, "Kundër") + r","),
                Count("invalid", i, printed(i, "Të pavlefshme") + r"\s*(?:fletëvotime)?"),
            )
        )
        for t, p, a, i in rows
    )


ELECTIONS = [
    Election(
        date(2002, 3, 4),
        date(2002, 3, 4),
        "Ibrahim Rugova",
        (
            Round(
                (
                    Count("Ibrahim Rugova", 88, r"(88)\s*-\s*vota\s*p"),
                    Count("abstained", 15, r"(15)\s*-\s*apstenime"),
                    Count("against", 3, r"(3)\s*-\s*vota\s*kund"),
                )
            ),
        ),
        1,
        OLD + "2002_03_04",
        (),
        ("ibrahim rugova - kryetar i kosov", "kp-nuk votuan"),
        "The Assembly voted once, in the open, on an agreement among the leaders of the LDK, the PDK, and the AAK that "
        "named Ibrahim Rugova President of Kosovo, Bajram Rexhepi Prime Minister, and the ministers, and the minutes "
        "give the result of that vote. The Assembly's Serb coalition did not vote.",
        constitutional=False,
        scan=True,
    ),
    Election(
        date(2006, 2, 10),
        date(2006, 2, 10),
        "Fatmir Sejdiu",
        rounds_for_against([(110, 78, 8, 24), (110, 77, 9, 24), (109, 80, 12, 17)], "Fatmir Sejdiu"),
        3,
        OLD + "2006_02_10",
        (),
        ("votimi duhet të përsëritet", "zgjodhi kryetar të kosovës prof.dr. fatmir sejdiun"),
        "The election followed the death of President Ibrahim Rugova on 21 January 2006. Fatmir Sejdiu was the only "
        "candidate, proposed by the LDK.",
        constitutional=False,
    ),
    Election(
        date(2011, 2, 22),
        date(2011, 2, 22),
        "Behgjet Pacolli",
        rounds_for_against([(67, 54, 11, 2), (67, 58, 7, 2), (67, 62, 4, 1)], "Behgjet Pacolli"),
        3,
        OLD + "proc/proc_s_2011_02_22_15_al.pdf",
        (OLD + "proc/trans_s_2011_02_22_15_al.pdf",),
        ("behgjet pacolli, zgjedhet president",),
        "Behgjet Pacolli was the only candidate. Of the 120 deputies 81 were in the hall at the start and 67 took "
        "part in each ballot. The Constitutional Court annulled this election on 28 March 2011.",
    ),
    Election(
        date(2011, 4, 7),
        date(2011, 4, 7),
        "Atifete Jahjaga",
        (
            Round(
                (
                    took(100),
                    Count("Atifete Jahjaga", 80, r"Për Atifete Jahjagën votuan\s+(80)\s+deputetë"),
                    Count("Suzana Novoberdaliu", 10, r"Për Suzana Novoberdaliun votuan\s+(10)\s+deputetë"),
                    Count("invalid", 10, r"Të pavlefshme ishin\s+(10)\s+fletëvotime"),
                )
            ),
        ),
        1,
        OLD + "proc/proc_s_2011_04_07_18_al.pdf",
        (OLD + "proc/trans_s_2011_04_07_18_al.pdf",),
        ("atifete jahjaga, zgjedhet presidente",),
        "Two candidates were nominated: Atifete Jahjaga, by 41 deputies, and Suzana Novoberdaliu, by 31. The "
        "Vetëvendosje parliamentary group left the hall before the vote.",
    ),
    Election(
        date(2016, 2, 26),
        date(2016, 2, 26),
        "Hashim Thaçi",
        tuple(
            Round(
                (
                    took(81),
                    Count("Hashim Thaçi", t, rf"Për Hashim Thaçin votuan\s+({t})\s+deputetë"),
                    Count("Rafet Rama", r_, rf"Për Rafet Ramën votuan\s+({r_})\s+deputetë"),
                    Count("invalid", i, rf"Të pavlefshme janë\s+({i})\s+fletëvotime"),
                )
            )
            for t, r_, i in [(50, 4, 27), (64, 2, 15), (71, 0, 10)]
        ),
        3,
        OLD + "proc/proc__2016_02_26_11_6334_al.pdf",
        (OLD + "proc/trans__2016_02_26_11_6334_al.pdf",),
        ("hashim thaçi zgjidhet president",),
        "Two candidates were nominated: Hashim Thaçi, by the PDK parliamentary group, and Rafet Rama. 104 deputies "
        "were in the hall at the start, and opposition deputies released tear gas in it during the session. 81 "
        "deputies took part in each ballot.",
    ),
    Election(
        date(2021, 4, 3),
        date(2021, 4, 4),
        "Vjosa Osmani-Sadriu",
        (),
        3,
        HOME
        + "Uploads/Data/SessionFiles/2021_04_06_VendimperzgjedjenePresidentitteRepublikesseKosoves._j2WGNvmSac.pdf",
        (),
        ("vjosa osmani-sadriu zgjidhet presidente",),
        "The decision records that two candidates were nominated in the extraordinary session of 3 and 4 April 2021 "
        "and that Vjosa Osmani-Sadriu was elected in the third secret ballot. The Assembly's file gives no vote "
        "counts.",
    ),
]


def urls(e: Election) -> list[str]:
    return [e.minutes, *e.others]


def store_fetched(folder: Path) -> int:
    """Put the files fetched for the pipeline into the raw cache; `folder` holds them and their `index.json`."""
    index = json.loads((folder / "index.json").read_text())
    wanted = {url for e in ELECTIONS for url in urls(e)}
    stored = 0
    for link, entry in index.items():
        if link not in wanted:
            continue
        content = (folder / entry["file"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError(f"{link} does not match its checksum")
        raw.store(
            SOURCE,
            entry.get("source", link),
            content,
            suffix=Path(link).suffix.lower() or ".pdf",
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )
        stored += 1
    return stored


def fetch() -> None:
    folder = os.environ.get(FETCHED)
    if folder:
        print(f"{store_fetched(Path(folder))} Assembly files stored", flush=True)
        return
    for e in ELECTIONS:
        for url in urls(e):
            raw.fetch(SOURCE, url, suffix=".pdf")


@dataclass
class Page:
    width: float
    height: float
    words: list[tuple[int, int, float, float, float, float]] = field(default_factory=list)


def read(url: str) -> tuple[str, list[tuple[int, int, int]], list[Page]]:
    """The text of a PDF with its words' character spans and boxes, from `pdftotext -bbox`."""
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


def locate(e: Election) -> dict[tuple[int, str], tuple[int, float, float, float, float]]:
    """Each printed count's page (from 1) and normalized box, found in order in the minutes."""
    text, spans, pages = read(e.minutes)
    found: dict[tuple[int, str], tuple[int, float, float, float, float]] = {}
    position = 0
    for index, round_ in enumerate(e.rounds):
        for count in round_.counts:
            match = re.compile(count.pattern, re.IGNORECASE).search(text, position)
            if match is None or int(match[1]) != count.value:
                raise ValueError(f"{e.day} round {index + 1}: the minutes do not print {count.field} {count.value}")
            position = match.end(1)
            at = match.start(1)
            number = next(n for start, end, n in spans if start <= at < end or start > at)
            page = pages[number]
            word = next(w for w in page.words if w[0] <= at < w[1] + 1)
            found[index, count.field] = (
                number + 1,
                word[2] / page.width,
                word[3] / page.height,
                word[4] / page.width,
                word[5] / page.height,
            )
    return found


def whole(url: str) -> str:
    out = subprocess.run(["pdftotext", str(raw.cached(SOURCE, url).path), "-"], check=True, capture_output=True)
    return " ".join(out.stdout.decode("utf-8", "replace").split()).lower()


def check(e: Election) -> None:
    body = whole(e.minutes)
    for needle in e.needles:
        if needle not in body:
            raise ValueError(f"{e.day}: the file does not contain {needle!r}")
    for index, round_ in enumerate(e.rounds, 1):
        votes = sum(c.value for c in round_.counts if c.field not in ("took_part", "invalid"))
        took_part, invalid = round_.get("took_part"), round_.get("invalid")
        if took_part is not None and votes + (invalid or 0) != took_part:
            raise ValueError(f"{e.day} round {index}: the votes and invalid ballots do not add up to {took_part}")
    if e.rounds:
        last = e.rounds[-1]
        ranked = sorted((c.value, c.field) for c in last.counts if c.field not in ("took_part", "invalid", "against"))
        if ranked[-1][1] != e.winner:
            raise ValueError(f"{e.day}: {ranked[-1][1]} leads the last round, not {e.winner}")
    for url in e.others:
        whole(url)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(election_key(e) for e in ELECTIONS)]


def election_key(e: Election) -> str:
    return f"{SOURCE}/{e.last}"


def parse() -> None:
    for e in ELECTIONS:
        check(e)
    registry().write()
    for e in ELECTIONS:
        election(e).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Kosovo's presidency and the electoral systems of its election by the Assembly",
    )
    p.external.add(KOSOVO)
    p.add("scheme", code=SCHEME, polity_id=KOSOVO, kind="administrative", rank=1, noun="nation", code_system=None)
    p.add("office", id=PRESIDENCY, polity_id=KOSOVO, kind="head_of_state")
    p.name(PRESIDENCY, "President of Kosovo", "en", "official")
    p.name(PRESIDENCY, "Presidenti i Kosovës", "sq", "official", False)
    p.add(
        "electoral_system",
        id=SYSTEM_KK,
        polity_id=KOSOVO,
        valid_from=date(2001, 5, 15),
        valid_to=CONSTITUTION,
        rules={
            "formula": "repeated_ballot",
            "majorities": [{"fromRound": 1, "share": 2 / 3}, {"fromRound": 3, "share": 0}],
        },
        rules_version=1,
    )
    p.name(
        SYSTEM_KK,
        "Election of the President of Kosovo by the Assembly under the Constitutional Framework",
        "en",
        "official",
    )
    p.add(
        "note",
        id=key("note", SYSTEM_KK),
        entity_id=SYSTEM_KK,
        kind="methodology",
        body_md="Under the Constitutional Framework for Provisional Self-Government (UNMIK Regulation 2001/9) the "
        "Assembly elected the President of Kosovo for three years by secret ballot, by two thirds of its 120 members "
        "in the first two rounds and a simple majority in the third.",
        lang="en",
    )
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=KOSOVO,
        valid_from=CONSTITUTION,
        rules={
            "formula": "repeated_ballot",
            "majorities": [{"fromRound": 1, "share": 2 / 3}, {"fromRound": 3, "share": 0.5, "strict": True}],
        },
        rules_version=1,
    )
    p.name(SYSTEM, "Election of the President of the Republic by the Assembly", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Under articles 86 and 87 of the Constitution the Assembly elects the President by secret ballot for "
        "five years, on the nomination of at least 30 deputies. The first two rounds need two thirds of all the "
        "Assembly's 120 deputies (80 votes). If no candidate has them, a third round between the two leaders "
        "needs a majority of all deputies (61 votes). If it fails, the Assembly is dissolved.",
        lang="en",
    )
    return p


def label(e: Election) -> str:
    n = sum(1 for x in ELECTIONS if x.day.year == e.day.year)
    return f"{e.day:%B} {e.day.year}" if n > 1 else str(e.day.year)


def election(e: Election) -> Package:
    day = e.last
    p = Package(
        election_key(e),
        SOURCE,
        f"The election of Kosovo's President by the Assembly on {day:%-d %B %Y}, from the Assembly's minutes",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {KOSOVO, COUNTRY}
    p.add(
        "source",
        id=ASSEMBLY,
        kind="official_body",
        publisher="Kuvendi i Republikës së Kosovës",
        title="Minutes of the plenary sessions and decisions of the Assembly of the Republic of Kosovo",
        url=HOME,
        license="No license stated; published by the Assembly for public use",
        attribution="Kuvendi i Republikës së Kosovës",
    )
    artifacts = {url: p.artifact(raw.cached(SOURCE, url), ASSEMBLY) for url in urls(e)}
    stored = raw.cached(SOURCE, e.minutes)
    id = key("election", day)
    path = f"xk/{day}-president"
    p.add("election", id=id, polity_id=KOSOVO, kind="indirect", date_from=e.day, date_to=e.last, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, f"{label(e)} Kosovo presidential election", "en", "official")
    month = MONTHS[day.month] if label(e) != str(day.year) else None
    p.name(
        id,
        f"Zgjedhja e Presidentit të Kosovës, {month + ' ' if month else ''}{day.year}",
        "sq",
        "native",
        False,
    )
    unit = key("unit", day)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=KOSOVO,
        scheme_code=SCHEME,
        code="XK",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    people: dict[str, str] = {}
    boxes = locate(e) if e.rounds else {}
    pages: dict[int, str] = {}
    for number, *_rest in boxes.values():
        if number not in pages:
            pages[number] = key("page", day, number)
            width, height = page_size(stored.path, number)
            p.add(
                "source_page",
                id=pages[number],
                artifact_id=artifacts[e.minutes],
                page_number=number,
                image_key=f"pages/{SOURCE}/{stored.sha256}/{number}.png",
                width=width,
                height=height,
            )
    total = len(e.rounds)
    previous: str | None = None
    rows: list[tuple[str, str, str | None, int, int, int]] = []
    locators: list[dict[str, object]] = []
    for number in range(1, total + 1) if e.rounds else [e.winning_round]:
        index = number - 1 if e.rounds else 0
        round_ = e.rounds[index] if e.rounds else Round(())
        contest = key("contest", day, number)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="office",
            office_id=PRESIDENCY,
            magnitude=1,
            round_number=number,
            previous_round_id=previous,
            electoral_system_id=SYSTEM if e.constitutional else SYSTEM_KK,
            default_result_kind="counted",
        )
        ordinal = {1: "first", 2: "second", 3: "third"}[number]
        multi = len(e.rounds) > 1 or e.winning_round > 1
        p.name(contest, f"{label(e)} presidential election" + (f", {ordinal} round" if multi else ""), "en", "official")
        p.add(
            "slug", path=f"{path}/president" + (f"-round-{number}" if multi else ""), entity_id=contest, is_current=True
        )
        result_set = key("result_set", day, number)
        final = number == e.winning_round
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
            completeness=1.0 if e.rounds else 0.0,
        )
        body = (
            "The result as the Assembly's voting commission read it out in the plenary session, from the minutes. "
            "The Assembly's deputies vote by secret ballot, one ballot paper each; a ballot is for a candidate, "
            "against, or invalid." + (f" {e.note}" if e.note else "")
        )
        if not e.rounds:
            body = e.note
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=body, lang="en")
        options: dict[str, str] = {}
        names = [c.field for c in round_.counts if c.field not in ("took_part", "invalid", "against", "abstained")]
        if not e.rounds:
            names = [e.winner]
        for position, name in enumerate(names, 1):
            option = options[name] = key("option", day, number, name)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=name,
            )
            people[name] = key("person", name)
            p.add(
                "candidacy",
                id=key("candidacy", day, number, name),
                person_id=people[name],
                option_id=option,
                role="head",
                ballot_name=name,
                is_independent=False,
            )
        for extra, text in (("against", "Against"), ("abstained", "Abstention")):
            if round_.get(extra) is not None:
                option = options[extra] = key("option", day, number, extra)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="none_of_the_above",
                    ballot_position=len(names) + (1 if extra == "against" else 2),
                    ballot_label=text,
                )
        if final:
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[e.winner],
                candidacy_id=None,
                kind="elected",
                seats=1,
            )
        printed_votes = {c.field: c.value for c in round_.counts}
        for count in round_.counts:
            if count.field in ("took_part", "invalid"):
                option_id, measure = None, BALLOTS if count.field == "took_part" else INVALID
            else:
                option_id, measure = options[count.field], VOTES
            flag = FLAG_DIGITIZED if e.scan else 0
            rows.append((result_set, unit, option_id, measure, count.value, flag))
            p.add("declared", result_set_id=result_set, option_id=option_id, measure=measure, value=count.value)
            page, x0, y0, x1, y1 = boxes[index, count.field]
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
        if e.rounds:
            valid = sum(v for k, v in printed_votes.items() if k not in ("took_part", "invalid"))
            rows.append((result_set, unit, None, VALID, valid, FLAG_DERIVED))
            if "took_part" not in printed_votes:
                rows.append((result_set, unit, None, BALLOTS, valid + printed_votes.get("invalid", 0), FLAG_DERIVED))
        previous = contest
    for name, person_id in people.items():
        p.add("person", id=person_id)
        p.name(person_id, name, "sq", "official")
    if rows:
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


def page_size(path: Path, number: int) -> tuple[int, int]:
    out = subprocess.run(
        ["pdfinfo", "-f", str(number), "-l", str(number), str(path)], capture_output=True, text=True, check=True
    ).stdout
    found = re.search(rf"Page\s+{number} size:\s+([\d.]+) x ([\d.]+)", out)
    if found is None:
        raise ValueError(f"{path}: no size for page {number}")
    return round(float(found[1])), round(float(found[2]))
