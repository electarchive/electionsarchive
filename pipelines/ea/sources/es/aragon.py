import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "es.aragon"
VERSION = "2"
PUBLISHER = "Gobierno de Aragón"
TITLE = "Elecciones a Cortes de Aragón (Open Data Aragón)"
PORTAL = "https://opendata.aragon.es/"
LAW = "Ley 2/1987, de 4 de febrero, Electoral de la Comunidad Autónoma de Aragón"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
FIRST_ELECTION_NOTE = (
    "The first Cortes were elected under the Statute of Autonomy and, for what it left open, the rules for the "
    "Congreso de los Diputados (Decree of the Diputación General de Aragón of 9 March 1983, BOE of 10 March 1983): the "
    "province was the constituency, with 18 deputies for Huesca, 16 for Teruel, and 32 for Zaragoza."
)
PARSED = DATA / "parsed" / SOURCE
PROVINCES = {"22": "Huesca", "44": "Teruel", "50": "Zaragoza"}
THRESHOLD = 0.03
DAYS = {
    1983: date(1983, 5, 8),
    1987: date(1987, 6, 10),
    1991: date(1991, 5, 26),
    1995: date(1995, 5, 28),
    1999: date(1999, 6, 13),
    2003: date(2003, 5, 25),
    2007: date(2007, 5, 27),
    2011: date(2011, 5, 22),
    2015: date(2015, 5, 24),
    2019: date(2019, 5, 26),
    2023: date(2023, 5, 28),
    2026: date(2026, 2, 8),
}
CATALOG = "https://opendata.aragon.es/aod/api/3/action/package_show?id="
DOWNLOAD = "https://opendata.aragon.es/GA_OD_Core/download"
YEARS = (1983, 1987, 1991, 1995, 1999, 2003, 2007, 2011, 2015, 2019, 2023, 2026)
MESA_YEARS = (1995, 1999, 2003, 2007, 2019, 2023, 2026)
MESA_TURNOUT_YEARS = (2003, 2007, 2019, 2023, 2026)
RESOURCES = {
    "community_turnout": 372,
    "community_lists": 373,
    "province_turnout": 374,
    "province_lists": 375,
    "municipality_turnout": 378,
    "municipality_lists": 379,
    "mesa_turnout": 380,
    "mesa_lists": 381,
}
BOA_PUBLISHER = "Junta Electoral de Aragón"
BOA_TITLE = "Actas de proclamación y relaciones de diputados electos a Cortes de Aragón (Boletín Oficial de Aragón)"
BOA_PORTAL = "https://www.boa.aragon.es/"
BOA_PDF = "https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&MLKOB="
BOA = {
    "000001042": ("499147542524", "BOA 17, 7 June 1983"),
    "000001057": ("504282293028", "BOA 18, 9 July 1983"),
    "000005323": ("490959172929", "BOA 92, 5 August 1987"),
    "000005324": ("490960183736", "BOA 92, 5 August 1987"),
    "000005325": ("490961194644", "BOA 92, 5 August 1987"),
    "000013740": ("489831992424", "BOA 80, 1 July 1991"),
    "000029083": ("454569373625", "BOA 74, 21 June 1995"),
    "000050617": ("3222941715", "BOA 83, 2 July 1999"),
    "000080079": ("12268514805", "BOA 74, 20 June 2003"),
    "000118977": ("204935031212", "BOA 72, 18 June 2007"),
    "000160297": ("601897850404", "BOA 109, 6 June 2011"),
    "000197111": ("859084765555", "BOA 108, 9 June 2015"),
    "000228911": ("1076905222828", "BOA 112, 12 June 2019"),
    "007933923": ("1282779080505", "BOA 115, 19 June 2023"),
    "007956299": ("1437358090606", "BOA 38, 25 February 2026"),
}
DEPUTIES = Path(__file__).with_name("aragon_deputies.tsv")
DATASETS = (
    "elecciones-a-cortes-de-aragon-ano-2026-detalle-distrito-mesa-electoral",
    "elecciones-a-cortes-de-aragon-ano-2023-detalle-distrito-mesa-electoral",
    "elecciones-a-cortes-de-aragon-anyo-2019-detalle-distrito-seccion",
    "elecciones-a-cortes-de-aragon-ano-2015-detalle-mesa-electoral",
    "elecciones-a-cortes-de-aragon-ano-2011-detalle-mesa-electoral",
)


