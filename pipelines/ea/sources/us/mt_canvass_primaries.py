import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.mt_canvass_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    date(2022, 6, 7): "2022-Primary-Report-State-Canvass.pdf",
    date(2024, 6, 4): "2024-Primary-State-Canvass.pdf",
}
URLS = {
    date(2022, 6, 7): "https://sosmt.gov/elections/results/2022-primary-state-canvass.pdf",
    date(2024, 6, 4): "https://sosmt.gov/elections/results/2024-primary-state-canvass.pdf",
}
INBOX = DATA / "inbox" / "manual" / "MF-286"
PUBLISHER = "Montana Secretary of State"
TITLE = "Statewide primary election canvass"
PARTIES = {
    "democrat": "democratic",
    "democratic": "democratic",
    "republican": "republican",
    "libertarian": "libertarian",
    "green": "green",
}
NUMBER = re.compile(r"\d[\d,]*")


def fetch() -> None:
    for day, name in FILES.items():
        raw.store(
            SOURCE,
            URLS[day],
            (INBOX / name).read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime(2026, 10, 29, tzinfo=UTC),
            media_type="application/pdf",
        )


def parse() -> None:
    with connect() as conn:
        counties = {
            name.upper().replace(" & ", " AND "): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '30%%'"""
            ).fetchall()
        }

    def county(line: str) -> tuple[str, re.Match[str]] | None:
        label = re.match(r"\s*([A-Za-z&' .]+?)\s+\d", line)
        if label is None:
            return None
        key = label[1].upper().replace(" & ", " AND ").strip()
        matched = [c for c in counties if c == key] or [c for c in counties if c.startswith(key)]
        return (counties[matched[0]], label) if len(matched) == 1 else None

    rows: list[tuple[date, str, int | None, str, str, int]] = []
    for day, name in FILES.items():
        lines = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, URLS[day]).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        i = 0
        while i < len(lines):
            head: list[str] = []
            while i < len(lines) and county(lines[i]) is None:
                head.append(lines[i])
                i += 1
            body: list[str] = []
            while i < len(lines) and not re.match(r"\s*Total\b", lines[i], re.IGNORECASE):
                body.append(lines[i])
                i += 1
            if i >= len(lines):
                break
            total = lines[i]
            i += 1
            party_line = next(
                (
                    n
                    for n in range(len(head) - 1, -1, -1)
                    if re.search(r"\b(Democrat|Democratic|Republican|Libertarian|Green)\s*$", head[n].strip())
                ),
                None,
            )
            text = (
                " ".join(line.strip() for line in head[max(0, party_line - 4) : party_line + 1])
                if party_line is not None
                else ""
            )
            if party_line is None or ("SENATOR" not in text.upper() and "REPRESENTAT" not in text.upper()):
                continue
            party = PARTIES[
                re.search(r"(Democratic|Democrat|Republican|Libertarian|Green)\s*$", head[party_line].strip())[
                    1
                ].lower()
            ]
            district = None
            if "SENATOR" not in text.upper():
                district = int("".join(re.search(r"\b(\d)(?:ST|ND)\b|DIST\s+(\d)", text).groups("")))
            spans = [(m.start(), m.end()) for m in NUMBER.finditer(total)]
            named = [line for line in head[party_line + 1 :] if line.strip()]
            first = [(m.start(), m.end(), m[0]) for m in re.finditer(r"\S+(?: \S+)*", named[0])]
            if len(first) != len(spans):
                raise ValueError(f"{name} {day}: {len(first)} name chunks for {len(spans)} columns: {named}")
            names = [[chunk[2]] for chunk in first]
            for line in named[1:]:
                for word in re.finditer(r"\S+(?: \S+)*", line):
                    middle = (word.start() + word.end()) / 2
                    names[min(range(len(first)), key=lambda c: abs((first[c][0] + first[c][1]) / 2 - middle))].append(
                        word[0]
                    )
            candidates = [" ".join(words).upper() for words in names]
            votes: list[dict[str, int]] = [{} for _ in spans]
            for line in body:
                found = county(line)
                if found is None:
                    continue
                numbers = [int(n.replace(",", "")) for n in NUMBER.findall(line[found[1].end() - 1 :])]
                if len(numbers) != len(spans):
                    raise ValueError(f"{name} {day}: {line.strip()} has {len(numbers)} numbers for {candidates}")
                for column, count in enumerate(numbers):
                    votes[column][found[0]] = count
            printed = [int(n.replace(",", "")) for n in NUMBER.findall(total)]
            for column, candidate in enumerate(candidates):
                if sum(votes[column].values()) != printed[column]:
                    raise ValueError(
                        f"{name} {day}: {candidate} counties sum {sum(votes[column].values())}, total {printed[column]}"
                    )
                rows.extend(
                    (day, f"{'senate' if district is None else 'house'}:{party}", district, candidate, code, count)
                    for code, count in votes[column].items()
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(
            rows,
            schema={
                "day": pl.Date,
                "chamber": pl.String,
                "district": pl.Int16,
                "candidate": pl.String,
                "code": pl.String,
                "votes": pl.Int64,
            },
            orient="row",
        )
        .with_columns(
            year=pl.col("day").dt.year().cast(pl.Int32),
            state=pl.lit("MT"),
            measure=pl.lit(1, dtype=pl.Int16),
            writein=pl.lit(False),
            kind=pl.lit("county"),
            source=pl.lit("MT"),
            special=pl.lit(False),
        )
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Montana's 2022 and 2024 House and Senate primaries by county",
            polity="US-MT",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sosmt.gov/elections/results/",
            license="Public record",
        )
        for url in URLS.values():
            cs.artifact(raw.cached(SOURCE, url), source)
        medsl.attach(cs, conn, pl.read_parquet(PARSED / "counties.parquet"), {"MT": source}, strict=True)
        return cs.write()
