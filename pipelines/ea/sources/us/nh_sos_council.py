import io
import json
import re
import subprocess
import time
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import nh_sos_legislature, nh_sos_primaries
from ea.sources.us.nh_sos_legislature import town
from ea.store import DATA, connect
from ea.typed import one, present

SOURCE = "us.nh_sos_council"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "New Hampshire Secretary of State"
WAYBACK = nh_sos_legislature.WAYBACK.format(snapshot="2026")
GENERALS = {
    2016: "2016-ge-executive-council-district-1-5.xls",
    2018: "2018-ge-executive-council-district-1-5.xls",
    2020: "2020-executive-council-district-1-5.xls",
    2022: "2022-executive-council-district-1-5_0.xls",
    2024: "2024-ge-executive-council-district-1-5_4.xls",
}
PRIMARIES = {
    (2016, "democratic"): "2016-sp-executive-council-1-5-democratic.xls",
    (2016, "republican"): "2016-sp-executive-council-1-5-republican-.xls",
    (2018, "democratic"): "2018-sp-executive-council-1-5-democratic-_0.xls",
    (2018, "republican"): "2018-sp-executive-council-1-5-republican-_0.xls",
    (2018, "libertarian"): "2018-sp-executive-council-1-5-libertarian-_0.xls",
    (2020, "democratic"): "2020-sp-executive-council-1-5-democratic-.xls",
    (2020, "republican"): "2020-sp-executive-council-1-5-republican-.xls",
    (2022, "democratic"): "2022-sp-executive-council-1-5-democratic-_2.xls",
    (2022, "republican"): "2022-sp-executive-council-1-5-republican-_0.xls",
    (2024, "democratic"): "2024-sp-executive-council-1-5-democratic_2.xls",
    (2024, "republican"): "2024-sp-executive-council-1-5-republican_2.xls",
}
GENERAL_DAYS = {**nh_sos_legislature.DAYS, 2026: date(2026, 11, 3)}
PRIMARY_DAYS = nh_sos_primaries.DAYS
GENERAL_PAGES = nh_sos_legislature.PAGES
PRIMARY_PAGES = nh_sos_primaries.PAGES
CANDIDATES = "https://mm.nh.gov/files/uploads/sos/docs/2026-ge-candidate-list.pdf"
CANDIDATES_INBOX = DATA / "inbox" / "manual" / "MF-132" / "2026-ge-candidate-list.pdf"
GRANIT = (
    "https://ftp.granit.unh.edu/GRANIT_Data/Vector_Data/Administrative_and_Political_Boundaries/d-executivecouncil/"
)
SHAPES = {2012: GRANIT + "2012/NHExecCo2012.zip", 2022: GRANIT + "NHExecDistricts2022.zip"}
SHAPE_FIELDS = {2012: "EXECCO2012", 2022: "ExecCo2022"}
PLANS = {2012: (date(2012, 1, 1), date(2022, 1, 1)), 2022: (date(2022, 1, 1), None)}
PARTIES = {
    "r": "republican-party",
    "d": "democratic-party",
    "l": "libertarian-party",
    "lib": "libertarian-party",
    "DEM": "democratic-party",
    "REP": "republican-party",
}
SIDES = {"democratic": "d", "republican": "r", "libertarian": "l"}
POOLED = re.compile(r"(?i)scatter\w*|write-?ins?")
IGNORED = re.compile(r"(?i)(?:under|over)\s*votes?")
NUMBER = re.compile(r"\d[\d,]*(?:\.0+)?|[-−–]")
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
ALIASES = {"atkinsonandgilmacademygrant": "atkinsonandgilmantonacademygrant"}
MISSPELLED = {"Shoshanna Kelley": "Shoshanna Kelly"}


def unit_key(text: str) -> str:
    key = town(text.rstrip("*").strip())
    return ALIASES.get(key, key)


def files() -> list[tuple[int, str, str | None, str]]:
    return [
        *((year, "general", None, name) for year, name in GENERALS.items()),
        *((year, "primary", party, name) for (year, party), name in PRIMARIES.items()),
    ]


