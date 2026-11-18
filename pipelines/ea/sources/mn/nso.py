"""Mongolia's presidential elections of 1993 to 2009, national candidate totals, from the National Statistics Office.

The Office's table DT_NSO_0200_001V1 gives, for each election year, the number of voters who took part and each
candidate's votes, for the whole country. The election days come from the National Great Khural's resolutions that
called each election, published on legalinfo.mn. The presidency and its electoral system are the ones `mn.gec`
holds; the table's own parties are source-local.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.mn import gec

SOURCE = "mn.nso"
MONGOLIA = gec.MONGOLIA
TERRITORY = gec.TERRITORY
SCHEME = "mn.nso_country"
TABLE = "https://data.1212.mn/api/v1/mn/NSO/Society,%20development/Election/DT_NSO_0200_001V1.px"
PAGE = "https://data.1212.mn/mn/NSO/Society,%20development/Election/DT_NSO_0200_001V1.px"
QUERY: dict[str, object] = {
    "query": [
        {"code": "Нэр дэвшигчид", "selection": {"filter": "all", "values": ["*"]}},
        {"code": "Он", "selection": {"filter": "all", "values": ["*"]}},
    ],
    "response": {"format": "json"},
}
LAW = "https://legalinfo.mn/mn/detail?lawId="
# The resolution that set each election day, by year: (law id, election day).
RESOLUTIONS = {
    1993: (6115, date(1993, 6, 6)),
    1997: (0, date(1997, 5, 18)),
    2001: (6116, date(2001, 5, 20)),
    2005: (6118, date(2005, 5, 22)),
    2009: (6182, date(2009, 5, 24)),
}
BALLOTS_CAST = 3
VOTES = 1
METHOD = (
    "The National Statistics Office's table of presidential election results by election year, which it takes from "
    "the General Election Commission. For each election it gives the number of voters who took part, taken here as "
    "the ballots cast, and each candidate's votes for the whole country. The table has no breakdown of the ballots "
    "that were invalid or blank, and no registered voters, so the candidates' votes fall short of the ballots cast "
    "by the ballots it does not credit to a candidate. Only the national totals are stored."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NSO_SOURCE = key("source", "nso")


@dataclass
class Entry:
    name: str
    party: str | None
    votes: int


@dataclass
class Year:
    year: int
    cast: int
    entries: list[Entry]


def url(year: int) -> str:
    return f"{LAW}{RESOLUTIONS[year][0]}"


def fetch() -> None:
    raw.fetch(SOURCE, TABLE, suffix=".json")
    raw.fetch(SOURCE, TABLE, suffix=".json", body=QUERY)
    for year, (law, _) in RESOLUTIONS.items():
        if law:
            raw.fetch(SOURCE, url(year), suffix=".html")


def table() -> dict[int, Year]:
    data = json.loads(raw.cached(SOURCE, TABLE, QUERY).path.read_bytes())
    assert [c["code"] for c in data["columns"]][:2] == ["Нэр дэвшигчид", "Он"]
    meta = json.loads(raw.cached(SOURCE, TABLE).path.read_bytes())
    variables = {v["code"]: v for v in meta["variables"]}
    texts = dict(zip(variables["Нэр дэвшигчид"]["values"], variables["Нэр дэвшигчид"]["valueTexts"], strict=True))
    years = dict(zip(variables["Он"]["values"], (int(t) for t in variables["Он"]["valueTexts"]), strict=True))
    cells: dict[tuple[str, int], int] = {}
    for row in data["data"]:
        (cell,) = row["values"]
        if cell.strip("."):
            cells[row["key"][0], years[row["key"][1]]] = int(cell)
    out: dict[int, Year] = {}
    header: str | None = None
    for code, label in texts.items():
        if "оны" in label and re.search(r"\d{4}", label):
            header = code
            continue
        for (found, year), votes in cells.items():
            if found != code or year not in RESOLUTIONS:
                continue
            assert header is not None and (header, year) in cells, (label, year)
            held = out.setdefault(year, Year(year, cells[header, year], []))
            name, _, party = label.partition("/")
            held.entries.append(
                Entry(re.sub(r"\s+", " ", name).strip(), re.sub(r"\s+", " ", party).strip() or None, votes)
            )
    return out


def party(text: str) -> str:
    return key("party", text)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/president-{year}" for year in RESOLUTIONS)]


def parse() -> None:
    registry().write()
    for year in RESOLUTIONS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The National Statistics Office's election table, its candidates' parties, and its national reporting unit",
    )
    p.external |= {MONGOLIA, TERRITORY}
    p.add(
        "source",
        id=NSO_SOURCE,
        kind="official_body",
        publisher="Үндэсний статистикийн хороо",
        title="Results of the presidential elections of Mongolia, by election year (DT_NSO_0200_001V1)",
        url=PAGE,
        attribution="National Statistics Office of Mongolia",
    )
    p.artifact(raw.cached(SOURCE, TABLE, QUERY), NSO_SOURCE)
    for year, (law, _) in RESOLUTIONS.items():
        if law:
            p.add(
                "source",
                id=key("source", "resolution", year),
                kind="official_body",
                publisher="Монгол Улсын Их Хурал",
                title=f"Resolution of the State Great Khural calling the {year} presidential election",
                url=url(year),
                attribution="legalinfo.mn",
            )
            p.artifact(raw.cached(SOURCE, url(year)), key("source", "resolution", year))
    p.add("scheme", code=SCHEME, polity_id=MONGOLIA, kind="administrative", rank=1, noun="country", code_system=None)
    names = sorted({e.party for y in table().values() for e in y.entries if e.party})
    for text in names:
        id = party(text)
        p.add("party", id=id, kind="bloc" if "," in text or "эвсэл" in text.lower() else "party", polity_id=MONGOLIA)
        p.name(id, text, "mn", "official")
    return p


def election(year: int) -> Package:
    held = table()[year]
    law, day = RESOLUTIONS[year]
    p = Package(
        f"{SOURCE}/president-{year}",
        SOURCE,
        f"The {year} Mongolian presidential election, national totals, from the National Statistics Office",
        depends=[f"{SOURCE}/registry", f"{gec.SOURCE}/registry"],
    )
    p.external |= {MONGOLIA, TERRITORY}
    p.uses(raw.cached(SOURCE, TABLE, QUERY))
    if law:
        p.uses(raw.cached(SOURCE, url(year)))
    election_id = key("election", year)
    if not law:
        p.add(
            "note",
            id=key("note", election_id, "date"),
            entity_id=election_id,
            kind="caveat",
            body_md=(
                "The election day, 18 May 1997, is not cited to the State Great Khural's resolution that called the "
                "election, which legalinfo.mn does not publish in a form this pipeline could find. The National "
                "Statistics Office's table gives the year only."
            ),
            lang="en",
        )
    p.add("election", id=election_id, polity_id=MONGOLIA, kind="general", date_from=day, date_to=day, status="held")
    english = f"{year} Mongolian presidential election"
    p.name(election_id, english, "en", "official")
    p.name(election_id, f"Монгол Улсын Ерөнхийлөгчийн {year} оны сонгууль", "mn", "native", False)
    path = f"mn/{day}-presidential"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    contest = key("contest", year)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=gec.PRESIDENCY,
        district_area_id=TERRITORY,
        magnitude=1,
        round_number=1,
        electoral_system_id=gec.PRESIDENT_SYSTEM,
        default_result_kind="counted",
    )
    p.name(contest, english, "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", year)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=NSO_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    leader = max(range(len(held.entries)), key=lambda i: held.entries[i].votes)
    unit = key("unit", year)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=MONGOLIA,
        scheme_code=SCHEME,
        code=f"{year}:MN",
        area_relation="equals",
        area_id=TERRITORY,
        kind="standard",
    )
    p.name(unit, "Mongolia", "en", "official")
    for index, entry in enumerate(held.entries):
        option = key("option", year, index)
        person = key("person", year, index)
        p.add("person", id=person)
        p.name(person, entry.name, "mn", "official")
        label = entry.name + (f" ({entry.party})" if entry.party else "")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=party(entry.party) if entry.party else None,
            ballot_position=index + 1,
            ballot_label=label,
        )
        p.add(
            "candidacy",
            id=key("candidacy", year, index),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=entry.name,
            member_of_party_id=party(entry.party) if entry.party else None,
            is_independent=False,
        )
        p.add(
            "outcome", result_set_id=result_set, option_id=option, kind="elected" if index == leader else "eliminated"
        )
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=entry.votes)
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=VOTES,
            count_round=0,
            value=entry.votes,
        )
    p.add("declared", result_set_id=result_set, option_id=None, measure=BALLOTS_CAST, value=held.cast)
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=None,
        channel=0,
        measure=BALLOTS_CAST,
        count_round=0,
        value=held.cast,
    )
    return p
