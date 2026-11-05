import subprocess
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.webb_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
URL = "https://www.webbcountytx.gov/DocumentCenter/View/12238"
PUBLISHER = "Webb County Elections Administrator"
TITLE = "Unofficial Combined Results, November 3, 2026 Joint General and Special Election"
AS_OF = datetime(2026, 11, 3, 23, 5, tzinfo=timezone(timedelta(hours=-6)))
PLACE = "4841464"
PATH = f"us/tx/laredo/{DAY}-general"
DIGITIZED = 8
PAGES = DATA / "pages" / SOURCE
DPI = 100
WIDTH = 1272
HEIGHT = 1644
PROPOSITION = f"{DAY}:TX:{PLACE}:measure:proposition-1-non-binding-advisory-vote-on-pediatric-hospital-services"
CONTESTS = (
    (
        "mayor",
        11,
        (54897, 833, 41783, 12281),
        (
            ("Victor D. Trevino", 17725, 305, 13573, 3847, 653),
            ("JD Gonzalez", 10270, 80, 7843, 2347, 682),
            ("Jorge A. Garza", 1628, 16, 1131, 481, 710),
            ("Poncho Casso", 5764, 71, 4640, 1053, 739),
            ("Alyssa Cigarroa", 19510, 361, 14596, 4553, 768),
        ),
    ),
    (
        "district-1",
        11,
        (5469, 45, 4043, 1381),
        (
            ("Lupe De Leon Jr", 1320, 7, 969, 344, 1023),
            ("Gilbert Gonzalez", 4149, 38, 3074, 1037, 1052),
        ),
    ),
    (
        "district-2",
        11,
        (5084, 55, 3819, 1210),
        (
            ('Ricardo "Richie" Rangel Jr', 2641, 30, 1960, 651, 1306),
            ("Daisy Campos Rodriguez", 2443, 25, 1859, 559, 1335),
        ),
    ),
    (
        "district-3",
        12,
        (5877, 99, 4334, 1444),
        (
            ("Melissa R Cigarroa", 3147, 73, 2328, 746, 251),
            ('Michelle "Mimi" Winterroth', 1146, 12, 824, 310, 280),
            ('Clarissa "Claire" Cardenas', 1584, 14, 1182, 388, 309),
        ),
    ),
    (
        "district-6",
        12,
        (10793, 89, 8688, 2016),
        (
            ("D. Tyler King", 8460, 67, 6833, 1560, 563),
            ('Ubaldo "Baldo" Granados Jr', 2333, 22, 1855, 456, 592),
        ),
    ),
    (
        "proposition-1",
        12,
        (52971, 833, 40243, 11895),
        (
            ("Yes", 48269, 762, 36549, 10958, 1129),
            ("No", 4702, 71, 3694, 937, 1158),
        ),
    ),
)