def url(kind: str, year: int) -> str:
    filters = quote(f'{{"cod_elec":"CA{year}"}}', safe="")
    return f"{DOWNLOAD}?resource_id={RESOURCES[kind]}&formato=csv&filters={filters}"


def wanted() -> list[tuple[str, int]]:
    return [
        (kind, year)
        for year in YEARS
        for kind in RESOURCES
        if (kind != "mesa_lists" or year in MESA_YEARS) and (kind != "mesa_turnout" or year in MESA_TURNOUT_YEARS)
    ]


def fetch() -> None:
    for kind, year in wanted():
        raw.fetch(SOURCE, url(kind, year), suffix=".csv")
    for name in DATASETS:
        raw.fetch(SOURCE, CATALOG + name, suffix=".json")
    for mlkob, _ in BOA.values():
        raw.fetch(SOURCE, BOA_PDF + mlkob, suffix=".pdf")


def repair(text: str) -> str:
    if "Ã" not in text and "Â" not in text:
        return text
    try:
        return bytes(
            ord(char) if ord(char) < 256 and char.encode("cp1252", "ignore") == b"" else char.encode("cp1252")[0]
            for char in text
        ).decode("utf-8")
    except UnicodeDecodeError, UnicodeEncodeError:
        return text


def read(kind: str, year: int) -> pl.DataFrame:
    frame = pl.read_csv(raw.cached(SOURCE, url(kind, year)).path, infer_schema_length=0)
    names = [name for name in frame.columns if name.startswith("nombre_") and name != "nombre_elec"]
    return frame.with_columns(pl.col(name).map_elements(repair, return_dtype=pl.String) for name in names)


def integers(frame: pl.DataFrame, *columns: str) -> pl.DataFrame:
    return frame.with_columns(pl.col(column).cast(pl.Int64) for column in columns)


