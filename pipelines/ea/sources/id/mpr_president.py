import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from typing import TypedDict
from uuid import UUID

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "id.mpr_president"
VERSION = "1"
URL = (
    "https://www.mpr.go.id/dokumen/risalah/file/24%20Risalah%20Perubahan%20Undang-Undang%20Dasar%20Negara%20Republik"
    "%20Indonesia%20Tahun%201945%20Tahun%20Sidang%201999.pdf"
)
TITLE = "Risalah Perubahan Undang-Undang Dasar Negara Republik Indonesia Tahun 1945, Tahun Sidang 1999"
PUBLISHER = "Sekretariat Jenderal Majelis Permusyawaratan Rakyat Republik Indonesia"
MEASURES = {"votes": 1, "ballots": 3, "blank": 6, "present": 20}
METHODOLOGY = (
    "Under Article 6(2) of the 1945 Constitution as it stood in 1999, the People's Consultative Assembly (MPR) "
    "elected the President and the Vice President by the most votes. MPR decree VI/MPR/1999 set the procedure: "
    "fractions nominated candidates in writing, the MPR leadership checked their papers, and with more than one "
    "candidate the members voted by secret ballot, in stages while more than two remained."
)
NOTE = (
    "Counts as the chair announced them, from the minutes of the MPR's 13th plenary (20 October 1999) for the "
    "President and its 15th plenary (21 October 1999) for the Vice President, reprinted in the MPR Secretariat "
    "General's Risalah for the 1999 session year. Members present are the attendance the minutes record for each "
    "plenary. The minutes list abstentions and no invalid ballots; the President's table gives no total, and the "
    "Vice President's gives 685. Candidates who withdrew after their papers were accepted received no votes."
)


class Vote(TypedDict):
    office: str
    day: date
    path: str
    slug: str
    english: str
    native: str
    attendance: tuple[int, int]
    page: int
    rows: list[tuple[str, str, str, str | None, int | None, str]]
    totals: list[tuple[str, str, int]]
    tenure: tuple[date, date, str]


VOTES: list[Vote] = [
    {
        "office": "presiden",
        "day": date(1999, 10, 20),
        "path": "id/1999-10-20-presidential",
        "slug": "president",
        "english": "1999 Indonesian presidential election",
        "native": "Pemilihan Presiden Republik Indonesia 1999",
        "attendance": (845, 694),
        "page": 850,
        "rows": [
            ("Q76167", "Abdurrahman Wahid", "KH. Abdurahman Wahid", "Abdurahman", 373, "elected"),
            ("Q76179", "Megawati Soekarnoputri", "Megawati Soekarnoputri", "Megawati", 313, "lost"),
            ("Q8061915", "Yusril Ihza Mahendra", "Prof. Dr. Yusril Ihza Mahendra", None, None, "withdrew"),
        ],
        "totals": [("blank", "Abstain", 5)],
        "tenure": (date(1999, 10, 20), date(2001, 7, 23), "removed"),
    },
    {
        "office": "wakil-presiden",
        "day": date(1999, 10, 21),
        "path": "id/1999-10-21-vice-presidential",
        "slug": "vice-president",
        "english": "1999 Indonesian vice presidential election",
        "native": "Pemilihan Wakil Presiden Republik Indonesia 1999",
        "attendance": (857, 685),
        "page": 859,
        "rows": [
            ("Q76179", "Megawati Soekarnoputri", "Megawati Soekarno Putri", "Megawati", 396, "elected"),
            ("Q76344", "Hamzah Haz", "Dr. H. Hamzah Haz", "Haz", 284, "lost"),
            ("Q2581168", "Akbar Tanjung", "Ir. Akbar Tanjung", None, None, "withdrew"),
            ("Q731740", "Wiranto", "H. Wiranto, S.I.P., S.H.", None, None, "withdrew"),
        ],
        "totals": [("blank", "Abstain", 5), ("ballots", "Jumlah", 685)],
        "tenure": (date(1999, 10, 21), date(2001, 7, 23), "elevated"),
    },
]
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
SIZE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)"')


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def locate(path: Path, page: int, anchor: str, value: int) -> tuple[float, float, float, float]:
    xml = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-bbox", str(path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    match = SIZE.search(xml)
    if match is None:
        raise ValueError(f"page {page}: no size")
    width, height = (float(group) for group in match.groups())
    words = [(float(a), float(b), float(c), float(d), text) for a, b, c, d, text in WORD.findall(xml)]
    found = [
        (x0, y0, x1, y1)
        for x0, y0, x1, y1, text in words
        if text == str(value)
        and any(
            other == anchor and right < x0 and abs((top + bottom) / 2 - (y0 + y1) / 2) < 5
            for _, top, right, bottom, other in words
        )
    ]
    if len(found) != 1:
        raise ValueError(f"page {page}: {len(found)} matches for {anchor} {value}")
    x0, y0, x1, y1 = found[0]
    return x0 / width, y0 / height, x1 / width, y1 / height


