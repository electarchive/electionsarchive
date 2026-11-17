"""Danish national referendums 1920-2000 by nomination district, from Statistics Denmark's and the Ministry of the
Interior's printed volumes."""

import functools
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "dk.dst_hist"
SE = "https://www.dst.dk/Site/Dst/Udgivelser/GetSEPdf.aspx?id="
IM = "https://cdn2.gopublic.dk/indenrigssundhedsminvalg/media/"
DENMARK = str(uuid5(package.NAMESPACE, "polity\x1fDK"))
SCHEME = "dk.opstillingskreds"
REGISTERED, VOTES, CAST, VALID, INVALID = 2, 1, 3, 4, 5

FILES = {
    "1920": SE + "8214",
    "1939": SE + "8994",
    "1953": SE + "9873",
    "1961": "https://www.dst.dk/extranet/bibscan/da/mono/folkeafstemning_1961.pdf",
    "1963": SE + "10645",
    "1969": "https://www.dst.dk/extranet/bibscan/da/mono/folkeafstemning_1969_a.pdf",
    "1971": "https://www.dst.dk/Site/Dst/Udgivelser/GetPubFile.aspx?id=20207&sid=valg1971",
    "1972": SE + "11338",
    "1978": SE + "12041",
    "1986": "https://dst.dk/seudg/25498",
    "1992": "https://dst.dk/seudg/25497",
    "1993": "https://dst.dk/seudg/25499",
    "1998": IM + "18834/amsterdamtraktaten.pdf",
    "2000": IM
    + "6921/statistik-over-folkeafstemningen-den-28-september-2000-om-danmarks-deltagelse-i-den-faelles-valuta.pdf",
}


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


@functools.cache
def text(volume: str) -> str:
    """The layout text of one printed volume (needs poppler's pdftotext)."""
    result = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, FILES[volume]).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


COUNT = re.compile(r"^\d{1,3}(?:\.\d{3})+$|^\d+$")
GROUP = re.compile(
    r"^\s*(\D*?(?:Storkreds|Amtskreds|HELE LANDET|Hovedstaden|Øerne|Jylland|København-Frederiksberg)\S*)\s{2,}(\S.*)$"
)
ROW = re.compile(r"^\s*(\d{1,2})\.\s+([^\d].*?)\s{2,}(\S.*)$")


def integer(token: str) -> int | None:
    return int(token.replace(".", "")) if COUNT.match(token) else None


def block(volume: str, first: str, last: str) -> list[str]:
    lines = text(volume).splitlines()
    begin = next(i for i, line in enumerate(lines) if re.match(first, line))
    stop = next(i for i, line in enumerate(lines) if i > begin and re.match(last, line))
    return lines[begin:stop]


@dataclass
class Unit:
    group: str
    number: str
    name: str
    registered: int | None
    valid: int
    answers: list[tuple[int, int]]  # (yes, no) per question
    cast: int | None = None
    invalid: int | None = None


@dataclass
class Question:
    code: str
    english: str
    danish: str


@dataclass
class Vote:
    volume: str
    day: date
    english: str
    danish: str
    questions: list[Question]
    units: list[Unit]
    totals: dict[str, int]  # the printed national figures: registered, valid, and yes/no per question
    pages: str = ""


