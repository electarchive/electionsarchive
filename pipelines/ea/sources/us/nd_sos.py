import re
import subprocess
from datetime import date
from html import unescape
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nd_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = (
    "https://vip.sos.nd.gov/pdfs/Abstracts%20by%20Year/1990%20through%201998%20Statewide%20Election%20Results/"
    "{}/General%20Election%20{}.pdf"
)
HISTORY = "https://sos.nd.gov/sites/default/files/documents/elections/election-results-pdfs/{}.pdf"
PUBLISHER = "North Dakota Secretary of State"
TITLE = "Official abstracts of votes cast at the general elections"
FILES = {
    1950: "1950-11-07-general",
    1954: "1954-11-02-general",
    1958: "1958-11-04-general",
    1960: "1960-11-08-general",
    1962: "1962-11-06-general",
    1970: "1970-11-03-general",
    1974: "1974-11-05-general",
    1978: "1978-11-07-general",
    1980: "1980-11-04-general",
    1982: "1982-11-02-general",
    1984: "1984-11-06-general",
    1986: "1986-11-04-general",
    1988: "1988-11-08-general",
}
DAYS = {
    1992: date(1992, 11, 3),
    1994: date(1994, 11, 8),
    1996: date(1996, 11, 5),
    1998: date(1998, 11, 3),
    1950: date(1950, 11, 7),
    1954: date(1954, 11, 2),
    1958: date(1958, 11, 4),
    1960: date(1960, 11, 8),
    1962: date(1962, 11, 6),
    1970: date(1970, 11, 3),
    1974: date(1974, 11, 5),
    1978: date(1978, 11, 7),
    1980: date(1980, 11, 4),
    1982: date(1982, 11, 2),
    1984: date(1984, 11, 6),
    1986: date(1986, 11, 4),
    1988: date(1988, 11, 8),
}
COLUMNS: dict[int, tuple[int, dict[int, tuple[str, str, str | None]]]] = {
    1992: (
        1,
        {
            1: ("president", "Bill Clinton", "DEM"),
            2: ("president", "George Bush", "REP"),
            3: ("president", "Ross Perot", "IND"),
            4: ("president", "Lyndon H. LaRouche", "IND"),
            5: ("president", "Andre Marrou", "IND"),
            6: ("president", "John Hagelin", "IND"),
            7: ("president", "James Warren", "IND"),
            8: ("president", "Lenora B. Fulani", "IND"),
            9: ("president", "Earl F. Dodge", "OTH"),
            10: ("senate", "Byron L. Dorgan", "DEM"),
            11: ("senate", "Steve Sydness", "REP"),
            12: ("senate", "Tom Asbridge", "IND"),
            17: ("governor", "Nicholas Spaeth", "DEM"),
            18: ("governor", "Edward T. Schafer", "REP"),
            19: ("governor", "Harley McLain", "IND"),
            20: ("governor", "Michael O. DuPaul", "IND"),
            21: ("secretary-of-state", "Jim Kusler", "DEM"),
            22: ("secretary-of-state", "Alvin A. Jaeger", "REP"),
            23: ("auditor", "Mike Puklich", "DEM"),
            24: ("auditor", 'Robert "Bob" Peterson', "REP"),
            25: ("treasurer", "Kathi Gilmore", "DEM"),
            26: ("treasurer", "Claus H. Lembke", "REP"),
            27: ("attorney-general", "Heidi Heitkamp", "DEM"),
            28: ("attorney-general", 'Warren "Duke" Albrecht', "REP"),
            29: ("insurance-commissioner", "Glenn Pomeroy", "DEM"),
            30: ("insurance-commissioner", "Kent Olson", "REP"),
            31: ("insurance-commissioner", "Jan Shelver", "IND"),
            32: ("commissioner-of-agriculture", "Sarah Vogel", "DEM"),
            33: ("commissioner-of-agriculture", "Bill Bowman", "REP"),
            34: ("commissioner-of-agriculture", "Andy Heinze", "IND"),
            35: ("public-service-commissioner", "Julie Erjavec", "DEM"),
            36: ("public-service-commissioner", "Leo M. Reinbold", "REP"),
            37: ("tax-commissioner", 'Robert E. "Bob" Hanson', "DEM"),
            38: ("tax-commissioner", "Rod Backman", "REP"),
        },
    ),
    1994: (
        0,
        {
            1: ("senate", "Ben Clayburgh", "REP"),
            2: ("senate", "Kent Conrad", "DEM"),
            6: ("public-service-commissioner", "Ward K. Johnson", "REP"),
            7: ("public-service-commissioner", "Bruce Hagen", "DEM"),
            8: ("public-service-commissioner:special", "Susan E. Wefald", "REP"),
            9: ("public-service-commissioner:special", "Julie Hill", "DEM"),
            10: ("commissioner-of-labor", "Craig M. Hagen", None),
            11: ("commissioner-of-labor", "Gary Holm", None),
        },
    ),
    1996: (
        0,
        {
            2: ("president", "Bill Clinton", "DEM"),
            3: ("president", "Bob Dole", "REP"),
            4: ("president", "Harry Browne", "LIB"),
            5: ("president", "John Hagelin", "NAT"),
            6: ("president", "Ross Perot", "REF"),
            7: ("president", "Howard Phillips", "IND"),
            11: ("governor", "Lee Kaldor", "DEM"),
            12: ("governor", "Edward T. Schafer", "REP"),
            13: ("secretary-of-state", "Shelley Seeberg", "DEM"),
            14: ("secretary-of-state", 'Alvin A. "Al" Jaeger', "REP"),
            15: ("auditor", "Shirley Dykshoorn", "DEM"),
            16: ("auditor", "Robert R. (Bob) Peterson", "REP"),
            17: ("treasurer", "Kathi Gilmore", "DEM"),
            18: ("treasurer", "Randy A. Schobinger", "REP"),
            19: ("attorney-general", "Heidi Heitkamp", "DEM"),
            20: ("attorney-general", "Ward K. Johnson", "REP"),
            21: ("insurance-commissioner", "Glenn Pomeroy", "DEM"),
            22: ("insurance-commissioner", "C. H. Lembke", "REP"),
            23: ("insurance-commissioner", "Jan Shelver", "IND"),
            24: ("commissioner-of-agriculture", "Roger Johnson", "DEM"),
            25: ("commissioner-of-agriculture", "Dina Butcher", "REP"),
            26: ("public-service-commissioner", "Steve Tomac", "DEM"),
            27: ("public-service-commissioner", "Susan Wefald", "REP"),
            28: ("tax-commissioner", "Bob Hanson", "DEM"),
            29: ("tax-commissioner", "Rick Clayburgh", "REP"),
        },
    ),
    1950: (
        0,
        {
            10: ("lieutenant-governor", "Ray Schnell", "REP"),
            11: ("lieutenant-governor", "Milton K. Higgins", "DEM"),
            12: ("secretary-of-state", "Thomas Hall", "REP"),
            13: ("secretary-of-state", "Ruth B. Herzberg", "DEM"),
            14: ("auditor", "Berta E. Baker", "REP"),
            15: ("treasurer", "Albert Jacobson", "REP"),
            16: ("treasurer", "Loretta Gray", "DEM"),
            17: ("attorney-general", "E. T. Christianson", "REP"),
            18: ("attorney-general", "Richard Boulger", "DEM"),
            19: ("insurance-commissioner", "A. J. Jensen", "REP"),
            20: ("insurance-commissioner", "Henry W. Anderberg", "DEM"),
            21: ("commissioner-of-agriculture", "Math Dahl", "REP"),
            22: ("commissioner-of-agriculture", "Fred Port", "DEM"),
            23: ("public-service-commissioner", "E. H. Brant", "REP"),
            24: ("public-service-commissioner", "Norman B. Jenson", "DEM"),
        },
    ),
    1954: (
        0,
        {
            8: ("lieutenant-governor", "C. P. Dahl", "REP"),
            9: ("lieutenant-governor", "S. B. Hocking", "DEM"),
            10: ("secretary-of-state", "Ben Meier", "REP"),
            11: ("secretary-of-state", "B. Earle Robinson", "DEM"),
            12: ("auditor", "Berta E. Baker", "REP"),
            13: ("auditor", "S. J. Christopherson", "DEM"),
            14: ("treasurer", "Albert Jacobson", "REP"),
            15: ("treasurer", "Ethel Knudsen", "DEM"),
            16: ("attorney-general", "Leslie R. Burgum", "REP"),
            17: ("attorney-general", "S. E. Halpern", "DEM"),
            18: ("insurance-commissioner", "A. J. Jensen", "REP"),
            19: ("insurance-commissioner", "H. N. Jenson", "DEM"),
            20: ("commissioner-of-agriculture", "Math Dahl", "REP"),
            21: ("commissioner-of-agriculture", "William L. Guy", "DEM"),
            22: ("public-service-commissioner", "Anson J. Anderson", "REP"),
            23: ("public-service-commissioner", "Lawrence Knoke", "DEM"),
        },
    ),
    1958: (
        0,
        {
            12: ("lieutenant-governor", "C. P. Dahl", "REP"),
            13: ("lieutenant-governor", "Lavern Schoeder", "DEM"),
            18: ("treasurer", "John R. Erickson", "REP"),
            19: ("treasurer", "Ray Thompson", "DEM"),
        },
    ),
    1960: (
        0,
        {
            12: ("lieutenant-governor", "Orville W. Hagen", "REP"),
            13: ("lieutenant-governor", "Lavern Schoeder", "DEM"),
            14: ("secretary-of-state", "Ben Meier", "REP"),
            15: ("secretary-of-state", "Lloyd B. Omdahl", "DEM"),
            16: ("auditor", "Curtis Olson", "REP"),
            17: ("auditor", "Weldon Haugen", "DEM"),
            18: ("treasurer", "John R. Erickson", "REP"),
            19: ("treasurer", "Albert Heide", "DEM"),
            20: ("attorney-general", "Leslie R. Burgum", "REP"),
            21: ("attorney-general", "Charles Murphy", "DEM"),
            22: ("insurance-commissioner", "A. J. Jensen", "REP"),
            23: ("insurance-commissioner", "Don Sloan", "DEM"),
            24: ("commissioner-of-agriculture", "Math Dahl", "REP"),
            25: ("commissioner-of-agriculture", "Bruce Hagen", "DEM"),
            26: ("public-service-commissioner", "Richard J. Thompson", "REP"),
            27: ("public-service-commissioner", "Ralph Dewing", "DEM"),
        },
    ),
    1962: (
        0,
        {
            10: ("lieutenant-governor", "Frank A. Wenstrom", "REP"),
            11: ("lieutenant-governor", "Leonell W. (Lee) Fraase", "DEM"),
            12: ("secretary-of-state", "Ben Meier", "REP"),
            13: ("secretary-of-state", "Larry Schneider", "DEM"),
            14: ("auditor", "Curtis Olson", "REP"),
            15: ("auditor", "Florence Swenson", "DEM"),
            16: ("treasurer", "Phil Hoghaug", "REP"),
            17: ("treasurer", "Elmer Strand", "DEM"),
            18: ("attorney-general", "Helgi Johanneson", "REP"),
            19: ("attorney-general", "Charles Tighe", "DEM"),
            20: ("insurance-commissioner", "Frank Albers", "REP"),
            21: ("insurance-commissioner", "Hans Walker, Jr.", "DEM"),
            22: ("commissioner-of-agriculture", "Math Dahl", "REP"),
            23: ("commissioner-of-agriculture", "Harold R. Hanson", "DEM"),
            24: ("public-service-commissioner", "Ben J. Wolf", "REP"),
            25: ("public-service-commissioner", "E. Odin Sjaastad", "DEM"),
        },
    ),
    1970: (
        0,
        {
            9: ("public-service-commissioner", "Earl Redlin", "REP"),
            10: ("public-service-commissioner", "Bruce Hagen", "DEM"),
            12: ("commissioner-of-labor", "Orville W. Hagen", None),
            13: ("commissioner-of-labor", "Daryl E. Thompson", None),
        },
    ),
    1974: (
        0,
        {
            6: ("commissioner-of-labor", "Orville W. Hagen", None),
        },
    ),
    1982: (
        0,
        {
            8: ("public-service-commissioner", "Bruce Hagen", "DEM"),
            9: ("public-service-commissioner", "Dale Sandstrom", "REP"),
            18: ("commissioner-of-labor", "Orville W. Hagen", None),
            19: ("commissioner-of-labor", "Byron Knutson", None),
        },
    ),
    1984: (
        0,
        {
            16: ("secretary-of-state", "Claire Edwin Blomquist", "IND"),
            17: ("secretary-of-state", "Bryon Knutson", None),
            18: ("secretary-of-state", "Ben Meier", "REP"),
            19: ("auditor", 'M. K. "Heidi" Heitkamp', "DEM"),
            20: ("auditor", 'Robert "Bob" Peterson', "REP"),
            21: ("treasurer", "Bob Hanson", "DEM"),
            22: ("treasurer", "John Steven Lesmeister", "REP"),
            23: ("attorney-general", 'Nicholas "Nick" Spaeth', "DEM"),
            24: ("attorney-general", "Robert Wefald", "REP"),
            25: ("insurance-commissioner", "Diane Alm", "REP"),
            26: ("insurance-commissioner", "Jan Gerhart-Shelver", "IND"),
            27: ("insurance-commissioner", "Earl Pomeroy", "DEM"),
            28: ("commissioner-of-agriculture", "Kent Jones", "REP"),
            29: ("commissioner-of-agriculture", "Bruce W. Larson", "DEM"),
            30: ("public-service-commissioner", 'S. F. "Buckshot" Hoffner', "DEM"),
            31: ("public-service-commissioner", "Dale Sandstrom", "REP"),
        },
    ),
    1986: (
        0,
        {
            8: ("public-service-commissioner", "Leo M. Reinbold", "REP"),
            9: ("public-service-commissioner", "Jim Kusler", "DEM"),
            30: ("commissioner-of-labor", 'Orville W. "Ike" Hagen', None),
            31: ("commissioner-of-labor", "Byron Knutson", None),
        },
    ),
    1978: (
        0,
        {
            6: ("public-service-commissioner", 'Richard "Dick" Elkin', "REP"),
            7: ("public-service-commissioner", 'Robert E. "Bob" Hanson', "DEM"),
            11: ("commissioner-of-labor", "Orville W. Hagen", None),
        },
    ),
    1980: (
        0,
        {
            23: ("secretary-of-state", "Ben Meier", "REP"),
            24: ("secretary-of-state", "Bob Black", "DEM"),
            25: ("auditor", 'Robert "Bob" Peterson', "REP"),
            26: ("auditor", 'Austin "Jim" Engel', "DEM"),
            27: ("treasurer", "John Steven Lesmeister", "REP"),
            28: ("treasurer", "Bob Hanson", "DEM"),
            29: ("attorney-general", 'Robert "Bob" Wefald', "REP"),
            30: ("attorney-general", "Alice K. Olson", "DEM"),
            31: ("insurance-commissioner", 'J. O. "Bud" Wigen', "REP"),
            32: ("insurance-commissioner", "Byron Knutson", None),
            33: ("commissioner-of-agriculture", "Kent Jones", "REP"),
            34: ("commissioner-of-agriculture", 'S. F. "Buckshot" Hoffner', "DEM"),
            35: ("public-service-commissioner", "Leo Reinbold", "REP"),
            36: ("public-service-commissioner", "Rick Maixner", "DEM"),
        },
    ),
    1988: (
        0,
        {
            18: ("auditor", "Steve Pederson", "DEM"),
            19: ("auditor", 'Robert "Bob" Peterson', "REP"),
            20: ("treasurer", 'Robert E. "Bob" Hanson', "DEM"),
            21: ("treasurer", "Steve Easton", "REP"),
            22: ("attorney-general", 'Nicholas "Nick" Spaeth', "DEM"),
            23: ("attorney-general", "John Gosbee", "REP"),
            24: ("insurance-commissioner", "Earl R. Pomeroy", "DEM"),
            25: ("insurance-commissioner", "Margaret Bushee", "REP"),
            26: ("commissioner-of-agriculture", "Sarah Vogel", "DEM"),
            27: ("commissioner-of-agriculture", "Keith Bjerke", "REP"),
            28: ("commissioner-of-agriculture", "Kent Jones", "IND"),
            29: ("commissioner-of-agriculture", "Harley McLain", "IND"),
            30: ("public-service-commissioner", "Bruce Hagen", "DEM"),
            31: ("public-service-commissioner", "Leslie Volochenko", "REP"),
        },
    ),
    1998: (
        1,
        {
            2: ("senate", "Donna Nalewaja", "REP"),
            3: ("senate", "Byron L. Dorgan", "DEM"),
            4: ("senate", "Harley McLain", "REF"),
            8: ("public-service-commissioner", "Leo M. Reinbold", "REP"),
            9: ("public-service-commissioner", "Vern Thompson", "DEM"),
        },
    ),
}
NUMBER = r"[\dIl][\dIl,.?]*['`’:;]?"
ERRATA = {(1950, "A. J. Jensen")}
TYPOS = {"mclntosh": "mcintosh"}
ALONE = {"Roger Johnson"}
TITLES = {
    "lieutenant-governor": "Lieutenant Governor",
    "secretary-of-state": "Secretary of State",
    "auditor": "State Auditor",
    "treasurer": "State Treasurer",
    "attorney-general": "Attorney General",
    "insurance-commissioner": "Insurance Commissioner",
    "commissioner-of-agriculture": "Agriculture Commissioner",
    "public-service-commissioner": "Public Service Commissioner",
    "tax-commissioner": "Tax Commissioner",
    "commissioner-of-labor": "Labor Commissioner",
}


