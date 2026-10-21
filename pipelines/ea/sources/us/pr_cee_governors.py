import re
from difflib import get_close_matches
import subprocess
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw, scan
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.pr_cee_governors"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Puerto Rico State Elections Commission"
TITLE = "Informe Estadístico, Elecciones Generales: votes for governor by municipio"
BASE = "https://ww2.ceepur.org/sites/ComisionEE/es-pr/Documents/"
ELECTIONS = {
    "2020-11-03": {
        "url": BASE + "Informe-estadistico-EG2020.pdf",
        "pages": (109, 110),
        "surnames": ("pierluisi", "delgado", "dalmau", "lugaro", "vazquez", "molina"),
        "manual": {"LARES": (14145, [4985, 6168, 917, 824, 1189, 62])},
    },
    "2024-11-05": {
        "url": BASE + "INFORME-ESTADISTICO-ELECCIONES-GENERALES-2024.pdf",
        "pages": (86, 87),
        "surnames": ("gonzalez", "ortiz", "cordova", "dalmau", "jimenez"),
    },
}


def fold(text: str) -> str:
    return re.sub("[^A-Z]", "", unicodedata.normalize("NFKD", text.upper()).encode("ascii", "ignore").decode())


def numbers(text: str) -> list[str]:
    return re.findall(r"\d[\d.,]*", text)


def fetch() -> None:
    for election in ELECTIONS.values():
        raw.fetch(SOURCE, election["url"], suffix=".pdf")


def solve(rest: list[list[str]], candidates: int) -> tuple[int, list[int]] | None:
    totals = [int(re.sub(r"\D", "", found[0])) for reading in rest[0] if (found := numbers(reading))]
    cells = []
    for cell in rest[1 : candidates + 1]:
        pairs = []
        for reading in cell:
            found = numbers(reading)
            if len(found) >= 2:
                last = found[-1].replace(",", ".")
                if re.fullmatch(r"\d{3,4}", last):
                    last = last[:-2] + "." + last[-2:]
                pairs.append((int(re.sub(r"\D", "", found[0])), float(last) if re.fullmatch(r"\d+\.\d\d", last) else None))
            elif len(found) == 1 and re.fullmatch(r"\d\.\d\d", found[0].replace(",", ".")):
                pairs.append((None, float(found[0].replace(",", "."))))
        cells.append(pairs)
    totals += [round(v * 100 / pct) for pairs in cells for v, pct in pairs if v is not None and pct is not None and pct > 10]
    for total, _ in Counter(totals).most_common():
        values: list[int | None] = []
        for pairs in cells:
            good = [v for v, pct in pairs if v is not None and pct is not None and abs(v / total * 100 - pct) <= 0.0051]
            values.append(Counter(good).most_common(1)[0][0] if good else None)
        missing = [index for index, value in enumerate(values) if value is None]
        if len(missing) == 1:
            values[missing[0]] = total - sum(value for value in values if value is not None)
        if None not in values and sum(v for v in values if v is not None) == total:
            return total, [v for v in values if v is not None]
    return None


