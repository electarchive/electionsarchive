"""Hungary's National Assembly election of 4 November 1945, from the Central Statistical Office's own account.

The Magyar Statisztikai Szemle of 1946 (issue 7–9, pages 72–78) prints the office's article on the members of the
National Assembly. It gives the registered voters, the ballots cast, the invalid ballots, and each party's valid
votes nationwide, and the seats each party won in each of the 16 county constituencies and on the national list.
It prints no votes below the national level, so the constituencies carry seats only. Every number stored as a
result value was read from the scanned page and keeps its page and bounding box.

The office's election volumes with returns by constituency and settlement, and any account of the election of
31 August 1947, are on hosts this pipeline could not reach.
"""

import re
import subprocess
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "hu.ksh_1945"
HUNGARY = str(uuid5(package.NAMESPACE, "polity\x1fHU"))
# The country's area, minted by hu.valasztas/geo.
TERRITORY = "59a0ba57-5c5c-5af8-9987-c96feb05b10a"
URL = "http://www.ksh.hu/statszemle_archive/all/1946/1946_07-09/1946_07-09_0072_0078.pdf"
DAY = date(1945, 11, 4)
KEY = f"{SOURCE}/{DAY}"
PATH = f"hu/{DAY}-parliamentary"
SCHEME = "hu.valasztokerulet_1945"
NATION = "hu.nation"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
# The PDF's pages 2 and 3 are the journal's pages 73 and 74.
PAGE_SIZE = (692.28, 1019.88)


class Party(NamedTuple):
    hu: str
    en: str
    short: str


PARTIES = {
    "fkgp": Party(
        "Független Kisgazda, Földmunkás és Polgári Párt",
        "Independent Smallholders, Agrarian Workers and Civic Party",
        "FKGP",
    ),
    "mkp": Party("Magyar Kommunista Párt", "Hungarian Communist Party", "MKP"),
    "szdp": Party("Szociáldemokrata Párt", "Social Democratic Party", "SZDP"),
    "npp": Party("Nemzeti Parasztpárt", "National Peasant Party", "NPP"),
    "pdp": Party("Polgári Demokrata Párt", "Civic Democratic Party", "PDP"),
    "mrp": Party("Magyar Radikális Párt", "Hungarian Radical Party", "MRP"),
}

# Valid votes nationwide, in the order and with the boxes (in points, on PDF page 3) of the printed table.
NATIONAL_VOTES: list[tuple[str, int, tuple[float, float, float, float]]] = [
    ("fkgp", 2_691_384, (276.0, 346.38, 326.16, 351.89)),
    ("szdp", 822_666, (286.2, 375.32, 326.52, 380.33)),
    ("mkp", 801_341, (286.2, 403.48, 326.52, 409.49)),
    ("npp", 323_571, (286.56, 431.56, 326.88, 437.57)),
    ("pdp", 76_331, (293.4, 473.68, 327.6, 479.69)),
    ("mrp", 5_762, (300.24, 501.04, 328.32, 507.05)),
]
# (measure, value, PDF page, box in points)
TOTALS: list[tuple[int, int, int, tuple[float, float, float, float]]] = [
    (REGISTERED, 5_164_661, 2, (441.72, 332.12, 491.4, 337.13)),
    (CAST, 4_765_212, 3, (198.0, 123.18, 257.76, 128.69)),
    (INVALID, 44_157, 3, (93.96, 138.3, 137.88, 143.81)),
    (VALID, 4_721_055, 3, (279.0, 531.42, 329.76, 536.93)),
]

SEAT_COLUMNS = ("fkgp", "mkp", "szdp", "npp", "pdp")
# Seats by constituency, as the table on journal page 74 prints them: number, name, and seats of FKGP, MKP, SZDP,
# NPP, and PDP. Number 0 is the national list.
SEATS: list[tuple[int, str, tuple[int, int, int, int, int]]] = [
    (1, "Baranya–Tolna", (15, 3, 3, 1, 0)),
    (2, "Somogy", (12, 1, 1, 1, 0)),
    (3, "Zala", (12, 1, 1, 0, 0)),
    (4, "Vas", (8, 1, 1, 0, 0)),
    (5, "Győr–Moson–Sopron", (9, 2, 3, 1, 0)),
    (6, "Veszprém", (7, 1, 1, 0, 0)),
    (7, "Fejér–Komárom–Esztergom", (10, 3, 4, 1, 0)),
    (8, "Nagy-Budapest", (33, 14, 18, 1, 2)),
    (9, "Pest-Pilis-Solt-Kiskun–Bács-Bodrog", (31, 7, 7, 3, 0)),
    (10, "Csongrád–Csanád", (12, 5, 4, 1, 0)),
    (11, "Békés", (6, 4, 3, 1, 0)),
    (12, "Jász-Nagykun-Szolnok", (9, 4, 2, 1, 0)),
    (13, "Hajdú–Bihar", (10, 3, 3, 3, 0)),
    (14, "Szabolcs–Szatmár–Bereg", (14, 2, 2, 3, 0)),
    (15, "Borsod–Gömör–Zemplén–Abaúj", (15, 5, 4, 1, 0)),
    (16, "Heves–Nógrád–Hont", (13, 5, 3, 2, 0)),
    (0, "Országos lajstrom", (29, 9, 9, 3, 0)),
]
# The printed totals of each constituency, and of each party across all of them.
SEAT_TOTALS = {1: 22, 2: 15, 3: 14, 4: 10, 5: 15, 6: 9, 7: 18, 8: 68, 9: 48, 10: 22, 11: 14, 12: 16, 13: 19}
SEAT_TOTALS |= {14: 21, 15: 25, 16: 23, 0: 50}
# The Összesen row, less the 12 non-party members the Assembly chose into its ranks, who were not elected.
PARTY_SEATS = {"fkgp": 245, "mkp": 70, "szdp": 69, "npp": 23, "pdp": 2}
CO_OPTED = 12

