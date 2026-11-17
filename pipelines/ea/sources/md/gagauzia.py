"""Elections of the autonomous territorial unit of Gagauzia: the People's Assembly (Halk Topluşu) and the Governor
(Bashkan).

Two publishers. The Central Electoral Commission of Moldova runs the results portal of the regional elections of 15
November 2026, whose JSON datasets give each of the 35 single-member districts of the People's Assembly and the
autonomy-wide count for the Bashkan. Those are preliminary results, compiled from all 67 polling-station protocols.

Gagauzia's own Central Electoral Commission published the Bashkan election of 30 April and 14 May 2023 only as scanned
protocols: its own autonomy-wide protocol and those of the three district electoral councils (Comrat, Ceadîr-Lunga,
and Vulcănești), each round. Their figures were read from the page images into ``gagauzia.json`` beside this module,
with the page and bounding box of each number, and every district column adds up to the autonomy-wide protocol.
"""

import json
import re
import subprocess
from datetime import UTC, date, datetime
from functools import cache
from pathlib import Path
from typing import Any
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "md.gagauzia"
MOLDOVA = str(uuid5(package.NAMESPACE, "polity\x1fMD"))
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
DIGITIZED = 8
DATA_FILE = Path(__file__).with_name("gagauzia.json")

PORTAL = "https://pvgagauzia.cec.md/rezultate"
DATASET = "https://pvgagauzia-data.cec.md/datasets/{}/dataset.json"
ASSEMBLY_2026, BASHKAN_2026 = "20196", "20198"
MSK = "https://www.mskgagauzia.md/wp-content/uploads/2023/05"
MSK_PAGES = {
    1: "https://www.mskgagauzia.md/protokola-i-spets-blanki-vybory-glavy-gagauzii-30-aprelya-2023-goda/",
    2: "https://www.mskgagauzia.md/protokola-i-spetsblanki-ii-tur/",
}

DAYS = {
    "2023-04-30": date(2023, 4, 30),
    "2023-05-14": date(2023, 5, 14),
    "2026-11-15": date(2026, 11, 15),
}
EARLIER = {"2023-05-14": ["md.gagauzia/2023-04-30"]}

# The three district electoral councils of the Bashkan election, which are Gagauzia's three districts (raioane).
DISTRICTS = {"1": "Comrat", "2": "Ceadîr-Lunga", "3": "Vulcănești"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC_SOURCE = key("source", "cec")
MSK_SOURCE = key("source", "msk")
ASSEMBLY = key("body", "assembly")
BASHKAN = key("office", "bashkan")
SYSTEM = key("electoral_system", "two-round")
REGION = key("unit", "region", "GA")

ASSEMBLY_METHOD = (
    "Preliminary results from the Central Electoral Commission's results portal for the regional elections of 15 "
    "November 2026, compiled from all 67 polling-station protocols, by single-member district. Registered voters are "
    "the voters on the basic lists at the opening of the polls plus those added to the supplementary lists, as the "
    "portal computes turnout; voters are those who received a ballot. The portal gives no count of invalid ballots, so "
    "valid votes are not stored apart from each candidate's votes."
)
BASHKAN_METHOD = (
    "Preliminary results from the Central Electoral Commission's results portal for the regional elections of 15 "
    "November 2026, compiled from all 67 polling-station protocols. The portal gives the autonomy-wide count only. "
    "Registered voters are the voters on the basic lists at the opening of the polls plus those added to the "
    "supplementary lists, as the portal computes turnout; voters are those who received a ballot. The portal gives no "
    "count of invalid ballots."
)
SCAN_METHOD = (
    "Read from the scanned protocols that Gagauzia's Central Electoral Commission published: the three district "
    "electoral councils' protocols (Comrat, Ceadîr-Lunga, and Vulcănești) give the values by district, and the "
    "Commission's own protocol gives the totals stored as declared. Every district column adds up to the Commission's "
    "total. Registered voters are the voters on the basic lists plus those on the supplementary lists; voters are "
    "those who received a ballot; ballots cast are the voters who took part. The polling-station protocols, also "
    "published as scans, are not read here."
)


def dataset_url(election: str) -> str:
    return DATASET.format(election)


@cache
def dataset(election: str) -> dict[str, Any]:
    return json.loads(raw.cached(SOURCE, dataset_url(election)).path.read_text())


@cache
def scans() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text())


