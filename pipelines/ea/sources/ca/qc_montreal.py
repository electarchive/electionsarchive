"""Montréal's municipal general elections by polling station, 2009 to 2025.

The City of Montréal publishes, as open data, each general election's results for every polling station (bureau de
vote): one row for each candidate of each post at each station, with the candidate's votes and the valid and rejected
ballots the station counted for that post. The posts are the city mayor, each borough's mayor, the city councillors
elected by district, and the borough councillors. The Ministry's province-wide file (`ca.qc_municipal`) already holds
each of these posts as a contest with its candidates and its totals; this pipeline adds the polling-station count to
those contests as a result set of its own, matched by post and by candidate.

The voting sections of 2009, 2017, 2021, and 2025 and the electoral districts of every election come from the same
portal as open data (CC BY 4.0); the 2013 sections were not published, so those stations carry no outline.
"""

import functools
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ca import qc_municipal as qc

SOURCE = "ca.qc_montreal"
MUNICIPALITY = "66023"
PAGE = "https://donnees.montreal.ca/dataset/resultats-detailles"
SUMMARY_PAGE = "https://donnees.montreal.ca/dataset/resultats-sommaires"
POSTS_PAGE = "https://donnees.montreal.ca/dataset/postes-electifs"
SECTIONS_PAGE = "https://donnees.montreal.ca/dataset/sections-de-vote"
DISTRICTS_PAGE = "https://donnees.montreal.ca/dataset/districts-electoraux"
PORTAL = "https://donnees.montreal.ca"
RESULTS = {
    2025: "/fr/dataset/97e6096d-b22e-453c-98e9-1a55e79cc311/resource/880d809c-2dd6-4ece-93b6-8e4be6d8ff96/download/resultats-detailles-2025.csv",
    2021: "/fr/dataset/97e6096d-b22e-453c-98e9-1a55e79cc311/resource/e9ef328b-226f-4498-983e-7609767555b2/download/resultats-detailles-2021.csv",
    2017: "/fr/dataset/97e6096d-b22e-453c-98e9-1a55e79cc311/resource/5a691a7c-0a6e-4a48-a6b9-d4b72d01fbdd/download/resultats-detailles-2017.csv",
    2013: "/fr/dataset/97e6096d-b22e-453c-98e9-1a55e79cc311/resource/2974f587-326a-41e1-80e0-de1e145df8ed/download/resultats-election-2013-finaux-detailles-csv.csv",
    2009: "/fr/dataset/764944bf-1e3b-4c69-bb7b-b33311142982/resource/7d165e6e-2edd-41d9-bee1-2edd969b0959/download/electiongene-2009-distributiondesvotes.csv",
}
POSTS = {
    2025: "/fr/dataset/d3be675b-153a-4c1a-bef1-b89839933d1e/resource/0e9a2bca-8a5c-44f6-bde7-3bb6fa5029ce/download/postes-electifs-2025.csv",
    2021: "/fr/dataset/d3be675b-153a-4c1a-bef1-b89839933d1e/resource/8a997b8e-b27a-4e2e-b3da-75090b9383a6/download/postes-electifs-2021.csv",
    2017: "/fr/dataset/d3be675b-153a-4c1a-bef1-b89839933d1e/resource/6e1f1922-d2d3-49b9-9340-6604a3858ff3/download/postes-electifs-2017.csv",
    2013: "/fr/dataset/d3be675b-153a-4c1a-bef1-b89839933d1e/resource/b2b13b46-b416-4d4c-86a8-8a8e04751574/download/postes-electifs-2013.csv",
    2009: "/fr/dataset/d3be675b-153a-4c1a-bef1-b89839933d1e/resource/0a2df6d1-0283-4a2f-af53-ecdafe4393f7/download/postes-electifs-2009.csv",
}
SECTIONS = {
    2025: "/dataset/a6434443-10e4-4246-af15-6a2130eddbbc/resource/8b0097ff-5c7b-48b1-8ee1-320d385f820c/download/section-vote-2025.json",
    2021: "/dataset/a6434443-10e4-4246-af15-6a2130eddbbc/resource/923a7118-8193-439f-972c-3cf2ba545338/download/section-vote-2021.geojson",
    2017: "/dataset/a6434443-10e4-4246-af15-6a2130eddbbc/resource/e71d381b-bd53-4433-8837-12923c24f3c6/download/sections-vote-2017.json",
    2009: "/dataset/a6434443-10e4-4246-af15-6a2130eddbbc/resource/c10c78e7-33da-4085-ba9e-045df60b3dd4/download/sections-vote-2009.geojson",
}
DISTRICTS = {
    2025: "/dataset/70acec75-c2b4-4d26-a399-facc7b0ad9bf/resource/fa1f8cfc-cdbf-42fd-9979-32c16b68b5ca/download/districts-electoraux-2025.json",
    2021: "/dataset/70acec75-c2b4-4d26-a399-facc7b0ad9bf/resource/d0c1467b-a551-42df-98b4-057e00a84275/download/districts-electoraux-2021.geojson",
    2017: "/dataset/70acec75-c2b4-4d26-a399-facc7b0ad9bf/resource/5665b1b2-cea5-4970-9a45-603c395e2b79/download/districts-electoraux-2017.geojson",
    2013: "/dataset/70acec75-c2b4-4d26-a399-facc7b0ad9bf/resource/81ae4c0b-883c-4d17-a176-f7e0596da4f0/download/districts-electoraux-2013.geojson",
}
DAYS = qc.DAYS
YEARS = (2009, 2013, 2017, 2021, 2025)
SCHEME = "ca-qc.montreal_bureau"
DISTRICT_SCHEME = "ca-qc.montreal_district"
ROLES = {
    "maire de la ville de montreal": "mayor",
    "maire d'arrondissement": "borough_mayor",
    "conseiller de ville": "councillor",
    "conseiller d'arrondissement": "borough_councillor",
}
# The 2009 list of posts numbers the councillor of La Pointe-aux-Prairies 21.10; the results file and every other
# list call it 12.10.
TYPOS = {(2009, 21.1): 12.1}
SIMPLIFY = 0.00002
METHOD = (
    "Counted at each polling station (bureau de vote) and published by the City of Montréal. Each station's row for "
    "a post gives every candidate's votes and the valid and rejected ballots the station counted for that post. "
    "The official totals of the post are in the Ministry's count of the same contest."
)


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def url(table: dict[int, str], year: int) -> str:
    return PORTAL + table[year]


