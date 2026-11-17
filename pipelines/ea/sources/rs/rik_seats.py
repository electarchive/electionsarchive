"""Seats per list and presidential outcomes for the Republic Electoral Commission's elections.

The Commission declares them in documents, not in its result tables: the reports of results of 2003 to 2014 print each
list's votes beside its seats, the decision allocating the seats of 2016 names every member under the list that won
the seat, and the scanned decisions of 2023 and 2026 do the same. The presidential reports say which candidates
reached the second round and who was elected.
"""

import functools
import itertools
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from ea import raw
from ea.sources.rs import rik

# the report or decision that declares each parliamentary election's seats, and how to read it
REPORT_SEATS = {
    "2003-12-28": "2003-parliament-report",
    "2007-01-21": "2007-report",
    "2008-05-11": "2008-parliament-report",
    "2012-05-06": "2012-parliament-report",
    "2014-03-16": "2014-parliament-report",
}
DECISION_2016 = "2016-decision"

# The decisions of 2023 and 2026 are scans with no text layer. Each entry is the words that identify the list on the
# ballot and the number of members the decision names under it (the last row number printed under its heading less the
# last row number of the list before it); the sums are 250 in both.
SCANNED = {
    "2023-12-17": (
        "2023-decision",
        [
            ("СРБИЈА НЕ СМЕ ДА СТАНЕ", 129),
            ("СРБИЈА ПРОТИВ НАСИЉА", 65),
            ("ИВИЦА ДАЧИЋ", 18),
            ("МИЛОШ ЈОВАНОВИЋ", 13),
            ("МИ-ГЛАС", 13),
            ("VAJDASÁGI", 6),
            ("USAME ZUKORLIĆ", 2),
            ("СДА САНЏАКА", 2),
            ("ШАИП КАМБЕРИ", 1),
            ("РУСКА СТРАНКА", 1),
        ],
    ),
    "2026-10-25": (
        "2026-decision",
        [
            ("СТУДЕНТСКА ЛИСТА", 140),
            ("АЛЕКСАНДАР ВУЧИЋ", 103),
            ("VAJDASÁGI", 2),
            ("АЛБАНСКА КОАЛИЦИЈА", 2),
            ("РАСИМ ЉАЈИЋ", 1),
            ("USAME ZUKORLIĆ", 1),
            ("СДА САНЏАКА", 1),
        ],
    ),
}

# the report that declares each presidential round's outcome; the first round of 2002 and the rounds of 2004, 2008 and
# 2012 name the candidates who go on, and the deciding round names the winner
PRESIDENTIAL = {
    "2002-09-29-president": "2002-report-1",
    "2004-06-13-president": "2004-report-1",
    "2004-06-27-president": "2004-report-2",
    "2008-01-20-president": "2008-report-1",
    "2008-02-03-president": "2008-report-2",
    "2012-05-06-president": "2012-report-1",
    "2012-05-20-president": "2012-report-2",
    "2017-04-02-president": "2017-report",
}
# rounds whose report records that too few voters came out, so no one was elected and nothing is declared for the
# candidates
UNSUCCESSFUL = {
    "2002-10-13-president": "2002-report-2",
    "2002-12-08-president": "2002-report-3",
    "2003-11-16-president": "2003-president-report",
}


@functools.cache
def text(name: str) -> str:
    """The text of a cached report, decision, or publication, converted from the Word or PDF file the Commission posted."""
    path = raw.cached(rik.SOURCE, rik.FILES[name]).path
    if path.suffix.lower() == ".pdf":
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True)
        return out.stdout.decode("utf-8", "replace")
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if office is None:
        raise RuntimeError(f"LibreOffice is needed to read {name}")
    with tempfile.TemporaryDirectory() as work:
        folder = Path(work)
        copy = folder / f"source{path.suffix}"
        shutil.copy(path, copy)
        subprocess.run(
            [
                office,
                "--headless",
                "--convert-to",
                "txt:Text (encoded):UTF8",
                "--outdir",
                str(folder / "out"),
                str(copy),
            ],
            capture_output=True,
            check=True,
            cwd=folder,
        )
        return (folder / "out" / "source.txt").read_text(encoding="utf-8-sig")


LATIN = dict(
    zip(
        "абвгдђежзијклљмнњопрстћуфхцчџш",
        ["a", "b", "v", "g", "d", "dj", "e", "z", "z", "i", "j", "k", "l", "lj", "m", "n", "nj", "o", "p", "r", "s"]
        + ["t", "c", "u", "f", "h", "c", "c", "dz", "s"],
        strict=True,
    )
)


def _letters(value: object) -> str:
    """The letters of a name in plain lowercase Latin, so a Cyrillic and a Latin spelling of it compare equal."""
    lower = str(value).lower().replace("đ", "dj").replace("dž", "dz")
    plain = "".join(LATIN.get(c, c) for c in unicodedata.normalize("NFKD", lower) if not unicodedata.combining(c))
    return "".join(re.findall(r"[a-z]+", plain))


