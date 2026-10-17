import difflib
import io
import itertools
import json
import re
import subprocess
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image, ImageFilter

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.mo_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://web.archive.org/web/2025id_/https://www.sos.mo.gov/CMSImages/ElectionResultsStatistics/"
COUNTY = SITE + "CountyGeneral1996.pdf"
STATE = SITE + "AllRacesGeneralNovember1996.pdf"
PUBLISHER = "Missouri Secretary of State"
TITLE = "Official election returns"
BOOK = "Official Manual of the State of Missouri"
IMAGE = "https://mdh.contentdm.oclc.org/iiif/2/bluebook:{}/full/full/0/default.jpg"
TEXT = "https://mdh.contentdm.oclc.org/digital/api/collections/bluebook/items/{}/false"
DAY = "1996-11-05"
MANUAL = [
    (
        "1968-11-05",
        "president",
        (41027, 41028),
        [
            ("DEM", "Hubert H. Humphrey, Edmund S. Muskie"),
            ("REP", "Richard M. Nixon, Spiro T. Agnew"),
            ("AIP", "George C. Wallace, Marvin Griffin"),
        ],
    ),
    ("1968-11-05", "senate", (41029,), [("DEM", "Eagleton, Thomas F."), ("REP", "Curtis, Thomas B.")]),
    ("1968-11-05", "governor", (41030,), [("DEM", "Hearnes, Warren E."), ("REP", "Roos, Lawrence K.")]),
    ("1968-11-05", "lieutenant-governor", (41031,), [("DEM", "Morris, W. S. (Bill)"), ("REP", "Jones Jr., Lem T.")]),
    (
        "1968-11-05",
        "secretary-of-state",
        (41032,),
        [("DEM", "Kirkpatrick, James C. (Jim)"), ("REP", "Colbert, Murray C.")],
    ),
    ("1968-11-05", "treasurer", (41033,), [("DEM", "Robinson, William E."), ("REP", "Zimmerman, William T.")]),
    ("1968-11-05", "attorney-general", (41034,), [("DEM", "Anderson, Norman H."), ("REP", "Danforth, John C.")]),
    (
        "1976-11-02",
        "president",
        (47255,),
        [
            ("REP", "Gerald R. Ford, Robert J. Dole"),
            ("DEM", "Jimmy Carter, Walter F. Mondale"),
            ("IND", "Eugene J. McCarthy, Marlene K. Barrett"),
        ],
    ),
    (
        "1976-11-02",
        "senate",
        (47256,),
        [("REP", "Danforth, John C."), ("DEM", "Hearnes, Warren E."), ("IND", "Petty, Lawrence (Red)")],
    ),
    (
        "1976-11-02",
        "governor",
        (47257,),
        [("REP", "Bond, Christopher (Kit)"), ("DEM", "Teasdale, Joseph P."), ("IND", "Striler, Leon")],
    ),
    (
        "1976-11-02",
        "lieutenant-governor",
        (47258,),
        [("REP", "Phelps, William (Bill)"), ("DEM", "Rabbitt, Richard J. (Dick)"), ("IND", "Fischer, Leo J.")],
    ),
    (
        "1976-11-02",
        "secretary-of-state",
        (47259,),
        [("REP", "Huffman, Mildred P."), ("DEM", "Kirkpatrick, James C."), ("IND", "Carmack, Evelyn F.")],
    ),
    ("1976-11-02", "treasurer", (47260,), [("REP", "Kemp Jr., Albert L. (Al)"), ("DEM", "Spainhower, James I.")]),
    ("1976-11-02", "attorney-general", (47261,), [("REP", "Ashcroft, John"), ("DEM", "Baker, James G.")]),
    ("1978-11-07", "auditor", (48822,), [("DEM", "Hearnes, Warren E."), ("REP", "Antonio, James F.")]),
    (
        "1980-11-04",
        "president",
        (50270, 50271),
        [
            ("DEM", "Jimmy Carter, Walter F. Mondale"),
            ("REP", "Ronald Reagan, George Bush"),
            ("SWP", "Clifton DeBerry, Matilde Zimmermann"),
            ("LIB", "Ed Clark, David Koch"),
            ("IND", "John B. Anderson, Patrick J. Lucey"),
        ],
    ),
    (
        "1980-11-04",
        "senate",
        (50272,),
        [("DEM", "Eagleton, Thomas F."), ("REP", "McNary, Gene"), ("SWP", "Pettit, Martha")],
    ),
    (
        "1980-11-04",
        "governor",
        (50273,),
        [("DEM", "Teasdale, Joseph P."), ("REP", "Bond, Christopher (Kit)"), ("SWP", "Savio, Helen")],
    ),
    ("1980-11-04", "lieutenant-governor", (50274,), [("DEM", "Rothman, Kenneth J."), ("REP", "Blunt, Roy")]),
    (
        "1980-11-04",
        "secretary-of-state",
        (50275,),
        [("DEM", "Kirkpatrick, James C."), ("REP", "Pfeffer II, Walter L.")],
    ),
    ("1980-11-04", "treasurer", (50276,), [("DEM", "Carnahan, Mel"), ("REP", "Winship, Gerald E.")]),
    ("1980-11-04", "attorney-general", (50277,), [("DEM", "Baine, Robert"), ("REP", "Ashcroft, John")]),
    ("1982-11-02", "senate", (51653,), [("REP", "Danforth, John C."), ("DEM", "Woods, Harriett")]),
    ("1982-11-02", "auditor", (51654,), [("REP", "Antonio, James"), ("DEM", "Butler, James R. (Russ)")]),
    (
        "1984-11-06",
        "president",
        (53069,),
        [("REP", "Ronald Reagan, George Bush"), ("DEM", "Walter F. Mondale, Geraldine A. Ferraro")],
    ),
    ("1984-11-06", "governor", (53070,), [("REP", "Ashcroft, John"), ("DEM", "Rothman, Kenneth J.")]),
    ("1984-11-06", "lieutenant-governor", (53071,), [("REP", "Hancock, Mel"), ("DEM", "Woods, Harriett")]),
    ("1984-11-06", "secretary-of-state", (53072,), [("REP", "Blunt, Roy D."), ("DEM", "Sharpe, Gary D.")]),
    ("1984-11-06", "treasurer", (53073,), [("REP", "Bailey, Wendell"), ("DEM", "Villa, Thomas A.")]),
    ("1984-11-06", "attorney-general", (53074,), [("REP", "Webster, William L."), ("DEM", "Beard, Richard P.")]),
    (
        "1986-11-04",
        "senate",
        (54491,),
        [("REP", "Bond, Christopher (Kit)"), ("DEM", "Woods, Harriett"), ("IND", "Shepherd, Betty")],
    ),
    ("1986-11-04", "auditor", (54492,), [("REP", "Kelly, Margaret"), ("DEM", "Morrison, Travis")]),
    (
        "1988-11-08",
        "president",
        (55349,),
        [
            ("REP", "George Bush, Dan Quayle"),
            ("DEM", "Michael Dukakis, Lloyd Bentsen"),
            ("IND", "Lenora Fulani, Joyce Dattner"),
        ],
    ),
    (
        "1988-11-08",
        "senate",
        (55350,),
        [("REP", "Danforth, John C."), ("DEM", "Nixon, Jeremiah W."), ("LIB", "Guze, John")],
    ),
    (
        "1988-11-08",
        "governor",
        (55351,),
        [("REP", "Ashcroft, John"), ("DEM", "Hearnes, Betty C."), ("LIB", "Roberts, Mike")],
    ),
    (
        "1988-11-08",
        "lieutenant-governor",
        (55352,),
        [("REP", "Grisham, R. B."), ("DEM", "Carnahan, Mel"), ("LIB", "Rosenberg, Richard")],
    ),
    (
        "1988-11-08",
        "secretary-of-state",
        (55353,),
        [("REP", "Blunt, Roy D."), ("DEM", "Askew, James J."), ("LIB", "Manifold, Jay")],
    ),
    (
        "1988-11-08",
        "treasurer",
        (55354,),
        [("REP", "Bailey, Wendell"), ("DEM", "Holden, Bob"), ("LIB", "Geier, Gerald")],
    ),
    ("1988-11-08", "attorney-general", (55355,), [("REP", "Webster, William L."), ("DEM", "Wolff, Michael A.")]),
    ("1990-11-06", "auditor", (56834,), [("REP", "Kelly, Margaret"), ("DEM", "Hendren, Connie B.")]),
    (
        "1992-11-03",
        "president",
        (58227, 58228),
        [
            ("REP", "George Bush, Dan Quayle"),
            ("DEM", "Bill Clinton, Al Gore"),
            ("LIB", "Andre Marrou, Nancy Lord"),
            ("IND", "Ross Perot, James Stockdale"),
        ],
    ),
    (
        "1992-11-03",
        "senate",
        (58229,),
        [("REP", "Bond, Christopher (Kit)"), ("DEM", "Rothman-Serot, Geri"), ("LIB", "Bojarski, Jeanne F.")],
    ),
    ("1992-11-03", "governor", (58230,), [("REP", "Webster, William L."), ("DEM", "Carnahan, Mel")]),
    (
        "1992-11-03",
        "lieutenant-governor",
        (58231,),
        [("REP", "Kelly, Margaret"), ("DEM", "Wilson, Roger B."), ("LIB", "Nugent, Franklin M.")],
    ),
    (
        "1992-11-03",
        "secretary-of-state",
        (58232,),
        [("REP", "Hancock, John"), ("DEM", "Moriarty, Judith K. (Judi)"), ("LIB", "Harris, Eric")],
    ),
    (
        "1992-11-03",
        "treasurer",
        (58233,),
        [("REP", "Melton, Gary"), ("DEM", "Holden, Bob"), ("LIB", "Lewis, Janet")],
    ),
    (
        "1992-11-03",
        "attorney-general",
        (58234,),
        [("REP", "Steelman, David L."), ("DEM", "Nixon, Jeremiah W. (Jay)"), ("LIB", "Moore, Mitchell J.")],
    ),
    (
        "1994-11-08",
        "auditor",
        (59735,),
        [("DEM", "Danner, Steve"), ("REP", "Kelly, Margaret"), ("LIB", "Stauffer, Grant")],
    ),
    (
        "1994-11-08",
        "senate",
        (59736,),
        [("DEM", "Wheat, Alan"), ("REP", "Ashcroft, John"), ("LIB", "Johnson, Bill")],
    ),
    (
        "1998-11-03",
        "senate",
        (62622, 62623),
        [
            ("DEM", "Nixon, Jeremiah W. (Jay)"),
            ("REP", "Bond, Christopher (Kit)"),
            ("LIB", "Millay, Tamara A."),
            ("UST", "Frazier, Curt"),
            ("REF", "Newport, James F."),
        ],
    ),
    (
        "1998-11-03",
        "auditor",
        (62624, 62625),
        [
            ("DEM", "McCaskill, Claire C."),
            ("REP", "Pierce, Charles A. (Chuck)"),
            ("LIB", "Geier, Gerald R."),
            ("REF", "Weber, George D."),
        ],
    ),
]
ALONE = {"Eric Harris", "George D. Weber"}
CHECKED = {
    ("1992-11-03", "treasurer", 1),
    ("1976-11-02", "governor", 0),
    ("1976-11-02", "treasurer", 0),
    ("1976-11-02", "attorney-general", 0),
    ("1980-11-04", "senate", 0),
    ("1982-11-02", "auditor", 0),
    ("1988-11-08", "president", 2),
}
FIXES: dict[tuple[str, str, str], list[int]] = {
    ("1994-11-08", "auditor", "29141"): [1963, 3734, 222],
    ("1968-11-05", "attorney-general", "29097"): [13153, 15852],
    ("1968-11-05", "attorney-general", "29229"): [1454, 3564],
    ("1968-11-05", "governor", "29097"): [19198, 11034],
    ("1976-11-02", "attorney-general", "29227"): [734, 936],
    ("1976-11-02", "attorney-general", "29135"): [3390, 2052],
    ("1982-11-02", "senate", "29029"): [4190, 3773],
    ("1982-11-02", "senate", "29107"): [5939, 5550],
    ("1982-11-02", "senate", "29147"): [3805, 3564],
    ("1982-11-02", "auditor", "29073"): [3625, 904],
    ("1982-11-02", "auditor", "29175"): [4043, 3688],
}
OFFICES = {
    "U.S. President And Vice President": "president",
    "Governor": "governor",
    "Lieutenant Governor": "lieutenant-governor",
    "Secretary Of State": "secretary-of-state",
    "State Treasurer": "treasurer",
    "Attorney General": "attorney-general",
}
TITLES = {
    "lieutenant-governor": "Lieutenant Governor",
    "secretary-of-state": "Secretary of State",
    "treasurer": "State Treasurer",
    "attorney-general": "Attorney General",
    "auditor": "State Auditor",
}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LIB": "libertarian-party",
    "UST": "MO:u-s-taxpayers-party",
    "REF": "reform-party",
    "NAT": "natural-law-party",
    "IND": None,
    "AIP": "american-independent-party",
    "SWP": "socialist-workers-party",
}
WRITE_IN = "Write-ins"