def fetch() -> None:
    for url in SHAPES.values():
        raw.fetch(SOURCE, url, suffix=".zip")
    try:
        raw.cached(SOURCE, CANDIDATES)
    except LookupError:
        raw.store(
            SOURCE,
            CANDIDATES,
            CANDIDATES_INBOX.read_bytes(),
            suffix=".pdf",
            retrieved_at=datetime.fromtimestamp(CANDIDATES_INBOX.stat().st_mtime, UTC),
            media_type="application/pdf",
        )
    for *_, name in files():
        url = WAYBACK + name
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        for attempt in range(6):
            try:
                raw.fetch(SOURCE, url, suffix=Path(name).suffix)
                break
            except httpx.HTTPError, OSError:
                time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(f"not fetched: {name}")
        time.sleep(1.2)


def count(cell: str) -> int:
    return 0 if cell in "-−–" else int(float(cell.replace(",", "")))


def candidate(label: str) -> tuple[str, str | None]:
    found = re.fullmatch(r"(.+?),\s*([a-z]{1,3})\.?(?:\s*\(?w-?in\)?)?", label, flags=re.IGNORECASE)
    return (found[1].strip(), found[2].lower()) if found else (label.strip(), None)


def parse() -> None:
    rows = []
    for year, stage, party, name in files():
        sheets = pl.read_excel(
            raw.cached(SOURCE, WAYBACK + name).path,
            sheet_id=0,
            has_header=False,
            infer_schema_length=0,
            raise_if_empty=False,
        )
        districts = set()
        for sheet_name, sheet in sheets.items():
            lines = [[" ".join(cell.split()) if cell else "" for cell in row] for row in sheet.rows()]
            district = None
            labels: dict[int, str] = {}
            block: list[tuple[str, str, int]] = []
            for cells in lines:
                head = cells[0]
                if found := re.search(r"District No\.?\s*(\d)", head):
                    district = int(found[1])
                    continue
                if district is None:
                    continue
                if not labels:
                    labels = {
                        index: cell for index, cell in enumerate(cells) if index and cell and not NUMBER.fullmatch(cell)
                    }
                    continue
                if not head or head.startswith("*"):
                    continue
                if head.lower().startswith("total"):
                    for index, label in labels.items():
                        summed = sum(votes for column, _, votes in block if column == label)
                        if index >= len(cells) or not cells[index]:
                            print(f"{name} {sheet_name} {label}: no printed total, towns add to {summed}")
                        elif count(cells[index]) != summed:
                            message = f"{name} {sheet_name} {label}: towns add to {summed}, printed {cells[index]}"
                            ballot_name, side = candidate(label)
                            if POOLED.fullmatch(ballot_name) or (stage == "primary" and side != SIDES[str(party)]):
                                print(message)
                            else:
                                raise ValueError(message)
                    break
                if re.fullmatch(r"(?i)(?:court ordered )?recount", head):
                    raise ValueError(f"{name} {sheet_name}: a recount row under {block[-1][1]}")
                block.extend(
                    (label, head, count(cells[index]))
                    for index, label in labels.items()
                    if index < len(cells) and cells[index]
                )
            else:
                raise ValueError(f"{name} {sheet_name}: no totals row")
            if district in districts:
                raise ValueError(f"{name}: district {district} twice")
            districts.add(district)
            rows.extend((year, stage, party, district, label, place, votes) for label, place, votes in block)
        if districts != {1, 2, 3, 4, 5}:
            raise ValueError(f"{name}: districts {sorted(districts)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(
        rows,
        schema={
            "year": pl.Int32,
            "stage": pl.String,
            "party": pl.String,
            "district": pl.Int32,
            "label": pl.String,
            "town": pl.String,
            "votes": pl.Int64,
        },
        orient="row",
    )
    frame.write_parquet(PARSED / "towns.parquet")
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, CANDIDATES).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    ballot: list[tuple[int, str, str]] = []
    district = None
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "Executive Councilor":
            inside = True
            continue
        if not inside or not stripped or line.startswith(" ") or stripped.startswith("Candidate Name"):
            continue
        if found := re.fullmatch(r"District (\d)", stripped):
            district = int(found[1])
            continue
        fields = re.split(r"\s{2,}", stripped)
        if len(fields) < 4:
            break
        ballot.append((present(district, "district"), fields[0], fields[-1].split()[-1]))
    if sorted({row[0] for row in ballot}) != [1, 2, 3, 4, 5] or len(ballot) != 10:
        raise ValueError(f"2026 candidate list: {ballot}")
    pl.DataFrame(
        ballot, schema={"district": pl.Int32, "candidate": pl.String, "party": pl.String}, orient="row"
    ).write_parquet(PARSED / "ballot.parquet")
    print(frame.group_by("year", "stage", "party").agg(pl.col("votes").sum()).sort("year", "stage", "party"))


