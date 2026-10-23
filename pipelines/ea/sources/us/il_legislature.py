import re
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import il_sbe, medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.il_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = il_sbe.PUBLISHER
YEARS = (1986, 1988)
TEXT = "https://archive.org/download/{item}/{item}_djvu.txt"
HEADING = re.compile(r"^([A-Z][A-Z -]*?) (LEGISLATIVE|REPRESENTATIVE) DISTRICT$")
CANDIDATE = re.compile(
    r"^(?:\(WIN\) )?(.+?) (DEM|REP|IND|ITR|LIB|SWP|AWP|CON|USP|SOC|PRO|CPU|NON|TAX|IPP|UNA|NPA|COM|SPP|CIT|HUM) ([\d,]+) [\d.]+[%?3]$"
)
LOOSE = re.compile(
    r"^(?:\(WIN\) )?(.+?) (DEM|REP|IND|ITR|LIB|SWP|AWP|CON|USP|SOC|PRO|CPU|NON|TAX|IPP|UNA|NPA|COM|SPP|CIT|HUM) [\d,^. ]+[%?3]?$"
)
STRAY = {"PLURALITY", "COUNTY", "STATE", "SENATOR", "REPRESENTATIVE", "IN", "THE", "GENERAL", "ASSEMBLY", "—", "-", "•"}
ROW = re.compile(r"^([A-Z][A-Z .'-]*?) ([\d,]+) [A-Z]{2,4} ((?:[\d,]+ ?)+)$")
PLURALITY = re.compile(r"^([\d,]+) [A-Z]{2,4}$")
NUMBER = re.compile(r"^[\d,]+$")
PARTY = re.compile(
    r"^(OEM|SOL|DEM|REP|IND|ITR|LIB|SWP|AWP|CON|USP|SOC|PRO|CPU|NON|TAX|IPP|UNA|NPA|WI|COM|SPP|CIT|HUM)$"
)
RUNNING = re.compile(r"^(STATE SENATOR|REPRESENTATIVE IN THE GENERAL ASSEMBLY|NOVEMBER \d+, ?\d{4})")
ENDING = re.compile(r"^(REGIONAL SUPERINTENDENT|[A-Z ,]+ [—-] NOVEMBER \d+, ?\d{4})")
UNITS = {
    "FIRST": 1,
    "SECOND": 2,
    "THIRD": 3,
    "FOURTH": 4,
    "FIFTH": 5,
    "SIXTH": 6,
    "SEVENTH": 7,
    "EIGHTH": 8,
    "NINTH": 9,
    "TENTH": 10,
    "ELEVENTH": 11,
    "TWELFTH": 12,
    "THIRTEENTH": 13,
    "FOURTEENTH": 14,
    "FIFTEENTH": 15,
    "SIXTEENTH": 16,
    "SEVENTEENTH": 17,
    "EIGHTEENTH": 18,
    "NINETEENTH": 19,
}
TENS = {
    "TWENTY": 20,
    "THIRTY": 30,
    "FORTY": 40,
    "FIFTY": 50,
    "SIXTY": 60,
    "SEVENTY": 70,
    "EIGHTY": 80,
    "NINETY": 90,
}
ALIASES = {"jodaviesa": "jodaviess", "wm": "will", "pvdaski": "pulaski"}
FIXES: dict[tuple[int, str, int], list[str]] = {
    (1986, "senate", 36): ["Henry", "Mercer", "Rock Island"],
    (1986, "senate", 54): [
        "Clay",
        "Crawford",
        "Edwards",
        "Effingham",
        "Hamilton",
        "Jasper",
        "Jefferson",
        "Lawrence",
        "Richland",
        "Wabash",
        "Wayne",
        "White",
    ],
    (1986, "senate", 59): [
        "Alexander",
        "Franklin",
        "Gallatin",
        "Hardin",
        "Johnson",
        "Massac",
        "Pope",
        "Pulaski",
        "Saline",
        "White",
        "Williamson",
    ],
    (1986, "house", 44): ["Cook", "DuPage"],
    (1986, "house", 86): ["Iroquois", "Kankakee"],
    (1986, "house", 96): ["Adams", "Brown", "Cass", "Schuyler"],
    (1986, "house", 100): ["Christian", "Montgomery", "Sangamon"],
    (1986, "house", 107): ["Clay", "Crawford", "Edwards", "Effingham", "Jasper", "Lawrence", "Richland"],
    (1986, "house", 110): ["Bond", "Clinton", "Madison", "St. Clair"],
    (1986, "house", 117): ["Franklin", "Williamson"],
    (1988, "house", 73): ["Carroll", "Henry", "Knox", "Stark", "Whiteside"],
    (1988, "house", 95): ["Fulton", "Hancock", "Henderson", "McDonough", "Schuyler"],
}
MANUAL: dict[tuple[int, str, int], tuple[list[str], list[tuple[str, list[int]]]]] = {
    (1986, "senate", 54): (
        ["O'Daniel", "Carpentier"],
        [
            ("Clay", [2884, 2538]),
            ("Crawford", [503, 399]),
            ("Edwards", [1711, 1594]),
            ("Effingham", [6138, 4452]),
            ("Hamilton", [2411, 1972]),
            ("Jasper", [2865, 1998]),
            ("Jefferson", [7057, 4517]),
            ("Lawrence", [3165, 2952]),
            ("Richland", [3619, 3022]),
            ("Wabash", [2455, 2190]),
            ("Wayne", [5116, 2885]),
            ("White", [3626, 2356]),
        ],
    ),
    (1986, "house", 110): (
        ["Stephens", "Slape"],
        [("Bond", [571, 617]), ("Clinton", [375, 304]), ("Madison", [7295, 4607]), ("St. Clair", [7209, 4242])],
    ),
    (1986, "senate", 38): (
        ["Welch", "Setchell"],
        [
            ("Bureau", [203, 220]),
            ("DeKalb", [9342, 10598]),
            ("Kendall", [733, 1789]),
            ("LaSalle", [17144, 13082]),
            ("Livingston", [341, 269]),
            ("Marshall", [684, 819]),
            ("Putnam", [1537, 1299]),
        ],
    ),
    (1986, "senate", 48): (
        ["Donahue", "Staff"],
        [
            ("Adams", [14997, 8174]),
            ("Brown", [1703, 885]),
            ("Cass", [3183, 2267]),
            ("Fulton", [3582, 3185]),
            ("Hancock", [4235, 3325]),
            ("Henderson", [2048, 1422]),
            ("McDonough", [5523, 4726]),
            ("Schuyler", [2315, 1594]),
        ],
    ),
    (1986, "house", 50): (
        ["Hensel", "Sanders"],
        [("Cook", [1270, 661]), ("DuPage", [7572, 2721]), ("Kane", [9935, 3419])],
    ),
    (1986, "house", 75): (
        ["Breslin"],
        [("Bureau", [337]), ("LaSalle", [21778]), ("Livingston", [454]), ("Marshall", [1033]), ("Putnam", [2327])],
    ),
    (1986, "house", 87): (
        ["Ewing", "Metz"],
        [
            ("Champaign", [2703, 1229]),
            ("DeWitt", [741, 240]),
            ("Ford", [3572, 1057]),
            ("Livingston", [8321, 2308]),
            ("McLean", [2155, 589]),
            ("Piatt", [3459, 1568]),
        ],
    ),
    (1986, "house", 89): (
        ["Ackerman"],
        [("Marshall", [2623]), ("McLean", [1748]), ("Stark", [205]), ("Tazewell", [9836]), ("Woodford", [7851])],
    ),
    (1986, "house", 90): (
        ["Olson"],
        [
            ("DeWitt", [2759]),
            ("Logan", [9092]),
            ("McLean", [1223]),
            ("Menard", [3533]),
            ("Sangamon", [7730]),
            ("Tazewell", [1543]),
        ],
    ),
    (1986, "house", 97): (
        ["Ryder", "Barnett"],
        [
            ("Calhoun", [1625, 1231]),
            ("Greene", [2913, 2193]),
            ("Jersey", [4274, 2982]),
            ("Morgan", [7517, 2572]),
            ("Pike", [4594, 2980]),
            ("Scott", [1659, 1010]),
        ],
    ),
    (1986, "house", 98): (
        ["Hannig", "Doyle"],
        [
            ("Bond", [2068, 2516]),
            ("Greene", [183, 273]),
            ("Macoupin", [9927, 8094]),
            ("Madison", [1655, 2011]),
            ("Montgomery", [2405, 1911]),
            ("Morgan", [1269, 1507]),
        ],
    ),
    (1986, "house", 102): (
        ["Tate"],
        [("Christian", [2903]), ("Cumberland", [3348]), ("Macon", [8688]), ("Moultrie", [3289]), ("Shelby", [6591])],
    ),
    (1986, "house", 108): (
        ["Hicks", "Wyant"],
        [
            ("Edwards", [1494, 880]),
            ("Hamilton", [2766, 1522]),
            ("Jefferson", [7825, 3688]),
            ("Wabash", [3189, 1413]),
            ("Wayne", [4435, 3317]),
            ("White", [4057, 1798]),
        ],
    ),
    (1986, "house", 118): (
        ["Phelps", "Lahr"],
        [
            ("Alexander", [2912, 1343]),
            ("Gallatin", [2655, 1020]),
            ("Hardin", [1534, 1139]),
            ("Johnson", [2009, 2346]),
            ("Massac", [2772, 3326]),
            ("Pope", [1096, 1125]),
            ("Pulaski", [1637, 1416]),
            ("Saline", [6783, 4233]),
            ("White", [1244, 420]),
            ("Williamson", [712, 561]),
        ],
    ),
}
CITATION = "Source: the State Board of Elections' Official Vote Cast at the General Election, county tables for the General Assembly."


