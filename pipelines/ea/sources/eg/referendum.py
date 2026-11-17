"""Egypt's constitutional referendums of 2014 and 2019, nationally, from the electoral authorities' result decrees.

The Official Gazette prints the decree that announces each result. The High Elections Committee's decree 7 of 2014
announced the result of the referendum of 14 and 15 January 2014, and the National Election Authority's decree 38 of
2019 announced the result of the referendum of April 2019, which ran on 19 to 21 April abroad and 20 to 22 April in
Egypt. Both decrees are reprinted in the front matter of the Constitution that the Committee's site publishes, and each
gives the electorate, the voters, the valid and invalid ballots, and the Yes and No votes for the whole country,
Egyptians abroad included. The authorities' governorate tables, which the 2012 and 2014 referendum sites loaded from an
application that is no longer online, and the 2012 result, are not built.
"""

import re
import subprocess
import unicodedata
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "eg.referendum"
EGYPT = str(uuid5(package.NAMESPACE, "polity\x1fEG"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fEGY"))
CONSTITUTION = "https://www.elections.eg/images/pdfs/laws/Constitution_2014.pdf"
HOME = "https://www.elections.eg/"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
REGISTRY = f"{SOURCE}/registry"
SCHEME = "eg.referendum_national"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_HEC = key("source", "hec-decree-7-2014")
SOURCE_NEA = key("source", "nea-decree-38-2019")
SYSTEM = key("electoral_system", "constitutional-referendum")

# Each referendum: decree source, day of the first and last vote, the decree's words that open its figures, and names.
REFERENDUMS = {
    2014: {
        "source": SOURCE_HEC,
        "first": date(2014, 1, 14),
        "last": date(2014, 1, 15),
        "marker": "قرار اللجنة العليا لالنتخابات رقم 7لسنة 2014",
        "english": "2014 Egyptian constitutional referendum",
        "arabic": "الاستفتاء على الدستور 2014",
        "question": "Do you approve the draft constitutional amendments to the Constitution of 2012, which became the "
        "Constitution of 2014? (The decree announces the result of the referendum on the draft amendments.)",
    },
    2019: {
        "source": SOURCE_NEA,
        "first": date(2019, 4, 19),
        "last": date(2019, 4, 22),
        "marker": "قرار الهيئة الوطنية لالنتخابات رقم 38لسنة 2019",
        "english": "2019 Egyptian constitutional referendum",
        "arabic": "الاستفتاء على تعديل بعض مواد الدستور 2019",
        "question": "Do you approve the amendment of certain articles of the Constitution? Voting abroad ran on 19 to "
        "21 April 2019 and in Egypt on 20 to 22 April.",
    },
}


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{year}" for year in REFERENDUMS)]


def fetch() -> None:
    raw.fetch(SOURCE, CONSTITUTION, suffix=".pdf")


def parse() -> None:
    registry().write()
    for year in REFERENDUMS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def text() -> str:
    """The first pages of the PDF as logical-order Arabic, without bidi marks, tatweel, or runs of space."""
    path = raw.cached(SOURCE, CONSTITUTION).path
    out = subprocess.run(["pdftotext", "-layout", "-l", "6", str(path), "-"], check=True, capture_output=True)
    body = unicodedata.normalize("NFKC", out.stdout.decode("utf-8"))
    body = re.sub("[\u202a-\u202e\u0640]", "", body)
    return re.sub(r"\s+", " ", body)


def count(number: str) -> int:
    return int(re.sub(r"\D", "", number))


PATTERNS = {
    "registered": r"جملة الناخبين المدعوي?ين لالستفتاء :? ?([\d.]{5,})",
    "ballots": r"جملة الناخبين الذين أدلوا بأصواتهم :? ?([\d.]{5,})",
    "valid": r"جملة األصوات الصحيحة :? ?([\d.]{5,})",
    "invalid": r"جملة األصوات الباطلة :? ?([\d.]{5,})",
    "yes": r"جملة المصوتين (?:بنعم|بالموافقة) :? ?([\d.]{5,})",
    "no": r"جملة المصوتين (?:بال|بعدم الموافقة) :? ?([\d.]{5,})",
}


def declared(year: int) -> dict[str, int]:
    """The figures of one decree, read from the PDF's text layer and checked against each other."""
    body = text()
    start = body.index(str(REFERENDUMS[year]["marker"]))
    chunk = body[start : body.index("المادة الثانية", body.index("جملة المصوتين", start))]
    found: dict[str, int] = {}
    for name, pattern in PATTERNS.items():
        match = re.search(pattern, chunk)
        if match is None:
            raise ValueError(f"{year}: no {name} in the decree: {chunk!r}")
        found[name] = count(match.group(1))
    if found["yes"] + found["no"] != found["valid"] or found["valid"] + found["invalid"] != found["ballots"]:
        raise ValueError(f"{year}: the decree's figures do not add up: {found}")
    return found


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Egypt's constitutional referendums of 2014 and 2019: the decrees they come from and the electoral system",
    )
    p.external.add(EGYPT)
    for id, publisher, title in (
        (
            SOURCE_HEC,
            "High Elections Committee of Egypt",
            (
                "Decree 7 of 2014 announcing the result of the referendum on the draft constitutional amendments "
                "(Al-Waqa'i' al-Misriyya, issue 14 bis, 19 January 2014)"
            ),
        ),
        (
            SOURCE_NEA,
            "National Election Authority of Egypt",
            (
                "Decree 38 of 2019 announcing the result of the referendum on amending certain articles of the "
                "Constitution (Official Gazette, issue 16 bis (f), 23 April 2019)"
            ),
        ),
    ):
        p.add(
            "source",
            id=id,
            kind="official_body",
            publisher=publisher,
            title=title,
            url=CONSTITUTION,
            license="No license stated; an official decree published by the electoral authority for public use",
            attribution=publisher,
        )
        p.artifact(raw.cached(SOURCE, CONSTITUTION), id)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=EGYPT,
        rules={"formula": "referendum"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="A constitutional referendum: the proposal is approved when more than half of the valid votes say Yes.",
        lang="en",
    )
    p.add("scheme", code=SCHEME, polity_id=EGYPT, kind="precinct", rank=1, noun="national count", code_system=None)
    return p


def election(year: int) -> Package:
    info = REFERENDUMS[year]
    figures = declared(year)
    source = str(info["source"])
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Egyptian constitutional referendum nationally, from the authority's result decree",
        depends=[REGISTRY],
    )
    p.external |= {EGYPT, COUNTRY}
    p.uses(raw.cached(SOURCE, CONSTITUTION))
    first, last = info["first"], info["last"]
    assert isinstance(first, date) and isinstance(last, date)
    election_id = key("election", year)
    p.add(
        "election",
        id=election_id,
        polity_id=EGYPT,
        kind="referendum",
        date_from=first,
        date_to=last,
        date_precision="day",
        status="held",
    )
    path = f"eg/{first}-referendum"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.name(election_id, str(info["english"]), "en", "official")
    p.name(election_id, str(info["arabic"]), "ar", "native", False)
    contest = key("contest", year)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="ballot_question",
        electoral_system_id=SYSTEM,
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, str(info["english"]), "en", "official")
    p.add("slug", path=f"{path}/question", entity_id=contest, is_current=True)
    p.add(
        "note",
        id=key("note", contest, "question"),
        entity_id=contest,
        kind="context",
        body_md=str(info["question"]),
        lang="en",
    )
    options = {}
    for position, (name, english, arabic) in enumerate((("yes", "Yes", "نعم"), ("no", "No", "لا")), start=1):
        option = options[name] = key("option", year, name)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer",
            ballot_position=position,
            ballot_label=english,
        )
        p.name(option, english, "en", "official")
        p.name(option, arabic, "ar", "native", False)
    result_set = key("result_set", year)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
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
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md="The national totals of the authority's result decree, which count Egyptians abroad. The decree gives "
        "no figures for governorates or committees, so the result is stored as one national unit.",
        lang="en",
    )
    unit = key("unit", year)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=EGYPT,
        scheme_code=SCHEME,
        code=str(year),
        area_relation="within",
        area_id=COUNTRY,
        kind="aggregate",
    )
    p.name(unit, "Egypt, including voters abroad", "en", "official")
    p.add("outcome", result_set_id=result_set, option_id=options["yes"], kind="won")
    p.add("outcome", result_set_id=result_set, option_id=options["no"], kind="lost")
    rows = (
        (REGISTERED, None, figures["registered"]),
        (BALLOTS, None, figures["ballots"]),
        (VALID, None, figures["valid"]),
        (INVALID, None, figures["invalid"]),
        (VOTES, options["yes"], figures["yes"]),
        (VOTES, options["no"], figures["no"]),
    )
    for measure, option, value in rows:
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)
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
    return p
