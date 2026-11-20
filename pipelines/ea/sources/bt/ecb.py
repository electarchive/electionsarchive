"""Bhutan: the Election Commission of Bhutan's results for the National Assembly and the National Council.

The Commission publishes each election as a PDF table (and, for the 2018 general election, a web page naming the
members elected). The National Assembly tables report each constituency (demkhong), and the National Council tables
report each dzongkhag, with votes counted on the electronic voting machines (EVM) and by post."""

import json
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid5

import numpy as np
from PIL import Image

from ea import package, raw
from ea.package import Package
from ea.raw import Artifact
from ea.sources.bt import tables
from ea.sources.bt.tables import DZONGKHAGS, Row, Seat

SOURCE = "bt.ecb"
BASE = "https://ecb.bt"
UPLOADS = f"{BASE}/wp-content/uploads"
BHUTAN = str(uuid5(package.NAMESPACE, "polity\x1fBT"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fBTN"))
EVM, POSTAL = 1, 3
VOTES, REGISTERED = 1, 2

FILES = {
    "na2008": f"{UPLOADS}/2026/06/NA-ResultsFinal-2008.pdf",
    "na2013p": f"{UPLOADS}/2026/04/result.pdf",
    "na2013g": f"{UPLOADS}/2026/06/result.pdf",
    "na2018p": f"{UPLOADS}/2026/06/demkhongwise.pdf",
    "na2018g": f"{UPLOADS}/2026/06/Annexure-2.pdf",
    "na2018press": f"{UPLOADS}/2026/06/NA-Press.pdf",
    "na2018elected": f"{BASE}/wp-json/wp/v2/posts/13468?_fields=id,link,title,content",
    "na2023turnout": f"{UPLOADS}/2026/06/NA-Primary-Round-Overall-Voter-Turnout-2023-2024.pdf",
    "na2023p": f"{UPLOADS}/2026/07/Copy-of-National-Assembly-Result-Primary-Round-Demkhong-Wise-Result-30062026.pdf",
    "nc2007": f"{UPLOADS}/2026/06/NC-FinalResults-2007.pdf",
    "nc2013": f"{UPLOADS}/2026/06/NCFinalResult2013.pdf",
    "nc2023": f"{UPLOADS}/2026/06/FinalResult_NC2023.pdf",
    # The Commission's Election Statistics 2018 book, as the Internet Archive captured it from the Commission's site.
    "nc2018": "https://web.archive.org/web/20230630174627id_/https://www.ecb.bt/rnp/PE2018.pdf",
}
PRESS_2024 = "https://web.archive.org/web/20241212202143id_/https://www.ecb.bt/wp-content/uploads/2024/01/GE-Results-Eng-10012024.pdf"
BROWSER_2024 = "browser://20261117-210702-144716"
# The 2024 general-election results, one scanned page for each dzongkhag, in the Commission's dzongkhag order.
PAGES_2024 = {
    "Bumthang": "https://web.archive.org/web/20241222201610id_/https://www.ecb.bt/wp-content/uploads/2024/01/Bumthang.pdf",
    **{name: f"{BROWSER_2024}/{name.replace(' ', '-')}.pdf" for name in DZONGKHAGS if name != "Bumthang"},
}
PARTY_NAMES = {
    "BKP": "Bhutan Kuen-Nyam Party",
    "BTP": "Bhutan Tendrel Party",
    "DCT": "Druk Chirwang Tshogpa",
    "DNT": "Druk Nyamrup Tshogpa",
    "DPT": "Druk Phuensum Tshogpa",
    "DTT": "Druk Thuendrel Tshogpa",
    "PDP": "People's Democratic Party",
}
KNOWN = {("Samir Giri", 771, 424, 1029)}


@dataclass(frozen=True)
class Election:
    day: date
    body: str
    stage: str
    names: tuple[str, str]
    last_day: date | None = None


ELECTIONS = (
    Election(date(2007, 12, 31), "nc", "general", ("2007-08 National Council election", "nc"), date(2008, 1, 23)),
    Election(date(2008, 3, 24), "na", "general", ("2008 National Assembly general election", "na")),
    Election(date(2013, 4, 23), "nc", "general", ("2013 National Council election", "nc")),
    Election(date(2013, 5, 31), "na", "primary", ("2013 National Assembly primary round", "na-primary")),
    Election(date(2013, 7, 13), "na", "general", ("2013 National Assembly general election", "na")),
    Election(date(2018, 4, 20), "nc", "general", ("2018 National Council election", "nc")),
    Election(date(2018, 9, 15), "na", "primary", ("2018 National Assembly primary round", "na-primary")),
    Election(date(2018, 10, 18), "na", "general", ("2018 National Assembly general election", "na")),
    Election(date(2023, 4, 20), "nc", "general", ("2023 National Council election", "nc")),
    Election(date(2023, 11, 30), "na", "primary", ("2023 National Assembly primary round", "na-primary")),
    Election(date(2024, 1, 9), "na", "general", ("2024 National Assembly general election", "na")),
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECB = key("source", "ecb")
SYSTEM_FPTP = key("electoral_system", "fptp")
SYSTEM_PRIMARY = key("electoral_system", "primary")
NA_BODY = key("body", "national_assembly")
NC_BODY = key("body", "national_council")


def party(code: str) -> str:
    return key("party", code)


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{e.day}" for e in ELECTIONS)]


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(
            SOURCE, url, suffix=".json" if "wp-json" in url else ".pdf"
        )  # the 2018 book comes from the Internet Archive
    # The 2024 pages came from copies of the Commission's files, saved outside the fetcher.
    for url in (PRESS_2024, PAGES_2024["Bumthang"]):
        raw.fetch(SOURCE, url, suffix=".pdf")


def path(name: str) -> Path:
    return raw.DATA / raw.cached(SOURCE, FILES[name]).storage_key


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse() -> None:
    geo().write()
    registry().write()
    for election in ELECTIONS:
        build(election).write()
    print(json.dumps(TOTALS, indent=1))


def emit() -> None:
    package.run("load", packages())


def constituencies() -> list[tuple[int, str]]:
    """The 47 constituencies of 2018 in dzongkhag order, each with its dzongkhag's index."""
    groups, _ = tables.na2018(path("na2018g"))
    found = [
        (tables.dzongkhag_index(name), seat.name)
        for name, seats in sorted(groups, key=lambda group: tables.dzongkhag_index(group[0]))
        for seat in seats
    ]
    if len(found) != 47:
        raise ValueError(f"expected 47 constituencies, found {len(found)}")
    return found


def dzongkhag_area(index: int) -> str:
    return area("bt.dzongkhag", f"{index + 1:02d}")


def seat_codes(seats: list[tuple[int, str]]) -> list[str]:
    seen: dict[int, int] = {}
    codes = []
    for index, _ in seats:
        seen[index] = seen.get(index, 0) + 1
        codes.append(f"{index + 1:02d}.{seen[index]}")
    return codes


def demkhong_name(text: str) -> str:
    return clean(text.replace("_ ", "_").replace("- ", "-").replace(" Demkong", "").replace("Demkong", ""))


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Bhutan's 20 dzongkhags and the 47 National Assembly constituencies (demkhongs) of 2018, as the Election "
        "Commission of Bhutan lists them",
    )
    p.external |= {BHUTAN, COUNTRY}
    p.add(
        "source",
        id=ECB,
        kind="official_body",
        publisher="Election Commission of Bhutan",
        title="Election results published on ecb.bt",
        url="https://ecb.bt/",
        license="No license stated; published by the Election Commission of Bhutan",
        attribution="Election Commission of Bhutan",
    )
    for code, rank, kind, noun in (
        ("bt.dzongkhag", 1, "administrative", "dzongkhag"),
        ("bt.demkhong", 2, "electoral_district", "demkhong"),
    ):
        p.add("scheme", code=code, polity_id=BHUTAN, kind=kind, rank=rank, noun=noun, code_system=None)
    for index, name in enumerate(DZONGKHAGS):
        id = dzongkhag_area(index)
        p.add("area", id=id, scheme_code="bt.dzongkhag")
        p.add("area_code", area_id=id, code=f"{index + 1:02d}")
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "en", "official")
    seats = constituencies()
    for (index, name), code in zip(seats, seat_codes(seats), strict=True):
        id = area("bt.demkhong", code)
        p.add("area", id=id, scheme_code="bt.demkhong")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=dzongkhag_area(index))
        p.name(id, demkhong_name(name), "en", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Bhutan's parties, National Assembly, National Council, and electoral systems, with the Election Commission's "
        "result files",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {BHUTAN}
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), ECB)
    p.add("body", id=NA_BODY, polity_id=BHUTAN, kind="lower_house")
    p.name(NA_BODY, "National Assembly", "en", "official")
    p.add("body", id=NC_BODY, polity_id=BHUTAN, kind="upper_house")
    p.name(NC_BODY, "National Council", "en", "official")
    systems = (
        (
            SYSTEM_FPTP,
            date(2007, 12, 31),
            {"formula": "fptp"},
            (
                "One member for each constituency (National Assembly) or dzongkhag (National Council), elected by "
                "the most votes. A National Council candidate with no opponent stands on a yes or no vote."
            ),
        ),
        (
            SYSTEM_PRIMARY,
            date(2008, 3, 24),
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "National Assembly primary round: every registered party stands in all 47 constituencies, and the "
                "two parties with the most votes nationwide advance to the general election. No seats are filled "
                "in the primary round."
            ),
        ),
    )
    for id, since, rules, method in systems:
        p.add(
            "electoral_system", id=id, polity_id=BHUTAN, valid_from=since, valid_to=None, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for code, name in PARTY_NAMES.items():
        id = party(code)
        p.add("party", id=id, kind="party", polity_id=BHUTAN)
        p.add("external_id", entity_id=id, scheme="bt.ecb.party", value=code)
        p.name(id, name, "en", "official")
        p.name(id, code, "en", "abbreviation", False)
    return p


METHOD = (
    "Counted by the Election Commission of Bhutan and printed as a table of votes for each {unit}. Votes cast on the "
    "electronic voting machines at polling stations are stored on the election-day channel, and postal ballots on the "
    "postal channel. The table gives no counts of invalid or blank ballots, so none are stored."
)


@dataclass(frozen=True)
class Units:
    evm: str
    postal: str | None
    total: str | None


def seat_unit(
    p: Package, day: date, scheme: str, code: str, parent_area: str, equals: bool, *, channels: bool, roll: bool
) -> Units:
    """A seat's reporting units. Votes counted on the machines and votes cast by post are separate units, because a
    unit holds one value for each option and measure; the seat's total registered voters sit on a third unit."""

    def add(suffix: str, kind: str, relation: str) -> str:
        tag = f"{day}:{code}" + (f":{suffix}" if suffix else "")
        unit = key("unit", tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BHUTAN,
            scheme_code=scheme,
            code=tag,
            area_relation=relation,
            area_id=parent_area,
            kind=kind,
        )
        return unit

    if not channels:
        return Units(add("", "standard", "equals" if equals else "within"), None, None)
    return Units(
        add("evm", "standard", "within"),
        add("postal", "postal_district", "within"),
        add("", "aggregate", "equals" if equals else "within") if roll else None,
    )


def name_units(p: Package, units: Units, label: str) -> None:
    if units.postal is None:
        p.name(units.evm, label, "en", "official")
        return
    p.name(units.evm, f"{label}, voting machines", "en", "official")
    p.name(units.postal, f"{label}, postal ballots", "en", "official")
    if units.total is not None:
        p.name(units.total, label, "en", "official")


def value(p: Package, result_set: str, unit: str, option: str | None, measure: int, number: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=number,
    )


def split(p: Package, result_set: str, units: Units, option: str | None, row: Row) -> None:
    if units.postal is None:
        value(p, result_set, units.evm, option, VOTES, row.total)
        return
    if row.evm is None or row.postal is None:
        raise ValueError(f"{row.candidate or row.party}: no split of the votes")
    value(p, result_set, units.evm, option, VOTES, row.evm)
    if row.evm + row.postal != row.total:
        # The Commission's 2023 National Council table prints 771 and 424 for one candidate with a total of 1,029; the
        # 424 repeats the previous candidate's postal count. Only the machine count is kept for that candidate.
        if (row.candidate, row.evm, row.postal, row.total) not in KNOWN:
            raise ValueError(f"{row.candidate or row.party}: {row.evm} and {row.postal} do not add up to {row.total}")
        return
    value(p, result_set, units.postal, option, VOTES, row.postal)


def roll(p: Package, result_set: str, units: Units, seat: Seat) -> None:
    if seat.registered is not None and units.total is not None:
        value(p, result_set, units.total, None, REGISTERED, seat.registered)
    elif seat.registered is not None and units.postal is None:
        value(p, result_set, units.evm, None, REGISTERED, seat.registered)
    elif seat.evm_voters is not None and seat.postal_voters is not None and units.postal is not None:
        value(p, result_set, units.evm, None, REGISTERED, seat.evm_voters)
        value(p, result_set, units.postal, None, REGISTERED, seat.postal_voters)


def build(election: Election) -> Package:
    day = election.day
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {election.names[0]} by "
        + ("dzongkhag" if election.body == "nc" else "constituency")
        + " from the Election Commission of Bhutan",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {BHUTAN, COUNTRY}
    id = key("election", day)
    p.add(
        "election",
        id=id,
        polity_id=BHUTAN,
        kind="general",
        date_from=day,
        date_to=election.last_day or day,
        status="held",
    )
    p.name(id, election.names[0], "en", "official")
    path_ = f"bt/{day}-{election.names[1]}"
    p.add("slug", path=path_, entity_id=id, is_current=True)
    if election.body == "nc":
        council(p, election, id, path_)
    elif election.stage == "primary":
        primary(p, election, id, path_)
    elif day == date(2024, 1, 9):
        assembly2024(p, election, id, path_)
    else:
        assembly(p, election, id, path_)
    return p


def contest(p: Package, election: str, tag: str, **fields: object) -> tuple[str, str]:
    cid = key("contest", tag)
    p.add("contest", id=cid, election_id=election, round_number=1, default_result_kind="counted", **fields)
    rs = key("result_set", tag)
    p.add(
        "result_set",
        id=rs,
        election_id=election,
        contest_id=cid,
        stage="official",
        kind="counted",
        source_id=ECB,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    return cid, rs


def candidates(
    p: Package, cid: str, rs: str, tag: str, rows: list[Row], elected: set[int] | None = None
) -> dict[int, str]:
    options: dict[int, str] = {}
    for position, row in enumerate(rows, 1):
        name = clean(row.candidate)
        opt = options[position] = key("option", tag, position)
        person = key("person", tag, position)
        member = party(tables.party_code(row.party)) if row.party else None
        p.add("person", id=person)
        p.name(person, name, "en", "official")
        p.add(
            "contest_option",
            id=opt,
            contest_id=cid,
            kind="candidate",
            party_id=member,
            ballot_position=position,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", tag, position),
            person_id=person,
            option_id=opt,
            role="head",
            ballot_name=name,
            member_of_party_id=member,
            is_independent=not row.party,
        )
        if elected is not None and position in elected:
            p.add("outcome", result_set_id=rs, option_id=opt, kind="elected")
    return options


def assembly(p: Package, election: Election, eid: str, path_: str) -> None:
    day = election.day
    seats, totals, elected = read_assembly(day)
    layout = constituencies()
    codes = seat_codes(layout)
    names = pre2018_names()
    for number, seat in enumerate(seats):
        dz, canon = layout[number]
        code = codes[number]
        modern = day >= date(2017, 1, 1)
        unit = seat_unit(
            p,
            day,
            "bt.demkhong",
            code,
            area("bt.demkhong", code) if modern else dzongkhag_area(dz),
            modern,
            channels=True,
            roll=seat.registered is not None,
        )
        label = demkhong_name(canon if modern else names[number])
        name_units(p, unit, label)
        tag = f"{day}:{code}"
        cid, rs = contest(
            p,
            eid,
            tag,
            kind="body_seats",
            body_id=NA_BODY,
            district_area_id=area("bt.demkhong", code) if modern else None,
            magnitude=1,
            electoral_system_id=SYSTEM_FPTP,
        )
        p.name(cid, f"{label} constituency", "en", "official")
        p.add("slug", path=f"{path_}/{code}", entity_id=cid, is_current=True)
        p.add(
            "note",
            id=key("note", rs),
            entity_id=rs,
            kind="methodology",
            body_md=METHOD.format(unit="constituency"),
            lang="en",
        )
        won = {i for i, row in enumerate(seat.rows, 1) if elected is not None and row.elected}
        options = candidates(p, cid, rs, tag, seat.rows, won if elected is not None else None)
        for position, row in enumerate(seat.rows, 1):
            split(p, rs, unit, options[position], row)
        roll(p, rs, unit, seat)
    total = {
        "evm": sum(r.evm or 0 for s in seats for r in s.rows),
        "postal": sum(r.postal or 0 for s in seats for r in s.rows),
        "total": sum(r.total for s in seats for r in s.rows),
        "registered": sum(s.registered or 0 for s in seats),
    }
    verify(election, total, totals)


DIGITIZED = 8
PAGES = raw.DATA / "pages" / SOURCE


def render(artifact: Artifact) -> tuple[Path, int, int]:
    """The first page of a scanned file at 150 dpi, a PNG named for the file's hash, and its size."""
    image = PAGES / f"{artifact.sha256}-1.png"
    if not image.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    "150",
                    "-png",
                    "-f",
                    "1",
                    "-l",
                    "1",
                    "-singlefile",
                    str(artifact.path),
                    f"{folder}/p",
                ],
                check=True,
            )
            Image.open(f"{folder}/p.png").convert("RGB").save(image)
    width, height = Image.open(image).size
    return image, width, height


