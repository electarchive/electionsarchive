"""Croatia's elections to the Chamber of Counties (Županijski dom), 1993 and 1997, digitized from the scanned reports.

The State Electoral Commission's archive publishes each of these elections as one scanned report: for each of the 21
counties, its registered voters, voters, invalid ballots, the votes of each county list with its leader, and the
members elected from each list with their substitutes. Each county elected three members by D'Hondt. Every figure
was read from the page image, checked against the percentages the report prints beside it and against the county's
total of valid ballots, and keeps its page and bounding box. The transcriptions live in ``zupanijski.json`` beside
this module; ``izbori`` builds these packages along with its own.
"""

import json
import re
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from ea import raw
from ea.package import Package
from ea.sources.hr import izbori as z
from ea.sources.hr import skenovi as s

DATA_FILE = Path(__file__).with_name("zupanijski.json")
URLS = {
    1993: f"{z.DATA}/parlament/1993/rezultati/1993_2_Rezultati_Sabor_zupanijski_dom.pdf",
    1997: f"{z.DATA}/parlament/1997/rezultati/1997_3_Rezultati_Sabor_zupanijski_dom.pdf",
}
DAYS = {1993: date(1993, 2, 7), 1997: date(1997, 4, 13)}
BODY = z.key("body", "zupanijski_dom")
SYSTEM = z.system("county_list_1993")
# The 1993 counties whose territory changed markedly when the counties were redrawn in 1997: Zagreb County and the
# City of Zagreb were split along new lines, and the Knin area moved from Zadar-Knin to Šibenik-Knin County.
REDRAWN_1993 = {1, 13, 15, 21}
TITLES = re.compile(
    r"^(akademik|dipl\.?|dipl\.\w+\.?|doc\.\S*|dr\.\S*|ing\.|mr\.\S*|oecc?\.|prim\.\S*|prof\.\S*|sc\.|vet\.)$",
    re.IGNORECASE,
)


@cache
def data() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text())


def packages() -> list[str]:
    return [f"{z.SOURCE}/zupanijski-dom-{day}" for day in DAYS.values()]


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(z.SOURCE, url, suffix=".pdf")


def parse() -> None:
    for year in DAYS:
        election(year).write()


def person_name(printed: str) -> str:
    """A name without the academic titles and degrees the report prints around it."""
    words = printed.split(",")[0].split()
    while words and TITLES.match(words[0]):
        words.pop(0)
    return " ".join(words).replace(" - ", "-")


def _list_party(parties: list[str]) -> str | None:
    if not parties:
        return None
    return z.party(parties[0]) if len(parties) == 1 else z.bloc(parties)


def _parties() -> tuple[dict[str, str], dict[str, list[str]]]:
    single: dict[str, str] = {}
    blocs: dict[str, list[str]] = {}
    for counties in data().values():
        for county in counties:
            for row in county["lists"]:
                for name in row["parties"]:
                    single.setdefault(z.party(name), name)
                if len(row["parties"]) > 1:
                    blocs.setdefault(z.bloc(row["parties"]), row["parties"])
    return single, blocs


def registry(p: Package, known: set[str]) -> None:
    """Add the Chamber of Counties, its electoral system, and the parties its lists name that no other file does."""
    p.add("body", id=BODY, polity_id=z.CROATIA, kind="upper_house")
    p.name(BODY, "Chamber of Counties of the Croatian Parliament", "en", "official")
    p.name(BODY, "Županijski dom Sabora Republike Hrvatske", "hr", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=z.CROATIA,
        valid_from=date(1993, 1, 1),
        valid_to=date(2001, 4, 1),
        rules={"formula": "dhondt"},
        rules_version=1,
    )
    p.add(
        "note",
        id=z.key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "From 1993 until it was abolished in 2001, each of Croatia's 21 counties, the City of Zagreb among them, "
            "elected three members of the Chamber of Counties from closed county lists, by D'Hondt and without a "
            "threshold. The President of the Republic could appoint up to five further members."
        ),
        lang="en",
    )
    single, blocs = _parties()
    for id, name in sorted(single.items()):
        if id in known:
            continue
        known.add(id)
        p.add("party", id=id, kind="party", polity_id=z.CROATIA)
        p.add("external_id", entity_id=id, scheme="hr.izbori.stranka", value=z.label(name))
        p.name(id, name, "hr", "official")
        short = z.abbreviation(name)
        if short:
            p.name(id, short, "hr", "abbreviation", False)
    for id, names in sorted(blocs.items()):
        if id in known:
            continue
        known.add(id)
        p.add("party", id=id, kind="bloc", polity_id=z.CROATIA)
        p.name(id, ", ".join(names), "hr", "official")
        p.name(id, ", ".join(sorted(names)), "hr", "ballot", False)


def _pages(p: Package, year: int) -> dict[int, str]:
    stored = raw.cached(z.SOURCE, URLS[year])
    artifact = p.artifact(stored, z.SOURCE_ID)
    pages: dict[int, str] = {}
    for number, (width, height) in sorted(s._sizes(stored.path).items()):
        pages[number] = p.id("page", stored.sha256, number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{z.SOURCE}/{stored.sha256}-{number}.png",
            width=round(width * 100 / 72),
            height=round(height * 100 / 72),
        )
    return pages


