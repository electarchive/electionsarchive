"""Ecuador's election of the Council of Citizen Participation and Social Control on 24 March 2019, from the National
Electoral Council's results book.

The book prints each candidate's national votes in three contests: women, men, and the candidates of indigenous
peoples and nationalities, Afro-Ecuadorians, Montubios, and Ecuadorians abroad. It prints no figures by place, so the
votes are stored for the country as a whole. The three leading women, the three leading men, and the leading
candidate of the third contest took the seats of members, and the next three, three, and one took the seats of
substitutes.
"""

import functools
import re
import subprocess
import unicodedata
from datetime import date

from ea import package, raw
from ea.package import Package
from ea.sources.ec import cne, cne2023

SOURCE = cne.SOURCE
ECUADOR = cne.ECUADOR
COUNTRY = cne2023.COUNTRY
DAY = "2019-03-24"
DATE = date(2019, 3, 24)
BOOK = cne.BOOK_2019
key = cne.key
CNE_SOURCE = cne.CNE_SOURCE
BASE = f"ec/{DAY}-cpccs"
PAGE = 196
MEMBERS = {"women": 3, "men": 3, "peoples": 1}
SUBSTITUTES = {"women": 3, "men": 3, "peoples": 1}

NUMBER = r"\d{1,3}(?:\.\d{3})+"
ROW = re.compile(rf"(?P<name>[A-ZÁÉÍÓÚÑ]+(?: [A-ZÁÉÍÓÚÑ]+)+) +(?P<votes>{NUMBER}) +(?P<share>\d{{1,2}},\d\d)%")
# the book sets the men's table in the left column and the women's table and the peoples' table in the right
RIGHT = 40

METHOD = (
    "Counted nationally and published by the National Electoral Council in its book of the 2019 results, which "
    "prints each candidate's votes and share of the total for the country but no figures by place. Each voter could "
    "mark several candidates, so the candidates' votes add up to more than the ballots, and no ballot accounting is "
    "stored."
)
NOTE_CONTEST = (
    "Candidates ran without party lists. The leading candidates took the seats of members of the Council, and the "
    "next candidates in votes took the seats of substitutes, who are marked as substitutes and carry no outcome "
    "of their own. The book prints each candidate's two names, in capitals."
)
CONTESTS = (
    ("women", "women's seats", "mujeres"),
    ("men", "men's seats", "hombres"),
    ("peoples", "seats of peoples and nationalities and Ecuadorians abroad", "pueblos y nacionalidades y exterior"),
)


def packages() -> list[str]:
    return [f"{SOURCE}/registry-2023", f"{SOURCE}/cpccs-2019"]


def parse() -> None:
    cne2023.registry().write()
    build().write()


def emit() -> None:
    package.run("load", packages())


def fetch() -> None:
    raw.fetch(SOURCE, BOOK, suffix=".pdf")


