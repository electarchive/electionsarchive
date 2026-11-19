"""Indonesia's 2009 presidential election from Constitutional Court ruling 108-109/PHPU.B-VII/2009.

KPU's legal database holds no 2009 result decree. The Court's ruling on the two challenges restates the national
ticket votes of KPU decree 365/Kpts/KPU/2009 and the national voter counts, and prints a table of registered voters
by province. The ruling's PDF is served from s.mkri.id without a browser check.
"""

import csv
import io
import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "id.mk_pilpres"
KEY = "id.mk_pilpres/pilpres-2009"
DAY = date(2009, 7, 8)
COURT = "Mahkamah Konstitusi Republik Indonesia"
RULING = (
    "https://s.mkri.id/public/content/persidangan/putusan/Putusan%20baca%2012%20Agustus%202009.pdf",
    (
        "Putusan Mahkamah Konstitusi Nomor 108-109/PHPU.B-VII/2009 tentang Perselisihan Hasil Pemilihan Umum "
        "Presiden dan Wakil Presiden Tahun 2009"
    ),
)
TICKET_PAGE = 33
TABLE_PAGES = (340, 341)
COUNTRY = str(uuid5(package.NAMESPACE, "polity\x1fID"))
NATION = "518ec4bb-3c49-597a-9cb0-071beb1a090b"
PRESIDENCY = "97106898-58e9-58b2-afc6-1a8f2c1fef92"
UNITS = {
    "11": "4da0812d-8bc2-5935-8ba2-682d129ab18d",
    "12": "c6a19705-4ca7-53b9-9cfe-6785595da90d",
    "13": "5a471da7-bf54-5ec8-846d-629de9659a89",
    "14": "f439dc01-fc72-5edc-85de-fcbfd5222940",
    "15": "b8e07c14-c221-5a7d-8753-e5dbbec74e87",
    "16": "481033aa-a214-5d90-9f14-947c6e9f5811",
    "17": "48f70a9a-e825-5ef0-9a7a-8b32cf9edf74",
    "18": "819a427a-9d20-5084-a22c-ec21716d3979",
    "19": "883e119b-a2d6-5802-98e2-52e7bdff7116",
    "21": "595435a1-7b13-5d73-acbc-390e2f671d05",
    "31": "4ffcd5a3-e3d7-5223-a819-a39bc1ae29e6",
    "32": "0d491542-395c-5a40-9003-b2185d28ab0b",
    "33": "5d6067e7-e183-5169-aa4e-61b9f4647fe2",
    "34": "47f1c28f-4457-584a-ba0d-590487e1262e",
    "35": "ab31dca7-02fe-5166-9771-14d321385565",
    "36": "4ec48434-0524-5c8e-8d90-5e675f2775df",
    "51": "577e9a87-ac41-5092-9ae0-c8d06f715fd1",
    "52": "b69a6106-1108-5843-abca-99614867fde2",
    "53": "16bc069c-330f-58af-bd0d-6f478b0a7db6",
    "61": "84e0464a-2390-59dc-8ba3-32176491bbbe",
    "62": "77a15bf6-9e70-51a7-8c8c-6f9f8974b560",
    "63": "37a7253a-2dbd-58ee-8bba-59fb52dfa69a",
    "64+65": "e8c35ebe-5bca-51c9-9628-76d4b85c572f",
    "71": "5ba50a6f-c750-5567-8351-415c3a2a4ba6",
    "72": "e9e23951-02ce-5a11-b900-ab69d0ec3c55",
    "73": "6e332c6c-2cce-5344-9000-db3d75d73e09",
    "74": "ed721e6e-32df-5f4a-907a-749410258421",
    "75": "e7e75d8f-4754-56ea-813e-0aca3ed2a8fb",
    "76": "4eaf9c92-6f33-5522-9be3-9f481858b8f6",
    "81": "2829a9e2-8a99-549a-a10a-61f6503fdcd5",
    "82": "50efc6ea-e850-5360-a111-7891b514aca1",
    "91+93+94+95": "3c75eaab-b7df-5cf1-8320-a458d8f38472",
    "92+96": "d05463a9-939b-5664-a699-0efac8a890d5",
    "ID": "a4d0067d-0784-5dc1-938b-7f66fd629a3d",
    "LN": "e82b9d88-8e98-5a88-b459-cf06e6d0f1e7",
}
MEGAWATI = "26052a89-7895-5e6e-a9c8-f5e3597a0ea3"
PRABOWO = "969c5b49-030a-56ba-9150-a640e0cde603"
YUDHOYONO = "cb67db6e-2c7e-5e3e-8e2d-357310dd7335"
KALLA = "270c530f-8be4-50aa-967c-22daa5234085"
WIRANTO = "ce1e97cb-8961-51bb-b35a-c8d8c6c04c3c"
BOEDIONO = "boediono"
TICKETS = (
    (1, ("Hj. Megawati Soekarnoputri", MEGAWATI), ("H. Prabowo Subianto", PRABOWO), 32_548_105),
    (2, ("Dr. H. Susilo Bambang Yudhoyono", YUDHOYONO), ("Prof. Dr. Boediono", BOEDIONO), 73_874_562),
    (3, ("H.M. Jusuf Kalla", KALLA), ("H. Wiranto", WIRANTO), 15_081_814),
)
ELECTED = 2
VALID, INVALID, VOTERS, REGISTERED = 121_504_481, 6_479_144, 127_983_625, 176_375_196
# The ruling's table by province, in its row order.
TABLE = (
    "11", "12", "14", "21", "17", "13", "15", "19", "16", "18", "32", "36", "31", "33", "34", "35", "64+65", "62",
    "61", "63", "51", "52", "53", "73", "76", "72", "74", "75", "71", "81", "82", "91+93+94+95", "92+96", "LN",
)  # fmt: skip
MEASURES = {"votes": 1, "registered": 2, "valid": 4, "invalid": 5, "voters": 14}
CAVEAT = (
    "No electoral system is attached: UU 42/2008 art. 159 elects a ticket with over 50 percent of the valid votes and "
    "at least 20 percent in more than half of the provinces, else the top two meet in a second round, and the "
    "archive's rules cannot yet check the provincial condition here, because the sources print no ticket votes by "
    "province. Susilo Bambang Yudhoyono and Boediono took 60.80 percent nationally, and the Constitutional Court "
    "rejected both challenges on 12 August 2009, so no second round was held."
)
NOTE = (
    "National ticket votes as declared in KPU decree 365/Kpts/KPU/Tahun 2009 of 25 July 2009, which both applicants "
    "restate in Constitutional Court ruling 108-109/PHPU.B-VII/2009 (page 33 of the ruling's PDF); the ticket votes sum "
    "to the 121,504,481 valid votes the Court's findings state (page 340). The ruling prints ticket 2's votes as "
    "73,874,562 on page 33 and as 73.874.526 elsewhere; only the first sums to the valid votes. The findings also "
    "state the 127,983,625 voters and 6,479,144 invalid votes, which sum to the voters, and a table of the final "
    "voter list (DPT) by province and abroad (pages 340 and 341), whose rows sum to the national 176,375,196. Its "
    "column of DPT voters who voted, with the 382,540 who voted with an identity card, does not reach the national "
    "voters (127,163,035 and 382,540 against 127,983,625), so voters are loaded nationally only. The ruling prints "
    "no ticket votes by province. East Kalimantan includes North Kalimantan, and Papua and West Papua include the "
    "provinces split from them in 2022, so those three rows lie within the nation."
)


