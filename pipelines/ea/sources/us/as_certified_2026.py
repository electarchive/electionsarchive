"""American Samoa's November 3, 2026 election as the Election Office certified it.

The Office's November 10 release gives the Delegate's territorial summary (page 2) and the House of Representatives
results for the 17 districts (pages 3 and 4) as scanned images, so every figure was read from the page and is stored
with its page and box. The contests, options, candidacies, and polling-district units already exist; the package
attaches a certified result set to each and supersedes the election-night sheets.
"""

import csv
import difflib
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
from PIL import Image

from ea import package, raw
from ea.package import Package

SOURCE = "us.as_certified_2026"
KEY = f"{SOURCE}/2026-11-03"
PUBLISHER = "American Samoa Election Office"
URL = (
    "https://dehayf5mhw1h7.cloudfront.net/wp-content/uploads/sites/594/2026/11/12141031/"
    "2026-midterm-election-official-results-media-release-1.pdf"
)
TITLE = "Official results of the Midterm Election 2026 (Delegate and House of Representatives)"
COUNTRY = "01a13a9c-790b-772e-9f3f-d078d588c99b"
STATE = "01a13a9c-7915-7638-aa6d-42bcb74d3d44"
# The release is dated November 10, 2026, 4:30 PM in American Samoa (UTC-11).
AS_OF = datetime(2026, 11, 11, 3, 30, tzinfo=UTC)
REFS = Path(__file__).with_name("as_certified_2026.tsv")
PAGES = raw.DATA / "pages" / SOURCE
VOTES = 1

DELEGATE = "2026-11-03:AS:house:0"
HOUSE = "2026-11-03:AS:60:house:district-"
DISTRICTS = {
    1: "1-manua",
    2: "2-manua",
    3: "3-vaifanua",
    4: "4-saole",
    5: "5-sua-1",
    6: "6-sua-2",
    7: "7-maoputasi-1",
    8: "8-maoputasi-2",
    9: "9-maoputasi-3",
    10: "10-maoputasi-4",
    11: "11-maoputasi-5",
    12: "12-ituau",
    13: "13-fofo",
    14: "14-alataua",
    15: "15-tualauta",
    16: "16-tualatai",
    17: "17-aitulagi-leasina",
}

# Page 2, the Delegate's territorial summary: one row per polling booth. Columns: Amata, Lancaster, Chapman, Young,
# Lefaoseu. Cell edges are fractions of the page's width, and rows come from the table's ruled lines.
DELEGATE_NAMES = ("Aumua Amata", "Fualaau Rosie Tago Lancaster", "Meleagi Suitonu Chapman", "Sandra King Young")
DELEGATE_NAMES += ("Tuifagaloa Dr. Afu Lefaoseu",)
DELEGATE_ROWS: dict[str, tuple[int, int, int, int, int]] = {
    "central": (0, 1, 1, 1, 0),
    "d01": (285, 4, 38, 159, 12),
    "d02": (149, 5, 5, 105, 7),
    "d03": (299, 23, 5, 81, 15),
    "d04": (203, 2, 2, 68, 13),
    "d05": (261, 7, 3, 94, 11),
    "d06": (158, 0, 4, 70, 13),
    "d07": (142, 5, 4, 58, 1),
    "d08": (180, 3, 3, 132, 15),
    "d09": (270, 2, 7, 105, 17),
    "d10": (155, 1, 1, 61, 5),
    "d11": (183, 6, 5, 80, 13),
    "d12": (345, 35, 13, 239, 22),
    "d13": (194, 11, 1, 191, 28),
    "d14": (185, 4, 2, 96, 10),
    "d15": (816, 25, 7, 444, 106),
    "d16": (207, 4, 5, 121, 15),
    "d17": (183, 2, 2, 64, 13),
}
DELEGATE_PRINTED = (4215, 140, 108, 2169, 316)
DELEGATE_TOTALS = (3, 498, 271, 423, 288, 376, 245, 210, 333, 401, 223, 287, 654, 425, 297, 1398, 352, 264)
DELEGATE_COLUMNS = ((0.340, 0.431), (0.431, 0.538), (0.538, 0.647), (0.647, 0.725), (0.725, 0.841))
TABLE_ROWS_PAGE_2 = 22  # ruled lines: top, header bottom, 18 booth rows, total, percent

