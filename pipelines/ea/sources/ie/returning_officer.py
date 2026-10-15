import html
import json
import re
import struct
import subprocess
import unicodedata
from datetime import date
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "ie.returning_officer"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
ELECTION = date(2024, 6, 7)
ACCESSION = date(1973, 1, 1)
REVISION = 1377449257
WIKIPEDIA = (
    f"https://en.wikipedia.org/w/api.php?action=parse&oldid={REVISION}&prop=wikitext%7Crevid&format=json"
    "&formatversion=2"
)
SHEETS = {
    "dublin": (
        "Dublin",
        "Dublin County Returning Officer",
        "https://dublincountyreturningofficer.com/images/2024-06/Results_Sheet.pdf",
        (700, 1000),
    ),
    "midlands-north-west": (
        "Midlands–North-West",
        "Galway Returning Officer",
        "https://galwayreturningofficer.com/images/June2024/MNW_Declaration_and_Result.pdf",
        (530, 792),
    ),
    "south": (
        "South",
        "Cork Returning Officer",
        "https://corkreturningofficer.com/images/2024-06/Result_Sheet_South.pdf",
        (320, 455),
    ),
}
REPAIRED = "midlands-north-west"
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
UNSIGNED = re.compile(r"^\d[\d,]*$")
ORDINALS = [
    "First",
    "Second",
    "Third",
    "Fourth",
    "Fifth",
    "Sixth",
    "Seventh",
    "Eighth",
    "Ninth",
    "Tenth",
    "Eleventh",
    "Twelfth",
    "Thirteenth",
    "Fourteenth",
    "Fifteenth",
    "Sixteenth",
    "Seventeenth",
    "Eighteenth",
    "Nineteenth",
    "Twentieth",
]
HEADER = {
    2: r"electorate\s+([\d,]+)",
    3: r"(?:votes cast\s+\([\d.]+%\)|total poll)\s+([\d,]+)",
    5: r"invalid ballot papers\s+([\d,]+)",
    4: r"valid poll\s+([\d,]+)",
}
EXHAUSTED = 21
DERIVED = 1
DIGITIZED = 8
PARTIES = {
    "FF": ("Fianna Fáil", "FF", "#66bb66"),
    "FG": ("Fine Gael", "FG", "#6699ff"),
    "SF": ("Sinn Féin", "SF", "#326760"),
    "LAB": ("Labour Party", "Lab", "#cc0000"),
    "GRN": ("Green Party", "GP", "#99cc33"),
    "SD": ("Social Democrats", "SD", "#752f8b"),
    "PBP": ("People Before Profit–Solidarity", "PBP–S", None),
    "ANT": ("Aontú", None, None),
    "INDI": ("Independent Ireland", None, None),
    "IFP": ("Irish Freedom Party", "IFP", None),
    "TNP": ("National Party", None, None),
    "IF": ("Ireland First", None, None),
    "TIP": ("The Irish People", None, None),
    "I4C": ("Independents 4 Change", "I4C", None),
}
LABELS = {
    "NON-P": "",
    "NON-P.": "",
    "NON PARTY": "",
    "FIANNA FÁIL": "FF",
    "FINE GAEL": "FG",
    "SINN FÉIN": "SF",
    "THE LABOUR PARTY": "LAB",
    "GREEN PARTY/COMHAONTAS GLAS": "GRN",
    "SOCIAL DEMOCRATS": "SD",
    "PEOPLE BEFORE PROFIT - SOLIDARITY": "PBP",
    "AONTÚ": "ANT",
    "INDEPENDENT IRELAND": "INDI",
    "IRISH FREEDOM PARTY (I.F.P.)": "IFP",
    "THE NATIONAL PARTY - AN PÁIRTÍ NÁISIÚNTA": "TNP",
    "IRELAND FIRST": "IF",
    "THE IRISH PEOPLE": "TIP",
    "INDEPENDENTS 4 CHANGE": "I4C",
}
SPELLINGS = {"BrÍd": "Bríd"}
METHODOLOGY = (
    "Ireland elects its members of the European Parliament by single transferable vote in multi-member "
    "constituencies. The quota is the Droop quota: valid votes divided by one more than the number of seats, plus "
    "one, with any fraction ignored. A candidate who reaches the quota is elected, and their surplus passes to the "
    "next preference. At the first count, every ballot is examined for a surplus. For a later surplus, only the last "
    "parcel received is passed on. When no surplus is left to distribute, the lowest candidate is eliminated and "
    "their ballots are transferred. Ballots with no further usable preference become non-transferable. The last "
    "seats can be filled without the quota once the continuing candidates equal the seats left."
)
MERGED = (
    "The returning officer's sheet prints Michelle Smith's votes in Michelle Gildernew's row for counts 1 to 8 and "
    "has no row for Smith. Smith's counts 1 to 8 come from Wikipedia's count table (revision 1377449257, citing RTÉ). "
    "Gildernew's counts 1 to 8 are the sheet's figures minus Smith's, and they match Wikipedia. Smith was eliminated "
    "at count 9."
)
ELECTORATE = (
    "The count sheet labels the total poll (valid plus invalid ballot papers) as the total electorate. The archive "
    "stores it as ballots cast. The sheet does not give the electorate, so the archive takes it from Wikipedia "
    "(revision 1377449257, citing RTÉ)."
)
WIKIPEDIA_NOTE = (
    "Used only to repair the Midlands–North-West count sheet: Michelle Smith's votes for counts 1 to 8 and the "
    "constituency's electorate."
)

type Word = tuple[float, float, float, float, str]


def fold(text: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


def fetch() -> None:
    for _, _, url, _ in SHEETS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, WIKIPEDIA, suffix=".json")


def parse() -> None:
    page = json.loads(raw.cached(SOURCE, WIKIPEDIA).path.read_text())["parse"]
    if page["revid"] != REVISION:
        raise ValueError(f"expected Wikipedia revision {REVISION}, got {page['revid']}")
    box = next(b for b in page["wikitext"].split("{{STV Election box begin")[1:] if "2024" in b[:300])
    wiki_rows: dict[str, tuple[str, dict[int, int]]] = {}
    for block in box.split("{{STV Election box candidate")[1:]:
        field = re.search(r"\|\s*candidate\s*=\s*([^\n]+)", block)
        if field is None:
            raise ValueError(f"Wikipedia count row without a candidate: {block[:80]}")
        person = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", re.sub(r"\{\{[^}]*\}\}|'''", "", field[1])).strip()
        wiki_rows[fold(person)] = (
            person,
            {int(n): int(v.replace(",", "")) for n, v in re.findall(r"\|\s*count(\d+)\s*=\s*'*([\d,]+)", block)},
        )
    electorate = re.search(r"\|\s*electorate\s*=\s*([\d,]+)", box)
    if electorate is None:
        raise ValueError("the Wikipedia count table has no electorate")

    PAGES.mkdir(parents=True, exist_ok=True)
    sheet_rows: list[dict[str, object]] = []
    candidate_rows: list[dict[str, object]] = []
    value_rows: list[dict[str, object]] = []
    page_rows: list[dict[str, object]] = []
    for code, (label, _, url, (elected_from, elected_to)) in SHEETS.items():
        pdf = raw.cached(SOURCE, url)
        pages: list[list[Word]] = []
        sizes: list[tuple[float, float]] = []
        for chunk in subprocess.run(
            ["pdftotext", "-bbox", str(pdf.path), "-"], check=True, capture_output=True, text=True
        ).stdout.split("<page ")[1:]:
            size = re.match(r'width="([\d.]+)" height="([\d.]+)"', chunk)
            if size is None:
                raise ValueError(f"{code}: page without a size")
            sizes.append((float(size[1]), float(size[2])))
            pages.append(
                [(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in WORD.findall(chunk)]
            )
        lines_of: list[list[tuple[float, list[Word]]]] = []
        for words in pages:
            lines: list[tuple[float, list[Word]]] = []
            for word in sorted(words, key=lambda w: (w[1], w[0])):
                if lines and abs(lines[-1][0] - word[1]) < 2.5:
                    lines[-1][1].append(word)
                else:
                    lines.append((word[1], [word]))
            lines_of.append([(y, sorted(ws)) for y, ws in lines])
        for number in range(1, len(pages) + 1):
            image = PAGES / f"{pdf.sha256}-{number}.png"
            if not image.exists():
                subprocess.run(
                    [
                        "pdftoppm",
                        "-r",
                        "100",
                        "-png",
                        "-f",
                        str(number),
                        "-l",
                        str(number),
                        "-singlefile",
                        str(pdf.path),
                        str(image.with_suffix("")),
                    ],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            page_rows.append(
                {
                    "sheet": code,
                    "page": number,
                    "image": f"pages/{SOURCE}/{image.name}",
                    "width": width,
                    "height": height,
                }
            )

        header: dict[int, tuple[int, int, Word]] = {}
        magnitude = quota = 0
        for page_number, lines in enumerate(lines_of, 1):
            for _, ws in lines:
                text = " ".join(w[4] for w in ws)
                for measure, pattern in HEADER.items():
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match and measure not in header:
                        header[measure] = (
                            int(match[1].replace(",", "")),
                            page_number,
                            next(w for w in ws if w[4] == match[1]),
                        )
                if not magnitude and (
                    match := re.search(r"(?:number of seats|no of positions:)\s+(\d+)", text, re.IGNORECASE)
                ):
                    magnitude = int(match[1])
                if not quota and (match := re.search(r"quota\s+([\d,]+)", text, re.IGNORECASE)):
                    quota = int(match[1].replace(",", ""))

        labels: list[str] = []
        totals: dict[tuple[int, int], tuple[int | None, int, Word]] = {}
        printed: dict[str, dict[int, int]] = {}
        all_words = [w for words in pages for w in words]
        if any(w[4] == "CANDIDATE(S)" for w in all_words):
            group_rows: list[int] = []
            for page_number, lines in enumerate(lines_of, 1):
                words = pages[page_number - 1]
                transfer = [w for w in words if w[4] == "Transfer"]
                if not transfer:
                    continue
                top = min(w[1] for w in transfer)
                columns = [
                    ((w[0] + w[2]) / 2, int(w[4])) for w in words if w[4].isdigit() and abs(w[1] - (top - 21)) < 5
                ]
                named = any(w[0] < 35 and w[1] > top and w[4].endswith(",") for w in words)
                rows: list[tuple[float, list[Word]]] = []
                for y, ws in lines:
                    cells = [w for w in ws if not named or w[2] > 170]
                    if y > top and cells and all(UNSIGNED.match(w[4]) or w[4] == "-" for w in cells):
                        rows.append((y, cells))
                if named:
                    group_rows = []
                    for y, _ in rows:
                        labels.append(
                            " ".join(
                                w[4]
                                for w in sorted(
                                    (w for w in words if w[2] < 170 and y - 14 <= w[1] <= y + 3),
                                    key=lambda w: (w[1], w[0]),
                                )
                            )
                        )
                        group_rows.append(len(labels) - 1)
                if len(rows) != len(group_rows):
                    raise ValueError(f"{code} page {page_number}: {len(rows)} rows for {len(group_rows)} candidates")
                for candidate, (_, cells) in zip(group_rows, rows, strict=True):
                    for word in cells:
                        count = min(columns, key=lambda c: abs(c[0] - (word[0] + word[2]) / 2))[1]
                        totals[candidate, count] = (
                            None if word[4] == "-" else int(word[4].replace(",", "")),
                            page_number,
                            word,
                        )
        elif any(w[4] == "NAMES" for w in all_words):
            lines = lines_of[0]
            columns = [((w[0] + w[2]) / 2, ORDINALS.index(w[4]) + 1) for w in pages[0] if w[4] in ORDINALS]
            top = next(w[1] for w in pages[0] if w[4] == "NAMES") + 10
            starts = [
                y
                for y, ws in lines
                if y > top and ws[0][0] < 60 and not UNSIGNED.match(ws[0][4]) and ws[0][4][0] not in "+-"
            ]
            for index, start in enumerate(starts):
                end = starts[index + 1] if index + 1 < len(starts) else 10_000.0
                row: dict[int, tuple[int | None, int, Word]] = {}
                caption = ""
                for line_y, line_ws in lines:
                    if not start - 2 <= line_y < end - 2:
                        continue
                    for word in line_ws:
                        numeric = bool(UNSIGNED.match(word[4])) and word[0] > 250
                        if line_y == start and not numeric:
                            caption += f" {word[4]}"
                        elif numeric or (line_y != start and word[4] == "-"):
                            count = min(columns, key=lambda c: abs(c[0] - (word[0] + word[2]) / 2))[1]
                            row[count] = (None if word[4] == "-" else int(word[4].replace(",", "")), 1, word)
                caption = caption.strip()
                if caption.startswith(("TOTAL", "Non transfer")):
                    printed[caption.split()[0]] = {c: v for c, (v, _, _) in row.items() if v is not None}
                    continue
                labels.append(caption)
                for count, cell in row.items():
                    totals[len(labels) - 1, count] = cell
        else:
            index_of: dict[str, int] = {}
            for page_number, lines in enumerate(lines_of, 1):
                words = pages[page_number - 1]
                counts = sorted((a[0], int(b[4])) for a, b in pairwise(words) if a[4] == "Count" and b[4].isdigit())
                if not counts:
                    continue
                slots = [(c, kind) for _, c in counts for kind in (("total",) if c == 1 else ("transfer", "total"))]
                limit = min((w[0] for w in words if w[4] == "Order"), default=10_000.0) - 5
                entries: list[tuple[list[Word], list[Word]]] = []
                sum_line: list[Word] = []
                for _, ws in lines:
                    cells = [w for w in ws if 285 <= w[0] < limit and re.match(r"^-?\d[\d,]*$", w[4])]
                    if (
                        ws[0][0] < 60
                        and cells
                        and ws[0][4] not in ("Non", "Effective")
                        and all(w[4] != "Count" for w in ws)
                    ):
                        entries.append((cells, ws))
                    elif entries and not sum_line and all(UNSIGNED.match(w[4]) for w in ws):
                        sum_line = ws
                clusters: list[list[float]] = []
                for edge in sorted(w[2] for cells, _ in entries for w in cells):
                    if clusters and edge - clusters[-1][-1] < 8:
                        clusters[-1].append(edge)
                    else:
                        clusters.append([edge])
                if len(clusters) != len(slots):
                    raise ValueError(f"{code} page {page_number}: {len(clusters)} columns for {len(slots)} slots")
                centers = [sum(c) / len(c) for c in clusters]
                for cells, ws in entries:
                    surname_words = [w[4] for w in ws if w[0] < 170]
                    printed_surname = " ".join(surname_words)
                    if printed_surname not in index_of:
                        while re.match(r"^[A-Z](\.?[A-Z])*\.?$", surname_words[-1]):
                            surname_words.pop()
                        forename = " ".join(w[4] for w in ws if 170 <= w[0] < 285)
                        labels.append(f"{' '.join(surname_words)}, {forename}")
                        index_of[printed_surname] = len(labels) - 1
                    candidate = index_of[printed_surname]
                    for word in cells:
                        count, kind = slots[min(range(len(centers)), key=lambda i: abs(centers[i] - word[2]))]
                        if kind == "total":
                            totals[candidate, count] = (int(word[4].replace(",", "")), page_number, word)
                for word in sum_line:
                    count = min(
                        (pair for pair in zip(centers, slots, strict=True) if pair[1][1] == "total"),
                        key=lambda pair: abs(pair[0] - word[2]),
                    )[1][0]
                    printed.setdefault("sums", {})[count] = int(word[4].replace(",", ""))

        rounds = max(count for _, count in totals)
        valid = header[4][0]
        if quota != valid // (magnitude + 1) + 1:
            raise ValueError(f"{code}: quota {quota} is not the Droop quota of {valid} for {magnitude} seats")
        series: dict[int, dict[int, int]] = {}
        for candidate in range(len(labels)):
            series[candidate] = {}
            for count in range(1, rounds + 1):
                value = totals.get((candidate, count), (None,))[0]
                series[candidate][count] = value or 0
                if not value:
                    break
        repaired: dict[tuple[int, int], int] = {}
        derived: set[tuple[int, int]] = set()
        merged = [
            (candidate, count)
            for candidate, row in series.items()
            for count in row
            if count > 1 and 0 < row[count] < row[count - 1] < quota
        ]
        if merged and (code != REPAIRED or len(merged) != 1):
            raise ValueError(f"{code}: unexpected merged rows {merged}")
        for candidate, count in merged:
            sheet_keys = {
                fold(f"{lbl.split(',')[1].split('(')[0].replace('Dr.', '')}{lbl.split(',')[0]}"): i
                for i, lbl in enumerate(labels)
            }
            absent = [
                (person, history)
                for key, (person, history) in wiki_rows.items()
                if key not in sheet_keys and count - 1 in history and count not in history
            ]
            if len(absent) != 1:
                raise ValueError(f"{code}: no single Wikipedia row explains the merged row of {labels[candidate]}")
            person, history = absent[0]
            labels.append(f"{person.split()[-1]}, {' '.join(person.split()[:-1])} (Non-P)")
            other = len(labels) - 1
            series[other] = {n: history[n] for n in range(1, count)} | {count: 0}
            for n in range(1, count + 1):
                repaired[other, n] = series[other][n]
            derived.add((other, count))
            for n in range(1, count):
                series[candidate][n] -= history[n]
                derived.add((candidate, n))
            own = next(r for key, (_, r) in wiki_rows.items() if sheet_keys.get(key) == candidate)
            if any(series[candidate][n] != own[n] for n in range(1, count)):
                raise ValueError(f"{code}: the repaired row of {labels[candidate]} does not match Wikipedia")
        relabeled = 2 in header and 5 in header and header[2][0] == header[4][0] + header[5][0]
        if relabeled:
            if code != REPAIRED:
                raise ValueError(f"{code}: the electorate equals the total poll")
            header[3] = header.pop(2)
        exhausted = {
            count: valid - sum(row[count] for row in series.values() if count in row) for count in range(1, rounds + 1)
        }
        failures = [
            f"printed {kind} at count {count}: {value} != {expected[count]}"
            for kind, expected in (
                ("TOTAL", dict.fromkeys(exhausted, valid)),
                ("Non", exhausted),
                ("sums", {count: valid - value for count, value in exhausted.items()}),
            )
            for count, value in printed.get(kind, {}).items()
            if value != expected[count]
        ]
        if 3 in header and header[3][0] != valid + header[5][0]:
            failures.append("ballots cast differ from valid plus invalid")
        if failures:
            raise ValueError(f"{code}: " + "; ".join(failures))

        first = next(lines for lines in lines_of if lines)
        heading = next(i for i, (_, ws) in enumerate(first) if any(w[4].upper() == "ELECTED" for w in ws))
        declared = [
            re.sub(r"\s*\(.*\)$", "", " ".join(names))
            for _, ws in first[heading + 1 :]
            if (names := [w[4] for w in ws if elected_from <= w[0] < elected_to])
        ][:magnitude]
        parsed: list[tuple[str, str, str | None]] = []
        for text in labels:
            match = re.match(r"^(?P<surname>[^,]+),\s*(?P<forename>[^(]+?)\s*(?:\((?P<party>.+)\))?$", text)
            if match is None:
                raise ValueError(f"{code}: cannot read the candidate {text}")
            party = LABELS.get((match["party"] or "").upper(), (match["party"] or "").upper())
            if party and party not in PARTIES:
                raise ValueError(f"{code}: unknown party {match['party']}")
            parsed.append((match["surname"].strip(), match["forename"].removeprefix("Dr. ").strip(), party or None))
        winners: dict[int, str] = {}
        for text in declared:
            matches = [
                i
                for i, (surname, forename, _) in enumerate(parsed)
                if fold(surname) in fold(text) and fold(forename.split()[0]) in fold(text)
            ]
            if len(matches) != 1:
                raise ValueError(f"{code}: the elected {text} matches {len(matches)} candidates")
            winners[matches[0]] = text
        counted: list[int] = []
        for count in range(1, rounds + 1):
            for candidate, path in sorted(series.items(), key=lambda item: -item[1].get(count, 0)):
                if path.get(count, 0) >= quota and candidate not in counted:
                    counted.append(candidate)
        continuing = sorted(
            (c for c, row in series.items() if row.get(rounds, 0) > 0 and c not in counted),
            key=lambda c: -series[c][rounds],
        )
        counted += continuing[: magnitude - len(counted)]
        if len(winners) != magnitude or set(winners) != set(counted):
            raise ValueError(f"{code}: the declared {declared} differ from the count")

        sheet_rows.append(
            {
                "sheet": code,
                "magnitude": magnitude,
                "quota": quota,
                "rounds": rounds,
                "merged": bool(merged),
                "relabeled": relabeled,
            }
        )
        for candidate, (surname, forename, party) in enumerate(parsed):
            last = max(series[candidate])
            candidate_rows.append(
                {
                    "sheet": code,
                    "candidate": candidate,
                    "label": labels[candidate],
                    "printed": (candidate, 1) not in repaired,
                    "name": winners.get(candidate)
                    or " ".join(SPELLINGS.get(token, token) for token in f"{forename} {surname}".split()),
                    "party": party,
                    "outcome": "elected"
                    if candidate in winners
                    else "eliminated"
                    if series[candidate][last] == 0
                    else "lost",
                }
            )
        cells: list[tuple[int | None, int, int, int, int | None, Word | None]] = [
            (None, measure, 0, value, page_number, word) for measure, (value, page_number, word) in header.items()
        ]
        if relabeled:
            cells.append((None, 2, 0, int(electorate[1].replace(",", "")), None, None))
        for candidate, path in series.items():
            for count, value in path.items():
                if (candidate, count) in repaired:
                    cells.append((candidate, 1, count, value, None, None))
                else:
                    _, page_number, word = totals[candidate, count]
                    cells.append((candidate, 1, count, value, page_number, word))
        for candidate, measure, count, value, page_number, located in cells:
            width, height = (1.0, 1.0) if page_number is None else sizes[page_number - 1]
            value_rows.append(
                {
                    "sheet": code,
                    "candidate": candidate,
                    "measure": measure,
                    "count_round": count,
                    "value": value,
                    "flags": (0 if located is None else DIGITIZED) | (DERIVED if (candidate, count) in derived else 0),
                    "page": page_number,
                    "box": None
                    if located is None
                    else [located[0] / width, located[1] / height, located[2] / width, located[3] / height],
                    "wiki": located is None,
                }
            )
        for count, value in exhausted.items():
            value_rows.append(
                {
                    "sheet": code,
                    "candidate": None,
                    "measure": EXHAUSTED,
                    "count_round": count,
                    "value": value,
                    "flags": DERIVED,
                    "page": None,
                    "box": None,
                    "wiki": False,
                }
            )

    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(sheet_rows).write_parquet(PARSED / "sheets.parquet")
    pl.DataFrame(candidate_rows).write_parquet(PARSED / "candidates.parquet")
    pl.DataFrame(
        value_rows,
        schema={
            "sheet": pl.String,
            "candidate": pl.Int32,
            "measure": pl.Int16,
            "count_round": pl.Int16,
            "value": pl.Int64,
            "flags": pl.Int16,
            "page": pl.Int32,
            "box": pl.List(pl.Float32),
            "wiki": pl.Boolean,
        },
    ).write_parquet(PARSED / "values.parquet")
    pl.DataFrame(page_rows).write_parquet(PARSED / "pages.parquet")


def emit() -> Path:
    sheets = pl.read_parquet(PARSED / "sheets.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    values = pl.read_parquet(PARSED / "values.parquet")
    pages = pl.read_parquet(PARSED / "pages.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2024 European Parliament election in Ireland, count by count from the returning officers' sheets",
            polity="IE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        for code, (label, publisher, url, _) in SHEETS.items():
            title = f"European Parliament election 2024, {label}: declaration and result sheet"
            sources[code] = cs.source(publisher, title)
            cs.add("source", id=sources[code], kind="official_body", publisher=publisher, title=title, url=url)
            artifacts[code] = cs.artifact(raw.cached(SOURCE, url), sources[code])
        wikipedia = cs.source("Wikipedia", "Midlands–North-West")
        cs.add(
            "source",
            id=wikipedia,
            kind="community",
            publisher="Wikipedia",
            title="Midlands–North-West",
            url=f"https://en.wikipedia.org/w/index.php?oldid={REVISION}",
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution="Wikipedia contributors, “Midlands–North-West”",
            notes=WIKIPEDIA_NOTE,
        )
        cs.artifact(raw.cached(SOURCE, WIKIPEDIA), wikipedia)
        for code, number, image, width, height in pages.iter_rows():
            cs.add(
                "source_page",
                id=cs.page(artifacts[code], number),
                artifact_id=artifacts[code],
                page_number=number,
                image_key=image,
                width=width,
                height=height,
            )

        ireland = cs.polity("IE")
        cs.add("polity", id=ireland, kind="sovereign_state", iso_code="IE")
        name(ireland, "Ireland")
        name(ireland, "Éire", "ga", "native")
        cs.add("slug", path="ie", entity_id=ireland)
        cs.add("external_id", entity_id=ireland, scheme="wikidata", value="Q27")
        union = cs.polity("EU")
        parliament = cs.key("eu.body", "european-parliament")
        umbrella = cs.key("eu.election", "2024-06-06:european-parliament")
        if {union, parliament, umbrella} & cs.minted:
            raise ValueError("publish eu.eurlex before Ireland")
        cs.add("polity_parent", polity_id=ireland, parent_id=union, valid_from=ACCESSION)
        for code, rank, noun, kind in (
            ("ie.nation", 0, "country", "administrative"),
            ("ie.ep_constituency", 1, "European Parliament constituency", "electoral_district"),
        ):
            cs.add("scheme", code=code, polity_id=ireland, kind=kind, rank=rank, noun=noun)
        nation = cs.area("ie.nation", "IE")
        cs.add("area", id=nation, scheme_code="ie.nation")
        cs.add("area_code", area_id=nation, code="IE", valid_from=None)
        name(nation, "Ireland")
        cs.add("polity_territory", polity_id=ireland, area_id=nation)

        party_ids: dict[str, UUID] = {}
        for code in sorted(set(candidates["party"].drop_nulls())):
            label, abbreviation, color = PARTIES[code]
            party_ids[code] = cs.key("ie.party", code)
            cs.add("party", id=party_ids[code], kind="party", polity_id=ireland)
            name(party_ids[code], label)
            if abbreviation is not None:
                name(party_ids[code], abbreviation, "en", "abbreviation")
            if color is not None:
                cs.add("party_color", party_id=party_ids[code], color=color, valid_from=None)
        system = cs.key("ie.electoral_system", "european-parliament")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=ireland,
            rules={"formula": "stv", "quota": "droop", "surplus": "last_parcel"},
            rules_version=1,
        )
        name(system, "European Parliament elections in Ireland")
        cs.add("note", entity_id=system, kind="methodology", body_md=METHODOLOGY)
        election = cs.key("ie.election", f"{ELECTION}:european-parliament")
        cs.add(
            "election",
            id=election,
            polity_id=ireland,
            parent_id=umbrella,
            kind="general",
            date_from=ELECTION,
            date_to=ELECTION,
            status="held",
        )
        name(election, "2024 European Parliament election in Ireland")
        path = f"ie/{ELECTION}-european-parliament"
        cs.add("slug", path=path, entity_id=election)

        units: dict[str, str] = {}
        result_sets: dict[str, str] = {}
        options: dict[str, str] = {}
        for code, magnitude, _, _, merged, relabeled in sheets.iter_rows():
            label = SHEETS[code][0]
            area = cs.area("ie.ep_constituency", code)
            cs.add("area", id=area, scheme_code="ie.ep_constituency")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=nation, valid_from=None)
            name(area, label)
            unit = cs.unit("ie.ep_constituency", code)
            units[code] = str(unit)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=ireland,
                scheme_code="ie.ep_constituency",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=ELECTION,
            )
            contest_key = f"{ELECTION}:european-parliament:{code}"
            contest = cs.key("ie.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=parliament,
                district_area_id=area,
                magnitude=magnitude,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"2024 European Parliament election in Ireland, {label}")
            cs.add("slug", path=f"{path}/{code}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=sources[code],
                is_default=True,
                completeness=1,
            )
            if result_set in cs.minted:
                result_sets[code] = str(result_set)
            if merged:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=MERGED)
            if relabeled:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=ELECTORATE)
            for candidate, label_text, printed, display, party, outcome in (
                candidates.filter(pl.col("sheet") == code).drop("sheet").iter_rows()
            ):
                option = cs.key("ie.option", f"{contest_key}:{fold(display)}")
                options[f"{code}:{candidate}"] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None if party is None else party_ids[party],
                    ballot_label=label_text,
                )
                person = cs.key(f"{SOURCE}.person", f"{ELECTION.year}:{fold(display)}")
                cs.add("person", id=person)
                name(person, display)
                cs.add(
                    "candidacy",
                    id=cs.key("ie.candidacy", f"{contest_key}:{fold(display)}"),
                    person_id=person,
                    option_id=option,
                    ballot_name=label_text if printed else None,
                    member_of_party_id=None if party is None else party_ids[party],
                    is_independent=party is None,
                )
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)

        located = values.filter(pl.col("sheet").is_in(list(result_sets)))
        keys = {
            "result_set_id": pl.col("sheet").replace_strict(result_sets),
            "unit_id": pl.col("sheet").replace_strict(units),
            "option_id": pl.format("{}:{}", "sheet", "candidate").replace_strict(options, default=None),
            "channel": pl.lit(0, pl.Int16),
            "measure": "measure",
            "count_round": "count_round",
        }
        cs.frame("result_value", located.select(**keys, flags="flags", value="value"))
        page_ids = {
            f"{code}:{number}": str(cs.page(artifacts[code], number))
            for code, number in pages.select("sheet", "page").iter_rows()
        }
        cs.frame(
            "value_locator",
            located.filter(pl.col("page").is_not_null() | pl.col("wiki")).select(
                **keys,
                page_id=pl.format("{}:{}", "sheet", "page").replace_strict(page_ids, default=None),
                source_id=pl.when(pl.col("wiki")).then(pl.lit(str(wikipedia))),
                x0=pl.col("box").list.get(0, null_on_oob=True),
                y0=pl.col("box").list.get(1, null_on_oob=True),
                x1=pl.col("box").list.get(2, null_on_oob=True),
                y1=pl.col("box").list.get(3, null_on_oob=True),
            ),
        )
        return cs.write()
