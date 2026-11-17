"""Iceland's Constitutional Assembly election of 27 November 2010: the first preferences of the electronic count."""

import html
import re
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "is.stjornlagathing"
BASE = "http://landskjor.is/stjornlagathing/talning/Final_DryRun1-"
INFO_URL = f"{BASE}0.html"
FIRST_URL = f"https://web.archive.org/web/20110301023745/{BASE}1.html"
PAGE = "https://www.landskjor.is/stjornlagathing/talning/"
ICELAND = str(uuid5(package.NAMESPACE, "polity\x1fIS"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fISL"))
DAY = date(2010, 11, 27)
EKEY = "constitutional-assembly-2010-11-27"
SCHEME = "is-sl.landid"
SYSTEM_RULES = {"formula": "stv", "quota": "droop", "surplus": "weighted_inclusive_gregory"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


STATE = key("source", "landskjorstjorn")
BODY = key("body", "constitutional-assembly")
SYSTEM = key("electoral_system", "constitutional-assembly")
PACKAGE = f"{SOURCE}/{EKEY}"
ELECTION = key("election", EKEY)
CONTEST = key("contest", EKEY)
NOTE = (
    "The National Electoral Commission published the electronic count of the election in stages. This result set "
    "holds the first stage, the first preference each candidate received, as the report prints it. The valid "
    "ballots are the sum of the first preferences. The later stages, which transfer the surplus of elected "
    "candidates and the votes of excluded ones, and so name the rest of the 25 members elected, are not stored "
    "here. The only candidate the first stage marks elected, because the candidate's first preferences reached "
    "the quota, is recorded as elected."
)
SYSTEM_NOTE = (
    "Voters rank up to 25 candidates, with no parties or lists. The 25 seats are filled by the single transferable "
    "vote, counted by the weighted inclusive Gregory method (WIG) in the eSTV software, with the Droop quota."
)


# ---------------------------------------------------------------- fetch


def fetch() -> None:
    raw.fetch(SOURCE, INFO_URL, suffix=".html")
    raw.fetch(SOURCE, FIRST_URL, suffix=".html")


# ---------------------------------------------------------------- parse


def text_of(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text(encoding="utf8")


def cell(page: str, label: str) -> str:
    found = re.search(rf"<td>{label}</td>\s*<td colspan=\"2\">([^<]*)", page)
    if found is None:
        raise LookupError(f"the count report has no row {label!r}")
    return html.unescape(found.group(1)).strip()


def first_stage(page: str) -> tuple[list[tuple[int, str, str, int, bool]], int]:
    """The candidates of stage 1 as (ballot number, name, gender, first preferences, marked elected), and the
    printed total."""
    start = page.index("Frambjóðendur")
    rows = re.findall(
        r'<td class="head">([^<]*)</td>\s*<td( class="elected")?>\s*<strong>([\d.]+)</strong>', page[start:]
    )
    candidates: list[tuple[int, str, str, int, bool]] = []
    for label, elected, amount in rows:
        if label == "Ófæranlegt":
            continue
        found = re.fullmatch(r"(\d+) (.+) - ([MF])", html.unescape(label).strip())
        if found is None:
            raise ValueError(f"unreadable candidate {label!r}")
        whole, _, fraction = amount.partition(".")
        if fraction.strip("0"):
            raise ValueError(f"{label}: a first preference of {amount} is not a whole number")
        candidates.append(
            (int(found.group(1)), " ".join(found.group(2).split()), found.group(3), int(whole), bool(elected))
        )
    total = re.search(r"<td>Samtals</td>\s*<td>(\d+)</td>", page[start:])
    non = re.search(r'<td class="head">Ófæranlegt</td>\s*<td>\s*<strong>([\d.]+)</strong>', page[start:])
    if total is None or non is None:
        raise LookupError("the first stage prints no total")
    if float(non.group(1)) != 0:
        raise ValueError("the first stage has non-transferable votes")
    return candidates, int(total.group(1))


# ---------------------------------------------------------------- packages


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The national scheme under which the Constitutional Assembly election of 2010 reports its count",
    )
    p.external |= {ICELAND, TERRITORY}
    p.add("scheme", code=SCHEME, polity_id=ICELAND, kind="administrative", rank=0, noun="country")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Constitutional Assembly of Iceland, its electoral system, and the National Electoral Commission as a "
        "source",
    )
    p.external.add(ICELAND)
    p.add(
        "source",
        id=STATE,
        kind="official_body",
        publisher="Landskjörstjórn (National Electoral Commission of Iceland)",
        title="Stjórnlagaþing: talning atkvæða (count of votes for the Constitutional Assembly)",
        url=PAGE,
        license="Public reports of the National Electoral Commission",
        license_url="https://www.landskjor.is/",
        attribution="Landskjörstjórn, landskjor.is",
    )
    p.add("body", id=BODY, polity_id=ICELAND, kind="constituent_assembly")
    p.name(BODY, "Constitutional Assembly", "en", "official")
    p.name(BODY, "Stjórnlagaþing", "is", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=ICELAND,
        valid_from=DAY,
        valid_to=date(2010, 12, 31),
        rules=SYSTEM_RULES,
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    return p


def election() -> Package:
    p = Package(
        PACKAGE,
        SOURCE,
        "The Constitutional Assembly election of 27 November 2010, with each candidate's first preferences from the "
        "electronic count of the National Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {ICELAND, TERRITORY}
    info = text_of(INFO_URL)
    seats = int(cell(info, "Fjöldi sæta"))
    valid = int(cell(info, "Gild atkvæði"))
    candidates, total = first_stage(text_of(FIRST_URL))
    if total != valid or sum(c[3] for c in candidates) != valid:
        raise ValueError(f"the first preferences add up to {sum(c[3] for c in candidates)}, not {valid}")
    if len(candidates) != 522 or seats != 25:
        raise ValueError(f"{len(candidates)} candidates for {seats} seats")
    if len({c[0] for c in candidates}) != len(candidates):
        raise ValueError("a ballot number repeats")
    p.artifact(raw.cached(SOURCE, INFO_URL), STATE)
    p.artifact(raw.cached(SOURCE, FIRST_URL), STATE)
    p.add(
        "election",
        id=ELECTION,
        polity_id=ICELAND,
        kind="special",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        original_text="27. nóvember 2010",
        status="held",
    )
    p.name(ELECTION, "2010 Icelandic Constitutional Assembly election", "en", "official")
    p.name(ELECTION, "Kosning til stjórnlagaþings 2010", "is", "native", False)
    slug = "is/2010-11-27-constitutional-assembly"
    p.add("slug", path=slug, entity_id=ELECTION, is_current=True)
    p.add(
        "contest",
        id=CONTEST,
        election_id=ELECTION,
        kind="body_seats",
        body_id=BODY,
        district_area_id=TERRITORY,
        magnitude=seats,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
    )
    p.name(CONTEST, "2010 Icelandic Constitutional Assembly election", "en", "official")
    p.name(CONTEST, "Stjórnlagaþing 2010", "is", "native", False)
    p.add("slug", path=f"{slug}/{changeset.slug('Constitutional Assembly')}", entity_id=CONTEST, is_current=True)
    result_set = key("result_set", EKEY)
    p.add(
        "result_set",
        id=result_set,
        election_id=ELECTION,
        contest_id=CONTEST,
        stage="official",
        kind="counted",
        source_id=STATE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=NOTE, lang="en")
    unit = key("unit", EKEY, "national")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ICELAND,
        scheme_code=SCHEME,
        code="IS",
        area_relation="equals",
        area_id=TERRITORY,
        kind="standard",
    )

    def value(option: str | None, measure: int, amount: int) -> None:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=amount,
        )
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=amount)

    value(None, 4, valid)
    for number, name, gender, votes, elected in candidates:
        option = key("option", EKEY, number)
        p.add(
            "contest_option",
            id=option,
            contest_id=CONTEST,
            kind="candidate",
            ballot_position=number,
            ballot_label=name,
        )
        person = key("person", EKEY, number)
        p.add("person", id=person, gender={"M": "male", "F": "female"}[gender])
        p.name(person, name, "is", "official")
        p.add(
            "candidacy",
            id=key("candidacy", EKEY, number),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
            is_independent=True,
        )
        value(option, 1, votes)
        if elected:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
    return p


# ---------------------------------------------------------------- run


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", PACKAGE]


def parse() -> None:
    geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())
