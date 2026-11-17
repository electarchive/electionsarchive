"""Croatia's elections of 1991 to 1997, digitized from the State Electoral Commission's scanned reports.

The authority's archive publishes these elections only as scanned PDFs: the 1991 independence referendum report,
the 1992 and 1995 reports on the Chamber of Representatives (Zastupnički dom), and the 1992 and 1997 presidential
reports by municipality and abroad. Each figure was read from the page image, checked against the printed totals,
and keeps its page and bounding box. The transcriptions live in ``skenovi.json`` beside this module; ``izbori``
builds these packages along with its own.
"""

import json
import re
import subprocess
from collections.abc import Sequence
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.hr import izbori as z

DATA_FILE = Path(__file__).with_name("skenovi.json")
DIGITIZED = 8
REGISTERED, CAST, INVALID, VOTES, VOTERS = z.REGISTERED, z.CAST, z.INVALID, z.VOTES, z.VOTERS
ARCHIVE = f"{z.DATA}"
URLS = {
    "referendum_1991": f"{ARCHIVE}/referendum/1991/rezultati/1991_Rezultati_Referendum.pdf",
    "sabor_1992": f"{ARCHIVE}/parlament/1992/rezultati/1992_2_1_Rezultati_Sabor_zastupnicki_dom_po_IJ.pdf",
    "sabor_1992_lists": f"{ARCHIVE}/parlament/1992/rezultati/1992_2_2_Rezultati_Sabor_zastupnicki_dom_po_listama.pdf",
    "sabor_1995": f"{ARCHIVE}/parlament/1995/rezultati/1995_Rezultati_Sabor_zastupnicki_dom.pdf",
    "predsjednik_1992": f"{ARCHIVE}/predsjednik/1992/rezultati/1992_1_1_Rezultati_Predsjednik_po_opcinama_i_inozemstvo.pdf",
    "predsjednik_1992_check": f"{ARCHIVE}/predsjednik/1992/rezultati/1992_1_2_Rezultati_Predsjednik_po_opcinama_redovita_BM.pdf",
    "predsjednik_1997_towns": f"{ARCHIVE}/predsjednik/1997/rezultati/1997_4_1_Rezultati_Predsjednik_gradovi.pdf",
    "predsjednik_1997_munis": f"{ARCHIVE}/predsjednik/1997/rezultati/1997_4_2_Rezultati_Predsjednik_opcine.pdf",
    "predsjednik_1997_abroad": f"{ARCHIVE}/predsjednik/1997/rezultati/1997_4_3_Rezultati_Predsjednik_inozemstvo.pdf",
    "predsjednik_1997_summary": f"{ARCHIVE}/predsjednik/1997/rezultati/1997_4_4_Rezultati_Predsjednik_sumarni.pdf",
    "predsjednik_1997_official": f"{ARCHIVE}/predsjednik/1997/rezultati/1997_4_5_Rezultati_Predsjednik_sluzbeni.pdf",
}
# Further scans in the same folders, fetched so the raw cache holds the whole archive of these elections.
OTHER = [
    f"{ARCHIVE}/parlament/1992/rezultati/1992_2_3_Rezultati_Sabor_zastupnicki_dom_Sabor_liste_redovita_BM.pdf",
    f"{ARCHIVE}/parlament/1992/rezultati/1992_2_4_Rezultati_Sabor_zastupnicki_dom_analitika.pdf",
    f"{ARCHIVE}/parlament/1992/rezultati/1992_2_5_Rezultati_Sabor_zastupnicki_dom_izracun_broja_zastupnickih_mjesta.pdf",
]
REFERENDUM = date(1991, 5, 19)
SABOR_1992 = PRESIDENT_1992 = date(1992, 8, 2)
SABOR_1995 = date(1995, 10, 29)
PRESIDENT_1997 = date(1997, 6, 15)


