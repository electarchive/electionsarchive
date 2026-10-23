import html
import io
import re
import zipfile
from datetime import date
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.sources.us.al_leg_2014 import LANDING, PUBLISHER
from ea.sources.us.al_sos import PRECINCTS, SITE
from ea.store import DATA, connect

SOURCE = "us.al_leg_generals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    2002: (date(2002, 11, 5), SITE + "2002-GeneralElection-PrecinctLevel_0.xls"),
    2010: (date(2010, 11, 2), SITE + PRECINCTS),
}
OFFICE_2002 = re.compile(r"STATE (HOUSE OF REPRESENTATIVES|SENATE), DISTRICT (\d+)")
OFFICE_2010 = re.compile(r"FOR STATE (REPRESENTATIVE|SENATOR), DISTRICT NO\. (\d+) \(Vote For 1\)")


def fetch() -> None:
    for _, url in FILES.values():
        raw.fetch(SOURCE, url, suffix=Path(url).suffix)


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '01%%'"""
            ).fetchall()
        }
    rows = []
    for county, frame in pl.read_excel(
        raw.cached(SOURCE, FILES[2002][1]).path, sheet_id=0, engine="calamine", has_header=False, infer_schema_length=0
    ).items():
        cells = frame.rows()
        if cells[0][0] == "COUNTY CODE":
            cells = list(zip(*cells, strict=True))
        for row in cells:
            found = OFFICE_2002.fullmatch(row[2] or "")
            if found is None:
                continue
            parts = [part.strip() for part in row[4].split(",")]
            suffix = [part for part in parts if re.fullmatch(r"(?:JR|SR|II|III|IV)\.?", part, re.IGNORECASE)]
            rest = [part for part in parts if part not in suffix]
            rows.append(
                (
                    2002,
                    f"{'house' if found[1] == 'HOUSE OF REPRESENTATIVES' else 'senate'}:{int(found[2])}:g",
                    counties[re.sub(r"[^a-z]", "", county.lower())],
                    "WRITE-INS" if row[3] == "WI" else " ".join(reversed(rest) if len(rest) == 2 else rest),
                    row[3] == "WI",
                    int(float(row[5] or 0)),
                )
            )
    with zipfile.ZipFile(raw.cached(SOURCE, FILES[2010][1]).path) as archive:
        for member in archive.namelist():
            code = counties[re.sub(r"[^a-z]", "", member.removesuffix(".xls").lower())]
            data = archive.read(member)
            if data.startswith(bytes.fromhex("d0cf11e0")):
                cells = [
                    [str(cell).strip() for cell in row if cell is not None and str(cell).strip()]
                    for frame in pl.read_excel(
                        io.BytesIO(data), sheet_id=0, engine="calamine", has_header=False, infer_schema_length=0
                    ).values()
                    for row in frame.rows()
                ]
            else:
                cells = [
                    [
                        html.unescape(cell).strip()
                        for cell in re.findall(r"<s:Data[^>]*>([^<]*)</s:Data>", row)
                        if cell.strip()
                    ]
                    for row in re.findall(r"<s:Row[^>]*>(.*?)</s:Row>", data.decode("utf-8"), re.DOTALL)
                ]
            for k, row in enumerate(cells):
                found = OFFICE_2010.fullmatch(row[0]) if len(row) == 1 else None
                if (
                    found is None
                    or k + 2 >= len(cells)
                    or cells[k + 2][:1] not in (["Precinct"], ["Voting Center"], ["Voting Centers"])
                ):
                    continue
                names = cells[k + 1]
                chamber = f"{'house' if found[1] == 'REPRESENTATIVE' else 'senate'}:{int(found[2])}:g"
                header = cells[k + 2]
                columns = [column for column, cell in enumerate(header) if cell == "Total Votes"]
                if len(columns) != len(names) or header[-1] != "Total":
                    raise ValueError(f"{member} {chamber}: {header} against {names}")
                sums = [0] * len(names)
                for line in cells[k + 3 :]:
                    numbers = [int(float(cell)) for cell in line[1:]]
                    votes = [numbers[column - 1] for column in columns]
                    if len(line) != len(header) or sum(votes) != numbers[-1]:
                        raise ValueError(f"{member} {chamber}: {line} against {header}")
                    if line[0] == "Totals:":
                        if votes != sums:
                            raise ValueError(f"{member} {chamber}: precincts sum to {sums}, printed {votes}")
                        break
                    sums = [total + count for total, count in zip(sums, votes, strict=True)]
                rows.extend(
                    (2010, chamber, code, name, name.upper().startswith("WRITE"), votes)
                    for name, votes in zip(names, sums, strict=True)
                )
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "code": pl.String,
            "candidate": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    )
    print(frame.group_by("year").agg(pl.col("chamber").n_unique()).sort("year"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.group_by("year", "chamber", "code", "candidate", "writein").agg(pl.col("votes").sum()).with_columns(
        candidate=pl.col("candidate").str.to_uppercase().str.replace(r",?\s+(JR|SR|II|III|IV)\.?$", ""),
        day=pl.col("year").replace_strict({year: day for year, (day, _) in FILES.items()}, return_dtype=pl.Date),
        district=pl.lit(None, pl.Int16),
        special=pl.lit(False),
        state=pl.lit("AL"),
        measure=pl.lit(1, pl.Int16),
        kind=pl.lit("county"),
        source=pl.col("year").cast(pl.String),
    ).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alabama's 2002 and 2010 legislative generals by county from the Secretary of State's "
            "precinct-level workbooks, for the contests without a complete county or precinct set",
            polity="US-AL",
        )
        sources = {}
        for year, (_, url) in FILES.items():
            title = f"{year} general election: precinct-level results"
            sources[str(year)] = cs.source(PUBLISHER, title)
            if sources[str(year)] in cs.minted:
                cs.add(
                    "source",
                    id=sources[str(year)],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title,
                    url=LANDING,
                    license="Public record",
                )
            cs.artifact(raw.cached(SOURCE, url), sources[str(year)])
        covered = [
            key
            for (key,) in conn.execute(
                """select x.value from external_id x
                   join result_set rs on rs.contest_id = x.entity_id and rs.status = 'active'
                   join changeset c on c.id = rs.changeset_id
                   where x.value ~ '^(2002-11-05|2010-11-02):AL:(house|senate):\\d+:g$'
                     and c.pipeline not in ('us.klarner', %s) and coalesce(rs.completeness, 1) >= 0.98""",
                [SOURCE],
            ).fetchall()
        ]
        frame = pl.read_parquet(PARSED / "counties.parquet").filter(
            ~pl.format("{}:AL:{}", pl.col("day"), pl.col("chamber")).is_in(covered)
        )
        print(frame.select("day", "chamber").unique().sort("day", "chamber").rows())
        medsl.attach(cs, conn, frame, sources, strict=True)
        return cs.write()
