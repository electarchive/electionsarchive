"""Botswana's general election of 16 October 1999, digitized from the Independent Electoral Commission's scanned report.

The report has no text layer, so every figure was read from the page images, twice: once by a vision model reading the
printed tables, and once by OCR with word boxes. Each block was checked against its own printed totals: the candidates'
votes against the valid total, valid plus rejected against the votes cast, the winning margin against the two leaders,
and the votes cast over the roll against the printed poll. Where the model and the OCR disagreed on a digit, the reading
that satisfies the printed totals was kept and checked on the page. The transcription lives in ``iec1999.json`` beside
this module, with the box of each figure in pixels of the page rendered at 100 dpi.

Appendix A prints the 40 National Assembly constituencies and Appendix F the 406 polling districts of the local councils.
Appendix F prints one of its pages twice (pages 67 and 68 of the PDF); the second, uncropped copy is used.
"""

import json
import re
import subprocess
from datetime import date
from functools import cache
from pathlib import Path
from typing import TypedDict, cast
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

DATA_FILE = Path(__file__).with_name("iec1999.json")
DAY = date(1999, 10, 16)
URL = "https://www.iec.gov.bw/images/documents/1999_General_Elections_Report.pdf"
DIGITIZED = 8
DPI = 100
VOTES, REGISTERED, CAST, VALID, REJECTED = 1, 2, 3, 4, 5
# Appendix D: votes by party in the National Assembly election, and the seats won in Appendix C.
PARTY_VOTES = {"BDP": 192598, "BNF": 87457, "BCP": 40096, "BAM": 15803, "IND": 1004, "MELS": 22}
PARTY_SEATS = {"BDP": 33, "BNF": 6, "BCP": 1}
# Appendix H: the seats each party won in each council, unopposed seats included.
COUNCIL_SEATS = {
    "NORTH WEST DISTRICT COUNCIL": {"BDP": 30, "BCP": 6, "BAM": 4},
    "NORTH EAST DISTRICT COUNCIL": {"BDP": 14, "BAM": 2},
    "CENTRAL DISTRICT COUNCIL": {"BDP": 104, "BCP": 1, "BAM": 1},
    "KGATLENG DISTRICT COUNCIL": {"BDP": 12, "BNF": 6, "BCP": 3},
    "SOUTH EAST DISTRICT COUNCIL": {"BDP": 13, "BNF": 5},
    "KWENENG DISTRICT COUNCIL": {"BDP": 42, "BNF": 7, "BCP": 1},
    "SOUTHERN DISTRICT COUNCIL": {"BDP": 26, "BNF": 18},
    "KGALAGADI DISTRICT COUNCIL": {"BDP": 13, "BNF": 7},
    "GHANZI DISTRICT COUNCIL": {"BDP": 15, "BNF": 3},
    "FRANCISTOWN CITY COUNCIL": {"BDP": 14, "BAM": 2},
    "SELIBE PHIKWE TOWN COUNCIL": {"BDP": 11, "BCP": 2},
    "GABORONE CITY COUNCIL": {"BDP": 7, "BNF": 18},
    "LOBATSE TOWN COUNCIL": {"BDP": 2, "BNF": 9},
    "JWANENG TOWN COUNCIL": {"BNF": 7},
}
# Notes on blocks whose print is inconsistent or whose reading needed a decision.
NOTES = {
    ("parliament", "SEROWE SOUTH"): "The report prints no registered voters for Serowe South, so none is stored. The "
    "report's national roll of 459,662 less the other 39 constituencies leaves 10,819, which with the 8,110 votes cast "
    "gives the 74.96 percent poll the report prints.",
    (
        "parliament",
        "NORTH EAST",
    ): "The report prints a poll of 78.27 percent, but its 11,096 votes cast over its 14,139 "
    "registered voters is 78.48 percent.",
    ("parliament", "TONOTA"): "The report prints a poll of 71.12 percent, but its 7,185 votes cast over its 10,093 "
    "registered voters is 71.19 percent.",
    ("parliament", "LENTSWELETAU"): "The report prints the BCP candidate's 708 votes one column to the left, under the "
    "registered voters; they are stored as his votes, which the valid total confirms.",
    ("local", "MATSHELAGABEDI"): "The two leading candidates tied on 239 votes, and the report prints RE-ELECTION "
    "instead of a margin, so no winner is stored.",
    (
        "local",
        "WHITE CITY",
    ): "The report prints a poll of 29.30 percent, but its 638 votes cast over its 802 registered "
    "voters is 79.55 percent.",
    (
        "local",
        "MOLOPO",
    ): "The report prints Molopo after the Southern District Council heading, but the council summary "
    "in Appendix E and the seats in Appendix H count it among the Jwaneng Town Council's seven wards, whose registered "
    "voters and valid votes add up only with it, so it is stored under Jwaneng.",
}
EXPANDED = (
    "Appendix A abbreviates seven constituency names, which are stored in full: S/GWETA as Sebina/Gweta, F/WEST and "
    "F/EAST as Francistown West and East, S/PHIKWE as Selibe Phikwe, and BARO LONG as Barolong, as the report's "
    "deposit appendices print them, and LEN/TAU as Lentsweletau and M/TSHANE as Mogoditshane."
)


