"""Iceland's 2022 municipal elections by candidate, from the electoral committees' reports published by the ministry."""

import importlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import polars as pl

from ea import package, raw
from ea.package import Package

h = importlib.import_module("ea.sources.is.hagstofa")

SOURCE = "is.stjornarradid"
BASE = "https://www.stjornarradid.is/library/03-Verkefni/Kosningar/Sveitarstjkosn2022/Kosningaskyrslur/"
FILES = [
    "0000 Reykjavíkurborg", "1000 Kópavogsbær", "1100 Seltjarnarnesbær", "1300 Garðabær",
    "1400 Hafnarfjarðarkaupstaður", "1604 Mosfellsbær", "1606 Kjósarhreppur", "2000 Reykjanesbær",
    "2300 Grindavíkurbær", "2506 Sveitarfélagið Vogar", "2510 Suðurnesjabær", "3000 Akraneskaupstaður",
    "3506 Skorradalshreppur", "3511 Hvalfjarðarsveit", "3609 Borgarbyggð", "3709 Grundarfjarðarbær",
    "3710 Helgafellssveit og 3711 Stykkishólmsbær", "3713 Eyja- og Miklaholtshreppur", "3714 Snæfellsbær",
    "3811 Dalabyggð", "4100 Bolungarvíkurkaupstaður", "4200 Ísafjarðarbær", "4502 Reykhólahreppur",
    "4604 Tálknafjarðarhreppur", "4803 Súðavíkurhreppur", "4901 Árneshreppur", "4902 Kaldrananeshreppur",
    "4911 Strandabyggð", "5200 Sveitarfélagið Skagafjörður og 5706 Akrahreppur", "5508 Húnaþing Vestra",
    "5604 Blönduósbær og 5612 Húnavatnshreppur", "5611 Skagabyggð", "6000 Akureyrarbær", "6100 Norðurþing",
    "6250 Fjallabyggð", "6400 Dalvíkurbyggð", "6513 Eyjafjarðarsveit", "6515 Hörgársveit",
    "6601 Svalbarðsstrandarhreppur", "6602 Grýtubakkahreppur", "6607 Skútustaðahreppur og 6612 Þingeyjarsveit",
    "6706 Svalbarðshreppur og 6709 Langanesbyggð", "7300 Fjarðabyggð", "7400 Múlaþing", "7502 Vopnafjarðarhreppur",
    "7505 Fljótsdalshreppur", "8000 Vestmannaeyjabær", "8200 Sveitarfélagið Árborg", "8508 Mýrdalshreppur",
    "8509 Skaftárhreppur", "8610 Ásahreppur", "8613 Rangárþing eystra", "8614 Rangárþing ytra",
    "8710 Hrunamannahreppur", "8716 Hveragerðisbær", "8717 Sveitarfélagið Ölfus",
    "8719 Grímsnes- og Grafningshreppur", "8720 Skeiða- og Gnúpverjahreppur", "8721 Bláskógabyggð",
    "8722 Flóahreppur",
]  # fmt: skip


def url(name: str) -> str:
    return f"{BASE}{quote(name)}.pdf"


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, url(name), suffix=".pdf")


# The municipality each report covers, as Statistics Iceland names it. A joint report covers the municipality
# that its parts merged into after the election.
MUNICIPALITIES = {
    "0000": "Reykjavík", "1000": "Kópavogur", "1100": "Seltjarnarnes", "1300": "Garðabær",
    "1400": "Hafnarfjörður", "1604": "Mosfellsbær", "1606": "Kjósarhreppur", "2000": "Reykjanesbær",
    "2300": "Grindavík", "2506": "Sveitarfélagið Vogar", "2510": "Suðurnesjabær", "3000": "Akranes",
    "3506": "Skorradalshreppur", "3511": "Hvalfjarðarsveit", "3609": "Borgarbyggð", "3709": "Grundarfjarðarbær",
    "3710": "Stykkishólmur", "3713": "Eyja- og Miklaholtshreppur", "3714": "Snæfellsbær", "3811": "Dalabyggð",
    "4100": "Bolungarvík", "4200": "Ísafjarðarbær", "4502": "Reykhólahreppur", "4604": "Tálknafjarðarhreppur",
    "4803": "Súðavíkurhreppur", "4901": "Árneshreppur", "4902": "Kaldrananeshreppur", "4911": "Strandabyggð",
    "5200": "Skagafjörður", "5508": "Húnaþing vestra", "5604": "Húnabyggð", "5611": "Skagabyggð",
    "6000": "Akureyri", "6100": "Norðurþing", "6250": "Fjallabyggð", "6400": "Dalvíkurbyggð",
    "6513": "Eyjafjarðarsveit", "6515": "Hörgársveit", "6601": "Svalbarðsstrandarhreppur",
    "6602": "Grýtubakkahreppur", "6607": "Þingeyjarsveit", "6706": "Langanesbyggð", "7300": "Fjarðabyggð",
    "7400": "Múlaþing", "7502": "Vopnafjarðarhreppur", "7505": "Fljótsdalshreppur", "8000": "Vestmannaeyjar",
    "8200": "Sveitarfélagið Árborg", "8508": "Mýrdalshreppur", "8509": "Skaftárhreppur", "8610": "Ásahreppur",
    "8613": "Rangárþing eystra", "8614": "Rangárþing ytra", "8710": "Hrunamannahreppur", "8716": "Hveragerði",
    "8717": "Sveitarfélagið Ölfus", "8719": "Grímsnes- og Grafningshreppur", "8720": "Skeiða- og Gnúpverjahreppur",
    "8721": "Bláskógabyggð", "8722": "Flóahreppur",
}  # fmt: skip
COVERED = frozenset(MUNICIPALITIES.values())
SCANS = Path(__file__).with_name("stjornarradid_scans.json")
LETTERS = "A-ZÞÆÖÐÁÉÍÓÚÝ"


