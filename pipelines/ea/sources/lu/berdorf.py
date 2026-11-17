"""Berdorf's communal election of 8 October 2023, from the commune's own bulletin.

The commune's council election, due on 11 June 2023 with every other commune's, was put off to 8 October 2023 after a
candidate died on 14 May 2023. The national elections site carries no results for it, so they come from the official
result the commune printed in its bulletin, the Gemengenzeitung 2023-3 (pages 16 and 17): the statistics of its four
polling stations and every candidate's votes in each of them. The tables are images, so each number was read from the
page twice and checked against the bulletin's own row and column totals; each value keeps its page and box.

The contest belongs to the 2023 communal election that `lu.elections` loads, and it uses that pipeline's commune,
council, and majority-vote electoral system.
"""

from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.lu import elections

SOURCE = "lu.berdorf"
KEY = f"{SOURCE}/2023-10-08"
BULLETIN = "https://www.berdorf.lu/media/44607303-2ca4-4643-bb0c-ed2839dd7a80/gemengenzeitung-03-2023.pdf"
LUXEMBOURG = str(uuid5(package.NAMESPACE, "polity\x1fLU"))
DAY = "2023-10-08"
SEATS = 9
PAGE_SIZE = (595, 842)  # A4, in PDF points; every box below is in points from the page's top left

REGISTERED, CAST, VALID, INVALID, VOTES = elections.REGISTERED, elections.CAST, elections.VALID, elections.INVALID, 1

# The four polling stations, as the bulletin names them. The first also counted the postal votes.
BUREAUX = (
    ("1", "Bureau principal 1 - Berdorf"),
    ("2", "Bureau 2 - Berdorf"),
    ("3", "Bureau 3 - Berdorf"),
    ("4", "Bureau 4 - Bollendorf-Pont"),
)

# Page 16, "Statistique des bureaux de votes": the column edges (four stations, then the total) and each row's band.
STATS_COLUMNS = (268.6, 333.0, 390.5, 448.4, 497.8, 549.0)
STATS = {
    # measure: (top, bottom, the four stations, the printed total)
    REGISTERED: (360.5, 381.0, (417, 291, 296, 154), 1158),
    CAST: (401.4, 416.0, (407, 269, 267, 137), 1080),
    INVALID: (488.9, 518.4, (18, 9, 10, 5), 42),
    VALID: (518.4, 535.8, (389, 260, 257, 132), 1038),
}
POSTAL_REGISTERED, POSTAL_CAST = 315, 313  # of the first station's registered voters and voters
VOTES_CAST = (2176, 1356, 1431, 645), 5608  # the "votes exprimés" row, and the "Total des voix" row of the result

# Pages 16 and 17, "Résultat officiel des élections": the column edges and row edges of each page's part of the table.
RESULT_COLUMNS = {
    16: (181.8, 246.5, 306.0, 365.2, 425.4, 479.0),
    17: (198.4, 254.5, 306.0, 357.1, 409.1, 455.4),
}
RESULT_ROWS = {
    16: (673.8, 692.2, 711.2, 730.6, 749.5),
    17: (48.1, 64.5, 80.6, 97.0, 113.5, 130.1, 146.4, 162.9, 179.2, 195.5, 211.9, 228.4),
}
# Each candidate as printed (surname first), with the votes in the four stations, the total, and the placement.
RESULT = (
    (16, "Adehm Guy", (136, 96, 95, 69), 396, 6, True),
    (16, "François Raymond", (118, 73, 79, 33), 303, 12, False),
    (16, "Hermann-Wetz Mandy", (130, 53, 68, 26), 277, 13, False),
    (16, "Kretz Sam", (136, 83, 62, 33), 314, 11, False),
    (17, "Muller Anne", (157, 95, 89, 33), 374, 7, True),
    (17, "Muller Mike", (99, 52, 68, 34), 253, 15, False),
    (17, "Nilles Joe", (245, 166, 181, 78), 670, 1, True),
    (17, "Scharff Daniel", (150, 110, 111, 43), 414, 4, True),
    (17, "Schmitt Simone", (120, 71, 84, 73), 348, 8, True),
    (17, "Scholtes Raoul", (156, 102, 105, 38), 401, 5, True),
    (17, "Schoos Jean", (125, 82, 95, 42), 344, 9, True),
    (17, "Speller Bernard", (140, 88, 74, 29), 331, 10, False),
    (17, "Streff Ben", (201, 136, 141, 35), 513, 2, True),
    (17, "Wanderscheid Daniel", (99, 52, 70, 34), 255, 14, False),
    (17, "Wintersdorf Marc", (164, 97, 109, 45), 415, 3, True),
)

