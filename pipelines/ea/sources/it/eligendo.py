"""Italy's European Parliament elections, 1979-2024, by municipality, from the Interior Ministry's open data."""

import io
import re
import unicodedata
import zipfile
from datetime import date
from functools import cache
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "it.eligendo"
OPENDATA = "https://dait.interno.gov.it/documenti/opendata/europee/europee-{day:%Y%m%d}.zip"
PAGE = "https://elezionistorico.interno.gov.it/eligendo/opendata.php"
# Italy's polity id in the archive, minted before country ids followed the package namespace.
ITALY = "01a13bf7-2f71-7303-964e-fdd9421ca0b6"
DAYS = [
    date(1979, 6, 10),
    date(1984, 6, 17),
    date(1989, 6, 18),
    date(1994, 6, 12),
    date(1999, 6, 13),
    date(2004, 6, 12),
    date(2009, 6, 7),
    date(2014, 5, 25),
    date(2019, 5, 26),
    date(2024, 6, 9),
]
SPECIAL = "FUORI SEDE"
REGION_ALIASES = {"ABRUZZI": "ABRUZZO"}
LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)
METHOD = (
    "Counted by the municipal electoral offices and published by the Interior Ministry's Eligendo archive as one "
    "row per municipality and list. The file has no ISTAT codes, so places are the municipality, province, and "
    "region names exactly as the file prints them, folded for case, accents, and hyphens, and a municipality "
    "that the file spells differently in another year is a separate area. Registered electors, voters, and blank "
    "ballots are the file's figures. Valid votes are the sum of the municipality's list votes. Invalid ballots "
    "are voters less blank ballots less valid votes, so they include any disputed ballots not assigned to a list. "
    "Seats and the preference votes for candidates are not stored here."
)
FUORI_SEDE_NOTE = (
    " The 2024 file also lists the special polling stations for voters temporarily living away from the "
    "municipality where they are registered (fuori sede). Each is a separate unit placed in the municipality "
    "where its voters cast their ballots."
)
SYSTEMS = (
    (
        "ep-1979",
        date(1979, 1, 1),
        date(2009, 1, 1),
        {"formula": "hare"},
        (
            "Proportional representation by party list: seats are shared among the lists by the Hare quota and "
            "largest remainders, with no national threshold."
        ),
    ),
    (
        "ep-2009",
        date(2009, 1, 1),
        None,
        {"formula": "hare", "threshold": {"level": "list", "scope": "national", "percent": 4}},
        (
            "Proportional representation by party list: seats are shared among the lists that won at least 4% of "
            "the valid votes nationally, by the Hare quota and largest remainders."
        ),
    ),
)
ACCOUNTING = ((2, "electors"), (3, "voters"), (6, "blank"), (4, "valid"), (5, "invalid"), (15, "contested"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def url(day: date) -> str:
    return OPENDATA.format(day=day)


def packages() -> list[str]:
    return ["it.eligendo/geo", "it.eligendo/registry", *(f"it.eligendo/ep-{day}" for day in DAYS)]


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, url(day), suffix=".zip")


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def fold(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper().replace("-", " ")
    text = re.sub(r"[^A-Z0-9' ]", " ", text)
    text = re.sub(r"'(?= |$)", "", re.sub(r"\s+", " ", text).strip())
    return re.sub(r"\s+", " ", text).strip()


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


COLUMNS = {
    "circ": ("CIRCOSCRIZIONE", "DESCRCIRC", "DESCCIRCEUROPEA"),
    "region": ("REGIONE", "DESCRREG", "DESCREGIONE"),
    "province": ("PROVINCIA", "DESCRPROV", "DESCPROVINCIA"),
    "comune": ("COMUNE", "DESCRCOMUNE", "DESCCOMUNE"),
    "electors": ("ELETTORI",),
    "voters": ("VOTANTI",),
    "blank": ("SCHEDE_BIANCHE", "SCHEDEBIANCHE", "NUMSCHEDEBIANCHE"),
    "contested": ("NUMSCHEDECONTESTAT",),
    "list": ("LISTA", "DESCLISTA"),
    "votes": ("VOTI_LISTA", "VOTILISTA", "NUMVOTI"),
}


def _table(data: bytes, special: int) -> pl.DataFrame:
    frame = pl.read_csv(io.StringIO(_decode(data)), separator=";", infer_schema=False)
    picked = {
        name: next(column for column in options if column in frame.columns)
        for name, options in COLUMNS.items()
        if any(column in frame.columns for column in options)
    }
    frame = frame.select([pl.col(column).alias(name) for name, column in picked.items()])
    if "contested" not in frame.columns:
        frame = frame.with_columns(contested=pl.lit(None, pl.String))
    return frame.with_columns(special=pl.lit(special, pl.Int8)).select(
        "circ", "region", "province", "comune", "electors", "voters", "blank", "contested", "list", "votes", "special"
    )


@cache
def read(day: date) -> pl.DataFrame:
    """The day's rows, one per municipality and list, with folded place keys and integer counts."""
    archive = zipfile.ZipFile(raw.cached(SOURCE, url(day)).path)
    names = archive.namelist()
    if day.year == 2024:
        frame = pl.concat(
            [
                _table(archive.read("EUROPEE_SCRUTINI&LISTE_ITALIA_LivComune.csv"), 0),
                _table(archive.read("EUROPEE_SCRUTINI&LISTE_FUORI_SEDE.csv"), 1),
            ]
        )
    else:
        member = next(name for name in names if name.startswith("europee-"))
        frame = _table(archive.read(member), 0)
    counts = ["electors", "voters", "blank", "contested", "votes"]
    frame = frame.with_columns(pl.col(name).str.strip_chars().cast(pl.Int64) for name in counts)
    clean = lambda column: pl.col(column).map_elements(fold, return_dtype=pl.String)
    return frame.with_columns(
        region_key=clean("region").replace(REGION_ALIASES),
        province_key=clean("province"),
        comune_key=clean("comune"),
        circ_key=pl.when(pl.col("special") == 1).then(clean("circ")).otherwise(pl.lit("")),
        label=pl.col("list").str.strip_chars().str.replace_all(r"\s+", " "),
    )


def _special_place(province: str) -> tuple[str, str] | None:
    found = re.fullmatch(r"(.+?)\s*\((.+)\)", province.strip())
    return (fold(found.group(2)), fold(found.group(1))) if found else None


def _places() -> tuple[dict[str, str], dict[tuple[str, str], str], dict[tuple[str, str, str], str]]:
    """Region, province, and municipality names as last printed, keyed by their folded place."""
    regions: dict[str, str] = {}
    provinces: dict[tuple[str, str], str] = {}
    towns: dict[tuple[str, str, str], str] = {}
    for day in DAYS:
        frame = read(day).filter(pl.col("special") == 0)
        for row in (
            frame.select("region", "region_key", "province", "province_key", "comune", "comune_key")
            .unique()
            .iter_rows()
        ):
            region, region_key, province, province_key, comune, comune_key = row
            regions[region_key] = region.strip()
            provinces[(region_key, province_key)] = province.strip()
            towns[(region_key, province_key, comune_key)] = comune.strip()
    return regions, provinces, towns


def _title(text: str) -> str:
    return text if text != text.upper() else text.title().replace("'S", "'s")


def area(scheme: str, *parts: str) -> str:
    return key("area", scheme, *parts)


def geo() -> Package:
    p = Package(
        "it.eligendo/geo",
        SOURCE,
        "The regions, provinces, and municipalities that appear in the Interior Ministry's European Parliament "
        "results, 1979-2024, named as the files print them",
    )
    source = key("source", SOURCE)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Ministero dell'Interno",
        title="Eligendo: archivio storico delle elezioni, open data",
        url=PAGE,
        license=LICENSE,
        notes="Place names are taken from the European Parliament results files, which carry no ISTAT codes.",
    )
    for day in DAYS:
        p.artifact(raw.cached(SOURCE, url(day)), source)
    regions, provinces, towns = _places()
    for code, rank, noun in (
        ("it.eligendo_regione", 1, "region"),
        ("it.eligendo_provincia", 2, "province"),
        ("it.eligendo_comune", 3, "municipality"),
    ):
        p.add("scheme", code=code, polity_id=ITALY, kind="administrative", rank=rank, noun=noun)
    p.external.add(ITALY)

    def place(scheme: str, code: str, text: str, parent: str | None) -> None:
        id = area(scheme, code)
        p.add("area", id=id, scheme_code=scheme)
        p.add("area_code", area_id=id, code=code)
        if parent is not None:
            p.add("area_parent", area_id=id, parent_id=parent)
        p.name(id, _title(text), "it", "official")

    for region_key, text in sorted(regions.items()):
        place("it.eligendo_regione", region_key, text, None)
    for (region_key, province_key), text in sorted(provinces.items()):
        place(
            "it.eligendo_provincia",
            f"{region_key}|{province_key}",
            text,
            area("it.eligendo_regione", region_key),
        )
    for (region_key, province_key, comune_key), text in sorted(towns.items()):
        place(
            "it.eligendo_comune",
            f"{region_key}|{province_key}|{comune_key}",
            text,
            area("it.eligendo_provincia", f"{region_key}|{province_key}"),
        )
    return p


def _lists() -> list[str]:
    return sorted({label for day in DAYS for label in read(day)["label"].unique().to_list()})


def party(label: str) -> str:
    return key("party", label)


def registry() -> Package:
    p = Package(
        "it.eligendo/registry",
        SOURCE,
        "The party lists on Italy's European Parliament ballots 1979-2024 and the electoral systems that applied",
    )
    p.external.add(ITALY)
    for day in DAYS:
        p.uses(raw.cached(SOURCE, url(day)))
    for name, since, until, rules, method in SYSTEMS:
        id = key("electoral_system", name)
        p.add(
            "electoral_system", id=id, polity_id=ITALY, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    for label in _lists():
        id = party(label)
        p.add("party", id=id, kind="party", polity_id=ITALY)
        p.name(id, label, "it", "ballot")
    return p


def system(day: date) -> str:
    for name, since, until, _, _ in SYSTEMS:
        if since <= day and (until is None or day < until):
            return key("electoral_system", name)
    raise ValueError(f"no electoral system for {day}")


def election(day: date) -> Package:
    p = Package(
        f"it.eligendo/ep-{day}",
        SOURCE,
        f"Italy's {day.year} European Parliament election by municipality from the Interior Ministry's open data",
        depends=["it.eligendo/geo", "it.eligendo/registry"],
    )
    p.external.add(ITALY)
    source = key("source", SOURCE)
    p.uses(raw.cached(SOURCE, url(day)))
    frame = read(day)
    id = key("election", day)
    p.add(
        "election",
        id=id,
        polity_id=ITALY,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.name(id, f"{day.year} European Parliament election in Italy", "en", "official")
    p.name(id, f"Elezioni europee del {day.year}", "it", "native", False)
    path = f"it/{day}-european-parliament-by-comune"
    p.add("slug", path=path, entity_id=id, is_current=True)
    contest = key("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        round_number=1,
        electoral_system_id=system(day),
        default_result_kind="counted",
    )
    p.name(contest, "Members of the European Parliament elected in Italy", "en", "official")
    p.add("slug", path=f"{path}/lists", entity_id=contest, is_current=True)
    result_set = key("result_set", day)
    p.add(
        "result_set",
        id=result_set,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = METHOD + (FUORI_SEDE_NOTE if day.year == 2024 else "")
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    labels = frame["label"].unique(maintain_order=False).sort().to_list()
    options = {label: key("option", day, label) for label in labels}
    for label in labels:
        p.add(
            "contest_option",
            id=options[label],
            contest_id=contest,
            kind="list",
            party_id=party(label),
            ballot_label=label,
        )
    keys = ["region_key", "province_key", "comune_key", "circ_key", "special"]
    units = frame.group_by(keys).agg(
        pl.col("region").first(),
        pl.col("province").first(),
        pl.col("comune").first(),
        pl.col("electors").first(),
        pl.col("voters").first(),
        pl.col("blank").first(),
        pl.col("contested").first(),
        pl.col("votes").sum().alias("valid"),
        pl.col("electors").n_unique().alias("electors_n"),
        pl.col("voters").n_unique().alias("voters_n"),
        pl.col("label").n_unique().alias("lists"),
        pl.len().alias("rows"),
    )
    if units.filter((pl.col("electors_n") > 1) | (pl.col("voters_n") > 1) | (pl.col("rows") != pl.col("lists"))).height:
        raise ValueError(f"{day}: a unit's rows disagree on its accounting or repeat a list")
    towns = _places()[2]
    by_name: dict[tuple[str, str], list[str]] = {}
    for region_key, province_key, comune_key in towns:
        by_name.setdefault((region_key, comune_key), []).append(province_key)
    unit_rows: list[dict[str, object]] = []
    ids: list[str] = []
    for row in units.sort(keys).iter_rows(named=True):
        region_key, province_key, comune_key = row["region_key"], row["province_key"], row["comune_key"]
        if row["special"]:
            where = _special_place(row["province"])
            code = f"{day}:{SPECIAL}:{province_key}:{comune_key}:{row['circ_key']}"
            found = by_name.get((where[0], comune_key), []) if where else []
            if len(found) == 1 and where is not None:
                relation, place = "within", area("it.eligendo_comune", f"{where[0]}|{found[0]}|{comune_key}")
            else:
                relation, place = "unlocated", None
        else:
            code = f"{day}:{region_key}:{province_key}:{comune_key}"
            relation, place = "equals", area("it.eligendo_comune", f"{region_key}|{province_key}|{comune_key}")
        unit = key("unit", code)
        ids.append(unit)
        unit_rows.append(
            {
                "id": unit,
                "polity_id": ITALY,
                "scheme_code": "it.eligendo_comune",
                "code": code,
                "area_relation": relation,
                "area_id": place,
                "kind": "standard",
            }
        )
    p.frame("reporting_unit", pl.DataFrame(unit_rows, schema_overrides={"area_id": pl.String}))
    units = units.sort(keys).with_columns(
        unit=pl.Series(ids), invalid=pl.col("voters") - pl.col("blank") - pl.col("valid")
    )
    negative = units.filter(pl.col("invalid") < 0)
    units = units.with_columns(invalid=pl.when(pl.col("invalid") < 0).then(None).otherwise(pl.col("invalid")))
    facts: list[pl.DataFrame] = []
    for measure, column in ACCOUNTING:
        facts.append(
            units.filter(pl.col(column).is_not_null()).select(
                result_set_id=pl.lit(result_set),
                unit_id="unit",
                option_id=pl.lit(None, pl.String),
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(measure, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value=pl.col(column),
            )
        )
    unit_of = units.select(*keys, "unit")
    votes = frame.join(unit_of, on=keys, how="left").select(
        result_set_id=pl.lit(result_set),
        unit_id="unit",
        option_id=pl.col("label").replace_strict(options, return_dtype=pl.String),
        channel=pl.lit(0, pl.Int16),
        measure=pl.lit(1, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        flags=pl.lit(0, pl.Int16),
        value=pl.col("votes"),
    )
    p.frame("result_value", pl.concat([*facts, votes]))
    over = units.filter(pl.col("voters") > pl.col("electors")).height
    if negative.height:
        p.accept(
            "accounting.ballots_sum",
            f"In {negative.height} municipalities the file's list votes plus blank ballots exceed its voters, "
            "so no invalid-ballot figure is stored for them; the file's figures are stored as printed.",
        )
    if over:
        p.accept(
            "accounting.turnout",
            f"In {over} units the file prints more voters than registered electors; the figures are stored as printed.",
        )
    return p
