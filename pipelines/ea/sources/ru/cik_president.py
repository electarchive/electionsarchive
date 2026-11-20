"""Russia's presidential elections of 2000 to 2024 by precinct election commission (UIK), for `ru.cik`.

The Central Election Commission publishes each precinct commission's protocol on its results portal
(izbirkom.ru), which does not answer scripted requests. Sergey Shpilkin (2000 to 2018) and Ivan Shukshin (2024)
copied the protocols from the portal into one table per election, published in Dmitry Kobak's repository
github.com/dkobak/elections; the files are read from it at a pinned commit. Each row is one UIK with its subject and
its territorial commission (TIK), the protocol's lines, and the votes for each candidate.

What the tables leave out, as the repository's notes say:

- 2000: the 104 TIKs of the Chechen Republic, the Sakha Republic, and some other subjects (2,930,483 registered
  voters), and every polling station abroad.
- 2018: four precincts whose results the Commission later canceled are still in the table; they are dropped here,
  so the totals are the Commission's.
- 2024: the 2,268 precincts in the four occupied Ukrainian regions, for which no precinct results were released.
  The remote electronic vote is a separate table by subject, stored as one aggregate unit per subject.

Precincts in Crimea and Sevastopol (2018 and 2024) are kept, because the Commission's national count includes them,
but their units are left unlocated: the archive places that territory under Ukraine.
"""

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date

import polars as pl
import shapely

from ea import raw
from ea.package import Package
from ea.sources.ru import cik

SOURCE = cik.SOURCE
COMMIT = "a6530610baf200b15442239511a1375c51ba1cb5"
REPOSITORY = f"https://raw.githubusercontent.com/dkobak/elections/{COMMIT}/data/"
PORTAL = "http://www.izbirkom.ru/region/izbirkom"
UIK = "ru.uik"
DERIVED = cik.DERIVED

PORTAL_SOURCE = cik.key("source", "izbirkom")
GEO = f"{SOURCE}/geo-2000"
REGISTRY = f"{SOURCE}/registry-2000"

CRIMEA = {"CR", "SEV"}
OCCUPIED_2022 = ("донецк", "луганск", "запорож", "херсон")
BAIKONUR = "baikonur"

# Subjects formed by the mergers of 2005 to 2008. The ISO codes of Perm, Krasnoyarsk, Kamchatka, and Irkutsk passed
# to the merged subjects, whose outlines geoBoundaries draws; Zabaykalsky Krai is new.
MERGERS = {
    "PER": date(2005, 12, 1),
    "KYA": date(2007, 1, 1),
    "KAM": date(2007, 7, 1),
    "IRK": date(2008, 1, 1),
    "ZAB": date(2008, 3, 1),
}
ZAB_NAME = "Забайкальский край"


@dataclass(frozen=True)
class Election:
    year: int
    day: date
    first: date
    winner: str


ELECTIONS = [
    Election(2000, date(2000, 3, 26), date(2000, 3, 26), "ПУТИН"),
    Election(2004, date(2004, 3, 14), date(2004, 3, 14), "Путин"),
    Election(2008, date(2008, 3, 2), date(2008, 3, 2), "Медведев"),
    Election(2012, date(2012, 3, 4), date(2012, 3, 4), "Путин"),
    Election(2018, date(2018, 3, 18), date(2018, 3, 18), "Путин"),
    Election(2024, date(2024, 3, 17), date(2024, 3, 15), "Путин"),
]
BY_YEAR = {e.year: e for e in ELECTIONS}

# Rows (1-based, after the header) of the four 2018 precincts whose results the Commission canceled after the count,
# which the repository's notes list.
CANCELED_2018 = (45558, 45562, 92553, 92560)

# Registered voters the repository's notes give for each table, and the Commission's totals they cite.
NOTED = {
    2000: 105_635_786,
    2004: 108_064_342,
    2008: 107_222_016,
    2012: 109_860_331,
    2018: 109_008_428,
    2024: 99_764_974,
}


def results_url(year: int) -> str:
    return f"{REPOSITORY}{year}.csv.zip"


ELECTRONIC = f"{REPOSITORY}2024-electronic-voting.csv"
NOTES = f"{REPOSITORY}README.md"


def fetch() -> None:
    for e in ELECTIONS:
        raw.fetch(SOURCE, results_url(e.year), suffix=".zip")
    raw.fetch(SOURCE, ELECTRONIC, suffix=".csv")
    raw.fetch(SOURCE, NOTES, suffix=".md")
    raw.fetch(SOURCE, cik.LAYER, suffix=".geojson")
    raw.fetch(SOURCE, cik.META, suffix=".json")


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"{SOURCE}/president-{e.year}" for e in ELECTIONS)]


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        president(e).write()


