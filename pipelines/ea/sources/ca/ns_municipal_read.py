"""Read the Halifax Regional Municipality's published election results (PDF)."""

import re
import subprocess
import warnings
from dataclasses import dataclass, field
from pathlib import Path


def clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").replace("‐", "-").replace("‑", "-").split())


def fix(text: str) -> str:
    """Undo the CP437 reading of UTF-8 that some of the 2024 tables carry (`├ë` for `É`)."""
    if "├" in text:
        try:
            return text.encode("cp437").decode("utf-8")
        except UnicodeEncodeError, UnicodeDecodeError:
            return text
    return text


def person(text: str) -> str:
    """A candidate's name as 'Given Surname' in title case, from 'SURNAME, Given' or 'Given SURNAME'."""
    text = clean(text).replace("_", "-")
    if "," in text:
        surname, given = (part.strip() for part in text.split(",", 1))
        text = f"{given} {surname}"
    words = []
    for word in text.split():
        if word.isupper() and len(word) > 1:
            word = word.title()
            word = re.sub(r"^(Mc|Mac(?=[A-Z]))([a-z])", lambda m: m.group(1) + m.group(2).upper(), word)
            word = re.sub(r"(-|')([a-z])", lambda m: m.group(1) + m.group(2).upper(), word)
        words.append(word)
    return " ".join(words)


@dataclass
class Race:
    label: str  # "Mayor" or "District 5"
    names: list[str]
    votes: list[int]
    rejected: int | None = None
    total: int | None = None
    electors: int | None = None
    acclaimed: bool = False


def text_lines(path: Path) -> list[str]:
    import fitz

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lines: list[str] = []
        for page in fitz.open(path):
            lines.extend(clean(line) for line in page.get_text("text").split("\n") if line.strip())
    return lines


_HEAD = re.compile(r"^(Mayor|District \d+)$")
_NUM = re.compile(r"^\d[\d,]*$")


def summary(path: Path) -> list[Race]:
    """The race blocks of a summary results sheet: a label, the candidate names, then the figures."""
    lines = text_lines(path)
    for n, line in enumerate(lines):  # the poll-by-poll tables follow the summary
        if line == "Location" or line.startswith("DISTRICT "):
            lines = lines[:n]
            break
    races: list[Race] = []
    i = 0
    while i < len(lines):
        if not _HEAD.match(lines[i]) or (i and lines[i - 1].startswith("Halifax Regional Council")):
            i += 1  # the second condition skips a stray running heading
            continue
        label = lines[i]
        i += 1
        if i < len(lines) and lines[i] == "Mayor":  # a section title repeats the label
            i += 1
        if i < len(lines) and "Acclaimed" in lines[i]:
            name = re.sub(r"\s*-\s*Acclaimed.*$", "", lines[i]).replace("Nicoll-", "Nicoll").strip()
            races.append(Race(label, [person(name)], [], acclaimed=True))
            i += 1
            continue
        names: list[str] = []
        while i < len(lines) and lines[i] not in ("Total Votes Cast", "Rejected"):
            names.append(lines[i])
            i += 1
        rejected_header = i < len(lines) and lines[i] == "Rejected"
        while i < len(lines) and lines[i] in ("Rejected", "Total Votes Cast", "Elector Count", "Participation %"):
            i += 1
        nums: list[int] = []
        while i < len(lines) and (_NUM.match(lines[i]) or lines[i].endswith("%")):
            if _NUM.match(lines[i]):
                nums.append(int(lines[i].replace(",", "")))
            i += 1
        want = len(names) + (1 if rejected_header else 0) + 2
        if len(nums) != want:
            raise ValueError(f"{path.name} {label}: {len(names)} names but {len(nums)} figures")
        votes = nums[: len(names)]
        rest = nums[len(names) :]
        rejected = rest[0] if rejected_header else None
        races.append(
            Race(label, [person(n) for n in names], votes, rejected=rejected, total=rest[-2], electors=rest[-1])
        )
    return races


@dataclass
class Poll:
    code: str
    name: str
    registered: int
    valid: int
    rejected: int
    votes: list[int]


@dataclass
class Sheet:
    district: int
    title: str
    names: list[str]
    polls: list[Poll] = field(default_factory=list)
    totals: list[int] = field(default_factory=list)  # the district totals row's candidate votes
    total_valid: int = 0
    total_registered: int = 0
    total_rejected: int = 0


_ROW = re.compile(r"^(?P<poll>\S.*?)\s+(?P<table>\d{1,2})\s+(?P<rest>[\d,]+(?:\s+[\d,]+)*\s+[\d.]+%\s+[\d,\s]*)$")
_POLL = re.compile(r"^(?P<code>\d{2}(?:\d{2}|AP|[A-Z]{2}))\s+(?P<name>.+)$")


def _ints(text: str) -> list[int]:
    found: list[int] = []
    for token in text.split():
        if not re.fullmatch(r"\d[\d,]*", token):
            break  # a note after the figures
        found.append(int(token.replace(",", "")))
    return found


