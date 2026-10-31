import html
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "gb.council_tax"
VERSION = "1"
ROWS = Path(__file__).with_name("council_tax_referendums.tsv")
VOTES, BALLOTS, INVALID = 1, 3, 5
PFA_CODES = {"E23000026": "Bedfordshire"}
SOURCES = {
    "luton": (
        (
            "https://www.luton.gov.uk/council-elections/elections-voting/election-results/referendum-results/"
            "council-tax-referendum-2015"
        ),
        "Luton Borough Council",
        (
            "Notice by the Chief Counting Officer: referendums on the council tax increase for Bedfordshire's Police "
            "and Crime Commissioner, 7 May 2015"
        ),
    ),
    "central_bedfordshire": (
        "https://www.centralbedfordshire.gov.uk/directory_record/2651/election_results_from_the_pcc_referendum",
        "Central Bedfordshire Council",
        "Election results from the PCC referendum, 7 May 2015",
    ),
}
QUESTION = (
    "Do you want the Bedfordshire Police and Crime Commissioner to increase the amount it charges by 15.8%? "
    "If most voters chose Yes, the increase would be 15.8%; if most chose No, it would be 2.0% instead."
)
CONTEXT = (
    "Under the Localism Act 2011, a council tax increase above the government's threshold has to be approved by "
    "a local referendum. The Bedfordshire Police and Crime Commissioner proposed a rise of 15.8% for 2015-16, the "
    "only such referendum held so far. The poll ran in the Bedford Borough, Central Bedfordshire, and Luton areas "
    "alongside the general election and the local elections of 7 May 2015; a No vote meant an increase of 2.0%."
)
PARTIAL = (
    "The counting officer's declaration gives one combined count for the three council areas; the separate counts "
    "for Bedford Borough, Central Bedfordshire, and Luton are not yet loaded."
)


def fetch() -> None:
    for url, *_ in SOURCES.values():
        raw.fetch(SOURCE, url, suffix=".html")


def printed(key: str) -> set[str]:
    text = html.unescape(
        re.sub(r"(?s)<(script|style).*?</\1>|<[^>]+>", " ", raw.cached(SOURCE, SOURCES[key][0]).path.read_text())
    )
    return set(re.sub(r"(?<=\d),(?=\d{3}\b)", "", text).split())


def emit() -> Path:
    table = pl.read_csv(ROWS, separator="\t", infer_schema=False)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Council tax referendums in England: the Bedfordshire Police and Crime Commissioner's 15.8% "
            "increase, 7 May 2015, from the counting officers' declarations",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        system = cs.key("gb.electoral_system", "referendum")
        if system in cs.minted:
            raise ValueError("publish gb.referendums first")
        sources: dict[str, UUID] = {}
        for key, (url, publisher, title) in SOURCES.items():
            sources[key] = cs.source(publisher, title)
            if sources[key] in cs.minted:
                cs.add("source", id=sources[key], kind="official_body", publisher=publisher, title=title, url=url)
                cs.artifact(raw.cached(SOURCE, url), sources[key])
        for pfa in sorted(set(table["area"]) & set(PFA_CODES)):
            day = date.fromisoformat(table.filter(pl.col("area") == pfa)["day"][0])
            rows = table.filter(pl.col("day") == str(day))
            place = PFA_CODES[pfa]
            polity = cs.key("gb.polity", pfa)
            national = conn.execute(
                "select entity_id from external_id where scheme = 'gb.election' and value = %s", [f"{day}:local:gb"]
            ).fetchone()
            election = cs.key("gb.election", f"{day}:council-tax:{pfa}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=None if national is None else national[0],
                kind="referendum",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{day.year} {place} council tax referendum")
            path = f"gb/{slug(place)}-police-area/{day}-council-tax-referendum"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key("gb.contest", f"{day}:council-tax:{pfa}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=cs.area("gb.pfa", pfa),
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            name(contest, "Police and Crime Commissioner council tax increase")
            cs.add("slug", path=f"{path}/police-council-tax-increase", entity_id=contest)
            cs.add("note", entity_id=contest, kind="context", body_md=f"{CONTEXT} The question was: {QUESTION}")
            options = [cs.key("gb.council_tax.option", f"{day}:{pfa}:{position}") for position in range(2)]
            for position, (option, label) in enumerate(zip(options, ("Yes", "No"), strict=True)):
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=label,
                )
            result_set = cs.key("gb.council_tax.result_set", f"{day}:{pfa}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=sources["luton"],
                is_default=True,
                completeness=1,
            )
            for source in sorted(set(rows["source"])):
                cs.add("citation", entity_id=result_set, source_id=sources[source])
            first = rows.filter(pl.col("area") == pfa).row(0, named=True)
            parts = rows.filter(pl.col("area") != pfa)
            caveat = PARTIAL if parts.is_empty() else None
            if caveat is not None:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=caveat)
            for note, text in conn.execute(
                """select n.id, n.body_md from note n join changeset c on c.id = n.changeset_id
                   where n.entity_id = %s and n.kind = 'caveat' and c.pipeline = %s""",
                [result_set, SOURCE],
            ).fetchall():
                if text != caveat:
                    cs.add(
                        "note_drop",
                        note_id=note,
                        entity_id=result_set,
                        body_md=text,
                        reason="The caveat is reworded or no longer applies in this emit.",
                    )
            if not parts.is_empty():
                for field in ("yes", "no", "rejected"):
                    if sum(int(value) for value in parts[field]) != int(first[field]):
                        raise ValueError(f"{pfa} {field}: the council counts do not add up to the declared total")
            if int(first["yes"]) == int(first["no"]):
                raise ValueError("tied")
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[int(first["yes"]) < int(first["no"])],
                kind="won",
            )
            for row in (rows if parts.is_empty() else parts).iter_rows(named=True):
                for field in ("yes", "no", "rejected", "ballots"):
                    if row[field] is not None and row[field] not in printed(row["source"]):
                        raise ValueError(f"{row['area']} {field} {row[field]} not printed in {row['source']}")
                code = row["area"]
                if code in PFA_CODES:
                    unit = cs.unit("gb.pfa", code)
                else:
                    unit = cs.unit("gb.lad", code)
                    if unit in cs.minted:
                        council = cs.key("gb.polity", code)
                        found = conn.execute(
                            "select text from name where entity_id = %s and is_preferred", [council]
                        ).fetchone()
                        if found is None:
                            raise ValueError(f"{code}: the council has no name")
                        council_name = found[0]
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=council,
                            scheme_code="gb.lad",
                            code=code,
                            area_relation="equals",
                            area_id=cs.area("gb.lad", code),
                            kind="standard",
                            valid_from=None,
                        )
                        name(unit, council_name.removesuffix(" Council"))
                for option, measure, value in (
                    (options[0], VOTES, row["yes"]),
                    (options[1], VOTES, row["no"]),
                    (None, INVALID, row["rejected"]),
                    (None, BALLOTS, row["ballots"]),
                ):
                    if value is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=0,
                            value=int(value),
                        )
        return cs.write()