@dataclass(frozen=True)
class Block:
    """One constituency's block on a page, as fractions of the page: its rows, its left panel of figures, and the
    columns that hold each candidate's votes."""

    top: float
    bottom: float
    left: float
    split: float
    right: float


def blocks(image: Path) -> list[Block]:
    """Find each constituency's block on a page from the pale blue heading band above it. Every block on a page has
    the same height, so each runs from its band to the next band's top."""
    pixels = np.asarray(Image.open(image).convert("RGB")).astype(int)
    height, width = pixels.shape[:2]
    red, green, blue = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    band = (blue - red > 25) & (blue > green) & (red > 150)
    rows = np.flatnonzero(band.mean(axis=1) > 0.4)
    starts: list[int] = []
    ends: list[int] = []
    for y in rows:
        if starts and y - ends[-1] <= 10:
            ends[-1] = int(y)
        else:
            starts.append(int(y))
            ends.append(int(y))
    found = [(top, bottom) for top, bottom in zip(starts, ends, strict=True) if bottom - top >= 20]
    if len(found) < 2:
        raise ValueError(f"{image.name}: expected at least two constituency bands, found {len(found)}")
    pitch = found[1][0] - found[0][0]
    columns = np.flatnonzero(band[found[0][0] + 2])
    left, right = float(columns.min()), float(columns.max())
    wide = right - left
    return [
        Block(
            round((bottom + 2) / height, 4),
            round(min(top + pitch, height) / height, 4),
            round(left / width, 4),
            round((left + 0.585 * wide) / width, 4),
            round(right / width, 4),
        )
        for top, bottom in found
    ]