METHOD = (
    "The official result of Berdorf's communal election of 8 October 2023, as the commune printed it in its bulletin "
    "(Gemengenzeitung 2023-3, pages 16 and 17), by polling station. The first station, the principal one in Berdorf, "
    "also counted the postal votes: 315 of its 417 registered voters had asked to vote by post, and 313 of its 407 "
    "voters did. Ballots cast are the voters (nombre des votants), and invalid ballots are the blank and spoiled "
    "ballots together, as the bulletin prints them. Each voter has nine votes, one for each seat, so the candidates' "
    "votes add up to more than the valid ballots. The bulletin prints the fourth station's registered voters as 1546, "
    "with the last digit running over the cell's border; the total of 1,158 and the turnout of 88.96 % printed "
    "beside it both give 154, which is stored. The numbers were read from the page images twice and checked against "
    "the bulletin's row and column totals."
)
CONTEXT = (
    "Berdorf's communal election was due on 11 June 2023 with those of the other communes. A council member who was "
    "a candidate died on 14 May 2023, so the election was put off and held on 8 October 2023, the day of the "
    "legislative election."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [KEY]


def fetch() -> None:
    raw.fetch(SOURCE, BULLETIN, suffix=".pdf")


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())


def verify() -> None:
    """Hold the transcription to the bulletin's own totals, so a misread number cannot pass."""
    for measure, (_, _, stations, total) in STATS.items():
        if sum(stations) != total:
            raise ValueError(f"measure {measure}: the stations add up to {sum(stations)}, the bulletin prints {total}")
    registered, cast = STATS[REGISTERED][2], STATS[CAST][2]
    for i, (valid, invalid) in enumerate(zip(STATS[VALID][2], STATS[INVALID][2], strict=True)):
        if valid + invalid != cast[i]:
            raise ValueError(f"station {i + 1}: {valid} valid and {invalid} invalid ballots for {cast[i]} voters")
    if round(100 * cast[3] / registered[3], 2) != 88.96:
        raise ValueError("the fourth station's registered voters do not give its printed turnout")
    for name, stations, total, _, _ in (row[1:] for row in RESULT):
        if sum(stations) != total:
            raise ValueError(f"{name}: the stations add up to {sum(stations)}, the bulletin prints {total}")
    by_station, total = VOTES_CAST
    for i, printed in enumerate(by_station):
        if sum(row[2][i] for row in RESULT) != printed:
            raise ValueError(f"station {i + 1}: the candidates' votes do not add up to the printed {printed}")
    if sum(row[3] for row in RESULT) != total:
        raise ValueError(f"the candidates' totals do not add up to the printed {total}")
    places = sorted(row[4] for row in RESULT)
    if places != list(range(1, len(RESULT) + 1)):
        raise ValueError("the placements are not a ranking")
    ranked = sorted(RESULT, key=lambda row: -row[3])
    if [row[4] for row in ranked] != places or [row[5] for row in ranked] != [n <= SEATS for n in places]:
        raise ValueError("the placements and the elected do not follow the votes")