def render(path: Path, sha: str, number: int) -> tuple[Path, int, int]:
    PAGES.mkdir(parents=True, exist_ok=True)
    image = PAGES / f"{sha}-{number}.png"
    if not image.exists():
        stem = PAGES / f".{sha}-{number}"
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                str(DPI),
                "-png",
                "-singlefile",
                "-f",
                str(number),
                "-l",
                str(number),
                str(path),
                str(stem),
            ],
            check=True,
        )
        stem.with_suffix(".png").rename(image)
    with Image.open(image) as opened:
        return image, opened.width, opened.height


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        content = subprocess.run(["curl", "-sSL", "-m", "120", URL], capture_output=True, check=True).stdout
        raw.store(SOURCE, URL, content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = []
    for contest, page, totals, lines in CONTESTS:
        for column, total in enumerate(totals, start=1):
            if sum(int(line[column]) for line in lines) != total:
                raise ValueError(f"{contest}: column {column} does not add up to {total}")
        if sum(totals[1:]) != totals[0]:
            raise ValueError(f"{contest}: channels do not add up to {totals[0]}")
        for name, total, absentee, early, day, y in lines:
            if absentee + early + day != total:
                raise ValueError(f"{contest}: {name} channels do not add up to {total}")
            rows.append({"contest": contest, "page": page, "name": name, "votes": total, "y": y})
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "results.parquet")
    print(frame.height)


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "results.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Laredo's mayor, four City Council seats, and Proposition 1 on November 3, 2026, digitized from Webb County's scanned unofficial results",
            polity="US-TX",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL)
        cached = raw.cached(SOURCE, URL)
        artifact = cs.artifact(cached, source)
        pages = {}
        for number in frame["page"].unique().sort().to_list():
            pages[number] = cs.page(artifact, number)
            image, width, height = render(cached.path, cached.sha256, number)
            cs.add(
                "source_page",
                id=pages[number],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        election = cs.key("us.election", f"{DAY}:TX:{PLACE}")
        system = cs.key("us.electoral_system", "two_round")
        office = cs.key("us.office", f"TX:{PLACE}:mayor")
        body = cs.key("us.body", f"TX:{PLACE}:council")
        people = dict(
            conn.execute(
                """select distinct cd.ballot_name, cd.person_id from candidacy cd
                   join contest_option o on o.id = cd.option_id
                   join slug s on s.entity_id = o.contest_id and s.is_current
                   where s.path like 'us/tx/laredo/%' and cd.ballot_name is not null"""
            ).fetchall()
        )
        values = []
        for contest_name, _, _, _ in CONTESTS:
            group = frame.filter(pl.col("contest") == contest_name)
            proposition = contest_name == "proposition-1"
            district = contest_name.startswith("district-")
            tail = f"council:{contest_name}" if district else contest_name
            key = PROPOSITION if proposition else f"{DAY}:TX:{PLACE}:{tail}"
            contest = cs.key("us.contest", key)
            if district:
                code = f"48:{PLACE}:council:{contest_name}"
                unit = cs.unit("us.local_district", code)
            else:
                unit = cs.unit("us.place", PLACE)
            if not proposition:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if district else "office",
                    office_id=None if district else office,
                    body_id=body if district else None,
                    district_area_id=cs.area("us.local_district", code) if district else cs.area("us.place", PLACE),
                    magnitude=1,
                    term="full",
                    round_number=1,
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                number = contest_name.removeprefix("district-")
                text = (
                    f"2026 Laredo City Council election, District {number}"
                    if district
                    else "2026 Laredo mayoral election"
                )
                cs.add("name", entity_id=contest, text=text, lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=f"{PATH}/{'council-' if district else ''}{contest_name}", entity_id=contest)
                cs.add("citation", entity_id=contest, source_id=source, locator=None)
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="preliminary",
                kind="counted",
                status="active",
                source_id=source,
                as_of=AS_OF,
                supersedes_id=None,
                is_default=True,
                completeness=1,
            )
            cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
            for position, row in enumerate(group.iter_rows(named=True), start=1):
                label = row["name"]
                if proposition:
                    option = cs.key("us.option", f"{key}:{label.lower()}")
                else:
                    option = cs.key("us.option", f"{key}:{slug(label)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_position=position,
                        ballot_label=label,
                    )
                    person = people.get(label) or cs.key(f"{SOURCE}.person", f"{tail}:{slug(label)}")
                    if person in cs.minted:
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=label, lang="en", kind="official", is_preferred=True)
                    candidacy = cs.key("us.candidacy", f"{key}:{slug(label)}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        is_independent=False,
                    )
                    cs.add("citation", entity_id=candidacy, source_id=source, locator=None)
                values.append(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": str(option),
                        "channel": 0,
                        "measure": 1,
                        "count_round": 1,
                        "flags": DIGITIZED,
                        "value": row["votes"],
                        "page_id": str(pages[row["page"]]),
                        "x0": 368 / WIDTH,
                        "y0": (row["y"] - 14) / HEIGHT,
                        "x1": 446 / WIDTH,
                        "y1": (row["y"] + 14) / HEIGHT,
                    }
                )
        results = pl.DataFrame(
            values,
            schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
        )
        cs.frame("result_value", results.drop("page_id", "x0", "y0", "x1", "y1"))
        cs.frame("value_locator", results.drop("flags", "value").with_columns(source_id=pl.lit(None, pl.String)))
        return cs.write()