def press2024() -> tuple[dict[str, int], list[tuple[str, str]]]:
    """The 10 January 2024 declaration: the voters and votes it totals, and the members it names with their parties."""
    text = _text(raw.DATA / raw.cached(SOURCE, PRESS_2024).storage_key)
    flat = re.sub(r"\s+", " ", text)
    found = re.search(
        r"Of the total ([\d,]+) eligible registered voters, ([\d,]+) voters cast their votes\. From the total votes cast, "
        r"([\d,]+) votes were cast in person on the Electronic Voting Machines \(EVMs\) at the (\d+) polling stations "
        r"and ([\d,]+) votes were cast through Postal Ballots",
        flat,
    )
    if found is None:
        raise ValueError("2024 declaration totals not found")
    number = [int(group.replace(",", "")) for group in found.groups()]
    totals = {"registered": number[0], "total": number[1], "evm": number[2], "stations": number[3], "postal": number[4]}
    members = re.findall(r"^.*\s{2,}(\S+(?: \S+)*)\s{2,}(PDP|BTP)\s*$", text, re.MULTILINE)
    return totals, [(clean(name), code) for name, code in members]


def assembly2024(p: Package, election: Election, eid: str, path_: str) -> None:
    day = election.day
    seats = tables.na2024()
    if len(seats) != 47:
        raise ValueError(f"expected 47 constituencies, found {len(seats)}")
    totals, members = press2024()
    won = [
        (seat_row.candidate, tables.party_code(seat_row.party))
        for _, seat in seats
        for seat_row in seat.rows
        if seat_row.elected
    ]
    if Counter(code for _, code in members) != {"PDP": 30, "BTP": 17} or [code for _, code in members] != [
        code for _, code in won
    ]:
        raise ValueError("the declaration's members and parties do not match the constituency pages")
    for (named, _), (printed, _) in zip(members, won, strict=True):
        if SequenceMatcher(None, tables.squash(named), tables.squash(printed)).ratio() < 0.6:
            raise ValueError(f"the declaration names {named} where the page names {printed}")
    layout = [(tables.dzongkhag_index(name), seat.name) for name, seat in seats]
    codes = seat_codes(layout)
    try:
        # Each constituency's code is its place in the Commission's order, the order the 2018 table gives.
        earlier = constituencies()
    except LookupError:
        earlier = layout
    for (index, name), (before, label), code in zip(layout, earlier, codes, strict=True):
        if index != before or SequenceMatcher(None, tables.squash(name), tables.squash(label)).ratio() < 0.8:
            raise ValueError(f"{code}: {name} is not the constituency the 2018 table gives, {label}")
    artifacts: dict[str, str] = {}
    pictures: dict[str, tuple[str, list[Block]]] = {}
    for name in DZONGKHAGS:
        artifact = raw.cached(SOURCE, PAGES_2024[name])
        artifacts[name] = p.artifact(artifact, ECB)
        image, width, height = render(artifact)
        page = key("page", day, name)
        p.add(
            "source_page",
            id=page,
            artifact_id=artifacts[name],
            page_number=1,
            image_key=f"pages/{SOURCE}/{image.name}",
            width=width,
            height=height,
        )
        pictures[name] = (page, blocks(image))
    p.artifact(raw.cached(SOURCE, PRESS_2024), ECB)
    places: dict[str, int] = {}
    boxes: dict[tuple[str, str | None], tuple[str, tuple[float, float, float, float]]] = {}
    for number, (dzongkhag, seat) in enumerate(seats):
        index, canon = layout[number]
        code = codes[number]
        page, found = pictures[DZONGKHAGS[index]]
        if dzongkhag != DZONGKHAGS[index]:
            raise ValueError(f"{dzongkhag} is not a dzongkhag")
        places[dzongkhag] = places.get(dzongkhag, 0) + 1
        if places[dzongkhag] > len(found):
            raise ValueError(f"{dzongkhag}: the page has {len(found)} constituencies, the table has more")
        block = found[places[dzongkhag] - 1]
        unit = seat_unit(p, day, "bt.demkhong", code, area("bt.demkhong", code), True, channels=True, roll=True)
        label = demkhong_name(canon)
        name_units(p, unit, label)
        tag = f"{day}:{code}"
        cid, rs = contest(
            p,
            eid,
            tag,
            kind="body_seats",
            body_id=NA_BODY,
            district_area_id=area("bt.demkhong", code),
            magnitude=1,
            electoral_system_id=SYSTEM_FPTP,
        )
        p.name(cid, f"{label} constituency", "en", "official")
        p.add("slug", path=f"{path_}/{code}", entity_id=cid, is_current=True)
        p.add(
            "note",
            id=key("note", rs),
            entity_id=rs,
            kind="methodology",
            body_md=METHOD.format(unit="constituency")
            + " The pages are scanned images, so every number was read from the page and carries its page and place on "
            "it. Each constituency's votes were checked against the totals its page prints.",
            lang="en",
        )
        won_rows = {i for i, row in enumerate(seat.rows, 1) if row.elected}
        options = candidates(p, cid, rs, tag, seat.rows, won_rows)
        height = (block.bottom - block.top) / len(seat.rows)
        for position, row in enumerate(seat.rows, 1):
            split(p, rs, unit, options[position], row)
            top = round(block.top + (position - 1) * height, 4)
            boxes[(options[position], unit.evm)] = (page, (block.split, top, block.right, round(top + height, 4)))
            boxes[(options[position], unit.postal)] = boxes[(options[position], unit.evm)]
        panel = (page, (block.left, block.top, block.split - 0.01, block.bottom))
        assert unit.postal is not None and unit.total is not None
        value(p, rs, unit.evm, None, REGISTERED, seat.evm_voters or 0)
        value(p, rs, unit.postal, None, REGISTERED, seat.postal_voters or 0)
        value(p, rs, unit.total, None, REGISTERED, seat.registered or 0)
        for part in (unit.evm, unit.postal, unit.total):
            boxes[(f"registered:{rs}", part)] = panel
        locate(p, rs, boxes)
    total = {
        "evm": sum(r.evm or 0 for _, s in seats for r in s.rows),
        "postal": sum(r.postal or 0 for _, s in seats for r in s.rows),
        "total": sum(r.total for _, s in seats for r in s.rows),
        "registered": sum(s.registered or 0 for _, s in seats),
    }
    if sum(s.stations or 0 for _, s in seats) != totals["stations"]:
        raise ValueError("the polling stations do not add up to the declaration's")
    verify(election, total, {k: v for k, v in totals.items() if k != "stations"})


