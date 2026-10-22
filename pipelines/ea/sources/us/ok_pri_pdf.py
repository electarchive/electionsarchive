import re
import subprocess
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ok_pri_pdf"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = "https://oklahoma.gov/content/dam/ok/en/elections/election-results/"
FILES = [
    (date(2000, 8, 22), "2000-election-results/2000-primary-election-results/00dempri.pdf"),
    (date(2000, 8, 22), "2000-election-results/2000-primary-election-results/00reppri.pdf"),
    (date(2000, 9, 12), "2000-election-results/2000-runoff-primary-election-results/00demrun.pdf"),
    (date(2000, 9, 12), "2000-election-results/2000-runoff-primary-election-results/00reprun.pdf"),
    (date(2002, 8, 27), "2002-election-results/2002-primary-election-results/02dempri.pdf"),
    (date(2002, 8, 27), "2002-election-results/2002-primary-election-results/02reppri.pdf"),
    (date(2002, 9, 17), "2002-election-results/2002-runoff-primary-election-results/02demrun.pdf"),
    (date(2002, 9, 17), "2002-election-results/2002-runoff-primary-election-results/02reprun.pdf"),
    (date(2010, 7, 27), "2010-election-results/2010-primary-results/primary-results-county-20100727.pdf"),
    (date(2010, 8, 24), "2010-election-results/2010-runoff-primary-results/runoff-primary-results-county-20100824.pdf"),
]
PARTIES = {"DEMOCRAT": "democratic", "DEMOCRATIC": "democratic", "REPUBLICAN": "republican", "LIBERTARIAN": "libertarian"}
PUBLISHER = "Oklahoma State Election Board"
TITLE = "Election results: primary results by county"
RACE = re.compile(r"(?:UNITED STATES|FOR U\.S\.) (SENATOR|REPRESENTATIVE)(?:, DISTRICT (?:NO\. )?(\d+))?\s*$")
ROW = re.compile(r"^([A-Za-z][A-Za-z' .:]*?)\s{2,}([\d,]+(?:\s+[\d,]+)*)\s*$")


def fetch() -> None:
    for _, path in FILES:
        try:
            raw.cached(SOURCE, BASE + path)
        except LookupError:
            body = subprocess.run(
                ["curl", "-sfkL", "-A", raw.USER_AGENT, BASE + path], capture_output=True, check=True
            ).stdout
            raw.store(
                SOURCE,
                BASE + path,
                body,
                suffix=".pdf",
                retrieved_at=datetime.now(UTC),
                media_type="application/pdf",
            )


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '40%%'"""
            ).fetchall()
        }
    rows = []
    for day, path in FILES:
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, BASE + path).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        lines = text.splitlines()
        blocks: dict[tuple[str, str | None, str], list[dict]] = {}
        current = None
        office = district = party = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            title = stripped.removeprefix("Race:").strip() if stripped.startswith("Race:") else None
            if title is None and i + 1 < len(lines) and "Primary Election" in lines[i + 1] and stripped:
                title = stripped
            if title is not None:
                found = RACE.match(title)
                office, district, party, current = (found[1], found[2], None, None) if found else (None,) * 4
                if found and not stripped.startswith("Race:"):
                    party = PARTIES.get(lines[i + 1].split()[0].upper())
                continue
            if stripped.startswith("Party:") and office is not None:
                party = PARTIES.get(stripped.removeprefix("Party:").strip().upper())
                continue
            if re.fullmatch(r"=+(?: +=+)+", stripped) and office is not None and party is not None:
                segments = [(m.start(), m.end()) for m in re.finditer(r"=+", line)]
                headed = lines[i - 1].lstrip().startswith("County")
                names = [""] * len(segments)
                if headed:
                    names = [lines[i - 1][start : end + 1].strip() for start, end in segments]
                    totaled = "otal" in lines[i - 2] or names[-1].lower().endswith("votes")
                else:
                    for back in (1, 2):
                        for chunk in re.finditer(r"\S+(?: \S+)*", lines[i - back]):
                            centre = (chunk.start() + chunk.end()) / 2
                            k = min(range(len(segments)), key=lambda s: abs(centre - sum(segments[s]) / 2))
                            names[k] = f"{chunk.group()} {names[k]}".strip() if back == 2 else f"{names[k]} {chunk.group()}".strip()
                    totaled = True
                names = names[1:] if headed else names
                if totaled:
                    names = names[:-1]
                key = (office, district, party)
                options = blocks.setdefault(key, [])
                current = next((blk for blk in options if blk["names"] == names), None)
                if current is None:
                    current = {"segments": segments, "headed": headed, "names": names, "totaled": totaled, "counties": {}, "total": None}
                    options.append(current)
                continue
            if current is None:
                continue
            found = ROW.match(line)
            if found is None:
                continue
            cells = [int(n.replace(",", "")) for n in found[2].split()]
            width = len(current["names"]) + current["totaled"]
            if len(cells) < width and not current["headed"]:
                cells = [0] * width
                for token in re.finditer(r"[\d,]+", line[found.start(2) :]):
                    ends = [end for _, end in current["segments"]]
                    at = found.start(2) + token.end()
                    cells[min(range(width), key=lambda k: abs(ends[k] - at))] = int(token.group().replace(",", ""))
            if len(cells) != width:
                continue
            label = found[1].strip()
            if label.startswith("STATE TOTAL"):
                current["total"] = cells
            else:
                current["counties"][label] = cells
        for (office, district, party), options in blocks.items():
            labels = set.intersection(*(set(blk["counties"]) for blk in options))
            if not labels or any(set(blk["counties"]) != labels for blk in options):
                print(day, office, district, party, "county lists differ", [len(blk["counties"]) for blk in options])
                continue
            names = [name for blk in options for name in blk["names"]]
            table = {label: [n for blk in options for n in blk["counties"][label][: len(blk["names"])]] for label in labels}
            sums = [sum(v[k] for v in table.values()) for k in range(len(names))]
            bad = False
            for label, cells in table.items():
                for blk in options:
                    if blk["totaled"] and sum(cells) != blk["counties"][label][-1]:
                        print(day, office, district, party, label, "row", sum(cells), blk["counties"][label][-1])
                        bad = True
            offset = 0
            for blk in options:
                if blk["total"] is not None and blk["total"][: len(blk["names"])] != sums[offset : offset + len(blk["names"])]:
                    print(day, office, district, party, "sum", sums, "printed", blk["total"])
                    bad = True
                offset += len(blk["names"])
            if bad:
                continue
            lost = [c for c in table if re.sub(r"[^a-z]", "", c.lower()) not in counties]
            if lost:
                raise ValueError(f"unmatched counties: {lost}")
            for label, cells in table.items():
                for name, votes in zip(names, cells, strict=False):
                    rows.append(
                        (
                            day.year,
                            f"{'senate' if office == 'SENATOR' else 'house'}:{party}",
                            day,
                            False,
                            "OK",
                            int(district) if district else None,
                            counties[re.sub(r"[^a-z]", "", label.lower())],
                            name.upper(),
                            votes,
                        )
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "special": pl.Boolean,
            "state": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    ).with_columns(
        measure=pl.lit(1, pl.Int16), writein=pl.lit(False), kind=pl.lit("county"), source=pl.lit("OK")
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Oklahoma's 2000, 2002, and 2010 U.S. Senate and House primaries and runoffs by county from the State Election Board's results PDFs",
            polity="US-OK",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://oklahoma.gov/elections/elections-results/election-results.html",
            license="Public record",
        )
        for _, path in FILES:
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"OK": source}, strict=True)
        return cs.write()
