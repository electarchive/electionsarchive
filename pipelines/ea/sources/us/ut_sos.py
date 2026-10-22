import re
import subprocess
import time
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ut_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/{}"
SITE = "http://elections.utah.gov/Media/Default/Documents/Election_Results/"
PUBLISHER = "Utah Lieutenant Governor's Office of Elections"
TITLE = "Official canvass by county"
FILES = {
    (2000, "general"): ("20110927154949", "General/2000Gen.pdf"),
    (2004, "general"): ("20110927154944", "General/2004Gen.pdf"),
    (2006, "general"): ("20110927154942", "General/2006Gen.pdf"),
    (2008, "general"): ("20110927154940", "General/2008Gen.xls"),
    (2010, "general"): ("20110927154938", "General/2010Gen.xls"),
    (2012, "general"): (None, "https://vote.utah.gov/wp-content/uploads/2023/09/2012-General-Canvass-Report.xls"),
}
DAYS = {
    (2000, "general"): date(2000, 11, 7),
    (2004, "general"): date(2004, 11, 2),
    (2006, "general"): date(2006, 11, 7),
    (2008, "general"): date(2008, 11, 4),
    (2010, "general"): date(2010, 11, 2),
    (2012, "general"): date(2012, 11, 6),
}
TABLES = {
    (2000, "general"): [
        (
            1,
            False,
            2,
            "president",
            ['Bush & Cheney "R"', 'Gore & Lieberman "D"', 'Browne & Oliver "L"', 'Phillips & Frazier "IA"'],
        ),
        (
            2,
            False,
            0,
            "president",
            [
                'Hagelin & Goldhaber "NL"',
                'Buchanan & Foster "Ref"',
                'Nader & LaDuke "G"',
                'Harris & Trowe "SW"',
                'Youngkeit & Beck "Unaffiliated"',
                'Easton & Unknown "WI"',
                'Kunzler & Unknown "WI"',
                'LaBelle & Locke "WI"',
            ],
        ),
        (3, False, 0, "president", ['Pearlman & Lorraine "WI"', 'Schriner & Unknown "WI"', 'Strickland & Marth "WI"']),
        (
            3,
            False,
            3,
            "senate",
            ['Orrin G. Hatch "R"', 'Scott N. Howell "D"', 'Jim Dexter "L"', 'Carlton Edward Bowen "IA"'],
        ),
        (5, True, 0, "governor", ['Leavitt & Walker "R"', 'Orton & Hale "D"']),
        (6, False, 0, "governor", ['Friedbaum & McKenzie "IA"', 'Mason & Unknown "WI"', 'Bell & Unknown "WI"']),
        (
            6,
            False,
            3,
            "attorney-general",
            ['Mark L. Shurtleff "R"', 'Reed M. Richards "D"', 'W. Andrew McCullough "L"'],
        ),
        (6, False, 6, "auditor", ['Auston G. Johnson "R"', 'Tony Bereshnyi "WI"']),
        (7, False, 0, "treasurer", ['Edward T. Alter "R"', 'Hugh A. Butler "L"', 'Elliot J. Hulet "NL"']),
    ],
    (2004, "general"): [
        (
            1,
            False,
            2,
            "president",
            [
                'Badnarik & Campagna "L"',
                'Bush & Cheney "R"',
                'Jay & Taylor "PC"',
                'Kerry & Edwards "D"',
                'Harris & Trowe "SW"',
                'Peroutka & Baldwin "C"',
                'Nader & Camejo "Unaffiliated"',
                'Bone & Unknown "WI"',
                'Brown & Unknown "WI"',
                'Cobb & LaMarche "WI"',
                'Haines & Unknown "WI"',
                'Hayward & Unknown "WI"',
                'Judd & Unknown "WI"',
                'Kennedy & Unknown "WI"',
                'Larson & Unknown "WI"',
                'Schriner & Unknown "WI"',
                'Topham & Unknown "WI"',
                'Warrick & Unknown "WI"',
            ],
        ),
        (
            2,
            False,
            2,
            "senate",
            [
                'Robert F. Bennett "R"',
                'Joe LaBonte "PC"',
                'R. Paul Van Dam "D"',
                'Gary R. Van Horn "C"',
                'Cody Judy "WI"',
                'Nola Tuaone "WI"',
            ],
        ),
        (
            4,
            False,
            2,
            "governor",
            ['Huntsman & Herbert "R"', 'Larsen & Shadowind "PC"', 'Matheson & Hale "D"', 'Fonua & Unknown "WI"'],
        ),
        (
            5,
            False,
            2,
            "attorney-general",
            ['W. Andrew McCullough "L"', 'Mark L. Shurtleff "R"', 'Gregory G. Skordas "D"'],
        ),
        (
            6,
            False,
            2,
            "auditor",
            ['Auston Johnson "R"', 'Valerie S. Larsen "PC"', 'Mike Stoddard "L"', 'Carlos Vasquez "D"'],
        ),
    ],
    (2006, "general"): [
        (
            1,
            False,
            2,
            "senate",
            [
                'Pete Ashdown "D"',
                'Scott N. Bradley "C"',
                'Julian Hatch "DG"',
                'Orrin G. Hatch "R"',
                'Roger I. Price "PC"',
                'Dave Seely "L"',
                'S. Toney "WI"',
            ],
        ),
    ],
}
COUNTY_ORDER = [
    "Beaver",
    "Box Elder",
    "Cache",
    "Carbon",
    "Daggett",
    "Davis",
    "Duchesne",
    "Emery",
    "Garfield",
    "Grand",
    "Iron",
    "Juab",
    "Kane",
    "Millard",
    "Morgan",
    "Piute",
    "Rich",
    "Salt Lake",
    "San Juan",
    "Sanpete",
    "Sevier",
    "Summit",
    "Tooele",
    "Uintah",
    "Utah",
    "Wasatch",
    "Washington",
    "Wayne",
    "Weber",
]
COUNTIES = len(COUNTY_ORDER)
SHEETS = {
    "President": "president",
    "U.S. Senate": "senate",
    "Governor": "governor",
    "Attorney Gen": "attorney-general",
    "Treasurer": "treasurer",
    "Auditor": "auditor",
}
SPECIALS = {(2010, "governor")}
OFFICES = {
    "president": None,
    "senate": None,
    "governor": "Governor",
    "attorney-general": "Attorney General",
    "treasurer": "Treasurer",
    "auditor": "Auditor",
}
PARTIES = {
    "R": "republican-party",
    "REPUBLICAN": "republican-party",
    "D": "democratic-party",
    "DEMOCRATIC": "democratic-party",
    "L": "libertarian-party",
    "LIBERTARIAN": "libertarian-party",
    "C": "constitution-party",
    "CONSTITUTION": "constitution-party",
    "GREEN PARTY": "green-party",
    "PARTY FOR SOCIALISM AND LIBERATION": "party-for-socialism-and-liberation",
    "PC": "UT:personal-choice",
    "IA": "UT:independent-american-party",
    "NL": "natural-law-party",
    "REF": "reform-party",
    "G": "green-party",
    "SW": "socialist-workers-party",
    "DG": "UT:desert-greens",
}
NAMES = {
    (2000, "Friedbaum"): "Jeremy Friedbaum",
    (2004, "Larsen"): "Ken Larsen",
    (2008, "Schanze"): "Dell Schanze",
    (2008, "Doughton"): "Bob Doughton",
}
STATEWIDE = (
    "The canvass gives some write-in candidates a statewide total only, with no county figures, so they are left out "
    "of this set: {}."
)