def ordinal(text: str) -> int:
    words = text.replace("-", " ").split()
    total = 0
    if words[:2] == ["ONE", "HUNDRED"] or words[:1] == ["ONE"]:
        total = 100
        words = [word for word in words if word not in {"ONE", "HUNDRED", "AND"}]
        if not words:
            return 100
    if words == ["HUNDREDTH"]:
        return 100
    known = [*UNITS, *TENS, *(word[:-1] + "IETH" for word in TENS)]
    for word in words:
        word = word if word in known else next(iter(get_close_matches(word, known, 1, 0.8)), word)
        if word in UNITS:
            total += UNITS[word]
        elif word in TENS:
            total += TENS[word]
        elif word.endswith("IETH") and word[:-4] + "Y" in TENS:
            total += TENS[word[:-4] + "Y"]
        else:
            raise ValueError(f"unreadable ordinal {text}")
    return total


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, TEXT.format(item=il_sbe.ITEMS[year][0]), suffix=".txt")


def check(
    where: str,
    surnames: list[str | None],
    names: list[str],
    columns: list[list[int]],
    candidates: list[tuple[str, str, int | None]],
) -> list[tuple[str, str, int]]:
    labels = []
    for surname, column in zip(surnames, columns, strict=True):
        if surname is None:
            full = [name for name, _, total in candidates if total == sum(column)]
        else:
            full = [
                name
                for name, _, _ in candidates
                if SequenceMatcher(None, name.split()[-1].lower(), surname.lower()).ratio() >= 0.75
            ]
        if len(full) > 1:
            raise ValueError(f"{where}: {surname} matches {full}")
        if surname is None and not full:
            raise ValueError(f"{where}: a column of {sum(column)} matches no printed total")
        if full:
            total = next(total for name, _, total in candidates if name == full[0])
            if total is not None and sum(column) != total:
                raise ValueError(f"{where}: {surname} counties add up to {sum(column)} against {total}")
        labels.append(full[0] if full else surname)
    return [
        (names[row], labels[index], columns[index][row]) for row in range(len(names)) for index in range(len(columns))
    ]


