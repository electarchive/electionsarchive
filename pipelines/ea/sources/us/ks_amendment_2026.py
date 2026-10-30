import re
import subprocess
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import oe_primaries_2026 as oe
from ea.store import connect

SOURCE = "us.ks_amendment_2026"
VERSION = "1"
BASE = "https://sos.ks.gov/elections/26elec/"
COUNTIES = BASE + "2026-Primary-Election-Constitutional-Amendment-Precinct-Level-Results.xlsx"
TOTALS = BASE + "2026-Primary-Election-Official-Vote-Totals.pdf"
PAGE = "https://sos.ks.gov/elections/election-results.html"
PUBLISHER = "Kansas Secretary of State"
TITLE = "Kansas August 4, 2026 primary election results"
CONTEST_KEY = "2026-08-04:KS:question:elections-for-supreme-court-justices-amendment"


def fetch() -> None:
    raw.fetch(SOURCE, COUNTIES, suffix=".xlsx")
    raw.fetch(SOURCE, TOTALS, suffix=".pdf")


def statewide() -> dict[str, int]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, TOTALS).path), "-"], capture_output=True, text=True, check=True
    ).stdout
    block = text.partition("Constitutional Amendment\n")[2]
    found = {answer: int(votes.replace(",", "")) for answer, votes in re.findall(r'"(YES|NO)"\s+([\d,]+)', block)}
    if set(found) != {"YES", "NO"}:
        raise ValueError(f"amendment totals not found: {found}")
    return found


def emit() -> Path:
    frame = (
        pl.read_excel(raw.cached(SOURCE, COUNTIES).path)
        .with_columns(
            pl.col("Candidate").str.extract(r'"(YES|NO)"', 1).alias("answer"),
        )
        .group_by("County", "answer")
        .agg(pl.col("Votes").sum().alias("votes"))
    )
    totals = statewide()
    sums = {answer: frame.filter(pl.col("answer") == answer)["votes"].sum() for answer in totals}
    if any(sums[answer] > totals[answer] for answer in totals):
        raise ValueError(f"county sums exceed the statewide totals {sums} {totals}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas's August 4, 2026 constitutional amendment on electing Supreme Court justices: the official statewide totals and the county totals in the precinct file",
            polity="US-KS",
        )
        election = conn.execute(
            "select entity_id from external_id where scheme = 'us.election' and value = '2026-08-04:KS:primary'"
        ).fetchone()[0]
        area = conn.execute(
            """select u.area_id from reporting_unit u where u.scheme_code = 'us.state' and u.code = '20'"""
        ).fetchone()[0]
        system = cs.key("us.electoral_system", "referendum")
        state_unit = conn.execute("select id from reporting_unit where scheme_code = 'us.state' and code = '20'").fetchone()[0]
        counties: dict[str, UUID] = {}
        for text, id in conn.execute(
            """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code ~ '^20'"""
        ).fetchall():
            counties[oe.plain(text)] = id
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=PAGE, license="Public record")
        for url in (COUNTIES, TOTALS):
            cs.artifact(raw.cached(SOURCE, url), source)
        contest = cs.key("us.contest", CONTEST_KEY)
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="ballot_question",
            district_area_id=area,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
        cs.add(
            "name",
            entity_id=contest,
            text="Constitutional amendment on electing Kansas Supreme Court justices",
            lang="en",
            kind="official",
            is_preferred=True,
        )
        if contest in cs.minted:
            cs.add("slug", path="us/ks/2026-08-04-primary/elections-for-supreme-court-justices-amendment", entity_id=contest)
        cs.add("citation", entity_id=contest, source_id=source)
        options = {}
        for position, answer in enumerate(("YES", "NO")):
            options[answer] = cs.key("us.option", f"{CONTEST_KEY}:{answer.lower()}")
            cs.add(
                "contest_option",
                id=options[answer],
                contest_id=contest,
                kind="answer",
                ballot_position=position + 1,
                ballot_label=answer.capitalize(),
            )
        official = cs.key(f"{SOURCE}.result_set", f"{CONTEST_KEY}:official")
        by_county = cs.key(f"{SOURCE}.result_set", f"{CONTEST_KEY}:county")
        for id, default, completeness in ((official, True, 1), (by_county, False, round(sum(sums.values()) / sum(totals.values()), 4))):
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=default,
                completeness=completeness,
            )
        cs.add(
            "note",
            entity_id=by_county,
            kind="caveat",
            body_md=f"County totals for {frame['County'].n_unique()} of Kansas's 105 counties; the Secretary of State's precinct file omits the rest, so the county sums are below the official statewide totals.",
        )
        cs.add("outcome", result_set_id=official, option_id=options["NO" if totals["NO"] > totals["YES"] else "YES"], kind="won")
        rows = [(str(official), str(state_unit), str(options[answer]), 0, 1, 0, 0, votes) for answer, votes in totals.items()]
        rows.append((str(official), str(state_unit), None, 0, 4, 0, 0, sum(totals.values())))
        place: dict[str, int] = {}
        for county, answer, votes in frame.select("County", "answer", "votes").iter_rows():
            unit = counties[oe.plain(county)]
            place[str(unit)] = place.get(str(unit), 0) + votes
            rows.append((str(by_county), str(unit), str(options[answer]), 0, 1, 0, 0, votes))
        rows += [(str(by_county), unit, None, 0, 4, 0, 0, votes) for unit, votes in place.items()]
        cs.frame(
            "result_value",
            pl.DataFrame(
                rows,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ),
        )
        print(totals, sums)
        return cs.write()