def roman(n: int) -> str:
    out = ""
    for value, digit in ((50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += digit
            n -= value
    return out


@cache
def data() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text())


def packages() -> list[str]:
    return [
        f"{z.SOURCE}/referendum-{REFERENDUM}",
        f"{z.SOURCE}/sabor-{SABOR_1992}",
        f"{z.SOURCE}/predsjednik-{PRESIDENT_1992}",
        f"{z.SOURCE}/sabor-{SABOR_1995}",
        f"{z.SOURCE}/predsjednik-{PRESIDENT_1997}",
    ]


def fetch() -> None:
    for url in [*URLS.values(), *OTHER]:
        raw.fetch(z.SOURCE, url, suffix=".pdf")


def parse() -> None:
    referendum().write()
    sabor_1992().write()
    president_1992().write()
    sabor_1995().write()
    president_1997().write()


# Parties and electoral systems, added to the izbori registry.


# Parties the reports name in full; the district reports give only these abbreviations.
FULL = {
    "ASH": "Akcija socijaldemokrata Hrvatske - ASH",
    "DA": "Dalmatinska akcija - DA",
    "DGS": "Domovinska građanska stranka - DGS",
    "HDP": "Hrvatski državotvorni pokret - HDP",
    "HDS": "Hrvatska demokratska stranka - HDS",
    "HDZ": "Hrvatska demokratska zajednica - HDZ",
    "HKDS": "Hrvatska kršćanska demokratska stranka - HKDS",
    "HKDU": "Hrvatska kršćanska demokratska unija - HKDU",
    "HKS": "Hrvatska konzervativna stranka - HKS",
    "HND": "Hrvatski nezavisni demokrati - HND",
    "HNS": "Hrvatska narodna stranka - HNS",
    "HSLS": "Hrvatska socijalno liberalna stranka - HSLS",
    "HSNZ": "Hrvatska stranka naravnog zakona - HSNZ",
    "HSP": "Hrvatska stranka prava - HSP",
    "HSP-1861": "Hrvatska stranka prava - 1861 - HSP - 1861",
    "HSS": "Hrvatska seljačka stranka - HSS",
    "IDF": "Istarski demokratski forum - Foro democratico istriano - IDF-FDI",
    "IDS": "Istarski demokratski sabor - IDS",
    "KNS": "Kršćanska narodna stranka - KNS",
    "NSP": "Nezavisna stranka prava - NSP",
    "RDS": "Riječki demokratski savez",
    "RiDS": "Riječki demokratski savez",
    "SBHS": "Slavonsko-baranjska hrvatska stranka - SBHS",
    "SDA": "Stranka demokratske akcije Hrvatske - SDA Hrvatske",
    "SDH": "Socijaldemokratska stranka Hrvatske - SDH",
    "SDP": "Socijaldemokratska partija Hrvatske - SDP",
    "SDU": "Socijalno-demokratska unija Hrvatske - SDU",
    "SNS": "Srpska narodna stranka - SNS",
    "SSH": "Socijalistička stranka Hrvatske - SSH",
    "ZDD": "Zajednica domovine i dijaspore - ZDD",
    "ZZ": "Zelena zajednica - ZZ",
}
# The parties that nominated each 1997 presidential candidate, as the official report prints them.
NOMINATED_1997 = [["HSLS", "HSS", "HNS", "IDS", "IDF", "ASH", "DA", "SDA", "ZZ"], ["SDP"], ["HDZ"]]
# A list's printed name -> the parties that ran it.
LISTS = {
    "Dalmatinska akcija, Istarski demokratski sabor - IDS i Riječki demokratski savez": ["DA", "IDS", "RDS"],
    "Hrvatska seljačka stranka - HSS, Istarski demokratski sabor - IDS, Hrvatska narodna stranka - HNS, Hrvatska "
    "kršćanska demokratska unija - HKDU, Slavonsko-baranjska hrvatska stranka - SBHS": [
        "HSS",
        "IDS",
        "HNS",
        "HKDU",
        "SBHS",
    ],
    "Socijaldemokratska partija Hrvatske - Stranka demokratskih promjena - SDP": ["SDP"],
    "Hrvatska stranka naravnog zakona": ["HSNZ"],
    "Srpska narodna stranka": ["SNS"],
}


def party_name(token: str) -> str:
    """A party as the reports print it: an abbreviation, or a full name."""
    return FULL.get(token, token)


def party_id(token: str) -> str:
    return z.party(party_name(token))


def list_parties(name: str) -> list[str]:
    if name in LISTS:
        return LISTS[name]
    found = re.search(r" - ([^-]+)$", name)
    abbreviation = found.group(1).strip() if found else ""
    return [abbreviation if abbreviation in FULL else name]


def option_party(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    if len(tokens) == 1:
        return party_id(tokens[0])
    return z.bloc([party_name(t) for t in tokens])


def _parties() -> tuple[dict[str, str], dict[str, list[str]]]:
    """Every party (id -> printed name) and coalition (id -> member tokens) these elections name."""
    single: dict[str, str] = {}
    blocs: dict[str, list[str]] = {}

    def seen(tokens: list[str]) -> None:
        for token in tokens:
            single.setdefault(party_id(token), party_name(token))
        if len(tokens) > 1:
            blocs.setdefault(option_party(tokens) or "", tokens)

    d = data()
    for election in ("sabor_1992", "sabor_1995"):
        for part in ("national", "diaspora"):
            for row in d[election].get(part, {}).get("lists", []):
                seen(list_parties(row["name"]))
        for district in d[election]["districts"]:
            for candidate in district["candidates"]:
                seen(candidate["parties"])
    for tokens in NOMINATED_1997:
        seen(tokens)
    return single, blocs


SYSTEMS = [
    (
        "list_1992",
        date(1992, 1, 1),
        date(1995, 1, 1),
        {"formula": "dhondt", "threshold": {"level": "list", "scope": "contest", "percent": 3}},
        (
            "In 1992, 60 members of the Chamber of Representatives were elected from national lists, by D'Hondt among the "
            "lists with at least 3% of the votes cast. A national minority whose share of the 1981 census population "
            "exceeded 8% was guaranteed seats in proportion to that share, filled from candidates of that minority on the "
            "lists."
        ),
    ),
    (
        "list_1995",
        date(1995, 1, 1),
        date(1999, 1, 1),
        {"formula": "dhondt", "threshold": {"level": "list", "scope": "contest", "percent": 5}},
        (
            "In 1995, 80 members were elected from national lists by D'Hondt among the lists that passed the threshold: 5% "
            "of the votes cast for a party's list, 8% for a list of two parties, and 11% for a list of three or more."
        ),
    ),
    (
        "diaspora_1995",
        date(1995, 1, 1),
        date(1999, 1, 1),
        {"formula": "dhondt"},
        (
            "In 1995, Croatian citizens without residence in Croatia elected 12 members from separate lists (posebne "
            "liste), whose seats were divided by D'Hondt."
        ),
    ),
    (
        "single_1992",
        date(1992, 1, 1),
        date(1999, 1, 1),
        {"formula": "fptp"},
        (
            "Each single-member district (28 in 1995, 60 in 1992) and each special district of a national minority with "
            "one seat elected the candidate with the most votes."
        ),
    ),
    (
        "block_1995",
        date(1995, 1, 1),
        date(1999, 1, 1),
        {"formula": "block"},
        (
            "In 1995, members of the Serb minority elected three members in the fifth special district; a voter could "
            "vote for more than one candidate, and the three with the most votes were elected."
        ),
    ),
]


def system(name: str) -> str:
    return z.system(name)


def registry(p: Package, known: set[str]) -> None:
    """Add the 1990s electoral systems, and the parties of these elections the later files do not name."""
    for name, since, until, rules, method in SYSTEMS:
        id = system(name)
        p.add(
            "electoral_system",
            id=id,
            polity_id=z.CROATIA,
            valid_from=since,
            valid_to=until,
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=z.key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
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
    for id, tokens in sorted(blocs.items()):
        if id in known:
            continue
        known.add(id)
        p.add("party", id=id, kind="bloc", polity_id=z.CROATIA)
        p.name(id, ", ".join(tokens), "hr", "official")
        p.name(id, ", ".join(sorted({party_name(t) for t in tokens})), "hr", "ballot", False)


# Shared building blocks.


class Values:
    def __init__(self) -> None:
        self.values: list[tuple[str, str, str | None, int, int]] = []
        self.locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []

    def add(
        self, result_set: str, unit: str, option: str | None, measure: int, value: int, box: list | None, pages: dict
    ) -> None:
        self.values.append((result_set, unit, option, measure, value))
        if box:
            page, *coords = box
            self.locators.append((result_set, unit, option, measure, pages[page], *coords))

    def write(self, p: Package) -> None:
        p.frame(
            "result_value",
            pl.DataFrame(
                self.values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(DIGITIZED, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )
        if self.locators:
            p.frame(
                "value_locator",
                pl.DataFrame(
                    self.locators,
                    schema=["result_set_id", "unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
                    orient="row",
                ).with_columns(
                    channel=pl.lit(0, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    source_id=pl.lit(None, pl.String),
                    measure=pl.col("measure").cast(pl.Int16),
                ),
            )


def _sizes(path: Path) -> dict[int, tuple[float, float]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "999", str(path)], capture_output=True, text=True, check=True)
    found = (re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line) for line in out.stdout.splitlines())
    return {int(m[1]): (float(m[2]), float(m[3])) for m in found if m}


def _pages(p: Package, name: str) -> dict[int, str]:
    """Cite one scanned report and register its pages; boxes are in pixels of the page rendered at 100 dpi."""
    stored = raw.cached(z.SOURCE, URLS[name])
    artifact = p.artifact(stored, z.SOURCE_ID)
    pages: dict[int, str] = {}
    for number, (width, height) in sorted(_sizes(stored.path).items()):
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


def _election(p: Package, day: date, family: str, kind: str, english: str, native: str, slug: str) -> str:
    id = z.key("election", day, family)
    p.add("election", id=id, polity_id=z.CROATIA, kind=kind, date_from=day, date_to=day, status="held")
    p.name(id, english, "en", "official")
    p.name(id, native, "hr", "native", False)
    p.add("slug", path=slug, entity_id=id, is_current=True)
    return id


def _contest(
    p: Package,
    election: str,
    tag: str,
    kind: str,
    english: str,
    native: str,
    slug: str,
    magnitude: int,
    rules: str,
    *,
    body: str | None = None,
    office: str | None = None,
    district: str | None = None,
) -> tuple[str, str]:
    contest = z.key("contest", tag)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind=kind,
        office_id=office,
        body_id=body,
        district_area_id=district,
        magnitude=magnitude,
        round_number=1,
        electoral_system_id=rules,
        default_result_kind="counted",
    )
    p.name(contest, english, "en", "official")
    p.name(contest, native, "hr", "native", False)
    p.add("slug", path=slug, entity_id=contest, is_current=True)
    result_set = z.key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=z.SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    return contest, result_set


def _note(p: Package, entity: str, kind: str, body: str, *parts: object) -> None:
    p.add("note", id=z.key("note", entity, kind, *parts), entity_id=entity, kind=kind, body_md=body, lang="en")


def _unit(
    p: Package, code: str, scheme: str, kind: str = "standard", area: str | None = None, name: str | None = None
) -> str:
    id = z.key("unit", code)
    p.add(
        "reporting_unit",
        id=id,
        polity_id=z.CROATIA,
        scheme_code=scheme,
        code=code,
        area_relation="within" if area else "unlocated",
        area_id=area,
        kind=kind,
    )
    if name:
        p.name(id, name, "hr", "official")
    return id


def _person(p: Package, tag: str, name: str) -> str:
    person = z.key("person", tag)
    p.add("person", id=person)
    p.name(person, name, "hr", "official")
    return person


def _candidate(
    p: Package, contest: str, tag: str, position: int, name: str, tokens: list[str] | None, label: str | None = None
) -> str:
    """A candidate; tokens are the parties printed beside the name, or None where the report prints none at all."""
    option = z.key("option", tag)
    party = option_party(tokens or [])
    p.add(
        "contest_option",
        id=option,
        contest_id=contest,
        kind="candidate",
        party_id=party,
        ballot_position=position,
        ballot_label=label or name,
    )
    person = _person(p, tag, name)
    p.add(
        "candidacy",
        id=z.key("candidacy", tag),
        person_id=person,
        option_id=option,
        role="head",
        ballot_name=label or name,
        member_of_party_id=party,
        is_independent=None if tokens is None else party is None,
    )
    return option


DIGITIZED_NOTE = (
    "Digitized from the State Electoral Commission's scanned report: every figure was read from the page image, "
    "checked against the totals the report prints, and keeps its page and bounding box. Percentages are not stored."
)


def _new(key: str, summary: str) -> Package:
    p = Package(f"{z.SOURCE}/{key}", z.SOURCE, summary, [f"{z.SOURCE}/geo", f"{z.SOURCE}/registry"])
    p.external |= {z.CROATIA, z.COUNTRY}
    return p


# The 1991 referendum.

QUESTIONS_1991 = [
    (
        "blue",
        (
            "Independence: may Croatia, as a sovereign and independent state, enter into a union of sovereign states with "
            "the other republics?"
        ),
        (
            "Jeste li za to da Republika Hrvatska, kao suverena i samostalna država, koja jamči kulturnu autonomiju i sva "
            "građanska prava Srbima i pripadnicima drugih nacionalnosti u Hrvatskoj, može stupiti u savez suverenih država "
            "s drugim republikama (prema prijedlogu Republike Hrvatske i Republike Slovenije za rješenje državne krize "
            "SFRJ)?"
        ),
    ),
    (
        "red",
        "Federation: should Croatia remain in Yugoslavia as a single federal state?",
        (
            "Jeste li za to da Republika Hrvatska ostane u Jugoslaviji kao jedinstvenoj saveznoj državi (prema prijedlogu "
            "Republike Srbije i Socijalističke Republike Crne Gore za rješenje državne krize u SFRJ)?"
        ),
    ),
]


def referendum() -> Package:
    d = data()["referendum_1991"]
    p = _new(f"referendum-{REFERENDUM}", "The 1991 Croatian independence referendum, digitized from the report")
    pages = _pages(p, "referendum_1991")
    election = _election(
        p,
        REFERENDUM,
        "referendum",
        "referendum",
        "1991 Croatian independence referendum",
        "Referendum o samostalnosti Republike Hrvatske 1991.",
        f"hr/{REFERENDUM}-referendum",
    )
    values = Values()
    for number, ((color, english, native), q) in enumerate(zip(QUESTIONS_1991, d["questions"], strict=True), 1):
        tag = f"{REFERENDUM}:{color}"
        contest, result_set = _contest(
            p,
            election,
            tag,
            "ballot_question",
            english,
            native,
            f"hr/{REFERENDUM}-referendum/{'independence' if number == 1 else 'federation'}",
            1,
            system("referendum"),
            district=z.COUNTRY,
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        _note(
            p,
            result_set,
            "methodology",
            DIGITIZED_NOTE + " The report gives national totals only. Voters are those who voted in the referendum "
            f"({'blue' if color == 'blue' else 'red'} ballot); ballots were cast on 19 May 1991, with voting from 10 to "
            "18 May allowed under article 23 of the referendum law. The answers and invalid ballots of each question "
            "add up to fewer than the voters, and the report does not explain the difference.",
        )
        unit = _unit(p, f"{REFERENDUM}:{color}:total", "hr.bm", "aggregate", z.COUNTRY)
        yes, no = z.key("option", tag, "yes"), z.key("option", tag, "no")
        for option, position, label, english_label in ((yes, 1, "ZA", "Yes"), (no, 2, "PROTIV", "No")):
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
            p.name(option, english_label, "en", "official")
        carried = q["yes"] > q["no"]
        p.add("outcome", result_set_id=result_set, option_id=yes, kind="won" if carried else "lost")
        p.add("outcome", result_set_id=result_set, option_id=no, kind="lost" if carried else "won")
        boxes = q["boxes"]
        values.add(result_set, unit, None, REGISTERED, d["registered"], d["boxes"]["registered"], pages)
        values.add(result_set, unit, None, VOTERS, d["voters"], boxes.get("voters") or d["boxes"]["voters"], pages)
        values.add(result_set, unit, None, INVALID, q["invalid"], boxes["invalid"], pages)
        values.add(result_set, unit, yes, VOTES, q["yes"], boxes["yes"], pages)
        values.add(result_set, unit, no, VOTES, q["no"], boxes["no"], pages)
    values.write(p)
    return p


# The Chamber of Representatives, 1992 and 1995.


def _lists(
    p: Package,
    election: str,
    tag: str,
    english: str,
    native: str,
    slug: str,
    rules: str,
    part: dict,
    pages: dict,
    values: Values,
    unit_code: str,
    area: str | None,
    note: str,
) -> None:
    seats = sum(row["seats"] for row in part["lists"])
    contest, result_set = _contest(
        p, election, tag, "list_allocation", english, native, slug, seats, rules, body=z.HOUSE_2000, district=area
    )
    _note(p, result_set, "methodology", DIGITIZED_NOTE + " " + note)
    unit = _unit(p, unit_code, "hr.bm", "aggregate", area)
    values.add(result_set, unit, None, REGISTERED, part["registered"], part["boxes"]["registered"], pages)
    values.add(result_set, unit, None, CAST, part["cast"], part["boxes"]["cast"], pages)
    values.add(result_set, unit, None, INVALID, part["invalid"], part["boxes"]["invalid"], pages)
    for position, row in enumerate(part["lists"], 1):
        option = z.key("option", tag, position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=option_party(list_parties(row["name"])),
            ballot_position=position,
            ballot_label=row["name"],
        )
        p.add(
            "outcome",
            result_set_id=result_set,
            option_id=option,
            kind="won" if row["seats"] else "lost",
            seats=row["seats"],
        )
        values.add(result_set, unit, option, VOTES, row["votes"], row["box"], pages)


def _districts(
    p: Package, election: str, day: date, year: int, districts: list[dict], pages: dict, values: Values
) -> None:
    scheme = f"hr.izborna_jedinica_{year}"
    count = sum(1 for d in districts if d["district"].isdigit())
    p.add(
        "scheme",
        code=scheme,
        polity_id=z.CROATIA,
        kind="electoral_district",
        rank=1,
        noun="electoral district",
        kinds=None,
        code_system=None,
    )
    for d in districts:
        special = not d["district"].isdigit()
        n = int(d["district"].lstrip("P"))
        tag = f"{day}:{d['district']}"
        seats = 3 if d["district"] == "P5" else 1
        if special:
            english = f"Special electoral district {n}"
            native = f"{n}. posebna izborna jedinica"
            slug = f"special-district-{n}"
        else:
            english = f"Electoral district {roman(n)}"
            native = f"{roman(n)}. izborna jedinica"
            slug = f"district-{roman(n).lower()}"
        contest, result_set = _contest(
            p,
            election,
            tag,
            "body_seats",
            english,
            native,
            f"hr/{day}-parliamentary/{slug}",
            seats,
            system("block_1995") if seats > 1 else system("single_1992"),
            body=z.HOUSE_2000,
        )
        _note(
            p,
            result_set,
            "methodology",
            DIGITIZED_NOTE
            + " The report prints the district's registered voters, voters, invalid ballots, and each candidate's "
            "votes; candidates' votes add up to the voters less the invalid ballots"
            + (", except in this district, where a voter could vote for more than one candidate." if seats > 1 else ".")
            + f" Croatia had {count} single-member districts in {year}.",
        )
        unit = _unit(p, f"{day}:{d['district']}", scheme, name=native)
        b = d["boxes"]
        values.add(result_set, unit, None, REGISTERED, d["registered"], b["registered"], pages)
        values.add(result_set, unit, None, CAST, d["cast"], b["cast"], pages)
        values.add(result_set, unit, None, INVALID, d["invalid"], b["invalid"], pages)
        ranked = sorted(d["candidates"], key=lambda c: -c["votes"])
        elected = {id(c) for c in ranked[:seats]}
        for position, c in enumerate(d["candidates"], 1):
            option = _candidate(p, contest, f"{tag}:{position}", position, c["name"], c["parties"])
            values.add(result_set, unit, option, VOTES, c["votes"], c["box"], pages)
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="elected" if id(c) in elected else "lost",
            )


def sabor_1992() -> Package:
    d = data()["sabor_1992"]
    p = _new(f"sabor-{SABOR_1992}", "The 1992 election to the Chamber of Representatives, digitized from the reports")
    election = _election(
        p,
        SABOR_1992,
        "sabor",
        "general",
        "1992 Croatian parliamentary election",
        "Izbori za Zastupnički dom Sabora Republike Hrvatske 1992.",
        f"hr/{SABOR_1992}-parliamentary",
    )
    values = Values()
    _lists(
        p,
        election,
        f"{SABOR_1992}:lists",
        "National lists",
        "Državne liste",
        f"hr/{SABOR_1992}-parliamentary/national-lists",
        system("list_1992"),
        d["national"],
        _pages(p, "sabor_1992_lists"),
        values,
        f"{SABOR_1992}:lists:total",
        z.COUNTRY,
        "The report gives national totals only. Seats are the 60 the lists won under article 24; under article 26, "
        "13 further Serb members (2 from the HNS list, 8 from the SDP list, and 3 from the Srpska narodna stranka "
        "list) and one Jewish member (from the HSLS list) were also deemed elected from the lists, and are not "
        "counted here.",
    )
    _districts(p, election, SABOR_1992, 1992, d["districts"], _pages(p, "sabor_1992"), values)
    values.write(p)
    return p


def sabor_1995() -> Package:
    d = data()["sabor_1995"]
    p = _new(f"sabor-{SABOR_1995}", "The 1995 election to the Chamber of Representatives, digitized from the report")
    election = _election(
        p,
        SABOR_1995,
        "sabor",
        "general",
        "1995 Croatian parliamentary election",
        "Izbori za Zastupnički dom Sabora Republike Hrvatske 1995.",
        f"hr/{SABOR_1995}-parliamentary",
    )
    pages = _pages(p, "sabor_1995")
    values = Values()
    _lists(
        p,
        election,
        f"{SABOR_1995}:lists",
        "National lists",
        "Državne liste",
        f"hr/{SABOR_1995}-parliamentary/national-lists",
        system("list_1995"),
        d["national"],
        pages,
        values,
        f"{SABOR_1995}:lists:total",
        z.COUNTRY,
        "The report gives national totals only.",
    )
    _lists(
        p,
        election,
        f"{SABOR_1995}:diaspora",
        "Special lists (voters without residence in Croatia)",
        "Posebne liste",
        f"hr/{SABOR_1995}-parliamentary/special-lists",
        system("diaspora_1995"),
        d["diaspora"],
        pages,
        values,
        f"{SABOR_1995}:diaspora:total",
        None,
        "The report gives totals only, for voters without residence in Croatia.",
    )
    _districts(p, election, SABOR_1995, 1995, d["districts"], pages, values)
    values.write(p)
    return p


# Presidential elections.


def _president_options(
    p: Package,
    contest: str,
    result_set: str,
    day: date,
    names: list[str],
    parties: Sequence[list[str] | None],
    winner: int,
) -> list[str]:
    options = []
    for position, (name, tokens) in enumerate(zip(names, parties, strict=True), 1):
        option = _candidate(p, contest, f"{day}:president:{position}", position, name, tokens)
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if position == winner else "lost")
        options.append(option)
    return options


def president_1992() -> Package:
    d = data()["predsjednik_1992"]
    p = _new(
        f"predsjednik-{PRESIDENT_1992}", "The 1992 presidential election by municipality, digitized from the report"
    )
    pages = _pages(p, "predsjednik_1992")
    p.uses(raw.cached(z.SOURCE, URLS["predsjednik_1992_check"]))
    election = _election(
        p,
        PRESIDENT_1992,
        "predsjednik",
        "general",
        "1992 Croatian presidential election",
        "Izbori za predsjednika Republike Hrvatske 1992.",
        f"hr/{PRESIDENT_1992}-presidential",
    )
    contest, result_set = _contest(
        p,
        election,
        f"{PRESIDENT_1992}:president",
        "office",
        "President of the Republic of Croatia",
        "Predsjednik Republike Hrvatske",
        f"hr/{PRESIDENT_1992}-presidential/president",
        1,
        system("president"),
        office=z.PRESIDENCY,
        district=z.COUNTRY,
    )
    national = d["national"]
    _note(
        p,
        result_set,
        "methodology",
        DIGITIZED_NOTE + " Units are the 100 municipalities (općine) of 1992 for the regular electorate, as the report "
        "names them, and the total of polling stations abroad. Each municipality was read twice, once from its own "
        "table and once from the companion report that ranks the municipalities by each candidate's share, and the "
        "municipalities add up to the report's total for the regular electorate (3,575,032 registered, 2,578,032 "
        "ballots, 48,909 invalid).",
    )
    _note(
        p,
        result_set,
        "caveat",
        "The report's national result (2,677,764 ballots, 50,703 invalid) exceeds the regular electorate and the "
        "polling stations abroad together by 23,159 ballots and 305 invalid ballots, which the report does not break "
        "down further; registered voters abroad (80,062) are counted within the national total of 3,575,032.",
    )
    # The report prints no parties beside the candidates.
    options = _president_options(p, contest, result_set, PRESIDENT_1992, d["candidates"], [None] * 8, 6)
    scheme = "hr.opcina_1992"
    p.add(
        "scheme",
        code=scheme,
        polity_id=z.CROATIA,
        kind="administrative",
        rank=2,
        noun="municipality",
        kinds=None,
        code_system=None,
    )
    values = Values()
    for u in d["units"]:
        unit = _unit(p, f"{PRESIDENT_1992}:opcina:{u['code']}", scheme, name=u["name"])
        b = u["boxes"]
        values.add(result_set, unit, None, REGISTERED, u["registered"], b["reg"], pages)
        values.add(result_set, unit, None, CAST, u["cast"], b["voted"], pages)
        values.add(result_set, unit, None, INVALID, u["invalid"], b["inv"], pages)
        for option, votes, box in zip(options, u["votes"], u["vote_boxes"], strict=True):
            values.add(result_set, unit, option, VOTES, votes, box, pages)
    a = d["abroad"]
    unit = _unit(p, f"{PRESIDENT_1992}:abroad", scheme, "aggregate", name="Inozemstvo")
    values.add(result_set, unit, None, REGISTERED, a["registered"], a["boxes"]["registered"], pages)
    values.add(result_set, unit, None, CAST, a["cast"], a["boxes"]["cast"], pages)
    values.add(result_set, unit, None, INVALID, a["invalid"], a["boxes"]["invalid"], pages)
    for option, votes, box in zip(options, a["votes"], a["vote_boxes"], strict=True):
        values.add(result_set, unit, option, VOTES, votes, box, pages)
    values.write(p)
    for measure, value in (
        (REGISTERED, national["registered"]),
        (CAST, national["cast"]),
        (INVALID, national["invalid"]),
    ):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for option, votes in zip(options, national["votes"], strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
    p.accept(
        "accounting.declared",
        "the report's national result exceeds its municipalities and abroad together by 23,159 ballots, 305 invalid "
        "ballots, and the matching votes, which it does not break down, and counts registered voters abroad within "
        "its national 3,575,032",
    )
    return p


def president_1997() -> Package:
    d = data()["predsjednik_1997"]
    p = _new(f"predsjednik-{PRESIDENT_1997}", "The 1997 presidential election by town and municipality, digitized")
    towns = _pages(p, "predsjednik_1997_towns")
    munis = _pages(p, "predsjednik_1997_munis")
    _pages(p, "predsjednik_1997_abroad")
    _pages(p, "predsjednik_1997_summary")
    _pages(p, "predsjednik_1997_official")
    election = _election(
        p,
        PRESIDENT_1997,
        "predsjednik",
        "general",
        "1997 Croatian presidential election",
        "Izbori za predsjednika Republike Hrvatske 1997.",
        f"hr/{PRESIDENT_1997}-presidential",
    )
    contest, result_set = _contest(
        p,
        election,
        f"{PRESIDENT_1997}:president",
        "office",
        "President of the Republic of Croatia",
        "Predsjednik Republike Hrvatske",
        f"hr/{PRESIDENT_1997}-presidential/president",
        1,
        system("president"),
        office=z.PRESIDENCY,
        district=z.COUNTRY,
    )
    _note(
        p,
        result_set,
        "methodology",
        DIGITIZED_NOTE + " Units are the towns and municipalities of 1997 (regular polling stations), each placed in "
        "the current town or municipality of the same name where one exists, the mixed polling stations as one "
        "total, and the polling stations abroad by country. The towns and municipalities add up to the summary "
        "report's regular polling stations on every figure (3,683,774 registered, 2,120,186 ballots, 38,965 invalid), "
        "and the countries to its total abroad on every figure but registered voters.",
    )
    _note(
        p,
        result_set,
        "caveat",
        "The table of polling stations abroad prints 377,705 registered voters in its total row, while its 48 "
        "countries add up to 219,410; every other column of the table adds up. Each country is stored as printed. "
        "Mixed polling stations have no registered voters of their own.",
    )
    options = _president_options(p, contest, result_set, PRESIDENT_1997, d["candidates"], NOMINATED_1997, 3)
    values = Values()
    for group, pages in (("towns", towns), ("municipalities", munis)):
        for u in d[group]:
            code = u["code"] or z.label(u["name"]).replace(" ", "-")
            area = z.area("hr.opcina", u["area"]) if u["area"] else None
            unit = _unit(p, f"{PRESIDENT_1997}:{code}", "hr.opcina", area=area, name=u["name"])
            boxes = u.get("boxes") or [None] * 6
            figures = [
                (None, REGISTERED, u["registered"]),
                (None, CAST, u["cast"]),
                (None, INVALID, u["invalid"]),
                *((o, VOTES, v) for o, v in zip(options, u["votes"], strict=True)),
            ]
            for (option, measure, value), box in zip(figures, boxes, strict=True):
                values.add(result_set, unit, option, measure, value, box, pages)
    mixed = d["mixed"]
    unit = _unit(p, f"{PRESIDENT_1997}:mixed", "hr.bm", "aggregate", z.COUNTRY, "Mješovita biračka mjesta")
    values.add(result_set, unit, None, CAST, mixed["cast"], None, {})
    values.add(result_set, unit, None, INVALID, mixed["invalid"], None, {})
    for option, votes in zip(options, mixed["votes"], strict=True):
        values.add(result_set, unit, option, VOTES, votes, None, {})
    for country in d["abroad"]:
        unit = _unit(
            p, f"{PRESIDENT_1997}:abroad:{z.label(country['name'])}", "hr.bm", "abroad_mission", name=country["name"]
        )
        values.add(result_set, unit, None, REGISTERED, country["registered"], None, {})
        values.add(result_set, unit, None, CAST, country["cast"], None, {})
        values.add(result_set, unit, None, INVALID, country["invalid"], None, {})
        for option, votes in zip(options, country["votes"], strict=True):
            values.add(result_set, unit, option, VOTES, votes, None, {})
    values.write(p)
    national = d["national"]
    for measure, value in (
        (REGISTERED, national["registered"]),
        (CAST, national["cast"]),
        (INVALID, national["invalid"]),
    ):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for option, votes in zip(options, national["votes"], strict=True):
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
    p.accept(
        "accounting.declared",
        "the report's table abroad prints 377,705 registered voters in its total row while its countries add up to "
        "219,410, so the units' registered voters fall 158,295 short of the national 4,061,479; ballots, invalid "
        "ballots, and every candidate's votes match",
    )
    return p