def modern(volume: str, votes: tuple[str, str], ballots: tuple[str, str]) -> tuple[list[Unit], dict[str, int]]:
    """Tables of registered, turnout, valid, yes, no, then percentages; and of registered, cast, letter votes, invalid."""
    group = ""
    units: list[Unit] = []
    totals: dict[str, int] = {}
    for line in block(volume, *votes):
        found = GROUP.match(line)
        if found:
            group = found.group(1).strip()
            if group == "HELE LANDET":
                tokens = found.group(2).split()
                totals = {
                    "registered": integer(tokens[0]) or 0,
                    "valid": integer(tokens[2]) or 0,
                    "yes0": integer(tokens[3]) or 0,
                    "no0": integer(tokens[4]) or 0,
                }
            continue
        found = ROW.match(line)
        if found:
            tokens = found.group(3).split()
            values = [integer(tokens[i]) for i in (0, 2, 3, 4)]
            units.append(
                Unit(
                    group,
                    found.group(1),
                    found.group(2).strip(),
                    values[0],
                    values[1] or 0,
                    [(values[2] or 0, values[3] or 0)],
                )
            )
    by_name = {(u.group, u.number, u.name): u for u in units}
    group = ""
    for line in block(volume, *ballots):
        found = GROUP.match(line)
        if found:
            group = found.group(1).strip()
            if group == "HELE LANDET":
                tokens = found.group(2).split()
                totals["cast"] = integer(tokens[1]) or 0
                totals["invalid"] = integer(tokens[3]) or 0
            continue
        found = ROW.match(line)
        if found:
            tokens = found.group(3).split()
            unit = by_name[(group, found.group(1), found.group(2).strip())]
            if integer(tokens[0]) != unit.registered or integer(tokens[9]) != unit.valid:
                raise ValueError(f"{volume} {unit.name}: the two tables disagree on registered or valid voters")
            unit.cast = integer(tokens[1])
            unit.invalid = integer(tokens[3])
    return units, totals


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SYSTEM = key("electoral_system", "referendum")
SOURCE_DST = key("source", "dst")
SOURCE_IM = key("source", "im")
METHOD = (
    "Counted per nomination district (opstillingskreds) and printed in the official volume cited, which holds the "
    "result the election authority declared. Registered voters are the voters entitled to vote, valid votes are the "
    "valid ballots, and yes and no are the valid ballots for and against the question. Figures for a district are "
    "taken from the volume's table of nomination districts; the table's printed subtotals for each county or "
    "metropolitan constituency and for the whole country were used to check them."
)


def volume_source(volume: str) -> str:
    return SOURCE_IM if volume in ("1998", "2000") else SOURCE_DST


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Sources and the referendum electoral system for Danish national referendums before 2009",
    )
    p.external.update({DENMARK, SCHEME})
    p.add(
        "source",
        id=SOURCE_DST,
        kind="official_body",
        publisher="Danmarks Statistik",
        title="Statistiske Efterretninger and Statistiske Meddelelser: referendum volumes",
        url="https://www.dst.dk/da/Statistik/emner/borgere/demokrati/folkeafstemninger",
        license="Statistics Denmark data may be reused with attribution to Statistics Denmark as the source",
        attribution="Danmarks Statistik",
    )
    p.add(
        "source",
        id=SOURCE_IM,
        kind="official_body",
        publisher="Indenrigsministeriet",
        title="Tidligere folkeafstemninger",
        url="https://www.valg.im.dk/noegletal/folkeafstemninger/tidligere-folkeafstemninger",
        license="Published by the Danish Ministry of the Interior; reused with attribution to the ministry as the source",
        attribution="Indenrigsministeriet",
    )
    p.add("electoral_system", id=SYSTEM, polity_id=DENMARK, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="A yes or no question put to all voters.",
        lang="en",
    )
    return p


