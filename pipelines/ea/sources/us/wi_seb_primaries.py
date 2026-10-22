import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.wi_seb_primaries"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "http://web.archive.org/web/2010id_/http://elections.state.wi.us/docview.asp?docid={}&locid=47"
LANDING = "http://elections.state.wi.us/section.asp?linkid=155&locid=47"
PUBLISHER = "Wisconsin State Elections Board"
TITLE = "Canvass - County Totals, {} primary"
PARTIES = ("democratic", "republican", "libertarian", "constitution", "green")
DOCS = {
    date(2000, 9, 12): dict(zip((2239, 2249, 2247, 2238, 2266), PARTIES, strict=True)),
    date(2004, 9, 14): dict(zip((1418, 1421, 1420, 1417, 1422), PARTIES, strict=True)),
    date(2006, 9, 12): dict(zip((9549, 9579, 9580, 9582, 9581), PARTIES, strict=True))
    | {9561: "democratic", 9586: "republican"},
}
OFFICE = re.compile(r"(US Senate|US Congress, District No\. (\d+))\b")
ROW = re.compile(r"^\s*(\S.*?)\s*:?\s{2,}((?:[\d,]+\s+)*[\d,]+)\s*$")


def fetch() -> None:
    for docs in DOCS.values():
        for doc in docs:
            raw.fetch(SOURCE, URL.format(doc), suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '55%%'"""
            ).fetchall()
        }
    rows = []
    for day, docs in DOCS.items():
        for doc, party in docs.items():
            text = subprocess.run(
                ["pdftotext", "-layout", str(raw.cached(SOURCE, URL.format(doc)).path), "-"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout
            office = None
            names: list[str] = []
            parts: dict[str, list[int]] = {}
            for page in text.split("\f"):
                lines = page.splitlines()
                start = next((i for i, line in enumerate(lines) if "Primary Election" in line), None)
                title = next((line for line in lines[start + 1 :] if line.strip()), "") if start is not None else ""
                found = OFFICE.match(title)
                if found is None:
                    office = None
                    continue
                if found[0] != office:
                    office, names, parts = found[0], [], {}
                    body = lines[lines.index(title) + 1 :]
                    stop = next(i for i, line in enumerate(body) if ROW.match(line))
                    tokens = [
                        (match.start(), match[0])
                        for line in body[:stop]
                        if not re.fullmatch(r"(?:\s*[A-Z]{3})*\s*", line)
                        for match in re.finditer(r"\S+", line)
                        if match[0] not in ("Total", "Votes", "Cast")
                    ]
                    if 2 * sum(len(token) == 1 for _, token in tokens) > len(tokens):
                        letters: dict[int, str] = {}
                        for x, token in tokens:
                            letters[x] = letters.get(x, "") + token
                        tokens = sorted(letters.items())
                    ends: list[int] = []
                    for line in body[stop:]:
                        if row := ROW.match(line):
                            for i, cell in enumerate(re.finditer(r"[\d,]+", line[row.start(2) :])):
                                end = row.start(2) + cell.end()
                                if i == len(ends):
                                    ends.append(end)
                                ends[i] = max(ends[i], end)
                    words: dict[int, list[str]] = {}
                    for x, token in tokens:
                        column = next((i for i in range(1, len(ends)) if ends[i] >= x), len(ends) - 1)
                        words.setdefault(column, []).append(token)
                    names = [" ".join(words.get(i, [])) for i in range(1, len(ends))]
                for line in lines:
                    row = ROW.match(line)
                    if row is None:
                        continue
                    cells = [int(cell.replace(",", "")) for cell in row[2].split()]
                    if row[1].startswith("Office Totals"):
                        sums = [sum(values[i] for values in parts.values()) for i in range(len(names) + 1)]
                        if len(cells) != len(names) + 1 or sums != cells:
                            print(f"{day} {party} {office}: {names} counties sum to {sums}, printed {cells}, skipped")
                        else:
                            rows.extend(
                                (
                                    day,
                                    f"{'senate' if found[2] is None else 'house'}:{party}",
                                    None if found[2] is None else int(found[2]),
                                    code,
                                    name.upper(),
                                    values[i + 1],
                                    name.upper().startswith("SCATTER"),
                                )
                                for code, values in parts.items()
                                for i, name in enumerate(names)
                            )
                        office = None
                    elif len(cells) == len(names) + 1:
                        parts[counties[re.sub(r"[^a-z]", "", row[1].lower().removesuffix("county"))]] = cells
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.Int16,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
            "writein": pl.Boolean,
        },
        orient="row",
    ).with_columns(
        year=pl.col("day").dt.year().cast(pl.Int32),
        special=pl.lit(False),
        state=pl.lit("WI"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("WI"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wisconsin's U.S. House and Senate primaries by county, 2000-2006 (State Elections Board canvass)",
            polity="US-WI",
        )
        for day, docs in DOCS.items():
            title = TITLE.format(day.year)
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=LANDING,
                    license="Public record",
                )
            for doc in docs:
                cs.artifact(raw.cached(SOURCE, URL.format(doc)), source)
            medsl.attach(cs, conn, frame.filter(pl.col("day") == day), {"WI": source}, strict=True)
        return cs.write()
