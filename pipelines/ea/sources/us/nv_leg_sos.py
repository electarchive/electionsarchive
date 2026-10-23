import re
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, nv_sos, openelections
from ea.store import DATA, connect

SOURCE = "us.nv_leg_sos"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OWN = {2000: "2000General"}
TITLE = re.compile(
    r"(?i)state (assembly|senate),? (?:(capital|central) senatorial district|(?:(carson city|clark|humboldt|washoe) )?"
    r"(?:district )?(\d+))"
)


def path(year: int, county: str) -> Path:
    if year in OWN:
        return raw.cached(SOURCE, f"{nv_sos.WAYBACK}{OWN[year]}/{county}.aspx").path
    return raw.cached(nv_sos.SOURCE, nv_sos.address(year, county)).path


def fetch() -> None:
    for folder in OWN.values():
        for county in nv_sos.COUNTIES:
            raw.fetch(SOURCE, f"{nv_sos.WAYBACK}{folder}/{county}.aspx", suffix=".html")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["NV"]
    rows = []
    for year in sorted({*OWN, *nv_sos.FOLDERS}):
        day = date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)
        for county in nv_sos.COUNTIES:
            words = nv_sos.tokens(path(year, county))
            starts = [index for index, word in enumerate(words) if "Precincts Reporting" in word]
            for position, start in enumerate(starts):
                found = TITLE.fullmatch(words[start - 1].strip())
                if found is None:
                    continue
                chamber = found[1].lower()
                if chamber == "assembly":
                    district = found[4]
                elif found[2]:
                    district = f"{found[2].lower()}-1"
                elif year >= 2012:
                    district = found[4]
                else:
                    district = f"{(found[3] or county).lower()}-{found[4]}"
                end = starts[position + 1] - 1 if position + 1 < len(starts) else len(words)
                held: list[str] = []
                index = start
                while index < end:
                    word = words[index]
                    if re.fullmatch(r"\d+(?:\.\d+)?%", word):
                        votes = int(words[index + 1].replace(",", ""))
                        held = [text for text in held if text != "Incumbent"]
                        if len(held) > 1 and re.fullmatch(r"[A-Z]{2,4}", held[-1]):
                            held = held[:-1]
                        name = held[-1]
                        if not re.match(r"(?i)(under|over) votes", name):
                            rows.append((day, chamber, district, fips[county.lower()], name.upper(), votes))
                        held = []
                        index += 2
                        continue
                    held.append(word)
                    index += 1
    frame = pl.DataFrame(
        rows,
        schema={
            "day": pl.Date,
            "chamber": pl.String,
            "district": pl.String,
            "code": pl.String,
            "candidate": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    print(frame.group_by("day", "chamber").agg(pl.col("district").n_unique()).sort("day", "chamber"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        keys: dict[tuple[str, str, str], list[str]] = {}
        for (value,) in conn.execute(
            """select value from external_id where scheme = 'us.contest'
               and value ~ '^20[01][0-9]-11-\\d\\d:NV:(assembly|senate):.*:g$'"""
        ).fetchall():
            day, _, chamber, *district, _ = value.split(":")
            name = district[0]
            keys.setdefault((day, chamber, name.rsplit("-", 1)[-1] if chamber == "assembly" else name), []).append(
                ":".join([chamber, *district, "g"])
            )
        frame = frame.with_columns(
            chamber=pl.struct("day", "chamber", "district").map_elements(
                lambda row: (
                    found[0]
                    if len(found := keys.get((str(row["day"]), row["chamber"], row["district"]), [])) == 1
                    else None
                ),
                return_dtype=pl.String,
            )
        )
        print(frame.filter(pl.col("chamber").is_null()).select("day", "district").unique().sort("day", "district"))
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Nevada's State Assembly and Senate generals by county from the Secretary of State's county results "
            "pages",
            polity="US-NV",
        )
        source = cs.source(nv_sos.PUBLISHER, nv_sos.TITLE)
        if source in cs.minted:
            raise ValueError("the county results pages source must already exist")
        for year in sorted({*OWN, *nv_sos.FOLDERS}):
            for county in nv_sos.COUNTIES:
                cs.artifact(
                    raw.cached(SOURCE, f"{nv_sos.WAYBACK}{OWN[year]}/{county}.aspx")
                    if year in OWN
                    else raw.cached(nv_sos.SOURCE, nv_sos.address(year, county)),
                    source,
                )
        medsl.attach(
            cs,
            conn,
            frame.filter(pl.col("chamber").is_not_null()).select(
                year=pl.col("day").dt.year().cast(pl.Int32), chamber="chamber", day="day", special=pl.lit(False),
                state=pl.lit("NV"), district=pl.lit(None, pl.Int16), code="code", kind=pl.lit("county"),
                candidate="candidate", measure=pl.lit(1, pl.Int16), writein=pl.lit(False), votes="votes",
                source=pl.lit("NV"),
            ),
            {"NV": source},
            strict=True,
        )  # fmt: skip
        return cs.write()
