import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.licenses import UNLICENSED
from ea.store import DATA, connect

SOURCE = "de.reichspraesident"
VERSION = "1"
TSV = Path(__file__).with_name("reichspraesident.tsv")
PAGES = DATA / "pages" / SOURCE
BASE = "https://digi.bib.uni-mannheim.de/fileadmin/statjahrb/514401303_1932/pdf/514401303_{}.pdf"
SCANS = ("0590", "0591")
PUBLISHER = "Statistisches Reichsamt"
TITLE = "Statistisches Jahrbuch für das Deutsche Reich 1932"
NOTES = (
    "Chapter XX, Wahlen und Abstimmungen, table 4: Das Gesamtergebnis der Wahl des Reichspräsidenten (I. und II. "
    "Wahlgang) am 13. März und 10. April 1932, printed pages 546 and 547, digitized by the Universitätsbibliothek "
    "Mannheim as scan pages 0590 and 0591."
)
VOTES, REGISTERED, CAST, VALID = 1, 2, 3, 4
DIGITIZED = 8
ROUNDS = {
    date(1932, 3, 13): ("general", 1, "first round", "erster Wahlgang"),
    date(1932, 4, 10): ("runoff", 2, "second round", "zweiter Wahlgang"),
}
CANDIDATES = {
    "duesterberg": ("Theodor Duesterberg", "Q71217"),
    "hindenburg": ("Paul von Hindenburg", "Q2667"),
    "hitler": ("Adolf Hitler", "Q352"),
    "thaelmann": ("Ernst Thälmann", "Q77004"),
    "winter": ("Gustav Winter", "Q1556549"),
}
ELECTED = {date(1932, 4, 10): "hindenburg"}
COLUMNS = {
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
ROW_ANCHORS = {"0590": (0.0, 0.1, 35), "0591": (0.19, 0.21, 36)}
SYSTEM = (
    "The Reich President was elected directly by the people for seven years (Weimar Constitution art. 41 and 43). Under "
    "the Law on the Election of the Reich President of 4 May 1920, a candidate needed more than half of the valid votes "
    "in the first ballot. If no one reached it, a second ballot followed, open to new candidates, in which the most "
    "votes won."
)
NOTE = (
    "Counts by Reichstag constituency (Wahlkreis) are transcribed from the Statistisches Jahrbuch für das Deutsche Reich "
    "1932. In each constituency the candidates' votes and the scattered votes add up to the valid votes, and each column "
    "adds up to the national total printed beneath it. Ballots cast are the printed turnout (Wahlbeteiligung); the "
    "table gives no separate count of invalid ballots."
)
FIRST_ROUND = (
    "No candidate won more than half of the valid votes, so a second ballot was held on 10 April 1932. Duesterberg and "
    "Winter did not stand in it."
)


def url(scan: str) -> str:
    return BASE.format(scan)


def fetch() -> None:
    for scan in SCANS:
        raw.fetch(SOURCE, url(scan), suffix=".pdf")


def rows(path: Path, scan: str) -> list[tuple[float, float]]:
    html = subprocess.run(
        ["pdftotext", "-bbox-layout", str(path), "-"], check=True, capture_output=True, text=True
    ).stdout
    page = re.search(r'<page width="([^"]+)" height="([^"]+)"', html)
    if page is None:
        raise ValueError(f"{scan}: no page box")
    width, height = float(page[1]), float(page[2])
    low, high, expected = ROW_ANCHORS[scan]
    words = sorted(
        (float(y0) / height, float(y1) / height)
        for x0, y0, _, y1 in re.findall(r'<word xMin="([^"]+)" yMin="([^"]+)" xMax="([^"]+)" yMax="([^"]+)"', html)
        if low <= float(x0) / width < high and 0.25 < float(y0) / height < 0.95
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
    for scan in SCANS:
        artifact = raw.cached(SOURCE, url(scan))
        image = PAGES / f"{artifact.sha256}-1.png"
        if not image.exists():
            subprocess.run(
                ["pdftoppm", "-r", "100", "-png", "-singlefile", str(artifact.path), str(image.with_suffix(""))],
                check=True,
            )
        width, height = struct.unpack(">II", image.read_bytes()[16:24])
        pages.append(
            {
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
    for day, group in frame.group_by("date"):
        if group.height != 35 or sorted(group["wahlkreis"]) != list(range(1, 36)):
            raise ValueError(f"{day}: expected Wahlkreise 1 to 35")
    return frame


def emit() -> Path:
    frame = returns()
    pages = pl.read_parquet(raw.parsed(SOURCE) / "pages.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Reich President election of 1932, both ballots, by Reichstag constituency",
            polity="DE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        germany = cs.polity("DE")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=url(SCANS[0]),
            license=UNLICENSED,
            notes=NOTES,
        )
        page_ids = {}
        for page in pages.iter_rows(named=True):
            artifact = cs.artifact(raw.cached(SOURCE, url(page["scan"])), source)
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
            if item in linked:
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
                previous_round_id=previous,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, "Reich President")
            cs.add("slug", path=f"{path}/president", entity_id=contest)
            cs.add("note", entity_id=contest, kind="methodology", body_md=NOTE)
            if number == 1:
                cs.add("note", entity_id=contest, kind="caveat", body_md=FIRST_ROUND)
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
                source_id=source,
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
                    for column, (left, right) in columns.items():
                        found = options[column]
                        key = (str(result_set), unit, None if found is None else str(found), measures[column])
                        values.append((*key, row[column], DIGITIZED))
                        locators.append((*key, str(page_ids[scan]), left, top, right, bottom))
            if len(values) != len({value[:4] for value in values}):
                raise ValueError(f"{day}: a column is placed twice")

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