def packages() -> list[str]:
    return ["md.gagauzia/registry", *(f"md.gagauzia/{day}" for day in DAYS)]


def fetch() -> None:
    for election in (ASSEMBLY_2026, BASHKAN_2026):
        raw.fetch(SOURCE, dataset_url(election), suffix=".json")
    for count in scans()["rounds"]:
        for protocol in count["protocols"]:
            raw.fetch(SOURCE, protocol["url"], suffix=".pdf")


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def party_key(label: str) -> str:
    text = re.sub(r"[„”“«»\"']", "", label)
    text = re.sub(r"^(partidul politic|политическая партия)\s+", "", clean(text), flags=re.IGNORECASE)
    return text.casefold()


def party_id(label: str) -> str:
    return key("party", party_key(label))


def parties() -> dict[str, tuple[str, str]]:
    """Every party that nominated a candidate, by id, with its printed name and that name's language."""
    found: dict[str, tuple[str, str]] = {}
    for election in (ASSEMBLY_2026, BASHKAN_2026):
        for candidate in _candidates_2026(election):
            if not candidate["isIndependent"]:
                label = clean(candidate["competitorName"])
                found.setdefault(party_id(label), (label, "ro"))
    for count in scans()["rounds"]:
        for candidate in count["candidates"]:
            if candidate.get("party"):
                found.setdefault(party_id(candidate["party"]), (clean(candidate["party"]), "ru"))
    return found


def registry() -> Package:
    p = Package(
        "md.gagauzia/registry",
        SOURCE,
        "Gagauzia's People's Assembly, the office of Bashkan, their electoral system, and the parties that nominated "
        "candidates, from the Central Electoral Commission's results portal and Gagauzia's Central Electoral "
        "Commission",
    )
    p.external.add(MOLDOVA)
    p.add(
        "source",
        id=CEC_SOURCE,
        kind="official_body",
        publisher="Comisia Electorală Centrală a Republicii Moldova",
        title="Results portal of the regional elections in Gagauzia",
        url=PORTAL,
        license="Official publication; reuse terms are not stated",
        attribution="Comisia Electorală Centrală a Republicii Moldova",
    )
    p.add(
        "source",
        id=MSK_SOURCE,
        kind="official_body",
        publisher="Центральная избирательная комиссия Гагаузии",
        title="Scanned protocols of the election of the Bashkan of Gagauzia, 30 April and 14 May 2023",
        url=MSK_PAGES[1],
        license="Official publication; reuse terms are not stated",
        attribution="Центральная избирательная комиссия Гагаузии",
    )
    for election in (ASSEMBLY_2026, BASHKAN_2026):
        p.uses(raw.cached(SOURCE, dataset_url(election)))
    for code, rank, noun in (
        ("md-ga.region", 1, "autonomous territorial unit"),
        ("md-ga.district", 2, "district electoral council"),
        ("md-ga.circumscription", 2, "circumscription"),
    ):
        p.add(
            "scheme",
            code=code,
            polity_id=MOLDOVA,
            kind="electoral_district",
            rank=rank,
            noun=noun,
        )
    p.add(
        "reporting_unit",
        id=REGION,
        polity_id=MOLDOVA,
        scheme_code="md-ga.region",
        code="GA",
        area_relation="unlocated",
        area_id=None,
        kind="standard",
    )
    p.name(REGION, "UTA Găgăuzia", "ro", "official")
    for code, name in DISTRICTS.items():
        unit = key("unit", "district", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=MOLDOVA,
            scheme_code="md-ga.district",
            code=code,
            parent_id=REGION,
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, f"{name} (district electoral council no. {code})", "ro", "official")
    p.add("body", id=ASSEMBLY, polity_id=MOLDOVA, kind="unicameral")
    p.name(ASSEMBLY, "People's Assembly of Gagauzia", "en", "official")
    p.name(ASSEMBLY, "Adunarea Populară a Găgăuziei", "ro", "native", False)
    p.name(ASSEMBLY, "Народное Собрание Гагаузии", "ru", "native", False)
    p.name(ASSEMBLY, "Gagauziyanın Halk Topluşu", "gag", "native", False)
    p.add("office", id=BASHKAN, polity_id=MOLDOVA, kind="governor")
    p.name(BASHKAN, "Governor (Bashkan) of Gagauzia", "en", "official")
    p.name(BASHKAN, "Guvernatorul (Bașcanul) Găgăuziei", "ro", "native", False)
    p.name(BASHKAN, "Глава (Башкан) Гагаузии", "ru", "native", False)
    p.name(BASHKAN, "Gagauziya Başkanı", "gag", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=MOLDOVA,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "Two-round majority voting, for the Bashkan across the whole autonomy and for each of the 35 deputies of "
            "the People's Assembly in a single-member district. A candidate with more than half of the valid votes "
            "is elected in the first round; otherwise the two leading candidates go to a second round two weeks later. "
            "The turnout requirements of Gagauzia's electoral code are not stored in the rules."
        ),
        lang="en",
    )
    for id, (text, lang) in sorted(parties().items(), key=lambda item: item[1]):
        p.add("party", id=id, kind="party", polity_id=MOLDOVA)
        p.name(id, text, lang, "official")
    return p


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int, flags: int = 0) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=flags,
        value=value,
    )


