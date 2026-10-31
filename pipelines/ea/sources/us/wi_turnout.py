import re
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import openelections
from ea.store import DATA, connect

SOURCE = "us.wi_turnout"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Wisconsin Elections Commission"
TITLE = "Election Voting and Registration Statistics Report"
COLUMNS = {
    "ballots": ("totalballots",),
    "voters": ("totalelectors", "totalvoters"),
    "registrants": ("registrants", "earlyregistrants", "openregistrants"),
    "late": ("lateregistrants", "latereg"),
    "edr": ("edrs", "electiondayregistrants"),
}
SITE = "https://elections.wi.gov/sites/"
NOTE = "Registered voters is our sum of three columns of the same report: registrants (early registrants), late registrants, and election day registrants."
FILES = {
    date(2008, 11, 4): "20230316065807 default/files/legacy/publication/65/2008nov_gab190_statistics_xls_13418.xls",
    date(2009, 4, 7): "20230316065744 default/files/legacy/publication/65/20090407_gab190_statistics_xlsx_93580.xlsx",
    date(2010, 4, 6): "20230316065737 default/files/legacy/publication/65/20100406_gab190_statistics_xlsx_36847.xlsx",
    date(2010, 9, 14): "20230316065729 default/files/legacy/publication/65/20100914_gab190_statistics_xlsx_19510.xlsx",
    date(2010, 11, 2): "20230316065654 default/files/legacy/publication/65/20101102_general_gab190_xls_40096.xls",
    date(2011, 4, 5): "20230316065647 default/files/legacy/publication/65/20110405_gab190_statistics_xlsx_19647.xlsx",
    date(2012, 4, 3): "20230316065631 default/files/legacy/publication/65/20120403_gab190_statistics_xlsx_46511.xlsx",
    date(2012, 8, 14): "20230316065620 default/files/legacy/publication/65/20120814_gab190_statistics_xls_80204.xls",
    date(2012, 11, 6): "20230316065608 default/files/legacy/publication/65/20121106_gab190_statistics_xls_14031.xls",
    date(2013, 4, 2): "20230316065541 default/files/legacy/publication/65/20130402_gab190_statistics_xls_17167.xls",
    date(
        2014, 4, 1
    ): "20201019114155 elections.wi.gov/files/publication/65/gab_190nf_2014springelection_xlsx_19854.xlsx",
    date(2014, 8, 12): "20201019202158 elections.wi.gov/files/publication/65/20140812_gab190f_xlsx_42687.xlsx",
    date(
        2014, 11, 4
    ): "20230316065451 default/files/legacy/publication/65/2014_general_election_gab190f_20150226_xlsx_21131.xlsx",
    date(
        2015, 4, 7
    ): "20230316065436 default/files/legacy/publication/65/2015_spring_election_gab_190_complete_20150617_xls_30512.xlsx",
    date(
        2016, 8, 9
    ): "20230316065343 default/files/legacy/publication/2016_partisan_primary_el_190_2016_11_10_xlsx_88442.xlsx",
    date(
        2016, 11, 8
    ): "20171015034024 default/files/publication/2016_presidential_and_general_election_el_190_2017_83749.xlsx",
    date(
        2017, 4, 4
    ): "20230316065244 default/files/legacy/publication/el_190nf_2017_spring_election_2017_08_07_xlsx_46782.xlsx",
    date(
        2018, 4, 3
    ): "20180915010340 default/files/publication/el_190_2018_spring_election_report_2018_08_21_xlsx_10467.xlsx",
    date(
        2018, 8, 14
    ): "20201023173017 elections.wi.gov/files/2019-05/EL-190%202018%20Partisan%20Primary%20Election%20report%202019-05-07.xlsx",
    date(
        2018, 11, 6
    ): "20201020024152 elections.wi.gov/files/2019-05/EL-190%202018%20General%20Election%20report%202019-05-07.xlsx",
    date(
        2019, 4, 2
    ): "20201019174234 elections.wi.gov/files/2019-11/EL-190%202019%20Spring%20Election%20Election%20Statistics%20Report%202019-11-11_0.xlsx",
    date(
        2020, 4, 7
    ): "20230316064943 default/files/legacy/2021-01/Election%2520Statistics%2520Report%25202020%2520Spring%2520Election%2520and%2520Presidential%2520Preference%2520Vote%2520Election%25202021-01-04.xlsx",
    date(
        2020, 8, 11
    ): "20230315222627 default/files/legacy/2021-03/Election%2520Statistics%2520Report%25202020%2520Partisan%2520Primary%2520Election%25202021-03-02.xlsx",
    date(
        2020, 11, 3
    ): "20230315222557 default/files/legacy/2021-05/2020%2520General%2520Election%2520%2528EL-190F%2529%2520Election%2520Statistics%2520Report%25202021-05-10.xlsx",
    date(
        2021, 4, 6
    ): "20230715065806 default/files/legacy/2021-10/FINAL%25202021%2520Spring%2520Election%2520Election%2520Statistics%2520Report%25202021-10-15.xlsx",
    date(
        2022, 4, 5
    ): "20240502205934 default/files/documents/2022%20Spring%20Election%20Election%20Statistics%20Report%202022-10-19.xlsx",
    date(
        2022, 8, 9
    ): "20230217091853 default/files/documents/2022%20Partisan%20Primary%20Election%20Statistics%20Report%202023-2-7.xlsx",
    date(
        2022, 11, 8
    ): "20230615022538 default/files/documents/2022%20General%20Election%20Statistics%20Report%202023-06-1.xlsx",
    date(
        2023, 2, 21
    ): "20230915032712 default/files/documents/2023%20Spring%20Primary%20Election%20Statistics%20Report%202023-09-01.xlsx",
    date(
        2023, 4, 4
    ): "20231014224938 default/files/documents/2023%20Spring%20Election%20Statistics%20Report%202023-10-02.xlsx",
    date(
        2024, 4, 2
    ): "20241214220830 default/files/documents/2024%20Spring%20Election%20Statistics%20Report%202024-10-01_1.xlsx",
    date(
        2024, 8, 13
    ): "20250215005808 default/files/documents/2024%20Partisan%20Primary%20Election%20Statistics%20Report%202025-02-03.xlsx",
    date(
        2024, 11, 5
    ): "20250822193343 default/files/documents/2024%20General%20Election%20Statistics%20report%202025-06-02.xlsx",
    date(
        2025, 4, 1
    ): "20250822193258 default/files/documents/2025%20Spring%20Election%20Statistics%20Report%2008-01-2025.xlsx",
}