def fetch() -> None:
    for table in (RESULTS, POSTS, SECTIONS, DISTRICTS):
        for year in table:
            raw.fetch(SOURCE, url(table, year), suffix=Path(table[year]).suffix)
    qc_fetch()


def qc_fetch() -> None:
    for year in YEARS:
        raw.fetch(qc.SOURCE, qc.url(year), suffix=".csv")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", *(f"{SOURCE}/{year}" for year in YEARS)]


def parse() -> None:
    geo().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


class Post(NamedTuple):
    role: str
    poste: float


@functools.cache
def roles(year: int) -> dict[float, str]:
    frame = pl.read_csv(raw.cached(SOURCE, url(POSTS, year)).path, infer_schema_length=0, encoding="utf8-lossy")
    found: dict[float, str] = {}
    for number, kind in frame.select("no", "type").iter_rows():
        role = ROLES.get(fold(" ".join(str(kind).split())))
        if role is None:
            raise ValueError(f"{year}: unknown post type {kind}")
        value = round(float(str(number).replace(",", ".")), 2)
        found[TYPOS.get((year, value), value)] = role
    return found


@functools.cache
def table(year: int) -> pl.DataFrame:
    """One row per candidate at each polling station, under one set of column names."""
    frame = pl.read_csv(raw.cached(SOURCE, url(RESULTS, year)).path, infer_schema_length=0)
    frame = frame.rename({column: column.strip() for column in frame.columns})
    first = lambda *names: next(name for name in names if name in frame.columns)
    frame = frame.select(
        pl.col(first("District")).alias("district"),
        pl.col("Bureau").alias("bureau"),
        pl.col(first("Poste", "Postes")).alias("poste"),
        pl.col(first("Poste", "Postes")).str.replace(",", ".").alias("poste_text"),
        pl.col("Candidat").alias("candidate"),
        pl.col(first("Parti", "Partie")).alias("party"),
        pl.col("Votes").cast(pl.Int64).alias("votes"),
        pl.col("TotalValidVotes").cast(pl.Int64).alias("valid"),
        pl.col("TotalRejectedVotes").cast(pl.Int64).alias("rejected"),
        pl.col("TotalVotes").cast(pl.Int64).alias("total"),
    )
    frame = frame.with_columns(
        pl.col("poste").str.replace(",", ".").cast(pl.Float64).round(2),
        pl.col("district").str.strip_chars(),
        pl.col("bureau").str.strip_chars(),
        pl.col("candidate").str.strip_chars(),
    )
    frame = frame.with_columns(
        pl.col("district").str.extract(r"^(\d+)-", 1).cast(pl.Int64).alias("district_no"),
        pl.col("district").str.replace(r"^\d+-", "").alias("district_name"),
        pl.col("bureau").str.extract(r"^(\d+)", 1).cast(pl.Int64).alias("bureau_no"),
        pl.col("bureau").str.extract(r"^\d+(.*)$", 1).str.to_lowercase().alias("bureau_suffix"),
    )
    if frame["district_no"].null_count() or frame["bureau_no"].null_count():
        raise ValueError(f"{year}: a station without a district or bureau number")
    if year == 2013:
        # The 2013 file prints a post's number with its two parts swapped, as seat.borough: 10.11, the borough
        # councillor of district 1 in borough 10, reads 11.10, and 1.00, the mayor of borough 1, reads 0.1. The
        # borough is the district number without its last digit.
        frame = frame.with_columns(
            (pl.col("district_no") // 10 + (pl.col("poste").floor() / 100).round(2)).alias("poste")
        )
        frame = frame.with_columns(
            pl.when(pl.col("poste_text") == "0.0").then(0.0).otherwise(pl.col("poste")).alias("poste")
        )
    mapped = roles(year)
    frame = frame.with_columns(
        pl.col("poste")
        .map_elements(lambda value: mapped.get(value), return_dtype=pl.String, skip_nulls=False)
        .alias("role")
    )
    unknown = frame.filter(pl.col("role").is_null())["poste"].unique().to_list()
    if unknown:
        raise ValueError(f"{year}: posts without a type: {unknown}")
    return frame


def station(row_district: int, bureau_no: int, suffix: str) -> str:
    return f"{row_district:03d}-{bureau_no:03d}{suffix}"


def _name_key(*parts: str | None) -> str:
    tokens = re.findall(r"[a-z]+", fold(" ".join(part for part in parts if part)))
    return "".join(sorted(tokens))


class Target(NamedTuple):
    contest: str
    tag: str
    options: dict[str, str]
    votes: dict[str, int | None]
    election: str


@functools.cache
def existing(year: int) -> dict[tuple[str, float], list[Target]]:
    """The province-wide file's contests for Montréal, by post type and number, with each candidate's option."""
    rows = qc.table(year).filter(
        (pl.col("mun") == MUNICIPALITY)
        & (~pl.col("status").str.to_lowercase().is_in(list(qc.VACANT)) | pl.col("last").is_not_null())
    )
    found: dict[tuple[str, float], list[Target]] = defaultdict(list)
    groups = qc._posts(rows).partition_by(["mun", "arr", "role", "poste", "split"], maintain_order=True, as_dict=True)
    for (mun, arr, role, poste, split), group in groups.items():
        tag = f"{mun}:{arr or ''}:{role}:{poste}" + (f":{split}" if split else "")
        options: dict[str, str] = {}
        votes: dict[str, int | None] = {}
        for position, row in enumerate(group.iter_rows(named=True), start=1):
            if row["last"] is None and row["first"] is None:
                continue
            name = _name_key(row["last"], row["first"])
            option = qc.key("option", year, f"{tag}:{position}")
            options[name] = option
            votes[option] = qc._number(row["votes"])
        try:
            number = round(float(str(poste).replace(",", ".")), 2)
        except ValueError:
            continue
        found[(str(role), number)].append(
            Target(qc.key("contest", year, tag), tag, options, votes, qc.key("election", year, MUNICIPALITY))
        )
    return found


def _district_post(poste: float) -> float:
    return round(int(poste * 10 + 1e-6) / 10, 1)


def _candidates(frame: pl.DataFrame) -> dict[str, int]:
    return {name: int(total) for name, total in frame.group_by("candidate").agg(pl.col("votes").sum()).iter_rows()}


class Match(NamedTuple):
    target: Target
    options: dict[str, str]
    unmatched: list[str]


def match(year: int) -> tuple[dict[tuple[str, float, str], Match], list[str]]:
    """Each Montréal post's contest, by (role, post number, contest id), and what could not be matched."""
    posts = existing(year)
    table_ = table(year)
    found: dict[tuple[str, float, str], Match] = {}
    problems: list[str] = []
    parts = sorted(
        table_.partition_by(["role", "poste"], as_dict=True).items(), key=lambda item: (item[0][0], item[0][1])
    )
    # The 2009 and 2013 files of the province number a borough councillor's post like the district's city councillor
    # (10.1), where the city numbers it 10.11 and 10.12 for the district's seats.
    lumped = defaultdict(int)
    for (role, poste), _ in parts:
        lumped[(str(role), _district_post(float(str(poste))))] += 1
    for (role, poste), part in parts:
        role, poste = str(role), float(str(poste))
        targets = posts.get((role, poste), [])
        if not targets and role == "borough_councillor" and lumped[(role, _district_post(poste))] == 1:
            targets = posts.get((role, _district_post(poste)), [])
        if not targets:
            problems.append(f"{year} {role} {poste}: no contest in the province-wide file")
            continue
        names = {candidate: _name_key(candidate) for candidate in part["candidate"].unique().to_list()}
        totals = _candidates(part)
        assigned: dict[str, list[str]] = defaultdict(list)
        stray: list[str] = []
        for candidate, key in names.items():
            hits = [target for target in targets if key in target.options]
            if len(hits) == 1:
                assigned[hits[0].contest].append(candidate)
                continue
            # A name the file spells differently: the candidate whose official total equals this count.
            same = [
                (target, option)
                for target in targets
                for option, official in target.votes.items()
                if official == totals[candidate] and totals[candidate] > 0
            ]
            taken = {option for target in targets for name, option in target.options.items() if name in names.values()}
            same = [(target, option) for target, option in same if option not in taken]
            if len(same) == 1:
                assigned[same[0][0].contest].append(candidate)
                same[0][0].options[key] = same[0][1]
            else:
                stray.append(candidate)
        for target in targets:
            members = assigned.get(target.contest, [])
            if not members:
                continue
            options = {candidate: target.options[names[candidate]] for candidate in members}
            found[(role, poste, target.contest)] = Match(target, options, [])
        for candidate in stray:
            if totals[candidate] > 0:
                problems.append(f"{year} {role} {poste}: {candidate} has {totals[candidate]} votes and no option")
    return found, problems


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


MONTREAL = key("source", "montreal")
SECTION_KEYS = {
    2025: lambda props: props["CODE_SECTION"],
    2021: lambda props: props["NOM_SECTION"],
    2017: lambda props: props["NOM_SECTION"],
    2009: lambda props: props["SECTION"],
}
DISTRICT_KEYS = {
    2025: lambda props: (int(props["NO_DISTRICT"]), props["NOM_DISTRICT"]),
    2021: lambda props: (int(props["num"]), props["nom"]),
    2017: lambda props: (int(props["num"]), props["nom"]),
    2013: lambda props: (int(props["num"]), props["nom"]),
}
OUTLINE_YEARS = {"section": tuple(SECTION_KEYS), "district": tuple(DISTRICT_KEYS)}


def until(year: int) -> date | None:
    later = [DAYS[other] for other in YEARS if other > year]
    return later[0] if later else None


def _shape(geometry: object) -> shapely.Geometry:
    outline = shapely.geometry.shape(geometry)  # type: ignore[arg-type]
    shape = shapely.simplify(shapely.make_valid(outline), SIMPLIFY, preserve_topology=True)
    pieces = [part for part in shapely.get_parts(shapely.make_valid(shape)) if shapely.get_type_id(part) in (3, 6)]
    return shapely.set_srid(shapely.MultiPolygon(list(shapely.get_parts(pieces))), 4326)


def _load(table_: dict[int, str], year: int) -> list[dict[str, object]]:
    found: list[dict[str, object]] = json.loads(raw.cached(SOURCE, url(table_, year)).path.read_bytes())["features"]
    return found


def section_area(year: int, code: str) -> str:
    return key("area", "section", year, code)


def district_area(year: int, number: int) -> str:
    return key("area", "district", year, number)


def _write_outline(p: Package, area: str, shape: shapely.Geometry, year: int) -> None:
    p.add(
        "area_geometry",
        area_id=area,
        valid_from=DAYS[year],
        valid_to=until(year),
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality="official",
        source_id=MONTREAL,
    )


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The polling sections and electoral districts of Montréal's general elections, as the City published them",
    )
    p.external |= {qc.CANADA, qc.QUEBEC, qc.area(MUNICIPALITY)}
    p.add(
        "source",
        id=MONTREAL,
        kind="official_body",
        publisher="Ville de Montréal",
        title="Élections municipales: résultats détaillés, sections de vote et districts électoraux",
        url=PAGE,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Ville de Montréal, données ouvertes (CC BY 4.0)",
    )
    p.add("scheme", code=SCHEME, polity_id=qc.QUEBEC, kind="precinct", rank=5, noun="polling station", code_system=None)
    p.add(
        "scheme",
        code=DISTRICT_SCHEME,
        polity_id=qc.QUEBEC,
        kind="electoral_district",
        rank=4,
        noun="electoral district",
        code_system=None,
    )
    for table_ in (SECTIONS, DISTRICTS):
        for year in table_:
            p.artifact(raw.cached(SOURCE, url(table_, year)), MONTREAL)
    for scheme, years in (("section", SECTION_KEYS), ("district", DISTRICT_KEYS)):
        for year in years:
            retrieved = raw.cached(SOURCE, url(SECTIONS if scheme == "section" else DISTRICTS, year)).retrieved_at
            p.add(
                "boundary_set",
                id=key("boundary_set", scheme, year),
                scheme_code=SCHEME if scheme == "section" else DISTRICT_SCHEME,
                vintage=str(year),
                valid_from=DAYS[year],
                valid_to=until(year),
                as_of=retrieved.date(),
            )
    for year, finder in DISTRICT_KEYS.items():
        parts: dict[int, tuple[str, list[shapely.Geometry]]] = {}
        for feature in _load(DISTRICTS, year):
            number, name = finder(feature["properties"])  # type: ignore[arg-type]
            parts.setdefault(number, (name, []))[1].append(
                shapely.make_valid(shapely.geometry.shape(feature["geometry"]))  # type: ignore[arg-type]
            )
        for number, (name, shapes) in sorted(parts.items()):
            area = district_area(year, number)
            p.add("area", id=area, scheme_code=DISTRICT_SCHEME)
            p.add("area_code", area_id=area, code=f"{number:03d}", valid_from=DAYS[year], valid_to=until(year))
            p.add(
                "area_parent",
                area_id=area,
                parent_id=qc.area(MUNICIPALITY),
                valid_from=DAYS[year],
                valid_to=until(year),
            )
            p.name(area, f"{name} ({year})", "fr", "official")
            _write_outline(p, area, _shape(shapely.geometry.mapping(shapely.union_all(shapes))), year)
    for year, finder in SECTION_KEYS.items():
        districts = {
            number
            for number, _ in (
                DISTRICT_KEYS[y](f["properties"]) for y in [year] if y in DISTRICT_KEYS for f in _load(DISTRICTS, y)
            )
        }
        for feature in _load(SECTIONS, year):
            code = str(finder(feature["properties"]))  # type: ignore[arg-type]
            area = section_area(year, code)
            p.add("area", id=area, scheme_code=SCHEME)
            p.add("area_code", area_id=area, code=code, valid_from=DAYS[year], valid_to=until(year))
            number = int(code.split("-")[0])
            if number in districts:
                p.add(
                    "area_parent",
                    area_id=area,
                    parent_id=district_area(year, number),
                    valid_from=DAYS[year],
                    valid_to=until(year),
                )
            else:
                p.add(
                    "area_parent",
                    area_id=area,
                    parent_id=qc.area(MUNICIPALITY),
                    valid_from=DAYS[year],
                    valid_to=until(year),
                )
            p.name(area, f"Section {code} ({year})", "fr", "official")
            _write_outline(p, area, _shape(feature["geometry"]), year)
    return p