# ----------------------------------------------------------------------------------------------------------------
# Reading the tables


def subject(text: str) -> str:
    """The ISO 3166-2 suffix of a subject the tables name, BAIKONUR, cik.ABROAD, or 'occupied' for the four regions
    of 2022 (whose rows are empty)."""
    folded = re.sub(r"[^а-я]", "", text.lower().replace("ё", "е"))
    if "байконур" in folded:
        return BAIKONUR
    if "забайкал" in folded:
        return "ZAB"
    if "севастопол" in folded:
        return "SEV"
    if "крым" in folded:
        return "CR"
    if any(stem in folded for stem in OCCUPIED_2022):
        return "occupied"
    return cik.subject(text)


@dataclass
class Table:
    frame: pl.DataFrame
    candidates: list[str]
    against: str | None


# The protocol lines each year's table holds, by the words its headers use; ballots for absentee certificates and
# the lines that break down invalid ballots are not read.
ACCOUNTING = {
    "registered": r"^число избирателей, (включенных|внесенных) в спис",
    "early": r"^число избирательных бюллетеней, выданных .*досрочно\b",
    "inside": r"^число избирательных бюллетеней, выданных (избирателям на избирательных участках |в помещени[ия]х? "
    r"для голосования |)в день голосования",
    "outside": r"^число избирательных бюллетеней, выданных (избирателям, проголосовавшим )?вне помещени",
    "portable": r"^число избирательных бюллетеней,? (содержащихся )?в переносных ящиках",
    "stationary": r"^число (избирательных )?бюллетеней,? (содержащихся )?в стационарных ящиках",
    "invalid": r"^(общее )?число недействительных избирательных бюллетеней$",
    "valid": r"^число действительных избирательных бюллетеней$",
}


def _accounting(columns: list[str]) -> dict[str, str]:
    """The protocol line each accounting column holds."""
    found: dict[str, str] = {}
    for column in columns:
        text = " ".join(column.lower().split())
        for name, pattern in ACCOUNTING.items():
            if re.search(pattern, text):
                if name in found:
                    raise ValueError(f"two columns for {name}: {found[name]!r} and {column!r}")
                found[name] = column
    missing = set(ACCOUNTING) - set(found)
    if missing:
        raise ValueError(f"no column for {sorted(missing)}")
    return found


def _candidates(columns: list[str]) -> tuple[list[str], str | None]:
    names = [c for c in columns if not c.startswith("Число") and not c.startswith("Общее")]
    names = [c for c in names if c not in {"region", "tik", "uik", "url"}]
    against = next((c for c in names if c == "Против всех"), None)
    return [c for c in names if c != against], against


def read(year: int) -> Table:
    path = raw.cached(SOURCE, results_url(year)).path
    with zipfile.ZipFile(path) as archive:
        (name,) = archive.namelist()
        content = archive.read(name)
    frame = pl.read_csv(io.BytesIO(content), infer_schema_length=0)
    candidates, against = _candidates(frame.columns)
    frame = frame.with_row_index("row", offset=1)
    return Table(frame, candidates, against)


def printed(column: str) -> str:
    """A candidate column's name without the 2000 table's ballot number."""
    return re.sub(r"^\d+\.\s*", "", column).strip()


def person_name(text: str) -> str:
    """Surname Given Patronymic, with the surname in sentence case where the table prints it in capitals."""
    return cik.person_name(text) if text.split(" ", 1)[0].isupper() else text


def person_key(text: str) -> str:
    return cik.key("person", "president", re.sub(r"[^а-я ]", "", person_name(text).lower().replace("ё", "е")))


def uik_number(text: str) -> str:
    match = re.fullmatch(r"(?:УИК\s*№?\s*)?(\d+)", text.strip())
    if not match:
        raise ValueError(f"unreadable UIK {text!r}")
    return str(int(match.group(1)))


# ----------------------------------------------------------------------------------------------------------------
# Packages