# Pages 3 and 4, the House: (page, vertical center as a fraction of the page height) of each candidate's row.
# (name as printed, votes, ELECTED mark, page, row center)
HOUSE_ROWS: dict[int, list[tuple[str, int, bool, int, float]]] = {
    1: [
        ("FETUI F. FETU JR.", 251, True, 4, 281),
        ("MANA'OIMALO FAAALIGA MATAGI", 149, False, 4, 321),
        ("PAEPAETELE MAPU JAMIAS", 71, False, 4, 361),
        ("SATIA AOKUSO SATIA", 194, False, 4, 401),
        ("VALA POROTESANO LIUSAMOA", 211, True, 4, 441),
    ],
    2: [("NIUATOA LOLENI FAIAI", 104, False, 4, 561), ("TIAOALI'I FAUAGIGA T. SAI", 169, True, 4, 601)],
    3: [("SHAUN O. VA'A", 274, True, 4, 721), ("TIAPULA DR. TOAFA VAIAGA'E", 152, False, 4, 761)],
    4: [
        ("DR. VA'ASA SIMANU", 136, True, 4, 881),
        ("TITIALI'I KITARA VAIAU", 55, False, 4, 921),
        ("VITOLIO I. LOTU", 99, False, 4, 961),
    ],
    5: [("LUAITAUA GENE PAN", 239, True, 4, 1081), ("LYNN NUUULI PULOU-ALAIMALO", 135, False, 4, 1121)],
    6: [
        ("AVAOLAGI TA'ATIATIA I. MATA'UTIA SUA", 123, False, 4, 1239),
        ("MATAGAONO VAIMAGA MAIAVA", 124, True, 4, 1279),
    ],
    7: [("VAILOATA ETEUATI AMITUANA'I", 205, True, 4, 1399)],
    8: [("MALAEOLETALU MELESIO GURR", 236, True, 4, 1520), ("MARIOTA TUIASOSOPO", 100, False, 4, 1560)],
    9: [("TRUDE GASETOTOLEMASINA LEDOUX-SUNIA", 390, True, 4, 1680)],
    10: [
        ("SAENA OMAR SHALHOUT", 132, True, 3, 259),
        ("TALALI PERRY TO. LOLOASO WIGHTMAN-UIA", 28, False, 3, 299),
        ("TAPAI ALAILEPULE BEN VAIVAO", 64, False, 3, 339),
    ],
    11: [("FAIMEALELEI ANTHONY FU'E ALLEN", 182, True, 3, 459), ("MUAIAVAONA FOFOGA PILA", 106, False, 3, 499)],
    12: [
        ("MANUMAUA WYNE WILSON", 547, True, 3, 620),
        ("SAUSETOA TAUTOLOITUA SOLIAI HO CHING", 542, True, 3, 660),
    ],
    13: [("FIU J. SAELUA", 409, True, 3, 779)],
    14: [("MONA ULI", 25, False, 3, 899), ("SAVALI TALAVOU ALE", 272, True, 3, 939)],
    15: [
        ("ALEX SENE JR.", 260, False, 3, 1060),
        ("BEN VAOMU SOLOMONA SAUVAO", 633, True, 3, 1099),
        ("FAGAIMA LARRY SANITOA", 879, True, 3, 1139),
        ("SEFULU MIKA KELEMETE", 618, False, 3, 1178),
        ('TAPUSALAIA "TAP" MOEMALEMALO MANEAFAIGA', 177, False, 3, 1218),
    ],
    16: [("MANAVAALOFA TUTUILA MANASE", 292, True, 3, 1338)],
    17: [("APE MIKE ASIFOA", 211, True, 3, 1458), ("FUASALI'I I. IOSE", 57, False, 3, 1498)],
}
SHEET = (1414, 2000)  # the size the rows above were read at; positions are stored as fractions of it
VOTE_COLUMN = {3: (1035, 1150), 4: (1055, 1175)}
HOUSE_PRELIMINARY_NOTE = (
    "In District 10 the election-night sheet gave Wightman-Uia 64 votes and Vaivao 28; the certified sheet gives "
    "Wightman-Uia 28 and Vaivao 64 (Talanei, November 12, 2026). Saena's 132 votes and election are unchanged."
)