def locate(
    p: Package, rs: str, boxes: dict[tuple[str, str | None], tuple[str, tuple[float, float, float, float]]]
) -> None:
    """Mark a result set's values as digitized and give each its page and box."""
    for row in p.rows["result_value"]:
        if row["result_set_id"] != rs or row["flags"] == DIGITIZED:
            continue
        row["flags"] = DIGITIZED
        option = row["option_id"]
        page, box = boxes[(f"registered:{rs}" if option is None else str(option), str(row["unit_id"]))]
        p.add(
            "value_locator",
            result_set_id=rs,
            unit_id=row["unit_id"],
            option_id=option,
            channel=0,
            measure=row["measure"],
            count_round=0,
            page_id=page,
            source_id=None,
            x0=box[0],
            y0=box[1],
            x1=box[2],
            y1=box[3],
        )


def pre2018_names() -> list[str]:
    seats, _ = tables.na2008(path("na2008"))
    return [seat.name for seat in seats]


def read_assembly(day: date) -> tuple[list[Seat], dict[str, int], bool | None]:
    if day == date(2008, 3, 24):
        seats, totals = tables.na2008(path("na2008"))
        return seats, totals, None
    if day == date(2013, 7, 13):
        seats, totals = tables.na_round(path("na2013g"), kind="general13")
        return seats, totals, None
    if day == date(2018, 10, 18):
        groups, _ = tables.na2018(path("na2018g"))
        seats = [seat for _, group in sorted(groups, key=lambda g: tables.dzongkhag_index(g[0])) for seat in group]
        mark_elected(seats)
        # The general-election table prints merged or missing registered-voter cells, so the figures come from the
        # primary-round table, whose 47 constituencies add up to the 438,663 voters the press release gives.
        roll, _ = tables.na_round(path("na2018p"), kind="primary18")
        for seat, counted in zip(seats, roll, strict=True):
            seat.registered = counted.registered
        return seats, press_totals(), True
    raise ValueError(f"no National Assembly general election on {day}")


