import html
import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.ny_returns"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://web.archive.org/web/20021202192437id_/http://www.elections.state.ny.us/elections/"
PUBLISHER = "New York State Board of Elections"
TITLE = "Statewide election returns by county and party"
DAYS = {1994: date(1994, 11, 8), 1996: date(1996, 11, 5), 1998: date(1998, 11, 3)}
FILES = {
    (1994, "governor"): "1994/gov94.htm",
    (1994, "senate"): "1994/sen94.htm",
    (1994, "attorney-general"): "1994/ag94.htm",
    (1994, "comptroller"): "1994/comptr94.htm",
    (1996, "president"): "1996/pres96.htm",
    (1998, "governor"): "1998/GOVWEB.pdf",
    (1998, "senate"): "1998/USSENWEB.pdf",
    (1998, "comptroller"): "1998/COMPWEB.pdf",
    (1998, "attorney-general"): "1998/ATGENWEB.pdf",
}
COLUMNS = {
    (1998, "governor"): [
        ("DEM", "Peter F. Vallone"),
        ("REP", "George E. Pataki"),
        ("CON", "George E. Pataki"),
        ("IND", "B. Thomas Golisano"),
        ("LIB", "Betsy McCaughey Ross"),
        ("RTL", "Michael Reynolds"),
        ("MRP", "Thomas K. Leighton"),
        ("UNI", "Mary Alice France"),
        ("GRN", "Al Lewis"),
        ("SWP", "Al Duncan"),
        ("WOR", "Peter F. Vallone"),
        ("LBT", "Christopher B. Garvey"),
    ],
    (1998, "senate"): [
        ("DEM", "Charles E. Schumer"),
        ("REP", "Al D'Amato"),
        ("CON", "Al D'Amato"),
        ("IND", "Charles E. Schumer"),
        ("LIB", "Charles E. Schumer"),
        ("RTL", "Al D'Amato"),
        ("MRP", "Corinne E. Kurtz"),
        ("GRN", "Joel Kovel"),
        ("SWP", "Rose Ana Berbeo"),
        ("LBT", "William P. McMillen"),
    ],
    (1998, "comptroller"): [
        ("DEM", "H. Carl McCall"),
        ("REP", "Bruce A. Blakeman"),
        ("CON", "Bruce A. Blakeman"),
        ("IND + WOR", "H. Carl McCall"),
        ("LIB", "H. Carl McCall"),
        ("RTL", "Douglas H. Harknett"),
        ("MRP", "Dean Venezia"),
        ("GRN", "Howie Hawkins"),
        ("LBT", "Robert M. Goodman"),
    ],
    (1998, "attorney-general"): [
        ("DEM", "Eliot Spitzer"),
        ("REP", "Dennis C. Vacco"),
        ("CON", "Dennis C. Vacco"),
        ("IND", "Catherine Abate"),
        ("LIB", "Eliot Spitzer"),
        ("RTL", "Robert W. Dapelo"),
        ("GRN", "Johann L. Moore"),
        ("LBT", "Daniel A. Conti Jr."),
    ],
}
HEADS = {
    "Cuomo": "Mario M. Cuomo",
    "Pataki": "George E. Pataki",
    "Walsh": "Robert L. Walsh",
    "Lane": "Lawrence Lane",
    "Golisano": "B. Thomas Golisano",
    "Schulz": "Robert L. Schulz",
}
ALONE = {"Robert M. Goodman"}
FIXES = {(1994, "governor", "Hamilton", "Mario M. Cuomo", "DEM"): 546}
CODES = {
    "Democrat": "DEM",
    "Democratic": "DEM",
    "Republican": "REP",
    "Republican/Tax Cut Now": "REP + TCN",
    "Republican / Tax Cut Now": "REP + TCN",
    "Conservative": "CON",
    "Conservative/Tax Cut Now": "CON + TCN",
    "Right to Life": "RTL",
    "Liberal": "LIB",
    "Tax Cut Now": "TCN",
    "Socialist Workers": "SWP",
    "Independence Fusion": "IFU",
    "Independence": "IND",
    "Libertarian": "LBT",
    "Freedom": "FDM",
    "Natural Law": "NLP",
    "Worker's World": "WWP",
    "Green": "GRN",
}
LINES = {
    "DEM": ("democratic-party", "Democratic"),
    "REP": ("republican-party", "Republican"),
    "REP + TCN": ("republican-party", "Republican and Tax Cut Now"),
    "CON": ("NY:conservative-party", "Conservative"),
    "CON + TCN": ("NY:conservative-party", "Conservative and Tax Cut Now"),
    "RTL": ("NY:right-to-life", "Right to Life"),
    "LIB": ("NY:liberal", "Liberal"),
    "TCN": ("NY:tax-cut-now-party", "Tax Cut Now"),
    "SWP": ("socialist-workers-party", "Socialist Workers"),
    "IFU": ("NY:independence-fusion-party", "Independence Fusion"),
    "IND": ("NY:independence-party", "Independence"),
    "IND + WOR": ("NY:independence-party", "Independence and Working Families"),
    "WOR": ("NY:working-families-party", "Working Families"),
    "LBT": ("libertarian-party", "Libertarian"),
    "FDM": ("NY:freedom-party", "Freedom"),
    "NLP": ("natural-law-party", "Natural Law"),
    "WWP": ("workers-world-party", "Workers World"),
    "GRN": ("green-party", "Green"),
    "MRP": ("NY:marijuana-reform-party", "Marijuana Reform"),
    "UNI": (None, "Unity"),
}
TITLES = {"attorney-general": "Attorney General", "comptroller": "Comptroller"}
BLANK = (
    "The Board's returns combine blank, void, and scattered votes in one figure, so this set has no write-in option."
)


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, SITE + path, suffix="." + path.rsplit(".", 1)[1])


