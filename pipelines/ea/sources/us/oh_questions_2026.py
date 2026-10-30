import io
import json
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import ceda
from ea.store import DATA, connect

SOURCE = "us.oh_questions_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
FILE = DATA / "inbox" / "manual" / "MF-367" / "OH" / "2026-11-general-qandi.xlsx"
URL = "https://www.ohiosos.gov/elections/elections-calendar-and-results/"
PUBLISHER = "Ohio Secretary of State"
TITLE = "November 3, 2026 General Election: questions and issues"
PLACES = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_place_national.zip"
COUSUBS = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_cousubs_national.zip"
DIRECTORY = "https://educationdata.urban.org/api/v1/school-districts/ccd/directory/2022/?fips=39"
SCHEMES = {"Township": "us.town", "Village": "us.place", "City": "us.place", "County": "us.county", "School District": "us.school_district"}


WORDING: dict[str, str] = json.loads(Path(__file__).with_name("oh_franklin_wording.json").read_text())


def fetch() -> None:
    try:
        raw.cached(SOURCE, URL)
    except LookupError:
        raw.store(
            SOURCE,
            URL,
            FILE.read_bytes(),
            suffix=".xlsx",
            retrieved_at=datetime.fromtimestamp(FILE.stat().st_mtime, UTC),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    for url, suffix in ((PLACES, ".zip"), (COUSUBS, ".zip"), (DIRECTORY, ".json")):
        raw.fetch(SOURCE, url, suffix=suffix)


def gazetteer(url: str) -> pl.DataFrame:
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        frame = pl.read_csv(archive.read(archive.namelist()[0]), separator="\t", infer_schema=False)
    return frame.rename(lambda column: column.strip()).filter(pl.col("USPS") == "OH")


def clean(value: object) -> str | None:
    text = re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""
    return None if text in ("", "N/A", "None") else text


def describe(row: dict[str, object]) -> str:
    kind = clean(row["Question Type"]) or "Question"
    change = clean(row["Description"])
    head = f"{kind}, {change.lower()}" if change else kind
    details = []
    if (mills := clean(row["Millage"])) is not None:
        details.append(f"{mills} mills")
    if (percent := clean(row["Percent"])) is not None:
        details.append(f"{float(percent) * 100:g} percent")
    if (amount := clean(row["Dollar Amount"])) is not None:
        details.append(f"${amount}")
    if (years := clean(row["Length of Levy in Years or Continuing Period of Time (cpt)"])) is not None:
        details.append("continuing" if years.lower() == "cpt" else f"{years} years")
    if (start := clean(row["Commencing Year or Effective Date"])) is not None:
        details.append(f"beginning {start[:4]}")
    text = head + (": " + ", ".join(details) if details else "")
    purpose = clean(row["Purpose"])
    return f"{text}. Purpose: {purpose}." if purpose else f"{text}."


def parse() -> None:
    frame = pl.read_excel(raw.cached(SOURCE, URL).path, sheet_name="Issues Summary", infer_schema_length=0)
    rows = []
    for row in frame.iter_rows(named=True):
        kind = clean(row["Subdivision Type"])
        name = clean(row["Subdivision Name"])
        if kind not in SCHEMES or name is None or clean(row["County"]) is None:
            continue
        label = clean(row["Purpose"]) or clean(row["Question Type"]) or "Question"
        rows.append(
            {
                "county": clean(row["County"]),
                "kind": kind,
                "name": name,
                "text": WORDING.get(f"{name}|{label}") or describe(row),
                "label": label,
            }
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    out = pl.DataFrame(rows)
    out.write_parquet(PARSED / "questions.parquet")
    print(out.group_by("kind").len().sort("kind").rows())


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "questions.parquet")
    with connect() as conn:
        counties: dict[str, str] = {}
        towns: dict[tuple[str, str], str] = {}
        places: dict[str, list[str]] = {}
        schools: dict[str, list[str]] = {}
        found: dict[str, tuple[UUID, UUID, str, str, str]] = {}
        for scheme, code, polity, area, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, a.id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality', 'school_district')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.town', 'us.school_district')
                 and left(c.code, 2) = '39'"""
        ).fetchall():
            key = f"{scheme}:{code}"
            found[key] = (polity, area, text, path, code)
            if scheme == "us.county":
                counties[ceda._core(text)] = code
            elif scheme == "us.town":
                towns[(code[:5], ceda._core(text))] = key
            elif scheme == "us.place":
                places.setdefault(ceda._city(text), []).append(key)
            else:
                schools.setdefault(ceda._core(text), []).append(key)
        slugs = dict(conn.execute("select path, entity_id from slug where path like 'us/oh/%%'").fetchall())
        candidates: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        for geoid, full in gazetteer(PLACES).select("GEOID", "NAME").iter_rows():
            label = re.sub(r" (city|village|CDP)$", "", full)
            candidates.setdefault(("place", ceda._city(label)), []).append((f"place:{geoid}", geoid, label))
        for geoid, full in gazetteer(COUSUBS).select("GEOID", "NAME").iter_rows():
            if full.endswith("township"):
                candidates.setdefault(("town", geoid[:5] + ceda._core(full)), []).append((f"town:{geoid}", geoid, full.title()))
        for entry in json.loads(raw.cached(SOURCE, DIRECTORY).path.read_text())["results"]:
            candidates.setdefault(("school", ceda._core(entry["lea_name"])), []).append(
                (f"school:{entry['leaid']}", entry["leaid"], entry["lea_name"])
            )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Ohio township, village, city, county, and school district questions on the November 3, 2026 ballot, from the Secretary of State's issue summaries",
            polity="US-OH",
        )
        system = cs.key("us.electoral_system", "referendum")
        source = cs.source(PUBLISHER, TITLE)
        cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=TITLE, url=URL)
        cs.artifact(raw.cached(SOURCE, URL), source)
        parent = cs.key("us.election", f"{DAY}:OH")
        elections: set[UUID] = set()
        counts: dict[str, int] = {}
        kept = 0
        taken = {path for path in slugs}
        ohio = cs.polity("US-OH")
        state_area = cs.area("us.state", "39")
        for row in frame.sort("kind", "county", "name", "label").iter_rows(named=True):
            name = row["name"]
            county = counties.get(ceda._core(f"{row['county']} County"))
            spec: list[tuple[str, str, str]] = []
            scheme = ""
            if row["kind"] == "County":
                key = f"us.county:{county}" if county is not None else None
            elif row["kind"] == "Township":
                bare = name if "Township" in name else f"{name} Township"
                key = towns.get((county, ceda._core(bare))) if county else None
                spec = candidates.get(("town", (county or "") + ceda._core(bare)), [])
                scheme = "us.town"
            elif row["kind"] == "School District":
                options = schools.get(ceda._core(name), [])
                key = options[0] if len(options) == 1 else None
                spec = candidates.get(("school", ceda._core(name)), [])
                scheme = "us.school_district"
            else:
                options = places.get(ceda._city(name), [])
                key = options[0] if len(options) == 1 else None
                spec = candidates.get(("place", ceda._city(name)), [])
                scheme = "us.place"
            if key is None and len(spec) == 1:
                owner, geoid, text = spec[0]
                key = f"{scheme}:{geoid}"
                if key not in found:
                    polity_id = cs.key("us.polity", owner)
                    path = f"us/oh/{slug(text)}"
                    if path in taken:
                        path = (
                            f"{path}-{slug(row['county'])}-county" if scheme == "us.town" else f"{path}-city"
                        )
                    if path in taken:
                        print("slug taken:", path)
                        continue
                    taken.add(path)
                    area_id = cs.area(scheme, geoid)
                    if polity_id in cs.minted:
                        cs.add("polity", id=polity_id, kind="school_district" if scheme == "us.school_district" else "municipality", iso_code=None)
                        cs.add("name", entity_id=polity_id, text=text, lang="en", kind="official", is_preferred=True)
                        cs.add("polity_parent", polity_id=polity_id, parent_id=ohio)
                        cs.add("slug", path=path, entity_id=polity_id)
                    if area_id in cs.minted:
                        cs.add("area", id=area_id, scheme_code=scheme)
                        cs.add("area_code", area_id=area_id, code=geoid, valid_from=None)
                        cs.add(
                            "area_parent",
                            area_id=area_id,
                            parent_id=cs.area("us.county", geoid[:5]) if scheme == "us.town" else state_area,
                        )
                        cs.add("name", entity_id=area_id, text=f"{text}, OH", lang="en", kind="official", is_preferred=True)
                    if polity_id in cs.minted:
                        cs.add("polity_territory", polity_id=polity_id, area_id=area_id)
                        cs.add(
                            "reporting_unit",
                            id=cs.unit(scheme, geoid),
                            polity_id=polity_id,
                            scheme_code=scheme,
                            code=geoid,
                            area_relation="equals",
                            area_id=area_id,
                            kind="standard",
                            valid_from=None,
                        )
                    found[key] = (polity_id, area_id, text, path, geoid)
            if key is None or key not in found:
                print("no polity:", row["kind"], row["county"], name)
                continue
            kept += 1
            polity, area, place, path, code = found[key]
            election = cs.key("us.election", f"{DAY}:OH:{code}")
            if election in cs.minted and election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind="general",
                    date_from=DAY,
                    date_to=DAY,
                    date_precision="day",
                    status="scheduled",
                )
                cs.add("name", entity_id=election, text=f"{DAY.year} {place} general election", lang="en", kind="official", is_preferred=True)
                cs.add("slug", path=f"{path}/{DAY}-general", entity_id=election)
            label = row["label"]
            counts[key] = counts.get(key, 0) + 1
            stem = slug(label)
            contest_key = f"{DAY}:OH:{code}:question:{stem}-{counts[key]}"
            contest = cs.key("us.contest", contest_key)
            contest_path = f"{path}/{DAY}-general/{stem}-{counts[key]}"
            if slugs.get(contest_path, contest) != contest:
                print("slug taken:", contest_path)
                continue
            slugs[contest_path] = contest
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
            cs.add("name", entity_id=contest, text=f"{DAY.year} {place}: {label}", lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                cs.add("slug", path=contest_path, entity_id=contest)
            for position, answer in enumerate(("Yes", "No")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{answer.lower()}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add("note", entity_id=contest, kind="context", body_md=row["text"])
            cs.add("citation", entity_id=contest, source_id=source)
        print(f"{kept} of {frame.height} questions")
        return cs.write()