def url(day: date) -> str:
    stamp, path = FILES[day].split(" ")
    return f"https://web.archive.org/web/{stamp}id_/{SITE}{path}"


def fetch() -> None:
    for day, name in FILES.items():
        raw.fetch(SOURCE, url(day), suffix=Path(name).suffix)


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["WI"]
    frames = []
    for day in FILES:
        sheet = pl.read_excel(raw.cached(SOURCE, url(day)).path, sheet_id=1, has_header=False, infer_schema_length=0)
        top = next(i for i, row in enumerate(sheet.head(10).rows()) if "County" in row)
        names = [re.sub(r"[^a-z]", "", str(v).lower()) for v in sheet.row(top)]
        picks = {key: next(names.index(n) for n in options if n in names) for key, options in COLUMNS.items()}
        rows = []
        for row in sheet.slice(top + 1).rows():
            county = str(row[names.index("county")] or "")
            if not county.upper().strip().endswith("COUNTY"):
                continue
            values = {key: int(float(row[i] or 0)) for key, i in picks.items()}
            rows.append((fips[re.sub(r"[^a-z]", "", county.lower().removesuffix("county").strip())], *values.values()))
        frame = (
            pl.DataFrame(rows, schema=["code", *COLUMNS], orient="row")
            .group_by("code")
            .sum()
            .with_columns(day=pl.lit(day), registered=pl.col("registrants") + pl.col("late") + pl.col("edr"))
        )
        print(day, frame.height, "counties", frame.select(pl.col("registered", "ballots", "voters").sum()).row(0))
        frames.append(frame)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames).write_parquet(PARSED / "turnout.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Wisconsin's registered voters (registrants, late registrants, and election day registrants), ballots, and voters by county from the Elections Commission's election voting and registration statistics reports (GAB-190 and EL-190), latest posting of each, from Wayback copies",
            polity="US",
        )
        elections: dict[date, list[tuple[str, str]]] = {}
        for value, entity_id, start in conn.execute(
            """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':WI(:[a-z]+)?$'"""
        ):
            elections.setdefault(start, []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                """select r.election_id from result_set r join changeset c on c.id = r.changeset_id
                   where r.contest_id is null and r.is_default and r.status <> 'withdrawn' and c.pipeline <> %s""",
                [SOURCE],
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://elections.wi.gov/statistics-data/voting-statistics",
                license="Public record",
            )
        for (day,), rows in frame.group_by("day", maintain_order=True):
            if day not in elections:
                print(f"{day}: no election")
                continue
            cs.artifact(raw.cached(SOURCE, url(day)), source)
            for value_key, election_id in elections[day]:
                result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:county")
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election_id,
                    contest_id=None,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=election_id not in held,
                    completeness=round(rows.height / 72, 4),
                )
                cs.add(
                    "note", id=uuid5(result_set, "registered"), entity_id=result_set, kind="methodology", body_md=NOTE
                )
                for code, registered, ballots, voters in rows.select(
                    "code", "registered", "ballots", "voters"
                ).iter_rows():
                    for measure, value in ((2, registered), (3, ballots), (14, voters)):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=cs.unit("us.county", code),
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=1 if measure == 2 else 0,
                            value=value,
                        )
        return cs.write()