def emit() -> Path:
    cached = raw.cached(SOURCE, URL)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Indonesia's 1999 elections of the President and the Vice President by the People's Consultative "
                "Assembly, from the MPR's minutes"
            ),
            polity="ID",
        )

        def name(entity: UUID, text: str, lang: str = "id", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        indonesia = cs.polity("ID")
        nation = cs.area("id.nation", "ID")
        assembly = cs.key("id.body", "mpr")
        offices = {key: cs.key("id.office", key) for key in ("presiden", "wakil-presiden")}
        if assembly in cs.minted or any(office in cs.minted for office in offices.values()):
            raise ValueError("publish id.kpu before id.mpr_president")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license="Public record: official minutes of the MPR, no license stated",
            license_url="https://www.mpr.go.id/risalah",
        )
        artifact = cs.artifact(cached, source)
        pages: dict[int, UUID] = {}

        def page(number: int) -> UUID:
            if number not in pages:
                image = DATA / "pages" / SOURCE / f"{cached.sha256}-{number}.png"
                if not image.exists():
                    image.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        [
                            "pdftoppm",
                            "-r",
                            "100",
                            "-png",
                            "-f",
                            str(number),
                            "-l",
                            str(number),
                            "-singlefile",
                            str(cached.path),
                            str(image.with_suffix("")),
                        ],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                pages[number] = cs.page(artifact, number)
                cs.add(
                    "source_page",
                    id=pages[number],
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            return pages[number]

        cs.add("scheme", code="id.assembly", polity_id=indonesia, kind="electoral_district", rank=0, noun="assembly")
        unit = cs.unit("id.assembly", "mpr")
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=indonesia,
            scheme_code="id.assembly",
            code="mpr",
            area_relation="unlocated",
            kind="aggregate",
        )
        name(unit, "Majelis Permusyawaratan Rakyat dalam Rapat Paripurna")
        name(unit, "People's Consultative Assembly in plenary", "en", "english")
        system = cs.key("id.electoral_system", "mpr:presiden")
        cs.add("electoral_system", id=system, polity_id=indonesia, rules={"formula": "fptp"}, rules_version=1)
        name(system, "Election of the President and Vice President by the MPR", "en", "english")
        cs.add("note", entity_id=system, kind="methodology", body_md=METHODOLOGY)

        persons: dict[str, UUID] = {}
        for vote in VOTES:
            for item, common, *_ in vote["rows"]:
                if item not in persons:
                    persons[item] = cs.key("wikidata", item)
                    if persons[item] in cs.minted:
                        cs.add("person", id=persons[item])
                        name(persons[item], common)

        for vote in VOTES:
            day = vote["day"]
            election = cs.key("id.election", str(day))
            cs.add(
                "election",
                id=election,
                polity_id=indonesia,
                kind="indirect",
                date_from=day,
                date_to=day,
                date_precision="day",
                status="held",
            )
            name(election, vote["english"], "en", "english")
            name(election, vote["native"])
            cs.add("slug", path=vote["path"], entity_id=election)
            contest_key = f"{day}:{vote['office']}"
            contest = cs.key("id.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=offices[vote["office"]],
                body_id=assembly,
                district_area_id=nation,
                term="full",
                magnitude=1,
                round_number=1,
                previous_round_id=None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, vote["english"], "en", "english")
            name(contest, vote["native"])
            cs.add("slug", path=f"{vote['path']}/{vote['slug']}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)

            def value(option: UUID | None, measure: str, number: int, at: int, anchor: str) -> None:
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": option,
                    "channel": 0,
                    "measure": MEASURES[measure],
                    "count_round": 0,
                }
                cs.add("result_value", **fact, flags=0, value=number)
                x0, y0, x1, y1 = locate(cached.path, at, anchor, number)
                cs.add("value_locator", **fact, page_id=page(at), source_id=None, x0=x0, y0=y0, x1=x1, y1=y1)

            value(None, "present", vote["attendance"][1], vote["attendance"][0], "Hadir")
            for measure, anchor, number in vote["totals"]:
                value(None, measure, number, vote["page"], anchor)
            for item, _, ballot, anchor, votes, outcome in vote["rows"]:
                option = cs.key("id.option", f"{contest_key}:{item}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None,
                    ballot_position=None,
                    ballot_label=ballot,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("id.candidacy", f"{contest_key}:{item}"),
                    person_id=persons[item],
                    option_id=option,
                    role="head",
                    ballot_name=ballot,
                    is_independent=False,
                )
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome, seats=None)
                if votes is not None and anchor is not None:
                    value(option, "votes", votes, vote["page"], anchor)
                if outcome == "elected":
                    start, end, via = vote["tenure"]
                    cs.add(
                        "tenure",
                        id=cs.key(f"{SOURCE}.tenure", contest_key),
                        person_id=persons[item],
                        office_id=offices[vote["office"]],
                        from_date=start,
                        to_date=end,
                        acquired_via="elected",
                        ended_via=via,
                        contest_id=contest,
                    )
        return cs.write()
