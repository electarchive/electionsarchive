import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.sources.us.sc_report import COUNTIES, LANDING, PUBLISHER, TITLE, URL
from ea.store import DATA, connect

SOURCE = "us.sc_statewide"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
BOOKS = {1992: "1992-1993", 1994: "1994-1995", 1996: "1995-1996", 1998: "1997-1998"}
DAYS = {1992: date(1992, 11, 3), 1994: date(1994, 11, 8), 1996: date(1996, 11, 5), 1998: date(1998, 11, 3)}
BLOCKS = [
    (1992, "president", 5128, 6),
    (1992, "senate", 5218, 5),
    (1996, "president", 2543, 7),
    (1996, "senate", 2707, 6),
    (1998, "governor", 1713, 5),
    (1998, "senate", 1998, 4),
]
STACKED: dict[tuple[int, str], dict[int, list[int]]] = {
    (1992, "president"): {21: [18, 25, 6870, 25, 1840, 7494], 22: [143, 99, 65066, 249, 13699, 34651]},
    (1996, "president"): {
        0: [3054, 8, 3493, 5, 310, 227, 3],
        1: [26539, 154, 14314, 53, 1152, 832, 30],
        2: [941, 12, 2222, 7, 66, 21, 4],
        3: [24137, 158, 17460, 79, 2285, 1611, 39],
        4: [1715, 13, 3380, 8, 143, 49, 4],
        5: [3808, 14, 3620, 19, 236, 74, 3],
        42: [12080, 120, 12198, 33, 641, 292, 28],
        43: [3855, 15, 5407, 21, 462, 287, 8],
        44: [3957, 19, 6987, 15, 215, 160, 4],
        45: [22222, 146, 16873, 72, 1891, 1282, 37],
    },
    (1996, "senate"): {
        20: [19556, 245, 16402, 224, 110, 0],
        21: [7685, 204, 9101, 181, 80, 0],
        22: [73170, 1058, 45767, 1337, 1599, 0],
    },
}
KEYED_TOTALS = {(1994, "governor"): [5875, 470756, 447002, 8003, 120, 557, 1537]}
KEYED = {
    (1994, "governor"): {
        "ABBEVILLE": [11, 2779, 3536, 22, 0, 0, 0],
        "AIKEN": [127, 20995, 11983, 278, 0, 0, 38],
        "ALLENDALE": [49, 774, 1766, 17, 0, 0, 11],
        "ANDERSON": [131, 20096, 17330, 282, 0, 0, 16],
        "BAMBERG": [63, 1436, 2620, 51, 0, 0, 6],
        "BARNWELL": [72, 3187, 2485, 49, 0, 0, 0],
        "BEAUFORT": [110, 12354, 12104, 277, 0, 0, 13],
        "BERKELEY": [386, 13726, 12885, 271, 0, 34, 65],
        "CALHOUN": [21, 2103, 2086, 13, 0, 0, 14],
        "CHARLESTON": [897, 35334, 36995, 895, 95, 323, 553],
        "CHEROKEE": [56, 5655, 5780, 81, 0, 0, 2],
        "CHESTER": [34, 3246, 3968, 104, 0, 0, 0],
        "CHESTERFIELD": [32, 5214, 4205, 48, 0, 0, 11],
        "CLARENDON": [16, 4136, 5008, 121, 0, 0, 0],
        "COLLETON": [113, 3712, 4724, 79, 0, 0, 0],
        "DARLINGTON": [72, 9620, 6296, 67, 0, 0, 7],
        "DILLON": [6, 3280, 3586, 89, 0, 0, 0],
        "DORCHESTER": [188, 10873, 9276, 193, 0, 0, 75],
        "EDGEFIELD": [80, 2933, 2582, 45, 0, 0, 2],
        "FAIRFIELD": [71, 2177, 4099, 70, 0, 0, 6],
        "FLORENCE": [96, 18310, 13212, 175, 0, 0, 0],
        "GEORGETOWN": [38, 5828, 6819, 81, 0, 0, 16],
        "GREENVILLE": [208, 49003, 41048, 538, 0, 0, 193],
        "GREENWOOD": [204, 7261, 6532, 123, 0, 0, 16],
        "HAMPTON": [86, 2077, 5039, 41, 0, 0, 36],
        "HORRY": [412, 21324, 19449, 260, 0, 0, 29],
        "JASPER": [63, 1278, 2600, 57, 0, 0, 5],
        "KERSHAW": [51, 8211, 6414, 164, 0, 40, 31],
        "LANCASTER": [66, 8494, 7049, 152, 0, 0, 27],
        "LAURENS": [25, 6680, 6860, 71, 0, 0, 23],
        "LEE": [12, 2468, 3319, 47, 0, 0, 2],
        "LEXINGTON": [164, 34219, 22856, 612, 25, 113, 67],
        "MCCORMICK": [12, 911, 1613, 20, 0, 0, 1],
        "MARION": [75, 3723, 3899, 52, 0, 0, 7],
        "MARLBORO": [20, 2319, 3419, 16, 0, 0, 9],
        "NEWBERRY": [105, 4882, 4445, 68, 0, 0, 8],
        "OCONEE": [64, 9511, 7064, 145, 0, 0, 9],
        "ORANGEBURG": [81, 9381, 14820, 115, 0, 0, 72],
        "PICKENS": [90, 13986, 8425, 214, 0, 0, 24],
        "RICHLAND": [572, 31459, 47806, 774, 0, 47, 32],
        "SALUDA": [27, 2617, 2294, 57, 0, 0, 0],
        "SPARTANBURG": [572, 26129, 24563, 459, 0, 0, 68],
        "SUMTER": [99, 10576, 10721, 150, 0, 0, 27],
        "UNION": [18, 3941, 4337, 63, 0, 0, 9],
        "WILLIAMSBURG": [21, 4058, 6375, 99, 0, 0, 3],
        "YORK": [159, 18480, 12710, 398, 0, 0, 4],
    },
}
TOTALS = {
    (1996, "president"): [573458, 4271, 506283, 2043, 37079, 27307, 1248],
    (1996, "senate"): [619859, 12988, 510951, 9740, 7693, 141],
}
METHOD = (
    "County totals are from the State Election Commission's Election Reports (read from the text layer, columns "
    "assigned by position). A cell the scan garbled is the printed state total less the other counties, and each "
    "candidate's county sum equals the state total printed in the report."
)


