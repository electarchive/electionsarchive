import io
import json
import os
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import httpx
import polars as pl
import shapefile

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.la_local"
VERSION = "1"
PUBLISHER = "Louisiana Secretary of State"
TITLE = "Official election results"
DATA_URL = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob="
DATES = DATA_URL + "ElectionDates.htm"
FIRST_YEAR = 2000
PARSED = DATA / "parsed" / SOURCE
PLACES = "https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_22_place_500k.zip"
KINDS = {"25": "City", "43": "Town", "47": "Village"}
PARTIES = {
    "DEM": "democratic-party",
    "REP": "republican-party",
    "LBT": "libertarian-party",
    "GRN": "green-party",
    "CON": "constitution-party",
    "REF": "reform-party",
}
INDEPENDENT = {"IND", "NOPTY", "NPTY", "NOP"}
PLACE = re.compile(r"(?:^|[ ,])(City|Town|Village) of (.+?)\s*$")
PREFIX = re.compile(r"(City|Town|Village) of (.+)")
SUFFIX = re.compile(r"\s*\(([A-Z]{2,5})\)\s*$")
BODIES = {"council", "board-of-aldermen", "board-of-trustees", "police-jury", "school-board", "board"}
HEADS = [
    (r"(?:city-parish )?mayor[- ]president|city-parish president", "mayor-president", "Mayor-President"),
    (r"mayor", "mayor", "Mayor"),
    (r"chief of police", "chief-of-police", "Chief of Police"),
    (r"(?:city )?marshal", "marshal", "Marshal"),
    (r"city judge", "city-judge", "City Judge"),
    (r"(?:city )?constables?", "constable", "Constable"),
    (r"justices? of the peace", "justice-of-the-peace", "Justice of the Peace"),
    (r"sheriff", "sheriff", "Sheriff"),
    (r"clerk of court", "clerk-of-court", "Clerk of Court"),
    (r"assessor", "assessor", "Assessor"),
    (r"coroner", "coroner", "Coroner"),
    (r"parish president", "parish-president", "Parish President"),
    (r"alderm[ae]n|alderwoman", "board-of-aldermen", "Board of Aldermen"),
    (r"police jurors?", "police-jury", "Police Jury"),
    (r"(?:members? of )?school board(?: members?)?", "school-board", "School Board"),
    (r"(?:mem[a-z]*\.?,? )?(?:board|bd\.?) of trustees", "board-of-trustees", "Board of Trustees"),
    (
        r"(?:(?:city |parish )?council ?(?:members?|m[ae]n|woman|persons?|ors?)?|(?:member of )?parish council"
        r"|parish commission member|parish council member|selectman|commissioner)",
        "council",
        "Council",
    ),
]
METHOD = (
    "Totals are from the Louisiana Secretary of State's election results portal. Where the portal lists "
    "precinct results that add up to the parish total they are shown by precinct, and otherwise by parish."
)
DISTRICT = (
    "Only part of the {whole} votes in this contest. The archive has no boundary for the district yet, so the "
    "results are shown for the district as a whole."
)


def many(value: Any) -> list[Any]:
    return [value] if isinstance(value, dict) else value or []


def days() -> list[tuple[date, bool]]:
    listed = json.loads(raw.cached(SOURCE, DATES).path.read_text())["Dates"]["Date"]
    return sorted(
        (datetime.strptime(entry["ElectionDate"], "%m/%d/%Y").date(), entry["ResultsOfficial"] == "1")
        for entry in listed
    )


def bundle(day: date) -> str:
    return f"{DATA_URL}{day:%Y%m%d}/"


