import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.package import Draft
from ea.sources.licenses import UNLICENSED
from ea.store import DATA, connect

SOURCE = "de.reichspraesident"
VERSION = "2"
TSV = Path(__file__).with_name("reichspraesident.tsv")
PAGES = DATA / "pages" / SOURCE
BASE = "https://digi.bib.uni-mannheim.de/fileadmin/statjahrb/514401303_{}/pdf/514401303_{}.pdf"
# Each round is read from the Statistisches Jahrbuch volume that printed it, one source per volume.
SCANS = {"1926": ("0498", "0499"), "1932": ("0590", "0591")}
VOLUME = {1925: "1926", 1932: "1932"}
PUBLISHER = "Statistisches Reichsamt"
TITLE = "Statistisches Jahrbuch für das Deutsche Reich {}"
NOTES = {
    "1926": (
        "Chapter XIX, Wahlen und Abstimmungen, table 3: Gesamtergebnis der Wahl des Reichspräsidenten (I. und II. "
        "Wahlgang) am 29. März und 26. April 1925, printed pages 450 and 451, digitized by the Universitätsbibliothek "
        "Mannheim as scan pages 0498 and 0499."
    ),
    "1932": (
        "Chapter XX, Wahlen und Abstimmungen, table 4: Das Gesamtergebnis der Wahl des Reichspräsidenten (I. und II. "
        "Wahlgang) am 13. März und 10. April 1932, printed pages 546 and 547, digitized by the Universitätsbibliothek "
        "Mannheim as scan pages 0590 and 0591."
    ),
}
VOTES, REGISTERED, CAST, VALID = 1, 2, 3, 4
DIGITIZED = 8
ROUNDS = {
    date(1925, 3, 29): ("general", 1, "first round", "erster Wahlgang"),
    date(1925, 4, 26): ("runoff", 2, "second round", "zweiter Wahlgang"),
    date(1932, 3, 13): ("general", 1, "first round", "erster Wahlgang"),
    date(1932, 4, 10): ("runoff", 2, "second round", "zweiter Wahlgang"),
}
CANDIDATES = {
    "braun": ("Otto Braun", "Q58667"),
    "duesterberg": ("Theodor Duesterberg", "Q71217"),
    "held": ("Heinrich Held", "Q69033"),
    "hellpach": ("Willy Hellpach", "Q69366"),
    "hindenburg": ("Paul von Hindenburg", "Q2667"),
    "hitler": ("Adolf Hitler", "Q352"),
    "jarres": ("Karl Jarres", "Q67512"),
    "ludendorff": ("Erich Ludendorff", "Q58579"),
    "marx": ("Wilhelm Marx", "Q77183"),
    "thaelmann": ("Ernst Thälmann", "Q77004"),
    "winter": ("Gustav Winter", "Q1556549"),
}
ELECTED = {date(1925, 4, 26): "hindenburg", date(1932, 4, 10): "hindenburg"}
# Per round and scan: each column's left and right edge as fractions of the page width, and optionally how far its
# boxes sit above the row bands.
COLUMNS: dict[tuple[date, str], dict[str, tuple[float, ...]]] = {
    (date(1925, 3, 29), "0498"): {
        "registered": (0.265, 0.340),
        "cast": (0.350, 0.420),
        "valid": (0.470, 0.545),
        "jarres": (0.550, 0.625),
        "held": (0.625, 0.695),
        "ludendorff": (0.700, 0.760),
        "braun": (0.765, 0.830),
        "marx": (0.835, 0.900),
        "hellpach": (0.905, 0.975),
    },
    # Thälmann's first-ballot column runs into the binding, where the print curves upward, so its boxes sit 0.006
    # of the page height above the rows.
    (date(1925, 3, 29), "0499"): {"thaelmann": (0.020, 0.100, 0.006), "scattered": (0.110, 0.170)},
    (date(1925, 4, 26), "0499"): {
        "registered": (0.185, 0.265),
        "cast": (0.280, 0.360),
        "valid": (0.455, 0.530),
        "hindenburg": (0.555, 0.625),
        "marx": (0.650, 0.720),
        "thaelmann": (0.740, 0.800),
        "scattered": (0.840, 0.890),
    },
    (date(1932, 3, 13), "0590"): {
        "registered": (0.275, 0.345),
        "cast": (0.350, 0.420),
        "valid": (0.455, 0.525),
        "duesterberg": (0.528, 0.585),
        "hindenburg": (0.630, 0.690),
        "hitler": (0.735, 0.797),
        "thaelmann": (0.838, 0.890),
    },
    (date(1932, 3, 13), "0591"): {"winter": (0.010, 0.090), "scattered": (0.135, 0.190)},
    (date(1932, 4, 10), "0591"): {
        "registered": (0.235, 0.305),
        "cast": (0.312, 0.380),
        "valid": (0.425, 0.495),
        "hindenburg": (0.505, 0.572),
        "hitler": (0.625, 0.687),
        "thaelmann": (0.735, 0.795),
        "scattered": (0.850, 0.910),
    },
}
# Per scan: the column whose text lines mark the rows (left and right edge), the top of the table body, and the
# number of lines expected there; the first 35 are the Wahlkreise.
ROW_ANCHORS = {
    "0498": (0.26, 0.345, 0.17, 36),
    "0499": (0.18, 0.28, 0.17, 36),
    "0590": (0.0, 0.1, 0.25, 35),
    "0591": (0.19, 0.21, 0.25, 36),
}
# The national totals printed beneath each column.
TOTALS = {
    date(1925, 3, 29): {
        "registered": 39226138,
        "cast": 27016760,
        "valid": 26866106,
        "jarres": 10416658,
        "held": 1007450,
        "ludendorff": 285793,
        "braun": 7802497,
        "marx": 3887734,
        "hellpach": 1568398,
        "thaelmann": 1871815,
        "scattered": 25761,
    },
    date(1925, 4, 26): {
        "registered": 39414316,
        "cast": 30567874,
        "valid": 30351813,
        "hindenburg": 14655641,
        "marx": 13751605,
        "thaelmann": 1931151,
        "scattered": 13416,
    },
}
SYSTEM = (
    "The Reich President was elected directly by the people for seven years (Weimar Constitution art. 41 and 43). Under "
    "the Law on the Election of the Reich President of 4 May 1920, a candidate needed more than half of the valid votes "
    "in the first ballot. If no one reached it, a second ballot followed, open to new candidates, in which the most "
    "votes won."
)
NOTE = (
    "Counts by Reichstag constituency (Wahlkreis) are transcribed from the Statistisches Jahrbuch für das Deutsche Reich "
    "{}. In each constituency the candidates' votes and the scattered votes add up to the valid votes, and each column "
    "adds up to the national total printed beneath it. Ballots cast are the printed turnout (Wahlbeteiligung); the "
    "table gives no separate count of invalid ballots."
)
FIRST_ROUND = {
    1925: (
        "No candidate won more than half of the valid votes, so a second ballot was held on 26 April 1925. Of the "
        "first-ballot candidates only Marx and Thälmann stood in it; Hindenburg stood for the first time."
    ),
    1932: (
        "No candidate won more than half of the valid votes, so a second ballot was held on 10 April 1932. Duesterberg "
        "and Winter did not stand in it."
    ),
}