class Candidate(TypedDict, total=False):
    name: str
    party: str
    votes: int | None
    box: list[float]


class Block(TypedDict, total=False):
    page: int
    name: str
    printed: str
    council: str
    unopposed: bool
    registered: int | None
    rejected: int | None
    cast: int | None
    valid: int | None
    margin: int | None
    poll: str | None
    registered_box: list[float]
    rejected_box: list[float]
    cast_box: list[float]
    valid_box: list[float]
    candidates: list[Candidate]


@cache
def blocks() -> tuple[list[Block], list[Block]]:
    """The transcribed constituencies and polling districts, each checked against the report's own totals."""
    body = json.loads(DATA_FILE.read_text())
    parliament = cast(list[Block], body["parliament"])
    local = cast(list[Block], body["local"])
    for kind, found in (("parliament", parliament), ("local", local)):
        for block in found:
            _check(kind, block)
    if len(parliament) != 40 or len(local) != 406:
        raise ValueError(
            f"1999: expected 40 constituencies and 406 polling districts, got {len(parliament)} and {len(local)}"
        )
    _declared(parliament, local)
    return parliament, local


def _check(kind: str, block: Block) -> None:
    name = block["name"]
    if block["unopposed"]:
        if len(block["candidates"]) != 1 or any(block.get(k) is not None for k in ("registered", "cast", "valid")):
            raise ValueError(f"1999 {kind} {name}: an unopposed seat has one candidate and no figures")
        return
    votes = [c["votes"] for c in block["candidates"]]
    if any(v is None for v in votes):
        raise ValueError(f"1999 {kind} {name}: a candidate has no votes")
    counted = sum(v for v in votes if v is not None)
    valid, rejected, total = block["valid"], block["rejected"], block["cast"]
    if valid != counted or rejected is None or total != counted + rejected:
        raise ValueError(f"1999 {kind} {name}: {votes} {valid} {rejected} {total} do not add up")
    registered, poll = block["registered"], block["poll"]
    # The report rounds some polls and cuts others short, to one or two places.
    if (
        registered is not None
        and poll is not None
        and (kind, name) not in NOTES
        and abs(total / registered * 100 - float(poll)) > 10 ** -len(poll.partition(".")[2])
    ):
        raise ValueError(f"1999 {kind} {name}: {total} of {registered} is not the printed {poll} percent")


def _winner(block: Block) -> str | None:
    if block["unopposed"]:
        return block["candidates"][0]["party"]
    ranked = sorted((c["votes"] or 0 for c in block["candidates"]), reverse=True)
    if len(ranked) > 1 and ranked[0] == ranked[1]:
        return None
    return max(block["candidates"], key=lambda c: c["votes"] or 0)["party"]


def _declared(parliament: list[Block], local: list[Block]) -> None:
    """Check the units against the report's national party votes and seats, and its seats by council."""
    votes: dict[str, int] = {}
    seats: dict[str, int] = {}
    for block in parliament:
        for candidate in block["candidates"]:
            votes[candidate["party"]] = votes.get(candidate["party"], 0) + (candidate["votes"] or 0)
        winner = _winner(block)
        if winner:
            seats[winner] = seats.get(winner, 0) + 1
    # Appendix D prints 15,803 votes for the BAM and 17,483 rejected ballots; the constituencies give 15,805 and
    # 17,481, the rejected figure the report's own text gives. The total of 354,463 ballots cast agrees either way.
    if votes != {**PARTY_VOTES, "BAM": 15805} or seats != PARTY_SEATS:
        raise ValueError(f"1999 constituencies do not match the report's party votes and seats: {votes} {seats}")
    if sum(b["cast"] or 0 for b in parliament) != 354463 or sum(b["rejected"] or 0 for b in parliament) != 17481:
        raise ValueError("1999 constituencies do not match the report's ballots cast and rejected")
    councils: dict[str, dict[str, int]] = {}
    for block in local:
        winner = _winner(block)
        if winner:
            tally = councils.setdefault(block["council"], {})
            tally[winner] = tally.get(winner, 0) + 1
    if councils != COUNCIL_SEATS:
        raise ValueError(f"1999 council seats do not match Appendix H: {councils}")