def parse() -> None:
    rows: list[dict[str, object]] = []
    for (year, office), path in FILES.items():
        file = raw.cached(SOURCE, SITE + path).path
        counties: dict[str, dict[tuple[str, str], int]] = {}
        printed: dict[tuple[str, str], int] = {}
        if path.endswith(".pdf"):
            columns = COLUMNS[(year, office)]
            text = subprocess.run(["pdftotext", "-layout", str(file), "-"], capture_output=True, text=True, check=True)
            for line in text.stdout.splitlines():
                found = re.fullmatch(r"([A-Za-z][A-Za-z. ]+?)\s{2,}([\d,]+(?:\s+[\d,]+)+)\s*", line.strip())
                if found is None or found[1] == "RECAP" or found[1].startswith("Total "):
                    continue
                values = [int(value.replace(",", "")) for value in found[2].split()]
                if len(values) != len(columns) + 2 or sum(values[:-1]) != values[-1]:
                    raise ValueError(f"{year} {office}: {line}")
                cells = {(head, code): value for (code, head), value in zip(columns, values, strict=False)}
                if found[1] == "STATEWIDE TOTAL":
                    printed = cells
                else:
                    counties[found[1].title()] = cells
        else:
            text = html.unescape(re.sub(r"<[^>]*>", "", file.read_text(errors="replace")))
            block = None
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("Total "):
                    block = None
                    continue
                if found := re.fullmatch(r"(?!.* by County)(.+?) County", line):
                    block = found[1]
                    counties[block] = {}
                    continue
                if re.fullmatch(r"New York State Total|STATEWIDE TOTAL", line):
                    block = "STATE"
                    continue
                if re.match(r"(New York City|Rest of State) (Sub)?total", line, re.IGNORECASE):
                    block = None
                    continue
                found = re.fullmatch(r"(.+?)\s*-\s*(.+?)\s*\.{2,}\s*([\d,]+)", line)
                if found is None or block is None:
                    continue
                left, right = found[1].strip(), found[2].strip()
                if office == "president":
                    code, head = CODES[left], right.split("/")[0]
                else:
                    code, head = CODES[right], left.split("/")[0]
                head = HEADS.get(head, head)
                cells = printed if block == "STATE" else counties[block]
                votes = FIXES.get((year, office, block, head, code), int(found[3].replace(",", "")))
                cells[(head, code)] = cells.get((head, code), 0) + votes
        if len(counties) != 62:
            raise ValueError(f"{year} {office}: {len(counties)} counties")
        sums: dict[tuple[str, str], int] = {}
        for county, cells in counties.items():
            for (head, code), votes in cells.items():
                sums[(head, code)] = sums.get((head, code), 0) + votes
                rows.append(
                    {"year": year, "office": office, "head": head, "party": code, "county": county, "votes": votes}
                )
        if sums != printed:
            raise ValueError(f"{year} {office}: counties sum to {sums}, printed {printed}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York's governor, U.S. Senate, attorney general, and comptroller 1994 and 1998 and "
            "president 1996 by county and ballot line, from the State Board of Elections",
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

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="http://www.elections.state.ny.us/elections/election.htm",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, SITE + path), source)
        counties: dict[str, UUID] = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '36%%'"""
            ).fetchall()
        }
        missing = sorted({county for county in frame["county"] if plain(county) not in counties})
        if missing:
            raise ValueError(f"unmatched counties: {missing}")
        people: dict[str, set[UUID]] = {}
        ranks: dict[UUID, tuple[bool, bool, list[int]]] = {}
        texts: dict[UUID, str] = {}
        for person, text, statewide, known, years in conn.execute(
            """select cd.person_id, n.text, bool_or(x.value ~ ':NY:[a-z-]+(:special|:[a-z-]+)?$'),
                      bool_or(exists (select from external_id w where w.entity_id = cd.person_id
                                      and w.scheme in ('wikidata', 'bioguide'))),
                      array_agg(distinct left(x.value, 4)::int)
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                    and x.value ~ '^\\d{4}.*(:NY:|:president$)' and x.value !~ ':NY:36\\d{3}:'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            if not text.split():
                continue
            people.setdefault(plain(text), set()).add(person)
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
            ranks[person] = (statewide, known, years)
            texts[person] = text

        def fits(one: UUID, candidate: str, year: int) -> bool:
            if candidate in ALONE or min((abs(other - year) for other in ranks[one][2]), default=0) > 30:
                return False
            mine, mark = parts(candidate)
            theirs, their_mark = parts(texts[one])
            if mark & {"ii", "iii", "iv"} != their_mark & {"ii", "iii", "iv"}:
                return False
            return len(mine) < 3 or len(theirs) < 3 or mine[1][:1] == theirs[1][:1]

        parties = {code: cs.key("us.party", key) for code, (key, _) in LINES.items() if key is not None}
        fptp = cs.key("us.electoral_system", "fptp")
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the parties and the fptp system must already exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':NY:'")
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
        chosen: dict[str, UUID] = {}
        for (year, office), lines in frame.group_by("year", "office", maintain_order=True):
            day = DAYS[year]
            key = f"{day}:NY:{office}"
            result_set = cs.key(f"{SOURCE}.result_set", key)
            contest = contests.get(key)
            if contest is None:
                ((election, area),) = conn.execute(
                    "select election_id, district_area_id from contest where id = %s", [contests[f"{day}:NY:governor"]]
                ).fetchall()
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=cs.key("us.office", f"{office}:NY"),
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} New York {TITLES[office]} election")
                cs.add("slug", path=f"us/ny/{day}-general/{office}", entity_id=contest)
            else:
                ((election,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            roots: list[tuple[UUID, str]] = []
            children: dict[tuple[UUID, UUID | None], UUID] = {}
            for option, kind, label, parent, party in conn.execute(
                "select id, kind, ballot_label, parent_option_id, party_id from contest_option where contest_id = %s",
                [contest],
            ).fetchall():
                if parent is None and kind in ("candidate", "ticket", "write_in"):
                    roots.append((option, label or ""))
                elif parent is not None:
                    children.setdefault((parent, party), option)
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
            cs.add("note", entity_id=result_set, kind="caveat", body_md=BLANK)
            totals: dict[UUID, int] = {}
            targets: dict[tuple[str, str], UUID] = {}
            for (head,), cells in lines.group_by("head", maintain_order=True):
                found = [option for option, label in roots if plain(label).endswith(plain(head.split()[-1]))]
                if len(found) > 1:
                    found = [option for option, label in roots if surname(label) == surname(head)]
                if len(found) > 1:
                    raise ValueError(f"{key}: {len(found)} options for {head}")
                codes = cells["party"].unique(maintain_order=True).to_list()
                if found:
                    root = found[0]
                    label = " ".join(next(text for option, text in roots if option == root).split())
                else:
                    if office == "president":
                        raise ValueError(f"{key}: no ticket for {head}")
                    label = head
                    first = plain(head.split()[0])
                    known = {
                        one
                        for one in people.get(plain(head)) or people.get(f"{first}|{surname(head)}", set())
                        if fits(one, head, year)
                    }
                    person = chosen.get(plain(head)) or (
                        max(
                            known,
                            key=lambda one: (
                                ranks[one][0],
                                ranks[one][1],
                                -min(abs(other - year) for other in ranks[one][2]),
                            ),
                        )
                        if known
                        else cs.key(f"{SOURCE}.person", f"NY:{slug(head)}")
                    )
                    if person not in chosen.values():
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, head)
                            people.setdefault(f"{first}|{surname(head)}", set()).add(person)
                            ranks[person] = (True, False, [year])
                            texts[person] = head
                        else:
                            print(f"{key}: {head} is joined to {texts[person]}")
                    chosen[plain(head)] = person
                    root = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=root,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(codes[0]) if len(codes) == 1 else None,
                        ballot_label=head,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=root,
                        role="head",
                        ballot_name=head,
                    )
                    roots.append((root, head))
                totals[root] = int(cells["votes"].sum())
                for code in codes:
                    if len(codes) == 1:
                        targets[(head, code)] = root
                        continue
                    party = parties.get(code)
                    child = children.get((root, party)) if party is not None else None
                    if child is None:
                        child = cs.key("us.option", f"{key}:{root}:{slug(code)}")
                        cs.add(
                            "contest_option",
                            id=child,
                            contest_id=contest,
                            parent_option_id=root,
                            kind="ticket" if office == "president" else "candidate",
                            party_id=party,
                            ballot_label=f"{label} ({LINES[code][1]})",
                        )
                        children[(root, party)] = child
                    targets[(head, code)] = child
            kept = [row for row in outcomes if row[0] in totals]
            for option, kind, seats in kept:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            if not kept:
                winner = max(totals, key=totals.__getitem__)
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
            if conn.execute("select from result_set where id = %s", [result_set]).fetchone() is None:
                for county, head, code, votes in lines.select("county", "head", "party", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=counties[plain(county)],
                        option_id=targets[(head, code)],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
        return cs.write()
