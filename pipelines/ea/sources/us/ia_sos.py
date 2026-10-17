import csv
import io
import itertools
import json
import re
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw, scan
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ia_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Iowa Secretary of State"
TITLE = "Official canvass summary"
URL = "https://sos.iowa.gov/elections/pdf/results/90s/{}gencanv.pdf"
SC = "Scattering"
TOTAL = "TOTAL"
TABLES: dict[str, list[tuple[str, tuple[int, ...], tuple[str | None, ...]]]] = {
    "1990-11-06": [
        ("senate", (3, 4), ("Tom Harkin", "Tom Tauke", SC, TOTAL)),
        ("governor", (9, 10), ("Donald D. Avenson", "Terry E. Branstad", "Nan Bailey", SC, None, TOTAL, None)),
        ("secretary-of-state", (11, 12), ("Elaine Baxter", "Beverly Anderson", SC, TOTAL)),
        ("auditor", (13, 14), ("Beverly Dickerson", "Richard D. Johnson", SC, TOTAL)),
        ("treasurer", (15, 16), ("Michael L. Fitzgerald", "Burtwin L. Day", "Derrick P. Grimmer", SC, TOTAL)),
        (
            "secretary-of-agriculture",
            (17, 18),
            ("Dale M. Cochran", "Varel G. Bailey", "Richard A. Bychowski", SC, TOTAL),
        ),
        ("attorney-general", (19, 20), ("Bonnie Campbell", "Ed Kelly", SC, TOTAL)),
    ],
    "1994-11-08": [
        (
            "governor",
            (10, 11),
            (
                "Bonnie J. Campbell",
                "Terry E. Branstad",
                "Richard O'Dell Hughes",
                "Veronica Butler",
                "Carl Eric Olsen",
                "Michael Galati",
                SC,
                TOTAL,
                None,
                None,
            ),
        ),
        ("secretary-of-state", (12, 13), ("Anne Pedersen", "Paul Danny Pate", "Steven M. Druker", SC, TOTAL)),
        ("auditor", (14, 15), ("Dan Gray", "Richard D. Johnson", SC, TOTAL)),
        ("treasurer", (16, 17), ("Michael L. Fitzgerald", "Jay Irwin", "Elizabeth Koehler", SC, TOTAL)),
        (
            "secretary-of-agriculture",
            (18, 19),
            ("Dale M. Cochran", "Tom Cory", "Sue Atkinson", "Jim Schaefer", SC, TOTAL),
        ),
        ("attorney-general", (20, 21), ("Tom Miller", "Joe Gunderson", "Jay B. Marcus", SC, TOTAL)),
    ],
}
TEXT = {
    "president": (
        "Bob Dole",
        "Bill Clinton",
        "John Hagelin",
        "Ross Perot",
        "Howard Phillips",
        "Ralph Nader",
        "Harry Browne",
        "James Harris",
        SC,
    ),
    "senate": (
        "Jim Ross Lightfoot",
        "Tom Harkin",
        "Joe Sulentic",
        "Sue Atkinson",
        "Fred Gratzon",
        "Shirley E. Pena",
        SC,
    ),
}
PARTIES = {
    "democratic-party": (
        "Tom Harkin",
        "Donald D. Avenson",
        "Elaine Baxter",
        "Beverly Dickerson",
        "Michael L. Fitzgerald",
        "Dale M. Cochran",
        "Bonnie Campbell",
        "Bonnie J. Campbell",
        "Anne Pedersen",
        "Dan Gray",
        "Tom Miller",
    ),
    "republican-party": (
        "Tom Tauke",
        "Terry E. Branstad",
        "Beverly Anderson",
        "Richard D. Johnson",
        "Burtwin L. Day",
        "Varel G. Bailey",
        "Ed Kelly",
        "Paul Danny Pate",
        "Jay Irwin",
        "Tom Cory",
        "Joe Gunderson",
    ),
    "socialist-workers-party": ("Nan Bailey", "Michael Galati"),
    "IA:grassroots": ("Derrick P. Grimmer", "Richard A. Bychowski"),
    "natural-law-party": ("Veronica Butler", "Steven M. Druker", "Elizabeth Koehler", "Jim Schaefer", "Jay B. Marcus"),
    "libertarian-party": ("Carl Eric Olsen",),
}
PETITION = {"Richard O'Dell Hughes", "Sue Atkinson"}
READ: dict[tuple[str, str, str], tuple[int, ...]] = {
    ("1990-11-06", "senate", "Washington"): (3107, 3037, 1, 6145),
    ("1990-11-06", "senate", "Winnebago"): (2160, 2809, 1, 4970),
    ("1990-11-06", "governor", "Adair"): (1256, 2463, 7, 1, 3727),
    ("1990-11-06", "governor", "Appanoose"): (1880, 2753, 11, 0, 4644),
    ("1990-11-06", "governor", "Cass"): (1648, 4265, 27, 4, 5944),
    ("1990-11-06", "governor", "Crawford"): (2193, 3250, 19, 1, 5463),
    ("1990-11-06", "governor", "Jefferson"): (1928, 3728, 41, 11, 5708),
    ("1990-11-06", "governor", "Muscatine"): (3919, 7323, 33, 3, 11278),
    ("1990-11-06", "governor", "Plymouth"): (2160, 5087, 24, 0, 7271),
    ("1990-11-06", "governor", "Winneshiek"): (2334, 4965, 43, 0, 7342),
    ("1990-11-06", "governor", "Worth"): (1454, 1990, 27, 5, 3476),
    ("1990-11-06", "secretary-of-state", "Davis"): (1703, 1091, 0, 2794),
    ("1990-11-06", "secretary-of-state", "Washington"): (2507, 2978, 1, 5486),
    ("1990-11-06", "secretary-of-state", "Fayette"): (4023, 4238, 0, 8261),
    ("1990-11-06", "auditor", "Osceola"): (669, 1411, 0, 2080),
    ("1990-11-06", "auditor", "Wayne"): (1352, 1365, 0, 2717),
    ("1990-11-06", "treasurer", "Jackson"): (3431, 2174, 61, 0, 5666),
    ("1990-11-06", "treasurer", "Wapello"): (7420, 3232, 104, 1, 10757),
    ("1990-11-06", "treasurer", "Washington"): (2742, 2662, 66, 1, 5471),
    ("1990-11-06", "secretary-of-agriculture", "Dallas"): (6480, 3614, 292, 1, 10387),
    ("1990-11-06", "secretary-of-agriculture", "Decatur"): (1726, 924, 76, 1, 2727),
    ("1990-11-06", "secretary-of-agriculture", "Howard"): (2109, 1515, 107, 2, 3733),
    ("1990-11-06", "secretary-of-agriculture", "Lee"): (7346, 4147, 121, 1, 11615),
    ("1990-11-06", "secretary-of-agriculture", "Webster"): (9178, 3611, 90, 1, 12880),
    ("1990-11-06", "attorney-general", "Louisa"): (1669, 1911, 1, 3581),
    ("1990-11-06", "attorney-general", "Sioux"): (1708, 8122, 0, 9830),
    ("1990-11-06", "attorney-general", "Fremont"): (1280, 1654, 1, 2935),
    ("1990-11-06", "governor", "Johnson"): (16196, 15687, 351, 136, 32370),
    ("1990-11-06", "auditor", "Winnebago"): (1821, 2926, 0, 4747),
    ("1990-11-06", "secretary-of-state", "Union"): (2163, 2126, 0, 4289),
    ("1994-11-08", "governor", "Jasper"): (6175, 8021, 39, 28, 46, 8, 55, 14372),
    ("1994-11-08", "governor", "Johnson"): (20180, 13093, 176, 182, 210, 59, 83, 33983),
    ("1994-11-08", "governor", "Linn"): (27976, 34081, 104, 181, 341, 33, 299, 63015),
    ("1994-11-08", "governor", "Marion"): (3740, 6395, 46, 26, 20, 5, 16, 10248),
    ("1994-11-08", "governor", "Polk"): (61161, 67229, 655, 215, 335, 132, 577, 130304),
    ("1994-11-08", "governor", "Wayne"): (1091, 1719, 16, 1, 2, 0, 5, 2834),
    ("1994-11-08", "governor", "Woodbury"): (12824, 16471, 156, 42, 79, 27, 480, 30079),
    ("1994-11-08", "secretary-of-state", "Greene"): (1976, 1804, 102, 2, 3884),
    ("1994-11-08", "governor", "Ida"): (1084, 1810, 12, 2, 7, 2, 9, 2926),
    ("1994-11-08", "auditor", "Lee"): (5762, 5557, 5, 11324),
    ("1994-11-08", "auditor", "Ringgold"): (797, 1389, 0, 2186),
    ("1990-11-06", "attorney-general", "Hamilton"): (2897, 2651, 3, 5551),
    ("1990-11-06", "attorney-general", "Van Buren"): (1339, 1430, 4, 2773),
}