def from_report(name: str, votes: dict[int, int]) -> dict[int, int]:
    """Seats of each list from a report that prints, for every list, its votes and then its seats.

    A list is found by its votes, which the printed report and the Commission's own tables agree on for most lists. Where
    a polling-station table sums to slightly different votes than the report prints, the list takes the printed row
    nearest to its votes, within one percent, and no printed row is taken twice.
    """
    tokens = text(name).replace("\u00a0", " ").replace(";", " ").split()
    rows = [
        (int(token.replace(".", "")), int(after))
        for token, after in itertools.pairwise(tokens)
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+|\d{4,}", token) and after.isdigit() and int(after) <= 250
    ]
    taken: set[int] = set()
    seats: dict[int, int] = {}
    for number, count in sorted(votes.items(), key=lambda item: -item[1]):
        near = sorted((abs(printed - count), i) for i, (printed, _) in enumerate(rows) if i not in taken and printed)
        if not near or near[0][0] > count / 100:
            continue
        if len(near) > 1 and near[1][0] <= count / 100 and rows[near[1][1]][0] == rows[near[0][1]][0]:
            raise ValueError(f"{name}: two printed rows share {rows[near[0][1]][0]:,} votes")
        taken.add(near[0][1])
        if rows[near[0][1]][1]:
            seats[number] = rows[near[0][1]][1]
    return seats


def from_decision_2016(labels: dict[int, str]) -> dict[int, int]:
    """Seats of each list in 2016, counted from the members the decision names under each list's heading."""
    body = text(DECISION_2016)
    headings = list(re.finditer(r"^\s*\d+\)\s+са Изборне листе (.+)$", body, flags=re.MULTILINE))
    seats: dict[int, int] = {}
    for i, heading in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
        members = len(re.findall(r"^\d+\)\s*$", body[heading.end() : end], flags=re.MULTILINE))
        head = _letters(heading.group(1).split(", коју")[0])
        matches = [n for n, label in labels.items() if _letters(label)[:24] == head[:24]]
        if len(matches) != 1:
            raise ValueError(f"2016: the decision's list {heading.group(1)[:60]!r} matches ballot lists {matches}")
        seats[matches[0]] = members
    return seats


def from_scan(slug: str, labels: dict[int, str]) -> dict[int, int]:
    rows = SCANNED[slug][1]
    seats: dict[int, int] = {}
    for needle, count in rows:
        matches = [n for n, label in labels.items() if _letters(needle) in _letters(label)]
        if len(matches) != 1:
            raise ValueError(f"{slug}: {needle!r} matches ballot lists {matches}")
        seats[matches[0]] = count
    return seats


def seats(slug: str, labels: dict[int, str], votes: dict[int, int]) -> tuple[dict[int, int], str] | None:
    """The seats of each list and the cached file that declares them, for a parliamentary election."""
    if slug in REPORT_SEATS:
        return from_report(REPORT_SEATS[slug], votes), REPORT_SEATS[slug]
    if slug == "2016-04-24":
        return from_decision_2016(labels), DECISION_2016
    if slug in SCANNED:
        return from_scan(slug, labels), SCANNED[slug][0]
    return None


def presidential(slug: str, labels: dict[int, str]) -> tuple[dict[int, str], str] | None:
    """Each candidate's declared outcome (`won` or `advanced`) in a presidential round, and the report that says so."""
    if slug not in PRESIDENTIAL:
        return None
    name = PRESIDENTIAL[slug]
    body = text(name)
    found: dict[int, str] = {}

    def locate(candidate: str) -> int:
        words = [_letters(w) for w in candidate.split() if _letters(w) not in {"dr", "prof", "mr"}]
        matches = [n for n, label in labels.items() if all(w in _letters(label) for w in words)]
        if len(matches) != 1:
            raise ValueError(f"{slug}: {candidate!r} matches ballot candidates {matches}")
        return matches[0]

    winner = re.search(r"изабран\s+([^,.\n]+?)\s*[,.]", body)
    if winner:
        found[locate(winner.group(1))] = "won"
    else:
        advance = re.search(r"(?:и то|учествују[^:]*):\s*\n((?:\s*\d\.[^\n]*\n)+)", body)
        if advance is None:
            advance = re.search(r"\n\s*(1\.\s+[^\n]+\n\s*2\.\s+[^\n]+)", body)
        if advance is None:
            raise ValueError(f"{slug}: {name} names neither a winner nor the candidates who go on")
        for line in re.findall(r"\d\.\s+([^\n]+)", advance.group(1)):
            candidate = re.split(r",| кога | кога\b| који ", line)[0].strip()
            found[locate(candidate)] = "advanced"
        if len(found) != 2:
            raise ValueError(f"{slug}: expected two candidates to go on, found {found}")
    return found, name