def fetch() -> None:
    raw.fetch(SOURCE, RULING[0], suffix=".pdf")


def packages() -> list[str]:
    return [KEY]


def parse() -> None:
    pilpres().write()


def emit() -> None:
    package.run("load", packages())


def _words(pdf: Path, page: int) -> list[tuple[str, float, float, float, float]]:
    out = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-tsv", str(pdf), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows = csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE)
    return [
        (
            word["text"].strip(",;."),
            float(word["left"]),
            float(word["top"]),
            float(word["left"]) + float(word["width"]),
            float(word["top"]) + float(word["height"]),
        )
        for word in rows
        if word["level"] == "5"
    ]


def _figure(text: str) -> int | None:
    return int(text.replace(".", "")) if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text) else None


Box = tuple[float, float, float, float]


def _find(words: list[tuple[str, float, float, float, float]], value: int) -> Box:
    found = [word[1:] for word in words if _figure(word[0]) == value]
    if len(found) != 1:
        raise ValueError(f"{value}: {len(found)} matches on the page, expected 1")
    return found[0]


def _table(pdf: Path) -> dict[str, tuple[int, Box, int]]:
    """Each province's DPT with its box and page, from the ruling's table."""
    out: dict[str, tuple[int, Box, int]] = {}
    for page in TABLE_PAGES:
        words = _words(pdf, page)
        for index, _, top, _, _ in [word for word in words if re.fullmatch(r"\d{1,2}", word[0]) and word[1] < 165]:
            row = sorted((word for word in words if abs(word[2] - top) < 3 and word[1] > 165), key=lambda w: w[1])
            figures = [(_figure(word[0]), word[1:]) for word in row if _figure(word[0]) and len(word[0]) >= 7]
            number = int(index)
            if figures and 1 <= number <= len(TABLE) and TABLE[number - 1] not in out:
                value, box = figures[0]
                out[TABLE[number - 1]] = (value or 0, box, page)
    return out