def norm(label: str) -> str:
    return re.sub(r"[^a-z ]", "", label.lower().replace("’", "").replace("'", ""))


def refs() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with REFS.open() as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            out.setdefault(row["kind"], {})[row["key"]] = row["id"]
    return out


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def render(artifact: raw.Artifact, number: int) -> tuple[Path, int, int]:
    image = PAGES / f"{artifact.sha256}-{number}.png"
    if not image.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(
                ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                + [str(artifact.path), f"{folder}/p"],
                check=True,
            )
            shutil.copy(f"{folder}/p.png", image)
    width, height = Image.open(image).size
    return image, width, height


def rules(image: Path) -> list[float]:
    """The vertical positions of the table's long ruled lines, as fractions of the page height."""
    from ea import scan

    (table,) = scan.tables(image)
    height = Image.open(image).size[1]
    return [y / height for y in table[0]]


def parse() -> None:
    fetch()
    table = refs()
    artifact = raw.cached(SOURCE, URL)
    p = Package(
        KEY,
        SOURCE,
        "American Samoa's November 3, 2026 Delegate and House of Representatives elections as the Election Office "
        "certified them, by polling district",
    )
    p.external |= {COUNTRY, STATE}
    source = p.id("source", "official-results")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=TITLE,
        url=URL,
        license="Public record",
    )
    stored = p.artifact(artifact, source)
    pages: dict[int, str] = {}
    sizes: dict[int, tuple[int, int]] = {}
    images: dict[int, Path] = {}
    for number in (2, 3, 4):
        images[number], width, height = render(artifact, number)
        sizes[number] = (width, height)
        pages[number] = p.id("page", number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=stored,
            page_number=number,
            image_key=f"pages/{SOURCE}/{images[number].name}",
            width=width,
            height=height,
        )
    p.external |= set(table["election"].values()) | set(table["unit"].values())
    p.external |= set(table["contest"].values()) | set(table["prelim"].values()) | set(table["option"].values())
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []

    def put(result_set: str, unit: str, option: str, votes: int, page: int, box: tuple[float, float, float, float]):
        values.append(
            {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": VOTES,
                "count_round": 0,
                "flags": 0,
                "value": votes,
            }
        )
        locators.append(
            {
                "result_set_id": result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": VOTES,
                "count_round": 0,
                "page_id": pages[page],
                "x0": round(box[0], 5),
                "y0": round(box[1], 5),
                "x1": round(box[2], 5),
                "y1": round(box[3], 5),
            }
        )

    def result_set(contest_key: str, note: str) -> tuple[str, str]:
        contest = table["contest"][contest_key]
        rs = p.id("result_set", contest_key)
        p.add(
            "result_set",
            id=rs,
            election_id=table["election"][contest_key],
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            as_of=AS_OF,
            is_default=True,
            completeness=1.0,
            supersedes_id=table["prelim"][contest_key],
        )
        p.add("note", id=p.id("note", rs), entity_id=rs, kind="methodology", body_md=note, lang="en")
        return contest, rs

    # The Delegate: one set, 17 districts and the central office (Swains Island).
    options = {
        name: next(
            id
            for key, id in table["option"].items()
            if key.startswith(DELEGATE + "|") and norm(name) == norm(key.split("|")[1])
        )
        for name in DELEGATE_NAMES
    }
    lines = rules(images[2])
    if len(lines) != TABLE_ROWS_PAGE_2:
        raise ValueError(f"page 2 has {len(lines)} ruled lines, expected {TABLE_ROWS_PAGE_2}")
    _, delegate = result_set(
        DELEGATE,
        "Votes by polling district from the territorial summary in the Election Office's official results of the "
        "2026 Midterm Election, issued November 10, 2026 after the period for contesting the results lapsed (reported "
        "by Talanei, November 12, 2026). The central office row holds Swains Island. The sheet is a scanned image; "
        "each figure was read from the page and is stored with its box. The sheet's district rows sum to the totals "
        "it prints (Amata 4,215, Lancaster 140, Chapman 108, Young 2,169, Lefaoseu 316; 6,948 in all). It marks "
        "no winner; Amata, with 60.7 percent, is above the majority a runoff would require.",
    )
    for index, (code, row) in enumerate(DELEGATE_ROWS.items()):
        if sum(row) != DELEGATE_TOTALS[index]:
            raise ValueError(f"{code}: row sums to {sum(row)}, the sheet prints {DELEGATE_TOTALS[index]}")
        unit = table["unit"][f"2026-11-03:AS:{code}"]
        top, bottom = lines[2 + index], lines[3 + index]
        for name, votes, (left, right) in zip(DELEGATE_NAMES, row, DELEGATE_COLUMNS, strict=True):
            put(delegate, unit, options[name], votes, 2, (left, top, right, bottom))
    for column, name in enumerate(DELEGATE_NAMES):
        total = sum(row[column] for row in DELEGATE_ROWS.values())
        if total != DELEGATE_PRINTED[column]:
            raise ValueError(f"{name}: districts sum to {total}, the sheet prints {DELEGATE_PRINTED[column]}")
        p.add("declared", result_set_id=delegate, option_id=options[name], measure=VOTES, value=total)

    # The House: one set for each of the 17 districts.
    for number, slug in DISTRICTS.items():
        key = HOUSE + slug
        labels = {k.split("|")[1]: id for k, id in table["option"].items() if k.startswith(key + "|")}
        rows = HOUSE_ROWS[number]
        if len(rows) != len(labels):
            raise ValueError(f"district {number}: {len(rows)} sheet rows, {len(labels)} options")
        scored = sorted(
            (
                (difflib.SequenceMatcher(None, norm(name), norm(label)).ratio(), r, label)
                for r, (name, *_) in enumerate(rows)
                for label in labels
            ),
            reverse=True,
        )
        paired: dict[int, str] = {}
        used: set[str] = set()
        for score, r, label in scored:
            if r in paired or label in used:
                continue
            if score < 0.7:
                raise ValueError(f"district {number}: {rows[r][0]} vs {label} scores {score:.2f}")
            paired[r] = label
            used.add(label)
        note = (
            "Votes for each candidate as printed on the Election Office's official results of the 2026 Midterm "
            "Election, issued November 10, 2026 after the period for contesting the results lapsed (reported by "
            "Talanei, November 12, 2026), with the sheet's ELECTED marks stored as the outcomes. The sheet is a "
            "scanned image; each figure was read from the page and is stored with its box. The sheet has no "
            "Swains Island seat, which the island's voters fill at a meeting."
        )
        if number == 10:
            note += " " + HOUSE_PRELIMINARY_NOTE
        contest, rs = result_set(key, note)
        unit = table["unit"][f"2026-11-03:AS:d{number:02d}"]
        for r, (name, votes, elected, page, center) in enumerate(rows):
            option = labels[paired[r]]
            left, right = VOTE_COLUMN[page]
            fx, fy = SHEET
            put(rs, unit, option, votes, page, (left / fx, (center - 16) / fy, right / fx, (center + 16) / fy))
            p.add("declared", result_set_id=rs, option_id=option, measure=VOTES, value=votes)
            if elected:
                p.add("outcome", result_set_id=rs, option_id=option, kind="elected", seats=None)
        del contest
    p.frame("result_value", pl.DataFrame(values))
    p.frame("value_locator", pl.DataFrame(locators))
    p.write()


def emit() -> None:
    package.run("load", [KEY])