def address(stamp: str | None, path: str) -> str:
    return path if stamp is None else WAYBACK.format(stamp, SITE + path)


def fetch() -> None:
    for stamp, path in FILES.values():
        location = address(stamp, path)
        try:
            raw.cached(SOURCE, location)
        except LookupError:
            for attempt in range(6):
                time.sleep(4 + 60 * attempt)
                try:
                    raw.fetch(SOURCE, location, suffix="." + path.rsplit(".", 1)[1])
                    break
                except httpx.TransportError:
                    if attempt == 5:
                        raise


def parse() -> None:
    rows: list[dict[str, object]] = []
    for (year, stage), tables in TABLES.items():
        stamp, path = FILES[(year, stage)]
        document = raw.cached(SOURCE, address(stamp, path)).path
        for page, backward, offset, office, headers in tables:
            text = subprocess.run(
                ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(document), "-"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
            start = max(index for index, line in enumerate(text) if '"' in line) + 1
            numbers = [
                cells
                for line in text[start:]
                if (
                    cells := [
                        (found.end(), int(found[0].replace(",", "")))
                        for found in re.finditer(r"(?<!\S)\d[\d,]*(?!\S)", line)
                    ]
                )
            ]
            ends = [end for end, _ in numbers[COUNTIES]]
            span = (
                range(len(ends) - offset - len(headers), len(ends) - offset)
                if backward
                else range(offset, offset + len(headers))
            )
            grid = []
            for cells in numbers[: COUNTIES + 1]:
                placed = {
                    min(range(len(ends)), key=lambda index: abs(ends[index] - end)): value for end, value in cells
                }
                grid.append([placed.get(index, 0) for index in span])
            for column, header in enumerate(headers):
                if sum(row[column] for row in grid[:COUNTIES]) != grid[COUNTIES][column]:
                    raise ValueError(f"{year} page {page} {header}: the counties do not add up to the total")
            for county, row in zip(COUNTY_ORDER, grid[:COUNTIES], strict=True):
                for header, votes in zip(headers, row, strict=True):
                    rows.append(
                        {
                            "year": year,
                            "stage": stage,
                            "office": office,
                            "header": header,
                            "county": county,
                            "votes": votes,
                        }
                    )
    for (year, stage), (stamp, path) in FILES.items():
        if path.endswith(".pdf"):
            continue
        book = fastexcel.read_excel(raw.cached(SOURCE, address(stamp, path)).path)
        if year == 2012:
            table = book.load_sheet("Constitutional", header_row=None, dtypes="string").to_polars()
            cells = [[str(cell).strip() if cell is not None else "" for cell in row] for row in table.iter_rows()]
            offices = {}
            for column, label in enumerate(cells[0][4:], 4):
                if label:
                    offices[column] = SHEETS[label.replace("State ", "").replace("Attorney General", "Attorney Gen")]
            office = ""
            total = next(row for row in cells if row[0].upper() == "TOTAL")
            counties = cells[2 : cells.index(total)]
            for column in range(4, len(cells[1])):
                office = offices.get(column, office)
                header = re.sub(r"\s+", " ", cells[1][column])
                header = re.sub(r"\s*,\s*", " & ", header, count=1) if office == "governor" else header
                header = re.sub(r"\s*\(([A-Z]+)\)$", r' "\1"', header)
                counted = sum(int(float(row[column])) for row in counties if row[column])
                if counted and counted != int(float(total[column])):
                    raise ValueError(f"{year} {office} {header}: {counted} against {total[column]}")
                for row in counties if counted else [total]:
                    rows.append(
                        {
                            "year": year,
                            "stage": stage,
                            "office": office,
                            "header": header,
                            "county": row[0] if counted else "",
                            "votes": int(float(row[column])) if row[column] else 0,
                        }
                    )
            continue
        for sheet in book.sheet_names:
            if sheet not in SHEETS:
                continue
            table = book.load_sheet(sheet, header_row=None, dtypes="string").to_polars()
            cells = [[str(cell).strip() if cell is not None else "" for cell in row] for row in table.iter_rows()]
            start = next(index for index, row in enumerate(cells) if row[0].upper() == "COUNTY")
            header = cells[start]
            for row in cells[start + 1 :]:
                if row[0].upper() == "TOTAL":
                    totals = row
                    break
                for column in range(4, len(header)):
                    if header[column] and row[column]:
                        rows.append(
                            {
                                "year": year,
                                "stage": stage,
                                "office": SHEETS[sheet],
                                "header": re.sub(r"\s+", " ", header[column]),
                                "county": row[0],
                                "votes": int(float(row[column])),
                            }
                        )
            for column in range(4, len(header)):
                if header[column] and totals[column] and float(totals[column]):
                    counted = sum(
                        int(float(row[column]))
                        for row in cells[start + 1 : cells.index(totals)]
                        if column < len(row) and row[column]
                    )
                    if counted and counted != int(float(totals[column])):
                        raise ValueError(f"{year} {sheet} {header[column]}: {counted} against {totals[column]}")
                    if not counted:
                        rows.append(
                            {
                                "year": year,
                                "stage": stage,
                                "office": SHEETS[sheet],
                                "header": re.sub(r"\s+", " ", header[column]),
                                "county": "",
                                "votes": int(float(totals[column])),
                            }
                        )
    frame = pl.DataFrame(rows)
    counts = frame.filter(pl.col("county") != "").group_by("year", "office").agg(pl.col("county").n_unique())
    if (short := counts.filter(pl.col("county") != COUNTIES)).height:
        raise ValueError(f"contests without all 29 counties: {short}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")
    print(
        frame.group_by("year", "office", "header")
        .agg(pl.col("votes").sum(), (pl.col("county") == "").any().alias("statewide"))
        .sort("year", "office", "votes", descending=[False, False, True])
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Utah's president, U.S. Senate, governor, and statewide executive elections 2008–2012 by county "
            "from the state's official canvass",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def split(header: str) -> tuple[str, str]:
            found = re.fullmatch(r'(.*?)\s*(?:"\s*([^"]+?)\s*"|\((write[ -]?in)\))', header, re.IGNORECASE)
            if found is None:
                raise ValueError(f"no party in {header}")
            party = (found[2] or found[3]).upper()
            return found[1].strip(), "W" if re.fullmatch(r"WRITE ?-?IN|WI", party) else party

        def surname(text: str) -> str:
            words = [
                word
                for word in re.sub(r"\([^)]*\)|\"[^\"]*\"", " ", text).replace(",", " ").split()
                if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}
            ]
            return plain(words[-1]) if words else ""

        def full(text: str) -> str:
            return plain(re.sub(r"\([^)]*\)|\"[^\"]*\"|,? (Jr|Sr|III|II)\.?$", " ", text))

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://vote.utah.gov",
            license="Public record",
        )
        for stamp, path in FILES.values():
            cs.artifact(raw.cached(SOURCE, address(stamp, path)), source)
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-UT'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties: dict[str, str] = {
            plain(text): str(unit)
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '49%%'"""
            ).fetchall()
        }
        missing = sorted({county for county in frame["county"] if county and plain(county) not in counties})
        if missing:
            raise ValueError(f"unmatched counties: {missing}")
        elections: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':UT:'")
        )
        people: dict[str, set[UUID]] = {}
        for person, text in conn.execute(
            """select distinct cd.person_id, n.text from candidacy cd
               join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
               join election e on e.id = c.election_id join polity p on p.id = e.polity_id and p.iso_code = 'US-UT'
               join name n on n.entity_id = cd.person_id and n.is_preferred"""
        ).fetchall():
            people.setdefault(full(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must already exist")
        filled = 0
        made: set[UUID] = set()
        for (year, stage, office), lines in frame.sort("year", "office").group_by(
            "year", "stage", "office", maintain_order=True
        ):
            day = DAYS[(year, stage)]
            special = (year, office) in SPECIALS
            title = OFFICES[office]
            key = f"{day}:UT:{office}" + (":special" if special else "")
            election = elections[f"{day}:UT"]
            existing = key in contests
            contest = cs.key("us.contest", key)
            if not existing:
                if title is None:
                    raise ValueError(f"{key} must already exist")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=cs.key("us.office", f"{office}:UT"),
                    district_area_id=area,
                    term="remainder" if special else "full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} Utah {title} {'special election' if special else 'election'}")
                cs.add("slug", path=f"us/ut/{day}-general/{office}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            if result_set not in cs.minted:
                continue
            options: list[tuple[UUID, str, str]] = []
            outcomes: list[tuple[UUID, str, int | None]] = []
            on_file: dict[UUID, int] = {}
            if existing:
                options = conn.execute(
                    """select id, kind, ballot_label from contest_option
                       where contest_id = %s and kind <> 'others' and parent_option_id is null""",
                    [contest],
                ).fetchall()
                for row in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                         supersedes_id from result_set where contest_id = %s and is_default""",
                    [contest],
                ).fetchall():
                    cs.add(
                        "result_set",
                        **dict(
                            zip(
                                (
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
                                ),
                                row,
                                strict=True,
                            )
                        ),
                        is_default=False,
                    )
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
            statewide = lines.filter(pl.col("county") == "")
            lines = lines.filter(pl.col("county") != "")
            picked: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            for header, votes in (
                lines.group_by("header", maintain_order=True)
                .agg(pl.col("votes").sum())
                .sort("votes", descending=True)
                .iter_rows()
            ):
                if not votes:
                    continue
                shown, code = split(header)
                head = shown.split(" & ")[0].strip()
                head = NAMES.get((year, head), head)
                matches = [
                    option
                    for option, kind, text in options
                    if surname(text) == surname(head) and (kind == "write_in") == (code == "W")
                ]
                if len(matches) > 1:
                    matches = [option for option, _, text in options if full(text) == full(head)]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {header}")
                if matches:
                    option = matches[0]
                elif code == "W":
                    option = cs.key("us.option", f"{key}:write-in:{slug(head)}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label=head)
                else:
                    if " " not in head:
                        raise ValueError(f"{key}: {header} is a surname only and matches no option")
                    probes = [full(head), f"{plain(head.split()[0])}|{surname(head)}"]
                    person = next(
                        (next(iter(found)) for probe in probes if len(found := people.get(probe, set())) == 1), None
                    )
                    if person is None:
                        person = cs.key(f"{SOURCE}.person", f"UT:{slug(head)}")
                    if person in cs.minted and person not in made:
                        made.add(person)
                        cs.add("person", id=person)
                        name(person, head)
                        for probe in probes:
                            people.setdefault(probe, set()).add(person)
                    option = cs.key("us.option", f"{key}:{person}")
                    if option in cs.minted:
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="ticket" if office == "president" else "candidate",
                            party_id=parties.get(code),
                            ballot_label=head,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{key}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=head,
                            is_independent=code not in PARTIES,
                        )
                if option in totals:
                    raise ValueError(f"{key}: two candidates share an option with {header}")
                if option in on_file and abs(on_file[option] - votes) > max(10, on_file[option] // 200):
                    print(f"{key}: {header} has {votes}, {on_file[option]} on file")
                totals[option] = votes
                picked[header] = option
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
            if statewide.height:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=STATEWIDE.format(
                        ", ".join(
                            f"{split(header)[0]} ({votes:,})"
                            for header, votes in statewide.select("header", "votes").iter_rows()
                        )
                    ),
                )
            for header, county, votes in lines.select("header", "county", "votes").iter_rows():
                if header in picked:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[plain(county)],
                        option_id=picked[header],
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
                if office == "president":
                    cs.add("outcome", result_set_id=result_set, option_id=winner, kind="won", seats=5)
                else:
                    cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
            filled += 1
        print(filled, "contests filled")
        return cs.write()
