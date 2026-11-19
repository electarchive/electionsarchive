"""Readers for the Electoral Commission of Zambia's results notices, from the PDFs' text layers."""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

PERCENT = re.compile(r"^\d+(?:[.,]\d+)?%$")
DECIMAL = re.compile(r"^\d+[.,]\d{1,2}$")
INTEGER = re.compile(r"^\d{1,3}(?:[, ]\d{3})*$|^\d+$")
GAP = re.compile(r"\s{2,}")
CANDIDATE = re.compile(
    r"^(?P<who>.+?)\s*,\s*(?P<party>[^,]+?)\s+(?P<votes>\d[\d,]*)(?:\s+\d+[.,]\d+%)?(?:\s+\d+[.,]\d+%)?\s*$"
)
ORPHAN = re.compile(r"^\s*,\s*(?P<party>[A-Za-z0-9.\- ]{1,20}?)\s*$")
VOTED = re.compile(r"^(?P<who>\D+?)\s+(?P<rest>\d[\d,]*\s+\d+[.,]\d+%.*)$")


@dataclass
class Candidate:
    name: str
    party: str
    votes: int
    gender: str = ""


@dataclass
class Unit:
    code: str
    name: str
    page: int
    candidates: list[Candidate] = field(default_factory=list)
    valid: int | None = None
    rejected: int | None = None
    cast: int | None = None
    registered: int | None = None
    province: str = ""
    district: str = ""
    parent: str = ""
    parent_name: str = ""


def lines(path: Path) -> list[tuple[int, str]]:
    """Every line of a PDF's layout text, with its page number."""
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    out: list[tuple[int, str]] = []
    for page, body in enumerate(text.split("\f"), 1):
        out.extend((page, line.rstrip()) for line in body.split("\n"))
    return unwrapped(out)


WRAPPED = re.compile(r"^\s+(\d[\d,]*\s+\d+[.,]\d+%.*)$")