def fetch() -> None:
    for url in (COUNTY, STATE):
        raw.fetch(SOURCE, url, suffix=".pdf")
    for _, _, pages, _ in MANUAL:
        for page in pages:
            raw.fetch(SOURCE, IMAGE.format(page), suffix=".jpg")
            raw.fetch(SOURCE, TEXT.format(page), suffix=".json")


def readings(text: str, width: int, names: dict[str, str]) -> dict[str, list[int]]:
    text = re.sub(
        r"(?<=\d)[)\]]|[|]",
        " ",
        re.sub(
            r"\bS[1l]\.",
            "St.",
            re.sub(
                r"(?<=\d) ,(?=\d{3}\b)",
                ",",
                re.sub(r"(?<=\d )[lI](?=\s|$)", "1", re.sub(r"(?m)^(\s*TOTALS)[^\d\n]*", r"\1 ", text)),
            ),
        ),
    )
    found: dict[str, list[int]] = {}
    pattern = r"((?:[A-Za-z.']+ ){0,2}[A-Za-z.']+)\s+((?:\d[\d,./]*\s+){" + str(width - 1) + r"}\d[\d,./]*)(?=\s|$)"
    for line in text.splitlines():
        for match in re.finditer(pattern, line):
            if re.match(r"\s+\d", line[match.end() :]):
                continue
            words = match[1].split()
            county = next(
                (
                    names[key]
                    for start in range(len(words))
                    if (key := re.sub(r"[^a-z]", "", " ".join(words[start:]).lower())) in names
                ),
                None,
            )
            if county is None:
                close = difflib.get_close_matches(re.sub(r"[^a-z]", "", match[1].lower()), names, 1, 0.8)
                county = names[close[0]] if close else None
            if county is not None and county not in found:
                found[county] = [int(re.sub(r"\D", "", value.replace("/", "7"))) for value in match[2].split()]
    pair = (
        r"\b((?:[A-Za-z.']+ ){0,2}?[A-Za-z.']+) ((?:[A-Za-z.']+ ){0,2}?[A-Za-z.']+) ((?:\d[\d,]* ){"
        + str(2 * width - 1)
        + r"}\d[\d,]*)(?= |$)"
    )
    for match in re.finditer(pair, " ".join(text.split())):
        first, second = (re.sub(r"[^a-z]", "", match[group].lower()) for group in (1, 2))
        values = [int(value.replace(",", "")) for value in match[3].split()]
        if first in names and second in names and names[first] not in found and names[second] not in found:
            found[names[first]] = values[:width]
            found[names[second]] = values[width:]
    return found


