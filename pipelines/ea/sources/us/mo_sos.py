import re
import subprocess
from pathlib import Path
from uuid import UUID

import polars as pl

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
DAY = "1996-11-05"
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
}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LIB": "libertarian-party",
    "UST": "MO:u-s-taxpayers-party",
    "REF": "reform-party",
    "NAT": "natural-law-party",
}
WRITE_IN = "Write-ins"


def fetch() -> None:
    for url in (COUNTY, STATE):
        raw.fetch(SOURCE, url, suffix=".pdf")


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
                rows.append({"office": office, "county": code, "label": label, "party": party, "votes": votes})
        if sums != {label: votes for label, _, votes in printed}:
            raise ValueError(f"{office}: counties sum to {sums}, printed {printed}")
    frame = (
        pl.DataFrame(rows)
        .group_by("office", "county", "label", "party", maintain_order=True)
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
            summary="Missouri's president, governor, and statewide offices by county 1996 "
            "from the Secretary of State's official returns",
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

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.sos.mo.gov/elections/resultsandstats",
            license="Public record",
        )
        for url in (COUNTY, STATE):
            cs.artifact(raw.cached(SOURCE, url), source)
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

        def fits(one: UUID, candidate: str) -> bool:
            if min((abs(other - 1996) for other in ranks[one][2]), default=0) > 30:
                return False
            mine, mark = parts(candidate)
            theirs, their_mark = parts(texts[one])
            if mark & {"ii", "iii", "iv"} != their_mark & {"ii", "iii", "iv"}:
                return False
            return len(mine) < 3 or len(theirs) < 3 or mine[1][:1] == theirs[1][:1]

        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", party) for code, party in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must exist")
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':MO:'")
        )
        ((election, area),) = conn.execute(
            "select election_id, district_area_id from contest where id = %s", [contests[f"{DAY}:MO:governor"]]
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
        for (office,), lines in frame.group_by("office", maintain_order=True):
            key = f"{DAY}:MO:{office}"
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
                name(contest, f"1996 Missouri {TITLES[office]} election")
                cs.add("slug", path=f"us/mo/{DAY}-general/{office}", entity_id=contest)
                election_id = election
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
                        if fits(one, candidate)
                    }
                    person = (
                        max(
                            known,
                            key=lambda one: (
                                ranks[one][0],
                                ranks[one][1],
                                -min(abs(other - 1996) for other in ranks[one][2]),
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
                source_id=source,
                is_default=True,
                completeness=1,
            )
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