def press_totals() -> dict[str, int]:
    text = re.sub(r"\s+", " ", _text(path("na2018press")))
    found = re.search(
        r"Electronic Voting Machine \(EVM\) ([\d,]+) ([\d,]+) ([\d,]+) .*? Postal Ballot \(PB\) ([\d,]+) ([\d,]+) ([\d,]+)",
        text,
    )
    registered = re.search(r"total number of registered voters are ([\d,]+)", text)
    if found is None or registered is None:
        raise ValueError("2018 press release totals not found")

    def number(text: str) -> int:
        return int(text.replace(",", ""))

    return {
        "evm": number(found[3]),
        "postal": number(found[6]),
        "total": number(found[3]) + number(found[6]),
        "registered": number(registered[1]),
    }


def _text(file: Path) -> str:
    import subprocess

    return subprocess.run(["pdftotext", "-layout", str(file), "-"], capture_output=True, text=True, check=True).stdout


def mark_elected(seats: list[Seat]) -> None:
    html = json.loads(path("na2018elected").read_text())["content"]["rendered"]
    text = re.sub(r"<[^>]+>", "|", html.replace("&nbsp;", " ").replace("&#8211;", "-"))
    cells = [c.strip() for c in re.split(r"\|+", text) if c.strip()]
    start = cells.index("Name of Party of the Elected Candidates") + 1
    rows: list[tuple[str, str]] = []
    number = 0
    block: list[str] = []
    for cell in cells[start:]:
        if cell == str(number + 1):
            if block:
                rows.append((block[-2], block[-1]))
            block = []
            number += 1
        elif cell.startswith("As per Section"):
            break
        else:
            block.append(cell)
    if block and len(rows) == number - 1:
        rows.append((block[-2], block[-1]))
    if len(rows) != 47:
        raise ValueError(f"expected 47 elected members, found {len(rows)}")
    for seat, (name, code) in zip(seats, rows, strict=True):
        hit = [
            r
            for r in seat.rows
            if tables.party_code(r.party) == code
            and SequenceMatcher(None, tables.squash(r.candidate), tables.squash(name)).ratio() >= 0.8
        ]
        if len(hit) != 1:
            raise ValueError(f"{seat.name}: elected {name} ({code}) is not among {[r.candidate for r in seat.rows]}")
        hit[0].elected = True
        for row in seat.rows:
            if row is not hit[0]:
                row.elected = False
    won = Counter(code for _, code in rows)
    if won != {"DNT": 30, "DPT": 17}:
        raise ValueError(f"the press release gives 30 and 17 seats, not {dict(won)}")