def _source(p: Package) -> None:
    p.add(
        "source",
        id=PORTAL_SOURCE,
        kind="official_body",
        publisher="Центральная избирательная комиссия Российской Федерации",
        title="Выборы Президента Российской Федерации: результаты по участковым избирательным комиссиям",
        url=PORTAL,
        license="Official election results of a state body; not subject to copyright (Civil Code of the Russian "
        "Federation, art. 1259(6))",
        license_url="https://www.consultant.ru/document/cons_doc_LAW_64629/",
        attribution="Центральная избирательная комиссия Российской Федерации",
        notes="The precinct commissions' protocols as the Commission's portal published them, read from the copies "
        "Sergey Shpilkin (2000-2018) and Ivan Shukshin (2024) made of the portal and published in "
        f"github.com/dkobak/elections at commit {COMMIT}; the portal does not answer scripted requests.",
    )


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The precinct election commission scheme, Zabaykalsky Krai, and the outlines of the five federal subjects "
        "formed by the mergers of 2005 to 2008, for the presidential elections of 2000 to 2024",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {cik.RUSSIA, cik.TERRITORY, cik.SHAPES}
    p.uses(raw.cached(SOURCE, cik.LAYER))
    p.uses(raw.cached(SOURCE, cik.META))
    p.add(
        "scheme",
        code=UIK,
        polity_id=cik.RUSSIA,
        kind="precinct",
        rank=4,
        noun="precinct election commission",
        code_system=None,
    )
    zab = cik.subject_area("ZAB")
    p.add("area", id=zab, scheme_code=cik.SUBJECT)
    p.add("area_code", area_id=zab, code="RU-ZAB", valid_from=MERGERS["ZAB"])
    p.add("area_parent", area_id=zab, parent_id=cik.TERRITORY, valid_from=MERGERS["ZAB"])
    p.name(zab, ZAB_NAME, "ru", "official")
    shapes = cik.outlines()
    for iso, since in MERGERS.items():
        area = cik.subject_area(iso)
        if iso != "ZAB":
            p.external.add(area)
        _outline(p, area, shapes[iso], since)
    return p


def _outline(p: Package, id: str, shape: shapely.Geometry, since: date) -> None:
    gridded = shapely.set_precision(shapely.make_valid(shape), cik.GRID)
    polygons = [part for part in shapely.get_parts(gridded) if shapely.get_type_id(part) == 3 and not part.is_empty]
    outline = shapely.set_srid(shapely.normalize(shapely.MultiPolygon(polygons)), 4326)
    p.add(
        "area_geometry",
        area_id=id,
        valid_from=since,
        geom=shapely.to_wkb(outline, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(outline), 4326), hex=True, include_srid=True
        ),
        quality="osm",
        source_id=cik.SHAPES,
    )


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The candidates in the presidential elections of 2000 to 2024 and the Commission's results portal, from "
        "the Central Election Commission",
        depends=[f"{SOURCE}/registry"],
    )
    _source(p)
    seen: set[str] = set()
    for e in ELECTIONS:
        table = read(e.year)
        p.uses(raw.cached(SOURCE, results_url(e.year)))
        for column in table.candidates:
            text = printed(column)
            person = person_key(text)
            if person in seen:
                continue
            seen.add(person)
            p.add("person", id=person)
            p.name(person, person_name(text), "ru", "official")
    return p


METHOD = (
    "Each precinct election commission's (UIK) protocol, as the Commission's results portal published it, read from "
    "the copy Sergey Shpilkin made of the portal (Ivan Shukshin's for 2024) in github.com/dkobak/elections at commit "
    f"{COMMIT[:9]}. Registered voters, valid and invalid ballots are the protocol's lines; voters are the ballots "
    "issued early, in the polling room, and outside it, and ballots cast are the ballots found in the portable and "
    "stationary boxes, both added here. Each UIK's unit names its territorial commission (TIK) as its parent. "
    "Precincts in Crimea and Sevastopol are kept, as the Commission counted them, but are not placed on the map."
)
GAPS = {
    2000: " The table has no precincts for 104 TIKs (all of the Chechen Republic and the Sakha Republic and some in "
    "other subjects, 2,930,483 registered voters) and none abroad, so the set is incomplete; completeness is the "
    "share of the registered voters it holds.",
    2004: " Two precincts that report registered voters but no ballots are dropped, as the Commission's totals "
    "leave them out.",
    2018: " Four precincts whose results the Commission later canceled are dropped, so the totals are the "
    "Commission's.",
    2024: " No precinct results were released for the four occupied Ukrainian regions, and seven precincts report "
    "nothing; both are left out. The remote electronic vote is one aggregate unit per subject that held it.",
}
# The registered voters of the precincts the 2000 table lacks, from the repository's notes; the stations abroad are
# not counted in it.
MISSING_2000 = 2_930_483


