import base64
import difflib
import itertools
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.mo_directory"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Missouri Secretary of State"
IMAGE = "https://cdm16795.contentdm.oclc.org/digital/iiif/bluebook/{}/full/full/0/default.jpg"
VOLUMES: dict[str, list[range]] = {
    "1879": [range(538, 540)],
    "1881": [range(77, 81)],
    "1883": [range(233, 235), range(251, 253)],
    "1885": [range(636, 648)],
    "1887": [range(1964, 1973), range(2029, 2038)],
}
PROMPTS = {
    "a": (
        "This is one page of a printed Missouri election-returns volume from the 1880s. Transcribe every table on the "
        'page exactly as printed. Reply with only a JSON object: {"title": the printed headings, "blocks": a list '
        'with one entry per table or district block, each {"heading": the block heading, "columns": the column '
        'headings in printed order, "rows": [[row label, then one entry per column]]}}. Use an integer for each '
        "printed number, null for a blank or dotted cell, and include the printed total, majority, and plurality rows "
        "as rows with their labels. Copy every digit as printed; do not add, correct, or infer numbers."
    ),
    "b": (
        "Read the table or tables on this scanned page of Missouri election returns, line by line from the top. "
        'Output only JSON of the form {"title": string, "blocks": [{"heading": string, "columns": [string], '
        '"rows": [[label, value, value, ...]]}]}. Each value is the integer printed in that column of that line, or '
        "null where the cell is blank or filled with dots. Keep total, majority, and plurality lines as rows. Never "
        "correct or compute a number; if a digit is unclear, still give your best reading of what is printed."
    ),
}


def pages() -> list[int]:
    return [item for ranges in VOLUMES.values() for span in ranges for item in span]


def fetch() -> None:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch

    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for item in pages():
        image = raw.fetch(SOURCE, IMAGE.format(item), suffix=".jpg")
        data = base64.b64encode(image.path.read_bytes()).decode()
        for variant, prompt in PROMPTS.items():
            params = MessageCreateParamsNonStreaming(
                model="claude-haiku-5-5",
                max_tokens=16000,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
            wanted[f"read:{item}:{variant}"] = (params, (4500 * price_in + 6000 * price_out) / 1e6)
    print(batch.ensure(SOURCE, "pages", wanted)[1])


def reading(item: int, variant: str) -> dict[str, Any]:
    blocks = json.loads(raw.cached(SOURCE, f"read:{item}:{variant}").path.read_text())["content"]
    text = "".join(str(block["text"]) for block in blocks if block.get("type") == "text")
    return json.loads(text[text.index("{") : text.rindex("}") + 1])


BOOK = "Official Directory of Missouri"
DAYS = {1878: "1878-11-05", 1880: "1880-11-02", 1882: "1882-11-07", 1884: "1884-11-04", 1886: "1886-11-02"}
PAGE_YEARS = {538: 1878, 539: 1878, 79: 1880, 80: 1880, 233: 1882, 234: 1882}
HOUSE_PAGES = [*PAGE_YEARS, *range(642, 648), *range(2029, 2038)]
ORDINALS = {
    word: number
    for number, word in enumerate(
        [
            "first",
            "second",
            "third",
            "fourth",
            "fifth",
            "sixth",
            "seventh",
            "eighth",
            "ninth",
            "tenth",
            "eleventh",
            "twelfth",
            "thirteenth",
            "fourteenth",
        ],
        1,
    )
}
Column = tuple[int, str, str, str, str]
STATEWIDE: list[tuple[tuple[int, ...], list[tuple[int, str, str, str, str] | None]]] = [
    (
        (640, 641),
        [
            (1880, "president", "hancock", "DEM", "Winfield Hancock"),
            None,
            (1884, "president", "cleveland", "DEM", "Grover Cleveland"),
            None,
            None,
            None,
        ],
    ),
    (
        (77, 78),
        [
            (1880, "president", "hancock", "DEM", "Winfield Hancock"),
            (1880, "president", "garfield", "REP", "James Garfield"),
            (1880, "president", "weaver", "GBK", "James Weaver"),
            (1880, "governor", "crittenden", "DEM", "Thomas T. Crittenden"),
            (1880, "governor", "dyer", "REP", "David P. Dyer"),
            (1880, "governor", "brown", "GBK", "Luman A. Brown"),
        ],
    ),
    (
        (251, 252),
        [
            (1880, "president", "hancock", "DEM", "Winfield Hancock"),
            (1880, "president", "garfield", "REP", "James Garfield"),
            (1880, "president", "weaver", "GBK", "James Weaver"),
            (1880, "governor", "crittenden", "DEM", "Thomas T. Crittenden"),
            (1880, "governor", "dyer", "REP", "David P. Dyer"),
            (1880, "governor", "brown", "GBK", "Luman A. Brown"),
        ],
    ),
    (
        (636, 637),
        [
            (1880, "president", "hancock", "DEM", "Winfield Hancock"),
            (1880, "president", "garfield", "REP", "James Garfield"),
            (1880, "president", "weaver", "GBK", "James Weaver"),
            (1884, "president", "cleveland", "DEM", "Grover Cleveland"),
            (1884, "president", "blaine", "REP", "James Blaine"),
            (1884, "president", "stjohn", "PRO", "John St. John"),
        ],
    ),
    (
        (1966, 1967, 1968),
        [
            (1880, "president", "hancock", "DEM", "Winfield Hancock"),
            (1880, "president", "garfield", "REP", "James Garfield"),
            (1880, "president", "weaver", "GBK", "James Weaver"),
            (1884, "president", "cleveland", "DEM", "Grover Cleveland"),
            (1884, "president", "blaine", "REP", "James Blaine"),
            (1884, "president", "stjohn", "PRO", "John St. John"),
        ],
    ),
    (
        (638, 639),
        [
            (1884, "governor", "marmaduke", "DEM", "John S. Marmaduke"),
            (1884, "governor", "ford", "REP", "Nicholas Ford"),
            (1884, "governor", "brooks", "PRO", "John A. Brooks"),
            (1884, "lieutenant-governor", "morehouse", "DEM", "Albert P. Morehouse"),
            (1884, "lieutenant-governor", "starkloff", "REP", "H. M. Starkloff"),
            (1884, "lieutenant-governor", "eshbaugh", "PRO", "Henry Eshbaugh"),
            (1880, "governor", "crittenden", "DEM", "Thomas T. Crittenden"),
        ],
    ),
]
PARTY_TOKENS = {
    "d": "DEM",
    "dem": "DEM",
    "r": "REP",
    "rad": "REP",
    "rgbk": "REP",
    "gbk": "GBK",
    "gtk": "GBK",
    "gk": "GBK",
    "gbkr": "GBK",
    "f": "FUS",
    "fusion": "FUS",
    "pro": "PRO",
    "p": "PRO",
    "soc": "SOC",
    "id": "IDEM",
    "gbkd": "IDEM",
    "gd": "IDEM",
    "indd": "IDEM",
    "ul": "UL",
    "ind": "IND",
    "i": "IND",
}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "GBK": "greenback-party",
    "PRO": "prohibition-party",
    "SOC": "MO:socialist",
    "FUS": "MO:fusion",
    "IDEM": "MO:independent-democrat",
    "UL": "union-labor",
    "IND": None,
}
SKIP_ROWS = re.compile(r"majority|plurality|scatter|over |^none$|^value$|^\d{4}\s", re.IGNORECASE)
TOTAL_ROWS = re.compile(
    r"^(total|totals|county totals?|county subtotal|subtotal|county sums.*|\(column sums\)|\(unlabeled total\))?$",
    re.IGNORECASE,
)
CITY = "29510"


@dataclass
class Observation:
    page: int
    reading: str
    rows: dict[str, int]
    check: int | None


