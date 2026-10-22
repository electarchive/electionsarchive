import re
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, mn_sos
from ea.store import DATA, connect

SOURCE = "us.mn_sos_precincts"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PLACES = {
    2002: ["MCD NAME", "WD", "PRECINCT NAME"],
    2004: ["McdName", "WD", "PrecinctName"],
    2006: ["MCDName", "WD", "Precinct Name"],
    2008: ["MCDName", "Precinct Name"],
    2010: ["Precinct Name"],
}
COUNTY = {2002: "CC", 2004: "CC", 2006: "County_ID", 2008: "CountyID", 2010: "CountyID"}
OFFICES = {
    "house": ("Cong", "USCong", "CONG"),
    "senate": ("USSen", "USSEN"),
    "president": ("USPres", "USPRES"),
}
PARTIES = {
    "GP": "green",
    "GR": "green",
    "IP": "independence",
    "I": "independence",
    "R": "republican",
    "DFL": "farmer",
    "NNT": "no new taxes",
    "CP": "constitution",
    "C": "constitution",
    "L": "libertarian",
    "LIB": "libertarian",
    "SW": "socialist workers",
    "SWP": "socialist workers",
    "SE": "socialist equality",
    "CF": "christian freedom",
    "BL": "better life",
    "IND": "independent",
}
PUBLISHER = "Minnesota Secretary of State"
TITLE = "Precinct results spreadsheets"


def fetch() -> None:
    for path in mn_sos.FILES.values():
        cached = raw.cached(mn_sos.SOURCE, mn_sos.MEDIA + path)
        raw.store(
            SOURCE,
            mn_sos.MEDIA + path,
            cached.path.read_bytes(),
            suffix=".xls",
            retrieved_at=cached.retrieved_at,
            media_type="application/vnd.ms-excel",
        )