def url(volume: str, scan: str) -> str:
    return BASE.format(volume, scan)


def fetch() -> None:
    for volume, scans in SCANS.items():
        for scan in scans:
            raw.fetch(SOURCE, url(volume, scan), suffix=".pdf")


def rows(path: Path, scan: str) -> list[tuple[float, float]]:
    html = subprocess.run(
        ["pdftotext", "-bbox-layout", str(path), "-"], check=True, capture_output=True, text=True
    ).stdout
    page = re.search(r'<page width="([^"]+)" height="([^"]+)"', html)
    if page is None:
        raise ValueError(f"{scan}: no page box")
    width, height = float(page[1]), float(page[2])
    low, high, top, expected = ROW_ANCHORS[scan]
    words = sorted(
        (float(y0) / height, float(y1) / height)
        for x0, y0, _, y1 in re.findall(r'<word xMin="([^"]+)" yMin="([^"]+)" xMax="([^"]+)" yMax="([^"]+)"', html)
        if low <= float(x0) / width < high and top < float(y0) / height < 0.95
    )
    lines: list[tuple[float, float]] = []
    for y0, y1 in words:
        if lines and y0 - lines[-1][0] < 0.006:
            lines[-1] = (min(lines[-1][0], y0), max(lines[-1][1], y1))
        else:
            lines.append((y0, y1))
    if len(lines) != expected:
        raise ValueError(f"{scan}: {len(lines)} text lines in the anchor column, expected {expected}")
    return [(y0 - 0.004, y1 + 0.004) for y0, y1 in lines[:35]]


