"""Readers for the layout text of the national election authority's result PDFs."""

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

PERCENT = r"\d+(?:[.,]\d+)?\s?%"


def pages(path: Path) -> Iterator[str]:
    """Yield each page's layout text, reading `pdftotext` output as it arrives."""
    with subprocess.Popen(["pdftotext", "-layout", str(path), "-"], stdout=subprocess.PIPE, text=True) as proc:
        assert proc.stdout is not None
        buffer: list[str] = []
        for line in proc.stdout:
            if "\f" in line:
                head, _, tail = line.partition("\f")
                buffer.append(head)
                yield "".join(buffer)
                buffer = [tail]
            else:
                buffer.append(line)
        if "".join(buffer).strip():
            yield "".join(buffer)
    if proc.returncode:
        raise RuntimeError(f"pdftotext failed on {path}")


def number(text: str) -> int:
    return int(re.sub(r"[^\d]", "", text.split(",")[0]) or 0)


def clean(text: str) -> str:
    return " ".join(text.split())


@dataclass
class Count:
    """One reporting unit's accounting lines and its candidate votes in ballot order."""

    registered: int | None = None
    added: int | None = None
    voters: int | None = None
    blank: int | None = None
    invalid: int | None = None
    blank_invalid: int | None = None
    valid: int | None = None
    votes: dict[int, int] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)
    parties: dict[int, str] = field(default_factory=dict)
    total: int | None = None
    place: dict[str, str] = field(default_factory=dict)


CANDIDATE = re.compile(rf"^\s*(\d{{1,3}})\s{{2,}}(\S.*?)\s{{2,}}(\d[\d ]*)\s+({PERCENT})\s*$")
CANDIDATE_PARTY = re.compile(rf"^\s*(\d{{1,3}})\s{{2,}}(\S.*?)\s{{2,}}(\S.*?)\s{{2,}}(\d[\d ]*)\s+({PERCENT})\s*$")
INSCRITS = re.compile(r"Inscrits\s*:\s*([\d ]+?)\s+Rajouts?(?:\(s\))?\s*:\s*([\d ]+)", re.IGNORECASE)


def accounting(text: str, count: Count) -> None:
    """Read the registered, voters, blank, invalid, and valid lines that every format prints in similar words."""
    found = INSCRITS.search(text)
    if found:
        count.registered, count.added = number(found[1]), number(found[2])
    for pattern, attribute in (
        (r"^\s*Votants\s*:\s*([\d ]+?)(?:\s{2,}|$)", "voters"),
        (r"^\s*Suffrages exprim\S+s\s*:\s*([\d ]+?)(?:\s{2,}|$)", "valid"),
        (r"^\s*Blancs et Nuls\s*:\s*([\d ]+?)(?:\s{2,}|$)", "blank_invalid"),
    ):
        found = re.search(pattern, text, re.MULTILINE)
        if found:
            setattr(count, attribute, number(found[1]))
    found = re.search(r"^\s*Blancs\s*:\s*([\d ]+?)\s*(?:Nuls\s*:\s*([\d ,]+?))?(?:\s{2,}|$)", text, re.MULTILINE)
    if found:
        count.blank = number(found[1])
        if found[2]:
            count.invalid = number(found[2])
    if count.invalid is None:
        found = re.search(r"^\s*Nuls\s*:\s*([\d ,]+?)(?:\s{2,}|$)", text, re.MULTILINE)
        if found:
            count.invalid = number(found[1])
    found = re.search(r"Total (?:des )?voix\s*:\s*([\d ]+)", text)
    if found:
        count.total = number(found[1])


def station_blocks(path: Path) -> Iterator[Count]:
    """The 2018 presidential results by polling station; a block can run across a page break."""
    text = "\n".join(pages(path))
    for page in re.split(r"(?=RESULTAT PAR BV)", text):
        if "Bureau de vote" not in page:
            continue
        count = Count()
        head = re.search(r"R\S+gion\s*:\s*(.*?)\s{2,}District\s*:\s*(.*?)\s{2,}Commune\s*:\s*(.*)", page)
        centre = re.search(r"Fokontany\s*:\s*(.*?)\s{2,}Centre de vote\s*:\s*(\d+)-(.*)", page)
        bureau = re.search(r"(?<!\d)(\d{12})-(.*)", page)
        if not (head and bureau):
            raise ValueError(f"unreadable station header: {page[:700]!r}")
        count.place = {
            "region": clean(head[1]),
            "district": clean(head[2]),
            "commune": clean(head[3]),
            "fokontany": clean(centre[1]) if centre else "",
            "code": bureau[1],
            "name": clean(bureau[2]),
        }
        accounting(page, count)
        for line in page.splitlines():
            found = CANDIDATE.match(line)
            if found:
                count.votes[int(found[1])] = number(found[3])
                count.names[int(found[1])] = clean(found[2])
        yield count


