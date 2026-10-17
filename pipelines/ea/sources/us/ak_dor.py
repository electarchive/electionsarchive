import io
import json
import re
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.fec import NOTES
from ea.store import DATA, connect

SOURCE = "us.ak_dor"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ORIGIN = "https://www.elections.alaska.gov/results/"
WAYBACK = "https://web.archive.org/web/{}id_/" + ORIGIN + "{}"
PUBLISHER = "Alaska Division of Elections"
SHAPES = (
    "https://web.archive.org/web/20220904151037id_/"
    "https://www.elections.alaska.gov/doc/info/AK_Election_Precincts_2022Interim.zip"
)
SHAPES_TITLE = "Alaska Election Precincts, 2022 interim plan"
SPECIAL = "2022-08-16:AK:house:0:special"
CONTESTS: dict[str, tuple[str, tuple[str, str], tuple[str, str] | None, str, list[dict[str | None, int]], int]] = {
    SPECIAL: (
        "RCV Detailed Report: U.S. Representative (Special General), August 16, 2022",
        ("20220909051113", "22SSPG/RcvDetailedReport.txt"),
        ("20220909051114", "22SSPG/CVR_Export_20220908084311.zip"),
        "U.S. Representative (Special General)",
        [
            {"Begich, Nick": 53810, "Palin, Sarah": 58973, "Peltola, Mary S.": 75799, None: 3707},
            {"Palin, Sarah": 86026, "Peltola, Mary S.": 91266, None: 14997},
        ],
        192289,
    ),
    "2022-11-08:AK:senate": (
        "RCV Detailed Report: U.S. Senator, November 8, 2022 General Election",
        ("20221207225236", "22GENR/US%20SEN.txt"),
        ("20221223104437", "22GENR/rcv/CVR_Export.zip"),
        "U.S. Senator",
        [
            {
                "Chesbro, Patricia R.": 28233,
                "Kelley, Buzz A.": 8575,
                "Murkowski, Lisa": 114118,
                "Tshibaka, Kelly C.": 112101,
                None: 3770,
            },
            {"Chesbro, Patricia R.": 29134, "Murkowski, Lisa": 115759, "Tshibaka, Kelly C.": 115310, None: 6594},
            {"Murkowski, Lisa": 136330, "Tshibaka, Kelly C.": 117534, None: 12933},
        ],
        266797,
    ),
    "2022-11-08:AK:house:0": (
        "RCV Detailed Report: U.S. Representative, November 8, 2022 General Election",
        ("20221207225235", "22GENR/US%20REP.txt"),
        ("20221223104437", "22GENR/rcv/CVR_Export.zip"),
        "U.S. Representative",
        [
            {"Begich, Nick": 62505, "Bye, Chris": 4999, "Palin, Sarah": 68330, "Peltola, Mary S.": 128755, None: 2208},
            {"Begich, Nick": 64499, "Palin, Sarah": 69399, "Peltola, Mary S.": 129786, None: 3113},
            {"Palin, Sarah": 112471, "Peltola, Mary S.": 137263, None: 17063},
        ],
        266797,
    ),
    "2024-11-05:AK:house:0": (
        "RCV Detailed Report: U.S. Representative, November 5, 2024 General Election",
        ("20241204164835", "24GENR/RCV-USRep.pdf"),
        None,
        "U.S. Representative",
        [
            {
                "Begich, Nick": 159777,
                "Hafner, Eric": 3558,
                "Howe, John Wayne": 13210,
                "Peltola, Mary S.": 152948,
                None: 6360,
            },
            {"Begich, Nick": 160044, "Howe, John Wayne": 13871, "Peltola, Mary S.": 154261, None: 7677},
            {"Begich, Nick": 164861, "Peltola, Mary S.": 156985, None: 14007},
        ],
        335853,
    ),
}
OFFICIAL = (
    "The Division of Elections' ranked-choice tabulation. Ballots exhausted in a round are the report's blank "
    "ballots, overvotes, and exhausted ballots together. Write-in votes are set aside before the first round, so a "
    "ballot that ranks a write-in counts for its next choice. Each candidate's value at round n is their running "
    "total; an eliminated candidate has 0 at the round of their elimination."
)
UNMATCHED = (
    " The cast vote records the Division published for this election hold 340,778 ballots for this race, 4,925 more "
    "than the tabulation counted, most of them absentee and questioned ballots, so they are not used for a breakdown "
    "by precinct."
)
DERIVED = (
    "Tabulated by electionsarchive.com from the Division of Elections' cast vote records, precinct by precinct. A "
    "ballot exhausts at an overvote or after two skipped rankings in a row, a repeated candidate counts as a skipped "
    "ranking, and write-ins are passed over, as the Division's tabulation has them. Each round drops the candidates "
    "the official count dropped. Absentee, early, and questioned ballots are counted by state house district and "
    "overseas federal absentee ballots statewide, as the Division reports them. Summed over the state, every round "
    "reproduces the official count exactly."
)