def _station_code(row: tuple[int, int, str]) -> str:
    return station(row[0], row[1], row[2])


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Montréal municipal general election by polling station: city and borough mayors, city and "
        "borough councillors",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {qc.CANADA, qc.area(MUNICIPALITY)}
    for part in (RESULTS, POSTS):
        p.artifact(raw.cached(SOURCE, url(part, year)), MONTREAL)
    p.uses(raw.cached(qc.SOURCE, qc.url(year)))
    sections: set[str] = set()
    if year in SECTION_KEYS:
        sections = {str(SECTION_KEYS[year](f["properties"])) for f in _load(SECTIONS, year)}  # type: ignore[arg-type]
    data = table(year)
    found, _problems = match(year)
    p.external |= {found[k].target.election for k in found}
    units: dict[tuple[int, int, str], str] = {}
    close = day + timedelta(days=1)
    mismatched = 0
    for (role, poste, contest), hit in sorted(found.items()):
        part = data.filter(
            (pl.col("role") == role) & (pl.col("poste") == poste) & pl.col("candidate").is_in(list(hit.options))
        )
        # A post spread over several contests of the province's file (a seat count it splits) keeps only the
        # candidates this contest holds.
        p.external |= {contest, *hit.options.values()}
        result_set = p.id("result_set", year, contest)
        differs = []
        for candidate, option in hit.options.items():
            official = hit.target.votes[option]
            counted = int(part.filter(pl.col("candidate") == candidate)["votes"].sum())
            if official is not None and official != counted:
                differs.append(f"{candidate}: {counted} at the stations, {official} in the province's count")
        mismatched += bool(differs)
        p.add(
            "result_set",
            id=result_set,
            election_id=hit.target.election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=MONTREAL,
            status="active",
            is_default=False,
            completeness=None if differs else 1.0,
        )
        p.add(
            "note",
            id=p.id("note", result_set, "method"),
            entity_id=result_set,
            kind="methodology",
            body_md=METHOD,
            lang="en",
        )
        if differs:
            p.add(
                "note",
                id=p.id("note", result_set, "differs"),
                entity_id=result_set,
                kind="caveat",
                body_md="The station counts published by the City add up to different totals than the province's "
                "count of this post for: " + "; ".join(differs) + ".",
                lang="en",
            )
        for row in part.iter_rows(named=True):
            number = (int(row["district_no"]), int(row["bureau_no"]), str(row["bureau_suffix"]))
            if number not in units:
                code = _station_code(number)
                unit = units[number] = p.id("unit", day, code)
                exact = code in sections
                base = station(*number[:2], "")
                if exact:
                    area, relation = section_area(year, code), "equals"
                elif base in sections:
                    area, relation = section_area(year, base), "within"
                elif year in DISTRICT_KEYS:
                    area, relation = district_area(year, number[0]), "within"
                else:
                    area, relation = qc.area(MUNICIPALITY), "within"
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=qc.CANADA,
                    scheme_code=SCHEME,
                    code=f"{day}:{code}",
                    area_relation=relation,
                    area_id=area,
                    kind="standard",
                    valid_from=day,
                    valid_to=close,
                )
            unit = units[number]
            _value(p, result_set, unit, hit.options[row["candidate"]], 1, int(row["votes"]))
        totals = part.group_by("district_no", "bureau_no", "bureau_suffix").agg(
            pl.col("valid").first(), pl.col("rejected").first(), pl.col("total").first()
        )
        for row in totals.iter_rows(named=True):
            unit = units[(int(row["district_no"]), int(row["bureau_no"]), str(row["bureau_suffix"]))]
            _value(p, result_set, unit, None, 4, int(row["valid"]))
            _value(p, result_set, unit, None, 5, int(row["rejected"]))
            _value(p, result_set, unit, None, 3, int(row["total"]))
    return p


def _value(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )
