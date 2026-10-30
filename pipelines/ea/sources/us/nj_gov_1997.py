from pathlib import Path

import polars as pl

from ea import raw
from ea.sources.us import usrep_county
from ea.store import DATA

SOURCE = "us.nj_gov_1997"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://www.nj.gov/state/elections/assets/pdf/election-results/1997/1997-general-election-results-governor.pdf"
PUBLISHER = "New Jersey Division of Elections"
TITLE = "Gubernatorial general election returns by county, November 4, 1997"
COUNTIES = {
    "Atlantic": "34001", "Bergen": "34003", "Burlington": "34005", "Camden": "34007", "Cape May": "34009",
    "Cumberland": "34011", "Essex": "34013", "Gloucester": "34015", "Hudson": "34017", "Hunterdon": "34019",
    "Mercer": "34021", "Middlesex": "34023", "Monmouth": "34025", "Morris": "34027", "Ocean": "34029",
    "Passaic": "34031", "Salem": "34033", "Somerset": "34035", "Sussex": "34037", "Union": "34039", "Warren": "34041",
}
COLUMNS = ["Whitman", "McGreevey", "Sabrin", "Pezzullo", "Hoffman", "Perrone", "Miller", "Pason", "Norton", "Ripa"]
PRINTED = [1133394, 1107968, 114172, 34906, 10703, 6805, 2816, 2800, 2540, 2240]
ROWS = {
    "Atlantic": [31364, 29091, 3651, 1451, 285, 112, 35, 36, 43, 178],
    "Bergen": [148934, 118834, 7337, 2592, 1031, 386, 99, 125, 221, 112],
    "Burlington": [55523, 60690, 7613, 2035, 340, 603, 393, 194, 138, 169],
    "Camden": [51643, 82028, 6194, 3119, 420, 784, 115, 74, 90, 137],
    "Cape May": [18227, 15395, 2098, 653, 114, 61, 40, 21, 88, 84],
    "Cumberland": [13651, 19977, 2272, 1163, 83, 70, 23, 58, 19, 41],
    "Essex": [69470, 120429, 3708, 896, 672, 392, 154, 795, 91, 70],
    "Gloucester": [30314, 41082, 4793, 1788, 271, 336, 55, 35, 86, 155],
    "Hudson": [47468, 80526, 2973, 588, 608, 361, 386, 252, 115, 111],
    "Hunterdon": [24465, 10983, 4336, 861, 243, 76, 21, 84, 42, 35],
    "Mercer": [44056, 54977, 6327, 1616, 487, 143, 111, 67, 63, 91],
    "Middlesex": [83149, 110354, 12203, 2660, 1859, 747, 87, 143, 95, 117],
    "Monmouth": [105535, 74098, 10724, 3500, 830, 105, 69, 106, 693, 162],
    "Morris": [97414, 41296, 6393, 2752, 602, 131, 50, 151, 109, 64],
    "Ocean": [84897, 57944, 10043, 3320, 798, 454, 88, 97, 193, 83],
    "Passaic": [55541, 60256, 3203, 1191, 428, 1276, 149, 281, 264, 174],
    "Salem": [10686, 8790, 1235, 290, 197, 110, 22, 17, 25, 54],
    "Somerset": [51465, 29089, 6809, 1348, 473, 243, 24, 169, 54, 34],
    "Sussex": [25458, 11331, 4116, 647, 281, 168, 17, 23, 41, 39],
    "Union": [68721, 69673, 5594, 1618, 482, 126, 864, 53, 44, 284],
    "Warren": [15413, 11125, 2550, 818, 199, 121, 14, 19, 26, 46],
}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def parse() -> None:
    for position, name in enumerate(COLUMNS):
        total = sum(row[position] for row in ROWS.values())
        if total != PRINTED[position]:
            raise ValueError(f"{name}: counties sum to {total}, printed {PRINTED[position]}")
    lines = [
        ("1997-11-04", "governor", COUNTIES[county], f"governor{position}", votes)
        for county, values in ROWS.items()
        for position, votes in enumerate(values)
    ]
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(lines, schema=["day", "district", "county", "column", "votes"], orient="row").write_parquet(
        PARSED / "lines.parquet"
    )


def emit() -> Path:
    return usrep_county.emit(
        SOURCE,
        VERSION,
        "New Jersey's 1997 governor general election by county from the Division of Elections' official returns",
        PUBLISHER,
        TITLE,
        URL,
        "NJ",
        "34",
        pl.read_parquet(PARSED / "lines.parquet"),
        [raw.cached(SOURCE, URL)],
        lambda key: "County totals were keyed from the Division of Elections' scanned two-page county returns; every column's 21 counties add up to the printed state total, and the eight candidates other than Whitman and McGreevey are pooled into Other votes.",
        statewide=True,
    )