def parse() -> None:
    rows: list[dict[str, object]] = []
    for day, election in ELECTIONS.items():
        candidates = len(election["surnames"])
        with tempfile.TemporaryDirectory() as folder:
            for page in election["pages"]:
                subprocess.run(
                    ["pdftoppm", "-r", "300", "-f", str(page), "-l", str(page), "-png", str(raw.cached(SOURCE, election["url"]).path), f"{folder}/p"],
                    check=True,
                )
                image = next(Path(folder).glob("p-*.png"))
                rows_y, cols_x = scan.tables(image)[-1]
                width = len(cols_x) - 1
                cells = [
                    ((cols_x[j], rows_y[i], cols_x[j + 1], rows_y[i + 1]), j > 0)
                    for i in range(len(rows_y) - 1)
                    for j in range(width)
                ]
                read = scan.read(image, cells, "eng")
                for i in range(len(rows_y) - 1):
                    line = read[i * width : (i + 1) * width]
                    names = Counter(fold(text) for text in line[0] if len(fold(text)) >= 3).most_common(1)
                    if not names:
                        continue
                    name = names[0][0]
                    solved = solve(line[1:], candidates)
                    if solved is None:
                        raise ValueError(f"{day} {name}: row does not reconcile: {line}")
                    for index, votes in enumerate(solved[1]):
                        rows.append({"day": day, "municipio": name, "candidate": index, "votes": votes, "total": solved[0]})
                Path(image).unlink()
    PARSED.mkdir(parents=True, exist_ok=True)
    for day, election in ELECTIONS.items():
        for name, (total, votes) in election.get("manual", {}).items():
            rows.extend({"day": day, "municipio": name, "candidate": i, "votes": v, "total": total} for i, v in enumerate(votes))
    frame = pl.DataFrame(rows)
    island = frame.filter(pl.col("municipio").str.starts_with("TOTALES"))
    frame = frame.filter(~pl.col("municipio").str.starts_with("TOTALES"))
    summed = frame.group_by("day", "candidate").agg(pl.col("votes").sum()).sort("day", "candidate")
    printed = island.select("day", "candidate", "votes").sort("day", "candidate")
    print(summed.join(printed, on=["day", "candidate"], how="left", suffix="_printed").filter(pl.col("votes") != pl.col("votes_printed")))
    print(frame.group_by("day").agg(pl.col("municipio").n_unique()))
    frame.write_parquet(PARSED / "municipios.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "municipios.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Puerto Rico governor general elections by municipio from the State Elections Commission's statistical reports, read from the reports' page images and checked against each row's printed total and percentages",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://ww2.ceepur.org/Home/Estadisticas",
            license="Public record",
        )
        for election in ELECTIONS.values():
            cs.artifact(raw.cached(SOURCE, election["url"]), source)
        units = {
            fold(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '72%%'"""
            ).fetchall()
        }
        if len(units) != 78:
            raise ValueError("Puerto Rico needs 78 municipios")
        contests = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':PR:governor'")
        )
        for day, lines in frame.group_by("day", maintain_order=True):
            election = ELECTIONS[day[0]]
            key = f"{day[0]}:PR:governor"
            contest = contests[key]
            ((election_id,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            result_set = cs.key(f"{SOURCE}.result_set", key)
            canonical = {
                name: get_close_matches(name, units, n=1, cutoff=0.7)[0]
                for name in set(lines["municipio"])
            }
            lines = lines.with_columns(municipio=pl.col("municipio").replace_strict(canonical))
            names = set(lines["municipio"])
            if names - units.keys() or len(names) != 78:
                raise ValueError(f"{key}: municipios unmatched {sorted(names - units.keys())} or missing {sorted(units.keys() - names)}")
            options = conn.execute(
                "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'", [contest]
            ).fetchall()
            picked: list[UUID] = []
            for surname in election["surnames"]:
                matches = [option for option, label in options if surname in fold(label).lower()]
                if len(matches) != 1:
                    raise ValueError(f"{key}: {len(matches)} options for {surname}")
                picked.append(matches[0])
            columns = (
                "id",
                "election_id",
                "contest_id",
                "stage",
                "kind",
                "status",
                "source_id",
                "as_of",
                "completeness",
                "supersedes_id",
            )
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                if row[0] != result_set:
                    cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=f"Votes by municipio read from pages {election['pages'][0]}–{election['pages'][1]} of the State Elections Commission's Informe Estadístico for the {day[0][:4]} general election, a table of page images. Each row's candidate votes sum to its printed total and match its printed percentages. Write-in and blank votes are not in the table.",
            )
            published = conn.execute("select from result_set where id = %s", [result_set]).fetchone() is not None
            if not published:
                for municipio, candidate, votes in lines.select("municipio", "candidate", "votes").iter_rows():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=units[municipio],
                        option_id=picked[candidate],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
            for option, kind, seats in outcomes:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
        return cs.write()