def fetch() -> None:
    downloads = [(SHAPES, ".zip")]
    for _, report, records, _, _, _ in CONTESTS.values():
        downloads.append((WAYBACK.format(*report), "." + report[1].rsplit(".", 1)[1]))
        if records is not None:
            downloads.append((WAYBACK.format(*records), ".zip"))
    for url, suffix in dict.fromkeys(downloads):
        for attempt in range(5):
            try:
                raw.fetch(SOURCE, url, suffix=suffix)
                break
            except httpx.HTTPError:
                time.sleep(10 * 2**attempt)
        else:
            raw.fetch(SOURCE, url, suffix=suffix)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames = []
    for records in dict.fromkeys(records for _, _, records, _, _, _ in CONTESTS.values() if records is not None):
        with zipfile.ZipFile(raw.cached(SOURCE, WAYBACK.format(*records)).path) as archive:
            manifest = {
                name: json.loads(archive.read(f"{name}Manifest.json"))["List"]
                for name in ("Contest", "Candidate", "Precinct", "PrecinctPortion")
            }
            precincts = {}
            for precinct in manifest["Precinct"]:
                description = re.sub(r"\s+", " ", precinct["Description"]).strip()
                if re.match(r"\d\d[- ]\d\d\d ", description):
                    if not re.fullmatch(r"\d\d-\d\d\d", precinct["ExternalId"]):
                        raise ValueError(f"{records}: precinct {description} has no district-precinct number")
                    precincts[precinct["Id"]] = precinct["ExternalId"]
                elif found := re.fullmatch(r"District (\d+) - (Absentee|Early Voting|Question)", description):
                    precincts[precinct["Id"]] = f"{int(found[1]):02d}:{found[2].split()[0].lower()}"
                elif description == "HD99 Fed Overseas Absentee":
                    precincts[precinct["Id"]] = "overseas"
                else:
                    raise ValueError(f"{records}: no unit for precinct {description}")
            portions = {portion["Id"]: precincts[portion["PrecinctId"]] for portion in manifest["PrecinctPortion"]}
            wanted = {
                key: next(contest["Id"] for contest in manifest["Contest"] if contest["Description"] == description)
                for key, (_, _, source, description, _, _) in CONTESTS.items()
                if source == records
            }
            names = {candidate["Id"]: candidate["Description"] for candidate in manifest["Candidate"]}
            ballots: dict[int, list[tuple[str, list[set[str]]]]] = {contest: [] for contest in wanted.values()}
            for member in archive.namelist():
                if not member.startswith("CvrExport"):
                    continue
                for session in json.loads(archive.read(member))["Sessions"]:
                    side = session.get("Modified") or session["Original"]
                    for card in side["Cards"]:
                        for contest in card["Contests"]:
                            if contest["Id"] not in ballots:
                                continue
                            ranks: dict[int, set[str]] = {}
                            for mark in contest["Marks"]:
                                if not mark["IsAmbiguous"]:
                                    ranks.setdefault(mark["Rank"], set()).add(names[mark["CandidateId"]])
                            ballots[contest["Id"]].append(
                                (
                                    portions[side["PrecinctPortionId"]],
                                    [ranks.get(rank, set()) for rank in range(1, max(ranks, default=0) + 1)],
                                )
                            )
        for key, contest in wanted.items():
            rounds, total = CONTESTS[key][4], CONTESTS[key][5]
            if len(ballots[contest]) != total:
                raise ValueError(f"{key}: {len(ballots[contest])} records, the official count has {total} ballots")
            continuing = [{label for label in tally if label is not None} for tally in rounds]
            counts: dict[tuple[str, int, str | None], int] = {}
            for unit, ranking in ballots[contest]:
                for number, standing in enumerate(continuing, 1):
                    top, skips, seen = None, 0, set()
                    for marks in ranking:
                        if not marks:
                            skips += 1
                            if skips == 2:
                                break
                            continue
                        if len(marks) > 1:
                            break
                        (choice,) = marks
                        if choice in seen:
                            skips += 1
                            if skips == 2:
                                break
                            continue
                        skips = 0
                        seen.add(choice)
                        if choice in standing:
                            top = choice
                            break
                    counts[(unit, number, top)] = counts.get((unit, number, top), 0) + 1
            units = sorted({unit for unit, _, _ in counts})
            tally = pl.DataFrame(
                [
                    *((unit, number, label, votes) for (unit, number, label), votes in counts.items()),
                    *(
                        (unit, number, label, 0)
                        for number in range(2, len(continuing) + 1)
                        for label in sorted(continuing[number - 2] - continuing[number - 1])
                        for unit in units
                    ),
                ],
                schema={"unit": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Int64},
                orient="row",
            )
            sums = tally.group_by("round", "label").agg(pl.col("votes").sum())
            for number, official in enumerate(rounds, 1):
                found = {
                    label: votes for _, label, votes in sums.filter(pl.col("round") == number).iter_rows() if votes
                }
                if found != official:
                    raise ValueError(f"{key} round {number}: the records give {found}, the official count {official}")
            frames.append(tally.with_columns(key=pl.lit(key)))
    pl.concat(frames).write_parquet(PARSED / "tallies.parquet")

    with zipfile.ZipFile(raw.cached(SOURCE, SHAPES).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    shapes = []
    for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
        if record is None or shape is None:
            continue
        label = re.sub(r"\s+", " ", str(record["Precinct_N"])).strip()
        shapes.append((label.split()[0], label, json.dumps(shape.__geo_interface__)))
    with connect() as conn:
        pl.DataFrame(
            conn.execute(
                """select f.code, f.label, county.code, county.name, encode(st_asewkb(s.geom), 'hex'),
                     encode(st_asewkb(st_pointonsurface(s.geom)), 'hex')
                   from unnest(%s::text[], %s::text[], %s::text[]) as f(code, label, geojson)
                   cross join lateral (select st_multi(st_collectionextract(st_makevalid(st_transform(
                     st_setsrid(st_geomfromgeojson(f.geojson), 3857), 4326)), 3)) as geom) s
                   cross join lateral (
                     select c.code, (select n.text from name n where n.entity_id = a.id and n.is_preferred limit 1)
                       as name
                     from area a join area_code c on c.area_id = a.id join area_geometry g on g.area_id = a.id
                     where a.scheme_code = 'us.county' and c.code like '02%%' and st_intersects(g.geom, s.geom)
                     order by st_area(st_intersection(g.geom, s.geom)) desc limit 1) county""",
                [[shape[0] for shape in shapes], [shape[1] for shape in shapes], [shape[2] for shape in shapes]],
            ).fetchall(),
            schema=["code", "label", "county", "county_name", "geom", "point"],
            orient="row",
        ).write_parquet(PARSED / "precincts.parquet")


def emit() -> Path:
    tallies = pl.read_parquet(PARSED / "tallies.parquet")
    precincts = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Alaska's ranked-choice contests for the U.S. Senate and House, 2022 to 2024, with the official "
            "rounds and each round by precinct from the cast vote records",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        state = cs.polity("US-AK")
        state_area = cs.area("us.state", "02")
        first = date(2022, 8, 16)
        shapes = cs.source(PUBLISHER, SHAPES_TITLE)
        cs.add(
            "source",
            id=shapes,
            kind="official_body",
            publisher=PUBLISHER,
            title=SHAPES_TITLE,
            url="https://www.elections.alaska.gov/doc/info/AK_Election_Precincts_2022Interim.zip",
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, SHAPES), shapes)
        for code, label, county, county_name, geom, point in precincts.iter_rows():
            full = f"2022:AK:{code}"
            area = cs.area("us.precinct", full)
            cs.add("area", id=area, scheme_code="us.precinct")
            cs.add("area_code", area_id=area, code=full, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.county", county))
            name(area, f"{label}, {county_name}")
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=first,
                valid_to=date(2022, 11, 9),
                geom=geom,
                label_point=point,
                quality="official",
                source_id=shapes,
            )
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", full),
                polity_id=state,
                scheme_code="us.precinct",
                code=full,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=first,
            )
        cs.add(
            "boundary_set",
            scheme_code="us.precinct",
            vintage="2022-08-16",
            valid_from=first,
            valid_to=first + timedelta(days=1),
            as_of=first,
        )
        precinct_codes = set(precincts["code"])
        for unit in sorted(set(tallies["unit"]) - precinct_codes):
            cs.add(
                "reporting_unit",
                id=cs.unit("us.aggregate", f"AK:2022:{unit}"),
                polity_id=state,
                scheme_code="us.aggregate",
                code=f"AK:2022:{unit}",
                area_relation="within",
                area_id=state_area,
                kind="aggregate",
                valid_from=first,
            )
        units = {
            unit: cs.unit("us.precinct", f"2022:AK:{unit}")
            if unit in precinct_codes
            else cs.unit("us.aggregate", f"AK:2022:{unit}")
            for unit in set(tallies["unit"])
        }

        for key, (title, report, records, _, rounds, total) in CONTESTS.items():
            day = date.fromisoformat(key[:10])
            senate = ":senate" in key
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=ORIGIN + report[1],
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(*report)), source)
            if records is not None:
                cs.artifact(raw.cached(SOURCE, WAYBACK.format(*records)), source)

            contest = cs.key("us.contest", key)
            options: dict[str, UUID] = {}
            if key == SPECIAL:
                general = cs.key("us.contest", "2022-11-08:AK:house:0")
                row = conn.execute("select district_area_id, body_id from contest where id = %s", [general]).fetchone()
                plan = conn.execute(
                    "select id from boundary_set where scheme_code = 'us.cd' and vintage = '117'"
                ).fetchone()
                if general in cs.minted or row is None or plan is None:
                    raise ValueError("the 2022 Alaska House general and the 117th Congress's plan must already exist")
                election = cs.key("us.election", "2022-08-16:AK:special")
                cs.add(
                    "election",
                    id=election,
                    polity_id=state,
                    parent_id=None,
                    kind="special",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, "August 16, 2022 Alaska special election")
                cs.add("slug", path="us/ak/2022-08-16-special", entity_id=election)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=row[1],
                    district_area_id=row[0],
                    seat_id=None,
                    term="remainder",
                    boundary_set_id=plan[0],
                    party_scope_id=None,
                    magnitude=1,
                    round_number=1,
                    previous_round_id=None,
                    electoral_system_id=cs.key("us.electoral_system", "irv"),
                    default_result_kind="counted",
                )
                name(contest, "2022 U.S. House special election in Alaska's at-large congressional district")
                cs.add("slug", path="us/ak/2022-08-16-special/house-at-large-special", entity_id=contest)
                for option, party, label, person in conn.execute(
                    """select o.id, o.party_id, o.ballot_label, c.person_id from contest_option o
                       join candidacy c on c.option_id = o.id where o.contest_id = %s and o.kind = 'candidate'""",
                    [general],
                ).fetchall():
                    surname = next(
                        (text for text in rounds[0] if text is not None and text.split(",")[0] in label), None
                    )
                    if surname is None:
                        continue
                    options[surname] = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=options[surname],
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=label,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=options[surname],
                        role="head",
                        ballot_name=label,
                        is_independent=False,
                        is_incumbent=False,
                    )
                if len(options) != len(rounds[0]) - 1:
                    raise ValueError(f"{key}: options for {sorted(options)} only")
            else:
                row = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()
                if contest in cs.minted or row is None:
                    raise ValueError(f"{key} must already exist")
                election = row[0]
                existing = conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall()
                for text in (text for text in rounds[0] if text is not None):
                    found = [option for option, label in existing if text.split(",")[0] in label]
                    if len(found) != 1:
                        raise ValueError(f"{key}: {len(found)} options for {text}")
                    options[text] = found[0]
                federal = cs.key("us.fec.result_set", key)
                if federal in cs.minted:
                    raise ValueError(f"{key}: us.fec's result set must already exist")
                kind, body = NOTES[key]
                for (note,) in conn.execute(
                    "select id from note where entity_id = %s and kind = %s", [federal, kind]
                ).fetchall():
                    cs.add("note", id=note, entity_id=federal, kind=kind, body_md=body)
                for previous in conn.execute(
                    """select id, election_id, contest_id, stage, kind, status, source_id, as_of, completeness,
                         supersedes_id from result_set where id = %s""",
                    [federal],
                ).fetchall():
                    cs.add(
                        "result_set",
                        **dict(
                            zip(
                                (
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
                                ),
                                previous,
                                strict=True,
                            )
                        ),
                        is_default=False,
                    )

            top = cs.unit("us.state", "02") if senate else cs.unit("us.cd", "0200", day)
            official = cs.key(f"{SOURCE}.result_set", key)
            final = rounds[-1]
            winner = max((label for label in final if label is not None), key=lambda label: final[label])
            for label, option in options.items():
                cs.add(
                    "outcome",
                    result_set_id=official,
                    option_id=option,
                    kind="won" if label == winner else "lost" if label in final else "eliminated",
                )
            sets = [
                (
                    official,
                    True,
                    OFFICIAL + ("" if records is not None else UNMATCHED),
                    pl.DataFrame(
                        [
                            (str(top), number, label, tally.get(label, 0))
                            for number, tally in enumerate(rounds, 1)
                            for label in rounds[0]
                            if label in tally or label in rounds[number - 2]
                        ]
                        + [(str(top), 0, "ballots", total)],
                        schema={"unit_id": pl.String, "round": pl.Int16, "label": pl.String, "votes": pl.Int64},
                        orient="row",
                    ),
                    0,
                )
            ]
            if records is not None:
                rows = (
                    tallies.filter(pl.col("key") == key)
                    .group_by("unit", "round", "label")
                    .agg(pl.col("votes").sum())
                    .with_columns(unit_id=pl.col("unit").replace_strict({unit: str(id) for unit, id in units.items()}))
                )
                if not senate:
                    cs.frame(
                        "reporting_unit_area",
                        pl.DataFrame(
                            {
                                "unit_id": sorted(set(rows["unit_id"])),
                                "area_id": str(cs.area("us.cd", "0200", day)),
                                "valid_from": day,
                                "valid_to": day + timedelta(days=1),
                            }
                        ),
                    )
                rows = rows.select("unit_id", "round", "label", "votes")
                sets.append(
                    (
                        cs.key(f"{SOURCE}.result_set", f"{key}:precincts"),
                        False,
                        DERIVED,
                        pl.concat(
                            [
                                rows,
                                rows.filter(pl.col("round") == 1)
                                .group_by("unit_id")
                                .agg(pl.col("votes").sum())
                                .select("unit_id", round=pl.lit(0, pl.Int16), label=pl.lit("ballots"), votes="votes"),
                            ]
                        ),
                        1,
                    )
                )

            for result_set, default, note, values, flags in sets:
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=default,
                    completeness=1,
                )
                if result_set not in cs.minted:
                    continue
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                unknown = set(values["label"].drop_nulls()) - set(options) - {"ballots"}
                if unknown:
                    raise ValueError(f"{key}: no options for {unknown}")
                frame = pl.concat(
                    [
                        values,
                        values.filter(pl.col("round") == 1, pl.col("label").is_not_null())
                        .group_by("unit_id")
                        .agg(pl.col("votes").sum())
                        .select("unit_id", round=pl.lit(1, pl.Int16), label=pl.lit("valid"), votes="votes"),
                    ]
                )
                cs.frame(
                    "result_value",
                    frame.select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id="unit_id",
                        option_id=pl.col("label").replace_strict(
                            {label: str(option) for label, option in options.items()}, default=None
                        ),
                        channel=pl.lit(0, pl.Int16),
                        measure=pl.when(pl.col("label").is_null())
                        .then(21)
                        .otherwise(pl.col("label").replace_strict({"ballots": 3, "valid": 4}, default=1))
                        .cast(pl.Int16),
                        count_round=pl.col("round").cast(pl.Int16),
                        flags=pl.lit(flags, pl.Int16),
                        value="votes",
                    ),
                )
        return cs.write()
