import html
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.oh_sos_exec"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://web.archive.org/web/2015id_/http://www.sos.state.oh.us:80/sos/elections/Research/electResultsMain/"
LANDING = "https://www.ohiosos.gov/elections/election-results-and-data/"
PUBLISHER = "Ohio Secretary of State"
TITLE = "Official Election Results, {year}"
LICENSE = "Public record"
OFFICES = {
    "attorney-general": "Attorney General",
    "auditor": "Auditor",
    "secretary-of-state": "Secretary of State",
    "treasurer": "Treasurer",
}
PARTIES = {"D": "democratic-party", "R": "republican-party"}
ERRATA = {
    ("1990-1999OfficialElectionResults/Treasurer110398.aspx", "Joseph T. Deters"): 1719143,
    ("2006ElectionsResults/06-1107AttGen.aspx", "Marc Dann"): 2035865,
}
CAVEAT = (
    "The county table's {name} column adds to {summed:,}, not the {printed:,} the Secretary of State prints as the "
    "statewide total; the counties are kept as printed."
)
PAGES = [
    (
        date(1998, 11, 3),
        "1990-1999OfficialElectionResults/AGandAuditor110398.aspx",
        0,
        [
            ("attorney-general", "Richard Cordray", "D", 1240102),
            ("attorney-general", "Betty D. Montgomery", "R", 2037864),
            ("auditor", "Jim Petro", "R", 1975793),
            ("auditor", "Louis N. Strike", "D", 1191250),
        ],
    ),
    (
        date(1998, 11, 3),
        "1990-1999OfficialElectionResults/SoS110398.aspx",
        3,
        [
            ("secretary-of-state", "J. Kenneth Blackwell", "R", 1789105),
            ("secretary-of-state", "Robert Anthony Martin", "", 186),
            ("secretary-of-state", "Charleta B. Tavares", "D", 1404081),
        ],
    ),
    (
        date(1998, 11, 3),
        "1990-1999OfficialElectionResults/Treasurer110398.aspx",
        3,
        [
            ("treasurer", "Joseph T. Deters", "R", 1719133),
            ("treasurer", "John A. Donofrio", "D", 1472940),
        ],
    ),
    (
        date(2002, 11, 5),
        "2002Results/sostreas.aspx",
        0,
        [
            (None, "J. Kenneth Blackwell", "R", 1827995),
            (None, "Bryan Flannery", "D", 1256428),
            ("treasurer", "Mary O. Boyle", "D", 1459113),
            ("treasurer", "Joseph T. Deters", "R", 1666844),
            ("treasurer", "Robert Martin", "W", 108),
        ],
    ),
    (
        date(2006, 11, 7),
        "2006ElectionsResults/06-1107AttGen.aspx",
        0,
        [("attorney-general", "Marc Dann", "D", 2035825), ("attorney-general", "Betty Montgomery", "R", 1833846)],
    ),
    (
        date(2006, 11, 7),
        "2006ElectionsResults/06-1107Auditor.aspx",
        0,
        [("auditor", "Barbara Sykes", "D", 1891874), ("auditor", "Mary Taylor", "R", 1940665)],
    ),
    (
        date(2006, 11, 7),
        "2006ElectionsResults/06-1107SoS.aspx",
        0,
        [
            ("secretary-of-state", "Jennifer L. Brunner", "D", 2104114),
            ("secretary-of-state", "John A. Eastman", "", 94706),
            ("secretary-of-state", "Greg Hartmann", "R", 1546454),
            ("secretary-of-state", "Timothy J. Kettler", "", 78080),
        ],
    ),
    (
        date(2006, 11, 7),
        "2006ElectionsResults/06-1107ToS.aspx",
        0,
        [("treasurer", "Richard Cordray", "D", None), ("treasurer", "Sandra O'Brien", "R", None)],
    ),
]