def dhondt(votes: dict[str, int], valid: int, seats: int) -> Counter[str]:
    eligible = {code: count for code, count in votes.items() if count >= THRESHOLD * valid}
    quotients = sorted(
        ((count / divisor, count, code) for code, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(code for *_, code in quotients[:seats])


def province_tables(year: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    totals = integers(
        read("province_turnout", year),
        "mesas_p",
        "electores_p",
        "votantes_p",
        "nulos_p",
        "blancos_p",
        "validos_p",
        "candidatura_p",
    ).select(
        year=pl.lit(year, pl.Int32),
        province="cod_provincia",
        mesas="mesas_p",
        electors="electores_p",
        voters="votantes_p",
        invalid="nulos_p",
        blank="blancos_p",
        valid="validos_p",
        candidacies="candidatura_p",
    )
    lists = integers(read("province_lists", year), "votos_p", "electos_p").select(
        year=pl.lit(year, pl.Int32),
        province="cod_provincia",
        party="cod_par",
        name="nombre_par",
        votes="votos_p",
        seats="electos_p",
    )
    seats = lists.group_by("province").agg(seats=pl.col("seats").sum())
    problems = []
    for row in totals.join(seats, on="province").iter_rows(named=True):
        part = lists.filter(pl.col("province") == row["province"])
        if part["votes"].sum() != row["candidacies"]:
            print(
                f"{year} {row['province']}: list votes {part['votes'].sum():,} against {row['candidacies']:,} to candidacies"
            )
        if row["candidacies"] + row["blank"] != row["valid"] or row["valid"] + row["invalid"] != row["voters"]:
            problems.append(f"{row['province']}: turnout lines do not add up")
        won = dhondt(dict(zip(part["party"], part["votes"])), row["valid"], row["seats"])
        if any(won[party] != declared for party, declared in zip(part["party"], part["seats"])):
            print(f"{year} {row['province']}: D'Hondt with a 3% threshold does not give the declared seats")
    if problems:
        raise ValueError(f"{year}: " + "; ".join(problems))
    return totals.join(seats, on="province"), lists


def municipality_tables(year: int, lists: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    totals = integers(
        read("municipality_turnout", year),
        "electores_m",
        "votantes_m",
        "nulos_m",
        "blancos_m",
        "validos_m",
        "candidatura_m",
    ).select(
        year=pl.lit(year, pl.Int32),
        municipality="cod_municipio",
        name="nombre_m",
        province=pl.col("cod_municipio").str.slice(0, 2),
        electors="electores_m",
        voters="votantes_m",
        invalid="nulos_m",
        blank="blancos_m",
        valid="validos_m",
        candidacies="candidatura_m",
    )
    votes = integers(read("municipality_lists", year), "votos_m").select(
        year=pl.lit(year, pl.Int32),
        municipality="cod_municipio",
        province=pl.col("cod_municipio").str.slice(0, 2),
        party="cod_par",
        votes="votos_m",
    )
    ran = lists.select("province", "party")
    return totals, votes.join(ran, on=["province", "party"])


def mesa_tables(year: int, lists: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    clean = lambda frame: frame.with_columns(
        mesa=pl.col("cod_mesa").str.replace_all(" ", ""),
        province=pl.col("cod_municipio").str.slice(0, 2),
    ).with_columns(district=pl.col("mesa").str.slice(5, 2))
    votes = clean(integers(read("mesa_lists", year), "votos_e")).select(
        year=pl.lit(year, pl.Int32),
        mesa="mesa",
        municipality="cod_municipio",
        district="district",
        province="province",
        party="cod_par",
        votes="votos_e",
    )
    units = votes.select("year", "mesa", "municipality", "district", "province").unique()
    if year in MESA_TURNOUT_YEARS:
        accounting = clean(
            integers(
                read("mesa_turnout", year),
                "votantes_e",
                "nulos_e",
                "blancos_e",
                "candidatura_e",
                "electores_e",
                "validos_e",
            )
        ).select(
            "mesa",
            electors="electores_e",
            voters="votantes_e",
            invalid="nulos_e",
            blank="blancos_e",
            valid="validos_e",
            candidacies="candidatura_e",
        )
        units = units.join(accounting, on="mesa", how="left")
    else:
        units = units.with_columns(
            electors=pl.lit(None, pl.Int64),
            voters=pl.lit(None, pl.Int64),
            invalid=pl.lit(None, pl.Int64),
            blank=pl.lit(None, pl.Int64),
            valid=pl.lit(None, pl.Int64),
            candidacies=pl.lit(None, pl.Int64),
        )
    ran = lists.select("province", "party")
    return units, votes.join(ran, on=["province", "party"])


PARTICLES = {"de", "del", "la", "las", "los", "y", "i", "e"}


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(char for char in plain.lower() if char.isalnum())


def display(text: str) -> str:
    if text != text.upper():
        return text
    words = text.lower().split()
    return " ".join(
        word
        if index and word in PARTICLES
        else re.sub(r"(^|[-(])(\w)", lambda match: match[1] + match[2].upper(), word)
        for index, word in enumerate(words)
    )


def deputies(lists: pl.DataFrame) -> pl.DataFrame:
    frame = pl.read_csv(DEPUTIES, separator="\t", schema_overrides={"province": pl.String, "party": pl.String})
    frame = frame.with_columns(pl.col("docn").cast(pl.String).str.zfill(9))
    unknown = set(frame["docn"]) - set(BOA)
    if unknown:
        raise ValueError(f"deputies cite unknown BOA documents {sorted(unknown)}")
    counted = frame.group_by("year", "province", "party").agg(members=pl.len())
    check = (
        lists.filter(pl.col("seats") > 0)
        .join(counted, on=["year", "province", "party"], how="full", coalesce=True)
        .filter(pl.col("seats").fill_null(0) != pl.col("members").fill_null(0))
    )
    if not check.is_empty():
        raise ValueError(f"elected deputies differ from declared seats:\n{check}")
    return frame.with_columns(position=pl.int_range(1, pl.len() + 1).over("year", "province", "party"))


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    tables: dict[str, list[pl.DataFrame]] = defaultdict(list)
    for year in YEARS:
        provinces, lists = province_tables(year)
        municipalities, municipality_votes = municipality_tables(year, lists)
        pieces = {
            "provinces": provinces,
            "lists": lists,
            "municipalities": municipalities,
            "municipality_votes": municipality_votes,
        }
        if year in MESA_YEARS:
            pieces["mesas"], pieces["mesa_votes"] = mesa_tables(year, lists)
        for name, frame in pieces.items():
            tables[name].append(frame)
        for level in ("municipality", "mesa"):
            if f"{level}_votes" not in pieces:
                continue
            got = pieces[f"{level}_votes"].group_by("province", "party").agg(got=pl.col("votes").sum())
            gap = lists.join(got, on=["province", "party"], how="left").select(
                pl.col("votes").sum().alias("official"), pl.col("got").sum().alias("sum")
            )
            print(f"{year} {level}: list votes {gap['sum'][0]:,} of {gap['official'][0]:,}")
    tables["deputies"].append(deputies(pl.concat(tables["lists"])))
    for name, frames in tables.items():
        frame = pl.concat(frames)
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")


SYSTEM = (
    "Closed party lists in three provincial constituencies, Huesca, Teruel, and Zaragoza. Seats are shared by the "
    "D'Hondt method among the lists that win at least 3% of the valid votes in the province, and blank ballots count "
    "as valid votes. Each province's number of seats is the number the Government of Aragón publishes with the "
    "results. The Cortes have had 66 seats in 1983 and 67 since 1987 (" + LAW + ")."
)
PROVINCE_METHOD = (
    "List votes, seats, registered voters, ballots cast, blank ballots, and null ballots by province from the "
    "Government of Aragón's open data. Valid ballots here are the votes to candidacies; blank ballots, which Spanish "
    "electoral law counts as valid, are stored apart. The seats are the numbers the file declares for each list."
)
MUNICIPALITY_METHOD = (
    "List votes, registered voters, ballots cast, blank ballots, and null ballots by municipality from the Government "
    "of Aragón's open data. Valid ballots are the votes to candidacies, with blank ballots stored apart. The file lists a list's votes only in provinces where it stood. The municipal figures add "
    "up to {votes:,} list votes, against {official:,} in the province results, and the files give no unit for the "
    "difference."
)
MESA_METHOD = (
    "List votes by polling station (mesa) from the Government of Aragón's open data{accounting}, with each mesa on "
    "its census section or its municipality. The mesa figures add up to {votes:,} list votes, against {official:,} in "
    "the province results, and the files give no unit for the difference."
)
MESA_ACCOUNTING = ", with registered voters, ballots cast, blank ballots, and null ballots"
MESA_VOTES_ONLY = ". This year's file holds list votes only, with no registered voters, blank ballots, or null ballots"
MEMBERS_NOTE = (
    " The elected deputies are those the Juntas Electorales Provinciales proclaimed, as printed in {bulletin}, in "
    "the order printed there."
)
LISTS_DIFFER = (
    " In the source, the list votes of this province add up to {lists:,}, against {candidacies:,} votes to "
    "candidacies in its own totals."
)
DHONDT_DIFFERS = (
    " D'Hondt with a 3% threshold applied to the votes printed here gives a different split from the declared "
    "seats, which are stored as published."
)


REWRITTEN = "The methodology note was rewritten."
OVER = " In {count} units the ballots cast exceed the registered voters, as the file prints them."


def over(frame: pl.DataFrame) -> str:
    count = frame.filter(pl.col("voters") > pl.col("electors")).height
    return OVER.format(count=count) if count else ""


def clean(text: str) -> str:
    return " ".join(text.split())


def emit() -> Path:
    provinces = pl.read_parquet(PARSED / "provinces.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    municipalities = pl.read_parquet(PARSED / "municipalities.parquet")
    municipality_votes = pl.read_parquet(PARSED / "municipality_votes.parquet")
    mesas = pl.read_parquet(PARSED / "mesas.parquet")
    mesa_votes = pl.read_parquet(PARSED / "mesa_votes.parquet")
    members = pl.read_parquet(PARSED / "deputies.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Cortes de Aragón elections of 1983 to 2026 by province, municipality, and, for 1995, 1999, 2003, "
                "2007, 2019, 2023, and 2026, polling station, from the Government of Aragón's open data, with the deputies "
                "proclaimed elected from the Boletín Oficial de Aragón"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, aragon = cs.polity("ES"), cs.polity("ES-AR")
        for scheme in ("es.provincia", "es.municipio", "es.seccion", "es.mesa"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish es.ine and es.referendums before the Cortes de Aragón")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PORTAL,
            license="Creative Commons Attribution 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution="Fuente: Gobierno de Aragón, Open Data Aragón (CC BY 4.0).",
        )
        for kind, year in wanted():
            if kind.split("_")[0] in ("province", "municipality", "mesa"):
                cs.artifact(raw.cached(SOURCE, url(kind, year)), source)
        boa = cs.source(BOA_PUBLISHER, BOA_TITLE)
        cs.add(
            "source",
            id=boa,
            kind="official_body",
            publisher=BOA_PUBLISHER,
            title=BOA_TITLE,
            url=BOA_PORTAL,
            license=None,
            license_url=None,
            attribution="Fuente: Boletín Oficial de Aragón.",
        )
        for mlkob, _ in BOA.values():
            cs.artifact(raw.cached(SOURCE, BOA_PDF + mlkob), boa)
        people: set[UUID] = set()
        methods: dict[UUID, set[str]] = defaultdict(set)

        parties: dict[str, UUID] = {}
        for code, label in sorted(lists.select("party", "name").unique().iter_rows()):
            parties[code] = cs.key(f"{SOURCE}.party", code)
            cs.add("party", id=parties[code], kind="party", polity_id=aragon)
            name(parties[code], clean(label), "es")

        body = cs.key("es.body", "aragon-cortes")
        cs.add("body", id=body, polity_id=aragon, kind="unicameral")
        name(body, "Cortes of Aragon")
        name(body, "Cortes de Aragón", "es", "native")
        system = cs.key("es.electoral_system", "aragon-cortes")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=aragon,
            rules={
                "formula": "dhondt",
                "lists": {"order": "closed", "vacancy": "next_on_list"},
                "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD * 100},
            },
            rules_version=1,
        )
        name(system, "Cortes de Aragón closed-list D'Hondt system")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        sizes = provinces.group_by("year").agg(seats=pl.col("seats").sum()).sort("year")
        previous: tuple[date, int] | None = None
        for year, seats in sizes.iter_rows():
            if previous is not None and previous[1] != seats:
                cs.add("body_size", body_id=body, seats=previous[1], valid_from=previous[0], valid_to=DAYS[year])
                previous = None
            if previous is None:
                previous = (DAYS[year], seats)
        if previous is not None:
            cs.add("body_size", body_id=body, seats=previous[1], valid_from=previous[0], valid_to=None)

        municipality_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        section_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.seccion'"
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[UUID, str, str, UUID | None, str, UUID | None, str]] = {}
        province_units: dict[str, UUID] = {}
        for code in PROVINCES:
            unit = province_units[code] = cs.unit("es.provincia", code)
            unit_rows[unit] = (unit, "es.provincia", code, None, "equals", cs.area("es.provincia", code), "standard")
        municipality_units: dict[str, UUID] = {}

        def municipality_unit(code: str) -> UUID:
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                if code.endswith("000"):
                    area, relation, kind = cs.area("es.provincia", code[:2]), "within", "aggregate"
                else:
                    area = municipality_areas.get(code)
                    relation, kind = ("equals" if area is not None else "unlocated"), "standard"
                unit_rows[unit] = (unit, "es.municipio", code, province_units[code[:2]], relation, area, kind)
            return municipality_units[code]

        values: list[tuple[str, str, str | None, int, int, int]] = []

        def accounting(result_set: UUID, unit: UUID, row: dict[str, int | None], channel: int = 0) -> None:
            for measure, field in (
                (REGISTERED, "electors"),
                (BALLOTS, "voters"),
                (VALID, "candidacies"),
                (INVALID, "invalid"),
                (BLANK, "blank"),
            ):
                if (value := row[field]) is not None:
                    values.append((str(result_set), str(unit), None, measure, value, channel))

        for year in YEARS:
            day = DAYS[year]
            election = cs.key("es.election", f"aragon:{day}")
            cs.add("election", id=election, polity_id=aragon, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} Aragonese regional election")
            name(election, f"Elecciones a las Cortes de Aragón de {year}", "es", "native")
            path = f"es/ar/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            if year == 1983:
                cs.add("note", entity_id=election, kind="context", body_md=FIRST_ELECTION_NOTE)
            stamp = f"{year}{day.month:02d}"
            year_lists = lists.filter(pl.col("year") == year)
            for province, province_name in PROVINCES.items():
                total = provinces.filter((pl.col("year") == year) & (pl.col("province") == province)).row(0, named=True)
                rows = year_lists.filter(pl.col("province") == province).sort("votes", descending=True)
                key = f"{day}:{province}"
                contest = cs.key(f"{SOURCE}.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=cs.area("es.provincia", province),
                    magnitude=total["seats"],
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, province_name)
                cs.add("slug", path=f"{path}/{slug(province_name)}", entity_id=contest)
                options: dict[str, UUID] = {}
                for entry in rows.iter_rows(named=True):
                    option = options[entry["party"]] = cs.key(f"{SOURCE}.option", f"{key}:{entry['party']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=parties[entry["party"]],
                        ballot_label=clean(entry["name"]),
                    )
                official = int(rows["votes"].sum())
                if int(rows["seats"].sum()) != total["seats"]:
                    raise ValueError(f"{key}: declared seats differ from the province total")

                def make_set(label: str, default: bool, completeness: float, notes: list[str]) -> UUID:
                    result_set = cs.key(f"{SOURCE}.result_set", f"{key}:{label}")
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=default,
                        completeness=completeness,
                    )
                    for body_md in notes:
                        cs.add("note", entity_id=result_set, kind="methodology", body_md=body_md)
                        methods[result_set].add(body_md)
                    return result_set

                note = PROVINCE_METHOD
                if official != total["candidacies"]:
                    note += LISTS_DIFFER.format(lists=official, candidacies=total["candidacies"])
                won = dhondt(dict(zip(rows["party"], rows["votes"])), total["valid"], total["seats"])
                if any(won[party] != seats for party, seats in zip(rows["party"], rows["seats"])):
                    note += DHONDT_DIFFERS
                elected = members.filter((pl.col("year") == year) & (pl.col("province") == province))
                if not elected.is_empty():
                    bulletins = sorted({BOA[docn][1] for docn in elected["docn"]})
                    note += MEMBERS_NOTE.format(bulletin=" and ".join(bulletins))
                province_set = make_set("province", True, 1, [note])
                unit = province_units[province]
                accounting(province_set, unit, total)
                for entry in rows.iter_rows(named=True):
                    values.append(
                        (str(province_set), str(unit), str(options[entry["party"]]), VOTES, entry["votes"], 0)
                    )
                    if entry["seats"]:
                        cs.add(
                            "outcome",
                            result_set_id=province_set,
                            option_id=options[entry["party"]],
                            kind="won",
                            seats=entry["seats"],
                        )
                for member in elected.sort("party", "position").iter_rows(named=True):
                    printed = clean(member["name"])
                    person = cs.key(f"{SOURCE}.person", fold(printed))
                    if person not in people:
                        people.add(person)
                        if person in cs.minted:
                            cs.add("person", id=person)
                            name(person, display(printed), "es")
                    option = cs.key(f"{SOURCE}.option", f"{key}:{member['party']}:{fold(printed)}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=options[member["party"]],
                        party_id=parties[member["party"]],
                        ballot_label=display(printed),
                    )
                    candidacy = cs.key(f"{SOURCE}.candidacy", f"{key}:{member['party']}:{fold(printed)}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="list_member",
                        ballot_name=printed,
                        member_of_party_id=parties[member["party"]],
                        is_independent=False,
                    )
                    cs.add(
                        "citation",
                        entity_id=candidacy,
                        source_id=boa,
                        locator={"boa": BOA[member["docn"]][1], "docn": member["docn"], "page": member["page"]},
                    )
                    cs.add("outcome", result_set_id=province_set, option_id=option, kind="elected")

                places = municipalities.filter((pl.col("year") == year) & (pl.col("province") == province))
                place_votes = municipality_votes.filter((pl.col("year") == year) & (pl.col("province") == province))
                got = int(place_votes["votes"].sum())
                municipality_set = make_set(
                    "municipality",
                    False,
                    min(got / official, 1),
                    [MUNICIPALITY_METHOD.format(votes=got, official=official) + over(places)],
                )
                for row in places.iter_rows(named=True):
                    accounting(
                        municipality_set,
                        municipality_unit(row["municipality"]),
                        row,
                        ABROAD if row["municipality"].endswith("000") else 0,
                    )
                for row in place_votes.iter_rows(named=True):
                    values.append(
                        (
                            str(municipality_set),
                            str(municipality_unit(row["municipality"])),
                            str(options[row["party"]]),
                            VOTES,
                            row["votes"],
                            ABROAD if row["municipality"].endswith("000") else 0,
                        )
                    )

                if year not in MESA_YEARS:
                    continue
                units = mesas.filter((pl.col("year") == year) & (pl.col("province") == province))
                unit_votes = mesa_votes.filter((pl.col("year") == year) & (pl.col("province") == province))
                got = int(unit_votes["votes"].sum())
                mesa_set = make_set(
                    "mesa",
                    False,
                    min(got / official, 1),
                    [
                        MESA_METHOD.format(
                            accounting=MESA_ACCOUNTING if year in MESA_TURNOUT_YEARS else MESA_VOTES_ONLY,
                            votes=got,
                            official=official,
                        )
                        + over(units)
                    ],
                )
                mesa_units: dict[str, tuple[UUID, int]] = {}
                for row in units.iter_rows(named=True):
                    code = f"{stamp}:{row['mesa']}"
                    unit = cs.unit("es.mesa", code)
                    channel = 0
                    if row["municipality"].endswith("000"):
                        parent, area, relation, kind, channel = (
                            province_units[province],
                            cs.area("es.provincia", province),
                            "within",
                            "aggregate",
                            ABROAD,
                        )
                    else:
                        parent = municipality_unit(row["municipality"])
                        section = section_areas.get(row["mesa"][:10]) if year == 2026 else None
                        area = section or municipality_areas.get(row["municipality"])
                        relation, kind = ("within" if area is not None else "unlocated"), "standard"
                    unit_rows[unit] = (unit, "es.mesa", code, parent, relation, area, kind)
                    mesa_units[row["mesa"]] = (unit, channel)
                    accounting(mesa_set, unit, row, channel)
                for row in unit_votes.iter_rows(named=True):
                    unit, channel = mesa_units[row["mesa"]]
                    values.append((str(mesa_set), str(unit), str(options[row["party"]]), VOTES, row["votes"], channel))

        for note_id, entity, body in conn.execute(
            """select n.id, n.entity_id, n.body_md from note n join changeset c on c.id = n.changeset_id
               where c.pipeline = %s and n.kind = 'methodology' and n.entity_id = any(%s)""",
            (SOURCE, list(methods)),
        ).fetchall():
            if body not in methods[entity]:
                cs.add("note_drop", note_id=note_id, entity_id=entity, body_md=body, reason=REWRITTEN)
        minted_units = [
            {
                "id": str(unit),
                "polity_id": str(spain),
                "scheme_code": scheme,
                "code": code,
                "parent_id": None if parent is None else str(parent),
                "area_relation": relation,
                "area_id": None if area is None else str(area),
                "kind": kind,
            }
            for unit, scheme, code, parent, relation, area, kind in unit_rows.values()
            if unit in cs.minted
        ]
        if minted_units:
            cs.frame(
                "reporting_unit",
                pl.DataFrame(minted_units, schema_overrides={"parent_id": pl.String, "area_id": pl.String}),
            )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                    "channel": pl.Int16,
                },
                orient="row",
            ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