def unwrapped(rows: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Join a candidate whose name wraps onto a second line, so the party sits below the figures."""
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(rows):
        page, line = rows[i]
        if i + 2 < len(rows) and line.rstrip().endswith(",") and WRAPPED.match(rows[i + 1][1]):
            figures_line = WRAPPED.match(rows[i + 1][1])
            party = rows[i + 2][1].strip()
            if figures_line and re.fullmatch(r"[A-Z0-9.\- ]{1,20}", party):
                out.append((page, f"{line.strip()}{party}   {figures_line[1]}"))
                i += 3
                continue
        if i + 1 < len(rows) and ORPHAN.match(rows[i + 1][1]):
            voted = VOTED.match(line.strip())
            if voted and "," not in voted["who"]:
                party = ORPHAN.match(rows[i + 1][1])
                if party:
                    out.append((page, f"{voted['who']} ,{party['party']}   {voted['rest']}"))
                    i += 2
                    continue
        out.append((page, line))
        i += 1
    return out


def integer(text: str) -> int:
    return int(text.replace(",", "").replace(" ", ""))


def figures(line: str) -> list[int] | None:
    """The whole numbers on a totals line, without percentages and decimals; None for any other line."""
    parts = [part for part in GAP.split(line.strip()) if part]
    if not parts or any(re.search(r"[A-Za-z]", part) for part in parts):
        return None
    numbers: list[int] = []
    for part in parts:
        if PERCENT.match(part) or DECIMAL.match(part):
            continue
        if not INTEGER.match(part):
            return None
        numbers.append(integer(part))
    return numbers or None


def settle(unit: Unit, numbers: list[int]) -> None:
    """Assign a totals line's whole numbers: valid votes are printed only in some years."""
    if len(numbers) == 4:
        unit.valid, unit.rejected, unit.cast, unit.registered = numbers
    elif len(numbers) == 3:
        unit.rejected, unit.cast, unit.registered = numbers
    elif len(numbers) == 5:
        unit.valid, unit.rejected, unit.cast, unit.registered = numbers[0], numbers[1], numbers[2], numbers[3]
    else:
        raise ValueError(f"{unit.code} {unit.name} (page {unit.page}): totals line with {numbers}")


def tidy(name: str) -> str:
    """Collapse spacing, and restore the space some notices lose before a trailing initial (`PeterC.`)."""
    return " ".join(re.sub(r"([a-z])([A-Z]\.)", r"\1 \2", name.replace("’", "'")).split())


def candidate(line: str) -> Candidate | None:
    found = CANDIDATE.match(line.strip())
    if not found:
        return None
    who = tidy(found["who"])
    sex = re.search(r"\s-\s([MF])$", who)
    if sex:
        who = who[: sex.start()].strip()
    return Candidate(who, tidy(found["party"]), integer(found["votes"]), sex[1] if sex else "")


UNIT = re.compile(
    r"^\s*(?P<code>\d{8})\s+,?(?P<name>[A-Z][^|]*?)(?:\s*\|\s*Province:\s*(?P<province>[^|]+?)\s*\|\s*District:\s*(?P<district>.+?))?\s*$"
)
PROVINCE = re.compile(r"^\s*(?:PROVINCE:|Province:)\s*([A-Za-z\- ]+?)\s*$")
CONSTITUENCY = re.compile(r"^\s*(?P<code>\d{8}) ,(?P<name>.+?) CONSTITUENCY\s*$")
WARD = re.compile(r"^WARD:\s*(?P<code>\d+)\s+(?P<name>.+?)\s*$")


def read_listed(path: Path) -> list[Unit]:
    """Presidential and National Assembly notices of 2011 to 2026: a coded heading, candidate lines, a totals line."""
    units: list[Unit] = []
    province = ""
    current: Unit | None = None
    for page, line in lines(path):
        found = PROVINCE.match(line)
        if found:
            province = found[1].strip().title()
            continue
        found = UNIT.match(line)
        if found and not CANDIDATE.match(line.strip()):
            current = Unit(found["code"], tidy(found["name"]), page, province=(found["province"] or province).title())
            current.district = tidy(found["district"] or "").title()
            units.append(current)
            continue
        if current is None:
            continue
        numbers = figures(line)
        if numbers is not None and current.candidates:
            settle(current, numbers)
            current = None
            continue
        row = candidate(line)
        if row is not None:
            current.candidates.append(row)
    return units


def read_wards(path: Path) -> list[Unit]:
    """Councillor notices: constituency headings, then each ward's candidates and totals."""
    units: list[Unit] = []
    constituency = ("", "")
    current: Unit | None = None
    for page, line in lines(path):
        found = CONSTITUENCY.match(line)
        if found:
            constituency = (found["code"], tidy(found["name"]))
            current = None
            continue
        found = WARD.match(line.strip())
        if found:
            current = Unit(
                found["code"], tidy(found["name"]), page, parent=constituency[0], parent_name=constituency[1]
            )
            units.append(current)
            continue
        if current is None:
            continue
        numbers = figures(line)
        if numbers is not None and current.candidates:
            settle(current, numbers)
            current = None
            continue
        row = candidate(line)
        if row is not None:
            current.candidates.append(row)
    return units


def read_notice(path: Path) -> list[Unit]:
    """Parliamentary and mayoral notices of 2016 and 2021: province, district, and constituency columns, then candidates."""
    units: list[Unit] = []
    current: Unit | None = None
    for page, line in lines(path):
        parts = [part for part in GAP.split(line.strip()) if part]
        if not parts:
            continue
        numbers = figures(line)
        if numbers is not None:
            if current is not None and current.candidates and current.cast is None:
                settle(current, numbers)
            continue
        if len(parts) == 1 and re.fullmatch(r"[A-Z]", parts[0]) and current is not None and current.candidates:
            current.candidates[-1].name += f" {parts[0]}"
            continue
        if len(parts) == 2 and re.fullmatch(r"\d[\d,]*", parts[1]) and " " in parts[0]:
            parts = [*parts[0].rsplit(" ", 1), parts[1]]
            parts.insert(0, "")
            parts = [part for part in parts if part]
        if (
            len(parts) < 3
            or not re.fullmatch(r"\d[\d,]*", parts[-1])
            or re.search(r"\d", parts[-2])
            and len(parts[-2]) > 6
        ):
            continue
        votes, party, who = integer(parts[-1]), parts[-2], parts[-3]
        head = parts[:-3]
        if head:
            name = " ".join(head[1:] if len(head) > 1 else head)
            province = head[0].title()
            district = head[1] if len(head) > 2 else ""
            label = head[-1]
            found = re.match(r"^(\d+)\s+(.+)$", label)
            if not found:
                raise ValueError(f"page {page}: unreadable heading {head}")
            current = Unit(found[1], tidy(found[2]), page, province=province, district=tidy(district).title())
            units.append(current)
            del name
        if current is None or current.cast is not None:
            continue
        current.candidates.append(Candidate(tidy(who), tidy(party), votes))
    return units


FOUR = re.compile(r"^\s*(?P<code>\d{1,3})\s{2,}(?P<name>[A-Z][A-Z' .\-]*?)\s*$")
FIGURE_LINE = re.compile(r"^(?P<who>.+?)\s{2,}(?P<party>\S.*?)\s+(?P<votes>\d[\d,]*)\s+\d+\.\d+\s+\d+\.\d+\s*$")


def read_2008(path: Path) -> list[Unit]:
    """The 2008 notice: a numbered constituency heading, candidates with party columns, a `Totals for` line."""
    units: list[Unit] = []
    province = ""
    current: Unit | None = None
    for page, line in lines(path):
        found = re.match(r"^\s*PROVINCE\s+\d+\s+(.+?)\s*$", line)
        if found:
            province = found[1].title()
            continue
        found = FOUR.match(line)
        if found and not found["name"].startswith("TOTAL"):
            current = Unit(f"{int(found['code']):03d}", tidy(found["name"]), page, province=province)
            units.append(current)
            continue
        if current is None:
            continue
        if line.strip().startswith("Totals for"):
            numbers = figures(re.sub(r"^\s*Totals for\s+[A-Z' .\-]+?(?=\s{2,})", "", line))
            if numbers is None:
                raise ValueError(f"page {page}: unreadable totals {line.strip()}")
            settle(current, numbers)
            current = None
            continue
        found = FIGURE_LINE.match(line.strip())
        if found:
            current.candidates.append(Candidate(tidy(found["who"]), tidy(found["party"]), integer(found["votes"])))
    return units


def read_2006(path: Path) -> list[Unit]:
    """The 2006 notice: province, council, and constituency columns on a constituency's first line."""
    units: list[Unit] = []
    province = ""
    current: Unit | None = None
    for page, line in lines(path):
        parts = [part for part in GAP.split(line.strip()) if part]
        if not parts:
            continue
        numbers = figures(line)
        if numbers is not None:
            if current is not None and current.candidates and current.cast is None and len(numbers) >= 4:
                settle(current, numbers[:4])
            continue
        for i in range(len(parts) - 1):
            if re.fullmatch(r"\d{3}", parts[i]) and re.search(r"[A-Za-z]", parts[i + 1]):
                parts[i : i + 2] = [f"{parts[i]} {parts[i + 1]}"]
                break
        at = next((i for i, part in enumerate(parts) if re.fullmatch(r"\d{3} \S.*", part)), None)
        if at is not None:
            head = parts[:at]
            if len(head) == 2:
                province = head[0].title()
            found = re.match(r"^(\d{3}) (.+)$", parts[at])
            assert found
            current = Unit(
                found[1], tidy(found[2]), page, province=province, district=tidy(head[-1]).title() if head else ""
            )
            if current.district == "" and units:
                current.district = units[-1].district
            units.append(current)
            parts = parts[at + 1 :]
        if current is None or current.cast is not None or len(parts) < 3:
            continue
        tail = [part for part in parts if not DECIMAL.match(part)]
        if len(tail) >= 3 and re.fullmatch(r"\d[\d,]*", tail[2]):
            current.candidates.append(Candidate(tidy(tail[0]), tidy(tail[1]), integer(tail[2])))
    return units


def read_2001(path: Path) -> tuple[list[Unit], list[Unit]]:
    """The 2001 report: presidential and then parliamentary constituency tables, each with `Totals for` lines."""
    sections: dict[str, list[Unit]] = {"president": [], "parliament": []}
    section = ""
    province = ""
    current: Unit | None = None
    for page, line in lines(path):
        if "Presidential Election 2001 - Final Results" in line:
            section = "president"
            current = None
        elif "Parliamentary Elections 2001 - Final Results" in line or "Parliamentary Election - 2001" in line:
            section = "parliament"
            current = None
        found = re.match(r"^\s*Province : \d+ - (.+?)\s*$", line)
        if found:
            province = found[1].title()
        if not section:
            continue
        found = re.match(r"^(?P<code>\d{3}) (?P<rest>[A-Z].*\d+\.\d+%.*)$", line)
        if found:
            words = found["rest"].split()
            first = next((i for i, word in enumerate(words) if re.search(r"[a-z]", word)), None)
            if first is None or first < 2:
                raise ValueError(f"page {page}: unreadable heading {line.strip()}")
            current = Unit(found["code"], " ".join(words[: first - 1]), page, province=province)
            sections[section].append(current)
            line = " " * 22 + " ".join(words[first - 1 :])
        if current is None:
            continue
        if line.startswith("Totals for"):
            numbers = figures(re.sub(r"^Totals for\s+.+?(?=\s{2,})", "", line))
            if numbers is None:
                raise ValueError(f"page {page}: unreadable totals {line.strip()}")
            settle(current, numbers)
            current = None
            continue
        row = candidate(line)
        if row is not None:
            current.candidates.append(row)
    return sections["president"], sections["parliament"]


def despace(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


def read_1996(path: Path) -> list[Unit]:
    """The 1996 report: its text layer breaks names apart, so a name is matched on its letters alone."""
    units: list[Unit] = []
    province = ""
    current: Unit | None = None
    for page, line in lines(path):
        found = re.match(r"^\s*PROVI\s*NCE:\s*\d+-(.+?)\s*$", line)
        if found:
            province = re.sub(r"\s+", "", found[1]).title()
            current = None
            continue
        found = re.match(r"^(?P<code>\d{3})\s+(?P<name>[A-Z][A-Z' .\-]*?)\s*(?P<rest>CHAKOM BOKA.*)$", line)
        if found:
            current = Unit(found["code"], found["name"], page, province=province)
            units.append(current)
            line = " " * 22 + found["rest"]
        if current is None or line.strip().startswith("PROVI"):
            continue
        parts = [part for part in GAP.split(line.strip()) if part]
        numbers = figures(line)
        if numbers is not None:
            if current.candidates and current.cast is None and len(numbers) >= 4:
                settle(current, numbers[:4])
                current = None
            continue
        tail = [part for part in parts if not DECIMAL.match(part)]
        if len(tail) == 2 and re.fullmatch(r"\d[\d,]*", tail[1]):
            current.candidates.append(Candidate(tidy(tail[0]), "", integer(tail[1])))
    return units


MONTHS = {
    name: number
    for number, name in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        1,
    )
}


def by_election_day(path: Path) -> tuple[int, int, int]:
    """The date a by-election notice says its polls took place, which can wrap over several lines."""
    head = " ".join(line for _, line in lines(path)[:40])
    head = re.sub(r"Councilor Elections|Council Chairperson|Councillor Elections", " ", head)
    found = re.findall(
        r"took place on\s+\w+,?\s+(\d{1,2})\s*(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", " ".join(head.split())
    )
    if len(found) != 1:
        raise ValueError(f"{path.name}: {len(found)} polling dates in the notice")
    day, month, year = found[0]
    return int(year), MONTHS[month.lower()], int(day)


def read_by_election(path: Path) -> dict[str, list[Unit]]:
    """One notice's tables: council chairperson (`chair`), parliamentary (`assembly`), and councillor (`ward`) results."""
    out: dict[str, list[Unit]] = {"chair": [], "assembly": [], "ward": []}
    table = ""
    province = ""
    current: Unit | None = None
    for page, line in lines(path):
        flat = " ".join(line.split())
        if flat.startswith("PROVINCE DISTRICT"):
            table = "ward" if "WARD NO" in flat else "assembly" if "CONSTITUENCY NO" in flat else "chair"
            current = None
            continue
        if not table:
            continue
        parts = [part for part in GAP.split(line.strip()) if part]
        if not parts:
            continue
        numbers = figures(line)
        if numbers is not None:
            if current is not None and current.candidates and current.cast is None and len(numbers) >= 3:
                current.rejected, current.cast, current.registered = numbers[:3]
                current = None
            continue
        if len(parts) == 2 and re.fullmatch(r"\d[\d,]*", parts[1]) and " " in parts[0]:
            parts = [*parts[0].rsplit(" ", 1), parts[1]]
        if (
            len(parts) >= 2
            and re.fullmatch(r"\d[\d,]*", parts[-1])
            and re.search(r"[a-z]", parts[-2])
            and " " in parts[-2]
        ):
            parts = [*parts[:-2], *parts[-2].rsplit(" ", 1), parts[-1]]
        if (
            len(parts) < 3
            or not re.fullmatch(r"\d[\d,]*", parts[-1])
            or not re.fullmatch(r"[A-Za-z0-9.\- ]{1,12}", parts[-2])
        ):
            continue
        votes, party, who = integer(parts[-1]), parts[-2], parts[-3]
        head = parts[:-3]
        if head:
            province = head[0].title() if len(head) > 1 or table == "chair" else province
            if table == "chair":
                current = Unit("", tidy(head[-1]).title(), page, province=province, district=tidy(head[-1]).title())
                current.code = re.sub(r"[^a-z0-9]+", "-", current.name.lower()).strip("-")
            else:
                found = re.match(r"^(?:(.+?)\s+)?(\d{2,5})\s+(.+)$", head[-1])
                if not found:
                    raise ValueError(f"{path.name} page {page}: unreadable heading {head}")
                district = tidy(head[1] if len(head) > 2 else found[1] or "").title()
                current = Unit(found[2], tidy(found[3]), page, province=province, district=district)
            out[table].append(current)
        if current is None or current.cast is not None:
            continue
        current.candidates.append(Candidate(tidy(who), tidy(party), votes))
    return out


@dataclass
class National:
    """A notice's own national totals: option votes by name or party, and the accounting it prints."""

    votes: dict[str, int] = field(default_factory=dict)
    valid: int | None = None
    rejected: int | None = None
    cast: int | None = None
    registered: int | None = None


NAME_ONLY = re.compile(
    r"^\s*(?P<who>[A-Z][A-Za-z'.\-]*(?: [A-Za-z'.\-]+)*?)\s{2,}(?P<votes>\d[\d,]*)\s+\d+[.,]\d+%\s+\d+[.,]\d+%\s*$"
)


def national_listed(path: Path, marker: str) -> National:
    """The national table that follows `marker` (the last one in the file) in a presidential notice."""
    rows = lines(path)
    at = max(i for i, (_, line) in enumerate(rows) if marker in line)
    out = National()
    for _, line in rows[at + 1 : at + 60]:
        numbers = figures(line)
        if numbers is None and re.fullmatch(r"[\d.,% ]+", line.strip() or "x"):
            numbers = [integer(t) for t in line.split() if not PERCENT.match(t) and not DECIMAL.match(t)]
        if numbers is not None:
            if out.votes:
                if len(numbers) >= 4:
                    out.valid, out.rejected, out.cast, out.registered = numbers[:4]
                elif len(numbers) >= 2:
                    out.valid, out.rejected = numbers[0], numbers[1]
                else:
                    continue
                break
            continue
        row = candidate(line)
        if row is not None:
            out.votes[row.name] = row.votes
            continue
        found = NAME_ONLY.match(line)
        if found:
            out.votes[tidy(found["who"])] = integer(found["votes"])
    return out


def national_2026(path: Path) -> National:
    rows = lines(path)
    at = max(i for i, (_, line) in enumerate(rows) if "Total Votes Obtained" in line)
    out = National()
    for _, line in rows[at + 1 : at + 30]:
        found = re.match(r"^(?P<who>[A-Z ]+?)\s{2,}(?P<votes>\d[\d,]*)(?:\s+\d+\.\d+%)?\s*$", line.strip())
        if not found:
            continue
        who, votes = found["who"].strip(), integer(found["votes"])
        if who == "REJECTED":
            out.rejected = votes
        elif who == "TOTAL VOTES CAST":
            out.cast = votes
        elif who == "ELIGIBLE VOTERS":
            out.registered = votes
        else:
            out.votes[who] = votes
    return out


def national_1996(path: Path) -> National:
    """The 1996 report's national table, whose candidate names are broken apart like its other text."""
    rows = lines(path)
    at = max(i for i, (_, line) in enumerate(rows) if re.match(r"^NATI\s*ONAL TOTALS\s+\S", line))
    out = National()
    for _, line in rows[at : at + 10]:
        found = re.match(
            r"^(?:NATI\s*ONAL TOTALS\s+)?(?P<who>[A-Z][A-Z .'\-]+?)\s{2,}(?P<votes>\d[\d,]*)\s+\d+\.\d+\s+\d+\.\d+\s*$",
            line,
        )
        if found:
            out.votes[found["who"]] = integer(found["votes"])
            continue
        numbers = [integer(t) for t in line.split() if INTEGER.match(t) and not DECIMAL.match(t)]
        if out.votes and len(numbers) >= 4 and re.fullmatch(r"[\d., ]+", line.strip()):
            out.valid, out.rejected, out.cast, out.registered = numbers[:4]
            break
    return out


def read_schedule(path: Path) -> list[tuple[str, str, str]]:
    """A 2026 delimitation schedule's rows as (district, constituency, ward). A district and its constituency are
    printed only on a constituency's first row, so later rows carry them over, across pages too. The columns are
    told apart by where each word starts against the page's header."""
    out: list[tuple[str, str, str]] = []
    district = constituency = ""
    for page in pymupdf.open(path):
        rows: dict[int, list[tuple[float, str]]] = {}
        for word in page.get_text("words"):
            rows.setdefault(round(word[3]), []).append((word[0], word[4]))
        header: dict[str, float] = {}
        for y in sorted(rows):
            words = sorted(rows[y])
            if words[0][1] == "District":
                header = {text: x for x, text in words}
                continue
            line = " ".join(text for _, text in words)
            if not header or re.fullmatch(r"\d+ of \d+", line):
                continue
            columns = ["", "", ""]
            for x, text in words:
                at = 0 if x < header["Constituency"] - 45 else 1 if x < header["Ward"] - 80 else 2
                columns[at] = f"{columns[at]} {text}".strip()
            district = columns[0] or district
            constituency = columns[1] or constituency
            if not (district and constituency and columns[2]):
                raise ValueError(f"{path.name}: row {line!r} lacks a district, constituency, or ward")
            out.append((district, constituency, columns[2]))
    return out