def fetch() -> None:
    for _, path, _, _ in PAGES:
        raw.fetch(SOURCE, SITE + path, suffix=".html")


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '39%%'"""
            ).fetchall()
        }
    rows: list[dict[str, object]] = []
    for day, path, skip, columns in PAGES:
        text = raw.cached(SOURCE, SITE + path).path.read_text(encoding="utf-8", errors="replace")
        found: dict[str, list[int]] = {}
        printed: list[int] | None = None
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.DOTALL):
            cells = [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
                for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL)
            ]
            if not cells:
                continue
            label = re.sub(r"[^a-z]", "", cells[0].lower())
            numbers = [int(cell.replace(",", "")) for cell in cells[1 + skip :] if re.fullmatch(r"[\d,]+", cell)]
            if len(numbers) != len(columns):
                continue
            if label == "total":
                printed = numbers
            elif label in counties:
                found[label] = numbers
        if len(found) != 88:
            raise ValueError(f"{path}: {len(found)} counties")
        sums = [sum(values[i] for values in found.values()) for i in range(len(columns))]
        for i, (office, name, party, total) in enumerate(columns):
            expected = printed[i] if printed is not None else total
            if expected is not None and sums[i] != expected and ERRATA.get((path, name)) != sums[i]:
                raise ValueError(f"{path}: {name} sums to {sums[i]}, printed {expected}")
            if office is None:
                continue
            for county, values in found.items():
                rows.append(
                    {
                        "day": day,
                        "page": path,
                        "office": office,
                        "name": name,
                        "party": party,
                        "county": counties[county],
                        "votes": values[i],
                        "printed": expected,
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "lines.parquet")
    print(frame.group_by("day", "office", "name").agg(pl.col("votes").sum()).sort("day", "office", "votes"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio's attorney general, auditor, secretary of state, and treasurer elections of 1998, 2002, "
            "and 2006 by county from the Secretary of State's official tabulations",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            words = [word for word in text.replace(",", " ").split() if plain(word) not in {"jr", "sr", "ii", "iii"}]
            return plain(words[-1])

        sources: dict[int, UUID] = {}
        for year in sorted({day.year for day in frame["day"]}):
            title = TITLE.format(year=year)
            sources[year] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=LANDING,
                license=LICENSE,
            )
        for day, path, _, _ in PAGES:
            cs.artifact(raw.cached(SOURCE, SITE + path), sources[day.year])
        ((area,),) = conn.execute(
            """select u.area_id from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code = 'US-OH'
               where u.scheme_code = 'us.state'"""
        ).fetchall()
        counties = dict(
            conn.execute(
                "select u.code, u.id from reporting_unit u where u.scheme_code = 'us.county' and u.code like '39%%'"
            ).fetchall()
        )
        elections = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        contests = {
            value
            for (value,) in conn.execute(
                "select value from external_id where scheme = 'us.contest' and value ~ '^(1998|2002|2006).*:OH:'"
            )
        }
        people: dict[str, set[UUID]] = {}
        for person, text in conn.execute(
            """select distinct cd.person_id, n.text from candidacy cd
               join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
               join name n on n.entity_id = cd.person_id and n.is_preferred
               where c.district_area_id = %s""",
            [area],
        ).fetchall():
            people.setdefault(f"{plain(text.split()[0])}|{surname(text)}", set()).add(person)
        fptp = cs.key("us.electoral_system", "fptp")
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {fptp, *parties.values()}:
            raise ValueError("the electoral system and parties must already exist")
        made: set[UUID] = set()
        for (day, office), lines in frame.sort("day", "office").group_by("day", "office", maintain_order=True):
            key = f"{day}:OH:{office}"
            if key in contests:
                print(key, "is already in the archive")
                continue
            contest = cs.key("us.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=elections[f"{day}:OH"],
                kind="office",
                office_id=cs.key("us.office", f"{office}:OH"),
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=1,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{day.year} Ohio {OFFICES[office]} election")
            cs.add("slug", path=f"us/oh/{day}-general/{office}", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[f"{day}:OH"],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[day.year],
                is_default=True,
                completeness=1,
            )
            for printed_name, printed, summed in (
                lines.group_by("name").agg(pl.col("printed").first(), pl.col("votes").sum()).iter_rows()
            ):
                if printed != summed:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=CAVEAT.format(name=printed_name, summed=summed, printed=printed),
                    )
            totals: dict[UUID, int] = {}
            for (candidate, party), cells in lines.group_by("name", "party", maintain_order=True):
                probe = f"{plain(candidate.split()[0])}|{surname(candidate)}"
                found = people.get(probe, set())
                person = next(iter(found)) if len(found) == 1 else cs.key(f"{SOURCE}.person", f"OH:{slug(candidate)}")
                if person in cs.minted and person not in made:
                    made.add(person)
                    cs.add("person", id=person)
                    name(person, candidate)
                    people.setdefault(probe, set()).add(person)
                option = cs.key("us.option", f"{key}:{person}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="write_in" if party == "W" else "candidate",
                    party_id=parties.get(party),
                    ballot_label=candidate,
                )
                if party != "W":
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=party not in PARTIES,
                    )
                totals[option] = int(cells["votes"].sum())
                for county, votes in cells.select("county", "votes").iter_rows():
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
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=max(totals, key=totals.__getitem__),
                kind="elected",
                seats=1,
            )
        return cs.write()
