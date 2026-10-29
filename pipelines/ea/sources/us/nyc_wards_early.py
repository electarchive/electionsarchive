import json
import re
from datetime import timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import nyc_early_mayors as mayors
from ea.store import DATA, connect

SOURCE = "us.nyc_wards_early"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = (1859, 1861, 1870)
TITLE = "New York City mayoral election articles, ward tables"
NOTE = (
    "Ward tables from the English Wikipedia's articles on the 1859, 1861, and 1870 New York City mayoral elections. "
    "The 1859 and 1861 tables cite the Tribune Almanac (HathiTrust); the 1870 table cites the 1870 Manual of the "
    "Corporation."
)
METHOD = (
    "Votes by ward from the ward table in the English Wikipedia's article on the {year} New York City mayoral election. "
    "Each ward's candidate votes add up to its printed total, and each candidate's ward votes add up to the citywide "
    "total in the default set. Wards have no shapes yet; the city was Manhattan's 22 wards."
)


def parse() -> None:
    lines = pl.read_parquet(raw.parsed(mayors.SOURCE) / "lines.parquet")
    rows: list[dict[str, object]] = []
    for year in YEARS:
        wikitext = json.loads(raw.cached(SOURCE, mayors.page(year)).path.read_text())["query"]["pages"][0][
            "revisions"
        ][0]["slots"]["main"]["content"]
        table = next(
            match.group(0)
            for match in re.finditer(r"\{\|.*?\n\|\}", wikitext, re.DOTALL)
            if re.search(r"Results by ward", match.group(0))
        )
        names = [
            re.sub(r"\s+", " ", re.sub(r"<br\s*/?>", " ", re.sub(r"^\s*colspan=\d\|", "", head))).strip()
            for head in re.split(r"!!", re.search(r"! rowspan=2\| Ward(.*?)\n", table).group(1))
            if "Total" not in head
        ]
        names = [name for name in names if name]
        citywide = lines.filter((pl.col("year") == year) & (pl.col("stage") == "general") & (pl.col("office") == "mayor"))
        candidates = []
        for header in names:
            surname = header.split()[0].lower()
            (found,) = [c for c in citywide["candidate"] if c.split()[-1].lower() == surname]
            candidates.append(found)
        wards = []
        for chunk in re.split(r"\n\|-[^\n]*", table):
            match = re.match(r"\s*!\s*(\d+)\s*\n(.*)", chunk, re.DOTALL)
            if match is None:
                continue
            body = re.sub(r"\{\{[^}]*\}\}\|?", "", match.group(2)).replace("'''", "")
            numbers = [
                int(number.replace(",", ""))
                for number in re.findall(r"(?<![\d.,])\d[\d,]*(?![\d,.]*%)(?!\d)", body)
            ]
            if len(numbers) != len(candidates) + 1 or sum(numbers[:-1]) != numbers[-1]:
                raise ValueError(f"{year} ward {match.group(1)} does not add up: {numbers}")
            wards.append((int(match.group(1)), numbers))
        if [ward for ward, _ in wards] != list(range(1, 23)):
            raise ValueError(f"{year} wards are {[ward for ward, _ in wards]}")
        for position, candidate in enumerate(candidates):
            total = citywide.filter(pl.col("candidate") == candidate)["votes"].sum()
            if sum(numbers[position] for _, numbers in wards) != total:
                raise ValueError(f"{year} {candidate} wards add up to a different total than {total}")
            rows.extend(
                {"year": year, "ward": ward, "candidate": candidate, "votes": numbers[position]}
                for ward, numbers in wards
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "wards.parquet")


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, mayors.page(year), suffix=".json")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "wards.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New York City mayoral elections of 1859, 1861, and 1870 by ward, from the English Wikipedia's ward tables",
            polity="US",
        )
        source = cs.source("Wikipedia", TITLE)
        cs.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=TITLE,
            url="https://en.wikipedia.org/wiki/1859_New_York_City_mayoral_election",
            license="CC BY-SA 4.0",
            notes=NOTE,
        )
        ((polity,),) = conn.execute(
            "select o.polity_id from office o join external_id x on x.entity_id = o.id "
            "where x.scheme = 'us.office' and x.value = 'NY:3651000:mayor'"
        ).fetchall()
        if conn.execute("select 1 from scheme where code = 'us.nyc_ward'").fetchone() is None:
            cs.add("scheme", code="us.nyc_ward", polity_id=polity, kind="precinct", rank=1, noun="ward")
        added: set[object] = set()
        county = cs.unit("us.county", "36061")
        for year in YEARS:
            cs.artifact(raw.cached(SOURCE, mayors.page(year)), source)
            day = mayors.EARLY[year][0]
            contest_key = f"nyc_early_mayors:{day}:NY:3651000:mayor"
            contest = cs.key("us.contest", contest_key)
            election = cs.key("us.election", f"{day}:NY:3651000")
            result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:wards")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD.format(year=year))
            for ward, candidate, votes in frame.filter(pl.col("year") == year).select("ward", "candidate", "votes").iter_rows():
                code = f"36061:{ward:02d}"
                unit = cs.unit("us.nyc_ward", code, day)
                if unit in cs.minted and unit not in added:
                    added.add(unit)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="us.nyc_ward",
                        code=code,
                        parent_id=county,
                        area_relation="unlocated",
                        area_id=None,
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=cs.key("us.option", f"{contest_key}:{slug(candidate)}"),
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        return cs.write()