def commune_blocks(path: Path) -> Iterator[Count]:
    """The 2018 presidential results by commune, and the same layout for national and district recaps."""
    for page in pages(path):
        head = re.search(r"R\S+gion\s*:\s*(.*?)\s{2,}District\s*:\s*(.*)", page)
        commune = re.search(r"COMMUNE\s*:\s*(.*)", page)
        if not head:
            continue
        count = Count()
        count.place = {
            "region": clean(head[1]),
            "district": clean(head[2]),
            "commune": clean(commune[1]) if commune else "",
        }
        accounting(page, count)
        for line in page.splitlines():
            found = CANDIDATE.match(line)
            if found:
                count.votes[int(found[1])] = number(found[3])
                count.names[int(found[1])] = clean(found[2])
        yield count


def national(path: Path) -> Count:
    count = Count()
    text = "\n".join(pages(path))
    accounting(text, count)
    for line in text.splitlines():
        found = CANDIDATE.match(line)
        if found:
            count.votes[int(found[1])] = number(found[3])
            count.names[int(found[1])] = clean(found[2])
    return count


@dataclass
class Row:
    """One polling station's line in a commune recapitulation table."""

    province: str
    region: str
    district: str
    commune: str
    fokontany: str
    code: str
    name: str
    registered: int
    voters: int
    blank: int
    invalid: int
    valid: int
    votes: list[int]
    added: int = 0


RECAP_2023 = re.compile(
    rf"^\s*(\d{{12}})\s+(\S.*?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+((?:\d+\s+{PERCENT}\s*)+)$"
)
RECAP_2024 = re.compile(
    r"^\s*(\d{12})\s+(\S.*?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+((?:\d+\s+soit\s+[\d.,]+\s?%\s*)+)$"
)


def recap(path: Path, year: int) -> Iterator[Row]:
    """Polling-station rows from the commune recapitulation tables, with the place headers that precede them."""
    context = {"PROVINCE": "", "REGION": "", "DISTRICT": "", "COMMUNE": "", "FOKONTANY": ""}
    for page in pages(path):
        for line in page.splitlines():
            head = re.match(r"^\s*(PROVINCE|REGION|DISTRICT|COMMUNE|FOKONTANY)\s*:\s*(.*?)\s*$", line)
            if head:
                context[head[1]] = clean(head[2])
                if head[1] == "COMMUNE":
                    context["FOKONTANY"] = ""
                continue
            if year == 2023:
                found = RECAP_2023.match(line)
                if found:
                    votes = [number(part) for part in re.findall(rf"(\d+)\s+{PERCENT}", found[8])]
                    yield Row(
                        context["PROVINCE"],
                        context["REGION"],
                        context["DISTRICT"],
                        context["COMMUNE"],
                        context["FOKONTANY"],
                        found[1],
                        clean(found[2]),
                        number(found[3]),
                        number(found[4]),
                        number(found[5]),
                        number(found[6]),
                        number(found[7]),
                        votes,
                    )
            else:
                found = RECAP_2024.match(line)
                if found:
                    votes = [number(part) for part in re.findall(r"(\d+)\s+soit", found[9])]
                    yield Row(
                        context["PROVINCE"],
                        context["REGION"],
                        context["DISTRICT"],
                        context["COMMUNE"],
                        context["FOKONTANY"],
                        found[1],
                        clean(found[2]),
                        number(found[3]),
                        number(found[5]),
                        number(found[6]),
                        number(found[7]),
                        number(found[8]),
                        votes,
                        added=number(found[4]),
                    )


@dataclass
class Candidate:
    number: int
    name: str
    party: str
    votes: int


def segments(line: str) -> list[tuple[int, str]]:
    """A line's text segments with their start columns, split on runs of two or more spaces."""
    return [(found.start(), found[0].strip()) for found in re.finditer(r"\S+(?: \S+)*", line)]