def pilpres() -> Package:
    p = Package(
        KEY,
        SOURCE,
        "The 2009 Indonesian presidential election nationally, with registered voters by province, from "
        "Constitutional Court ruling 108-109/PHPU.B-VII/2009",
    )
    p.external |= {COUNTRY, NATION, PRESIDENCY, MEGAWATI, PRABOWO, YUDHOYONO, KALLA, WIRANTO, *UNITS.values()}
    artifact = raw.cached(SOURCE, RULING[0])
    pdf = artifact.path
    source = p.id("source", "ruling")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=COURT,
        title=RULING[1],
        url=RULING[0],
        license="Public record: official court ruling, no license stated",
        license_url="https://www.mkri.id",
    )
    artifact_id = p.artifact(artifact, source)
    pages: dict[int, tuple[str, int, int]] = {}

    def page(number: int) -> tuple[str, int, int]:
        if number not in pages:
            image = DATA / "pages" / SOURCE / f"{artifact.sha256}-{number}.png"
            if not image.exists():
                image.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    [
                        "pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile",
                        str(pdf), str(image.with_suffix("")),
                    ],
                    check=True,
                )  # fmt: skip
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            found = p.id("page", number)
            p.add(
                "source_page",
                id=found,
                artifact_id=artifact_id,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
            pages[number] = (found, width, height)
        return pages[number]

    election = p.id("election", str(DAY))
    p.add("election", id=election, polity_id=COUNTRY, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.name(election, "2009 Indonesian presidential election", "en", "official")
    p.name(election, "Pemilihan Umum Presiden dan Wakil Presiden Tahun 2009", "id", "native", False)
    path = f"id/{DAY}-presidential"
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = p.id("contest", str(DAY), "presiden")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENCY,
        district_area_id=NATION,
        term="full",
        magnitude=1,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, "2009 Indonesian presidential election", "en", "official")
    p.name(contest, "Pemilihan Umum Presiden dan Wakil Presiden Tahun 2009", "id", "native", False)
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    p.add("note", id=p.id("note", contest, "caveat"), entity_id=contest, kind="caveat", body_md=CAVEAT, lang="en")
    result_set = p.id("result_set", str(DAY), "presiden")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="certified",
        kind="counted",
        status="active",
        source_id=source,
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=NOTE, lang="en")

    def locate(unit: str, option: str | None, measure: str, page_number: int, box: Box) -> None:
        found, width, height = page(page_number)
        x0, y0, x1, y1 = (value * 100 / 72 for value in box)
        p.add(
            "value_locator",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=MEASURES[measure],
            count_round=0,
            page_id=found,
            x0=x0 / width,
            y0=y0 / height,
            x1=x1 / width,
            y1=y1 / height,
        )

    def value(unit: str, option: str | None, measure: str, number: int, page_number: int, box: Box) -> None:
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=MEASURES[measure],
            count_round=0,
            flags=0,
            value=number,
        )
        locate(unit, option, measure, page_number, box)

    nation = UNITS["ID"]
    boediono = p.id("person", "boediono")
    p.add("person", id=boediono, gender="male")
    p.name(boediono, "Boediono", "id", "official")
    people = {BOEDIONO: boediono}
    ticket_words = _words(pdf, TICKET_PAGE)
    for number, (head, head_id), (vice, vice_id), votes in TICKETS:
        option = p.id("option", str(DAY), number)
        label = f"{head.split('. ')[-1]} and {vice.split('. ')[-1]}"
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=number,
            ballot_label=label,
        )
        for role, ballot, person in (("head", head, head_id), ("running_mate", vice, vice_id)):
            p.add(
                "candidacy",
                id=p.id("candidacy", str(DAY), number, role),
                person_id=people.get(person, person),
                option_id=option,
                role=role,
                ballot_name=ballot,
                is_independent=False,
            )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if number == ELECTED else "lost")
        value(nation, option, "votes", votes, TICKET_PAGE, _find(ticket_words, votes))
        p.add("declared", result_set_id=result_set, option_id=option, measure=1, value=votes)
    if sum(votes for *_, votes in TICKETS) != VALID:
        raise ValueError("2009: ticket votes do not sum to the valid votes")
    if VALID + INVALID != VOTERS:
        raise ValueError("2009: valid and invalid votes do not sum to the voters")
    if TICKETS[ELECTED - 1][3] * 2 <= VALID:
        raise ValueError("2009: the elected ticket did not pass 50 percent")
    words = _words(pdf, TABLE_PAGES[0])
    for measure, number in (("valid", VALID), ("invalid", INVALID), ("voters", VOTERS)):
        value(nation, None, measure, number, TABLE_PAGES[0], _find(words, number))
        p.add("declared", result_set_id=result_set, option_id=None, measure=MEASURES[measure], value=number)
    rows = _table(pdf)
    if set(rows) != set(TABLE):
        raise ValueError(f"2009: the province table lacks {sorted(set(TABLE) - set(rows))}")
    if sum(number for number, *_ in rows.values()) != REGISTERED:
        raise ValueError("2009: registered voters by province do not sum to the national figure")
    for code, (number, box, page_number) in rows.items():
        value(UNITS[code], None, "registered", number, page_number, box)
    p.add("declared", result_set_id=result_set, option_id=None, measure=MEASURES["registered"], value=REGISTERED)
    return p