def manual(names: dict[str, str]) -> list[dict[str, object]]:
    (PARSED / "ocr").mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    caveats: list[dict[str, object]] = []
    for day, office, pages, candidates in MANUAL:
        width = len(candidates)
        reads: list[dict[str, list[int]]] = [{}, {}, {}, {}, {}, {}]
        for page in pages:
            texts = []
            for mode in ("gray", "150", "halves", "halves150", "sharp"):
                cache = PARSED / "ocr" / f"{page}-{mode}.txt"
                if not cache.exists():
                    image = Image.open(raw.cached(SOURCE, IMAGE.format(page)).path).convert("L")
                    image = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
                    if mode in ("150", "halves150"):
                        image = image.point(lambda value: 255 if value > 150 else 0)
                    if mode == "sharp":
                        image = image.filter(ImageFilter.UnsharpMask(4, 200, 2))
                    parts = [image]
                    if mode in ("halves", "halves150"):
                        parts = [
                            image.crop((0, 0, image.width // 2, image.height)),
                            image.crop((image.width // 2, 0, image.width, image.height)),
                        ]
                    output = []
                    for part in parts:
                        buffer = io.BytesIO()
                        part.save(buffer, "PNG")
                        output.append(
                            subprocess.run(
                                ["tesseract", "stdin", "stdout", "--psm", "6"],
                                input=buffer.getvalue(),
                                capture_output=True,
                                check=True,
                            ).stdout.decode()
                        )
                    cache.write_text("\n".join(output))
                texts.append(cache.read_text())
            texts.append(
                re.sub(
                    r"(TOTALS(?: [\d,]+){" + str(width) + r"}) \d+$",
                    r"\1",
                    " ".join(
                        (json.loads(raw.cached(SOURCE, TEXT.format(page)).path.read_text())["text"] or "").split()
                    ),
                )
            )
            for read, text in zip(reads, texts, strict=True):
                for county, values in readings(text, width, names).items():
                    read.setdefault(county, values)
        printed = [read["TOTALS"] for read in reversed(reads) if "TOTALS" in read]
        if not printed:
            raise ValueError(f"{day} {office}: no TOTALS row")
        totals = max(printed, key=printed.count)
        for read in reads:
            read.pop("TOTALS", None)
        counties = {
            county: [
                max(
                    (votes for votes in (read[county][column] for read in reversed(reads) if county in read)),
                    key=lambda votes, county=county, column=column: sum(
                        1 for read in reads if county in read and read[county][column] == votes
                    ),
                )
                for column in range(width)
            ]
            for county in set().union(*reads)
        }
        for (when, what, county), values in FIXES.items():
            if (when, what) == (day, office):
                counties[county] = values
        if len(counties) != (116 if "KC" in counties else 115):
            raise ValueError(
                f"{day} {office}: missing {sorted(set(names.values()) - set(counties) - {'TOTALS', 'KC'})}"
            )
        for column in range(width):
            total = sum(values[column] for values in counties.values())
            if any(read[column] == total for read in printed):
                continue
            gap = totals[column] - total
            others = sorted(
                {
                    (county, read[county][column])
                    for county in counties
                    if (day, office, county) not in FIXES
                    for read in reads
                    if county in read and read[county][column] != counties[county][column]
                }
            )
            swaps = [
                chosen
                for size in range(1, 4)
                for chosen in itertools.combinations(others, size)
                if len({county for county, _ in chosen}) == size
                and sum(value - counties[county][column] for county, value in chosen) == gap
            ]
            agreed = all(
                sum(1 for read in reads if county in read and read[county][column] == values[column]) >= 2
                for county, values in counties.items()
                if (day, office, county) not in FIXES
            )
            if (day, office, column) in CHECKED or (not swaps and agreed):
                caveats.append(
                    {
                        "day": day,
                        "office": office,
                        "note": f"The Manual's printed total for {candidates[column][1]} is {totals[column]:,}; "
                        f"its county figures sum to {totals[column] - gap:,}.",
                    }
                )
                continue
            if not swaps or len([chosen for chosen in swaps if len(chosen) == len(swaps[0])]) > 1:
                raise ValueError(f"{day} {office}: column {column} is off by {gap}, other readings {others}")
            for county, value in swaps[0]:
                counties[county][column] = value
        for county, values in counties.items():
            for (party, label), votes in zip(candidates, values, strict=True):
                rows.append(
                    {
                        "day": day,
                        "book": BOOK,
                        "office": office,
                        "county": names["jackson"] if county == "KC" else county,
                        "label": label,
                        "party": party,
                        "votes": votes,
                    }
                )
    pl.DataFrame(caveats, schema={"day": pl.String, "office": pl.String, "note": pl.String}).write_parquet(
        PARSED / "caveats.parquet"
    )
    return rows


def read(url: str) -> dict[str, dict[str, list[tuple[str, str, int]]]]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    found: dict[str, dict[str, list[tuple[str, str, int]]]] = {}
    county = "STATE"
    office = None
    for line in text.splitlines():
        if heading := re.fullmatch(r" {5}([A-Z][A-Z .']+)", line):
            county, office = heading[1].strip(), None
        elif line.strip() in OFFICES and re.match(r" {0,5}\S", line):
            office = OFFICES[line.strip()]
            found.setdefault(office, {}).setdefault(county, [])
        elif office is not None and (
            row := re.fullmatch(r"\s+(\S.*?)\s{2,}([A-Z]{2,3})\s+([\d,]+)(?:\s+[\d.]+%)?", line)
        ):
            found[office][county].append((row[1], row[2], int(row[3].replace(",", ""))))
        elif office is not None and (row := re.fullmatch(r"\s+Total Votes\s+([\d,]+)", line)):
            if sum(votes for _, _, votes in found[office][county]) != int(row[1].replace(",", "")):
                raise ValueError(f"{county} {office}: rows do not sum to {row[1]}")
            office = None
    return found


def parse() -> None:
    with connect() as conn:
        codes = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%'"""
            ).fetchall()
        }
    if len(codes) != 114:
        raise ValueError("Missouri needs 114 county names")
    keys = {"stlouiscity": "29510", "stlouiscounty": "29189", "kansascity": codes["jackson"]}
    statewide = read(STATE)
    rows: list[dict[str, object]] = []
    for office, counties in read(COUNTY).items():
        if len(counties) != 116:
            raise ValueError(f"{office}: {len(counties)} counties")
        printed = statewide[office]["STATE"]
        sums: dict[str, int] = {}
        for county, lines in counties.items():
            key = re.sub(r"[^a-z]", "", county.lower())
            code = keys.get(key) or codes[key]
            for label, party, votes in lines:
                sums[label] = sums.get(label, 0) + votes
                rows.append(
                    {
                        "day": DAY,
                        "book": TITLE,
                        "office": office,
                        "county": code,
                        "label": label,
                        "party": party,
                        "votes": votes,
                    }
                )
        if sums != {label: votes for label, _, votes in printed}:
            raise ValueError(f"{office}: counties sum to {sums}, printed {printed}")
    names = {
        **codes,
        "stlouis": "29189",
        "stlouiscity": "29510",
        "city": "29510",
        "kansascity": "KC",
        "lron": codes["iron"],
        "stlouiscounty": "29189",
        "louiscounty": "29189",
        "girardeau": codes["capegirardeau"],
        "genevieve": codes["stegenevieve"],
        "totals": "TOTALS",
    }
    rows.extend(manual(names))
    frame = (
        pl.DataFrame(rows)
        .group_by("day", "book", "office", "county", "label", "party", maintain_order=True)
        .agg(pl.col("votes").sum())
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's president, governor, U.S. Senate, and statewide offices by county 1968–1998 "
            "from the Secretary of State's official returns and the Official Manual",
            polity="US",
        )

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def parts(text: str) -> tuple[list[str], set[str]]:
            words = [plain(word) for word in re.sub(r'"[^"]*"|\([^)]*\)', " ", text).split()]
            suffixes = {"jr", "sr", "ii", "iii", "iv"}
            return [word for word in words if word and word not in suffixes], set(words) & suffixes

        def surname(text: str) -> str:
            words = parts(text)[0]
            return words[-1] if words else ""

        def shown(label: str, office: str) -> str:
            if office == "president":
                return label.split(",")[0].strip()
            last, _, first = label.partition(", ")
            first = re.sub(r"\(([^)]*)\)", r'"\1"', first)
            return f"{first} {last}".strip()

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        sources = {TITLE: cs.source(PUBLISHER, TITLE), BOOK: cs.source(PUBLISHER, BOOK)}
        for title, url in (
            (TITLE, "https://www.sos.mo.gov/elections/resultsandstats"),
            (BOOK, "https://mdh.contentdm.oclc.org/digital/collection/bluebook"),
        ):
            cs.add(
                "source",
                id=sources[title],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license="Public record",
            )
        for url in (COUNTY, STATE):
            cs.artifact(raw.cached(SOURCE, url), sources[TITLE])
        for _, _, pages, _ in MANUAL:
            for page in pages:
                for url in (IMAGE, TEXT):
                    cs.artifact(raw.cached(SOURCE, url.format(page)), sources[BOOK])
        caveats = {(day, office): note for day, office, note in pl.read_parquet(PARSED / "caveats.parquet").iter_rows()}
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '29%%'"
            ).fetchall()
        )
        if len(counties) != 115:
            raise ValueError("Missouri needs 115 counties")
        people: dict[str, set[UUID]] = {}
        ranks: dict[UUID, tuple[bool, bool, list[int]]] = {}
        texts: dict[UUID, str] = {}
        for person, text, statewide, known, years in conn.execute(
            """select cd.person_id, n.text, bool_or(x.value ~ ':MO:[a-z-]+(:special|:democratic|:republican)?$'),
                      bool_or(exists (select from external_id w where w.entity_id = cd.person_id
                                      and w.scheme in ('wikidata', 'bioguide'))),
                      array_agg(distinct left(x.value, 4)::int)
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                    and x.value ~ '^\\d{4}.*(:MO:|:president$)' and x.value !~ ':MO:29\\d{3}:'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            if not text.split():
                continue
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
            ranks[person] = (statewide, known, years)
            texts[person] = text

        def fits(one: UUID, candidate: str, year: int) -> bool:
            if min((abs(other - year) for other in ranks[one][2]), default=0) > 30:
                return False
            mine, mark = parts(candidate)
            theirs, their_mark = parts(texts[one])
            if mark & {"ii", "iii", "iv"} != their_mark & {"ii", "iii", "iv"}:
                return False
            return len(mine) < 3 or len(theirs) < 3 or mine[1][:1] == theirs[1][:1]

        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", party) if party else None for code, party in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()} - {None}:
            raise ValueError("the electoral system and parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':MO:'")
        )
        ((area,),) = conn.execute(
            "select district_area_id from contest where id = %s", [contests["1996-11-05:MO:governor"]]
        ).fetchall()
        elections: dict[str, UUID] = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.election' and value ~ '^\\d{4}-\\d\\d-\\d\\d:MO$'"
            )
        )
        columns = (
            "id",
            "election_id",
            "contest_id",
            "stage",
            "kind",
            "status",
            "source_id",
            "as_of",
            "completeness",
            "supersedes_id",
        )
        for (day, book, office), lines in frame.group_by("day", "book", "office", maintain_order=True):
            key = f"{day}:MO:{office}"
            year = int(day[:4])
            result_set = cs.key(f"{SOURCE}.result_set", key)
            contest = contests.get(key)
            if contest is None:
                office_id = cs.key("us.office", f"{office}:MO")
                if office_id in cs.minted:
                    raise ValueError(f"no office {office}")
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=elections[f"{day}:MO"],
                    kind="office",
                    office_id=office_id,
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} Missouri {TITLES[office]} election")
                cs.add("slug", path=f"us/mo/{day}-general/{office}", entity_id=contest)
                election_id = elections[f"{day}:MO"]
            else:
                ((election_id,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket', 'write_in')""",
                [contest],
            ).fetchall()
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                if row[0] != result_set:
                    cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            on_file = dict(
                conn.execute(
                    """select o.id, sum(v.value)::bigint from result_value v
                       join result_set rs on rs.k = v.result_set_k and rs.is_default
                       join contest_option o on o.k = v.option_k
                       where rs.contest_id = %s and v.measure = 1 group by 1""",
                    [contest],
                ).fetchall()
            )
            picked: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for label, code, votes in (
                lines.group_by("label", "party", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                candidate = shown(label, office)
                matches = [option for option, _, text in options if surname(text or "") == surname(candidate)]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                if matches:
                    option = matches[0]
                elif code == "WI":
                    option = cs.key("us.option", f"{key}:write-ins")
                    if option in cs.minted and option not in totals:
                        cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label=WRITE_IN)
                else:
                    first = plain(candidate.split()[0])
                    known = {
                        one
                        for one in people.get(plain(candidate)) or people.get(f"{first}|{surname(candidate)}", set())
                        if fits(one, candidate, year) and candidate not in ALONE
                    }
                    person = (
                        max(
                            known,
                            key=lambda one: (
                                ranks[one][0],
                                ranks[one][1],
                                -min(abs(other - year) for other in ranks[one][2]),
                            ),
                        )
                        if known
                        else cs.key(f"{SOURCE}.person", f"MO:{slug(candidate)}")
                    )
                    if person in cs.minted:
                        cs.add("person", id=person)
                        name(person, candidate)
                    else:
                        print(f"{key}: {candidate} is joined to {texts[person]}")
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="ticket" if office == "president" else "candidate",
                        party_id=parties[code],
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                    )
                if option in on_file and abs(on_file[option] - votes) > max(10, on_file[option] // 200):
                    print(f"{key}: {candidate} has {votes}, {on_file[option]} on file")
                totals[option] = totals.get(option, 0) + votes
                picked[label] = option
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[book],
                is_default=True,
                completeness=1,
            )
            if (day, office) in caveats:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=caveats[(day, office)])
            if conn.execute("select from result_set where id = %s", [result_set]).fetchone() is None:
                cells: dict[tuple[str, UUID], int] = {}
                for county, label, votes in lines.select("county", "label", "votes").iter_rows():
                    cells[(county, picked[label])] = cells.get((county, picked[label]), 0) + votes
                for (county, option), votes in cells.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[county],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            kept = [row for row in outcomes if row[0] in totals]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not kept:
                winner = max(totals, key=totals.__getitem__)
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
        return cs.write()
