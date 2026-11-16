"""Readers for the Malawi Electoral Commission's printed constituency and district result tables, 1994 to 2009."""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Result:
    kind: str
    code: str
    name: str
    district: str
    region: str
    candidates: list[tuple[str, str, int]] = field(default_factory=list)
    valid: int | None = None
    null: int | None = None
    cast: int | None = None
    registered: int | None = None
    line: int = 0


def text(path: Path) -> list[str]:
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    return done.stdout.replace("\f", "\n").splitlines()


def number(value: str) -> int:
    return int(value.replace(",", ""))


NUMBER = r"([\d,]+)"
CANDIDATE = re.compile(
    r"^(\S.*?)\s{2,}([A-Za-z][A-Za-z0-9.\-]*(?: [A-Za-z0-9]+)*)\s+" + NUMBER + r"(?:\s+[\d.]+%)?\s*$"
)
VALID = re.compile(r"TOTAL VALID VOTES\s+" + NUMBER, re.IGNORECASE)
NULL = re.compile(r"TOTAL NULL AND VOID VOTES\s+" + NUMBER, re.IGNORECASE)
CAST = re.compile(r"^\s*TOTAL VOTES CAST\s+" + NUMBER, re.IGNORECASE)
REGISTERED = re.compile(r"^\s*REGISTERED VOTERS:?\s+" + NUMBER, re.IGNORECASE)
BARE = re.compile(r"^\s*VOTES\s+" + NUMBER + r"\s*$")


def ppe(lines: list[str]) -> list[Result]:
    """Read the 1994 and 1999 books: constituency tables for the National Assembly, then district tables."""
    out: list[Result] = []
    region = district = ""
    mode = "parliament"
    current: Result | None = None
    bare: list[int] = []

    def close() -> None:
        nonlocal current, bare
        if current is not None and current.candidates:
            if current.valid is None and len(bare) >= 1:
                current.valid = bare[0]
            if current.null is None and len(bare) >= 2:
                current.null = bare[1]
            out.append(current)
        current = None
        bare = []

    for number_, line in enumerate(lines, 1):
        found = re.match(r"^\s*(NORTHERN|CENTRAL|SOUTHERN) REGION", line)
        if found:
            close()
            region = found[1].title()
            continue
        found = re.match(r"^\s*DISTRICT\s*:\s*(.+?)\s+CODE\s*:\s*(\d+)\s*$", line)
        if found:
            close()
            mode = "president"
            current = Result(
                "president", found[2], found[1].strip().title(), found[1].strip().title(), "", line=number_
            )
            continue
        found = re.match(r"^\s*(.+?) DISTRICT(?:\s+CODE\s*:?\s*(\d+))?\s*$", line)
        if found and mode == "parliament":
            close()
            district = found[1].strip().title()
            continue
        found = re.match(r"^\s*(.+?)(?: CONSTITUENCY)?\s+CODE\s*:?\s*(\d+)\s*$", line)
        if found:
            close()
            current = Result("parliament", found[2], found[1].strip().title(), district, region, line=number_)
            continue
        if current is None:
            continue
        for pattern, attribute in ((VALID, "valid"), (NULL, "null"), (CAST, "cast"), (REGISTERED, "registered")):
            found = pattern.search(line)
            if found:
                setattr(current, attribute, number(found[1]))
                break
        else:
            found = BARE.match(line)
            if found:
                bare.append(number(found[1]))
                continue
            found = CANDIDATE.match(line)
            if found and not found[2].upper().startswith("TOTAL"):
                current.candidates.append((found[1].strip(), found[2].strip(), number(found[3])))
    close()
    return out


HEADING = {
    "SUMMARY", "RESULTS", "FOR", "2004", "PARLIAMENTARY", "GENERAL", "ELECTIONS", "%", "OF", "INDIVIDUAL", "VOTES",
    "DISTRICT", "CONSTITUENCY", "CANDIDATE", "PARTY", "AGAINST", "VALID",
}  # fmt: skip