def constituency_name(block: Block) -> str:
    return block["name"].title()


def councils() -> set[str]:
    return {block["council"] for block in blocks()[1]}


def constituencies() -> dict[str, str]:
    return {changeset.slug(block["name"]): constituency_name(block) for block in blocks()[0]}


def districts() -> dict[tuple[str, str], str]:
    return {
        (changeset.slug(block["council"]), changeset.slug(block["name"])): block["name"].title()
        for block in blocks()[1]
    }


def parties() -> set[str]:
    return {c["party"] for units in blocks() for block in units for c in block["candidates"] if c["party"] != "IND"}


def artifact() -> raw.Artifact:
    from ea.sources.bw import iec

    return iec._artifact(URL)


def _sizes(path: Path) -> dict[int, tuple[float, float]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "999", str(path)], capture_output=True, text=True, check=True)
    found = (re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line) for line in out.stdout.splitlines())
    return {int(m[1]): (float(m[2]), float(m[3])) for m in found if m}


def images() -> list[Path]:
    """Render the cited pages at 100 dpi into the page tree, where the source_page rows point."""
    stored = artifact()
    out = raw.DATA / "pages" / "bw.iec"
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for number in sorted({block["page"] for units in blocks() for block in units}):
        target = out / f"{stored.sha256}-{number}.png"
        if not target.exists():
            stem = out / f"render-{number}"
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    str(DPI),
                    "-png",
                    "-singlefile",
                    "-f",
                    str(number),
                    "-l",
                    str(number),
                    str(stored.path),
                    str(stem),
                ],
                check=True,
                capture_output=True,
            )
            stem.with_suffix(".png").rename(target)
        written.append(target)
    return written


METHOD = (
    "Counted by the Independent Electoral Commission for each {unit}, as the appendix to its 1999 general election "
    "report prints them, and digitized from the scanned report: each figure keeps its page and box. Registered voters "
    "are the roll, rejected votes are the ballots set aside, and ballots cast are the valid and rejected ballots "
    "together; the candidates' votes add up to the printed valid total in every unit. The leading candidate is stored "
    "as elected, as the report's tallies of seats by party count them."
)


