import re
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, mn_sos, mn_sos_precincts
from ea.store import DATA, connect

SOURCE = "us.mn_leg_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OFFICES = {"senate": ("mnsen", "statesen"), "house": ("mnleg", "statehouse")}
FILE_2000 = mn_sos.MEDIA + "1545/2000_general_results.xls"
RENAMED_2000 = {"IND": "IP", "GREEN": "GP"}


def fetch() -> None:
    raw.fetch(SOURCE, FILE_2000, suffix=".xls")


def parse() -> None:
    rows = []
    for year, path in ({2000: None} | mn_sos.FILES).items():
        day = date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)
        cells = pl.read_excel(
            raw.cached(SOURCE, FILE_2000).path
            if path is None
            else raw.cached(mn_sos_precincts.SOURCE, mn_sos.MEDIA + path).path,
            sheet_name="full_00results" if path is None else "Results",
            engine="calamine",
            has_header=False,
            infer_schema_length=0,
        ).rows()
        start = 2 if year == 2002 else 0
        header = [re.sub(r"\s+", " ", str(cell)).strip() for cell in cells[start]]
        column = {name: index for index, name in enumerate(header)}
        fields = (
            {
                chamber: [
                    (RENAMED_2000.get(name.removesuffix(suffix), name.removesuffix(suffix)), index)
                    for index, name in enumerate(header)
                    if name.endswith(suffix)
                ]
                for chamber, suffix in (("senate", "_SEN"), ("house", "_HSE"))
            }
            if path is None
            else {
                chamber: [
                    (party, index)
                    for index, name in enumerate(header)
                    if (prefix := next((p for p in prefixes if name.lower().startswith(p)), None)) is not None
                    and (party := name[len(prefix) :].upper()) not in ("", "TOT")
                ]
                for chamber, prefixes in OFFICES.items()
            }
        )
        for row in cells[start + 1 :]:
            leg = str(row[column["LEG"]] or "")
            if row[0] is None or row[column["CG"]] is None or not re.fullmatch(r"\d\d[AB]", leg):
                continue
            precinct = re.sub(
                r"\s+",
                " ",
                " ".join(
                    str(row[column[name]])
                    for name in mn_sos_precincts.PLACES[year if path else 2002]
                    if row[column[name]] is not None
                ),
            ).strip()
            county = "27" + str(int(row[column[mn_sos_precincts.COUNTY[year if path else 2002]]]) * 2 - 1).zfill(3)
            for chamber, found in fields.items():
                district = f"{int(leg[:2])}-{leg[2].lower()}" if chamber == "house" else str(int(leg[:2]))
                rows.extend(
                    (year, f"{chamber}:{district}:g", day, f"{year}:MN:{county}::{precinct}", county, party,
                     party == "WI", int(float(str(row[index] or 0).strip() or 0)))
                    for party, index in found
                )  # fmt: skip
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "code": pl.String,
            "county": pl.String,
            "party": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    )
    if frame.select("chamber", "day", "code", "party").is_duplicated().any():
        raise ValueError("two precinct rows share a code")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    print(frame.group_by("year").agg(pl.col("chamber").n_unique(), pl.col("votes").sum()).sort("year"))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet").with_columns(
        key=pl.concat_str(pl.col("day").cast(pl.String), pl.lit(":MN:"), "chamber")
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's State Senate and House generals 2000–2010 by precinct from the Secretary of State's "
            "precinct results spreadsheets",
            polity="US-MN",
        )
        source = cs.source(mn_sos_precincts.PUBLISHER, mn_sos_precincts.TITLE)
        if source in cs.minted:
            raise ValueError("the precinct spreadsheets source must already exist")
        cs.artifact(raw.cached(SOURCE, FILE_2000), source)
        labels: dict[tuple[str, str], str | None] = {}
        for key, label, party in conn.execute(
            """select x.value, o.ballot_label, lower(n.text) from external_id x
               join contest_option o on o.contest_id = x.entity_id and o.parent_option_id is null and o.kind = 'candidate'
               left join name n on n.entity_id = o.party_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value = any(%s)""",
            [frame["key"].unique().to_list()],
        ).fetchall():
            for code, pattern in (mn_sos_precincts.PARTIES | {"DFL": "democratic"}).items():
                if pattern in party if party is not None else code in ("IND", "IP"):
                    labels[(key, code)] = None if (key, code) in labels else label
        frame = frame.with_columns(
            candidate=pl.when("writein")
            .then(pl.lit("WRITE-INS"))
            .otherwise(
                pl.struct("key", "party").map_elements(
                    lambda row: labels.get((row["key"], row["party"])) or row["party"], return_dtype=pl.String
                )
            )
        )
        for key, party, votes in (
            frame.filter(~pl.col("writein") & (pl.col("candidate") == pl.col("party")))
            .group_by("key", "party")
            .agg(pl.col("votes").sum())
            .filter(pl.col("votes") > 0)
            .sort("key")
            .rows()
        ):
            print(f"{key}: no option for the {party} column ({votes} votes)")
        medsl.attach(
            cs,
            conn,
            frame.select(
                "year", "chamber", "day", special=pl.lit(False), state=pl.lit("MN"), district=pl.lit(None, pl.Int16),
                code="code", candidate="candidate", measure=pl.lit(1, pl.Int16), writein="writein", votes="votes",
                kind=pl.lit("precinct"), source=pl.lit("MN"),
            ),
            {"MN": source},
            grain="precinct",
            strict=True,
            authoritative="Source: the Secretary of State's precinct results spreadsheets.",
        )  # fmt: skip
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        parents = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '27%%'"
            ).fetchall()
        }
        units = (
            frame.group_by("code")
            .agg(pl.col("county").first(), pl.col("day").first())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)) & pl.col("id").is_in([str(unit) for unit in cs.minted]))
        )
        cs.frame(
            "reporting_unit",
            units.select(
                "id",
                polity_id=pl.lit(str(cs.polity("US-MN"))),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("county").replace_strict({code: str(unit) for code, (unit, _) in parents.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("county").replace_strict({code: str(area) for code, (_, area) in parents.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{units.height} new precinct units")
        return cs.write()