def primary(p: Package, election: Election, eid: str, path_: str) -> None:
    day = election.day
    if day == date(2013, 5, 31):
        seats, totals = tables.na_round(path("na2013p"), kind="primary13")
    elif day == date(2018, 9, 15):
        seats, totals = tables.na_round(path("na2018p"), kind="primary18")
    else:
        seats = tables.na2023(path("na2023p"))
        grand = re.search(r"GRAND TOTAL.*?Total\s+(\d+)\s+(\d+)", _text(path("na2023turnout")), re.DOTALL)
        if grand is None:
            raise ValueError("2023 turnout table has no grand total")
        totals = {"registered": int(grand[1])}
    layout = constituencies()
    codes = seat_codes(layout)
    names = pre2018_names()
    modern = day >= date(2017, 1, 1)
    tag = f"{day}:primary"
    cid, rs = contest(
        p,
        eid,
        tag,
        kind="list_allocation",
        body_id=NA_BODY,
        district_area_id=COUNTRY,
        electoral_system_id=SYSTEM_PRIMARY,
    )
    p.name(cid, "National Assembly primary round", "en", "official")
    p.add("slug", path=f"{path_}/national", entity_id=cid, is_current=True)
    p.add(
        "note",
        id=key("note", rs),
        entity_id=rs,
        kind="methodology",
        body_md=(
            METHOD.format(unit="constituency")
            + " Each party's votes are counted in all 47 constituencies, and the national contest sums them."
        ),
        lang="en",
    )
    codes_in_use = sorted({tables.party_code(r.party) for s in seats for r in s.rows})
    options = {}
    for position, code in enumerate(codes_in_use, 1):
        options[code] = key("option", tag, code)
        p.add(
            "contest_option",
            id=options[code],
            contest_id=cid,
            kind="list",
            party_id=party(code),
            ballot_position=position,
            ballot_label=PARTY_NAMES[code],
        )
    for number, seat in enumerate(seats):
        dz, canon = layout[number]
        code = codes[number]
        unit = seat_unit(
            p,
            day,
            "bt.demkhong",
            code,
            area("bt.demkhong", code) if modern else dzongkhag_area(dz),
            modern,
            channels=True,
            roll=seat.registered is not None and seat.evm_voters is None,
        )
        name_units(p, unit, demkhong_name(canon if modern else names[number]))
        for row in seat.rows:
            split(p, rs, unit, options[tables.party_code(row.party)], row)
        roll(p, rs, unit, seat)
    total = {
        "evm": sum(r.evm or 0 for s in seats for r in s.rows),
        "postal": sum(r.postal or 0 for s in seats for r in s.rows),
        "total": sum(r.total for s in seats for r in s.rows),
        "registered": sum(s.registered or 0 for s in seats),
    }
    if totals.get("registered"):
        p.add("declared", result_set_id=rs, option_id=None, measure=REGISTERED, value=totals["registered"])
    verify(election, total, totals)


