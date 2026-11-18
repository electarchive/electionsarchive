"""Sweden's Sámi Parliament (Sametinget) elections of 2005 to 2025, from Valmyndigheten's published results.

The Sametinget has 31 members, chosen every four years by voters on the Sámi electoral roll. The whole country is one
constituency and seats go to party lists, so the authority reports one national count and no finer unit. The
authority publishes the results in four forms: a summary of every election since 2005 (party shares and seats), the
historical presentations of 2013, 2017, and 2021 (party votes, ballots, and seats), and the results service for the
ordinary election of 18 May 2025, which the Election Review Board annulled, and its re-run of 5 October 2025.
The 2009 votes come from the comparison columns of the 2013 presentation. The 2005 election has only shares.
"""

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "se.val_sameting"
SWEDEN = str(uuid5(package.NAMESPACE, "polity\x1fSE"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fSWE"))
SITE = "https://www.val.se/valresultat-och-statistik/sametingsval"
HISTORY = "https://historik.val.se/val"
RESULTS = "https://resultat.val.se/data/resultat"
SUMMARY = f"{SITE}/tidigare-valresultat"
PAGE_2025 = f"{SITE}/valresultat-2025"
JSON_2025_MAY = f"{RESULTS}/same2025/S_S.json"
JSON_2025_OCT = f"{RESULTS}/omvalsame2025/S_S.json"
CSV_2025_OCT = f"{RESULTS}/omvalsame2025/csv/S_S.csv"
SEATS = 31
VOTES, REGISTERED, CAST, VALID, INVALID, VOTE_SHARE = 1, 2, 3, 4, 5, 13
SHARES = 4
LICENSE = (
    "Public records of the Swedish Election Authority (Valmyndigheten), published as open data for free reuse with "
    "the source acknowledged"
)

PARTIES = {
    "AAAF": "Albmut - Almetjh - Almasj - Folket",
    "G": "Guovssonásti",
    "JF": "Jakt- och Fiskesamerna",
    "LSS": "Landspartiet Svenska Samer",
    "MG": "Mijjen Geajnoe, Mijjan Geäjdnuo, Mijá Gäjnno, Min Geaidnu",
    "NSR": "Nya Samiska Reformpartiet",
    "SAM": "Samerna",
    "SF": "Samiska Folkomröstningspartiet",
    "SOL": "Solidaritet",
    "SP": "Sámiid Riikkabellodat Samelandspartiet",
    "ViS": "Vi Samer",
    "VS": "Vuovdega-Skogssamerna",
}
ABBREVIATIONS = {"SÁR": "SP"}
LATER_NAMES = {"SAM": ("Partiet Samerna",)}
NO_PARTY = {"ÖVR", "VDT", "OG", "BLANK"}

METHOD = (
    "The authority's national count for the Sámi Parliament election. The whole country is one constituency, and the "
    "Norrbotten County Administrative Board counts all ballots centrally, so the count is stored in one national "
    "reporting unit. Registered voters are the Sámi electoral roll, ballots cast are the voters who voted, and valid "
    "votes are the votes for the parties. Each voter votes for one party list and may also mark one candidate."
)
SYSTEM_METHOD = (
    "The Sámi Parliament has 31 seats, filled from party lists in one national constituency. There is no "
    "threshold and no adjustment seat. The rule is stored as the modified Sainte-Laguë method with a first divisor of "
    "1.4, which reproduces the declared seats of every election from 2005 to 2025."
)
ANNULLED = (
    "The ordinary election of 18 May 2025 was annulled, because the Election Review Board set aside its result, "
    "and it was held again on 5 October 2025. The authority's results service still shows the count of 18 May as the "
    "result of the annulled election."
)


@dataclass
class Result:
    day: date
    registered: int | None = None
    cast: int | None = None
    valid: int | None = None
    invalid: int | None = None
    votes: dict[str, int] = field(default_factory=dict)
    shares: dict[str, Decimal] = field(default_factory=dict)
    seats: dict[str, int] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    members: list[tuple[str, int, int, str]] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)


DAYS = {
    "2005": date(2005, 5, 15),
    "2009": date(2009, 5, 17),
    "2013": date(2013, 5, 19),
    "2017": date(2017, 5, 21),
    "2021": date(2021, 5, 16),
    "2025-05": date(2025, 5, 18),
    "2025-10": date(2025, 10, 5),
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def party(code: str) -> str:
    return key("party", code)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{tag}" for tag in DAYS)]


def page(year: int, name: str) -> str:
    return f"{HISTORY}/same{year}/{name}.html"


def urls() -> list[str]:
    return [
        SUMMARY,
        PAGE_2025,
        *(page(year, name) for year in (2013, 2017, 2021) for name in ("roster", "valda")),
        JSON_2025_MAY,
        JSON_2025_OCT,
        CSV_2025_OCT,
    ]


def fetch() -> None:
    for url in urls():
        suffix = ".json" if url.endswith(".json") else ".csv" if url.endswith(".csv") else ".html"
        raw.fetch(SOURCE, url, suffix=suffix)


def read(url: str) -> str:
    return raw.cached(SOURCE, url).path.read_text("utf-8")


def text(fragment: str) -> str:
    plain = html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ")
    return " ".join(plain.split())


def whole(value: str) -> int | None:
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def percent(value: str) -> Decimal | None:
    found = re.search(r"\d+(?:,\d+)?", value)
    return Decimal(found.group().replace(",", ".")) if found else None


def rows(fragment: str) -> list[list[str]]:
    return [
        [text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)]
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", fragment, re.DOTALL)
    ]


def code(abbreviation: str) -> str:
    return ABBREVIATIONS.get(abbreviation, abbreviation)


def presentation(year: int, column: int, result: Result) -> None:
    """The votes and ballots of one election from a historical presentation; column 2 is its own year, 6 the previous."""
    result.urls.append(page(year, "roster"))
    invalid: list[int] = []
    started = False
    for cells in rows(read(page(year, "roster"))):
        if cells[:1] == ["Förk."]:
            started = True
        if not started or len(cells) <= column or cells[0] == "Förk.":
            continue
        abbreviation, label, value = cells[0], cells[1], whole(cells[column])
        if label == "Giltiga röster":
            result.valid = value
        elif label.startswith("Ogiltiga röster"):
            invalid += [value] if value is not None else []
        elif abbreviation == "VDT":
            result.cast = value
        elif label == "Antal röstberättigade":
            result.registered = value
        elif abbreviation and abbreviation not in NO_PARTY and value is not None:
            result.votes[code(abbreviation)] = value
            result.labels[code(abbreviation)] = label
            result.shares[code(abbreviation)] = percent(cells[column + 1]) or Decimal(0)
    result.invalid = sum(invalid)


def mandates(year: int, column: int, result: Result) -> None:
    result.urls.append(page(year, "valda"))
    for cells in rows(read(page(year, "valda"))):
        if len(cells) > column and cells[0] and cells[0] not in {"Förk.", "Totalt"}:
            result.seats[code(cells[0])] = whole(cells[column]) or 0


def summary(year: int, result: Result) -> None:
    """The authority's summary of an election: party shares and seats, the electors, and the voters."""
    result.urls.append(SUMMARY)
    sections = re.split(r"<h2[^>]*>Valresultat (\d{4})</h2>", read(SUMMARY))
    section = dict(zip(sections[1::2], sections[2::2], strict=True))[str(year)]
    names = {v: k for k, v in PARTIES.items()}
    names |= {n: k for k, ns in LATER_NAMES.items() for n in ns}
    names["Min Geaidnu Mijá Gäjnno Mijjen Geajnoe"] = "MG"
    shares: dict[str, Decimal] = {}
    seats: dict[str, int] = {}
    for cells in rows(section):
        if len(cells) == 3 and cells[0] in names:
            shares[names[cells[0]]] = percent(cells[1]) or Decimal(0)
            seats[names[cells[0]]] = int(cells[2])
    items = re.findall(r"<li>(.*?)</li>", section, re.DOTALL)
    for item in map(text, items):
        if item.startswith("antalet röstberättigade"):
            result.registered = whole(item)
        elif item.startswith("valdeltagandet"):
            result.cast = whole(item.split("(")[1])
    if year == 2005:
        result.shares, result.seats = shares, seats
        result.labels = {c: PARTIES[c] for c in shares}
        return
    for c, seat in seats.items():
        if result.seats.get(c, 0) != seat:
            raise ValueError(
                f"{year}: {c} has {result.seats.get(c)} seats in the presentation and {seat} in the summary"
            )
    for c, share in shares.items():
        places = -int(str(share.as_tuple().exponent))
        if abs(Decimal(result.votes[c]) * 100 / Decimal(result.valid or 1) - share) > Decimal(1).scaleb(-places):
            raise ValueError(f"{year}: {c} share {share} disagrees with the votes")


def historical(year: int) -> Result:
    day = DAYS[str(year)]
    result = Result(day)
    if year == 2009:
        presentation(2013, 6, result)
        mandates(2013, 4, result)
    else:
        presentation(year, 2, result)
        mandates(year, 2, result)
    summary(year, result)
    return result


def ordinary_2005() -> Result:
    result = Result(DAYS["2005"])
    summary(2005, result)
    return result


def json_2025(url: str, day: date) -> Result:
    data = json.loads(read(url))
    result = Result(day, urls=[url, PAGE_2025])
    result.registered = whole(data["antalRostberattigade"])
    result.cast = whole(data["totaltAntalRoster"])
    paying = data["rosterPaverkaMandat"]
    result.valid = int(paying["antalRoster"])
    result.invalid = int(data["rosterEjPaverkaMandat"]["antalRoster"])
    for row in paying["partiroster"]:
        if row["partikod"] is None:
            if row["antalRoster"]:
                raise ValueError("votes for unnamed parties")
            continue
        c = code(row["partiforkortning"])
        result.votes[c] = int(row["antalRoster"])
        result.shares[c] = Decimal(str(row["andelRoster"]))
        result.labels[c] = row["partibeteckning"]
    for row in data["partiMandat"]:
        result.seats[code(row["partiforkortning"])] = int(row["antalMandat"])
    number = 0
    if "ledamoterPerParti" in data:
        for row in data["ledamoterPerParti"]:
            for member in row["ledamoter"]:
                number += 1
                name = member["namn"].removesuffix(" (personvald)")
                result.members.append(
                    (code(row["partiforkortning"]), member["kandidatnummer"], member["ordning"], name)
                )
    else:
        order: dict[str, int] = {}
        for row in data["ledamoterPostLista"]:
            c = code(row["partiforkortning"])
            for member in row["ledamotList"]:
                order[c] = order.get(c, 0) + 1
                name = member["namn"].removesuffix(" (personvald)")
                result.members.append((c, member["kandidatnummer"], order[c], name))
    return result


def check_csv(result: Result) -> None:
    """The re-run's CSV export must hold the same electors and party votes as its JSON."""
    seen: dict[str, int] = {}
    electors = None
    for line in read(CSV_2025_OCT).lstrip("﻿").splitlines()[1:]:
        cells = [c.strip('"') for c in line.split(";")]
        if cells[1] == "Röstberättigade":
            electors = int(cells[5])
        elif cells[1] == "Partiröster":
            seen[
                next(c for c, n in PARTIES.items() if n == cells[3] or (c == "SAM" and cells[3] == "Partiet Samerna"))
            ] = int(cells[5])
    result.urls.append(CSV_2025_OCT)
    if electors != result.registered or seen != result.votes:
        raise ValueError(f"the CSV disagrees with the JSON: {electors} {seen} {result.votes}")


def results() -> dict[str, Result]:
    out = {"2005": ordinary_2005()}
    for year in (2009, 2013, 2017, 2021):
        out[str(year)] = historical(year)
    out["2025-05"] = json_2025(JSON_2025_MAY, DAYS["2025-05"])
    out["2025-10"] = json_2025(JSON_2025_OCT, DAYS["2025-10"])
    check_csv(out["2025-10"])
    return out


def allocate(votes: dict[str, int], seats: int = SEATS) -> dict[str, int]:
    """The modified Sainte-Laguë allocation, used only to check the declared seats."""
    won = {c: 0 for c in votes}
    for _ in range(seats):
        best = max(votes, key=lambda c: votes[c] / (1.4 if won[c] == 0 else 2 * won[c] + 1))
        won[best] += 1
    return won


def check(tag: str, r: Result) -> None:
    if sum(r.seats.values()) != SEATS:
        raise ValueError(f"{tag}: {sum(r.seats.values())} seats")
    if r.votes:
        if sum(r.votes.values()) != r.valid:
            raise ValueError(f"{tag}: party votes {sum(r.votes.values())} are not the valid votes {r.valid}")
        if r.valid is not None and r.invalid is not None and r.valid + r.invalid != r.cast:
            raise ValueError(f"{tag}: valid and invalid votes do not add up to the ballots cast")
        if {c: n for c, n in allocate(r.votes).items() if n} != {c: n for c, n in r.seats.items() if n}:
            raise ValueError(f"{tag}: the declared seats differ from the allocation")
    else:
        weights = {c: int(s * 100) for c, s in r.shares.items()}
        if {c: n for c, n in allocate(weights).items() if n} != {c: n for c, n in r.seats.items() if n}:
            raise ValueError(f"{tag}: the declared seats differ from the allocation of the shares")
    if r.members and len(r.members) != SEATS:
        raise ValueError(f"{tag}: {len(r.members)} members")


def parse() -> None:
    built = results()
    for tag, r in built.items():
        check(tag, r)
    registry(built).write()
    for tag, r in built.items():
        election(tag, r).write()


def emit() -> None:
    package.run("load", packages())


def registry(built: dict[str, Result]) -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "The Sámi Parliament of Sweden, its parties, and its electoral system")
    p.external.update({SWEDEN, COUNTRY})
    body = key("body", "sametinget")
    p.add("body", id=body, polity_id=SWEDEN, kind="unicameral")
    p.name(body, "Sametinget", "sv", "official")
    p.name(body, "Sámi Parliament of Sweden", "en", "english", False)
    p.name(body, "Sámediggi", "se", "native", False)
    p.add(
        "electoral_system",
        id=key("electoral_system", "sametinget"),
        polity_id=SWEDEN,
        valid_from=date(2005, 1, 1),
        rules={"formula": "modified_sainte_lague", "firstDivisor": 1.4},
        rules_version=1,
    )
    system = key("electoral_system", "sametinget")
    p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_METHOD, lang="en")
    p.add(
        "scheme",
        code="se.sameting",
        polity_id=SWEDEN,
        kind="electoral_district",
        rank=1,
        noun="constituency",
        code_system=None,
    )
    p.add(
        "reporting_unit",
        id=key("unit", "national"),
        polity_id=SWEDEN,
        scheme_code="se.sameting",
        code="SE",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    used = {c for r in built.values() for c in r.labels}
    for c in sorted(used):
        id = party(c)
        p.add("party", id=id, kind="party", polity_id=SWEDEN)
        p.add("external_id", entity_id=id, scheme="se.val_sameting.party", value=c)
        names = [PARTIES[c], *LATER_NAMES.get(c, ())]
        for label in {r.labels[c] for r in built.values() if c in r.labels} | set(names):
            if label != names[-1]:
                p.name(id, label, "sv", "historical", False)
        p.name(id, names[-1], "sv", "official")
        p.name(id, c, "sv", "abbreviation", False)
    return p


def election(tag: str, r: Result) -> Package:
    year = r.day.year
    p = Package(
        f"{SOURCE}/{tag}",
        SOURCE,
        f"The {r.day:%-d %B %Y} Sámi Parliament election of Sweden: the national count by party and the declared seats",
        depends=[f"{SOURCE}/registry", *([f"{SOURCE}/2025-05"] if tag == "2025-10" else [])],
    )
    p.external.update({SWEDEN, COUNTRY})
    source = key("source", tag)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Valmyndigheten",
        title=f"Valresultat i sametingsval {tag}",
        url=r.urls[0],
        license=LICENSE,
        license_url="https://www.val.se/valresultat-och-statistik/statistik-och-data/om-var-oppna-data",
        attribution="Valmyndigheten",
    )
    for url in dict.fromkeys(r.urls):
        p.artifact(raw.cached(SOURCE, url), source)
    annulled = tag == "2025-05"
    id = key("election", tag)
    p.add(
        "election",
        id=id,
        polity_id=SWEDEN,
        kind="general",
        date_from=r.day,
        date_to=r.day,
        date_precision="day",
        status="annulled" if annulled else "held",
        rerun_of_id=key("election", "2025-05") if tag == "2025-10" else None,
    )
    title = f"{year} Sámi Parliament election of Sweden" + (", re-run" if tag == "2025-10" else "")
    p.name(id, title, "en", "official")
    p.name(id, f"Val till Sametinget {year}" + (" (omval)" if tag == "2025-10" else ""), "sv", "native", False)
    base = f"se/{r.day}-sami-parliament"
    p.add("slug", path=base, entity_id=id, is_current=True)
    if annulled:
        p.add("note", id=key("note", id, "annulled"), entity_id=id, kind="caveat", body_md=ANNULLED, lang="en")
    contest = key("contest", tag)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        body_id=key("body", "sametinget"),
        magnitude=SEATS,
        round_number=1,
        electoral_system_id=key("electoral_system", "sametinget"),
        default_result_kind="counted",
    )
    p.name(contest, f"Val till Sametinget {year}", "sv", "official")
    p.name(contest, title, "en", "english", False)
    p.add("slug", path=f"{base}/sametinget", entity_id=contest, is_current=True)
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="certified",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    shares_only = not r.votes
    extra = ""
    if shares_only:
        extra = (
            " For this election the authority prints only each party's share of the vote, the seats, the electors, "
            "and the voters, so party votes and valid and invalid ballots are not stored; the shares are stored as "
            "printed."
        )
    if tag == "2009":
        extra = (
            " The authority published no presentation of this election. Its party votes and ballots are the "
            "comparison columns of the presentation of the 2013 election, and its seats are those of the 2013 "
            "presentation's comparison column."
        )
    if tag == "2013":
        extra = (
            " The presentation of the 2017 election prints 8,322 electors and a turnout of 54.43% for 2013; the "
            "2013 presentation, stored here, prints 8,327 and 54.40%."
        )
    if tag in {"2025-05", "2025-10"}:
        extra = (
            " Values come from the results service's final count (JSON), and the re-run's CSV export holds the same "
            "electors and party votes. The elected members are those the count names; personal votes and "
            "substitutes are not stored."
        )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD + extra,
        lang="en",
    )
    if annulled:
        p.add(
            "note",
            id=key("note", result_set, "annulled"),
            entity_id=result_set,
            kind="caveat",
            body_md=ANNULLED,
            lang="en",
        )
    unit = key("unit", "national")
    options = {c: key("option", tag, c) for c in r.labels}
    for position, c in enumerate(
        sorted(r.labels, key=lambda c: (-(r.votes.get(c) or 0), -r.shares.get(c, Decimal(0)))), 1
    ):
        p.add(
            "contest_option",
            id=options[c],
            contest_id=contest,
            kind="list",
            party_id=party(c),
            ballot_position=position,
            ballot_label=r.labels[c],
        )
        if r.seats.get(c):
            p.add("outcome", result_set_id=result_set, option_id=options[c], kind="won", seats=r.seats[c])
        elif r.seats.get(c) == 0:
            p.add("outcome", result_set_id=result_set, option_id=options[c], kind="lost", seats=0)
    for c, number, order, name in r.members:
        person = key("person", tag, number)
        if not any(row["id"] == person for row in p.rows.get("person", [])):
            p.add("person", id=person, birth_precision=None)
            p.name(person, name, "sv", "official")
        candidacy = key("candidacy", tag, number)
        p.add(
            "candidacy",
            id=candidacy,
            person_id=person,
            option_id=options[c],
            role="list_member",
            list_position=order,
            ballot_name=name,
            is_independent=False,
        )
        p.add("outcome", result_set_id=result_set, option_id=options[c], candidacy_id=candidacy, kind="elected")
    values: list[tuple[str | None, int, int | Decimal, int]] = []
    for measure, value in ((REGISTERED, r.registered), (CAST, r.cast), (VALID, r.valid), (INVALID, r.invalid)):
        if value is not None:
            values.append((None, measure, value, 0))
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for c, n in r.votes.items():
        values.append((options[c], VOTES, n, 0))
        p.add("declared", result_set_id=result_set, option_id=options[c], measure=VOTES, value=n)
    if shares_only:
        values += [(options[c], VOTE_SHARE, share, SHARES) for c, share in r.shares.items()]
    p.frame(
        "result_value",
        pl.DataFrame(
            [(o, m, str(v), f) for o, m, v, f in values],
            schema=["option_id", "measure", "value", "flags"],
            orient="row",
        ).with_columns(
            result_set_id=pl.lit(result_set),
            unit_id=pl.lit(unit),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.col("flags").cast(pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p
