"""Reads the tables of the electoral organ's results bulletins, which are laid out as plain text lines of counts.

A row is a name followed by seven counts (Sí, No, valid, blank, null, ballots cast, registered voters) and, in most
tables, the abstention. The name wraps over two lines in some tables, and a few rows are scrambled by a page break; the
module raises on any row that fails to add up, and the curated rows below carry their proof.
"""

import re
import subprocess
from pathlib import Path

NUM = r"\d{1,3}(?:\.\d{3})*|\d+"
COUNTS = ("yes", "no", "valid", "blank", "null", "cast", "registered")
ROW = re.compile(rf"^\s*(?P<name>[A-Za-zÁÉÍÓÚÑáéíóúñ][^\d]*?)\s{{2,}}(?P<n>(?:(?:{NUM})\s+){{6,7}}(?:{NUM}))\s*$")
PERCENT = r"\d{1,3},\d{2}\s?%"
ETA = re.compile(
    rf"^\s*(?:(?P<dept>[A-Za-zÁÉÍÓÚáéíóú ]+?)\s{{2,}})?(?P<name>[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúÑñ .]+?)"
    rf"\s+(?P<mesas>{NUM})\s+{PERCENT}\s+(?P<reg>{NUM})\s+(?P<si>{NUM})\s+{PERCENT}\s+(?P<no>{NUM})\s+{PERCENT}"
    rf"\s+(?P<valid>{NUM})\s+(?P<blank>{NUM})\s+(?P<null>{NUM})\s+(?P<cast>{NUM})\s*$"
)


def lines(pdf: Path) -> list[str]:
    out = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout
    return out.split("\n")


def integer(text: str) -> int:
    return int(text.replace(".", ""))


def counts(numbers: str) -> dict[str, int]:
    values = [integer(n) for n in numbers.split()]
    return dict(zip(COUNTS, values[:7], strict=True))


def after(text: list[str], marker: str, start: int = 0) -> int:
    """The index of the first line from start on that holds the marker."""
    for i in range(start, len(text)):
        if re.search(marker, text[i]):
            return i
    raise LookupError(marker)


def rows(text: list[str], start: int, stop: str = r"Fuente") -> dict[str, dict[str, int]]:
    """The named rows of the table that begins after a line, up to the line that closes it."""
    found: dict[str, dict[str, int]] = {}
    for line in text[start:]:
        if re.search(stop, line) and found:
            break
        match = ROW.match(line)
        if match:
            name = re.sub(r"\s+", " ", match.group("name")).strip()
            if name in found:
                raise ValueError(f"{name} appears twice")
            found[name] = counts(match.group("n"))
    return found


def eta_rows(text: list[str]) -> list[tuple[str, dict[str, int]]]:
    """The one row of each autonomous territorial entity in the summary table, and the table's total row."""
    found = []
    for line in text:
        match = ETA.match(line)
        if match:
            name = re.sub(r"\s+", " ", match["name"]).strip()
            found.append(
                (
                    name,
                    {
                        "yes": integer(match["si"]),
                        "no": integer(match["no"]),
                        "valid": integer(match["valid"]),
                        "blank": integer(match["blank"]),
                        "null": integer(match["null"]),
                        "cast": integer(match["cast"]),
                        "registered": integer(match["reg"]),
                    },
                )
            )
    return found


TOTAL = re.compile(
    rf"^\s*Total\s+(?:{NUM})\s+{PERCENT}\s+(?P<reg>{NUM})\s+(?P<si>{NUM})\s+(?P<no>{NUM})\s+(?P<valid>{NUM})"
    rf"\s+(?P<blank>{NUM})\s+(?P<null>{NUM})\s+(?P<cast>{NUM})\s*$",
    re.MULTILINE,
)
TOTAL_ROW = re.compile(rf"^\s*Total\s+(?P<n>(?:(?:{NUM})\s+){{6,7}}(?:{NUM}))\s*$")


def eta_total(text: list[str]) -> dict[str, int]:
    """The total row of a summary table of autonomous territorial entities whose percentages are not printed."""
    match = TOTAL.search("\n".join(text))
    if match is None:
        raise LookupError("no total row")
    return {
        "yes": integer(match["si"]),
        "no": integer(match["no"]),
        "valid": integer(match["valid"]),
        "blank": integer(match["blank"]),
        "null": integer(match["null"]),
        "cast": integer(match["cast"]),
        "registered": integer(match["reg"]),
    }


def locality_total(text: list[str], entity: str) -> dict[str, int]:
    """The total of a municipality's table by locality, the first total row after its heading."""
    start = after(text, rf"MUNICIPIO DE {entity.upper()}: .*SEGÚN(?:\s|$)|MUNICIPIO DE {entity.upper()}: .*LOCALIDAD")
    for line in text[start : start + 25]:
        match = TOTAL_ROW.match(line)
        if match:
            return counts(match["n"])
    raise LookupError(entity)