def _split(text: str) -> tuple[list[int], list[int]]:
    """The figures before the turnout percentage and the figures after it."""
    left, right = re.split(r"\s*\b[\d.]+%\s*", text.strip(), maxsplit=1)
    return _ints(left), _ints(re.sub(r"^(?:[\d.]+%\s*)+", "", right))  # a second percentage column is dropped


def _crowded(chunk: str) -> list[str]:
    """Names packed into one cell with single spaces: a surname ends at its comma, and the given name that
    follows is one word plus any initials."""
    chunk = re.sub(r"\s+,", ",", chunk.strip())
    if chunk.count(",") < 2:
        return [chunk]
    words = chunk.split()
    found: list[str] = []
    current: list[str] = []
    after_comma = False
    for word in words:
        if after_comma and not current[-1].endswith(",") and not re.fullmatch(r"[A-Z]\.", word):
            found.append(" ".join(current))
            current, after_comma = [], False
        current.append(word)
        if word.endswith(","):
            after_comma = True
    found.append(" ".join(current))
    return found


def _full(after: list[int], names: int) -> list[int]:
    if len(after) == names:  # a blank rejected-ballots cell
        return [0, *after]
    return after or [0] * (names + 1)  # or a place that took no votes


def poll_sheet(path: Path) -> Sheet:
    raw = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    lines = [line for line in raw.split("\n") if line.strip()]
    head = next(line for line in lines if re.match(r"^District \d+ ", line.strip()))
    match = re.match(r"^District (\d+)\s+(.+)$", head.strip())
    assert match
    district, title = int(match.group(1)), clean(match.group(2))
    start = next(n for n, line in enumerate(lines) if line.strip().startswith("Poll "))
    names_line = next(line for line in lines[start : start + 6] if "," in line)
    first = re.search(r"\w+, ", names_line)
    names_part = names_line[first.start() :] if first else ""
    chunks = [chunk for chunk in re.split(r"\s{2,}", names_part.strip()) if chunk.strip() and chunk.strip() != "Notes"]
    names = [fix(person(one)) for chunk in chunks for one in _crowded(chunk)]
    sheet = Sheet(district, title, names)
    keep: dict[str, Poll] = {}
    for line in lines[start + 1 :]:
        body = line.strip()
        if body.startswith("District Totals"):
            numbers = _split(re.sub(r"^District Totals\s+", "", body))
            before, after = numbers[0], _full(numbers[1], len(names))
            sheet.total_registered = (
                before[0] + before[1] if len(before) >= 4 and before[0] + before[1] == before[2] else before[0]
            )
            sheet.total_valid = before[-1]
            sheet.total_rejected = after[0]
            sheet.totals = after[1:]
            break
        if re.search(r"merge?rd? above", body, re.IGNORECASE):  # candidate votes of a table merged into the one above
            figures = _ints(re.sub(r"^.*?(?=\d[\d,]*(?:\s+\d[\d,]*){%d}$)" % (len(names) - 1), "", body))
            if len(figures) == len(names) and keep:
                last = keep[next(reversed(keep))]
                last.votes = [a + b for a, b in zip(last.votes, figures, strict=True)]
            continue
        row = re.match(r"^(?P<poll>\S.*?)\s{2,}(?P<table>\d{1,2})\s+(?P<rest>\d.*%.*)$", body)
        if not row:
            continue
        poll = _POLL.match(fix(clean(row.group("poll"))))
        if not poll:
            continue
        before, after = _split(row.group("rest"))
        after = _full(after, len(names))
        registered = before[0] + before[1] if len(before) >= 3 and before[0] + before[1] == before[2] else before[0]
        code = poll.group("code")
        if code in keep:
            one = keep[code]
            one.registered += registered
            one.valid += before[-1]
            one.rejected += after[0]
            one.votes = [a + b for a, b in zip(one.votes, after[1:], strict=True)]
        else:
            keep[code] = Poll(code, poll.group("name").rstrip("*").strip(), registered, before[-1], after[0], after[1:])
    sheet.polls = list(keep.values())
    for one in sheet.polls:
        if len(one.votes) != len(names):
            raise ValueError(f"{path.name} {one.code}: {len(one.votes)} figures for {len(names)} names")
    if len(sheet.totals) != len(names):
        raise ValueError(f"{path.name}: totals row has {len(sheet.totals)} figures for {len(names)} names")
    return sheet


def recount(path: Path) -> tuple[list[str], list[int]]:
    """A recount sheet's candidate surnames and the TOTAL row."""
    raw = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout
    lines = [line for line in raw.split("\n") if line.strip()]
    header = next(line for line in lines if line.strip().startswith("Box"))
    names = header.split()[1:]
    total = next(line for line in lines if line.strip().startswith("TOTAL"))
    return names, _ints(total.split(None, 1)[1])