def table(page: str) -> tuple[list[Candidate], int | None]:
    """Candidates from a table whose columns are number, name, party, votes, and percent; a name or a party that wraps
    continues on the lines that follow the line carrying the number and the votes."""
    out: list[Candidate] = []
    names: list[list[str]] = []
    parties: list[list[str]] = []
    name_at = 0
    total = None
    seen_header = False
    for line in page.splitlines():
        if not seen_header:
            seen_header = "Nom " in line and "Voix" in line
            continue
        found = re.search(r"Total(?:\s+des)?\s+voix\s*:\s*([\d ]+)", line)
        if found:
            total = number(found[1])
            break
        if not line.strip():
            continue
        start = re.match(r"^\s{0,12}(\d{1,3})\s{2,}(\S.*?)\s*$", line)
        only = re.match(rf"^\s{{20,}}(\d[\d ]*?)\s+{PERCENT}\s*$", line)
        if start:
            text = start[2]
            votes = re.search(rf"(?:\s|(?<=\)))(\d[\d ]*?)\s+{PERCENT}$", text) or re.search(
                rf"(?<=[A-Za-z])(\d[\d ]*?)\s+{PERCENT}$", text
            )
            if votes:
                text = text[: votes.start()]
            parts = re.split(r"\s{2,}", text.strip())
            name_at = line.index(parts[0])
            out.append(Candidate(int(start[1]), "", "", number(votes[1]) if votes else -1))
            names.append([parts[0]])
            parties.append(parts[1:])
        elif only and out and out[-1].votes < 0:
            out[-1].votes = number(only[1])
        elif out:
            if out[-1].votes < 0:
                pair = re.search(rf"(?:^|\s)(\d[\d ]*?)\s+{PERCENT}", line)
                alone = re.fullmatch(r"\s{20,}(\d[\d ]*?)\s*", line)
                lead = re.match(r"^(\s{40,})(\d+(?: \d{3})*)(?=\s)(.*)$", line)
                if pair:
                    out[-1].votes = number(pair[1])
                    line = line[: pair.start()] + " " * (pair.end() - pair.start()) + line[pair.end() :]
                elif alone:
                    out[-1].votes = number(alone[1])
                    continue
                elif lead:
                    out[-1].votes = number(lead[2])
                    line = lead[1] + " " * len(lead[2]) + lead[3]
            line = re.sub(rf"(?:^|(?<=\s)){PERCENT}", lambda found: " " * len(found[0]), line)
            for column, segment in segments(line):
                (names if abs(column - name_at) <= 2 else parties)[-1].append(segment)
    for candidate, name, party in zip(out, names, parties, strict=True):
        candidate.name = clean(" ".join(name))
        candidate.party = clean(" ".join(party))
    return out, total


def malagasy(text: str, count: Count) -> None:
    """Accounting lines of the Malagasy-language result sheets."""
    for pattern, attribute in (
        (r"Voasoratra anarana\s*:\s*([\d ]+?)(?:\s{2,}|$)", "registered"),
        (r"Natovana\s*:\s*([\d ]+)", "added"),
        (r"Tonga nifidy\s*:\s*([\d ]+?)(?:\s{2,}|$)", "voters"),
        (r"Vato fotsy\s*:\s*([\d ]+?)(?:\s+na|\s{2,}|$)", "blank"),
        (r"(?:[Vv]ato manan-?kery|Suffrages exprim\S+s)\s*:\s*([\d ]+?)(?:\s{2,}|$)", "valid"),
    ):
        found = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
        if found:
            setattr(count, attribute, number(found[1]))
    spoiled = [
        value
        for _, value in sorted(
            (m.start() - text.rfind("\n", 0, m.start()), number(m[1]))
            for m in re.finditer(r"[Vv]ato maty\s*:\s*(\d[\d ]*?)(?=\s|$)", text)
        )
    ]
    if spoiled:
        count.invalid = spoiled[-1]
        if count.blank is None and len(spoiled) > 1:
            count.blank = spoiled[0]
    found = re.search(r"Total\w*(?:'ny vato azo)?\s*:\s*([\d ]+)", text)
    if found:
        count.total = number(found[1])


@dataclass
class Sheet:
    place: dict[str, str]
    count: Count
    candidates: list[Candidate]

    def __post_init__(self) -> None:
        self.count.place = self.place


