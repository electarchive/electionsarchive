import json
import re
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ia_scans
from ea.store import DATA, connect

SOURCE = "us.ia_sos_1978"
RAW = "us.ia_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Iowa Secretary of State"
TITLE = "Official canvass summary"
OCR = "https://publications.iowa.gov/39565/1/General_Election_1978_OCR.pdf"
DAY = "1978-11-07"
KEY = f"{DAY}:IA:secretary-of-state"
CANDIDATES = ("Melvin D. Synhorst", "Walter R. Griffin", "Leighton V. Berryhill", "Scattering")
PARTIES = {"Melvin D. Synhorst": "republican-party", "Walter R. Griffin": "democratic-party"}
SOCIALIST = UUID("4e415db4-8d12-59a5-928b-a1863d63e3a9")
TEXT_LAYER = {
    "Winneshiek": (3045, 2092, 28, 1, 5166),
    "Woodbury": (12674, 10982, 109, 0, 23765),
    "Worth": (1731, 1164, 2, 0, 2897),
    "Wright": (2594, 1774, 8, 0, 4376),
}
PRINTED = (423425, 350454, 3067, 101, 777047)


def fetch() -> None:
    raw.fetch(RAW, OCR, suffix=".pdf")


def plain(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def number(value: str | None) -> int:
    return int(value) if value is not None and value.isdigit() else 0


def parse() -> None:
    sha = raw.cached(RAW, ia_scans.URL_1978).sha256
    reads: dict[str, dict[str, tuple[int, ...]]] = {}
    for reader in ("c", "d"):
        response = json.loads(raw.cached(ia_scans.SOURCE, ia_scans.key(DAY, 4, reader, sha)).path.read_text())
        text = "".join(block.get("text", "") for block in response["content"])
        for match in re.finditer(r'\{"county":\s*"([^"]*)",\s*"values":\s*(\[[^\]]*\])\}', text):
            values = tuple(number(value) for value in json.loads(match[2]))
            reads.setdefault(plain(match[1]), {})[reader] = values
    table: dict[str, tuple[str, tuple[int, ...]]] = {}
    for key, found in reads.items():
        sound = [values for values in found.values() if sum(values[:4]) == values[4]]
        if len(set(sound)) != 1:
            raise ValueError(f"{key}: {found}")
        table[key] = (key, sound[0])
    for county, values in TEXT_LAYER.items():
        if sum(values[:4]) != values[4]:
            raise ValueError(county)
        table[plain(county)] = (county, values)
    if len(table) != 99:
        raise ValueError(f"{len(table)} counties")
    sums = tuple(sum(values[index] for _, values in table.values()) for index in range(5))
    if sums != PRINTED:
        raise ValueError(f"column sums {sums}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        [
            {"county": key, "candidate": candidate, "votes": votes}
            for key, (_, values) in table.items()
            for candidate, votes in zip(CANDIDATES, values, strict=False)
        ]
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Iowa's 1978 Secretary of State election by county from the Secretary of State's canvass",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://sos.iowa.gov/elections/results/",
            license="Public record",
        )
        for url in (ia_scans.URL_1978, OCR):
            cs.artifact(raw.cached(RAW, url), source)
        counties = {
            plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '19%%'"""
            ).fetchall()
        }
        contests = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'us.contest' and value ~ '^1978.*:IA:'"
            )
        )
        if KEY in contests:
            raise ValueError("contest exists")
        ((election, area),) = conn.execute(
            "select election_id, district_area_id from contest where id = %s", [contests[f"{DAY}:IA:governor"]]
        ).fetchall()
        fptp = cs.key("us.electoral_system", "fptp")
        office = cs.key("us.office", "secretary-of-state:IA")
        if cs.minted & {fptp, office}:
            raise ValueError("the electoral system and office must exist")
        contest = cs.key("us.contest", KEY)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="office",
            office_id=office,
            district_area_id=area,
            term="full",
            magnitude=1,
            round_number=1,
            electoral_system_id=fptp,
            default_result_kind="counted",
        )
        cs.add(
            "name",
            entity_id=contest,
            text="1978 Iowa Secretary of State election",
            lang="en",
            kind="official",
            is_preferred=True,
        )
        cs.add("slug", path=f"us/ia/{DAY}-general/secretary-of-state", entity_id=contest)
        options: dict[str, UUID] = {}
        for candidate in CANDIDATES:
            if candidate == "Scattering":
                option = cs.key("us.option", f"{KEY}:write_in:scattered")
                cs.add("contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Scattered")
            else:
                known = conn.execute(
                    """select p.id from person p join name n on n.entity_id = p.id where n.text = %s
                       order by exists (select from external_id w where w.entity_id = p.id
                                        and w.scheme in ('wikidata', 'bioguide')) desc, p.id""",
                    [candidate],
                ).fetchall()
                if known:
                    person = known[0][0]
                else:
                    person = cs.key(f"{SOURCE}.person", f"IA:{slug(candidate)}")
                    cs.add("person", id=person)
                    cs.add("name", entity_id=person, text=candidate, lang="en", kind="official", is_preferred=True)
                option = cs.key("us.option", f"{KEY}:{person}")
                party = cs.key("us.party", PARTIES[candidate]) if candidate in PARTIES else SOCIALIST
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=candidate,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{KEY}:{person}:head"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate,
                    is_independent=False,
                )
            options[candidate] = option
        result_set = cs.key(f"{SOURCE}.result_set", KEY)
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
        totals: dict[str, int] = {}
        for name, candidate, votes in frame.select("county", "candidate", "votes").iter_rows():
            totals[candidate] = totals.get(candidate, 0) + votes
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=counties[name],
                option_id=options[candidate],
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=votes,
            )
        winner = max((c for c in totals if c != "Scattering"), key=totals.__getitem__)
        cs.add("outcome", result_set_id=result_set, option_id=options[winner], kind="elected", seats=1)
        return cs.write()