METHOD = (
    "The Central Statistical Office's account of the National Assembly election of 4 November 1945, in the Magyar "
    "Statisztikai Szemle of 1946 (issue 7–9, pages 73 and 74). Voters cast one vote for a party list in one of 16 "
    "constituencies, each made up of one or more counties with their towns, and 50 further seats went to the "
    "parties' national lists. The article prints the registered voters, ballots cast, invalid ballots, and each "
    "party's valid votes for the whole country only; they are stored on the national list, which is the contest "
    "that covers the whole country. The Magyar Radikális Párt won no seat. Every vote figure was read from the "
    "scanned page and keeps its page and bounding box. The 12 non-party members the Assembly chose into its ranks "
    "after the election, bringing it to 421, were not elected and are not stored."
)
DISTRICT_METHOD = (
    "Seats each party won in the constituency, from the table of members by party and constituency in the Central "
    "Statistical Office's article in the Magyar Statisztikai Szemle of 1946 (issue 7–9, page 74). The article "
    "prints votes for the whole country only, so the constituency carries seats and no votes."
)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", KEY]


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    validate()
    geo().write()
    registry().write()
    election().write()


def emit() -> None:
    package.run("load", packages())


def validate() -> None:
    """Check the transcription against every total the article prints."""
    votes = sum(value for _, value, _ in NATIONAL_VOTES)
    totals = {measure: value for measure, value, _, _ in TOTALS}
    if votes != totals[VALID]:
        raise ValueError(f"party votes add up to {votes:,}, not the printed {totals[VALID]:,}")
    if totals[VALID] + totals[INVALID] != totals[CAST]:
        raise ValueError("valid and invalid ballots do not add up to the ballots cast")
    for number, name, seats in SEATS:
        if sum(seats) != SEAT_TOTALS[number]:
            raise ValueError(f"{name}: seats add up to {sum(seats)}, not the printed {SEAT_TOTALS[number]}")
    for column, party in enumerate(SEAT_COLUMNS):
        won = sum(seats[column] for _, _, seats in SEATS)
        if won != PARTY_SEATS[party]:
            raise ValueError(f"{party}: seats add up to {won}, not the printed {PARTY_SEATS[party]}")
    if sum(PARTY_SEATS.values()) + CO_OPTED != 421:
        raise ValueError("the parties' seats and the co-opted members do not make the printed 421")


def slug(text: str) -> str:
    return changeset.slug(text)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(number: int) -> str:
    return key("area", SCHEME, number)


def party(code: str) -> str:
    return key("party", code)


BODY = key("body", "nemzetgyules-1945")


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The 16 constituencies of Hungary's National Assembly election of 1945, and a national scheme for "
        "country-wide results",
    )
    p.external |= {HUNGARY, TERRITORY}
    p.add("scheme", code=NATION, polity_id=HUNGARY, kind="administrative", rank=0, noun="country")
    p.add("scheme", code=SCHEME, polity_id=HUNGARY, kind="electoral_district", rank=1, noun="constituency")
    for number, name, _ in SEATS:
        if number == 0:
            continue
        id = area(number)
        p.add("area", id=id, scheme_code=SCHEME)
        p.add("area_code", area_id=id, code=f"1945:{number:02d}")
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, f"{name} választókerület", "hu", "official")
        p.name(id, f"{name} constituency", "en", "english", False)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Hungary's National Assembly of 1945 and the parties that ran in its election",
    )
    p.external.add(HUNGARY)
    p.add("body", id=BODY, polity_id=HUNGARY, kind="unicameral")
    p.name(BODY, "Nemzetgyűlés", "hu", "official")
    p.name(BODY, "National Assembly of Hungary (1945–1947)", "en", "english", False)
    for code, info in PARTIES.items():
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=HUNGARY)
        p.name(id, info.hu, "hu", "official")
        p.name(id, info.en, "en", "english", False)
        p.name(id, info.short, "hu", "abbreviation", False)
    return p