def fetch() -> None:
    raw.fetch(SOURCE, DATES, suffix=".json")
    raw.fetch(SOURCE, PLACES, suffix=".zip")
    client = httpx.Client(headers={"User-Agent": raw.USER_AGENT}, follow_redirects=True, timeout=120)

    def get(link: str) -> bytes | None:
        for attempt in range(8):
            response = client.get(link)
            if response.status_code == 404:
                return None
            if response.status_code not in (403, 429, 500, 502, 503):
                response.raise_for_status()
                return response.content
            time.sleep(2**attempt)
        response.raise_for_status()
        return None

    for day, official in (lambda ordered, at: ordered[at:] + ordered[:at])(
        days()[:: -1 if os.environ.get("LA_REVERSE") else 1], int(os.environ.get("LA_OFFSET", "0"))
    ):
        if day.year < FIRST_YEAR or not official or day >= date.today():
            continue
        try:
            raw.cached(SOURCE, bundle(day))
            continue
        except LookupError:
            pass
        base = bundle(day)
        files: dict[str, bytes] = {}
        listed = get(base + "ParishesInElection.htm")
        if listed is None:
            continue
        files["ParishesInElection.htm"] = listed
        parishes = [entry["ParishValue"] for entry in many(json.loads(listed)["ParishesInElection"]["Parish"])]
        names = [f"RacesCandidates/ByParish_{parish}.htm" for parish in parishes] + [
            f"VotesParish/Votes_{parish}.htm" for parish in parishes
        ]
        with ThreadPoolExecutor(12) as pool:
            for name, content in zip(names, pool.map(lambda name: get(base + name), names), strict=True):
                if content is not None:
                    files[name] = content
            precincts = [
                f"VotesRaceByPrecinct/Votes_{race['ID']}_{parish}.htm"
                for parish in parishes
                if f"RacesCandidates/ByParish_{parish}.htm" in files
                for race in many(
                    (json.loads(files[f"RacesCandidates/ByParish_{parish}.htm"]).get("Races") or {}).get("Race")
                )
                if race["IsMultiParish"] != "1"
            ]
            for name, content in zip(precincts, pool.map(lambda name: get(base + name), precincts), strict=True):
                if content is not None:
                    files[name] = content
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            for name, content in sorted(files.items()):
                zipped.writestr(name, content)
        raw.store(
            SOURCE,
            base,
            archive.getvalue(),
            suffix=".zip",
            retrieved_at=datetime.now(UTC),
            media_type="application/zip",
        )
        print(f"{day}: {len(files)} files", flush=True)

    for day, official in days():
        if day.year < FIRST_YEAR or not official or day >= date.today():
            continue
        for extra in ("RacesCandidates_Multiparish.htm", "Votes_Multiparish.htm"):
            try:
                raw.cached(SOURCE, bundle(day) + extra)
                continue
            except LookupError:
                pass
            content = get(bundle(day) + extra)
            if content is not None:
                raw.store(
                    SOURCE,
                    bundle(day) + extra,
                    content,
                    suffix=".json",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/json",
                )


