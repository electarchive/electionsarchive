import html
import re
import time
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.store import DATA

SOURCE = "gb.scottish_parliament"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = {
    year: f"https://www.parliament.scot/msps/elections/{year}-election-results"
    for year in (1999, 2003, 2007, 2011, 2016, 2021, 2026)
}
EC_2011 = "https://web.archive.org/web/20251202132040if_/https://www.electoralcommission.org.uk/sites/default/files/excel_doc/SP-2011-results-flat-file-WEB.xlsx"
EC_2016 = "https://web.archive.org/web/20250912224401if_/https://www.electoralcommission.org.uk/sites/default/files/2021-04/Electoral-Data-Results-May-2016-Scottish-Parliament-elections_2.xls"
EC_2007 = "https://web.archive.org/web/2015if_/http://www.electoralcommission.org.uk/__data/assets/excel_doc/0006/55590/Scotland-Parl-Elec-2007-Turnout-Electorate-Constituency.xls"
EMB_2021 = "https://www.emb.scot/downloads/file/815/spe21-full-votes-and-seats-by-party-candidate"


def fetch() -> None:
    for year, url in PAGES.items():
        raw.fetch(SOURCE, url, suffix=".html")
        time.sleep(1)
    raw.fetch(SOURCE, EC_2011, suffix=".xlsx")
    time.sleep(1)
    raw.fetch(SOURCE, EC_2016, suffix=".xls")
    time.sleep(1)
    raw.fetch(SOURCE, EC_2007, suffix=".xls")
    raw.fetch(SOURCE, EMB_2021, suffix=".xlsx")


def page_lines(year: int) -> list[str]:
    text = raw.cached(SOURCE, PAGES[year]).path.read_text()
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", text, flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def parse_pages() -> tuple[pl.DataFrame, pl.DataFrame]:
    candidates = []
    members = []
    for year in PAGES:
        lines = page_lines(year)
        start = lines.index("Aberdeen Central")
        split = next(index for index, line in enumerate(lines) if line == "Region results")
        index = start
        while index < split:
            if len(lines[index]) == 1:
                index += 1
                continue
            constituency = lines[index]
            index += 1
            block = []
            while lines[index] != "MSP elected":
                block.append(lines[index])
                index += 1
            member = lines[index + 1]
            majority = int(re.sub(r"\D", "", lines[index + 2]))
            index += 3
            position = 0
            while position < len(block):
                name = block[position]
                detail = (
                    block[position + 1] if position + 1 < len(block) and block[position + 1].startswith("- ") else None
                )
                position += 2 if detail is not None else 1
                match = re.fullmatch(r"- (?:(.+?) - )?([\d,]+) votes?", detail) if detail is not None else None
                if match is not None:
                    party, votes = match.group(1), int(match.group(2).replace(",", ""))
                elif detail is not None and not re.search(r"\d", detail):
                    party, votes = detail[2:], None
                else:
                    party, votes = None, None
                candidates.append(
                    {
                        "year": year,
                        "constituency": constituency,
                        "candidate": name,
                        "party": party,
                        "votes": votes,
                        "elected": name == member,
                        "majority": majority if name == member else None,
                    }
                )
        region = None
        index = split + 2
        while index < len(lines) and not lines[index].startswith("Copyright"):
            line = lines[index]
            if line == "MSPs elected":
                region = lines[index - 1]
                index += 1
                continue
            if (
                index + 1 < len(lines)
                and (lines[index + 1] == "-" or lines[index + 1].startswith("- "))
                and region is not None
            ):
                members.append({"year": year, "region": region, "member": line, "party": lines[index + 1][2:] or None})
                index += 2
                continue
            if index + 1 < len(lines) and lines[index + 1] == "MSPs elected":
                index += 1
                continue
            index += 1
    return pl.DataFrame(candidates), pl.DataFrame(members)


def sheet(path: Path, name: str) -> list[list[str | None]]:
    frame = fastexcel.read_excel(str(path)).load_sheet_by_name(name, header_row=None).to_polars()
    return [[None if cell is None or cell == "" else str(cell).strip() for cell in row] for row in frame.iter_rows()]


def number(cell: str | None) -> int | None:
    return None if cell is None else int(round(float(cell)))


def parse_workbooks() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    units = []
    lists = []
    path = raw.cached(SOURCE, EC_2011).path
    rows = sheet(path, "Constituency results")
    for row in rows[1:]:
        if row[0] is None or row[0] == "Total":
            continue
        units.append(
            {
                "year": 2011,
                "ballot": "constituency",
                "constituency": row[0],
                "region": None,
                "electorate": number(row[1]),
                "rejected": number(row[2]),
                "valid": number(row[3]),
            }
        )
    rows = sheet(path, "List results")
    header = rows[0]
    for row in rows[1:]:
        if row[1] is None or row[1] == "Total":
            continue
        units.append(
            {
                "year": 2011,
                "ballot": "list",
                "constituency": row[1],
                "region": row[0],
                "electorate": number(row[2]),
                "rejected": number(row[3]),
                "valid": number(row[4]),
            }
        )
        for label, cell in zip(header[6:], row[6:], strict=True):
            lists.append(
                {"year": 2011, "region": row[0], "constituency": row[1], "party": label, "votes": number(cell)}
            )
    path = raw.cached(SOURCE, EC_2016).path
    for ballot, name, width in (
        ("constituency", "Constituencies - Results", 9),
        ("list", "Regional List - Results", 9),
    ):
        rows = sheet(path, name)
        header = rows[1]
        parties = header[9 : header.index("Others", 9) + 1]
        for row in rows[2:]:
            if row[1] is None or row[0] is None:
                continue
            units.append(
                {
                    "year": 2016,
                    "ballot": ballot,
                    "constituency": row[1],
                    "region": row[2],
                    "electorate": number(row[3]),
                    "rejected": number(row[6]),
                    "valid": number(row[5]),
                }
            )
            if ballot == "list":
                for label, cell in zip(parties, row[9 : 9 + len(parties)], strict=True):
                    lists.append(
                        {"year": 2016, "region": row[2], "constituency": row[1], "party": label, "votes": number(cell)}
                    )
    return units, lists


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    candidates, members = parse_pages()
    candidates.write_parquet(PARSED / "candidates.parquet")
    members.write_parquet(PARSED / "members.parquet")
    units, lists = parse_workbooks()
    pl.DataFrame(units).write_parquet(PARSED / "units.parquet")
    pl.DataFrame(lists).write_parquet(PARSED / "list_votes.parquet")
    for year in sorted(set(candidates["year"])):
        winners = candidates.filter((pl.col("year") == year) & pl.col("elected"))
        print(
            year,
            "constituencies",
            candidates.filter(pl.col("year") == year)["constituency"].n_unique(),
            "winners",
            len(winners),
            "list members",
            len(members.filter(pl.col("year") == year)),
        )