@functools.cache
def book() -> dict[str, list[tuple[str, int, str]]]:
    """Each contest's candidates with their votes and printed shares, in the order the book prints them."""
    text = subprocess.run(
        ["pdftotext", "-layout", "-f", str(PAGE + 1), "-l", str(PAGE + 1), str(cne.artifact(BOOK).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    lines = unicodedata.normalize("NFC", text).splitlines()
    split = next(i for i, line in enumerate(lines) if "PUEBLOS, NACIONALIDADES" in line)
    found: dict[str, list[tuple[str, int, str]]] = {"women": [], "men": [], "peoples": []}
    for number, line in enumerate(lines):
        for row in ROW.finditer(line):
            if row.start() < RIGHT:
                part = "men"
            else:
                part = "peoples" if number > split else "women"
            found[part].append((cne.clean(row["name"]), int(row["votes"].replace(".", "")), row["share"]))
    return found


def check(rows: dict[str, list[tuple[str, int, str]]]) -> None:
    """Every table must run down in votes, and its printed shares must imply one total of votes."""
    for part, table in rows.items():
        votes = [v for _, v, _ in table]
        if votes != sorted(votes, reverse=True) or len({n for n, _, _ in table}) != len(table):
            raise ValueError(f"the {part} table does not read in order")
        base = sum(votes) / sum(float(s.replace(",", ".")) for _, _, s in table)
        for name, vote, share in table:
            if abs(vote / (base * float(share.replace(",", "."))) - 1) > 0.01:
                raise ValueError(f"{name}'s share of {part} disagrees with the votes")


def build() -> Package:
    p = Package(
        f"{SOURCE}/cpccs-2019",
        SOURCE,
        "Ecuador's 2019 election of the Council of Citizen Participation and Social Control, nationally, from the "
        "National Electoral Council's results book",
        depends=[f"{SOURCE}/registry-2023"],
    )
    p.external.update([ECUADOR, COUNTRY, "world.country"])
    p.uses(cne.artifact(BOOK))
    p.add(
        "source",
        id=CNE_SOURCE,
        kind="official_body",
        publisher="Consejo Nacional Electoral del Ecuador",
        title="Election results files and books published by the National Electoral Council",
        url=cne.PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Consejo Nacional Electoral del Ecuador",
    )
    p.artifact(cne.artifact(BOOK), CNE_SOURCE)
    rows = book()
    check(rows)
    election = key("election", DAY, "cpccs")
    p.add("election", id=election, polity_id=ECUADOR, kind="general", date_from=DATE, date_to=DATE, status="held")
    p.add("slug", path=BASE, entity_id=election, is_current=True)
    p.name(election, "2019 Ecuadorian Citizen Participation and Social Control Council election", "en", "official")
    p.name(
        election,
        "Elecciones del Consejo de Participación Ciudadana y Control Social del Ecuador 2019",
        "es",
        "native",
        False,
    )
    unit = key("unit", DAY, "cpccs", "national")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=ECUADOR,
        scheme_code="world.country",
        code=f"{DAY}:cpccs:national",
        area_relation="equals",
        area_id=COUNTRY,
        kind="standard",
    )
    p.name(unit, "Ecuador", "es", "official")
    for token, english, spanish in CONTESTS:
        table = rows[token]
        if len(table) < MEMBERS[token] + SUBSTITUTES[token]:
            raise ValueError(f"the {token} table is too short")
        name = f"cpccs/{token}"
        contest = key("contest", DAY, name)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            round_number=1,
            default_result_kind="counted",
            body_id=cne2023.CPCCS,
            district_area_id=COUNTRY,
            magnitude=MEMBERS[token],
        )
        p.name(
            contest,
            f"2019 Ecuadorian Council of Citizen Participation and Social Control election, {english}",
            "en",
            "official",
        )
        p.name(contest, f"Consejo de Participación Ciudadana y Control Social 2019, {spanish}", "es", "native", False)
        p.add("slug", path=f"{BASE}/{token}", entity_id=contest, is_current=True)
        p.add("note", id=key("note", contest), entity_id=contest, kind="methodology", body_md=NOTE_CONTEST, lang="en")
        result_set = key("result_set", DAY, name)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CNE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        seated = MEMBERS[token] + SUBSTITUTES[token]
        for position, (text, votes, _) in enumerate(table, 1):
            label = cne.title(text)
            option = key("option", DAY, name, text)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=label,
            )
            person = key("person", DAY, name, text)
            p.add("person", id=person)
            p.name(person, label, "es", "official")
            p.add(
                "candidacy",
                id=key("candidacy", DAY, name, text),
                person_id=person,
                option_id=option,
                role="substitute" if MEMBERS[token] < position <= seated else "head",
                ballot_name=label,
            )
            if position <= MEMBERS[token]:
                p.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=None, kind="elected")
            cne._put(p, result_set, unit, option, cne.VOTES, votes)
    return p