def election(vote: Vote) -> Package:
    p = Package(
        f"{SOURCE}/{vote.day}",
        SOURCE,
        f"The {vote.day} Danish referendum by nomination district",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update({DENMARK, SCHEME})
    source = volume_source(vote.volume)
    p.artifact(raw.cached(SOURCE, FILES[vote.volume]), source)
    id = key("election", vote.day)
    path = f"dk/{vote.day}-referendum"
    p.add("election", id=id, polity_id=DENMARK, kind="referendum", date_from=vote.day, date_to=vote.day, status="held")
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, vote.english, "en", "official")
    p.name(id, vote.danish, "da", "native", False)
    units: dict[int, str] = {}
    for index, unit in enumerate(vote.units):
        uid = key("unit", vote.day, unit.group, unit.number, unit.name)
        units[index] = uid
        p.add(
            "reporting_unit",
            id=uid,
            polity_id=DENMARK,
            scheme_code=SCHEME,
            code=f"{vote.day}:{unit.group}:{unit.number}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(uid, unit.name, "da", "official")
    for index, question in enumerate(vote.questions):
        contest = key("contest", vote.day, question.code)
        p.add(
            "contest",
            id=contest,
            election_id=id,
            kind="ballot_question",
            electoral_system_id=SYSTEM,
            round_number=1,
            default_result_kind="counted",
            magnitude=None,
        )
        p.name(contest, question.english, "en", "official")
        p.name(contest, question.danish, "da", "native", False)
        p.add("slug", path=f"{path}/{question.code}", entity_id=contest, is_current=True)
        p.add("ballot_question", contest_id=contest, type="referendum")
        result_set = key("result_set", vote.day, question.code)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        options = {}
        for position, (code, label) in enumerate((("yes", "Ja"), ("no", "Nej")), 1):
            options[code] = key("option", vote.day, question.code, code)
            p.add(
                "contest_option",
                id=options[code],
                contest_id=contest,
                kind="answer",
                parent_option_id=None,
                party_id=None,
                ballot_position=None,
                ballot_label=label,
            )
        for position, unit in enumerate(vote.units):
            uid = units[position]
            yes, no = unit.answers[index]
            put(p, result_set, uid, None, VALID, yes + no)
            if unit.registered is not None:
                put(p, result_set, uid, None, REGISTERED, unit.registered)
            if unit.cast is not None and unit.invalid is not None:
                put(p, result_set, uid, None, CAST, unit.cast)
                put(p, result_set, uid, None, INVALID, unit.invalid)
            put(p, result_set, uid, options["yes"], VOTES, yes)
            put(p, result_set, uid, options["no"], VOTES, no)
        for code, measure, value in (
            (None, VALID, vote.totals[f"yes{index}"] + vote.totals[f"no{index}"]),
            (None, REGISTERED, vote.totals.get("registered")),
            (None, CAST, vote.totals.get("cast")),
            (None, INVALID, vote.totals.get("invalid")),
            ("yes", VOTES, vote.totals[f"yes{index}"]),
            ("no", VOTES, vote.totals[f"no{index}"]),
        ):
            if value is not None:
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=options[code] if code else None,
                    measure=measure,
                    value=value,
                )
    return p


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


MODERN = {
    "1998": ((r"\s*Tabel 8 \(Side 1", r"\s*Tabel 9\. "), (r"\s*Tabel 10 \(Side 1", r"\s*Tabel 11\. ")),
    "2000": ((r"\s*Tabel 8 \(Side 1", r"\s*Tabel 9"), (r"\s*Tabel 10 \(Side 1", r"\s*Tabel 11\. ")),
}
TITLES = {
    "1998": (
        date(1998, 5, 28),
        "1998 referendum on the Amsterdam Treaty",
        "Folkeafstemningen den 28. maj 1998 om Danmarks tiltrædelse af Amsterdamtraktaten",
    ),
    "2000": (
        date(2000, 9, 28),
        "2000 referendum on Danish participation in the euro",
        "Folkeafstemningen den 28. september 2000 om Danmarks deltagelse i den fælles valuta",
    ),
}


def votes() -> list[Vote]:
    found: list[Vote] = []
    for volume, (votes_table, ballots_table) in MODERN.items():
        units, totals = modern(volume, votes_table, ballots_table)
        day, english, danish = TITLES[volume]
        check(volume, units, totals)
        found.append(Vote(volume, day, english, danish, [Question("question", english, danish)], units, totals))
    return found


def check(volume: str, units: list[Unit], totals: dict[str, int]) -> None:
    """Fail unless the districts add up to each other and to the printed national figures."""
    for unit in units:
        for yes, no in unit.answers:
            if yes + no != unit.valid:
                raise ValueError(f"{volume} {unit.name}: yes {yes} and no {no} do not make valid {unit.valid}")
    for index in range(len(units[0].answers)):
        for measure, value in (
            (f"yes{index}", sum(u.answers[index][0] for u in units)),
            (f"no{index}", sum(u.answers[index][1] for u in units)),
        ):
            if value != totals[measure]:
                raise ValueError(f"{volume} {measure}: districts add to {value}, the volume prints {totals[measure]}")
    for measure, value in (
        ("cast", sum(u.cast or 0 for u in units)),
        ("invalid", sum(u.invalid or 0 for u in units)),
    ):
        if measure in totals and value != totals[measure]:
            raise ValueError(f"{volume} {measure}: districts add to {value}, the volume prints {totals[measure]}")
    registered = sum(u.registered or 0 for u in units)
    if "registered" in totals and registered != totals["registered"]:
        raise ValueError(
            f"{volume} registered: districts add to {registered}, the volume prints {totals['registered']}"
        )


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{v.day}" for v in votes())]


def parse() -> None:
    registry().write()
    for vote in votes():
        election(vote).write()


def emit() -> None:
    package.run("load", packages())