@dataclass
class Candidate:
    position: int
    name: str
    weighted: Decimal
    rank: int
    struck: int


@dataclass
class Line:
    letter: str
    label: str
    votes: int
    seats: int
    changed: int | None = None
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class Person:
    name: str
    votes: int
    deputy: int  # 0 for a principal member, else the deputy's order


@dataclass
class Report:
    code: str
    file: str
    registered: int
    cast: int
    valid: int
    blank: int
    invalid: int
    lists: list[Line] = field(default_factory=list)
    people: list[Person] = field(default_factory=list)
    scanned: bool = False


def whole(text: str) -> int:
    return int(text.replace(".", ""))


def figure(text: str, label: str) -> int:
    found = re.search(label + r"\s+([\d.]+)", text)
    if found is None:
        raise ValueError(f"no {label!r} in the report")
    return whole(found.group(1))


def pdf_text(file: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(file), "-"], capture_output=True, text=True, check=True).stdout


ROW_TAIL = re.compile(r"(\d+)\s+(\d{1,3},\d{2})%\s+((?:\d{1,3}\.)*\d+,\d{2})\s+(\d+)\.\s*$")
LIST_ROW = re.compile(rf"^\s*([{LETTERS}]{{1,2}})\s+–\s+listi\s+(.+?)\s{{2,}}([\d.]+)\s+[\d,]+%\s+(\d+)\s*$")


def clean(text: str) -> str:
    return h.clean(re.sub(r"\s*\(\d{6}-\d{4}\)", "", text))


def candidates(page: str) -> tuple[str, str, int | None, list[Candidate]]:
    lines = page.splitlines()
    head = next(i for i, line in enumerate(lines) if "Atkvæðatala frambjóðenda" in line)
    title = next(
        m
        for m in (re.match(rf"^\s*([{LETTERS}]{{1,2}})\s{{2,}}(\S.*?)\s*$", line) for line in reversed(lines[:head]))
        if m
    )
    changed = re.search(r"seðla:\s+(\d+)\s+listans:", page)
    header = next(line for line in lines if "Nafn frambjóðanda" in line)
    address = header.index("Heimilisfang")
    rows: list[Candidate] = []
    for line in lines:
        start = re.match(r"^\s*(\d+)\.\s+", line)
        tail = ROW_TAIL.search(line)
        if not start or not tail:
            continue
        chunk = re.split(r"\s{2,}", line[start.end() :].strip())[0]
        name = chunk
        cut_here = line[start.end() : address]
        if re.search(r"\d", chunk) and line[address - 1 : address] == " " and not re.search(r"\d", cut_here):
            name = cut_here
        elif re.search(r"\d", chunk):
            parts = re.split(rf"(?<=[a-zþæöðáéíóúý.])(?=[{LETTERS}])", chunk)
            for cut in range(len(parts) - 1, 0, -1):
                if re.search(r"\d", "".join(parts[cut:])):
                    name = "".join(parts[:cut])
                else:
                    break
        elif start.end() + len(chunk) > address and line[address - 1 : address] == " ":
            name = line[start.end() : address]
        if re.search(r"\d", name):
            name = re.sub(r"\s+\S+\s+\d+\S*(?:\s.*)?$", "", name)
        name = clean(name.strip())
        if re.search(r"\d", name):
            raise ValueError(f"name with a digit: {name!r}")
        rows.append(
            Candidate(
                int(start.group(1)),
                name,
                Decimal(tail.group(3).replace(".", "").replace(",", ".")),
                int(tail.group(4)),
                int(tail.group(1)),
            )
        )
    return title.group(1), clean(title.group(2)), int(changed.group(1)) if changed else None, rows