def page_sizes(path: Path) -> dict[int, tuple[float, float]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "99", str(path)], capture_output=True, text=True, check=True)
    found = (re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line) for line in out.stdout.splitlines())
    return {int(m[1]): (float(m[2]), float(m[3])) for m in found if m}


def election() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "Hungary's National Assembly election of 4 November 1945: votes by party nationwide and seats by "
        "constituency, digitized from the Central Statistical Office's article",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {HUNGARY, TERRITORY}
    source = p.id("source")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Magyar Központi Statisztikai Hivatal",
        title="A nemzetgyűlési képviselők statisztikája (Magyar Statisztikai Szemle, 1946, 7–9. szám, 72–78. o.)",
        url=URL,
        license="No license stated: official statistical publication of 1946; facts published with attribution",
        attribution="Magyar Központi Statisztikai Hivatal, Magyar Statisztikai Szemle, XXIV. évfolyam (1946)",
    )
    stored = raw.cached(SOURCE, URL)
    artifact = p.artifact(stored, source)
    sizes = page_sizes(stored.path)
    pages: dict[int, str] = {}
    for number in (2, 3):
        width, height = sizes[number]
        if (width, height) != PAGE_SIZE:
            raise ValueError(f"page {number} is {width} x {height} points, not the {PAGE_SIZE} the boxes assume")
        pages[number] = p.id("page", number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
            width=round(width * 100 / 72),
            height=round(height * 100 / 72),
        )

    election_id = p.id("election", DAY)
    p.add(
        "election",
        id=election_id,
        polity_id=HUNGARY,
        kind="general",
        date_from=DAY,
        date_to=DAY,
        date_precision="day",
        status="held",
    )
    p.name(election_id, "1945 Hungarian parliamentary election", "en", "english")
    p.name(election_id, "1945. évi nemzetgyűlési választás", "hu", "official", False)
    p.add("slug", path=PATH, entity_id=election_id, is_current=True)

    def contest(number: int, name: str, seats: tuple[int, ...]) -> tuple[str, str, dict[str, str]]:
        national = number == 0
        id = p.id("contest", number)
        p.add(
            "contest",
            id=id,
            election_id=election_id,
            kind="list_allocation",
            body_id=BODY,
            district_area_id=TERRITORY if national else area(number),
            magnitude=sum(seats),
            round_number=1,
            default_result_kind="counted",
        )
        p.name(id, name if national else f"{name} választókerület", "hu", "official")
        p.name(id, "National list" if national else f"{name} constituency", "en", "english", False)
        p.add("slug", path=f"{PATH}/{slug('orszagos-lista' if national else name)}", entity_id=id, is_current=True)
        result_set = p.id("result_set", number)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0 if national else 0.0,
        )
        p.add(
            "note",
            id=p.id("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD if national else DISTRICT_METHOD,
            lang="en",
        )
        codes = list(PARTIES) if national else [code for code, won in zip(SEAT_COLUMNS, seats, strict=True) if won]
        options: dict[str, str] = {}
        for position, code in enumerate(codes, 1):
            option = p.id("option", number, code)
            options[code] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=id,
                kind="list",
                party_id=party(code),
                ballot_position=position if national else None,
                ballot_label=PARTIES[code].hu,
            )
        for code, won in zip(SEAT_COLUMNS, seats, strict=True):
            if won:
                p.add("outcome", result_set_id=result_set, option_id=options[code], kind="won", seats=won)
        return id, result_set, options

    national_set = ""
    national_options: dict[str, str] = {}
    for number, name, seats in SEATS:
        _, result_set, options = contest(number, name, seats)
        if number == 0:
            national_set, national_options = result_set, options

    unit = p.id("unit", "national")
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=HUNGARY,
        scheme_code=NATION,
        code=f"{DAY}:national",
        area_relation="equals",
        area_id=TERRITORY,
        kind="standard",
    )
    p.name(unit, "Magyarország", "hu", "official")
    p.name(unit, "Hungary", "en", "english", False)

    width, height = PAGE_SIZE

    def box(points: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = points
        return (round(x0 / width, 4), round(y0 / height, 4), round(x1 / width, 4), round(y1 / height, 4))

    values: list[tuple[str | None, int, int]] = []
    locators: list[tuple[str | None, int, str, float, float, float, float]] = []
    for code, value, points in NATIONAL_VOTES:
        values.append((national_options[code], VOTES, value))
        locators.append((national_options[code], VOTES, pages[3], *box(points)))
    for measure, value, page, points in TOTALS:
        values.append((None, measure, value))
        locators.append((None, measure, pages[page], *box(points)))
    p.frame(
        "result_value",
        pl.DataFrame(values, schema=["option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(national_set),
            unit_id=pl.lit(unit),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators, schema=["option_id", "measure", "page_id", "x0", "y0", "x1", "y1"], orient="row"
        ).with_columns(
            result_set_id=pl.lit(national_set),
            unit_id=pl.lit(unit),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p