def district_sheets_2019(path: Path) -> Iterator[Sheet]:
    for page in pages(path):
        head = re.search(r"R\S+gion\s*:\s*(.+)\n\s*District\s*:\s*(.+)", page)
        if not head:
            continue
        count = Count()
        accounting(page, count)
        candidates, total = table(page)
        count.total = total
        yield Sheet({"region": clean(head[1]), "district": clean(head[2])}, count, candidates)


def commune_sheets_2019(path: Path) -> Iterator[Sheet]:
    for page in pages(path):
        head = re.search(
            r"R\S+gion\s*:\s*(.+)\n\s*District\s*:\s*(.+)\n\s*Commune\s*:\s*(?:(\d+)\s*-\s*)?(.+)",
            page,
            re.IGNORECASE,
        )
        if not head:
            continue
        count = Count()
        accounting(page, count)
        candidates, total = table(page)
        count.total = total
        for candidate in candidates:
            found = re.fullmatch(r"(.*?)\s*\((.*)\)", candidate.name)
            if found and not candidate.party:
                candidate.name, candidate.party = clean(found[1]), clean(found[2])
        place = {"region": clean(head[1]), "district": clean(head[2]), "commune": clean(head[4].split("BV")[0])}
        if head[3]:
            place["code"] = head[3]
        yield Sheet(place, count, candidates)


def district_sheets_2024(path: Path) -> Iterator[Sheet]:
    """The 2024 legislative district sheets: votes sit above each name, and vote totals are also spelled in words."""
    sheet: Sheet | None = None
    for page in pages(path):
        district = re.search(r"Distrika\s*:\s*(.+?)\s{2,}", page + "  ")
        region = re.search(r"(?:Faritra|R\S+gion)\s*:\s*(.+)", page)
        if not (district and region):
            continue
        place = {"region": clean(region[1]), "district": clean(district[1])}
        if sheet is not None and sheet.place == place:
            target = sheet
        else:
            target = Sheet(place, Count(), [])
            malagasy(page, target.count)
        lines = page.splitlines()
        begin = next((i for i, line in enumerate(lines) if "Laharana" in line), len(lines))
        names: list[str] = []
        parties: list[str] = []
        votes: list[int] = []
        numbers: list[int] = []
        state = "party"
        for line in lines[begin + 1 :]:
            if re.search(r"T\w*(?:taliny|\s+voix)\s*:", line):
                found = re.search(r"T\w*(?:taliny|\s+voix)\s*:\s*([\d ]+?)\s{2,}", line + "  ")
                if found:
                    target.count.total = number(found[1])
                break
            if not line.strip():
                continue
            vote = re.search(rf"\s(\d{{1,7}})(?:\s+{PERCENT})?\s*$", line)
            if vote and vote.start(1) >= 70:
                votes.append(int(vote[1]))
            left = line[:84]
            indent = len(left) - len(left.lstrip())
            text = clean(left)
            marker = re.match(r"^\s{1,14}(\d{1,3})\s*$", left[:30].rstrip())
            if marker and indent < 20:
                numbers.append(int(marker[1]))
                state = "party"
                continue
            if not text or re.fullmatch(r"\d+", text):
                continue
            if indent >= 28:
                if state == "party":
                    names.append(text)
                    parties.append("")
                    state = "name"
                else:
                    names[-1] += " " + text
            elif names:
                parties[-1] = clean(parties[-1] + " " + text)
        if len(names) != len(votes) or len(names) != len(numbers):
            raise ValueError(f"{place}: {len(names)} names, {len(votes)} votes, {len(numbers)} numbers")
        target.candidates += [Candidate(n, a, b, v) for n, a, b, v in zip(numbers, names, parties, votes, strict=True)]
        if target is not sheet:
            if sheet is not None:
                yield sheet
            sheet = target
    if sheet is not None:
        yield sheet


RUNOFF_ROW = re.compile(r"^\s*(\d{12})\s+(\S.*?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$")


def runoff_2018(path: Path) -> Iterator[Row]:
    """The 2018 runoff by polling station: a table per fokontany with one column per remaining candidate."""
    context = {"REGION": "", "DISTRICT": "", "COMMUNE": "", "FOKONTANY": ""}
    for page in pages(path):
        for line in page.splitlines():
            head = re.match(r"^\s*(REGION|DISTRICT|COMMUNE|FOKONTANY)\s*:\s*(.*?)\s*$", line)
            if head:
                context[head[1]] = clean(head[2])
                continue
            found = RUNOFF_ROW.match(line)
            if found:
                yield Row(
                    "",
                    context["REGION"],
                    context["DISTRICT"],
                    context["COMMUNE"],
                    context["FOKONTANY"],
                    found[1],
                    clean(found[2]),
                    number(found[3]),
                    number(found[5]),
                    number(found[6]),
                    number(found[7]),
                    number(found[8]),
                    [number(found[9]), number(found[10])],
                    added=number(found[4]),
                )