def parse() -> None:
    rows = []
    residuals: dict[tuple[int, str, int | None], int] = {}
    for year, path in mn_sos.FILES.items():
        day = date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7)
        cells = pl.read_excel(
            raw.cached(SOURCE, mn_sos.MEDIA + path).path,
            sheet_name="Results",
            engine="calamine",
            has_header=False,
            infer_schema_length=0,
        ).rows()
        start = 2 if year == 2002 else 0
        header = [re.sub(r"\s+", " ", str(cell)).strip() for cell in cells[start]]
        column = {name: index for index, name in enumerate(header)}
        fields = {
            chamber: [
                (name, index)
                for index, name in enumerate(header)
                if (prefix := next((p for p in prefixes if name.startswith(p)), None)) is not None
                and name.removeprefix(prefix).upper() in {*PARTIES, "WI", "TOT"}
            ]
            + ([("Wellstone", column["Wellstone"])] if chamber == "senate" and year == 2002 else [])
            for chamber, prefixes in OFFICES.items()
        }
        for row in cells[start + 1 :]:
            if row[0] is None or row[0] == cells[start][0] or re.match(r"(?i)(statewide )?totals?\b", str(row[0])):
                continue
            precinct = re.sub(
                r"\s+", " ", " ".join(str(row[column[name]]) for name in PLACES[year] if row[column[name]] is not None)
            ).strip()
            aggregate = row[column["CG"]] is None
            county = None if aggregate else "27" + str(int(row[column[COUNTY[year]]]) * 2 - 1).zfill(3)
            code = f"MN:{year}:{precinct}" if aggregate else f"{year}:MN:{county}::{precinct}"
            for chamber, found in fields.items():
                if not found or (aggregate and chamber == "house"):
                    continue
                district = int(row[column["CG"]]) if chamber == "house" else None
                total = None
                counted = 0
                for name, index in found:
                    votes = int(float(row[index] or 0))
                    party = re.sub(r"^(Cong|USCong|CONG|USSen|USSEN|USPres|USPRES)", "", name).upper()
                    if party == "TOT":
                        total = votes
                        continue
                    counted += votes
                    rows.append(
                        (year, chamber, day, district, code, county, "aggregate" if aggregate else "precinct",
                         "WELLSTONE" if name == "Wellstone" else party, party == "WI", votes)
                    )  # fmt: skip
                if total is not None:
                    if total < counted:
                        raise ValueError(f"{code} {chamber}: {counted} counted, {total} total")
                    residuals[(year, chamber, district)] = residuals.get((year, chamber, district), 0) + total - counted
    for (year, chamber, district), votes in sorted(residuals.items(), key=lambda item: str(item[0])):
        if votes:
            print(f"{year} {chamber} {district}: {votes} votes in the total column only")
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "chamber": pl.String,
            "day": pl.Date,
            "district": pl.Int16,
            "code": pl.String,
            "county": pl.String,
            "kind": pl.String,
            "party": pl.String,
            "writein": pl.Boolean,
            "votes": pl.Int64,
        },
        orient="row",
    )
    if frame.select("year", "chamber", "code", "party").is_duplicated().any():
        raise ValueError("two precinct rows share a code")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "precincts.parquet")
    print(
        frame.group_by("year", "chamber")
        .agg(pl.col("district").n_unique(), pl.col("votes").sum())
        .sort("year", "chamber")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Minnesota's U.S. House generals 2002–2010, and its U.S. Senate and presidential generals of those "
            "years, by precinct from the Secretary of State's precinct results spreadsheets",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://www.sos.state.mn.us/elections-voting/election-results/",
                license="Public record",
            )
        for path in mn_sos.FILES.values():
            cs.artifact(raw.cached(SOURCE, mn_sos.MEDIA + path), source)
        frame = frame.with_columns(
            key=pl.when(pl.col("chamber") == "house")
            .then(
                pl.concat_str(pl.col("day").cast(pl.String), pl.lit(":MN:house:"), pl.col("district").cast(pl.String))
            )
            .otherwise(pl.concat_str(pl.col("day").cast(pl.String), pl.lit(":MN:"), "chamber"))
        )
        labels: dict[tuple[str, str], str | None] = {}
        for key, label, party in conn.execute(
            """select x.value, o.ballot_label, lower(n.text) from external_id x
               join contest_option o on o.contest_id = x.entity_id and o.parent_option_id is null and o.kind in ('candidate', 'ticket')
               left join name n on n.entity_id = o.party_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value = any(%s)""",
            [frame["key"].unique().to_list()],
        ).fetchall():
            for code, pattern in PARTIES.items():
                if (pattern in party if party is not None else code == "IND") and label != "Paul Wellstone":
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
        unmapped = frame.filter(~pl.col("writein") & (pl.col("candidate") == pl.col("party")))
        for key, party, votes in (
            unmapped.group_by("key", "party").agg(pl.col("votes").sum()).filter(pl.col("votes") > 0).sort("key").rows()
        ):
            print(f"{key}: no option for the {party} column ({votes} votes)")
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status = 'active'
                   join changeset g on g.id = r.changeset_id and g.pipeline <> %s
                   join result_value v on v.result_set_k = r.k
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
                   where x.scheme = 'us.contest' and x.value = any(%s)""",
                [SOURCE, frame["key"].unique().to_list()],
            ).fetchall()
        }
        precincts = (
            frame.filter(~pl.col("key").is_in(list(filled)))
            .with_columns(special=pl.lit(False), state=pl.lit("MN"), measure=pl.lit(1, pl.Int16))
            .with_columns(source=pl.col("state"))
        )
        medsl.attach(
            cs,
            conn,
            precincts.select(
                "year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure", "writein",
                "votes", "kind", "source",
            ),
            {"MN": source},
            grain="precinct",
            strict=True,
        )  # fmt: skip
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        parents = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '27%%'"
            ).fetchall()
        }
        state, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and code = '27'"
        ).fetchone()
        for code in precincts.filter(pl.col("kind") == "aggregate")["code"].unique().to_list():
            unit = cs.unit("us.aggregate", code)
            if str(unit) in used and unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=cs.polity("US-MN"),
                    scheme_code="us.aggregate",
                    code=code,
                    parent_id=state,
                    area_relation="within",
                    area_id=area,
                    kind="aggregate",
                    valid_from=None,
                    valid_to=None,
                )
        units = (
            precincts.filter(pl.col("kind") == "precinct")
            .group_by("code")
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