def parse_text(code: str, file: str, text: str) -> Report:
    pages = [page for page in text.split("\f") if page.strip()]
    first = pages[0]
    report = Report(
        code,
        file,
        figure(first, "Tala kjósenda á kjörskrá"),
        figure(first, "Greidd atkvæði alls"),
        figure(first, "Gildir atkvæðaseðlar"),
        figure(first, "Þar af auðir seðlar"),
        figure(first, r"Þar af ógildir seðlar \(en ekki auðir\)"),
    )
    if "óbundinna kosninga" in first:
        body = first.split("hlutu", 1)[1].split("Staður og dagsetning")[0]
        for line in body.splitlines():
            fields = re.split(r"\s{2,}", line.strip())
            if len(fields) < 2 or "Atkvæði" in line or not re.match(r"^\d+\b", fields[1]):
                continue
            tail = " ".join(fields[1:]).lower()
            if "var" in tail:
                deputy = 1 + sum(1 for x in report.people if x.deputy)
            elif "aðalm" in tail:
                deputy = 0
            else:
                continue
            name = re.sub(r"\s*[,.]\s+\S+(\s+\d+)?$", "", fields[0]) if code == "8610" else fields[0]
            report.people.append(Person(clean(name), int(fields[1].split()[0]), deputy))
        return report
    for line in first.splitlines():
        row = LIST_ROW.match(line)
        if row:
            report.lists.append(Line(row.group(1), clean(row.group(2)), whole(row.group(3)), int(row.group(4))))
    by_letter = {line.letter: line for line in report.lists}
    for page in pages[1:]:
        letter, _label, changed, rows = candidates(page)
        by_letter[letter].changed = changed
        by_letter[letter].candidates = rows
    return report


def scan_report(code: str, file: str, data: dict) -> Report:
    report = Report(
        code, file, data["registered"], data["cast"], data["valid"], data["blank"], data["invalid"], scanned=True
    )
    if data["kind"] == "personal":
        report.people = [Person(name, votes, deputy) for name, votes, deputy in data["people"]]
    else:
        for entry in data["lists"]:
            line = Line(entry["letter"], entry["label"], entry["votes"], entry["seats"], entry["changed"])
            line.candidates = [
                Candidate(position, name, Decimal(weighted), position, struck)
                for name, weighted, position, struck in entry["candidates"]
            ]
            report.lists.append(line)
    return report


def reports() -> list[Report]:
    scans = json.loads(SCANS.read_text())
    found: list[Report] = []
    for name in FILES:
        code = name[:4]
        artifact = raw.cached(SOURCE, url(name))
        text = pdf_text(artifact.path)
        if name in scans:
            found.append(scan_report(code, name, scans[name]))
        else:
            found.append(parse_text(code, name, text))
    return found


def authority_seats() -> dict[str, int]:
    """The council seats each municipality's committee report gives, by municipality, for the elections by list."""
    return {MUNICIPALITIES[r.code]: sum(line.seats for line in r.lists) for r in reports() if r.lists}


# ---------------------------------------------------------------- the package

EKEY = "local-2022-05-14"
KEY = f"{SOURCE}/{EKEY}"
PERSONAL_NOTE = (
    "The council was elected by direct personal voting, without candidate lists. The electoral committee's report "
    "names the people elected as members and as deputies, with the votes each received; it does not give votes for "
    "the other people who received votes. Each member is stored as elected, and each deputy is stored in the order "
    "the report gives."
)
LIST_NOTE = (
    "The council was elected by proportional representation from candidate lists. The electoral committee's report "
    "gives each list's votes and seats, the votes each candidate received as the committee weighted them under "
    "article 112 of the Elections Act (a candidate's weighted votes, to two decimal places, after crossings-out and "
    "moves down the list), and the number of ballots altered on each list. The committee ranks each list's candidates "
    "by weighted votes; the members are the highest-ranked candidates, one for each seat the list won. The report "
    "prints candidate figures only for the lists that won seats."
)


def hagstofa_ids() -> tuple[set[str], dict[str, dict[str, str]]]:
    """Statistics Iceland's 2022 contests, and for each contest the id of each list option by its ballot letter."""
    folder = package.folder(f"{h.SOURCE}/{EKEY}") / "build"
    contests = set(pl.read_parquet(folder / "contest.parquet")["id"].to_list())
    options: dict[str, dict[str, str]] = {}
    for row in pl.read_parquet(folder / "contest_option.parquet").iter_rows(named=True):
        options.setdefault(row["contest_id"], {})[h.letter(row["ballot_label"])] = row["id"]
    return contests, options