def url(year: int) -> str:
    if year in FILES:
        return HISTORY.format(FILES[year])
    return URL.format(year, DAYS[year].strftime("%m-%d-%Y"))


def fetch() -> None:
    for year in DAYS:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def parse() -> None:
    with connect() as conn:
        codes = {
            plain(text): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '38%'"""
            ).fetchall()
        }
    if len(codes) != 53:
        raise ValueError("North Dakota needs 53 counties")
    rows: list[dict[str, object]] = []
    for year, (index, columns) in COLUMNS.items():
        html = subprocess.run(
            ["pdftotext", "-bbox", str(raw.cached(SOURCE, url(year)).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        words = [
            (float(a), float(b), float(c), float(d), unescape(t))
            for a, b, c, d, t in re.findall(
                r'xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)<',
                html.split("<page ")[1:][index],
            )
        ]
        first = next(w for w in words if w[4].upper() == "ADAMS" and w[0] < 300)
        edge = (
            -20
            + min(w[0] for w in words if abs(w[1] - first[1]) < 3 and w[0] > first[2] and re.fullmatch(NUMBER, w[4]))
            if year in FILES
            else 125
        )
        names = sorted(
            (w for w in words if w[0] < edge and re.match(r"[A-Za-z]", w[4]) and w[3] - w[1] < 12),
            key=lambda w: w[1],
        )
        start = next(i for i, w in enumerate(names) if w[4].upper() == "ADAMS")
        lines: list[list] = []
        for w in names[start:]:
            middle = (w[1] + w[3]) / 2
            if lines and abs(lines[-1][0] - middle) < 2:
                lines[-1][1] += w[4]
                continue
            lines.append([middle, w[4]])
            if w[4].upper().rstrip(".") == "TOTAL":
                break
        keys = [TYPOS.get(plain(text), plain(text)) for _, text in lines[:-1]]
        if sorted(keys) != sorted(codes):
            raise ValueError(f"{year}: counties {sorted(set(keys) ^ set(codes))}")
        numbers = [w for w in words if re.fullmatch(NUMBER, w[4]) and w[0] >= edge and w[3] - w[1] <= 12]

        def nearest(w: tuple) -> int:
            middle = (w[1] + w[3]) / 2
            return min(range(len(lines)), key=lambda i: abs(lines[i][0] - middle))

        found = [(nearest(w), w) for w in numbers if abs(lines[nearest(w)][0] - (w[1] + w[3]) / 2) <= 3.6]
        totals = sorted(w for line, w in found if line == len(lines) - 1)

        def column_of(w: tuple) -> int:
            middle = (w[0] + w[2]) / 2
            return min(
                range(len(totals)),
                key=lambda i: (
                    0
                    if totals[i][0] - 2 <= middle <= totals[i][2] + 2
                    else abs((totals[i][0] + totals[i][2]) / 2 - middle)
                ),
            )

        def value(text: str) -> int:
            return int(re.sub(r"[Il]", "1", text).replace(",", "").replace(".", "").replace("?", "0").rstrip("'`’:;"))

        cells: dict[tuple[int, int], int] = {}
        unreadable: set[tuple[int, int]] = set()
        if year in FILES:
            bycolumn: dict[int, list[tuple]] = {}
            for w in numbers:
                bycolumn.setdefault(column_of(w), []).append(w)
            for column, members in bycolumn.items():
                members.sort(key=lambda w: w[1])
                ys = [(w[1] + w[3]) / 2 for w in members]
                shifts = sorted(y - lines[nearest(w)][0] for y, w in zip(ys, members, strict=True))
                shift = shifts[len(shifts) // 2]
                rows_y = [line[0] + shift for line in lines]
                inf = float("inf")
                cost = [[inf] * (len(rows_y) + 1) for _ in range(len(ys) + 1)]
                back: dict[tuple[int, int], tuple[int, int, int | None]] = {}
                cost[0][0] = 0.0
                for i in range(len(ys) + 1):
                    for j in range(len(rows_y) + 1):
                        if cost[i][j] == inf:
                            continue
                        options = []
                        if i < len(ys):
                            options.append((i + 1, j, cost[i][j] + 8.0, None))
                        if j < len(rows_y):
                            options.append((i, j + 1, cost[i][j] + 3.0, None))
                        if i < len(ys) and j < len(rows_y):
                            options.append((i + 1, j + 1, cost[i][j] + abs(ys[i] - rows_y[j]), j))
                        for ni, nj, c, matched in options:
                            if c < cost[ni][nj]:
                                cost[ni][nj] = c
                                back[(ni, nj)] = (i, j, matched)
                i, j = len(ys), len(rows_y)
                while (i, j) in back:
                    pi, pj, matched = back[(i, j)]
                    if matched is not None:
                        cells[(matched, column)] = value(members[pi][4])
                        if "?" in members[pi][4] or re.search(r",\d{1,2}$", members[pi][4]):
                            unreadable.add((matched, column))
                    i, j = pi, pj
        else:
            for line, w in found:
                column = column_of(w)
                if (line, column) in cells and column in columns:
                    raise ValueError(f"{year}: two numbers in {keys[line]} column {column}")
                cells[(line, column)] = value(w[4])
        for column, (office, candidate, code) in columns.items():
            printed = cells[(len(keys), column)]
            gaps = [line for line in range(len(keys)) if (line, column) in unreadable]
            if len(gaps) == 1:
                cells[(gaps[0], column)] = 0
                cells[(gaps[0], column)] = printed - sum(cells.get((line, column), 0) for line in range(len(keys)))
            values = [cells.get((line, column), 0) for line in range(len(keys))]
            if sum(values) != printed:
                if (
                    year not in FILES
                    or (year, candidate) not in ERRATA
                    and abs(sum(values) - printed) > max(20, printed // 100)
                ):
                    raise ValueError(f"{year} {candidate}: {sum(values)}, printed {printed}")
                print(f"{year} {candidate}: counties sum to {sum(values)}, printed total {printed}")
            rows.extend(
                {
                    "day": DAYS[year],
                    "office": office,
                    "code": code,
                    "candidate": candidate,
                    "county": codes[key],
                    "votes": value,
                }
                for key, value in zip(keys, values, strict=True)
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="North Dakota's president, U.S. Senate, governor, and other statewide offices by county "
            "1992–1998 from the Secretary of State's official abstracts",
            polity="US",
        )

        def surname(text: str) -> str:
            words = [word for word in text.split() if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}]
            return plain(words[-1]) if words else ""

        def parts(text: str) -> list[str]:
            words = [plain(word) for word in re.sub(r'"[^"]*"|\([^)]*\)', " ", text).split()]
            return [word for word in words if word and word not in {"jr", "sr", "ii", "iii", "iv", "dr"}]

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.nd.gov/elections/election-results",
            license="Public record",
        )
        for year in DAYS:
            cs.artifact(raw.cached(SOURCE, url(year)), source)
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '38%%'"
            ).fetchall()
        )
        if len(counties) != 53:
            raise ValueError("North Dakota needs 53 counties")
        dakota = cs.polity("US-ND")
        ((state,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-ND'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        people: dict[str, set[UUID]] = {}
        ranks: dict[UUID, tuple[bool, bool, list[int], str]] = {}
        for person, text, statewide, known, year in conn.execute(
            """select cd.person_id, n.text, bool_or(x.value ~ ':ND:[a-z-]+$'),
                      bool_or(exists (select from external_id w where w.entity_id = cd.person_id
                                      and w.scheme in ('wikidata', 'bioguide'))),
                      array_agg(distinct left(x.value, 4)::int)
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                    and x.value ~ '^\\d{4}.*(:ND:|:president$)' and x.value !~ ':ND:38\\d{3}:'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            if not text.split():
                continue
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
            ranks[person] = (statewide, known, year, text)
        chosen: dict[str, UUID] = {}

        def fits(one: UUID, candidate: str, year: int) -> bool:
            if candidate in ALONE or min((abs(other - year) for other in ranks[one][2]), default=0) > 30:
                return False
            mine, theirs = parts(candidate), parts(ranks[one][3])
            return len(mine) < 3 or len(theirs) < 3 or mine[1][:1] == theirs[1][:1]

        fptp = cs.key("us.electoral_system", "fptp")
        parties = {
            "DEM": cs.key("us.party", "democratic-party"),
            "REP": cs.key("us.party", "republican-party"),
            "LIB": cs.key("us.party", "libertarian-party"),
            "NAT": cs.key("us.party", "natural-law-party"),
            "REF": cs.key("us.party", "reform-party"),
        }
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':ND:'")
        )
        elections: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election' and value ~ ':ND$'")
        )
        offices = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.office'")}
        made: set[UUID] = set()
        for (day, office), lines in frame.sort("day").group_by("day", "office", maintain_order=True):
            year = day.year
            key = f"{day}:ND:{office}"
            election = elections[f"{day}:ND"]
            contest = contests.get(key)
            if contest is None:
                if office in ("president", "senate", "governor"):
                    raise ValueError(f"{key}: no contest on file")
                contest = cs.key("us.contest", key)
                contests[key] = contest
                base = office.removesuffix(":special")
                office_key = f"{base}:ND"
                office_id = cs.key("us.office", office_key)
                if office_key not in offices:
                    offices.add(office_key)
                    cs.add("office", id=office_id, polity_id=dakota, kind="other")
                    name(office_id, f"North Dakota {TITLES[base]}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office_id,
                    district_area_id=state,
                    term="remainder" if office.endswith(":special") else "full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(
                    contest,
                    f"{year} North Dakota {TITLES[base]} election" + (" (unexpired term)" if base != office else ""),
                )
                cs.add("slug", path=f"us/nd/{day}-general/{office.replace(':', '-')}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket', 'write_in')""",
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
            for candidate, code, votes in (
                lines.group_by("candidate", "code", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                matches = [option for option, _, text in options if surname(text or "") == surname(candidate)]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                if matches:
                    option = matches[0]
                elif code == "OTH":
                    option = cs.key("us.option", f"{key}:write-in:{slug(candidate)}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label=candidate)
                else:
                    known = {
                        one
                        for one in people.get(plain(candidate))
                        or people.get(f"{plain(parts(candidate)[0])}|{surname(candidate)}", set())
                        if one in made or fits(one, candidate, year)
                    }
                    person = chosen.get(plain(candidate)) or (
                        max(
                            known,
                            key=lambda one: (
                                ranks.get(one, (False, False, [], ""))[0],
                                ranks.get(one, (False, False, [], ""))[1],
                                -min(
                                    (abs(other - year) for other in ranks.get(one, (False, False, [year], ""))[2]),
                                    default=0,
                                ),
                            ),
                        )
                        if known
                        else cs.key(f"{SOURCE}.person", f"ND:{slug(candidate)}")
                    )
                    chosen[plain(candidate)] = person
                    if person in cs.minted and person not in made:
                        made.add(person)
                        people.setdefault(plain(candidate), set()).add(person)
                        people.setdefault(f"{plain(parts(candidate)[0])}|{surname(candidate)}", set()).add(person)
                        cs.add("person", id=person)
                        name(person, candidate)
                    elif person not in made:
                        print(f"{key}: {candidate} is joined to {ranks[person][3]}")
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="ticket" if office == "president" else "candidate",
                        party_id=parties.get(code or ""),
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=code == "IND",
                    )
                if option in totals:
                    raise ValueError(f"{key}: two columns share an option with {candidate}")
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
            for county, candidate, votes in lines.select("county", "candidate", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[county],
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
                winner = max(totals, key=totals.__getitem__)
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
        return cs.write()
