import re
import subprocess
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, openelections
from ea.store import DATA, connect

SOURCE = "us.ca_leg_2000"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
SITE = "https://elections.cdn.sos.ca.gov/sov/2000-general/"
FILES = {"assemb.pdf": "assembly", "sen.pdf": "senate"}
DAY = date(2000, 11, 7)
HEADER = re.compile(r"^\s*(\d+)(?:st|nd|rd|th) (?:State Senate|Assembly) District\s*$")
PARTIES = re.compile(r"^\s*(?:[A-Z]{2,3}(?: \(W/I\))?\s+)*[A-Z]{2,3}(?: \(W/I\))?\s*$")
ROW = re.compile(r"^(\S.*?)\s{2,}((?:[\d,]+\s+)*[\d,]+)\s*$")
PARTIES_BY_CODE = {
    "DEM": "democratic",
    "REP": "republican",
    "LIB": "libertarian",
    "NL": "natural law",
    "REF": "reform",
    "GRN": "green",
    "AI": "american independent",
}
CANVASS = {
    "assembly:28:g": "The Statement of Vote gives John Vogel (Reform) 2,038 votes; the earlier stored total was 2,891.",
    "assembly:78:g": "The Statement of Vote gives Dennis Triglia (Libertarian) 5,978 votes; the earlier stored total was "
    "35,978.",
    "senate:5:g": "The Statement of Vote gives William S. Nicolas (Natural Law) 2,667 votes; the earlier stored total "
    "was 2,267.",
}
PUBLISHER = "California Secretary of State"
TITLE = "Statement of Vote, November 7, 2000 General Election"


def fetch() -> None:
    for name in FILES:
        raw.fetch(SOURCE, SITE + name, suffix=".pdf")


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["CA"]
    rows = []
    for name, chamber in FILES.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, SITE + name).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        district, codes = 1, None
        for line in text.splitlines():
            if found := HEADER.match(line):
                district, codes = int(found[1]), None
            elif PARTIES.match(line):
                codes = re.findall(r"[A-Z]{2,3}(?: \(W/I\))?", line)
            elif codes is not None and (found := ROW.match(line)):
                label = found[1].strip()
                votes = [int(cell.replace(",", "")) for cell in found[2].split()][: len(codes)]
                if label == "District Totals":
                    rows.extend(
                        (
                            chamber,
                            district,
                            "total",
                            f"X{index}",
                            codes[index][:3].strip(),
                            codes[index].endswith("(W/I)"),
                            count,
                        )
                        for index, count in enumerate(votes)
                    )
                    district, codes = district + (2 if chamber == "senate" else 1), None
                else:
                    code = fips[re.sub(r"[^a-z]", "", label.lower())]
                    rows.extend(
                        (
                            chamber,
                            district,
                            code,
                            f"X{index}",
                            codes[index][:3].strip(),
                            codes[index].endswith("(W/I)"),
                            count,
                        )
                        for index, count in enumerate(votes)
                    )
    frame = pl.DataFrame(
        rows,
        schema={
            "chamber": pl.String,
            "district": pl.Int32,
            "code": pl.String,
            "candidate": pl.String,
            "party": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    )
    totals = frame.filter(pl.col("code") == "total")
    frame = frame.filter(pl.col("code") != "total")
    check = (
        frame.group_by("chamber", "district", "candidate", "party", "writein")
        .agg(pl.col("votes").sum())
        .join(totals, on=["chamber", "district", "candidate", "party", "writein"], how="full", coalesce=True)
    )
    print(check.filter(pl.col("votes") != pl.col("votes_right")))
    print(frame.group_by("chamber").agg(pl.col("district").n_unique()))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.select(
        year=pl.lit(DAY.year, pl.Int32),
        chamber=pl.concat_str("chamber", pl.col("district").cast(pl.String), pl.lit("g"), separator=":"),
        day=pl.lit(DAY),
        special=pl.lit(False),
        state=pl.lit("CA"),
        district=pl.lit(None, pl.Int16),
        code="code",
        kind=pl.lit("county"),
        candidate="candidate",
        party="party",
        measure=pl.lit(1, pl.Int16),
        writein="writein",
        votes="votes",
        source=pl.lit("CA"),
    ).write_parquet(PARSED / "lines.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet").with_columns(
        key=pl.concat_str(pl.col("day").cast(pl.String), pl.lit(":CA:"), "chamber")
    )
    with connect() as conn:
        labels: dict[tuple[str, str], str | None] = {}
        for key, label, party in conn.execute(
            """select x.value, o.ballot_label, lower(n.text) from external_id x
               join contest_option o on o.contest_id = x.entity_id and o.parent_option_id is null and o.kind = 'candidate'
               left join name n on n.entity_id = o.party_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value = any(%s)""",
            [frame["key"].unique().to_list()],
        ).fetchall():
            for code, pattern in PARTIES_BY_CODE.items():
                if party is not None and pattern in party:
                    labels[(key, code)] = None if (key, code) in labels else label
        frame = frame.with_columns(
            candidate=pl.struct("key", "party", "candidate").map_elements(
                lambda row: labels.get((row["key"], row["party"])) or row["candidate"], return_dtype=pl.String
            )
        ).drop("key", "party")
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's 2000 State Assembly and Senate generals by county from the Secretary of State's "
            "Statement of Vote",
            polity="US-CA",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=SITE + "contents.htm",
                license="Public record",
            )
        for name in FILES:
            cs.artifact(raw.cached(SOURCE, SITE + name), source)
        for strict in (True, False):
            medsl.attach(
                cs,
                conn,
                frame.filter(pl.col("chamber").is_in(["assembly:28:g", "assembly:78:g"]) != strict),
                {"CA": source},
                strict=strict,
                authoritative="Source: the Secretary of State's Statement of Vote, the certified canvass.",
            )
        for chamber, body in CANVASS.items():
            cs.add(
                "note",
                entity_id=cs.key(f"{SOURCE}.result_set", f"{DAY}:CA:{chamber}:county"),
                kind="caveat",
                body_md=body,
            )
        return cs.write()
