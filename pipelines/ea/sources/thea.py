from datetime import UTC, datetime
from pathlib import Path

import fastexcel
import polars as pl

from ea import raw
from ea.store import DATA

SOURCE = "thea"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
UPLOADS = "https://electiondataarchive.org/wp-content/uploads"
DOWNLOADS = Path.home() / "Downloads"
MEDIA = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
    ".pdf": "application/pdf",
}
FILES = [
    "2025/10/clea_lc_20251015-A-K.xlsx",
    "2025/10/clea_lc_20251015-L-Z.xlsx",
    "2026/01/clea_20251015_codebook_accessible.pdf",
    "2026/01/clea_20251015_appendix_I_accessible.pdf",
    "2025/10/clea_20251015_appendix_II.xlsx",
    "2025/10/clea_20251015_candidate_names.xlsx",
    "2026/09/clea_uc_20260908_excel.zip",
    "2026/09/clea_20260908_codebook.pdf",
    "2026/09/clea_20260908_appendix_I.pdf",
    "2026/09/clea_20260908_appendix_II.xlsx",
    "2023/11/clea_EU_20231115_excel.zip",
    "2026/01/clea_EU_20231115_codebook-accessible.pdf",
    "2025/09/clea_EU_20231115_appendix-I.xlsx",
    "2025/09/clea_EU_20231115_appendix-II.xlsx",
]


LOWER = ["2025/10/clea_lc_20251015-A-K.xlsx", "2025/10/clea_lc_20251015-L-Z.xlsx"]
CODES = ["release", "id", "ctr", "yr", "mn", "cst", "mag", "pty"]
NUMBERS = [
    "pev1",
    "vot1",
    "vv1",
    "ivv1",
    "to1",
    "cv1",
    "cvs1",
    "pv1",
    "pvs1",
    "pev2",
    "vot2",
    "vv2",
    "ivv2",
    "to2",
    "cv2",
    "cvs2",
    "pv2",
    "pvs2",
    "seat",
]
TEXT = ["rg", "ctr_n", "sub", "cst_n", "pty_n", "can"]
PUBLISHER = "THEA, The Elections Archive"
TITLE = "Constituency-Level Elections Archive, lower chamber, Release 18 (2025-10-15)"
LANDING = "https://electiondataarchive.org/data-and-documentation/clea-lower-chamber-elections-archive/"
LICENSE = "Open Database License (ODbL) 1.0, contents under the Database Contents License (DbCL) 1.0"
LICENSE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
ATTRIBUTION = "Contains information from THEA, which is made available here under the Open Database License (ODbL)."
NOTES = (
    "THEA (University of Michigan) compiles constituency results from official and secondary sources and names no "
    "source per row; its Appendix I names each country's sources. -990 marks a missing value, -992 an uncontested "
    "seat, and -994 a suspended election."
)


def fetch() -> None:
    for file in FILES:
        url = f"{UPLOADS}/{file}"
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = DOWNLOADS / Path(file).name
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=path.suffix,
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type=MEDIA[path.suffix],
            )


def parse() -> None:
    frame = pl.concat(
        [
            fastexcel.read_excel(raw.cached(SOURCE, f"{UPLOADS}/{file}").path).load_sheet(0).to_polars()
            for file in LOWER
        ],
        how="diagonal_relaxed",
    )
    if sorted(frame.columns) != sorted([*CODES, *NUMBERS, *TEXT]):
        raise ValueError(f"unexpected columns: {frame.columns}")
    fractional = [column for column in CODES if (frame[column] != frame[column].round()).any()]
    if fractional:
        raise ValueError(f"fractional codes in {fractional}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        *(pl.col(column).cast(pl.String) for column in TEXT),
        *(pl.col(column).cast(pl.Int32) for column in CODES),
        *(pl.col(column).cast(pl.Float64) for column in NUMBERS),
    ).write_parquet(PARSED / "lower.parquet")
    print(f"{frame.height} rows in {frame['id'].n_unique()} elections")
