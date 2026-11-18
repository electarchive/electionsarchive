"""The annexures of the Report of the 2023 General Election as rows.

Annexures 3 to 5 (governors, senatorial districts, federal constituencies) print one line per constituency with the
winning party; annexure 6 (state constituencies) adds the elected member's name and gender. The text layer is read
with `pdftotext -layout`, one printed line at a time. The presidential annexure (2) is a picture with no text layer,
so its numbers are transcribed in `ea.sources.ng.inec` with their boxes on the page.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

GOVERNORS = (458, 458)
SENATE = (459, 462)
HOUSE = (463, 479)
ASSEMBLY = (480, 523)

GOVERNOR = re.compile(
    r"^\s*(\d+)\s+([A-Z][A-Za-z ]+?)\s+Governor\s+([A-Z]{2})\s+[\d,]+\s+[\d,]+\s+[\d,]+\s+([A-Z]+)\s*$"
)
DISTRICT = re.compile(
    r"^(?P<lead>.*?)\s*(?P<number>\d{1,3})\s+(?P<name>\S.*?)\s+(?P<code>(?:SD|FC)/\d{3}/[A-Z]{2,3})"
    r"(?:\s+[\d,]+){1,3}(?:\s+(?P<party>[A-Z]+))?\s*$"
)
SPILL = re.compile(r"^\s+[\d,]+\s+(?P<party>[A-Z]+)\s*$")
MEMBER = re.compile(r"^(?P<lead>.*?)(?P<number>\d{1,4})\s+(?P<name>\S.*?)\s{2,}(?P<code>\S*SC\S*)\s+(?P<rest>\S.*)$")
REMARKS = {"Elected", "Electec", "Elelcted", "Court"}
SC_CODE = re.compile(r"SC/?(\d+)/?([A-Z]{2,3})")


@dataclass(frozen=True)
class Seat:
    annex: int
    number: int
    code: str
    state: str
    name: str
    party: str
    member: str = ""
    gender: str = ""
    remark: str = ""


def lines(path: Path, pages: tuple[int, int]) -> list[str]:
    out = subprocess.run(
        ["pdftotext", "-f", str(pages[0]), "-l", str(pages[1]), "-layout", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.rstrip() for line in out.splitlines() if line.strip() and "REPORT OF THE" not in line]


def governors(path: Path) -> list[Seat]:
    rows: list[Seat] = []
    for line in lines(path, GOVERNORS):
        found = GOVERNOR.match(line)
        if found:
            rows.append(Seat(3, int(found[1]), found[3], found[3], found[2], found[4]))
    return rows


def districts(path: Path, annex: int) -> list[Seat]:
    rows: list[Seat] = []
    printed = lines(path, SENATE if annex == 4 else HOUSE)
    for i, line in enumerate(printed):
        found = DISTRICT.match(line)
        if found is None:
            continue
        code = found["code"]
        party = found["party"]
        spill = SPILL.match(printed[i + 1]) if i + 1 < len(printed) else None
        if party is None and spill is not None:
            party = spill["party"]
        if party is None:
            raise ValueError(f"no party for {code}")
        rows.append(Seat(annex, int(found["number"]), code, code.split("/")[2], found["name"], party))
    return rows


def members(path: Path) -> list[Seat]:
    """Annexure 6, with continuation lines of a wrapped constituency name joined to the row above."""
    rows: list[Seat] = []
    for line in lines(path, ASSEMBLY):
        found = MEMBER.match(line)
        if found is None or not found["lead"].strip(" 0123456789").replace(" ", "").isalpha() and found["lead"].strip():
            text = line.strip()
            if rows and re.fullmatch(r"[A-Za-z() ]+", text) and len(line) - len(line.lstrip()) < 30:
                last = rows[-1]
                rows[-1] = Seat(
                    6,
                    last.number,
                    last.code,
                    last.state,
                    f"{last.name} {text}",
                    last.party,
                    last.member,
                    last.gender,
                    last.remark,
                )
            continue
        number, name = int(found["number"]), found["name"].strip()
        shifted = re.match(r"(\d+)\s{2,}(\S.*)$", name)
        if shifted:  # the state's seat count stands in the first column of its second row
            number, name = int(shifted[1]), shifted[2]
        code = SC_CODE.search(found["code"])
        if code is None:
            raise ValueError(f"annexure 6 line without a constituency code: {line!r}")
        rows.append(
            Seat(
                6,
                number,
                f"SC/{int(code[1]):02d}/{code[2]}" if int(code[1]) < 100 else f"SC/{int(code[1])}/{code[2]}",
                code[2],
                name,
                *_tail(found["rest"]),
            )
        )
    return rows


def _tail(text: str) -> tuple[str, str, str, str]:
    """Split what follows the code into party, member, gender, and remark, reading from the right."""
    words = text.split()
    remark = ""
    while words and words[-1] in REMARKS | {"Order"}:
        remark = f"{words.pop()} {remark}".strip()
    gender = words.pop() if words and words[-1] in ("M", "F") else ""
    party = words.pop() if words and re.fullmatch(r"[A-Z]{2,5}", words[-1]) else ""
    return party, " ".join(words), gender, remark