def general() -> Package:
    from ea.sources.bw import iec

    parliament, local = blocks()
    p = Package(
        f"{iec.SOURCE}/{DAY}",
        iec.SOURCE,
        f"The {DAY:%-d %B %Y} general election of Botswana: the National Assembly by constituency and the local "
        "councils by polling district, digitized from the scanned report",
        depends=[f"{iec.SOURCE}/geo", f"{iec.SOURCE}/registry"],
    )
    p.external |= {iec.BOTSWANA, iec.COUNTRY}
    stored = artifact()
    p.uses(stored)
    artifact_id = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{iec.IEC_SOURCE}\x1f{stored.storage_key}"))
    sizes = _sizes(stored.path)
    pages: dict[int, str] = {}
    for number in sorted({block["page"] for units in (parliament, local) for block in units}):
        width, height = sizes[number]
        pages[number] = p.id("page", stored.sha256, number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact_id,
            page_number=number,
            image_key=f"pages/{iec.SOURCE}/{stored.sha256}-{number}.png",
            width=round(width * DPI / 72),
            height=round(height * DPI / 72),
        )
    election = iec.key("election", DAY)
    path = f"bw/{DAY}"
    p.add("election", id=election, polity_id=iec.BOTSWANA, kind="general", date_from=DAY, date_to=DAY, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, "1999 Botswana general election", "en", "official")
    p.add(
        "note",
        id=iec.key("note", election),
        entity_id=election,
        kind="methodology",
        body_md="Digitized from the Independent Electoral Commission's scanned report on the 1999 general elections. "
        "The constituencies' votes by party match the report's Appendix D for every party but the BAM, which the "
        "constituencies give 15,805 and the appendix 15,803; the constituencies' 17,481 rejected ballots are the "
        "figure the report's text gives, against 17,483 in the appendix. The winners match the seats by party in "
        f"Appendices C and H. {EXPANDED}",
        lang="en",
    )
    values: list[tuple[str, str, str | None, int, int]] = []
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []

    def value(
        result_set: str, unit: str, option: str | None, measure: int, count: int, page: int, box: list[float]
    ) -> None:
        values.append((result_set, unit, option, measure, count))
        x0, y0, x1, y1 = box
        locators.append((result_set, unit, option, measure, pages[page], x0, y0, x1, y1))

    def contest(
        block: Block, tag: str, name: str, district: str, body: str, unit_id: str, note_key: tuple[str, str], kind: str
    ) -> None:
        walkover = block["unopposed"]
        contest = iec.key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=body,
            district_area_id=district,
            magnitude=1,
            round_number=1,
            electoral_system_id=iec.SYSTEM_FPTP,
            default_result_kind="tacit" if walkover else "counted",
        )
        p.name(contest, name, "en", "official")
        p.add("slug", path=f"{path}/{tag.split(':', 1)[1].replace(':', '/')}", entity_id=contest, is_current=True)
        result_set = iec.key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="tacit" if walkover else "counted",
            source_id=iec.IEC_SOURCE,
            status="active",
            is_default=True,
            # An unopposed seat has no figures in the report.
            completeness=0.0 if walkover else 1.0,
        )
        method = METHOD.format(unit=kind)
        if note_key in NOTES:
            method += " " + NOTES[note_key]
        if walkover:
            method += " The candidate was returned unopposed, so the report prints no figures."
        p.add(
            "note", id=iec.key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
        )
        winner = None if walkover else _winner(block)
        for index, candidate in enumerate(block["candidates"], start=1):
            option = iec.key("option", tag, index)
            person = iec.key("person", tag, index)
            label = candidate["name"].title()
            solo = candidate["party"] == "IND"
            party = None if solo else iec.party(candidate["party"])
            p.add("person", id=person)
            p.name(person, label, "en", "official")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=party,
                ballot_position=index,
                ballot_label=label,
            )
            p.add(
                "candidacy",
                id=iec.key("candidacy", tag, index),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
                member_of_party_id=party,
                is_independent=solo,
            )
            votes = candidate.get("votes")
            leads = votes is not None and votes == max(c["votes"] or 0 for c in block["candidates"])
            if walkover or (winner is not None and leads):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            if votes is not None:
                value(result_set, unit_id, option, VOTES, votes, block["page"], candidate["box"])
        figures = (
            (block.get("registered"), block.get("registered_box"), REGISTERED),
            (block.get("valid"), block.get("valid_box"), VALID),
            (block.get("rejected"), block.get("rejected_box"), REJECTED),
            (block.get("cast"), block.get("cast_box"), CAST),
        )
        for count, box, measure in figures:
            if count is not None and box is not None:
                value(result_set, unit_id, None, measure, count, block["page"], box)

    for block in parliament:
        code = changeset.slug(block["name"])
        unit_id = iec.key("unit", f"{DAY}:constituency:{code}")
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=iec.BOTSWANA,
            scheme_code="bw.constituency",
            code=f"{DAY}:{code}",
            area_relation="equals",
            area_id=iec.area("bw.constituency", code),
            kind="standard",
        )
        contest(
            block,
            f"{DAY}:parliament:{code}",
            f"1999 National Assembly election, {constituency_name(block)}",
            iec.area("bw.constituency", code),
            iec.ASSEMBLY,
            unit_id,
            ("parliament", block["name"]),
            "constituency",
        )
    for number, block in enumerate(local, start=1):
        council = changeset.slug(block["council"])
        code = f"{council}:{changeset.slug(block['name'])}"
        unit_id = iec.key("unit", f"{DAY}:polling_district:{number}:{code}")
        p.add(
            "reporting_unit",
            id=unit_id,
            polity_id=iec.BOTSWANA,
            scheme_code="bw.polling_district",
            code=f"{DAY}:{number}:{code}",
            area_relation="equals",
            area_id=iec.area("bw.polling_district", code),
            kind="standard",
        )
        contest(
            block,
            f"{DAY}:local:{number}-{changeset.slug(block['name'])}",
            f"1999 local council election, {block['name'].title()} ({block['council'].title()})",
            iec.area("bw.polling_district", code),
            iec.council_body(block["council"]),
            unit_id,
            ("local", block["name"]),
            "polling district",
        )
    p.frame(
        "result_value",
        pl.DataFrame(
            values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators,
            schema=["result_set_id", "unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
            orient="row",
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p
