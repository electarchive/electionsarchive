import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ca_sov"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://elections.cdn.sos.ca.gov/sov/"
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote"
FILES = {
    (date(1998, 11, 3), "governor"): "1998-general/sov1-3.pdf",
    (date(1998, 11, 3), "lieutenant-governor"): "1998-general/sov4-6.pdf",
    (date(1998, 11, 3), "secretary-of-state"): "1998-general/sov7-9.pdf",
    (date(1998, 11, 3), "controller"): "1998-general/sov10-12.pdf",
    (date(1998, 11, 3), "treasurer"): "1998-general/sov13-15.pdf",
    (date(1998, 11, 3), "attorney-general"): "1998-general/sov16-18.pdf",
    (date(1998, 11, 3), "insurance-commissioner"): "1998-general/sov19-21.pdf",
    (date(1998, 11, 3), "senate"): "1998-general/sov25-27.pdf",
    (date(1998, 11, 3), "house"): "1998-general/sov28-34.pdf",
}
NAMES = {
    "Harold H Bloomfield La": "Harold H Bloomfield",
    "Gloria Estela Riva": "Gloria Estela La Riva",
    "Lockyer": "Bill Lockyer",
    "Bill Dave Stirling": "Dave Stirling",
    "Alfred 'Al' Burgess": "Alfred 'Al' L Burgess",
    "L Pamela J Pescosolido": "Pamela J Pescosolido",
}
TITLES = {
    "lieutenant-governor": "Lieutenant Governor",
    "secretary-of-state": "Secretary of State",
    "controller": "Controller",
    "treasurer": "Treasurer",
    "attorney-general": "Attorney General",
    "insurance-commissioner": "Insurance Commissioner",
}
PARTIES = {
    "Dem": "democratic-party",
    "Rep": "republican-party",
    "Lib": "libertarian-party",
    "NL": "natural-law-party",
    "N.L.": "natural-law-party",
    "Rfm": "reform-party",
    "Grn": "green-party",
    "AI": "american-independent-party",
    "P&F": "CA:peace-and-freedom-party",
}


def fetch() -> None:
    for path in FILES.values():
        raw.fetch(SOURCE, SITE + path, suffix=".pdf")


def plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def parse() -> None:
    with connect() as conn:
        codes = {
            plain(text): code
            for text, code in conn.execute(
                """select n.text, u.code from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '06%'"""
            ).fetchall()
        }
    if len(codes) != 58:
        raise ValueError("California needs 58 counties")
    rows: list[dict[str, object]] = []
    for (day, office), path in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, SITE + path).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        tables: dict[
            int | None, tuple[dict[str, list[int]], list[tuple[str, str]], list[tuple[str, str]], list[int]]
        ] = {}
        district: int | None = None
        for page in text.split("\f"):
            segments: list[tuple[int | None, list[str]]] = [(district, [])]
            for line in page.splitlines():
                title = re.fullmatch(r"\s*(\d+)(?:st|nd|rd|th) Congressional District\s*", line)
                if title:
                    district = int(title[1])
                    segments.append((district, []))
                else:
                    segments[-1][1].append(line)
            for number, lines in segments:
                first = next(
                    (i for i, line in enumerate(lines) if re.match(r"[A-Z][a-z]+.*\s\d[\d,]*\s*$", line)), None
                )
                if first is None:
                    continue
                seen, header, shape, printed = tables.setdefault(number, ({}, [], [], []))
                edges = [found.end() for found in re.finditer(r"\d[\d,]*", lines[first])]
                words = [line for line in lines[:first] if line.strip()][-3:]
                columns: list[list[list[str]]] = [[[], [], []] for _ in edges]
                for row, line in enumerate(words):
                    for found in re.finditer(r"\S+", line):
                        end = found.end()
                        column = (
                            next((i for i, edge in enumerate(edges) if edge >= end - 1), len(edges) - 1)
                            if row == 0 and number is None
                            else min(range(len(edges)), key=lambda i: abs(edges[i] - end))
                        )
                        columns[column][row].append(found[0])
                names = [(" ".join([*first_names, *last]), " ".join(party)) for first_names, last, party in columns]
                check = [(" ".join(last), " ".join(party)) for _, last, party in columns]
                if not header:
                    header.extend(names)
                    shape.extend(check)
                elif check != shape:
                    raise ValueError(f"{path}: the header changes from {shape} to {check}")
                for line in lines[first:]:
                    found = re.fullmatch(r"(\S.*?)\s{2,}(\d[\d,]*(?:\s+\d[\d,]*)*)\s*", line)
                    if found is None:
                        continue
                    values = [int(value.replace(",", "")) for value in found[2].split()]
                    if len(values) != len(edges):
                        raise ValueError(f"{path}: {line}")
                    if found[1] in ("State Totals", "District Totals"):
                        printed.extend(values)
                    elif plain(found[1]) in codes:
                        seen[codes[plain(found[1])]] = values
                    else:
                        raise ValueError(f"{path}: unknown county {found[1]}")
        for number, (seen, header, _, printed) in tables.items():
            if number is None and len(seen) != 58:
                raise ValueError(f"{path}: {len(seen)} counties")
            if not printed and len(seen) == 1:
                printed = next(iter(seen.values()))
            for column, (name, party) in enumerate(header):
                if name.startswith("Votes not"):
                    continue
                total = sum(values[column] for values in seen.values())
                if total != printed[column]:
                    raise ValueError(f"{path} {number} {name}: {total}, printed {printed[column]}")
                if not total:
                    continue
                rows.extend(
                    {
                        "day": day,
                        "office": office,
                        "district": number,
                        "candidate": NAMES.get(name, name),
                        "party": party,
                        "county": county,
                        "votes": values[column],
                    }
                    for county, values in seen.items()
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows, infer_schema_length=None)
    frame.filter(pl.col("office") != "house").drop("district").write_parquet(PARSED / "lines.parquet")
    frame.filter(pl.col("office") == "house").write_parquet(PARSED / "house.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's 1998 governor, U.S. Senate, other statewide offices, and House by county "
            "from the Secretary of State's Statement of Vote",
            polity="US",
        )

        def surname(text: str) -> str:
            words = [word for word in text.replace(",", " ").split() if plain(word) not in {"jr", "sr", "ii", "iii"}]
            return plain(words[-1]) if words else ""

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.ca.gov/elections/prior-elections/statewide-election-results",
            license="Public record",
        )
        for path in FILES.values():
            cs.artifact(raw.cached(SOURCE, SITE + path), source)
        counties: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.county' and code like '06%%'"
            ).fetchall()
        )
        ((state,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-CA'
               where u.scheme_code = 'us.state'"""
        ).fetchall()

        def middle(text: str) -> str:
            words = [
                plain(word)
                for word in re.sub(r"'[^']*'|\"[^\"]*\"", " ", text.replace(",", " ")).split()
                if plain(word) not in {"jr", "sr", "ii", "iii", ""}
            ]
            return words[1][:1] if len(words) > 2 else ""

        people: dict[str, set[UUID]] = {}
        years: dict[UUID, list[int]] = {}
        texts: dict[UUID, str] = {}
        for person, text, year in conn.execute(
            """select cd.person_id, n.text, array_agg(distinct left(x.value, 4)::int)
               from candidacy cd
               join contest_option o on o.id = cd.option_id
               join external_id x on x.entity_id = o.contest_id and x.scheme = 'us.contest'
                    and x.value ~ '^\\d{4}.*(:CA:|:president$)' and x.value !~ ':CA:06\\d{3}:'
               join name n on n.entity_id = cd.person_id and n.is_preferred group by 1, 2"""
        ).fetchall():
            if text.split():
                people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
                years[person] = year
                texts[person] = text
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        missing = [code for code, party in parties.items() if party in cs.minted]
        if missing:
            raise ValueError(f"parties not on file: {missing}")
        fptp = cs.key("us.electoral_system", "fptp")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':CA:'")
        )
        elections: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election' and value ~ ':CA$'")
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
        made: set[UUID] = set()
        for (day, office), lines in frame.sort("day").group_by("day", "office", maintain_order=True):
            year = day.year
            key = f"{day}:CA:{office}"
            election = elections[f"{day}:CA"]
            contest = contests.get(key)
            if contest is None:
                contest = cs.key("us.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=cs.key("us.office", f"{office}:CA"),
                    district_area_id=state,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} California {TITLES[office]} election")
                cs.add("slug", path=f"us/ca/{day}-general/{office}", entity_id=contest)
            options = conn.execute(
                """select id, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null and kind in ('candidate', 'ticket')""",
                [contest],
            ).fetchall()
            result_set = cs.key(f"{SOURCE}.result_set", key)
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
            for candidate, party, votes in (
                lines.group_by("candidate", "party", maintain_order=True).agg(pl.col("votes").sum()).iter_rows()
            ):
                matches = [option for option, text in options if surname(text or "") == surname(candidate)]
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {candidate}")
                code = party.removesuffix("-Inc")
                if matches:
                    option = matches[0]
                elif code == "Write-in":
                    option = cs.key("us.option", f"{key}:write-in:{slug(candidate)}")
                    cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label=candidate)
                else:
                    known = {
                        one
                        for one in people.get(f"{plain(candidate.split()[0])}|{surname(candidate)}", set())
                        if one in made
                        or (
                            min(abs(other - year) for other in years[one]) <= 30
                            and middle(candidate) in ("", middle(texts[one]) or middle(candidate))
                        )
                    }
                    person = (
                        min(known, key=lambda one: min(abs(other - year) for other in years.get(one, [year])))
                        if known
                        else cs.key(f"{SOURCE}.person", f"CA:{slug(candidate)}")
                    )
                    if person in cs.minted and person not in made:
                        made.add(person)
                        years[person] = [year]
                        people.setdefault(f"{plain(candidate.split()[0])}|{surname(candidate)}", set()).add(person)
                        cs.add("person", id=person)
                        name(person, candidate)
                    elif person not in made:
                        print(f"{key}: {candidate} is joined to an existing person")
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
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
                picked[candidate] = option
                totals[option] = votes
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
            if conn.execute("select from result_set where id = %s", [result_set]).fetchone() is None:
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
        medsl.attach(
            cs,
            conn,
            pl.read_parquet(PARSED / "house.parquet").select(
                year=pl.col("day").dt.year(),
                chamber=pl.lit("house"),
                day="day",
                special=pl.lit(False),
                state=pl.lit("CA"),
                district=pl.col("district").cast(pl.Int16),
                code=pl.col("county"),
                candidate=pl.col("candidate").str.replace_all(",", ""),
                measure=pl.lit(1, pl.Int16),
                writein=pl.col("party") == "Write-in",
                votes="votes",
                kind=pl.lit("county"),
                source=pl.lit("CA"),
            ),
            {"CA": source},
            strict=True,
        )
        return cs.write()