def read_rows(
    where: str, candidates: list[tuple[str, str, int | None]], lines: list[str]
) -> list[tuple[str, str, int]]:
    surnames: list[str | None] = []
    names: list[str] = []
    columns: list[list[int]] = []
    for line in lines:
        if line.startswith("COUNTY PLURALITY"):
            surnames = list(line.removeprefix("COUNTY PLURALITY").split())
        elif match := ROW.match(line):
            values = [int(value.replace(",", "")) for value in match.group(3).split()]
            if len(values) != len(surnames):
                surnames = [None] * len(values)
            top = sorted(values, reverse=True)
            if int(match.group(2).replace(",", "")) != top[0] - (top[1] if len(top) > 1 else 0):
                raise ValueError(f"{where}: {line} plurality does not match its columns")
            names.append(match.group(1))
            columns = columns or [[] for _ in values]
            for column, value in zip(columns, values, strict=True):
                column.append(value)
    if not names:
        raise ValueError(f"{where}: no county rows")
    return check(where, surnames, names, columns, candidates)


def read_stream(
    where: str, candidates: list[tuple[str, str, int | None]], lines: list[str]
) -> list[tuple[str, str, int]]:
    begin = next((index for index, line in enumerate(lines) if line.upper().startswith("COUNTY")), None)
    if begin is None:
        raise ValueError(f"{where}: no county table")
    tokens = [
        part
        for line in lines[begin:]
        for part in (line.removeprefix("COUNTY PLURALITY").split() if line.startswith("COUNTY PLURALITY") else [line])
    ]
    entries: list[tuple[str, list[int]]] = []
    for token in tokens:
        text = re.sub(r"[ .,…]+$", "", token)
        if NUMBER.match(token) or PLURALITY.match(token):
            if entries:
                entries[-1][1].append(int(token.split()[0].replace(",", "")))
        elif (
            re.fullmatch(r"[A-Za-z .'-]+", text)
            and text.upper() not in STRAY
            and not all(PARTY.match(part) for part in re.sub(r"[^A-Za-z ]", " ", text).split())
        ):
            entries.append((text, []))
    split = next((index for index, (_, numbers) in enumerate(entries) if numbers), len(entries))
    surnames: list[str | None] = [word for word, _ in entries[:split]]
    rows = entries[split:]
    width = len(surnames)
    if len(rows) == 1:
        return []
    if not rows or width == 0:
        raise ValueError(f"{where}: no county rows in the token stream")
    names, columns = [], [[] for _ in surnames]
    for name, numbers in rows:
        if len(numbers) == width + 1:
            top = sorted(numbers[1:], reverse=True)
            if numbers[0] != top[0] - (top[1] if width > 1 else 0):
                raise ValueError(f"{where}: {name} plurality {numbers[0]} against {numbers[1:]}")
            numbers = numbers[1:]
        elif len(numbers) != width:
            raise ValueError(f"{where}: {name} has {numbers} for {width} candidates")
        names.append(name)
        for column, value in zip(columns, numbers, strict=True):
            column.append(value)
    return check(where, surnames, names, columns, candidates)