def parse() -> None:
    with connect() as conn:
        records = []
        with zipfile.ZipFile(raw.cached(SOURCE, PLACES).path) as archive:
            parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"])
        )
        for record, shape in zip(reader.records(), reader.shapes(), strict=True):
            if record is None or shape is None or record["LSAD"] not in KINDS:
                continue
            records.append((record.as_dict(), json.dumps(shape.__geo_interface__)))
        places = pl.DataFrame(
            conn.execute(
                """select code, name, kind, encode(st_asewkb(geom), 'hex'), encode(st_asewkb(st_pointonsurface(geom)), 'hex')
                   from (select code, name, kind, st_multi(st_collectionextract(st_makevalid(st_transform(
                           st_setsrid(st_geomfromgeojson(geojson), 4269), 4326)), 3)) as geom
                         from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as f(code, name, kind, geojson)) shapes""",
                [
                    [str(record["GEOID"]) for record, _ in records],
                    [str(record["NAME"]) for record, _ in records],
                    [KINDS[str(record["LSAD"])] for record, _ in records],
                    [geojson for _, geojson in records],
                ],
            ).fetchall(),
            schema=["code", "name", "kind", "geom", "point"],
            orient="row",
        )
    towns = {
        re.sub(r"[^A-Z]", "", name.upper().replace("SAINT", "ST")): code
        for code, name in places.select("code", "name").iter_rows()
    }
    contests: dict[str, dict[str, Any]] = {}
    options: dict[tuple[str, str], dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    skipped: dict[str, set[str]] = {}
    for day, official in days():
        if day.year < FIRST_YEAR or not official or day >= date.today():
            continue
        try:
            artifact = raw.cached(SOURCE, bundle(day))
        except LookupError:
            continue
        with zipfile.ZipFile(artifact.path) as archive:
            names = set(archive.namelist())
            batches = []
            for parish_entry in many(
                json.loads(archive.read("ParishesInElection.htm"))["ParishesInElection"]["Parish"]
            ):
                number = parish_entry["ParishValue"]
                if f"RacesCandidates/ByParish_{number}.htm" not in names:
                    continue
                batches.append(
                    (
                        number,
                        f"22{int(number) * 2 - 1:03}",
                        many(
                            (json.loads(archive.read(f"RacesCandidates/ByParish_{number}.htm")).get("Races") or {}).get(
                                "Race"
                            )
                        ),
                        many(
                            (json.loads(archive.read(f"VotesParish/Votes_{number}.htm")).get("Races") or {}).get("Race")
                        ),
                    )
                )
            try:
                batches.append(
                    (
                        "MP",
                        "22",
                        many(
                            (
                                json.loads(
                                    raw.cached(SOURCE, bundle(day) + "RacesCandidates_Multiparish.htm").path.read_text()
                                ).get("Races")
                                or {}
                            ).get("Race")
                        ),
                        many(
                            (
                                json.loads(
                                    raw.cached(SOURCE, bundle(day) + "Votes_Multiparish.htm").path.read_text()
                                ).get("Races")
                                or {}
                            ).get("Race")
                        ),
                    )
                )
            except LookupError:
                pass
            for number, fips, listed_races, counted_races in batches:
                counted = {
                    race["ID"]: {choice["ID"]: choice for choice in many(race["Choice"])} for race in counted_races
                }
                for race in listed_races:
                    level = int(race["OfficeLevel"])
                    if level < 225 or (race["IsMultiParish"] == "1") != (number == "MP") or race["ID"] not in counted:
                        continue
                    title = re.sub(r"\s+", " ", race["SpecificTitle"]).strip()
                    if re.search(r"(?i)cancel", title):
                        continue
                    head, _, tail = title.partition(" -- ")
                    if number == "36" and 300 <= level < 998 and tail and not PLACE.search(tail):
                        tail = f"{tail}, City of New Orleans"
                    head = re.sub(r"(?i),?\s*(?:reg\.?\s*&\s*)?unexp[a-z.]*\s*(?:term)?\.?", "", head).strip(" ,")
                    remainder = re.search(r"(?i)unexp", title) is not None
                    code, kind, label, office, seat, body = fips, "parish", None, None, None, False
                    question = level >= 998 or race["GeneralTitle"] == "Proposition"
                    if question:
                        prefix = PREFIX.match(head)
                        words = prefix[2].split(" ") if prefix else []
                        found = next(
                            (
                                (towns[key], " ".join(words[size:]))
                                for size in range(len(words), 0, -1)
                                if (key := re.sub(r"[^A-Z]", "", " ".join(words[:size]).upper().replace("SAINT", "ST")))
                                in towns
                            ),
                            None,
                        )
                        if found is not None:
                            code, kind = found[0], "place"
                            label = " -- ".join(part for part in (found[1], tail) if part)
                        elif prefix:
                            skipped.setdefault("place not found", set()).add(title)
                            continue
                        elif re.match(r"(?i)(parish(wide)?|p[./-]?w\b|prop(osition)?\b|proposition)", head):
                            label = title
                        else:
                            name = head
                            for pattern, full in (
                                (r"\bDist\.?(?=\s|$|,)", "District"),
                                (r"\bPro\.", "Protection"),
                                (r"\bRec\.", "Recreation"),
                                (r"\bCons\.", "Consolidated"),
                                (r"\bPrev\b\.?", "Prevention"),
                                (r"\bImprov?\b\.?", "Improvement"),
                                (r"\bImp\b\.?", "Improvement"),
                                (r"\bNeigh?\b\.?|\bNbrhd\b\.?|\bNeighbor\b", "Neighborhood"),
                                (r"\bSec\b\.?", "Security"),
                                (r"\bSubdiv\b\.?|\bSubd\b\.?|\bSub\b\.?", "Subdivision"),
                                (r"\bF\.?P\.?D\.?(?=\s|$)", "Fire Protection District"),
                                (r"\bFire Prot\b\.?|\bFire Protection Dist\b\.?", "Fire Protection District"),
                                (r"\bSch\b\.?", "School"),
                                (r"\bSys\b\.?|\bSystm\b", "System"),
                                (r"\bComm\b\.?(?= School| Sch)", "Community"),
                                (r"\bCrime Prev\w*\b", "Crime Prevention"),
                                (r"\bEnf\b\.?", "Enforcement"),
                                (r"\bEd\b\.?(?= Fac)", "Educational"),
                                (r"\bFac\b\.?", "Facilities"),
                                (r"\bNo\b(?!\.)", "No."),
                                (r"\bNumber\b", "No."),
                                (r"(?i),?\s*-?\s*prop(?:osition)?\.?\s*(?:no\.?\s*)?\d+(?:\s*of\s*\d+)?\s*$", ""),
                            ):
                                name = re.sub(pattern, full, name, flags=re.IGNORECASE)
                            name = re.sub(r"\s*#\s*", " No. ", name).strip()
                            code, kind, label = f"{fips}:{slug(name)}", "special", title
                            office = name
                        office = office or "question"
                    else:
                        entity = PLACE.search(tail) if tail else None
                        roman = re.search(r"\s+(I{1,3}|IV|V)$", re.sub(r"(?i)\s*\bat[- ]large\b", "", head))
                        base = re.sub(r"\(s\)", "", head)
                        base = re.sub(r"(?i)\s*\bat[- ]large\b", "", base)
                        base = re.sub(r"\s+(?:I{1,3}|IV|V)$", "", base).strip(" ,")
                        if re.match(r"(?i)recall\b", base):
                            skipped.setdefault("recall", set()).add(title)
                            continue
                        hint = ", ".join(
                            part
                            for part in (
                                "At-Large" if re.search(r"(?i)\bat[- ]large\b", head) else "",
                                f"Seat {roman[1]}" if roman else "",
                            )
                            if part
                        )
                        if level == 260:
                            name, _, rest = tail.partition(", ")
                            code, kind, office, body, seat = f"{fips}:{slug(name)}", "special", name, True, rest
                            label = None
                        else:
                            if entity is not None:
                                prefix_words = entity[2].split(" ")
                                found = next(
                                    (
                                        (towns[key], " ".join(prefix_words[size:]))
                                        for size in range(len(prefix_words), 0, -1)
                                        if (
                                            key := re.sub(
                                                r"[^A-Z]",
                                                "",
                                                " ".join(prefix_words[:size]).upper().replace("SAINT", "ST"),
                                            )
                                        )
                                        in towns
                                    ),
                                    None,
                                )
                                if found is None:
                                    skipped.setdefault("place not found", set()).add(title)
                                    continue
                                code, kind = found[0], "place"
                                seat = f"{tail[: entity.start()].strip(' ,')} {found[1]}".strip()
                            elif level in (300, 305, 308, 310):
                                skipped.setdefault("no place", set()).add(title)
                                continue
                            else:
                                seat = tail
                            matched = next(
                                (
                                    (key, text)
                                    for pattern, key, text in HEADS
                                    if re.fullmatch(pattern, base, re.IGNORECASE)
                                ),
                                None,
                            )
                            if matched is None and re.fullmatch(r"(?i)members?", base):
                                inner, _, rest = seat.partition(", ")
                                matched = next(
                                    (
                                        (key, text)
                                        for pattern, key, text in HEADS
                                        if re.fullmatch(pattern, re.sub(r"(?i)^city-parish ", "", inner), re.IGNORECASE)
                                    ),
                                    None,
                                )
                                if matched is not None:
                                    seat = rest
                            if matched is None:
                                matched = (slug(base), base)
                            office, label = matched[0], matched[1]
                            body = office in BODIES
                            seat = re.sub(r"(?i)^(?:justices? of the peace|metro)\s+(?=district|ward)", "", seat)
                            seat = re.sub(r"(?i)^(?:in )?at[- ]large$", "At-Large", seat.strip(" ,"))
                            seat = ", ".join(part for part in (seat, hint) if part)
                    if number == "MP" and kind != "place":
                        skipped.setdefault("multiparish, not a place", set()).add(title)
                        continue
                    scope = None
                    parties = set()
                    choices = many(race["Choice"])
                    for choice in choices:
                        found = SUFFIX.search(choice["Desc"].strip())
                        if found:
                            parties.add(found[1])
                    if race["IsClosedParty"] == "1" and len(parties) == 1:
                        scope = next(iter(parties))
                    ckey = f"{day}|{code}|{office}|{seat or ''}|{int(remainder)}|{scope or ''}|{label or ''}"
                    if ckey in contests and contests[ckey]["race"] != race["ID"] and contests[ckey]["fips"] == fips:
                        raise ValueError(f"{day} {number}: {title} repeats {ckey}")
                    contests.setdefault(
                        ckey,
                        {
                            "ckey": ckey,
                            "day": day,
                            "code": code,
                            "kind": kind,
                            "fips": fips,
                            "office": office,
                            "label": label,
                            "seat": seat or None,
                            "remainder": remainder,
                            "scope": scope,
                            "body": body,
                            "question": question,
                            "title": title,
                            "magnitude": int(race["NumberToBeElected"] or 1),
                            "text": race.get("FullText"),
                            "race": race["ID"],
                            "number": number,
                            "closed": race["IsClosedParty"] == "1",
                        },
                    )
                    totals = {choice_id: int(choice["VoteTotal"]) for choice_id, choice in counted[race["ID"]].items()}
                    by_precinct: dict[str, dict[str, int]] = {}
                    name = f"VotesRaceByPrecinct/Votes_{race['ID']}_{number}.htm"
                    if name in names:
                        for precinct in many((json.loads(archive.read(name)).get("Precincts") or {}).get("Precinct")):
                            by_precinct[precinct["Precinct"].strip()] = {
                                choice["ID"]: int(choice["VoteTotal"]) for choice in many(precinct["Choice"])
                            }
                    summed = {
                        choice_id: sum(row.get(choice_id, 0) for row in by_precinct.values()) for choice_id in totals
                    }
                    for position, choice in enumerate(choices, start=1):
                        raw_name = re.sub(r"\s+", " ", choice["Desc"]).strip()
                        found = SUFFIX.search(raw_name)
                        candidate = SUFFIX.sub("", raw_name) if found else raw_name
                        outcome = counted[race["ID"]].get(choice["ID"], {}).get("Outcome")
                        previous = options.get((ckey, candidate))
                        if previous is None or (outcome is not None and previous["outcome"] is None):
                            options[(ckey, candidate)] = {
                                "ckey": ckey,
                                "name": candidate,
                                "party": found[1] if found else None,
                                "outcome": outcome,
                                "position": position,
                            }
                        if choice["ID"] not in totals:
                            continue
                        if summed == totals and by_precinct:
                            for precinct, row in by_precinct.items():
                                if choice["ID"] in row:
                                    results.append(
                                        {
                                            "ckey": ckey,
                                            "fips": fips,
                                            "precinct": precinct,
                                            "name": candidate,
                                            "votes": row[choice["ID"]],
                                        }
                                    )
                        else:
                            results.append(
                                {
                                    "ckey": ckey,
                                    "fips": fips,
                                    "precinct": None,
                                    "name": candidate,
                                    "votes": totals[choice["ID"]],
                                }
                            )
    for reason, titles in skipped.items():
        print(reason, len(titles), sorted(titles)[:40])
    frame = pl.DataFrame(list(contests.values()), infer_schema_length=None).sort("day", "ckey")
    runs = set(
        pl.DataFrame(list(options.values()), infer_schema_length=None)
        .filter(pl.col("outcome") == "Runoff")["ckey"]
        .unique()
    )
    latest: dict[tuple[object, ...], tuple[date, str]] = {}
    rounds, previous_keys = [], []
    for row in frame.iter_rows(named=True):
        group = (
            row["code"],
            row["office"],
            row["seat"],
            row["remainder"],
            row["scope"],
            row["label"] if row["question"] else None,
        )
        before = latest.get(group)
        follows = before is not None and 0 < (row["day"] - before[0]).days <= 100 and before[1] in runs
        rounds.append(2 if follows else 1)
        previous_keys.append(before[1] if follows and before is not None else None)
        latest[group] = (row["day"], row["ckey"])
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.with_columns(
        round=pl.Series(rounds, dtype=pl.Int16),
        previous=pl.Series(previous_keys, dtype=pl.String),
        runoff=pl.col("ckey").is_in(list(runs)),
    ).write_parquet(PARSED / "contests.parquet")
    pl.DataFrame(list(options.values()), infer_schema_length=None).write_parquet(PARSED / "options.parquet")
    pl.DataFrame(results, infer_schema_length=None).write_parquet(PARSED / "results.parquet")
    places.write_parquet(PARSED / "places.parquet")


def emit() -> Path:
    decade = int(os.environ["LA_DECADE"])
    everything = pl.read_parquet(PARSED / "contests.parquet")
    contests = everything.filter((pl.col("day").dt.year() // 10) * 10 == decade)
    options = pl.read_parquet(PARSED / "options.parquet").join(contests.select("ckey"), on="ckey")
    results = pl.read_parquet(PARSED / "results.parquet").join(contests.select("ckey"), on="ckey")
    places = pl.read_parquet(PARSED / "places.parquet")
    keys: dict[str, str] = {}
    for row in everything.iter_rows(named=True):
        keys[row["ckey"]] = (
            f"{row['day']}:LA:{row['code']}:{slug(row['office'])}"
            + (f":{slug(row['seat'])}" if row["seat"] else "")
            + (":remainder" if row["remainder"] else "")
            + (f":{row['scope'].lower()}" if row["scope"] else "")
            + (f":q{row['race']}" if row["question"] else "")
        )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Louisiana's parish, municipal, and special district elections, {decade}s",
            polity="US-LA",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://voterportal.sos.la.gov/static/",
                license="Public record",
            )
        for day in contests["day"].unique().sort():
            cs.artifact(raw.cached(SOURCE, bundle(day)), source)
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        cs.artifact(raw.cached(SOURCE, PLACES), census)
        us = cs.polity("US")
        state = cs.polity("US-LA")
        polities: dict[str, tuple[UUID, str, str, str]] = {
            code: (polity, text, path, "parish")
            for code, polity, text, path in conn.execute(
                """select c.code, t.polity_id, n.text, s.path from area a
                   join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join polity p on p.id = t.polity_id and p.kind = 'county'
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join slug s on s.entity_id = p.id
                   where a.scheme_code = 'us.county' and c.code like '22%%'"""
            ).fetchall()
        }
        if len(polities) != 64:
            raise ValueError("Louisiana needs 64 parishes")
        names = dict(
            conn.execute(
                """select u.code, upper(n.text) from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '22%%'"""
            ).fetchall()
        )
        held = {
            code: (polity, text, path, kind)
            for code, polity, text, path, kind in conn.execute(
                """select c.code, t.polity_id, n.text, s.path, 'place' from area a
                   join area_code c on c.area_id = a.id
                   join polity_territory t on t.area_id = a.id
                   join polity p on p.id = t.polity_id and p.kind = 'municipality'
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   join slug s on s.entity_id = p.id
                   where a.scheme_code = 'us.place' and c.code like '22%%'"""
            ).fetchall()
        }
        foreign = {
            table: dict(
                conn.execute(
                    f"""select s.path, s.entity_id from slug s join {table} t on t.id = s.entity_id
                        join changeset c on c.id = t.changeset_id
                        where s.path like 'us/la/%%' and c.pipeline <> %s""",
                    [SOURCE],
                ).fetchall()
            )
            for table in ("election", "contest")
        }
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/la/%'").fetchall()}
        used = set(contests.filter(pl.col("kind") == "place")["code"])
        kinds = {}
        for code, text, kind, geom, point in places.iter_rows():
            kinds[code] = kind
            if code not in used:
                continue
            if code in held:
                polities[code] = held[code]
                continue
            polity = cs.key("us.polity", f"place:{code}")
            path = f"us/la/{slug(text)}"
            polities[code] = (polity, text, path, "place")
            if polity not in cs.minted:
                continue
            if path in taken:
                raise ValueError(f"{text} would take {path}")
            cs.add("polity", id=polity, kind="municipality", iso_code=None)
            name(polity, text)
            cs.add("polity_parent", polity_id=polity, parent_id=state)
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area("us.place", code)
            cs.add("area", id=area, scheme_code="us.place")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", "22"))
            name(area, f"{text}, LA")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=census,
            )
            cs.add(
                "reporting_unit",
                id=cs.unit("us.place", code),
                polity_id=polity,
                scheme_code="us.place",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        systems = {
            "fptp": cs.key("us.electoral_system", "fptp"),
            "block": cs.key("us.electoral_system", "block"),
            "referendum": cs.key("us.electoral_system", "referendum"),
            "two_round": cs.key("us.electoral_system", "two_round"),
        }
        parties = {code: cs.key("us.party", key) for code, key in PARTIES.items()}
        if cs.minted & {*systems.values(), *parties.values()}:
            raise ValueError("the electoral systems and parties must already exist")
        cs.add("scheme", code="us.local_district", polity_id=us, kind="electoral_district", rank=3, noun="district")
        cs.add(
            "scheme", code="us.special_district", polity_id=us, kind="administrative", rank=3, noun="special district"
        )
        special_names = dict(
            contests.filter(pl.col("kind") == "special").select("code", "office").unique("code").iter_rows()
        )
        for code, text in special_names.items():
            polity = cs.key("us.polity", f"special:{code}")
            path = f"{polities[code[:5]][2]}/{slug(text)}"
            polities[code] = (polity, text, path, "special")
            if polity not in cs.minted:
                continue
            if path in taken:
                raise ValueError(f"{text} would take {path}")
            cs.add("polity", id=polity, kind="special_district", iso_code=None)
            name(polity, text)
            cs.add("polity_parent", polity_id=polity, parent_id=polities[code[:5]][0])
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area("us.special_district", code)
            cs.add("area", id=area, scheme_code="us.special_district")
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.county", code[:5]))
            name(area, f"{text}, LA")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "reporting_unit",
                id=cs.unit("us.special_district", code),
                polity_id=polity,
                scheme_code="us.special_district",
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )

        precincts: dict[str, UUID] = dict(
            conn.execute(
                "select code, id from reporting_unit where scheme_code = 'us.precinct' and code like '____-__-__:LA:%%'"
            ).fetchall()
        )
        parishes = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '22%%'"
            ).fetchall()
        }
        grouped = {key: rows for key, rows in results.group_by("ckey")}
        choices = {key: rows for key, rows in options.group_by("ckey")}
        elections: dict[tuple[date, str], UUID] = {}
        kind_of = {
            (day, code): (
                "runoff"
                if rows["round"].min() == 2
                else "primary"
                if rows["closed"].any()
                else "general"
                if day.month == 11 and day.year % 2 == 0
                else "local"
            )
            for (day, code), rows in contests.group_by("day", "code")
        }
        owners: dict[tuple[str, str], UUID] = {}
        persons: dict[UUID, str] = {}
        minted: dict[str, tuple[date, str]] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        districts: set[str] = set()
        paths: set[str] = set()
        for row in contests.sort("day", "code", "office", "seat", "ckey", nulls_last=True).iter_rows(named=True):
            day, code, office = row["day"], row["code"], row["office"]
            polity, place, path, owner_kind = polities[code]
            question = row["question"]
            election_kind = kind_of[(day, code)]
            label = {"general": "general", "local": "municipal", "runoff": "runoff", "primary": "primary"}[
                election_kind
            ]
            if question:
                title = place
            elif row["body"]:
                title = (
                    place
                    if owner_kind == "special"
                    else f"{place} {kinds[code]} Council"
                    if office == "council" and owner_kind == "place"
                    else f"{place} {row['label']}"
                )
            elif office == "mayor":
                title = f"Mayor of {place}"
            else:
                title = f"{place} {row['label']}"
            if not question and (code, office) not in owners:
                owner = owners[(code, office)] = cs.key(
                    "us.body" if row["body"] else "us.office", f"LA:{code}:{office}"
                )
                if owner in cs.minted:
                    if row["body"]:
                        cs.add("body", id=owner, polity_id=polity, kind="council")
                    else:
                        cs.add(
                            "office",
                            id=owner,
                            polity_id=polity,
                            kind="mayor" if office.startswith("mayor") else "other",
                        )
                    name(owner, title)
            if (day, code) not in elections and f"{path}/{day}-{election_kind}" in foreign["election"]:
                elections[(day, code)] = foreign["election"][f"{path}/{day}-{election_kind}"]
            if (day, code) not in elections:
                election = elections[(day, code)] = cs.key("us.election", f"{day}:LA:{code}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind=election_kind,
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} {place} {label} election")
                cs.add("slug", path=f"{path}/{day}-{election_kind}", entity_id=election)
            election = elections[(day, code)]
            scheme = (
                "us.special_district"
                if owner_kind == "special"
                else "us.place"
                if owner_kind == "place"
                else "us.county"
            )
            whole = area = cs.area(scheme, code)
            unit = cs.unit(scheme, code)
            seat = row["seat"]
            district = not question and seat is not None and seat != "At-Large"
            if district:
                key = f"22:{code}:{office}:{slug(seat)}"
                area = cs.area("us.local_district", key)
                unit = cs.unit("us.local_district", key)
                if key not in districts:
                    districts.add(key)
                    if area in cs.minted:
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=key, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                        name(area, f"{title}, {seat}")
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polity,
                            scheme_code="us.local_district",
                            code=key,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                        )
            contest_key = keys[row["ckey"]]
            contest_path = (
                f"{path}/{day}-{election_kind}/"
                + (slug(row["label"])[:70] if question else office + (f"-{slug(seat)}" if seat else ""))
                + ("-unexpired" if row["remainder"] else "")
                + (f"-{row['scope'].lower()}" if row["scope"] else "")
            )
            while contest_path in paths:
                contest_path += "-2"
            paths.add(contest_path)
            if contest_path in foreign["contest"]:
                continue
            contest = cs.key("us.contest", contest_key)
            magnitude = row["magnitude"]
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question" if question else "body_seats" if row["body"] else "office",
                office_id=None if question or row["body"] else owners[(code, office)],
                body_id=owners[(code, office)] if row["body"] else None,
                district_area_id=area,
                magnitude=magnitude,
                round_number=row["round"],
                previous_round_id=cs.key("us.contest", keys[row["previous"]]) if row["previous"] else None,
                party_scope_id=parties.get(row["scope"]),
                electoral_system_id=systems["referendum"]
                if question
                else systems["two_round"]
                if (row["runoff"] or row["round"] == 2) and magnitude == 1
                else systems["block" if magnitude > 1 else "fptp"],
                default_result_kind="counted",
                term=None if question else "remainder" if row["remainder"] else "full",
            )
            if question:
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
                if row["text"]:
                    cs.add(
                        "ballot_question_text",
                        id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1ftext\x1fen\x1f{source}"),
                        contest_id=contest,
                        kind="text",
                        lang="en",
                        body=row["text"].strip(),
                        url=f"{bundle(day)}RacesCandidates/ByParish_{row['number']}.htm",
                        source_id=source,
                    )
            name(
                contest,
                f"{day.year} {place}: {row['label']}"
                if question
                else (f"{day.year} {place} mayoral election" if office == "mayor" else f"{day.year} {title} election")
                + (f", {seat}" if seat else "")
                + (" (unexpired term)" if row["remainder"] else "")
                + (f" ({row['scope']} primary)" if row["scope"] else ""),
            )
            cs.add("slug", path=contest_path, entity_id=contest)
            if district:
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="caveat",
                    body_md=DISTRICT.format(whole="parish" if owner_kind != "place" else "municipality"),
                )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=METHOD)
            lines = choices[(row["ckey"],)].sort("position")
            canon: dict[str, str] = {}
            first: dict[UUID, str] = {}
            ids: dict[str, UUID] = {}
            made: dict[str, UUID] = {}
            for line in lines.iter_rows(named=True):
                candidate = line["name"]
                writein = re.search(r"(?i)write-?\s*in", candidate) is not None
                if question:
                    answer = candidate.title()
                    option = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=1 if answer == "Yes" else 2,
                        ballot_label=answer,
                    )
                    canon[candidate] = answer
                elif writein:
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    if "Write-ins" not in made:
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins"
                        )
                    canon[candidate] = "Write-ins"
                else:
                    person = cs.key(f"{SOURCE}.person", f"{code}:{slug(candidate)}")
                    canon[candidate] = first.setdefault(person, candidate)
                    if canon[candidate] != candidate:
                        continue
                    if person not in persons:
                        persons[person] = candidate
                        cs.add("person", id=person)
                        name(person, candidate)
                    option = cs.key("us.option", f"{contest_key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(line["party"]),
                        ballot_label=candidate,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=candidate,
                        is_independent=line["party"] in INDEPENDENT,
                    )
                made[canon[candidate]] = option
                if line["outcome"] in ("Elected", "Approved") and not (question and canon[candidate] != "Yes"):
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="won" if question or row["closed"] else "elected",
                        seats=None,
                    )
                elif line["outcome"] == "Runoff":
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="advanced", seats=None)
            cells: dict[tuple[UUID, UUID], int] = {}
            for fips, precinct, candidate, votes in (
                grouped[(row["ckey"],)].select("fips", "precinct", "name", "votes").iter_rows()
            ):
                target = unit
                if precinct is not None:
                    place_code = f"{day}:LA:{fips}:{names[fips]}:{precinct}"
                    target = precincts.get(place_code) or cs.unit("us.precinct", place_code)
                    if place_code not in precincts:
                        minted.setdefault(place_code, (day, fips))
                    if owner_kind != "parish" or district:
                        memberships.add((target, area, day))
                cell = (target, made[canon[candidate]])
                cells[cell] = cells.get(cell, 0) + votes
            for (target, option), votes in cells.items():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=target,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        for place_code, (day, fips) in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", place_code),
                polity_id=state,
                scheme_code="us.precinct",
                code=place_code,
                parent_id=parishes[fips][0],
                area_relation="within",
                area_id=parishes[fips][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        for target, area, day in memberships:
            cs.add(
                "reporting_unit_area",
                unit_id=target,
                area_id=area,
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        return cs.write()