@dataclass
class Item:
    top: int
    left: int
    text: str
    italic: bool


def xml_pages(path: Path) -> Iterator[list[Item]]:
    """Yield each page's text items from `pdftohtml -xml`, which keeps the italic face that sets parties apart from
    candidate names."""
    import xml.etree.ElementTree as ET

    result = subprocess.run(
        ["pdftohtml", "-xml", "-i", "-stdout", "-q", str(path)], capture_output=True, check=True
    ).stdout.decode("utf-8", "replace")
    root = ET.fromstring(re.sub(r"<!DOCTYPE[^>]*>", "", result))
    faces = {spec.get("id"): "italic" in (spec.get("family") or "").lower() for spec in root.iter("fontspec")}
    for page in root.iter("page"):
        items = []
        for node in page.iter("text"):
            text = clean("".join(node.itertext()))
            if text:
                items.append(
                    Item(int(node.get("top", 0)), int(node.get("left", 0)), text, faces.get(node.get("font"), False))
                )
        yield items


def line_text(items: list[Item]) -> str:
    """The page's items as layout-like lines: items within a few pixels of one another's top share a line."""
    rows: list[list[Item]] = []
    for item in sorted(items, key=lambda it: (it.top, it.left)):
        if rows and abs(rows[-1][0].top - item.top) <= 3:
            rows[-1].append(item)
        else:
            rows.append([item])
    return "\n".join("  ".join(it.text for it in sorted(row, key=lambda it: it.left)) for row in rows)


def candidates_xml(items: list[Item], votes_left: int) -> list[Candidate]:
    """Candidates in the order the page prints them: names in plain type, parties in italics, a ballot number
    and a vote count apiece."""
    ordered = sorted(items, key=lambda it: (it.top, it.left))
    header = next((it for it in ordered if it.text in ("Laharana", "Lisitry ny Kandida", "Lisitry ny kandida")), None)
    floor = (header.top if header else 0) + 30
    numbers = [it for it in ordered if it.top > floor and it.left < 120 and re.fullmatch(r"\d{1,3}", it.text)]
    votes = [
        it
        for it in ordered
        if it.top > floor and abs(it.left - votes_left) < 90 and re.fullmatch(r"\d[\d ]*", it.text) and it.left > 300
    ]
    groups: list[tuple[list[str], list[str]]] = []
    last_italic = True
    for it in ordered:
        if it.top <= floor or it.left < 200 or it.left > votes_left - 60 or it.text in ("Antoko", "Antoko "):
            continue
        if re.search(r"Total\w*|Vato azo", it.text) and not it.italic:
            continue
        if not it.italic and last_italic:
            groups.append(([it.text], []))
        elif groups:
            (groups[-1][1] if it.italic else groups[-1][0]).append(it.text)
        last_italic = it.italic
    if not (len(numbers) == len(votes) == len(groups)):
        raise ValueError(f"{len(numbers)} numbers, {len(votes)} votes, {len(groups)} candidates")
    return [
        Candidate(number(a.text), clean(" ".join(b[0])), clean(" ".join(b[1])), number(c.text))
        for a, b, c in zip(numbers, groups, votes, strict=True)
    ]


def commune_sheets_2024(path: Path) -> Iterator[Sheet]:
    """The 2024 mayoral sheets: one per commune in a district's PDF."""
    for items in xml_pages(path):
        text = line_text(items)
        region = re.search(r"FARITRA\s*:\s*(.+)", text)
        district = re.search(r"DISTRIKA\s*:\s*(.+)", text)
        commune = re.search(r"KAOMININA\s*:\s*(.+)", text)
        if not (region and district and commune):
            continue
        count = Count()
        malagasy(text, count)
        vato = next((it for it in items if it.text == "Vato azo"), None)
        place = {"region": clean(region[1]), "district": clean(district[1]), "commune": clean(commune[1])}
        yield Sheet(place, count, candidates_xml(items, vato.left if vato else 620))


