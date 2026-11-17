"""Finland's Sámi Parliament elections of 2011, 2015, and 2019, from the election committee's published result notices.

The Sámi Parliament (Saamelaiskäräjät) has 21 members, chosen every four years under the Act on the Sámi Parliament
(974/1995). The whole country is one constituency. Three members come from each of four municipalities of the Sámi
homeland (Enontekiö, Inari, Sodankylä, Utsjoki), and nine more go to the candidates with the most votes after that.
The election committee (vaalilautakunta) publishes a notice for each election in the Sámi Parliament's document bank:
the voters, the ballots left out, and the votes of the elected members and their deputies. It publishes no full
candidate lists and no results for 2003, 2007, or 2023 in the files the archive could reach, so these packages hold
only what each notice prints.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "fi.saami"
FINLAND = str(uuid5(package.NAMESPACE, "polity\x1fFI"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fFIN"))
BANK = "https://dokumentit.solinum.fi/samediggi/download/?d=dokumenttipankki/vaalit/suomeksi/"
PAGE = "https://vaalit.samediggi.fi/tilastot-ja-tulokset/saamelaiskarajavaalien-tulokset/"
PUBLISHER = "Saamelaiskäräjien vaalilautakunta (Sámi Parliament election committee)"
LICENSE = "No license stated; published by the electoral authority for public use"
VOTES, CAST, INVALID = 1, 3, 5

# Each notice: the file, the day the committee confirmed the result, the voters, the ballots left out under section 35
# and the ballots rejected under section 37 of the Act, and the elected members as (municipality, name, votes), with
# the deputy of each municipality, then the nine members elected by votes alone.
HOMELAND = ("Enontekiö", "Inari", "Sodankylä", "Utsjoki")


@dataclass(frozen=True)
class Notice:
    year: int
    file: str
    confirmed: date
    voters: int
    disregarded: int
    rejected: int
    start: date
    end: date
    members: tuple[tuple[str, str, int], ...]
    deputies: tuple[tuple[str, str, int], ...]
    at_large: tuple[tuple[str, int], ...]
    drawn: str | None = None


NOTICES = (
    Notice(
        2011,
        "saamelaiskäräjävaalien_tulos_2011.pdf",
        date(2011, 10, 5),
        2720,
        114,
        7,
        date(2011, 8, 22),
        date(2011, 10, 2),
        (
            ("Enontekiö", "Näkkäläjärvi Heikki Klemetti", 142),
            ("Enontekiö", "Valkeapää Johan Nihkolas", 55),
            ("Enontekiö", "Magga Ellen Ulla–Maarit", 54),
            ("Inari", "Sanila- Aikio Tiina Juulia", 148),
            ("Inari", "Haltta Tauno", 110),
            ("Inari", "Avaskari Anu", 104),
            ("Sodankylä", "Äärelä Rauni Irja Anneli", 81),
            ("Sodankylä", "Magga-Vars Petra Biret", 76),
            ("Sodankylä", "Hetta Jouko Iisakki", 61),
            ("Utsjoki", "Nuorgam Mirva Anne Marjatta", 101),
            ("Utsjoki", "Tapiola Nilla Samuli", 95),
            ("Utsjoki", "Länsman Asko Tapani", 90),
        ),
        (
            ("Enontekiö", "Näkkäläjärvi Janne Oula", 54),
            ("Inari", "Pekkala Pekka Tapani", 66),
            ("Sodankylä", "Aikio Antti Uula Sammeli", 44),
            ("Utsjoki", "Porsanger Veikko Antero", 68),
        ),
        (
            ("Pohjanrinne Arto Juhani", 88),
            ("Paltto Heikki Yrjänä", 86),
            ("Guttorm Veikko Armas Johannes", 85),
            ("Tapiola Jouni Ilmari", 85),
            ("Heikkuri Pertti Olavi", 80),
            ("Näkkäläjärvi Pirita Susanna", 77),
            ("Morottaja Anna Margareeta", 77),
            ("Valle Pentti Viljo Heikki", 74),
            ("Musta Yrjö Oskari", 72),
        ),
    ),
    Notice(
        2015,
        "saamealiskäräjävaalien_tulos_2015.pdf",
        date(2015, 10, 7),
        3035,
        106,
        12,
        date(2015, 8, 24),
        date(2015, 10, 4),
        (
            ("Enontekiö", "Juuso Tuomas Aslak", 114),
            ("Enontekiö", "Magga Ulla-Maarit", 113),
            ("Enontekiö", "Keskitalo Pigga Päivi Kristiina", 77),
            ("Inari", "Avaskari Anu Aino-Sisko Maarit", 205),
            ("Inari", "Sanila-Aikio Tiina", 190),
            ("Inari", "Kyrö Kari", 189),
            ("Sodankylä", "Hetta Jouko Iisakki", 72),
            ("Sodankylä", "Magga Petra Biret", 64),
            ("Sodankylä", "Sara Saara Magreta", 144),
            ("Utsjoki", "Nuorgam Anne", 138),
            ("Utsjoki", "Tapiola Nilla Samuli", 94),
            ("Utsjoki", "Holmberg Aslak Niilonpoika", 83),
        ),
        (
            ("Enontekiö", "Valkeapää Nihkolas", 68),
            ("Inari", "Pekkala Pekka Tapani", 67),
            ("Utsjoki", "Länsman Asko Tapani", 79),
        ),
        (
            ("Pieski Pentti Ilmari", 128),
            ("Sanila Tanja", 119),
            ("Feodoroff Veikko Armas", 116),
            ("Paltto Heikki Yrjänä", 110),
            ("Saijets Janne", 95),
            ("Kustula Kirsti Maarit", 91),
            ("Jääskö Neeta", 89),
            ("Aikio Jouni", 88),
            ("Sujala Antti", 81),
        ),
        drawn="Sara Saara Magreta",
    ),
    Notice(
        2019,
        "saamelaiskäräjävaalien_tulos_2019.pdf",
        date(2019, 10, 2),
        2853,
        290,
        7,
        date(2019, 8, 19),
        date(2019, 9, 29),
        (
            ("Enontekiö", "Juuso Tuomas Aslak", 127),
            ("Enontekiö", "Magga Ulla-Maarit", 98),
            ("Enontekiö", "Keskitalo Pigga Päivi Kristiina", 88),
            ("Inari", "Kyrö Martti Kari Johannes", 220),
            ("Inari", "Avaskari Anu Aino-Sisko Maarit", 183),
            ("Inari", "Feodoroff Veikko Armas", 134),
            ("Sodankylä", "Alakorva Juha Petteri", 80),
            ("Sodankylä", "Hetta Karen-Anni", 78),
            ("Sodankylä", "Äärelä Jouni Kalevi", 34),
            ("Utsjoki", "Nuorgam Mirva Anne Marjatta", 167),
            ("Utsjoki", "Koivisto Anni Kaisa", 160),
            ("Utsjoki", "Länsman Asko Tapani", 145),
        ),
        (
            ("Enontekiö", "Valkeapää Niko-Mihkal", 57),
            ("Inari", "Linnamäki Teija Helinä", 61),
            ("Sodankylä", "Aikio Matti Ville Gabriel", 34),
            ("Utsjoki", "Lukkari Sammol Antti", 46),
        ),
        (
            ("Kangasniemi Inka Maarika", 115),
            ("Näkkäläjärvi Pirita Susanna", 106),
            ("Sara Saara Magreta", 88),
            ("Aikio Leo Jouni Olavi", 85),
            ("Pekkala Aslak Timo", 74),
            ("Sanila Tanja", 72),
            ("Ljetoff Tauno Juhani", 70),
            ("Porsanger Veikko Antero", 67),
            ("Seurujärvi-Kari Irja Anneli", 63),
        ),
    ),
)

METHOD = (
    "Votes of the 21 elected members and of the four deputies, and the voters and ballots left out, as the Sámi "
    "Parliament election committee's notice of the confirmed result prints them. The notice lists the twelve members "
    "elected from the four municipalities of the Sámi homeland (three each) and the nine elected by votes alone; "
    "it prints no other candidate's votes, so the other candidates are not stored. Each voter casts one vote. "
    "Ballots cast are the voters the notice counts, and invalid ballots are the ballots the committee rejected under "
    "section 37 of the Act; the notice also names the votes it left out under section 35, which are not stored. The "
    "voting period dates follow section 31 of the Act (six weeks from the Monday of the last full week of August); "
    "the notice gives only the day the committee confirmed the result. A deputy is stored with the outcome lost."
)
SYSTEM_METHOD = (
    "The country is one constituency with 21 seats. Each voter casts one vote for one candidate. The candidates "
    "with the most votes among those nominated from each of Enontekiö, Inari, Sodankylä, and Utsjoki fill three seats "
    "each, and the nine remaining seats go to the candidates with the most votes after that, wherever they live. "
    "The rule is stored as single non-transferable vote, which does not capture the municipal quota."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{n.year}" for n in NOTICES)]


def url(notice: Notice) -> str:
    return BANK + notice.file


def fetch() -> None:
    for notice in NOTICES:
        raw.fetch(SOURCE, url(notice), suffix=".pdf")
    raw.fetch(SOURCE, PAGE, suffix=".html")


def text(notice: Notice) -> str:
    path = raw.cached(SOURCE, url(notice)).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


def check(notice: Notice) -> None:
    """Every name and figure must appear in the notice, and the members must make up the 21 seats."""
    body = re.sub(r"\s+", " ", text(notice))
    names = [m[1] for m in notice.members] + [d[1] for d in notice.deputies] + [a[0] for a in notice.at_large]
    for name in names:
        if re.sub(r"\s+", " ", name) not in body:
            raise ValueError(f"{notice.year}: {name} is not in the notice")
    for figure in (notice.voters, notice.disregarded, notice.rejected):
        if str(figure) not in body:
            raise ValueError(f"{notice.year}: {figure} is not in the notice")
    if len(notice.members) + len(notice.at_large) != 21:
        raise ValueError(f"{notice.year}: {len(notice.members) + len(notice.at_large)} members, not 21")
    ordered = sorted(notice.at_large, key=lambda a: -a[1])
    if list(ordered) != list(notice.at_large):
        raise ValueError(f"{notice.year}: the nine members elected by votes are not in vote order")


def parse() -> None:
    for notice in NOTICES:
        check(notice)
    for built in (registry(), *(build(n) for n in NOTICES)):
        built.write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "The Sámi Parliament of Finland and its electoral system")
    p.external.update({FINLAND, COUNTRY})
    body = key("body", "samediggi")
    p.add("body", id=body, polity_id=FINLAND, kind="unicameral")
    p.name(body, "Sámi Parliament of Finland", "en", "official")
    p.name(body, "Saamelaiskäräjät", "fi", "native", False)
    p.name(body, "Sámediggi", "se", "native", False)
    p.name(body, "Sametinget", "sv", "official", False)
    system = key("electoral_system", "samediggi")
    p.add(
        "electoral_system",
        id=system,
        polity_id=FINLAND,
        valid_from=date(1996, 1, 1),
        rules={"formula": "sntv"},
        rules_version=1,
    )
    p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_METHOD, lang="en")
    scheme = "fi.saami"
    p.add(
        "scheme", code=scheme, polity_id=FINLAND, kind="administrative", rank=1, noun="constituency", code_system=None
    )
    unit = key("unit", "national")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=FINLAND,
        scheme_code=scheme,
        code="FI",
        area_relation="equals",
        area_id=COUNTRY,
        kind="aggregate",
    )
    return p


def build(notice: Notice) -> Package:
    year = notice.year
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Sámi Parliament election of Finland: the elected members and their votes, from the election "
        "committee's notice",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.update({FINLAND, COUNTRY})
    source = key("source", year)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=f"Saamelaiskäräjävaalien tulos {year}",
        url=PAGE,
        license=LICENSE,
        attribution="Saamelaiskäräjien vaalilautakunta",
    )
    p.artifact(raw.cached(SOURCE, url(notice)), source)
    p.artifact(raw.cached(SOURCE, PAGE), source)
    election = key("election", year)
    p.add(
        "election",
        id=election,
        polity_id=FINLAND,
        kind="general",
        date_from=notice.start,
        date_to=notice.end,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{year} Sámi Parliament election of Finland", "en", "official")
    p.name(election, f"Saamelaiskäräjävaalit {year}", "fi", "native", False)
    base = f"fi/{notice.start}-saami-parliament"
    p.add("slug", path=base, entity_id=election, is_current=True)
    contest = key("contest", year)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=key("body", "samediggi"),
        magnitude=21,
        round_number=1,
        electoral_system_id=key("electoral_system", "samediggi"),
        default_result_kind="counted",
    )
    p.name(contest, f"Saamelaiskäräjävaalit {year}", "fi", "official")
    p.name(contest, f"{year} Sámi Parliament election", "en", "official", False)
    p.add("slug", path=f"{base}/sami-parliament", entity_id=contest, is_current=True)
    members = [*(m[1:] for m in notice.members), *notice.at_large]
    listed = sum(votes for _, votes in members) + sum(d[2] for d in notice.deputies)
    valid = notice.voters - notice.disregarded - notice.rejected
    result_set = key("result_set", year)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="certified",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=round(min(listed / valid, 1.0), 2),
    )
    extra = ""
    if notice.drawn:
        extra = (
            f" Only two candidates stood in Sodankylä, so its third seat was drawn by lot at the count among the "
            f"fourth-ranked candidates of the other municipalities; {notice.drawn} won it."
        )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=METHOD
        + extra
        + f" Completeness is the votes stored divided by the voters less the ballots left out and rejected ({valid}).",
        lang="en",
    )
    unit = key("unit", "national")
    values: list[tuple[str | None, int, int]] = []
    seen: set[str] = set()
    position = 0
    entries: list[tuple[str, int, str, str | None]] = [
        *((m[1], m[2], "elected", "list_member") for m in notice.members),
        *((a[0], a[1], "elected", "list_member") for a in notice.at_large),
        *((d[1], d[2], "lost", "substitute") for d in notice.deputies),
    ]
    for name, votes, outcome, role in entries:
        if name in seen:
            continue
        seen.add(name)
        position += 1
        label = re.sub(r"\s+", " ", name)
        person = key("person", year, label)
        p.add("person", id=person, birth_precision=None)
        p.name(person, label, "fi", "official")
        option = key("option", year, label)
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
            id=key("candidacy", year, label),
            person_id=person,
            option_id=option,
            role=role,
            ballot_name=label,
            is_independent=False,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=votes)
        values.append((option, VOTES, votes))
    values.append((None, CAST, notice.voters))
    values.append((None, INVALID, notice.rejected))
    p.frame(
        "result_value",
        pl.DataFrame(values, schema=["option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            unit_id=pl.lit(unit),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p