def _election(p: Package, name: str, day: date, kind: str, english: str, native: str, path: str) -> str:
    election = key("election", name)
    p.add("election", id=election, polity_id=MOLDOVA, kind=kind, date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    p.name(election, native, "ro", "native", False)
    return election


def _contest(
    p: Package, name: str, election: str, english: str, native: str, path: str, round_number: int, **fields: object
) -> str:
    contest = key("contest", name)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office" if "office_id" in fields else "body_seats",
        magnitude=1,
        round_number=round_number,
        electoral_system_id=SYSTEM,
        default_result_kind="counted",
        **fields,
    )
    p.name(contest, english, "en", "official")
    p.name(contest, native, "ro", "native", False)
    p.add("slug", path=path, entity_id=contest, is_current=True)
    return contest


def _result_set(
    p: Package, name: str, election: str, contest: str, stage: str, source: str, method: str, as_of: datetime | None
) -> str:
    result_set = key("result_set", name)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage=stage,
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
        as_of=as_of,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    return result_set


def build(day: str) -> Package:
    first = DAYS[day]
    p = Package(
        f"md.gagauzia/{day}",
        SOURCE,
        f"The Gagauzia elections of {first}"
        + (
            " by single-member district, from the Central Electoral Commission's results portal"
            if first.year == 2026
            else " by district electoral council, from Gagauzia's Central Electoral Commission's scanned protocols"
        ),
        depends=["md.gagauzia/registry", *EARLIER.get(day, [])],
    )
    p.external.add(MOLDOVA)
    if first.year == 2026:
        _assembly_2026(p, day, first)
        _bashkan_2026(p, day, first)
    else:
        _bashkan_2023(p, day, first)
    return p


# The 2026 results portal.


def _candidates_2026(election: str) -> list[dict[str, Any]]:
    data = dataset(election)
    if election == BASHKAN_2026:
        return list(data["atLargeCandidates"]["byBallotOrder"])
    return [candidate for district in data["circumscriptii"] for candidate in district["candidates"]]


def _as_of(election: str) -> datetime:
    stamp = dataset(election)["timestamp"]
    return datetime.fromisoformat(stamp).astimezone(UTC)


def _person_name(candidate: dict[str, Any]) -> str:
    return f"{clean(candidate['firstName'])} {clean(candidate['lastName'])}"