def county_codes() -> dict[str, str]:
    with connect() as conn:
        found = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '29%%' and u.code <> '29510'"""
            ).fetchall()
        }
    if len(found) != 114:
        raise ValueError("Missouri needs 114 county names")
    found.update({"cgirardeau": found["capegirardeau"], "cpgirardeau": found["capegirardeau"]})
    found.update({"ironton": found["iron"], "calaway": found["callaway"], "clarke": found["clark"]})
    return found


def row_key(label: str, codes: dict[str, str]) -> str | None:
    text = re.sub(r"\s+", " ", label).strip()
    flat = re.sub(r"[^a-z]", "", text.lower())
    if re.search(r"\bwards?\b", text, re.IGNORECASE) or re.fullmatch(r"\d+(st|d|nd|rd|th)?", text):
        return CITY
    if re.search(r"city", text, re.IGNORECASE) and re.search(r"louis|l\.", text, re.IGNORECASE):
        return CITY
    if re.search(r"louis (co|county)|ferdinand|carondelet|bonhomme|meramec|central", text, re.IGNORECASE) and re.search(
        r"part|tp|township|,", text, re.IGNORECASE
    ):
        return "PART"
    flat = re.sub(r"(county|co)$", "", flat)
    flat = re.sub(r"^ste?(?=charles|clair|genevieve|francois|louis)", "st", flat)
    flat = re.sub(r"^stfrancois", "stfrancois", flat)
    if flat in {"stfrancois", "stfrancoiss"}:
        return codes["stfrancois"]
    return codes.get(flat) or codes.get(flat + "e") or codes.get(re.sub(r"^st", "ste", flat))


def clean_value(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def table_rows(
    rows: list[list[object]], width: int, codes: dict[str, str]
) -> tuple[dict[str, list[int | None]], list[int | None] | None]:
    data: dict[str, list[int | None]] = {}
    check: list[int | None] | None = None
    for row in rows:
        label = str(row[0] if row and row[0] is not None else "").strip()
        values = [clean_value(value) for value in row[1:]]
        values = (values + [None] * width)[:width]
        if (
            check is None
            and TOTAL_ROWS.fullmatch(re.sub(r"\s+", " ", label))
            and any(value is not None for value in values)
        ):
            check = values if sum(value is not None for value in values) > 1 or width == 1 else [None] * width
            continue
        if check is not None:
            continue
        if SKIP_ROWS.search(label) or not any(value is not None for value in values):
            continue
        key = row_key(label, codes)
        if key is None:
            raise ValueError(f"unknown row {label!r}")
        previous = data.get(key)
        data[key] = values if previous is None else [(a or 0) + (b or 0) for a, b in zip(previous, values, strict=True)]
    return data, check


def header(text: str) -> tuple[int | None, str, str, str | None]:
    text = re.sub(r"\s+", " ", str(text)).strip()
    year = None
    found = re.match(r"^\(?(188\d?)\)?[\s:]+", text)
    if found:
        year = int(found.group(1)) if len(found.group(1)) == 4 else 0
        text = text[found.end() :]
    found = re.search(r"\s*\((188\d)\)$", text)
    if found:
        year = int(found.group(1))
        text = text[: found.start()]
    party = None
    name = text
    for match in re.finditer(r"[,.\s]+", text):
        tail = re.sub(r"[^a-z]", "", text[match.end() :].lower())
        if tail in PARTY_TOKENS:
            party = PARTY_TOKENS[tail]
            name = text[: match.start()]
            break
    words = [
        word
        for word in re.sub(r"[^A-Za-z' .]", "", name).replace(".", ". ").split()
        if word and word.lower().strip(".") not in {"jr", "sr"}
    ]
    surname = re.sub(r"[^a-z]", "", (words[-1] if words else "").lower())
    label = re.sub(r"\s+", " ", re.sub(r"\.(?=\S)", ". ", name)).strip(" .,")
    return year, surname, label, party


def district_of(heading: object, title: object) -> int | None:
    for text in (heading, title):
        if text and "vacancy" not in str(text).lower() and "mayor" not in str(text).lower():
            for word in re.findall(r"[a-z]+", str(text).lower()):
                if word in ORDINALS:
                    return ORDINALS[word]
    return None


def pages_blocks(page: int, variant: str) -> list[dict[str, Any]]:
    return reading(page, variant)["blocks"]


def statewide_observations(codes: dict[str, str]) -> dict[tuple[int, str, str], list[Observation]]:
    found: dict[tuple[int, str, str], list[Observation]] = {}
    for pages, columns in STATEWIDE:
        for variant in "ab":
            rows: list[dict[str, int | None]] = [{} for _ in columns]
            checks: list[int | None] = [None for _ in columns]
            for page in pages:
                for block in pages_blocks(page, variant):
                    heading = " ".join(
                        str(block.get("heading") or "") + " " + " ".join(map(str, block["columns"]))
                    ).lower()
                    cells = [row for row in block["rows"] if isinstance(row, list)]
                    width = max((len(row) - 1 for row in cells), default=0)
                    if width != len(columns) or "votes" in heading:
                        continue
                    data, check = table_rows(cells, width, codes)
                    for key, values in data.items():
                        for index, value in enumerate(values):
                            if value is not None:
                                rows[index][key] = value
                    if check is not None:
                        checks = check
            for index, column in enumerate(columns):
                if column is None:
                    continue
                year, office, surname, _, _ = column
                found.setdefault((year, office, surname), []).append(
                    Observation(
                        pages[0], variant, {k: v for k, v in rows[index].items() if v is not None}, checks[index]
                    )
                )
    return found


def house_observations(
    codes: dict[str, str],
) -> tuple[
    dict[tuple[int, int, int], list[Observation]], dict[tuple[int, int, int], Counter[tuple[str, str, str | None]]]
]:
    found: dict[tuple[int, int, int], list[Observation]] = {}
    names: dict[tuple[int, int, int], Counter[tuple[str, str, str | None]]] = {}
    for page in HOUSE_PAGES:
        for variant in "ab":
            data = reading(page, variant)
            for block in data["blocks"]:
                district = district_of(block.get("heading"), data.get("title"))
                heading = str(block.get("heading") or "")
                if district is None or re.search(r"circuit|court|mayor|appeals", heading, re.IGNORECASE):
                    continue
                cells = [row for row in block["rows"] if isinstance(row, list) and row]
                width = max((len(row) - 1 for row in cells), default=0)
                columns = [str(column) for column in block["columns"]]
                if len(columns) < width or width == 0:
                    continue
                columns = columns[len(columns) - width :]
                try:
                    table, check = table_rows(cells, width, codes)
                except ValueError as error:
                    print(f"page {page}{variant} district {district}: {error}")
                    continue
                parsed = [header(column) for column in columns]
                block_year = re.search(r"\b(188\d)\b", heading)
                seen = {year for year, *_ in parsed if year}
                counters: Counter[int] = Counter()
                for index, (year, surname, label, party) in enumerate(parsed):
                    if year not in DAYS and year is not None:
                        year = 0
                    if year == 0:
                        year = next((each for each in (1884, 1886) if each not in seen), None)
                    year = year or (int(block_year.group(1)) if block_year else PAGE_YEARS.get(page))
                    if year is None or not surname:
                        continue
                    position = counters[year]
                    counters[year] += 1
                    key = (year, district, position)
                    found.setdefault(key, []).append(
                        Observation(
                            page,
                            variant,
                            {county: value for county, values in table.items() if (value := values[index]) is not None},
                            check[index] if check else None,
                        )
                    )
                    names.setdefault(key, Counter())[(surname, label, party)] += 1
    return found, names


def resolve(observations: list[Observation]) -> tuple[dict[str, int] | None, str, tuple[int, int] | None]:
    groups: dict[int | None, list[Observation]] = {}
    for each in observations:
        groups.setdefault(each.check, []).append(each)
    if len(groups) == 2 and None in groups:
        loose = groups.pop(None)
        next(iter(groups.values())).extend(loose)
    if len(groups) > 1:
        solved = {check: resolve(each) for check, each in groups.items()}
        widest = max(solved, key=lambda check: check or 0)
        fuller = solved[widest][0]
        if fuller is not None and all(
            rows is not None and all(fuller.get(key) == value for key, value in rows.items() if key != "PART")
            for rows, _, _ in solved.values()
        ):
            return solved[widest]
        return None, f"printed totals disagree {sorted(check or 0 for check in groups)}", None
    keys = sorted({key for each in observations for key in each.rows})
    checks = {each.check for each in observations if each.check is not None}
    count = len(observations)
    options: dict[str, list[int]] = {}
    majority: dict[str, int] = {}
    for key in keys:
        tally = Counter(each.rows.get(key, 0) for each in observations)
        options[key] = sorted(tally)
        (value, votes), *_ = tally.most_common(1)
        if votes * 2 > count:
            majority[key] = value
    if len(checks) > 1:
        return None, f"printed totals disagree {sorted(checks)}", None
    if not checks:
        return None, "no printed total", None
    (check,) = checks
    if len(majority) == len(keys) and sum(majority.values()) == check:
        return majority, "", None
    disputed = [key for key in keys if len(options[key]) > 1]
    if len(disputed) <= 14:
        solutions = []
        for picks in itertools.product(*(options[key] for key in disputed)):
            chosen = {key: options[key][0] for key in keys}
            chosen.update(dict(zip(disputed, picks, strict=True)))
            if sum(chosen.values()) == check:
                solutions.append(chosen)
        if len(solutions) == 1:
            return solutions[0], "", None
    unanimous = all(len(options[key]) == 1 for key in keys)
    if (
        len(majority) == len(keys)
        and abs(sum(majority.values()) - check)
        <= max(10, check // (100 if unanimous and count >= 4 else 500 if unanimous else 200))
        and (count >= 4 or unanimous)
    ):
        return majority, "", (check, sum(majority.values()))
    return (
        None,
        f"sum {sum(majority.values()) if len(majority) == len(keys) else '?'} does not reach printed {check}",
        None,
    )


FAILED: list[str] = []


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    codes = county_codes()
    columns: dict[tuple[str, str], list[tuple[str, str, str, list[Observation]]]] = {}
    for (year, office, surname), observations in statewide_observations(codes).items():
        label = next(
            col for _, cols in STATEWIDE for col in cols if col is not None and col[:3] == (year, office, surname)
        )
        columns.setdefault((DAYS[year], office), []).append((label[4], surname, label[3], observations))
    houses, names = house_observations(codes)
    for (year, district, _), observations in sorted(houses.items()):
        surname, label, party = names[(year, district, _)].most_common(1)[0][0]
        columns.setdefault((DAYS[year], f"house:{district}"), []).append((label, surname, party or "IND", observations))
    lines: list[dict[str, object]] = []
    notes: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    for (day, office), candidates in sorted(columns.items()):
        solved = []
        for label, surname, party, observations in candidates:
            rows, why, gap = resolve(observations)
            total = max((each.check or 0 for each in observations), default=0)
            solved.append((label, surname, party, rows, why, gap, total))
        lost = sum(total for *_, rows, _, _, total in solved if rows is None)
        if lost * 10 > sum(total for *_, total in solved) or all(rows is None for *_, rows, _, _, _ in solved):
            for label, *_, rows, why, _, _ in solved:
                if rows is None:
                    failed.append({"day": day, "office": office, "label": label, "why": why, "printed": 0})
            print(
                f"{day} {office}: left out ({[(label, why) for label, _, _, rows, why, _, _ in solved if rows is None]})"
            )
            continue
        for label, surname, party, rows, why, gap, total in solved:
            if rows is None:
                notes.append(
                    {
                        "day": day,
                        "office": office,
                        "note": f"The county lines for {label} are not loaded: {total:,} votes statewide.",
                    }
                )
                failed.append({"day": day, "office": office, "label": label, "why": why, "printed": total})
                continue
            if gap:
                notes.append(
                    {
                        "day": day,
                        "office": office,
                        "note": f"The Directory's printed total for {label} is {gap[0]:,}; its county figures sum to {gap[1]:,}.",
                    }
                )
            partial = rows.get("PART", 0)
            if partial:
                notes.append(
                    {
                        "day": day,
                        "office": office,
                        "note": f"{partial:,} votes for {label} were cast in parts of St. Louis County that the Directory prints by township; they are left out of the county lines.",
                    }
                )
            for county, votes in rows.items():
                if county != "PART":
                    lines.append(
                        {
                            "day": day,
                            "office": office,
                            "county": county,
                            "label": label,
                            "match": surname,
                            "party": party,
                            "votes": votes,
                            "printed": total,
                        }
                    )
    schema = {
        "day": pl.String,
        "office": pl.String,
        "county": pl.String,
        "label": pl.String,
        "match": pl.String,
        "party": pl.String,
        "votes": pl.Int64,
        "printed": pl.Int64,
    }
    frame = pl.DataFrame(lines, schema=schema)
    frame.write_parquet(PARSED / "lines.parquet")
    pl.DataFrame(notes, schema={"day": pl.String, "office": pl.String, "note": pl.String}).write_parquet(
        PARSED / "caveats.parquet"
    )
    pl.DataFrame(
        failed,
        schema={"day": pl.String, "office": pl.String, "label": pl.String, "why": pl.String, "printed": pl.Int64},
    ).write_parquet(PARSED / "failed.parquet")
    print(
        frame.group_by("day", "office")
        .agg(pl.col("label").n_unique().alias("candidates"))
        .sort("day", "office")
        .to_pandas()
        .to_string()
        if False
        else frame.select("day", "office").unique().height,
        "contests",
    )


def surname_of(text: str) -> str:
    words = [
        word
        for word in re.sub(r"[^a-z ]", "", re.sub(r"\([^)]*\)", " ", text.lower())).split()
        if word not in {"jr", "sr", "ii", "iii"}
    ]
    return words[-1] if words else ""


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    failed = pl.read_parquet(PARSED / "failed.parquet")
    notes: dict[tuple[str, str], list[str]] = {}
    for day, office, note in pl.read_parquet(PARSED / "caveats.parquet").iter_rows():
        notes.setdefault((day, office), []).append(note)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Missouri's president, governor, lieutenant governor, and U.S. House elections of 1878 to 1886 by county from the Official Directory",
            polity="US",
        )
        book = cs.source(PUBLISHER, BOOK)
        cs.add(
            "source",
            id=book,
            kind="official_body",
            publisher=PUBLISHER,
            title=BOOK,
            url="https://mdh.contentdm.oclc.org/digital/collection/bluebook",
            license="Public record",
        )
        for item in pages():
            cs.artifact(raw.cached(SOURCE, IMAGE.format(item)), book)
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '29%%'"
            ).fetchall()
        )
        parties = {code: cs.key("us.party", slug) if slug else None for code, slug in PARTIES.items()}
        if cs.minted & set(parties.values()) - {None}:
            raise ValueError("the parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':MO:'")
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
        for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
            key = f"{day}:MO:{office}"
            if key not in contests:
                raise ValueError(f"{key}: no contest")
            contest = contests[key]
            result_set = cs.key(f"{SOURCE}.result_set", key)
            ((election_id,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            options = conn.execute(
                """select id, ballot_label, party_id, kind from contest_option
                   where contest_id = %s and parent_option_id is null""",
                [contest],
            ).fetchall()
            old = conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall()
            for row in old:
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
            printed = 0
            for label, match, party, votes, total in (
                lines.group_by("label", "match", "party", maintain_order=True)
                .agg(pl.col("votes").sum(), pl.col("printed").first())
                .iter_rows()
            ):
                printed += total
                code = party
                if match == "scattering":
                    found = [option for option, _, _, kind in options if kind == "others"]
                    if len(found) != 1:
                        option = cs.key("us.option", f"{key}:others")
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="others", ballot_label="Scattering"
                        )
                    else:
                        option = found[0]
                    picked[label] = option
                    totals[option] = totals.get(option, 0) + votes
                    continue
                matches = [
                    option for option, text, _, kind in options if kind != "others" and surname_of(text or "") == match
                ]
                if not matches:
                    close = difflib.get_close_matches(
                        match, [surname_of(text or "") for _, text, _, kind in options if kind != "others"], 1, 0.85
                    )
                    matches = [option for option, text, _, _ in options if close and surname_of(text or "") == close[0]]
                if len(matches) > 1:
                    matches = [
                        option for option, _, owner, _ in options if option in matches and owner == parties[code]
                    ]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {label}")
                if matches:
                    option = matches[0]
                else:
                    person = cs.key(f"{SOURCE}.person", f"{key}:{match}")
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=label, lang="en", kind="official", is_preferred=True)
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="ticket" if office == "president" else "candidate",
                        party_id=parties[code],
                        ballot_label=label,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                    )
                if option in on_file and abs(on_file[option] - votes) > max(10, on_file[option] // 100):
                    print(f"{key}: {label} has {votes}, {on_file[option]} on file")
                picked[label] = option
                totals[option] = totals.get(option, 0) + votes
            if len(set(picked.values())) != len(picked):
                raise ValueError(f"{key}: two lines share an option")
            loaded = int(lines.group_by("label").agg(pl.col("votes").sum())["votes"].sum())
            lost = int(failed.filter((pl.col("day") == day) & (pl.col("office") == office))["printed"].sum())
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=book,
                is_default=True,
                completeness=round(min(1, loaded / (printed + lost)), 4),
                supersedes_id=old[0][0] if old and old[0][0] != result_set else None,
            )
            if (day, office) in notes:
                prior = conn.execute(
                    "select id from note where entity_id = %s and kind = 'caveat'", [result_set]
                ).fetchone()
                cs.add(
                    "note",
                    **({"id": prior[0]} if prior else {}),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=" ".join(notes[(day, office)]),
                )
            if conn.execute("select from result_set where id = %s", [result_set]).fetchone() is None:
                for county, label, votes in lines.select("county", "label", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[county],
                        option_id=picked[label],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            top = max((option for option in totals if option != picked.get("Scattering")), key=totals.__getitem__)
            kept = [
                (option, kind, seats)
                for option, kind, seats in outcomes
                if kind not in {"elected", "won"} or option == top
            ]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not any(kind in {"elected", "won"} for _, kind, _ in kept):
                cs.add("outcome", result_set_id=result_set, option_id=top, kind="elected", seats=1)
        return cs.write()
