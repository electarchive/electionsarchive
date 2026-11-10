import html
import re
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

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
    "bedford": (
        (
            "https://web.archive.org/web/20210418084211/https://www.bedford.gov.uk/council-and-democracy/"
            "elections-and-voting/election-results/police-commissioner-referendum/"
        ),
        "Bedford Borough Council",
        "Police Commissioner Referendum: declaration of count totals, 7 May 2015",
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
    "The Chief Counting Officer's notice gives one combined count for the three council areas. Bedford Borough's "
    "and Central Bedfordshire's own counts are in a separate result set by council; Luton Borough Council has not "
    "published its own count."
)
COUNCILS = {"E06000032", "E06000055", "E06000056"}
INBOX = {"bedford": "MF-499/bedford-pcc-council-tax-referendum-2015-declaration-of-count-totals.html"}


def fetch() -> None:
    for key, (url, *_) in SOURCES.items():
        if key not in INBOX:
            raw.fetch(SOURCE, url, suffix=".html")
            continue
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            path = DATA / "inbox" / "manual" / INBOX[key]
            raw.store(
                SOURCE,
                url,
                path.read_bytes(),
                suffix=".html",
                retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC),
                media_type="text/html",
            )


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

        def add_values(result_set: UUID, options: list[UUID], row: dict[str, str | None]) -> None:
            for field in ("yes", "no", "rejected", "ballots"):
                if row[field] is not None and row[field] not in printed(str(row["source"])):
                    raise ValueError(f"{row['area']} {field} {row[field]} not printed in {row['source']}")
            code = str(row["area"])
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
                    name(unit, found[0].removesuffix(" Council"))
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
            first = rows.filter(pl.col("area") == pfa).row(0, named=True)
            parts = rows.filter(pl.col("area") != pfa)
            for field in ("yes", "no", "rejected", "ballots"):
                printed_parts = [int(value) for value in parts[field] if value is not None]
                if sum(printed_parts) > int(first[field]) or (
                    len(printed_parts) == len(COUNCILS) and sum(printed_parts) != int(first[field])
                ):
                    raise ValueError(f"{pfa} {field}: the council counts do not add up to the declared total")
            if int(first["yes"]) == int(first["no"]):
                raise ValueError("tied")
            complete = set(parts["area"]) == COUNCILS and not any(parts[f].null_count() for f in parts.columns)
            whole = rows.filter(pl.col("area") == pfa)
            sets = {cs.key("gb.council_tax.result_set", f"{day}:{pfa}"): parts if complete else whole}
            if not parts.is_empty() and not complete:
                sets[cs.key("gb.council_tax.result_set", f"{day}:{pfa}:council")] = parts
            for result_set, units in sets.items():
                default = result_set == next(iter(sets))
                cited = sorted(set((rows if complete else units)["source"]))
                valid = sum(int(row["yes"]) + int(row["no"]) for row in units.iter_rows(named=True))
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources[first["source"] if default else cited[0]],
                    is_default=default,
                    completeness=1 if default else round(valid / (int(first["yes"]) + int(first["no"])), 4),
                )
                for source in cited:
                    cs.add("citation", entity_id=result_set, source_id=sources[source])
                caveat = PARTIAL if default and not parts.is_empty() and not complete else None
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
                if default:
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options[int(first["yes"]) < int(first["no"])],
                        kind="won",
                    )
                for row in units.iter_rows(named=True):
                    add_values(result_set, options, row)
        return cs.write()