def fetch() -> None:
    for day in [*TABLES, "1996-11-05"]:
        raw.fetch(SOURCE, URL.format(day[:4]), suffix=".pdf")


def cells(
    pdf: Path, page: int, columns: int
) -> tuple[float, list[list[tuple[float, dict[int, float], dict[int, float]]]]]:
    cache = PARSED / "ocr" / f"{pdf.stem}-{page}.json"
    if cache.exists():
        saved = json.loads(cache.read_text())
        return saved["pitch"], [
            [
                (middle, {int(k): v for k, v in base.items()}, {int(k): v for k, v in extra.items()})
                for middle, base, extra in column
            ]
            for column in saved["columns"]
        ]
    words = scan.words(pdf, page, PARSED / "ocr" / f"{pdf.stem}-{page}.tsv")
    numeric = [[word for word in line if re.search(r"\d", word[0])] for line in scan.lines(words, scan.skew(words))]
    edges = sorted(word[1][2] for line in numeric if len(line) >= 2 for word in line)
    clusters: list[list[int]] = []
    for edge in edges:
        if clusters and edge - clusters[-1][-1] < 40:
            clusters[-1].append(edge)
        else:
            clusters.append([edge])
    count = sum(1 for line in numeric if len(line) >= 2)
    rights = [sorted(cluster)[len(cluster) // 2] for cluster in clusters if len(cluster) >= 0.3 * count]
    if len(rights) != columns:
        raise ValueError(f"{pdf.stem} page {page}: {len(rights)} columns, expected {columns}: {rights}")
    data = [
        line
        for line in numeric
        if len({index for word in line for index, right in enumerate(rights) if abs(word[1][2] - right) < 60}) >= 2
    ]
    centers = sorted((min(word[1][1] for word in line) + max(word[1][3] for word in line)) / 2 for line in data)
    pitch = sorted(b - a for a, b in itertools.pairwise(centers))[len(centers) // 2]
    first, final = centers[0] - 1.5 * pitch, centers[-1] + 1.5 * pitch
    width = max(word[1][2] - word[1][0] for line in data for word in line if abs(word[1][2] - rights[0]) < 60)
    bounds = [
        (rights[index - 1] + 45 if index else rights[0] - width - 60, right + 60) for index, right in enumerate(rights)
    ]
    entries: list[list[tuple[tuple[int, int, int, int], str]]] = []
    with tempfile.TemporaryDirectory() as folder:
        image = scan.rendered(pdf, page, Path(folder))
        picture = Image.open(image).convert("L")
        for left, right in bounds:
            crop = picture.crop((left, int(first), right, int(final)))
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            crop.save(buffer, "PNG")
            table = subprocess.run(
                ["tesseract", "stdin", "stdout", "--psm", "6", "-c", "tessedit_char_whitelist=0123456789,", "tsv"],
                input=buffer.getvalue(),
                capture_output=True,
                check=True,
            ).stdout.decode()
            found: list[list[int | str]] = []
            for record in csv.DictReader(io.StringIO(table), delimiter="\t", quoting=csv.QUOTE_NONE):
                if record["level"] != "5" or not re.search(r"\d", record["text"]):
                    continue
                x0 = left + int(record["left"]) // 2
                y0 = int(first) + int(record["top"]) // 2
                x1 = x0 + int(record["width"]) // 2
                y1 = y0 + int(record["height"]) // 2
                if found and abs((y0 + y1) / 2 - (int(found[-1][1]) + int(found[-1][3])) / 2) < pitch / 2:
                    previous = found[-1]
                    found[-1] = [
                        min(int(previous[0]), x0),
                        min(int(previous[1]), y0),
                        max(int(previous[2]), x1),
                        max(int(previous[3]), y1),
                        f"{previous[4]}{record['text']}",
                    ]
                else:
                    found.append([x0, y0, x1, y1, record["text"]])
            entries.append([((int(a), int(b), int(c), int(d)), str(text)) for a, b, c, d, text in found])
        boxes = [
            (max(left, box[0] - 30), box[1] - 4, min(right, box[2] + 20), box[3] + 4)
            for (left, right), column in zip(bounds, entries, strict=True)
            for box, _ in column
        ]
        texts = scan.read(image, [(box, True) for box in boxes])
    result: list[list[tuple[float, dict[int, float], dict[int, float]]]] = []
    position = 0
    for column in entries:
        found: list[tuple[float, dict[int, float], dict[int, float]]] = []
        for box, strip in column:
            options: dict[int, float] = {}
            for text in texts[position]:
                for value, weight in scan.numbers(text).items():
                    options[value] = options.get(value, 0) + weight
            position += 1
            extra = {value: float(weight) for value, weight in scan.numbers(strip).items()}
            for value, weight in options.items():
                extra[value] = extra.get(value, 0) + weight / 10
            found.append(((box[1] + box[3]) / 2, options or {0: 1}, extra or {0: 1}))
        result.append(found)
    cache.write_text(json.dumps({"pitch": pitch, "columns": result}))
    return pitch, result


def layout(
    pitch: float, found: list[list[tuple[float, dict[int, float], dict[int, float]]]], columns: tuple[str | None, ...]
) -> list[list[tuple[dict[int, float], dict[int, float]]]]:
    reference = [entry[0] for entry in found[0]]
    counted = [index for index, label in enumerate(columns) if label not in (None, TOTAL)]
    total = columns.index(TOTAL)

    def assign(column: int, start: float) -> list[tuple[dict[int, float], dict[int, float]] | None]:
        slots: list[tuple[float, tuple[dict[int, float], dict[int, float]]] | None] = [None] * len(reference)
        offset = start
        for middle, base, extra in found[column]:
            index = min(range(len(reference)), key=lambda one: abs(middle - offset - reference[one]))
            distance = abs(middle - offset - reference[index])
            if distance > pitch * 0.6:
                continue
            current = slots[index]
            if current is None or distance < current[0]:
                slots[index] = (distance, (base, extra))
            offset = 0.6 * offset + 0.4 * (middle - reference[index])
        return [slot[1] if slot else None for slot in slots]

    def score(grid: list[list[tuple[dict[int, float], dict[int, float]] | None]]) -> int:
        good = 0
        for number in range(len(reference)):
            cells = [grid[index][number] for index in [*counted, total]]
            if resolve([cell[0] if cell else {} for cell in cells], len(counted)) or resolve(
                [cell[1] if cell else {} for cell in cells], len(counted)
            ):
                good += 1
        return good

    grid = [assign(0, 0.0)] + [assign(column, 0.0) for column in range(1, len(found))]
    best = score(grid)
    for _ in range(2):
        for column in range(1, len(found)):
            for start in (-pitch, -pitch / 2, pitch / 2, pitch, 0.0):
                trial = [*grid[:column], assign(column, start), *grid[column + 1 :]]
                value = score(trial)
                if value > best:
                    best, grid = value, trial
    return [[grid[column][number] or ({}, {}) for column in range(len(found))] for number in range(len(reference))]


def resolve(row: list[dict[int, float]], total: int) -> list[int] | None:
    if row.count({}) == 1:
        index = row.index({})
        known = [max(cell, key=cell.__getitem__) if cell else 0 for cell in row]
        known[index] = known[total] - sum(known[:total]) if index != total else sum(known[:total])
        return known if known[index] >= 0 else None
    if {} in row:
        return None
    ranked = [sorted(cell, key=lambda value: -cell[value]) for cell in row]
    best = [cell[0] for cell in ranked]
    for changes in range(3):
        for picks in itertools.combinations(range(len(row)), changes):
            for values in itertools.product(*(ranked[index][1:] for index in picks)):
                trial = list(best)
                for index, value in zip(picks, values, strict=True):
                    trial[index] = value
                if sum(trial[:total]) == trial[total]:
                    return trial
    return None


def parse() -> None:
    (PARSED / "ocr").mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        counties = sorted(
            text
            for (text,) in conn.execute(
                """select n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '19%'"""
            ).fetchall()
        )
    counties.sort(key=lambda text: re.sub(r"[^a-z]", "", text.lower()))
    rows: list[dict[str, object]] = []
    for day, parts in TABLES.items():
        pdf = raw.cached(SOURCE, URL.format(day[:4])).path
        offices: dict[
            str, list[tuple[tuple[str | None, ...], list[list[dict[int, float]]], list[dict[int, float]]]]
        ] = {}
        for office, pages, columns in parts:
            read = [
                [cell for pair in row for cell in pair]
                for page in pages
                for row in layout(*cells(pdf, page, len(columns)), columns)
            ]
            body, printed = read[:-1], read[-1:]
            if len(body) != len(counties):
                raise ValueError(f"{day} {office} {pages}: {len(body)} rows")
            offices.setdefault(office, []).append((columns, body, printed[0]))
        for office, tables in offices.items():
            labels = [label for columns, _, _ in tables for label in columns]
            keep = [index for index, label in enumerate(labels) if label is not None]
            order = [index for index in keep if labels[index] != TOTAL] + [labels.index(TOTAL)]
            sums = [0] * len(order)
            for number, county in enumerate(counties):
                merged = [cell for _, body, _ in tables for cell in body[number]]
                base, extra = merged[0::2], merged[1::2]
                cell = (
                    list(READ.get((day, office, county), ()))
                    or resolve([base[index] for index in order], len(order) - 1)
                    or resolve([extra[index] for index in order], len(order) - 1)
                )
                if cell is None:
                    print(
                        day,
                        office,
                        county,
                        [sorted(extra[index], key=lambda v: -extra[index][v])[:3] for index in order],
                    )
                    continue
                for index, value in enumerate(cell):
                    sums[index] += value
                rows.extend(
                    {"day": day, "office": office, "county": county, "candidate": labels[index], "votes": value}
                    for index, value in zip(order[:-1], cell, strict=False)
                )
            printed = [cell for _, _, total in tables for cell in total[1::2]]
            for position, index in enumerate(order):
                if sums[position] not in printed[index]:
                    print(day, office, labels[index], "sums to", sums[position], "printed", sorted(printed[index]))
    keys = {re.sub(r"[^a-z]", "", county.lower()): county for county in counties}
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL.format(1996)).path), "-"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    middle = text.index("United Stateis Senator")
    chunks = {"president": text[:middle], "senate": text[middle : text.index("United States Representative", middle)]}
    for office, names in TEXT.items():
        found: dict[str, list[int]] = {}
        for line in chunks[office].splitlines():
            match = re.fullmatch(r"\s*([A-Z][A-Z'’ ]*?)[ .]*\s([\d,]+(?:\s+[\d,]+)+)\s*", line)
            if match is None:
                continue
            values = [int(value.replace(",", "")) for value in match[2].split()]
            if len(values) != len(names) + 1 or sum(values[:-1]) != values[-1]:
                raise ValueError(f"1996 {office}: {line}")
            found[re.sub(r"[^a-z]", "", match[1].lower())] = values
        printed = found.pop("totals")
        if sorted(found) != sorted(keys):
            raise ValueError(f"1996 {office}: {sorted(set(found) ^ set(keys))}")
        if [sum(column) for column in zip(*found.values(), strict=True)] != printed:
            raise ValueError(f"1996 {office}: columns do not match the totals")
        rows.extend(
            {"day": "1996-11-05", "office": office, "county": keys[key], "candidate": name, "votes": value}
            for key, values in found.items()
            for name, value in zip(names, values, strict=False)
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa's governor, U.S. Senate, and statewide offices by county 1990 and 1994 "
            "and president and U.S. Senate by county 1996, from the Secretary of State's canvass summaries",
            polity="US",
        )

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [word for word in text.split() if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}]
            return plain(words[-1]) if words else ""

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.iowa.gov/elections/results/",
            license="Public record",
        )
        for day in [*TABLES, "1996-11-05"]:
            cs.artifact(raw.cached(SOURCE, URL.format(day[:4])), source)
        counties: dict[str, UUID] = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '19%%'"""
            ).fetchall()
        }
        if len(counties) != 99:
            raise ValueError("Iowa needs 99 counties")
        people: dict[str, set[UUID]] = {}
        ranks: dict[UUID, tuple[bool, bool, int]] = {}
        for person, text, statewide, known, distance in conn.execute(
            """select cd.person_id, n.text, bool_or(x.value ~ ':IA:[a-z-]+(:special)?$'),
                      bool_or(exists (select from external_id w where w.entity_id = cd.person_id
                                      and w.scheme in ('wikidata', 'bioguide'))),
                      min(abs(left(x.value, 4)::int - 1993))
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest' and x.value ~ '^\\d{4}.*:IA:'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            if not text.split():
                continue
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
            ranks[person] = (statewide, known, -distance)
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {
            candidate: cs.key("us.party", party) for party, candidates in PARTIES.items() for candidate in candidates
        }
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':IA:'")
        )
        titles = {
            "secretary-of-state": "Secretary of State",
            "auditor": "Auditor of State",
            "treasurer": "Treasurer of State",
            "secretary-of-agriculture": "Secretary of Agriculture",
            "attorney-general": "Attorney General",
        }
        made: set[UUID] = set()
        for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
            key = f"{day}:IA:{office}"
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if key in contests:
                contest = contests[key]
                ((election, area),) = conn.execute(
                    "select election_id, district_area_id from contest where id = %s", [contest]
                ).fetchall()
            else:
                ((election, area),) = conn.execute(
                    "select election_id, district_area_id from contest where id = %s",
                    [contests[f"{day}:IA:governor"]],
                ).fetchall()
                office_id = cs.key("us.office", f"{office}:IA")
                if office_id in cs.minted:
                    raise ValueError(f"no office {office}")
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office_id,
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                cs.add(
                    "name",
                    entity_id=contest,
                    text=f"{day[:4]} Iowa {titles[office]} election",
                    lang="en",
                    kind="official",
                    is_preferred=True,
                )
                cs.add("slug", path=f"us/ia/{day}-general/{office}", entity_id=contest)
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket')""",
                [contest],
            ).fetchall()
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
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
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
            for candidate, votes in (
                lines.group_by("candidate", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                matches = [option for option, _, text in options if surname(text or "") == surname(candidate)]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                if candidate == SC:
                    option = cs.key("us.option", f"{key}:write_in:scattered")
                    if option in cs.minted:
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Scattered"
                        )
                elif matches:
                    option = matches[0]
                else:
                    known = people.get(plain(candidate)) or people.get(
                        f"{plain(candidate.split()[0])}|{surname(candidate)}", set()
                    )
                    person = (
                        max(known, key=lambda one: ranks.get(one, (False, False, -99)))
                        if known
                        else cs.key(f"{SOURCE}.person", f"IA:{slug(candidate)}")
                    )
                    if person in cs.minted and person not in made:
                        made.add(person)
                        people.setdefault(plain(candidate), set()).add(person)
                        people.setdefault(f"{plain(candidate.split()[0])}|{surname(candidate)}", set()).add(person)
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=candidate, lang="en", kind="official", is_preferred=True)
                    elif person not in made:
                        print(f"{key}: {candidate} matched an existing person")
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(candidate),
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=candidate in PETITION,
                    )
                if option in on_file and abs(on_file[option] - votes) > max(10, on_file[option] // 200):
                    print(f"{key}: {candidate} has {votes}, {on_file[option]} on file")
                totals[option] = votes
                picked[candidate] = option
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            for name, candidate, votes in lines.select("county", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[plain(name)],
                    option_id=picked[candidate],
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
                winner = max((option for option in totals if option != picked.get(SC)), key=totals.__getitem__)
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
        return cs.write()