def words(label: str) -> list[str]:
    return [
        part for part in re.sub(r"[^a-z ]", "", re.sub(r"[.\-]", " ", label.lower())).split() if part not in SUFFIXES
    ]


def emit() -> Path:
    towns = pl.read_parquet(PARSED / "towns.parquet")
    ballot = pl.read_parquet(PARSED / "ballot.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire Executive Council: the 2016 to 2024 generals and party primaries by town and ward "
            "from the Secretary of State's workbooks, the 2026 general candidates, and GRANIT shapes for the 2012 "
            "and 2022 district plans",
            polity="US-NH",
        )

        def name(entity: object, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def source(title: str, url: str, publisher: str = PUBLISHER) -> UUID:
            found = cs.source(publisher, title)
            if found in cs.minted:
                cs.add(
                    "source",
                    id=found,
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=url,
                    license="Public record",
                )
            return found

        polity = cs.polity("US-NH")
        state = cs.area("us.state", "33")
        system = cs.key("us.electoral_system", "fptp")
        body = cs.key("us.body", "NH:executive-council")
        if body in cs.minted:
            raise ValueError("the New Hampshire Executive Council body must already exist")
        for key in set(PARTIES.values()):
            if cs.key("us.party", key) in cs.minted:
                raise ValueError(f"party {key} must already exist")

        granit = source(
            "New Hampshire Executive Council Districts",
            "https://granit.unh.edu/data/downloadfreedata/category/databycategory.html",
            "NH GRANIT",
        )
        districts: dict[tuple[int, int], UUID] = {}
        for plan, url in SHAPES.items():
            artifact = raw.cached(SOURCE, url)
            cs.artifact(artifact, granit)
            with zipfile.ZipFile(artifact.path) as archive:
                parts = {
                    item.rsplit(".", 1)[1].lower(): io.BytesIO(archive.read(item))
                    for item in archive.namelist()
                    if item.lower().endswith((".shp", ".shx", ".dbf"))
                }
            reader = shapefile.Reader(shp=parts["shp"], shx=parts.get("shx"), dbf=parts["dbf"])
            shapes: dict[int, list[str]] = {}
            for record, item in zip(reader.iterRecords(), reader.iterShapes(), strict=True):
                number = int(str(present(record, "record")[SHAPE_FIELDS[plan]]))
                if number:
                    shapes.setdefault(number, []).append(json.dumps(present(item, "shape").__geo_interface__))
            if sorted(shapes) != [1, 2, 3, 4, 5]:
                raise ValueError(f"{plan} plan: districts {sorted(shapes)}")
            valid_from, valid_to = PLANS[plan]
            for number, pieces in shapes.items():
                code = f"33:executive-council:{number}:{plan}"
                area = cs.area("us.local_district", code)
                unit = cs.unit("us.local_district", code)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code="us.local_district")
                    cs.add("area_code", area_id=area, code=code, valid_from=valid_from, valid_to=valid_to)
                    cs.add("area_parent", area_id=area, parent_id=state, valid_from=valid_from, valid_to=valid_to)
                    name(area, f"New Hampshire Executive Council District {number} ({plan} plan)")
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="us.local_district",
                        code=code,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                        valid_from=valid_from,
                        valid_to=valid_to,
                    )
                geom, point = one(
                    conn.execute(
                        """select encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                       from (select st_multi(st_collectionextract(st_makevalid(st_transform(st_union(
                               st_setsrid(st_geomfromgeojson(piece), 3437)), 4326)), 3)) as geom
                             from unnest(%s::text[]) piece) shape""",
                        [pieces],
                    )
                )
                cs.add(
                    "area_geometry",
                    area_id=area,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    geom=geom,
                    label_point=point,
                    quality="official",
                    source_id=granit,
                )
                districts[(plan, number)] = area

        known: dict[str, set[UUID]] = {}
        council: dict[tuple[str, str], set[UUID]] = {}
        for label, person, is_council in conn.execute(
            """select distinct c.ballot_name, c.person_id, k.body_id = %s from candidacy c
               join contest_option o on o.id = c.option_id
               join contest k on k.id = o.contest_id
               join external_id x on x.entity_id = k.id and x.scheme = 'us.contest'
               where x.value ~ '^\\d{4}-\\d\\d-\\d\\d:NH:' and c.person_id is not null and c.ballot_name is not null""",
            [body],
        ).fetchall():
            parts = words(label)
            if len(parts) < 2:
                continue
            known.setdefault(f"{parts[0]} {parts[-1]}", set()).add(person)
            if is_council:
                council.setdefault((parts[-1], parts[0][0]), set()).add(person)
        people: dict[tuple[str, str], UUID] = {}

        def person(label: str) -> UUID:
            label = MISSPELLED.get(label, label)
            parts = words(label)
            key = (parts[-1], parts[0][0])
            if key not in people:
                hits = council.get(key, set())
                if len(hits) != 1:
                    hits = known.get(f"{parts[0]} {parts[-1]}", set())
                if len(hits) == 1:
                    people[key] = next(iter(hits))
                else:
                    found = cs.key(f"{SOURCE}.person", f"{key[0]}:{key[1]}")
                    if found in cs.minted:
                        cs.add("person", id=found)
                    name(found, label)
                    people[key] = found
            return people[key]

        def election(value: str) -> UUID:
            found = cs.key("us.election", value)
            if found in cs.minted:
                raise ValueError(f"election {value} must already exist")
            return found

        candidates = source("2026 general election candidate list", CANDIDATES)
        cs.artifact(raw.cached(SOURCE, CANDIDATES), candidates)
        sources: dict[tuple[int, str], UUID] = {}
        for year, stage, _, file in files():
            if (year, stage) not in sources:
                title = f"{year} {'general' if stage == 'general' else 'state primary'} election results, Executive Council, by town and ward"
                sources[(year, stage)] = source(title, (GENERAL_PAGES if stage == "general" else PRIMARY_PAGES)[year])
            cs.artifact(raw.cached(SOURCE, WAYBACK + file), sources[(year, stage)])

        units: dict[int, dict[str, str]] = {}
        for year in sorted(set(towns["year"])):
            units[year] = {}
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.precinct' and code like %s order by code",
                [f"{year}:NH:%%"],
            ).fetchall():
                parts = code.split(":", 4)
                if len(parts) == 5:
                    units[year].setdefault(unit_key(parts[4] or parts[3]), code)
            missing = sorted(
                {
                    place
                    for place in towns.filter(pl.col("year") == year, pl.col("votes") > 0)["town"]
                    if unit_key(place) not in units[year]
                }
            )
            if missing:
                raise ValueError(f"towns with no {year} unit: {missing}")

        counts = {"contests": 0, "result sets": 0, "values": 0}
        values = []
        groups = [
            *(((year, "general", None, number), None) for year in [2026] for number in range(1, 6)),
            *towns.group_by("year", "stage", "party", "district", maintain_order=True),
        ]
        for (year, stage, party, number), rows in groups:
            plan = 2022 if year >= 2022 else 2012
            area = districts[(plan, number)]
            if stage == "general":
                day = GENERAL_DAYS[year]
                external = f"{day}:NH:executive-council:{number}"
                event = election(f"{day}:NH")
                text = f"{year} New Hampshire Executive Council election, District {number}"
                path = f"us/nh/{day}-general/executive-council-{number}"
            else:
                day = PRIMARY_DAYS[year]
                external = f"{day}:NH:executive-council:{number}:{party}"
                event = election(f"{day}:NH:primary")
                text = f"{year} New Hampshire Executive Council {str(party).title()} primary, District {number}"
                path = f"us/nh/{day}-primary/executive-council-{number}-{party}-primary"
            contest = cs.key("us.contest", external)
            cs.add(
                "contest",
                id=contest,
                election_id=event,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=1,
                term="full",
                round_number=1,
                electoral_system_id=system,
                party_scope_id=cs.key("us.party", PARTIES[SIDES[str(party)]]) if stage == "primary" else None,
                default_result_kind="counted",
            )
            name(contest, text)
            cs.add("slug", path=path, entity_id=contest)
            counts["contests"] += 1
            if rows is None:
                cs.add("citation", entity_id=contest, source_id=candidates, locator=None)
                for position, row in enumerate(ballot.filter(pl.col("district") == number).iter_rows(named=True), 1):
                    option = cs.key("us.option", f"{external}:{slug(row['candidate'])}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", PARTIES[row["party"]]),
                        ballot_position=position,
                        ballot_label=row["candidate"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{external}:{slug(row['candidate'])}:head"),
                        person_id=person(row["candidate"]),
                        option_id=option,
                        role="head",
                        ballot_name=row["candidate"],
                        is_independent=False,
                    )
                continue
            mapping: dict[str, UUID] = {}
            pool: UUID | None = None
            tally = []
            position = 0
            for label in rows["label"].unique(maintain_order=True):
                if IGNORED.fullmatch(label):
                    continue
                ballot_name, side = candidate(label)
                if POOLED.fullmatch(ballot_name) or (stage == "primary" and side != SIDES[str(party)]):
                    pool = pool or cs.key("us.option", f"{external}:write-ins")
                    mapping[label] = pool
                    continue
                if side not in PARTIES and side is not None:
                    raise ValueError(f"{external}: {label} has party {side!r}")
                position += 1
                option = cs.key("us.option", f"{external}:{slug(ballot_name)}")
                if option in mapping.values():
                    raise ValueError(f"{external}: two columns for {ballot_name}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=cs.key("us.party", PARTIES[side]) if stage == "general" and side else None,
                    ballot_position=position,
                    ballot_label=ballot_name,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{external}:{slug(ballot_name)}:head"),
                    person_id=person(ballot_name),
                    option_id=option,
                    role="head",
                    ballot_name=ballot_name,
                    is_independent=stage == "general" and side is None,
                )
                mapping[label] = option
                tally.append((int(rows.filter(pl.col("label") == label)["votes"].sum()), option))
            if pool is not None:
                cs.add("contest_option", id=pool, contest_id=contest, kind="write_in", ballot_label="Write-ins")
            result_set = cs.key(f"{SOURCE}.result_set", external)
            cs.add(
                "result_set",
                id=result_set,
                election_id=event,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[(year, stage)],
                is_default=True,
                completeness=1,
            )
            cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
            if stage == "primary":
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md="Write-ins pools the Secretary of State's scattered write-ins with the write-in votes "
                    "for candidates of other parties, which the workbook lists by name.",
                )
            ranked = sorted(tally, key=lambda entry: -entry[0])
            if ranked and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
                cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="won", seats=None)
            lines = (
                rows.filter(pl.col("label").is_in(list(mapping)), pl.col("votes") > 0)
                .with_columns(
                    unit_id=pl.col("town").map_elements(
                        lambda place: str(cs.unit("us.precinct", units[year][unit_key(place)])),
                        return_dtype=pl.String,
                    ),
                    option_id=pl.col("label").replace_strict({label: str(option) for label, option in mapping.items()}),
                )
                .group_by("unit_id", "option_id")
                .agg(value=pl.col("votes").sum())
            )
            values.append(
                lines.with_columns(
                    result_set_id=pl.lit(str(result_set)),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                )
            )
            counts["result sets"] += 1
            counts["values"] += lines.height
        cs.frame(
            "result_value",
            pl.concat(values).select(
                "result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags", "value"
            ),
        )
        print(counts)
        return cs.write()