def parliament_2004(path: Path) -> list[Result]:
    """Read the 2004 summary by word position: names there run into the party column in plain text."""
    from ea.sources.mw import pdf

    out: list[Result] = []
    district = ""
    current: Result | None = None
    for page, words in pdf.pages(path):
        for line in pdf.lines(words):
            texts = {word.text for word in line}
            if texts <= HEADING or line[0].text == "MALAWI":
                continue
            if line[0].text == "REGIONAL":
                return out
            fixed: list[pdf.Word] = []
            for word in line:
                fused = re.fullmatch(r"([A-Za-z].*?)(\d{3})", word.text) if word.x < 147 else None
                if fused:
                    fixed += [
                        pdf.Word(word.x, word.y, fused[1], word.x1 - 15),
                        pdf.Word(130.0, word.y, fused[2], 145.0),
                    ]
                else:
                    fixed.append(word)
            line = sorted(fixed, key=lambda word: word.x)
            left = [w for w in line if w.x < 228]
            code = [w for w in left if 125 <= w.x < 147 and re.fullmatch(r"\d{3}", w.text)]
            middle = [w for w in line if 228 <= w.x < 380]
            party = [w for w in line if 380 <= w.x < 435]
            votes = [w for w in line if 435 <= w.x < 478]
            if code:
                names = [w.text for w in left if w.x >= 147]
                if not votes:
                    names = [w.text for w in line if w.x >= 147]
                    middle = []
                places = [w.text for w in left if 66 <= w.x < 125]
                if places:
                    district = " ".join(places).title()
                current = Result("parliament", code[0].text, " ".join(names), district, "", line=page)
                out.append(current)
            if current is None:
                continue
            wide = [w for w in line if 340 <= w.x < 435]
            if wide and wide[0].text in {"Total", "Null"} and line[0].x >= 340 and votes:
                label = " ".join(w.text for w in wide)
                value = int(votes[0].text)
                if label.startswith("Total Valid"):
                    current.valid = value
                elif label.startswith("Null and Void"):
                    current.null = value
                elif label.startswith("Total Votes"):
                    current.cast = value
                elif label.startswith("Total Registered"):
                    current.registered = value
                continue
            if wide and wide[0].text == "%":
                continue
            if middle and votes:
                given = [w.text for w in middle]
                label = [w.text for w in party]
                if label and label[0].isdigit():
                    fused = re.fullmatch(r"(.*[a-z.'-])(IND)", given[-1])
                    if fused is None:
                        raise ValueError(f"{path.name} page {page}: stray party number on {[w.text for w in line]}")
                    given[-1], label = fused[1], [fused[2], *label]
                if not label:
                    fused = re.fullmatch(r"(.*[a-z.'-])([A-Z]{2,}[A-Z0-9]*)", given[-1])
                    if fused is None:
                        raise ValueError(f"{path.name} page {page}: no party on {[w.text for w in line]}")
                    given[-1], label = fused[1], [fused[2]]
                width = 2 if label[0] == "IND" and len(label) > 1 and label[1].isdigit() else 1
                current.candidates.append(
                    (" ".join(given + label[width:]), " ".join(label[:width]), int(votes[0].text))
                )
            elif middle or party or votes:
                raise ValueError(f"{path.name} page {page}: unread line {[w.text for w in line]}")
    return out


def president_2009(lines: list[str]) -> list[Result]:
    out: list[Result] = []
    current: Result | None = None
    row = re.compile(r"^(?:(?:\d+ )?(\S[A-Z .'-]*?)\s{2,})?\s*(\S.*?)\s{2,}([A-Z][A-Za-z]*)\s+(\d+)\s+([\d.]+)\s*$")
    for index, line in enumerate(lines, 1):
        for pattern, attribute in (
            (r"TOTAL VALID VOTES\s+(\d+)", "valid"),
            (r"TOTAL VOTES CAST\s+(\d+)", "cast"),
            (r"TOTAL REGISTERED FIGURES\s+(\d+)", "registered"),
        ):
            found = re.search(pattern, line)
            if found and current is not None:
                setattr(current, attribute, int(found[1]))
                break
        else:
            found = row.match(line)
            if found and not found[2].startswith("CANDIDATE"):
                if found[1]:
                    current = Result(
                        "president", "", found[1].strip().title(), found[1].strip().title(), "", line=index
                    )
                    out.append(current)
                if current is not None:
                    current.candidates.append((found[2].strip(), found[3], int(found[4])))
    for result in out:
        if result.null is None and result.valid is not None and result.cast is not None:
            result.null = result.cast - result.valid
    return out


def parliament_2009(lines: list[str]) -> list[Result]:
    out: list[Result] = []
    current: Result | None = None
    row = re.compile(r"^(\S.*?)\s{2,}([A-Z][A-Za-z]*)\s+([\d,]+)\s*$")
    for index, line in enumerate(lines, 1):
        found = re.match(r"^\s{10,}([A-Z][A-Za-z' .-]*?)\s+(\d{2,3})\s*$", line)
        if found:
            current = Result("parliament", found[2], found[1].strip(), "", "", line=index)
            out.append(current)
            continue
        if current is None:
            continue
        for pattern, attribute in (
            (r"Total Valid Votes\s+([\d,]+)", "valid"),
            (r"Null & Void Votes\s+[\d.]+%\s+([\d,]+)", "null"),
            (r"Total Votes Cast\s+([\d,]+)", "cast"),
            (r"Number of Registered Voters\s+([\d,]+)", "registered"),
        ):
            found = re.search(pattern, line)
            if found:
                setattr(current, attribute, number(found[1]))
                break
        else:
            found = row.match(line)
            if found and found[1] not in {"Candidate"}:
                current.candidates.append((found[1].strip(), found[2], number(found[3])))
    return out


def national_2004(path: Path) -> tuple[int, int, int, int]:
    """The printed national summary's registered voters, valid votes, null and void votes, and votes cast."""
    done = subprocess.run(
        ["pdftotext", "-layout", "-f", "113", "-l", "113", str(path), "-"], capture_output=True, text=True, check=True
    )
    marker = done.stdout.index("NATIONAL SUMMARY")
    found = re.search(r"^\s+(\d{7})\s+(\d{7})\s+(\d{6})\s+(\d{7})\s", done.stdout[marker:], re.MULTILINE)
    if found is None:
        raise ValueError("the 2004 national summary was not found")
    return int(found[1]), int(found[2]), int(found[3]), int(found[4])