def _options_2026(p: Package, name: str, contest: str, candidates: list[dict[str, Any]]) -> dict[int, str]:
    options: dict[int, str] = {}
    for candidate in sorted(candidates, key=lambda c: c["ballotOrder"]):
        position = int(candidate["ballotOrder"])
        person = key("person", "cec", candidate["id"])
        label = _person_name(candidate)
        p.add("person", id=person, gender={"M": "male", "F": "female"}.get(candidate["gender"]))
        p.name(person, label, "ro", "official")
        option = options[candidate["id"]] = key("option", name, position)
        independent = bool(candidate["isIndependent"])
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=label,
        )
        p.add(
            "candidacy",
            id=key("candidacy", name, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=label,
            member_of_party_id=None if independent else party_id(clean(candidate["competitorName"])),
            is_independent=independent,
        )
        if candidate["isWithdrawn"]:
            raise ValueError(f"{label} withdrew; the portal's handling of withdrawals is not modeled")
    return options


def _accounting_2026(row: dict[str, Any]) -> tuple[int, int]:
    """Registered voters and voters, checked against the portal's own sums."""
    if row["ListaBaza"] + row["ListaSuplimentara"] != row["Prezenti"]:
        raise ValueError(f"the portal's voters do not add up in {row['dbName']}")
    return int(row["OpeningVoters"]) + int(row["ListaSuplimentara"]), int(row["Prezenti"])


def _assembly_2026(p: Package, day: str, first: date) -> None:
    p.artifact(raw.cached(SOURCE, dataset_url(ASSEMBLY_2026)), CEC_SOURCE)
    data = dataset(ASSEMBLY_2026)
    if data["pvSummary"]["received"] != data["pvSummary"]["total"]:
        raise ValueError("the portal has not received every protocol")
    election = _election(
        p,
        f"assembly{day}",
        first,
        "general",
        "2026 Gagauzia People's Assembly election",
        "Alegerile Adunării Populare a Găgăuziei din 15 noiembrie 2026",
        f"md/{day}-gagauzia-assembly",
    )
    districts = sorted(data["circumscriptii"], key=lambda d: int(d["number"]))
    if [int(d["number"]) for d in districts] != list(range(1, 36)):
        raise ValueError("the portal does not list districts 1 to 35")
    totals = {"registered": 0, "voters": 0}
    for district in districts:
        number = int(district["number"])
        name = f"assembly{day}-{number}"
        place = clean(district["regionName"]).title()
        contest = _contest(
            p,
            name,
            election,
            f"2026 Gagauzia People's Assembly election, district {number} ({place})",
            f"Alegerile Adunării Populare a Găgăuziei 2026, circumscripția nr. {number} ({place})",
            f"md/{day}-gagauzia-assembly/district-{number}",
            1,
            body_id=ASSEMBLY,
        )
        result_set = _result_set(
            p, name, election, contest, "preliminary", CEC_SOURCE, ASSEMBLY_METHOD, _as_of(ASSEMBLY_2026)
        )
        unit = key("unit", "circumscription", day, number)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=MOLDOVA,
            scheme_code="md-ga.circumscription",
            code=f"{first.year}:{number}",
            parent_id=REGION,
            area_relation="unlocated",
            area_id=None,
            kind="standard",
            valid_from=first,
        )
        p.name(unit, f"{clean(district['dbName'])} ({place})", "ro", "official")
        if not district["isComplete"] or district["receivedStations"] != district["totalStations"]:
            raise ValueError(f"district {number} is not complete on the portal")
        registered, voters = _accounting_2026(district)
        totals["registered"] += registered
        totals["voters"] += voters
        options = _options_2026(p, name, contest, district["candidates"])
        _put(p, result_set, unit, None, REGISTERED, registered)
        _put(p, result_set, unit, None, VOTERS, voters)
        p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=registered)
        p.add("declared", result_set_id=result_set, option_id=None, measure=VOTERS, value=voters)
        for candidate in district["candidates"]:
            _put(p, result_set, unit, options[candidate["id"]], VOTES, int(candidate["votes"]))
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=options[candidate["id"]],
                measure=VOTES,
                value=int(candidate["votes"]),
            )
        _pending(p, result_set)
    expected = _accounting_2026(data["totals"] | {"dbName": "the autonomy"})
    if (totals["registered"], totals["voters"]) != expected:
        raise ValueError(f"the districts add up to {totals}, the portal's totals are {expected}")


def _bashkan_2026(p: Package, day: str, first: date) -> None:
    p.artifact(raw.cached(SOURCE, dataset_url(BASHKAN_2026)), CEC_SOURCE)
    data = dataset(BASHKAN_2026)
    if data["pvSummary"]["received"] != data["pvSummary"]["total"]:
        raise ValueError("the portal has not received every protocol")
    election = _election(
        p,
        f"bashkan{day}",
        first,
        "general",
        "2026 Gagauzia Bashkan election",
        "Alegerile Bașcanului Găgăuziei din 15 noiembrie 2026",
        f"md/{day}-gagauzia-bashkan",
    )
    name = f"bashkan{day}-1"
    contest = _contest(
        p,
        name,
        election,
        "2026 Gagauzia Bashkan election, first round",
        "Alegerile Bașcanului Găgăuziei 2026, turul I",
        f"md/{day}-gagauzia-bashkan/bashkan",
        1,
        office_id=BASHKAN,
    )
    result_set = _result_set(
        p, name, election, contest, "preliminary", CEC_SOURCE, BASHKAN_METHOD, _as_of(BASHKAN_2026)
    )
    [whole] = data["circumscriptii"]
    registered, voters = _accounting_2026(whole)
    if (registered, voters) != _accounting_2026(data["totals"] | {"dbName": "the autonomy"}):
        raise ValueError("the autonomy's row differs from the portal's totals")
    candidates = _candidates_2026(BASHKAN_2026)
    options = _options_2026(p, name, contest, candidates)
    _put(p, result_set, REGION, None, REGISTERED, registered)
    _put(p, result_set, REGION, None, VOTERS, voters)
    p.add("declared", result_set_id=result_set, option_id=None, measure=REGISTERED, value=registered)
    p.add("declared", result_set_id=result_set, option_id=None, measure=VOTERS, value=voters)
    for candidate in candidates:
        _put(p, result_set, REGION, options[candidate["id"]], VOTES, int(candidate["votes"]))
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=options[candidate["id"]],
            measure=VOTES,
            value=int(candidate["votes"]),
        )
    _pending(p, result_set)


def _pending(p: Package, result_set: str) -> None:
    p.add(
        "note",
        id=key("note", result_set, "pending"),
        entity_id=result_set,
        kind="caveat",
        body_md="Preliminary results. No outcome is stored until the electoral authorities declare who was elected and "
        "who goes to the second round of 29 November 2026.",
        lang="en",
    )


# The 2023 scanned protocols.


def _sizes(path: Path) -> dict[int, tuple[float, float]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "99", str(path)], capture_output=True, text=True, check=True)
    found = (re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line) for line in out.stdout.splitlines())
    return {int(m[1]): (float(m[2]), float(m[3])) for m in found if m}


def _pages(p: Package, url: str) -> dict[int, str]:
    """Cite one scanned protocol and register its pages; boxes are in pixels of the page rendered at 100 dpi."""
    stored = raw.cached(SOURCE, url)
    artifact = p.artifact(stored, MSK_SOURCE)
    pages: dict[int, str] = {}
    for number, (width, height) in sorted(_sizes(stored.path).items()):
        pages[number] = p.id("page", stored.sha256, number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
            width=round(width * 100 / 72),
            height=round(height * 100 / 72),
        )
    return pages


def _locate(
    p: Package,
    result_set: str,
    unit: str,
    option: str | None,
    measure: int,
    pages: dict[int, str],
    box: list[float],
) -> None:
    page, x0, y0, x1, y1 = box
    p.add(
        "value_locator",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        page_id=pages[int(page)],
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
    )


def _union(*boxes: list[float]) -> list[float]:
    if len({box[0] for box in boxes}) != 1:
        raise ValueError("boxes on different pages")
    return [
        boxes[0][0],
        min(b[1] for b in boxes),
        min(b[2] for b in boxes),
        max(b[3] for b in boxes),
        max(b[4] for b in boxes),
    ]


def _check(protocol: dict[str, Any], candidates: int) -> None:
    """The protocol's own formulas: c ≤ a+b, ç = e+f, d = c−ç, f = sum of the candidates, g = c+h."""
    v = protocol["values"]
    votes = protocol["votes"]
    if len(votes) != candidates:
        raise ValueError(f"{protocol['url']} has {len(votes)} candidates, not {candidates}")
    if not (
        v["c"] <= v["a"] + v["b"]
        and v["ç"] == v["e"] + v["f"]
        and v["d"] == v["c"] - v["ç"]
        and v["f"] == sum(votes)
        and v["g"] == v["c"] + v["h"]
    ):
        raise ValueError(f"the protocol {protocol['url']} breaks its own formulas")


def _bashkan_2023(p: Package, day: str, first: date) -> None:
    number = 1 if day == "2023-04-30" else 2
    count = next(r for r in scans()["rounds"] if r["round"] == number)
    runoff = number == 2
    election = _election(
        p,
        f"bashkan{day}",
        first,
        "runoff" if runoff else "general",
        "2023 Gagauzia Bashkan election" + (", second round" if runoff else ""),
        "Alegerile Bașcanului Găgăuziei din 30 aprilie 2023" + (", turul II" if runoff else ""),
        f"md/{day}-gagauzia-bashkan" + ("-runoff" if runoff else ""),
    )
    name = f"bashkan2023-{number}"
    contest = _contest(
        p,
        name,
        election,
        f"2023 Gagauzia Bashkan election, {'second' if runoff else 'first'} round",
        f"Alegerile Bașcanului Găgăuziei 2023, turul {'II' if runoff else 'I'}",
        f"md/{day}-gagauzia-bashkan{'-runoff' if runoff else ''}/bashkan",
        number,
        office_id=BASHKAN,
        previous_round_id=key("contest", "bashkan2023-1") if runoff else None,
    )
    result_set = _result_set(p, name, election, contest, "official", MSK_SOURCE, SCAN_METHOD, None)
    candidates = count["candidates"]
    options: list[str] = []
    for position, candidate in enumerate(candidates, 1):
        person = key("person", "msk", candidate["name"])
        if not runoff:
            p.add("person", id=person)
            p.name(person, candidate["name"], "ru", "official")
            p.name(person, candidate["latin"], "gag", "official", False)
        option = key("option", name, position)
        options.append(option)
        party = candidate.get("party")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=candidate["name"],
        )
        p.add(
            "candidacy",
            id=key("candidacy", name, position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate["name"],
            member_of_party_id=party_id(party) if party else None,
            is_independent=candidate.get("independent"),
        )
    protocols = {protocol["unit"]: protocol for protocol in count["protocols"]}
    whole = protocols["GA"]
    for protocol in protocols.values():
        _check(protocol, len(candidates))
    summed = {field: sum(protocols[code]["values"][field] for code in DISTRICTS) for field in whole["values"]}
    if summed != whole["values"]:
        raise ValueError(f"the district protocols add up to {summed}, the Commission's to {whole['values']}")
    for position in range(len(candidates)):
        if sum(protocols[code]["votes"][position] for code in DISTRICTS) != whole["votes"][position]:
            raise ValueError(f"the district votes for {candidates[position]['name']} differ from the total")
    rows = ((REGISTERED, ("a", "b")), (VOTERS, ("c",)), (CAST, ("ç",)), (INVALID, ("e",)), (VALID, ("f",)))
    for code in DISTRICTS:
        protocol = protocols[code]
        pages = _pages(p, protocol["url"])
        unit = key("unit", "district", code)
        for measure, fields in rows:
            value = sum(protocol["values"][field] for field in fields)
            _put(p, result_set, unit, None, measure, value, DIGITIZED)
            _locate(p, result_set, unit, None, measure, pages, _union(*(protocol["boxes"][f] for f in fields)))
        for position, option in enumerate(options):
            _put(p, result_set, unit, option, VOTES, protocol["votes"][position], DIGITIZED)
            _locate(p, result_set, unit, option, VOTES, pages, protocol["vote_boxes"][position])
    p.artifact(raw.cached(SOURCE, whole["url"]), MSK_SOURCE)
    for measure, fields in rows:
        value = sum(whole["values"][field] for field in fields)
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for position, option in enumerate(options):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=whole["votes"][position])
    for position, option in enumerate(options):
        kind = candidates[position]["outcome"]
        p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
    p.add(
        "note",
        id=key("note", result_set, "outcomes"),
        entity_id=result_set,
        kind="methodology",
        body_md="The outcomes are as Gagauzia's Central Electoral Commission declared them in its protocol on the "
        + ("second" if runoff else "first")
        + f" round ({whole['url']}, published at {MSK_PAGES[number]}).",
        lang="en",
    )