def read(
    year: int, chamber: str, district: int, candidates: list[tuple[str, str, int | None]], lines: list[str]
) -> list[tuple[str, str, int]]:
    where = f"{year} {chamber} {district}"
    if (year, chamber, district) in MANUAL:
        surnames, rows = MANUAL[(year, chamber, district)]
        return check(
            where,
            surnames,
            [name for name, _ in rows],
            [[votes[i] for _, votes in rows] for i in range(len(surnames))],
            candidates,
        )
    lines = [
        part
        for line in lines
        for part in (
            ["PLURALITY", *line.removeprefix("COUNTY PLURALITY").split()]
            if line.startswith("COUNTY PLURALITY")
            else [line]
        )
    ]
    plurality = [match.group(1) for line in lines if (match := PLURALITY.match(line))]
    counties = len(plurality)
    if counties == 0:
        raise ValueError(f"{where}: no plurality column")
    if counties == 1:
        return []
    numbers = [int(line.replace(",", "")) for line in lines if NUMBER.match(line)]
    words = [re.sub(r"[ .,…]+$", "", line) for line in lines if not NUMBER.match(line) and not PLURALITY.match(line)]
    words = [
        word
        for word in words
        if re.search(r"[A-Za-z]", word)
        and word.upper() not in STRAY
        and not all(PARTY.match(part) for part in re.sub(r"[^A-Za-z ]", " ", word).split())
    ]
    if len(numbers) % counties == 1:
        numbers = numbers[:-1]
    if len(numbers) % counties:
        raise ValueError(f"{where}: {len(numbers)} numbers for {counties} counties")
    width = len(numbers) // counties
    if (year, chamber, district) in FIXES:
        names = FIXES[(year, chamber, district)]
        surnames = [word for word in words if word.upper() not in {name.upper() for name in names}][:width]
    else:
        surnames, names = words[:width], words[width:]
    if len(names) != counties:
        raise ValueError(f"{where}: {len(names)} counties for {counties} rows: {names}")
    margins = [int(value.replace(",", "")) for value in plurality]
    for columns in (
        [numbers[index * counties : (index + 1) * counties] for index in range(width)],
        [numbers[index::width] for index in range(width)],
    ):
        rows = [sorted((column[row] for column in columns), reverse=True) for row in range(counties)]
        if all(margin == top[0] - (top[1] if width > 1 else 0) for margin, top in zip(margins, rows, strict=True)):
            break
    else:
        raise ValueError(f"{where}: no column order matches the printed pluralities {plurality}")
    return check(where, surnames, names, columns, candidates)


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["IL"]
    rows = []
    for year in YEARS:
        text = raw.cached(SOURCE, TEXT.format(item=il_sbe.ITEMS[year][0])).path.read_text()
        blocks: list[tuple[str, int, list[str]]] = []
        closed = False
        for line in (
            re.sub(r"(?<=\d) ?, ?(?=\d)", ",", re.sub(r"\s+", " ", line)).strip() for line in text.splitlines()
        ):
            if match := HEADING.match(line):
                closed = False
                blocks.append(("senate" if match.group(2) == "LEGISLATIVE" else "house", ordinal(match.group(1)), []))
            elif ENDING.match(line) and not RUNNING.match(line):
                closed = True
            elif blocks and line and not closed and not RUNNING.match(line):
                blocks[-1][2].append(line)
        failed = []
        for chamber, district, lines in blocks:
            candidates = [
                (
                    match.group(1),
                    match.group(2),
                    int(strict.group(3).replace(",", "")) if (strict := CANDIDATE.match(line)) else None,
                )
                for line in lines
                if (match := LOOSE.match(line))
            ]
            start = next((index for index, line in enumerate(lines) if line.upper() == "COUNTY"), None)
            table = lines[start + 1 :] if start is not None else []
            where = f"{year} {chamber} {district}"
            try:
                try:
                    cells = (
                        read_rows(where, candidates, lines)
                        if any(line.startswith("COUNTY PLURALITY") for line in lines)
                        else read(year, chamber, district, candidates, table)
                    )
                except ValueError:
                    if (year, chamber, district) in MANUAL:
                        raise
                    cells = read_stream(where, candidates, lines)
            except ValueError as error:
                failed.append(str(error))
                continue
            for county, candidate, votes in cells:
                rows.append((year, chamber, district, county, candidate, votes))
        for message in failed:
            print(message)
    frame = pl.DataFrame(rows, schema=["year", "chamber", "district", "county", "candidate", "votes"], orient="row")
    frame = frame.with_columns(
        code=pl.col("county")
        .str.to_lowercase()
        .str.replace_all(r"[^a-z]", "")
        .replace(ALIASES)
        .replace_strict(fips, default=None)
    )
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        raise ValueError(f"unmatched counties: {lost.select('year', 'chamber', 'district', 'county').rows()}")
    frame = frame.filter(pl.col("code").n_unique().over("year", "chamber", "district") > 1)
    days = {year: il_sbe.ITEMS[year][1] for year in YEARS}
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        state=pl.lit("IL"),
        day=pl.col("year").replace_strict(days, return_dtype=pl.Date),
        chamber=pl.concat_str(pl.col("chamber"), pl.col("district").cast(pl.String), pl.lit("g"), separator=":"),
        code=pl.col("code"),
        candidate=pl.col("candidate"),
        measure=pl.lit(1, pl.Int16),
        writein=pl.col("candidate").str.to_uppercase().str.starts_with("WRITE"),
        votes=pl.col("votes").cast(pl.Int64),
        year=pl.col("year").cast(pl.Int32),
        special=pl.lit(False),
        district=pl.lit(None, pl.Int16),
        kind=pl.lit("county"),
        source=pl.lit("IL"),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Illinois's State Senate and State House general elections by county, from the State Board of Elections' official vote books",
            polity="US-IL",
        )
        source = cs.source(PUBLISHER, il_sbe.TITLE.format(day=il_sbe.ITEMS[YEARS[0]][1]))
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title="Official Vote Cast at the General Election",
            url=il_sbe.VOLUMES[YEARS[0]][1],
            license=il_sbe.LICENSE,
        )
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, TEXT.format(item=il_sbe.ITEMS[year][0])), source)
        medsl.attach(cs, conn, frame, {"IL": source}, extend=True)
        return cs.write()