def national_2023(path: Path) -> Count:
    """The 2023 national recapitulation, printed in Malagasy: a counts block and one line per candidate."""
    text = "\n".join(pages(path))
    count = Count()
    found = re.search(r"Nisoratra anarana:\s*(\d+)\s*/\s*(\d+)", text)
    if found:
        count.registered = int(found[1])
    for pattern, attribute in (
        (r"Nandatsa-bato:\s*(\d+)", "voters"),
        (r"Vato manan-kery:\s*(\d+)", "valid"),
        (r"Vato fotsy:\s*(\d+)", "blank"),
        (r"Vato maty:\s*(\d+)", "invalid"),
        (r"Totaliny:\s*(\d+)", "total"),
    ):
        found = re.search(pattern, text)
        if found:
            setattr(count, attribute, int(found[1]))
    lines = text.splitlines()
    for index, line in enumerate(lines):
        found = re.match(rf"^\s*Kandida (\d+)\s+(\S.*?)\s{{2,}}(\d+)\s+{PERCENT}\s*$", line)
        if found:
            count.names[int(found[1])] = clean(found[2])
            count.votes[int(found[1])] = int(found[3])
            continue
        found = re.match(rf"^\s*Kandida (\d+)\s{{2,}}(\d+)\s+{PERCENT}\s*$", line)
        if found:
            label = [clean(lines[index - 1])]
            if index + 1 < len(lines) and re.fullmatch(r"\s*\S{1,6}\s*", lines[index + 1]):
                label.append(clean(lines[index + 1]))
            count.names[int(found[1])] = clean(" ".join(label))
            count.votes[int(found[1])] = int(found[2])
    return count


@dataclass
class Ballot:
    """One senatorial candidate's vote line, with the name and party text on it and on the lines around it."""

    number: int
    votes: int
    on_line: dict[str, list[str]]
    around: dict[str, list[tuple[int, str]]] = field(default_factory=lambda: {"name": [], "party": []})


def senate_sheet(path: Path) -> Sheet:
    """One province's senatorial sheet. A candidate's name and party each wrap onto the lines above and below the line
    that prints the ballot number and the votes, so every other line joins the nearest vote line, by its column."""
    text = "\n".join(pages(path))
    lines = text.splitlines()
    province = re.search(r"dans la PROVINCE\s+(\S+(?: \S+)*?)\s*\(", text)
    if not province:
        raise ValueError(f"{path} names no province")
    count = Count()
    accounting(text, count)
    head = next(i for i, line in enumerate(lines) if "Entit" in line and "Voix" in line)
    end = next(i for i, line in enumerate(lines) if "Total voix" in line)
    boundary = (lines[head].index("Nom et") + lines[head].index("Entit")) // 2
    ballots: dict[int, Ballot] = {}
    for i in range(head + 1, end):
        parts = segments(lines[i])
        if len(parts) >= 3 and parts[0][1].isdigit() and re.fullmatch(PERCENT, parts[-1][1]):
            middle = parts[1:-2]
            ballots[i] = Ballot(
                int(parts[0][1]),
                number(parts[-2][1]),
                {"name": [t for a, t in middle if a < boundary], "party": [t for a, t in middle if a >= boundary]},
            )
    for i in range(head + 1, end):
        if i in ballots:
            continue
        for start, piece in segments(lines[i]):
            kind = "party" if start >= boundary else "name"
            distance = sorted((abs(at - i), at) for at in ballots)
            near = [at for d, at in distance if d == distance[0][0]]
            if len(near) > 1:
                near = [at for at in near if not ballots[at].on_line[kind]]
            if len(near) != 1:
                raise ValueError(f"{path}: line {i} {piece!r} cannot be placed with one candidate")
            ballots[near[0]].around[kind].append((i, piece))
    candidates = []
    for at, ballot in sorted(ballots.items()):
        full: dict[str, str] = {}
        for kind in ("name", "party"):
            above = [t for i, t in ballot.around[kind] if i < at]
            below = [t for i, t in ballot.around[kind] if i > at]
            full[kind] = clean(" ".join([*above, *ballot.on_line[kind], *below]))
        candidates.append(Candidate(ballot.number, full["name"], full["party"], ballot.votes))
    count.votes = {c.number: c.votes for c in candidates}
    total = re.search(r"Total voix\s*:\s*([\d ]+?)\s", text)
    count.total = number(total[1]) if total else None
    return Sheet({"province": clean(province[1])}, count, candidates)