def parse() -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    pages = []
    for volume, scans in SCANS.items():
        for scan in scans:
            artifact = raw.cached(SOURCE, url(volume, scan))
            image = PAGES / f"{artifact.sha256}-1.png"
            if not image.exists():
                subprocess.run(
                    ["pdftoppm", "-r", "100", "-png", "-singlefile", str(artifact.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            pages.append(
                {
                    "volume": volume,
                    "scan": scan,
                    "image": f"pages/{SOURCE}/{image.name}",
                    "width": width,
                    "height": height,
                    "rows": [list(row) for row in rows(artifact.path, scan)],
                }
            )
    folder = raw.parsed(SOURCE)
    folder.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(pages).write_parquet(folder / "pages.parquet")


def returns() -> pl.DataFrame:
    frame = pl.read_csv(TSV, separator="\t", try_parse_dates=True)
    counted = [column for column in [*CANDIDATES, "scattered"] if column in frame.columns]
    for row in frame.iter_rows(named=True):
        total = sum(row[column] or 0 for column in counted)
        if total != row["valid"] or not row["valid"] <= row["cast"] <= row["registered"]:
            raise ValueError(f"{row['date']} {row['name']}: {total} counted against {row['valid']} valid")
    for (day,), group in frame.group_by("date"):
        if group.height != 35 or sorted(group["wahlkreis"]) != list(range(1, 36)):
            raise ValueError(f"{day}: expected Wahlkreise 1 to 35")
        for column, printed in TOTALS.get(day, {}).items():
            if group[column].sum() != printed:
                raise ValueError(f"{day} {column}: the Wahlkreise add up to {group[column].sum()}, printed {printed}")
    if set(frame["date"]) != set(ROUNDS):
        raise ValueError("the rows and ROUNDS name different dates")
    return frame


def emit() -> Path:
    frame = returns()
    pages = pl.read_parquet(raw.parsed(SOURCE) / "pages.parquet")
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Reich President elections of 1925 and 1932, both ballots each, by Reichstag constituency",
            polity="DE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        germany = cs.polity("DE")
        sources = {}
        for volume, scans in SCANS.items():
            sources[volume] = cs.source(PUBLISHER, TITLE.format(volume))
            cs.add(
                "source",
                id=sources[volume],
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE.format(volume),
                url=url(volume, scans[0]),
                license=UNLICENSED,
                notes=NOTES[volume],
            )
        page_ids = {}
        for page in pages.iter_rows(named=True):
            artifact = cs.artifact(raw.cached(SOURCE, url(page["volume"], page["scan"])), sources[page["volume"]])
            page_ids[page["scan"]] = cs.page(artifact, 1)
            cs.add(
                "source_page",
                id=page_ids[page["scan"]],
                artifact_id=artifact,
                page_number=1,
                image_key=page["image"],
                width=page["width"],
                height=page["height"],
            )
        bands = {page["scan"]: page["rows"] for page in pages.iter_rows(named=True)}

        office = cs.key("de.office", "reichspraesident")
        cs.add("office", id=office, polity_id=germany, kind="head_of_state")
        name(office, "President of the German Reich")
        name(office, "Reichspräsident", "de", "native")
        system = cs.key("de.electoral_system", "reichspraesident")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=germany,
            rules={
                "formula": "repeated_ballot",
                "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}, {"fromRound": 2, "share": 0}],
            },
            rules_version=1,
        )
        name(system, "Popular election of the Reich President")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        cs.add(
            "scheme",
            code="de.reich_wahlkreis",
            polity_id=germany,
            kind="electoral_district",
            rank=1,
            noun="constituency",
        )
        units = {}
        for number, label in frame.select("wahlkreis", "name").unique().sort("wahlkreis").iter_rows():
            unit = cs.unit("de.reich_wahlkreis", str(number))
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=germany,
                    scheme_code="de.reich_wahlkreis",
                    code=str(number),
                    area_relation="unlocated",
                    kind="standard",
                )
                name(unit, label, "de")
            units[number] = unit

        persons: dict[str, UUID] = {}
        linked = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = 'wikidata' and value = any(%s)",
                [[item for _, item in CANDIDATES.values()]],
            ).fetchall()
        )
        for column, (full, item) in CANDIDATES.items():
            if item in linked and linked[item] not in cs.own_entities:
                persons[column] = linked[item]
                continue
            persons[column] = cs.key(f"{SOURCE}.person", item)
            if persons[column] in cs.minted:
                cs.add("person", id=persons[column])
                name(persons[column], full, "de")
                cs.add("external_id", entity_id=persons[column], scheme="wikidata", value=item)

        values: list[tuple[str, str, str | None, int, int, int]] = []
        locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
        previous = None
        for day, (kind, number, english, german) in ROUNDS.items():
            group = frame.filter(pl.col("date") == day).sort("wahlkreis")
            election = cs.key("de.election", f"{day}:{kind}")
            cs.add("election", id=election, polity_id=germany, kind=kind, date_from=day, date_to=day, status="held")
            name(election, f"{day.year} German presidential election, {english}")
            name(election, f"Reichspräsidentenwahl {day.year}, {german}", "de", "native")
            path = f"de/{day}-{kind}"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key("de.contest", f"{day}:reichspraesident")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                magnitude=1,
                round_number=number,
                previous_round_id=previous if number > 1 else None,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, "Reich President")
            cs.add("slug", path=f"{path}/president", entity_id=contest)
            volume = VOLUME[day.year]
            cs.add("note", entity_id=contest, kind="methodology", body_md=NOTE.format(volume))
            if number == 1:
                cs.add("note", entity_id=contest, kind="caveat", body_md=FIRST_ROUND[day.year])
            previous = contest
            result_set = cs.key(f"{SOURCE}.result_set", str(day))
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=sources[volume],
                is_default=True,
                completeness=1,
            )
            options: dict[str, UUID | None] = {"registered": None, "cast": None, "valid": None}
            measures = {"registered": REGISTERED, "cast": CAST, "valid": VALID}
            for column, (full, _) in CANDIDATES.items():
                if group[column].null_count() == group.height:
                    continue
                option = cs.key("de.option", f"{day}:reichspraesident:{column}")
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=full)
                cs.add(
                    "candidacy",
                    id=cs.key("de.candidacy", f"{day}:reichspraesident:{column}"),
                    person_id=persons[column],
                    option_id=option,
                )
                options[column] = option
                measures[column] = VOTES
                if ELECTED.get(day) == column:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            option = cs.key("de.option", f"{day}:reichspraesident:scattered")
            cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Zersplittert")
            options["scattered"] = option
            measures["scattered"] = VOTES
            for (when, scan), columns in COLUMNS.items():
                if when != day:
                    continue
                for row, (top, bottom) in zip(group.iter_rows(named=True), bands[scan], strict=True):
                    unit = str(units[row["wahlkreis"]])
                    for column, (left, right, *lift) in columns.items():
                        found = options[column]
                        key = (str(result_set), unit, None if found is None else str(found), measures[column])
                        values.append((*key, row[column], DIGITIZED))
                        up = lift[0] if lift else 0.0
                        locators.append((*key, str(page_ids[scan]), left, top - up, right, bottom - up))
            if len(values) != len({value[:4] for value in values}):
                raise ValueError(f"{day}: a column is placed twice")
            placed = {column for (when, _), columns in COLUMNS.items() if when == day for column in columns}
            if placed != set(options):
                raise ValueError(f"{day}: columns {sorted(placed ^ set(options))} are not placed once on a page")

        def frame_of(data: list[Any], schema: dict[str, Any]) -> pl.DataFrame:
            return pl.DataFrame(data, schema=schema, orient="row").with_columns(
                channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)
            )

        key_schema = {"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "measure": pl.Int16}
        cs.frame("result_value", frame_of(values, {**key_schema, "value": pl.Int64, "flags": pl.Int16}))
        cs.frame(
            "value_locator",
            frame_of(
                locators,
                {
                    **key_schema,
                    "page_id": pl.String,
                    "x0": pl.Float64,
                    "y0": pl.Float64,
                    "x1": pl.Float64,
                    "y1": pl.Float64,
                },
            ),
        )
        return cs.write()