def president(e: Election) -> Package:
    key = f"{SOURCE}/president-{e.year}"
    p = Package(
        key,
        SOURCE,
        f"The {e.year} presidential election by precinct election commission (UIK), from the Central Election "
        "Commission's results portal",
        depends=[GEO, REGISTRY, f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {cik.RUSSIA, cik.TERRITORY}
    p.artifact(raw.cached(SOURCE, results_url(e.year)), PORTAL_SOURCE)
    p.uses(raw.cached(SOURCE, NOTES))
    if e.year == 2024:
        p.artifact(raw.cached(SOURCE, ELECTRONIC), PORTAL_SOURCE)
    b = cik.Builder(p)
    path_ = f"ru/{e.day}-presidential"
    election = cik.key("election", "president", e.year)
    p.add(
        "election", id=election, polity_id=cik.RUSSIA, kind="general", date_from=e.first, date_to=e.day, status="held"
    )
    p.add("slug", path=path_, entity_id=election, is_current=True)
    p.name(election, f"{e.year} Russian presidential election", "en", "official")
    p.name(election, f"Выборы Президента Российской Федерации {e.year} года", "ru", "native", False)
    contest = b.contest(
        cik.key("contest", "president", e.year),
        election,
        "office",
        f"{path_}/president",
        "Президент Российской Федерации",
        district=cik.TERRITORY,
        magnitude=1,
        system=cik.PRESIDENT_SYSTEM,
        office=cik.PRESIDENCY,
        round_number=1,
    )
    p.external |= {cik.PRESIDENT_SYSTEM, cik.PRESIDENCY}
    result_set = cik.key("result_set", "president", e.year)

    table = read(e.year)
    columns = _accounting(table.frame.columns)
    options: dict[str, str] = {}
    for position, column in enumerate(table.candidates, start=1):
        text = printed(column)
        option = options[column] = b.option(
            cik.key("option", "president", e.year, text), contest, "candidate", position, text
        )
        person = person_key(text)
        p.external.add(person)
        candidacy = cik.key("candidacy", "president", e.year, text)
        p.add("candidacy", id=candidacy, person_id=person, option_id=option, role="head", ballot_name=text)
        won = text.split(" ", 1)[0] == e.winner
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if won else "lost")
    if table.against:
        options[table.against] = b.option(
            cik.key("option", "president", e.year, "against"), contest, "none_of_the_above", None, "Против всех"
        )

    frame = _numbers(table.frame, [*columns.values(), *options])
    frame = frame.with_columns(pl.col("region").map_elements(subject, return_dtype=pl.String).alias("iso"))
    dropped = _dropped(e.year, frame, columns)
    frame = frame.filter(~pl.col("row").is_in(dropped))
    occupied = frame.filter(pl.col("iso") == "occupied")
    if occupied.select(pl.col(columns["registered"]).is_not_null().any()).item():
        raise ValueError("the occupied regions of 2022 have figures; they need a place")
    frame = frame.filter(pl.col("iso") != "occupied")

    units, uiks = _units(e, frame, b)
    values = _values(result_set, uiks, columns, options)
    if e.year == 2024:
        evote = _electronic(e, b, columns, options, result_set)
        values = pl.concat([values, evote])
    p.frame("result_value", values)
    for row in units:
        p.add("reporting_unit", **row)

    registered = int(values.filter(pl.col("measure") == 2).select(pl.col("value").sum()).item())
    completeness: float | None = 1.0
    if e.year == 2000:
        completeness = cik.completeness(registered, registered + MISSING_2000)
    elif e.year == 2024:
        completeness = None
    b.result_set(result_set, election, contest, True, METHOD + GAPS.get(e.year, ""), completeness)
    summary = values.filter(pl.col("option_id").is_not_null()).group_by("option_id").agg(pl.col("value").sum())
    total = dict(zip(summary["option_id"].to_list(), summary["value"].to_list(), strict=True))
    winner = next(options[c] for c in table.candidates if printed(c).split(" ", 1)[0] == e.winner)
    if total[winner] != max(total.values()):
        raise ValueError(f"{e.year}: the declared winner does not lead the count")
    print(
        f"{key}: {len(uiks):,} UIKs, {registered:,} registered (notes: {NOTED[e.year]:,}); "
        + ", ".join(f"{printed(c)} {total[options[c]]:,}" for c in options),
        flush=True,
    )
    return p


def _numbers(frame: pl.DataFrame, columns: list[str]) -> pl.DataFrame:
    return frame.with_columns(pl.col(c).str.strip_chars().cast(pl.Int64, strict=True) for c in columns)


def _dropped(year: int, frame: pl.DataFrame, columns: dict[str, str]) -> list[int]:
    if year == 2018:
        return list(CANCELED_2018)
    if year in (2004, 2024):
        empty = frame.filter(
            pl.col(columns["registered"]).is_not_null() & ((pl.col(columns["valid"]) + pl.col(columns["invalid"])) == 0)
        )
        return empty["row"].to_list()
    return []


def _place(iso: str) -> tuple[str, str, str | None, str]:
    """A subject's code part, the unit relation, the area, and the unit kind for a precinct within it."""
    if iso == cik.ABROAD:
        return "abroad", "unlocated", None, "abroad_mission"
    if iso == BAIKONUR:
        return "Baikonur", "unlocated", None, "standard"
    if iso in CRIMEA:
        return ("Crimea" if iso == "CR" else "Sevastopol"), "unlocated", None, "standard"
    return f"RU-{iso}", "within", cik.subject_area(iso), "standard"


def _units(e: Election, frame: pl.DataFrame, b: cik.Builder) -> tuple[list[dict[str, object]], pl.DataFrame]:
    rows: list[dict[str, object]] = []
    tiks: dict[tuple[str, str], str] = {}
    ids: list[str] = []
    codes: set[str] = set()
    for iso, tik, uik in zip(frame["iso"], frame["tik"], frame["uik"], strict=True):
        place, relation, area, kind = _place(iso)
        if (place, tik) not in tiks:
            tid = tiks[(place, tik)] = cik.key("unit", cik.TIK, f"{e.day}:{place}:{tik}")
            rows.append(
                {
                    "id": tid,
                    "polity_id": cik.RUSSIA,
                    "scheme_code": cik.TIK,
                    "code": f"{e.day}:{place}:{tik}",
                    "area_relation": relation,
                    "area_id": area,
                    "kind": kind,
                }
            )
        code = f"{e.day}:{place}:{tik}:{uik_number(uik)}"
        if code in codes:
            raise ValueError(f"{e.year}: UIK {code} appears twice")
        codes.add(code)
        id = cik.key("unit", UIK, code)
        ids.append(id)
        rows.append(
            {
                "id": id,
                "polity_id": cik.RUSSIA,
                "scheme_code": UIK,
                "code": code,
                "parent_id": tiks[(place, tik)],
                "area_relation": relation,
                "area_id": area,
                "kind": kind,
            }
        )
    b.units.update(str(row["id"]) for row in rows)
    return rows, frame.with_columns(pl.Series("unit_id", ids))


def _values(result_set: str, frame: pl.DataFrame, columns: dict[str, str], options: dict[str, str]) -> pl.DataFrame:
    c = {name: pl.col(column) for name, column in columns.items()}
    lines: list[tuple[str | None, int, pl.Expr, int]] = [
        (None, 2, c["registered"], 0),
        (None, 14, c["early"] + c["inside"] + c["outside"], DERIVED),
        (None, 3, c["portable"] + c["stationary"], DERIVED),
        (None, 4, c["valid"], 0),
        (None, 5, c["invalid"], 0),
        *((option, 1, pl.col(column), 0) for column, option in options.items()),
    ]
    pieces = [
        frame.select(
            pl.lit(result_set).alias("result_set_id"),
            pl.col("unit_id"),
            pl.lit(option, dtype=pl.String).alias("option_id"),
            pl.lit(0, dtype=pl.Int16).alias("channel"),
            pl.lit(measure, dtype=pl.Int16).alias("measure"),
            pl.lit(0, dtype=pl.Int16).alias("count_round"),
            pl.lit(flags, dtype=pl.Int16).alias("flags"),
            expr.cast(pl.Int64).alias("value"),
        )
        for option, measure, expr, flags in lines
    ]
    return pl.concat(pieces).filter(pl.col("value").is_not_null())


def _electronic(
    e: Election, b: cik.Builder, columns: dict[str, str], options: dict[str, str], result_set: str
) -> pl.DataFrame:
    path = raw.cached(SOURCE, ELECTRONIC).path
    frame = pl.read_csv(path, infer_schema_length=0)
    names = {printed(c): c for c in options}
    frame = frame.rename({c: names[c] for c in frame.columns if c in names})
    frame = _numbers(frame, [*columns.values(), *options])
    ids: list[str] = []
    for region in frame["region"]:
        place, relation, area, _ = _place(subject(region))
        code = f"{e.day}:{place}:ДЭГ"
        id = cik.key("unit", UIK, code)
        if id in b.units:
            raise ValueError(f"{code} appears twice")
        b.units.add(id)
        b.p.add(
            "reporting_unit",
            id=id,
            polity_id=cik.RUSSIA,
            scheme_code=UIK,
            code=code,
            area_relation=relation,
            area_id=area,
            kind="aggregate",
        )
        ids.append(id)
    return _values(result_set, frame.with_columns(pl.Series("unit_id", ids)), columns, options)