def page(year: int) -> list[str]:
    path = raw.cached(SOURCE.replace("statewide", "report"), URL.format(BOOKS[year])).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout.split("\n")


def tokens(line: str) -> list[tuple[int, int | None]]:
    line = re.sub(r"(\d) ,(\d)", r"\1,\2", line.replace("'", ""))
    return [
        (m.end(), int(m[0].lstrip(".").replace(",", "")) if re.fullmatch(r"\.?\d[\d,]*", m[0]) else None)
        for m in re.finditer(r"\S+", line)
        if re.search(r"\d", m[0])
    ]


def parse() -> None:
    order = sorted(re.sub(r"[^A-Z]", "", c.upper()) for c in COUNTIES)
    with connect() as conn:
        codes = {
            re.sub(r"[^A-Z]", "", name.upper()): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '45%%'"""
            ).fetchall()
        }
    rows = []
    for year, office, start, width in BLOCKS:
        lines = page(year)
        data: list[list[tuple[int, int | None]]] = []
        total: list[tuple[int, int | None]] = []
        begin = next(i for i in range(start - 12, start + 15) if "ABBEVILLE" in lines[i])
        for line in lines[begin : begin + 200]:
            if re.search(r"STATE TOTAL|TOTALS", line):
                total = tokens(line)[:width]
                break
            found = tokens(line)
            if len(found) > 1 or (found and (year, office) not in STACKED):
                data.append(found)
        if (year, office) in TOTALS:
            full = next(found for found in data if len(found) == width)
            total = [(end, TOTALS[year, office][index]) for index, (end, _) in enumerate(full)]
        edges = [end for end, _ in total]
        table: dict[str, list[int | None]] = {}
        fixed = STACKED.get((year, office), {})
        names = [county for index, county in enumerate(order) if index not in fixed]
        for county, found in zip(names, data, strict=True):
            cells: list[int | None] = [0] * width
            for end, value in found:
                column = min(range(width), key=lambda i: abs(edges[i] - end))
                cells[column] = value
            table[county] = cells
        for index, cells in STACKED.get((year, office), {}).items():
            table[order[index]] = list(cells)
        printed = [value or 0 for _, value in total]
        for column in range(width):
            missing = [county for county, cells in table.items() if cells[column] is None]
            if len(missing) == 1:
                table[missing[0]][column] = printed[column] - sum(c[column] or 0 for c in table.values())
        print(year, office, "rows", len(data), "counties", len(table))
        for column in range(width):
            got = sum(cells[column] or 0 for cells in table.values())
            if got != printed[column]:
                print("  column", column, "sum", got, "printed", printed[column])
        for county, cells in table.items():
            for column, value in enumerate(cells):
                if value:
                    rows.append((str(DAYS[year]), office, codes[county], str(column), value, year))
    for (year, office), keyed in KEYED.items():
        printed = KEYED_TOTALS[year, office]
        for column, want in enumerate(printed):
            got = sum(cells[column] for cells in keyed.values())
            if got != want:
                print(year, office, "column", column, "sum", got, "printed", want)
        for county, cells in keyed.items():
            for column, value in enumerate(cells):
                if value:
                    rows.append((str(DAYS[year]), office, codes[re.sub(r"[^A-Z]", "", county)], str(column), value, year))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, schema=["day", "district", "county", "column", "votes", "year"], orient="row").drop("year").write_parquet(
        PARSED / "lines.parquet"
    )


def method(key: tuple[str, ...]) -> str:
    if "1994" in str(key):
        return (
            "County totals are keyed from the page image of the State Election Commission's 1994 Election Report "
            "(the scan has no usable text layer), checked against the Algara county set for the Democratic and Republican "
            "candidates, and each candidate's county sum equals the state total printed in the report."
        )
    return METHOD


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "South Carolina's president, governor, and U.S. Senate general elections of 1992, 1994, 1996, and 1998 by county from the State Election Commission's reports",
        PUBLISHER,
        TITLE.format(year="Election Report"),
        LANDING,
        "SC",
        "45",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached("us.sc_report", URL.format(book)) for book in BOOKS.values()],
        method,
        official=True,
        statewide=True,
    )