METHOD = (
    "Digitized from the State Electoral Commission's scanned report: every figure was read from the page image, "
    "checked against the percentage the report prints beside it and against the county's valid ballots (voters "
    "less invalid ballots, which the lists' votes add up to exactly), and keeps its page and bounding box. "
    "Percentages are not stored. Voters are those who voted; the report gives no separate count of ballots found in "
    "the box. Each county elected three members by D'Hondt; the report names the members elected from each list "
    "with their substitutes (zamjenik), stored here as the list's elected members and substitutes, and the list "
    "leader (nositelj liste), stored as the list's head."
)


def election(year: int) -> Package:
    day = DAYS[year]
    counties = data()[str(year)]
    p = s._new(
        f"zupanijski-dom-{day}",
        f"The {year} election to the Chamber of Counties by county, digitized from the report",
    )
    pages = _pages(p, year)
    election = s._election(
        p,
        day,
        "zupanijski_dom",
        "general",
        f"{year} Croatian Chamber of Counties election",
        f"Izbori za Županijski dom Sabora Republike Hrvatske {year}.",
        f"hr/{day}-chamber-of-counties",
    )
    if year == 1993:
        s._note(
            p,
            election,
            "methodology",
            "The election was held on 7 February 1993. By decision of the Electoral Commission, voting was repeated "
            "on 21 February 1993 at several polling stations in Zadar-Knin County (Vinjerac, Gorica, Lovinac-Rupanj, "
            "Poličnik, and Turanj), at Mihanović Dol in Krapina-Zagorje County, at Mošćenica in Sisak-Moslavina "
            "County, and at Labinci in Istria County; the county results include the repeated voting.",
        )
    values = s.Values()
    for county in counties:
        number = county["number"]
        code = f"{number:02d}"
        tag = f"{day}:zupanijski_dom:{code}"
        shaped = year == 1997 or number not in REDRAWN_1993
        area = z.area("hr.zupanija", code)
        native = f"{county['name']} županija" if county["name"] != "Grad Zagreb" else "Grad Zagreb"
        english = native if county["name"] != "Grad Zagreb" else "City of Zagreb"
        slug = z.label(county["name"]).replace(" ", "-")
        contest, result_set = s._contest(
            p,
            election,
            tag,
            "body_seats",
            english,
            native,
            f"hr/{day}-chamber-of-counties/{slug}",
            3,
            SYSTEM,
            body=BODY,
            district=area if shaped else None,
        )
        note = METHOD
        if not shaped:
            note += (
                f" {native} of 1993 covered a different territory from the county of the same number drawn in "
                "1997, so its results are not placed on the current county's map."
            )
        if county["valid_printed"] is not None:
            note += " The lists' votes match the total of valid votes the report prints."
        s._note(p, result_set, "methodology", note)
        unit = z.key("unit", tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=z.CROATIA,
            scheme_code="hr.zupanija",
            code=tag,
            area_relation="equals" if shaped else "unlocated",
            area_id=area if shaped else None,
            kind="standard",
        )
        p.name(unit, native, "hr", "official")
        boxes = county["boxes"]
        values.add(result_set, unit, None, z.REGISTERED, county["registered"], boxes["registered"], pages)
        values.add(result_set, unit, None, z.VOTERS, county["cast"], boxes["cast"], pages)
        values.add(result_set, unit, None, z.INVALID, county["invalid"], boxes["invalid"], pages)
        for row in county["lists"]:
            position = row["number"]
            option = z.key("option", tag, position)
            party = _list_party(row["parties"])
            label = row["label"] + ": " if row["label"] else ""
            ballot = label + ", ".join(row["printed"]) if row["printed"] else "Nezavisna županijska lista"
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party,
                ballot_position=position,
                ballot_label=ballot,
            )
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if row["seats"] else "lost",
                seats=row["seats"],
            )
            values.add(result_set, unit, option, z.VOTES, row["votes"], row["box"], pages)
            leader = person_name(row["leader"])
            head = _candidacy(p, option, f"{tag}:{position}:head", leader, row["leader"], "head", row)
            heads = {leader: head}
            for rank, member in enumerate(row["elected"], 1):
                name = person_name(member["name"])
                candidacy = heads.get(name)
                if candidacy is None:
                    member_tag = f"{tag}:{position}:elected:{rank}"
                    candidacy = _candidacy(p, option, member_tag, name, member["name"], "list_member", row)
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    candidacy_id=candidacy,
                    kind="elected",
                )
                _candidacy(
                    p,
                    option,
                    f"{tag}:{position}:substitute:{rank}",
                    person_name(member["substitute"]),
                    member["substitute"],
                    "substitute",
                    row,
                )
    values.write(p)
    return p


def _candidacy(p: Package, option: str, tag: str, name: str, printed: str, role: str, row: dict) -> str:
    person = z.key("person", tag)
    p.add("person", id=person)
    p.name(person, name, "hr", "official")
    candidacy = z.key("candidacy", tag)
    p.add(
        "candidacy",
        id=candidacy,
        person_id=person,
        option_id=option,
        role=role,
        ballot_name=printed,
        member_of_party_id=None,
        is_independent=True if row["independent"] else None,
    )
    return candidacy
