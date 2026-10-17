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
PUBLISHER = "North Dakota Secretary of State"
TITLE = "Official abstracts of votes cast at the general elections, 1990–1998"
DAYS = {
    1992: date(1992, 11, 3),
    1994: date(1994, 11, 8),
    1996: date(1996, 11, 5),
    1998: date(1998, 11, 3),
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
TYPOS = {"mclntosh": "mcintosh"}
ALONE = {"Roger Johnson"}
TITLES = {
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
        names = sorted(
            (w for w in words if w[0] < 120 and re.match(r"[A-Za-z]", w[4]) and w[3] - w[1] < 9),
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
            if w[4].upper() == "TOTAL":
                break
        keys = [TYPOS.get(plain(text), plain(text)) for _, text in lines[:-1]]
        if sorted(keys) != sorted(codes):
            raise ValueError(f"{year}: counties {sorted(set(keys) ^ set(codes))}")
        found = []
        for w in words:
            if not re.fullmatch(r"\d[\d,]*", w[4]) or w[0] < 125 or w[3] - w[1] > 9:
                continue
            middle = (w[1] + w[3]) / 2
            line = min(range(len(lines)), key=lambda i: abs(lines[i][0] - middle))
            if abs(lines[line][0] - middle) <= 3.6:
                found.append((line, w))
        totals = sorted(w for line, w in found if line == len(lines) - 1)
        cells: dict[tuple[int, int], int] = {}
        for line, w in found:
            middle = (w[0] + w[2]) / 2
            column = min(
                range(len(totals)),
                key=lambda i: (
                    0
                    if totals[i][0] - 2 <= middle <= totals[i][2] + 2
                    else abs((totals[i][0] + totals[i][2]) / 2 - middle)
                ),
            )
            if (line, column) in cells and column in columns:
                raise ValueError(f"{year}: two numbers in {keys[line]} column {column}")
            cells[(line, column)] = int(w[4].replace(",", ""))
        for column, (office, candidate, code) in columns.items():
            values = [cells.get((line, column), 0) for line in range(len(keys))]
            if sum(values) != cells[(len(keys), column)]:
                raise ValueError(f"{year} {candidate}: {sum(values)}, printed {cells[(len(keys), column)]}")
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