def build() -> Package:
    verify()
    artifact = raw.cached(SOURCE, BULLETIN)
    p = Package(
        KEY,
        SOURCE,
        "Berdorf's communal election of 8 October 2023 by polling station, from the official result in the commune's "
        "bulletin",
        depends=[f"{elections.SOURCE}/geo", f"{elections.SOURCE}/registry", f"{elections.SOURCE}/2023-06-11"],
    )
    p.external.add(LUXEMBOURG)
    source = key("source", "gemengenzeitung-2023-3")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Administration communale de Berdorf",
        title="Gemengenzeitung 2023-3, the communal bulletin of Berdorf: Elections communales du 8 octobre 2023",
        url=BULLETIN,
        attribution="Administration communale de Berdorf",
        notes="The official result of the communal election of 8 October 2023 is printed on pages 16 and 17.",
    )
    artifact_id = p.artifact(artifact, source)
    pages = {}
    for number in (16, 17):
        pages[number] = key("page", number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact_id,
            page_number=number,
            image_key=f"pages/{SOURCE}/{artifact.path.stem}/{number}.png",
            width=PAGE_SIZE[0],
            height=PAGE_SIZE[1],
        )

    commune = elections.area("lu.commune", "Berdorf")
    election = elections.key("election", "communal-2023")
    contest = key("contest", DAY)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=elections.council("Berdorf"),
        district_area_id=commune,
        magnitude=SEATS,
        round_number=1,
        electoral_system_id=elections.SYSTEM_BLOCK_COMMUNE,
        default_result_kind="counted",
    )
    p.name(contest, "2023 Berdorf communal election", "en", "official")
    p.add("slug", path="lu/2023-06-11-communal/berdorf", entity_id=contest, is_current=True)
    p.add("note", id=key("note", contest), entity_id=contest, kind="context", body_md=CONTEXT, lang="en")
    p.add(
        "citation",
        id=key("citation", contest),
        entity_id=contest,
        source_id=source,
        locator={"file": "gemengenzeitung-03-2023.pdf", "pages": [16, 17]},
    )
    result_set = key("result_set", DAY)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")

    units = []
    for code, label in BUREAUX:
        unit = key("unit", code)
        units.append(unit)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=LUXEMBOURG,
            scheme_code="lu.count",
            code=f"{DAY}:berdorf:bureau-{code}",
            area_relation="within",
            area_id=commune,
            kind="standard",
        )
        p.name(unit, label, "fr", "official")

    values: list[tuple[str, str | None, int, int]] = []
    boxes: list[tuple[str, str | None, int, str, float, float, float, float]] = []
    for measure, (top, bottom, stations, total) in STATS.items():
        for i, value in enumerate(stations):
            values.append((units[i], None, measure, value))
            boxes.append((units[i], None, measure, pages[16], STATS_COLUMNS[i], top, STATS_COLUMNS[i + 1], bottom))
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=total)

    rows = {16: 0, 17: 0}
    for position, (number, name, stations, total, _, elected) in enumerate(RESULT, 1):
        surname, given = _split(name)
        person = key("person", name)
        p.add("person", id=person)
        p.name(person, f"{given} {surname}", "fr", "official")
        option = key("option", position)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if elected else "lost")
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=total)
        edges, columns = RESULT_ROWS[number], RESULT_COLUMNS[number]
        top, bottom = edges[rows[number]], edges[rows[number] + 1]
        rows[number] += 1
        for i, value in enumerate(stations):
            values.append((units[i], option, VOTES, value))
            boxes.append((units[i], option, VOTES, pages[number], columns[i], top, columns[i + 1], bottom))

    p.frame(
        "result_value",
        pl.DataFrame(values, schema=["unit_id", "option_id", "measure", "value"], orient="row").select(
            result_set_id=pl.lit(result_set),
            unit_id="unit_id",
            option_id="option_id",
            channel=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            value=pl.col("value").cast(pl.Int64),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            boxes,
            schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
            orient="row",
        ).select(
            result_set_id=pl.lit(result_set),
            unit_id="unit_id",
            option_id="option_id",
            channel=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            page_id="page_id",
            source_id=pl.lit(source),
            x0="x0",
            y0="y0",
            x1="x1",
            y1="y1",
        ),
    )
    print(f"{KEY}: {len(RESULT)} candidates, {len(values)} values", flush=True)
    return p


def _split(printed: str) -> tuple[str, str]:
    """Split a name printed surname first into the surname and the given name."""
    surname, given = printed.rsplit(" ", 1)
    return surname, given