def council(p: Package, election: Election, eid: str, path_: str) -> None:
    day = election.day
    invalid: dict[str, tables.Rejected] = {}
    if day.year == 2007:
        seats, totals = tables.nc2007(path("nc2007"))
    elif day.year == 2013:
        seats, totals = tables.nc2013(path("nc2013")), {}
    elif day.year == 2018:
        text = subprocess.run(
            ["pdftotext", "-layout", str(path("nc2018")), "-"], check=True, capture_output=True, text=True
        ).stdout
        seats, invalid, book = tables.nc2018(text)
        totals = {"secured": book["total"], "registered": book["registered"]}
    else:
        seats, totals = tables.nc2023(path("nc2023")), {}
    seats = sorted(seats, key=lambda seat: tables.dzongkhag_index(seat.name))
    if len(seats) != 20:
        raise ValueError(f"expected 20 dzongkhags, found {len(seats)}")
    for seat in seats:
        index = tables.dzongkhag_index(seat.name)
        code = f"{index + 1:02d}"
        unit = seat_unit(
            p,
            day,
            "bt.dzongkhag",
            code,
            dzongkhag_area(index),
            True,
            channels=day.year != 2007,
            roll=seat.registered is not None and seat.evm_voters is None,
        )
        name_units(p, unit, DZONGKHAGS[index])
        tag = f"{day}:{code}"
        cid, rs = contest(
            p,
            eid,
            tag,
            kind="body_seats",
            body_id=NC_BODY,
            district_area_id=dzongkhag_area(index),
            magnitude=1,
            electoral_system_id=SYSTEM_FPTP,
        )
        p.name(cid, f"{DZONGKHAGS[index]} dzongkhag", "en", "official")
        p.add("slug", path=f"{path_}/{code}", entity_id=cid, is_current=True)
        p.add(
            "note",
            id=key("note", rs),
            entity_id=rs,
            kind="methodology",
            body_md=METHOD.format(unit="dzongkhag"),
            lang="en",
        )
        won = {i for i, row in enumerate(seat.rows, 1) if row.elected}
        flagged = any(row.elected is not None for row in seat.rows)
        options = candidates(p, cid, rs, tag, seat.rows, won if flagged else None)
        for position, row in enumerate(seat.rows, 1):
            split(p, rs, unit, options[position], row)
        if flagged and len(won) != 1:
            raise ValueError(f"{seat.name}: {len(won)} candidates marked elected")
        if seat.no is not None:
            no = key("option", tag, "no")
            p.add(
                "contest_option",
                id=no,
                contest_id=cid,
                kind="none_of_the_above",
                ballot_position=len(seat.rows) + 1,
                ballot_label="No",
            )
            evm, postal, count = seat.no
            split(p, rs, unit, no, Row(0, "", "", "No", evm, postal, count, 0))
        roll(p, rs, unit, seat)
        if seat.name in invalid and unit.postal is not None:
            rejected = invalid[seat.name]
            # Postal ballots the counters found invalid when they opened the inner envelopes. The Commission's postal
            # votes for the candidates are not reduced by them, so the count is kept apart from the votes.
            value(p, rs, unit.postal, None, 5, rejected.conventional + rejected.booth)
    rows = [r for s in seats for r in s.rows]
    total = {
        "total": sum(r.total for r in rows) + sum(s.no[2] for s in seats if s.no),
        "registered": sum(s.registered or 0 for s in seats),
    }
    if totals:
        totals = {"total": totals["secured"], "registered": totals["registered"]}
    if day.year == 2018:
        total["registered"] = sum((s.evm_voters or 0) + (s.postal_voters or 0) for s in seats)
    verify(election, total, totals)


TOTALS: dict[str, dict[str, object]] = {}


def verify(election: Election, ours: dict[str, int], theirs: dict[str, int]) -> None:
    """Compare the sums of what we store with the totals the Commission prints, and record the comparison."""
    compared = {}
    for name, given in theirs.items():
        if ours.get(name):
            compared[name] = {"ours": ours[name], "authority": given}
            if ours[name] != given:
                raise ValueError(f"{election.day}: {name} is {ours[name]:,} but the Commission prints {given:,}")
    TOTALS[str(election.day)] = {"ours": ours, "compared": compared}