def build() -> Package:
    all_reports = reports()
    contests, listed = hagstofa_ids()
    p = Package(
        KEY,
        SOURCE,
        "Iceland's 2022 municipal elections by candidate, from the reports of the municipal electoral committees",
        depends=[f"{h.SOURCE}/geo", f"{h.SOURCE}/registry", f"{h.SOURCE}/{EKEY}"],
    )
    source = p.id("source", SOURCE)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Innviðaráðuneytið (Ministry of Infrastructure)",
        title="Reports of the municipal electoral committees on the municipal elections of 14 May 2022",
        url="https://www.stjornarradid.is/library/03-Verkefni/Kosningar/Sveitarstjkosn2022/Kosningaskyrslur/",
        license="Official reports published by the Icelandic government",
        license_url="https://www.stjornarradid.is/",
        attribution="Source: yfirkjörstjórnir (municipal electoral committees), published by the Ministry of Infrastructure",
    )
    election = h.key("election", EKEY)
    sums = {"registered": 0, "cast": 0, "valid": 0}
    for report in all_reports:
        name = MUNICIPALITIES[report.code]
        contest = h.key("contest", EKEY, f"local:{name}")
        if contest not in contests:
            raise LookupError(f"Statistics Iceland has no 2022 contest for {name}")
        unit = h.key("unit", "is.sveitarfelag", EKEY, name)
        artifact = raw.cached(SOURCE, url(report.file))
        p.artifact(artifact, source)
        result_set = p.id("result_set", name)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
            supersedes_id=h.key("result_set", EKEY, f"local:{name}"),
        )
        note = PERSONAL_NOTE if report.people else LIST_NOTE
        if report.scanned:
            note += " The report is a scan with no text layer, and its figures were transcribed from the page by eye."
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")

        def value(
            option: str | None, measure: int, number: int | Decimal, result_set: str = result_set, unit: str = unit
        ) -> None:
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

        def declare(option: str | None, measure: int, number: int, result_set: str = result_set) -> None:
            p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=number)

        for measure, number in (
            (2, report.registered),
            (3, report.cast),
            (4, report.valid),
            (6, report.blank),
            (5, report.invalid),
        ):
            value(None, measure, number)
            declare(None, measure, number)
        for k in sums:
            sums[k] += getattr(report, k)
        for line in report.lists:
            option = listed.get(contest, {}).get(line.letter)
            if option is None:
                option = p.id("option", name, line.letter)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    ballot_position=None,
                    ballot_label=f"{line.letter}-listi {line.label}",
                )
            value(option, 1, line.votes)
            declare(option, 1, line.votes)
            if line.changed is not None and line.candidates:
                value(option, 23, line.changed)
            if line.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=line.seats)
            for c in line.candidates:
                tag = f"{name}:{line.letter}:{c.position}"
                person = p.id("person", tag)
                p.add("person", id=person, birth_precision=None)
                p.name(person, c.name, "is", "official")
                candidate_option = p.id("option", tag)
                p.add(
                    "contest_option",
                    id=candidate_option,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=option,
                    ballot_position=c.position,
                    ballot_label=c.name,
                )
                candidacy = p.id("candidacy", tag)
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    list_position=c.position,
                    ballot_name=c.name,
                )
                value(candidate_option, 10, c.weighted)
                if c.rank <= line.seats:
                    p.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        candidacy_id=candidacy,
                        kind="elected",
                    )
        for number, person_row in enumerate(report.people, 1):
            tag = f"{name}:{number}"
            person = p.id("person", tag)
            p.add("person", id=person, birth_precision=None)
            p.name(person, person_row.name, "is", "official")
            option = p.id("option", tag)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                ballot_position=number,
                ballot_label=person_row.name,
            )
            p.add(
                "candidacy",
                id=p.id("candidacy", tag),
                person_id=person,
                option_id=option,
                role="substitute" if person_row.deputy else "list_member",
                list_position=person_row.deputy or None,
                ballot_name=person_row.name,
            )
            value(option, 1, person_row.votes)
            if not person_row.deputy:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
    return p


def packages() -> list[str]:
    return [f"{h.SOURCE}/{EKEY}", KEY]


def parse() -> None:
    for event in h.events():
        if event.kind == "local" and event.day.first.year == 2022:
            h.build(event).write()
    build().write()


def emit() -> None:
    package.run("load", packages())
