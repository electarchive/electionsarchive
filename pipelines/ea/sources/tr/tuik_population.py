from datetime import date
from pathlib import Path

import polars as pl

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "tr.tuik_population"
VERSION = "1"
TSV = Path(__file__).with_name("muhtarlik_2024_tuik.tsv")
PUBLISHER = "Türkiye İstatistik Kurumu"
TITLE = "Adrese Dayalı Nüfus Kayıt Sistemi: belediye, köy ve mahalle nüfusları"
URL = "https://biruni.tuik.gov.tr/medas/?kn=95&locale=tr"
LICENSE = "No license stated. TÜİK allows its published statistics to be used with the source cited."
SCHEMES = {"m": "tr.tuik.mahalle", "k": "tr.tuik.koy"}
YEARS = (2013, 2018, 2023)


def emit() -> Path:
    register = pl.read_csv(TSV, separator="\t", infer_schema=False).filter(pl.col("tuik_code").is_not_null())
    with connect() as conn:
        areas = {
            (scheme, value): str(area)
            for scheme, value, area in conn.execute(
                "select scheme, value, entity_id from external_id where scheme = any(%s)", [list(SCHEMES.values())]
            ).fetchall()
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Population on 31 December 2013, 2018, and 2023 of each 2024 muhtarlık (mahalle and köy) from "
            "TÜİK's address-based population register, joined by TÜİK's own unit code",
            polity="TR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license=LICENSE,
            notes="MEDAS table 95, one export per year and level (Mahalle, Köy), every province; each row is a "
            "unit label with TÜİK's code and its population on 31 December of the year.",
        )
        rows, missing = [], []
        for row in register.iter_rows(named=True):
            code = row["tuik_code"]
            area = areas.get((SCHEMES[code[0]], code[1:]))
            if area is None:
                missing.append(code)
                continue
            level = "Mahalle" if code[0] == "m" else "Köy"
            for year in YEARS:
                if row[f"pop_{year}"] is not None:
                    rows.append(
                        {
                            "area_id": area,
                            "date": date(year, 12, 31),
                            "value": round(float(row[f"pop_{year}"])),
                            "locator": f"MEDAS kn=95, {year}, {level}, code {code[1:]}",
                        }
                    )
        if missing:
            raise ValueError(f"{len(missing)} TÜİK codes have no area, first {', '.join(missing[:5])}")
        frame = pl.DataFrame(
            rows, schema={"area_id": pl.String, "date": pl.Date, "value": pl.Int64, "locator": pl.String}
        ).with_columns(kind=pl.lit("population"), date_precision=pl.lit("day"), source_id=pl.lit(str(source)))
        cs.frame("area_figure", frame)
        for year, count, total in (
            frame.group_by(pl.col("date").dt.year()).agg(pl.len(), pl.col("value").sum()).sort("date").iter_rows()
        ):
            print(f"{year}: {count} areas, {total:,} people")
        return cs.write()
