import csv
import io
import re
import zipfile
from datetime import date, timedelta
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "mx.computos"
MEXICO = str(uuid5(package.NAMESPACE, "polity\x1fMX"))
PAGE = "https://www.ine.mx/voto-y-elecciones/resultados-electorales/"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
EARLY, PRISON = 2, 7
CHANNEL = {"A": EARLY, "P": PRISON}
KIND = {"A": "aggregate", "P": "prison"}

# One cut per election, taken when every expected acta was counted: the district counts of the Programa de Cómputos Web.
ELECTIONS = {
    2018: {
        "day": date(2018, 7, 1),
        "url": "https://computos2018.ine.mx/20180708_2130_CW.zip",
        "inner": "20180708_2130_CW_diputaciones.zip",
        "votes": "diputaciones.csv",
        "candidates": "diputaciones_candidaturas_2018.csv",
        "acta": "NUM_ACTA_IMPRESO",
        "state": "ID_ESTADO",
        "district": "ID_DISTRITO",
        "mr": {"6", "7"},
    },
    2021: {
        "day": date(2021, 6, 6),
        "url": "https://computos2021.ine.mx/20210611_1000_CW.zip",
        "inner": "20210611_1000_CW_diputaciones.zip",
        "votes": "diputaciones.csv",
        "candidates": "diputaciones_candidaturas_2021.csv",
        "acta": "NUM_ACTA_IMPRESO",
        "state": "ID_ESTADO",
        "district": "ID_DISTRITO",
        "mr": {"2", "2EMR"},
    },
    2024: {
        "day": date(2024, 6, 2),
        "url": "https://computos2024.ine.mx/20240608_2030_COMPUTOS.zip",
        "inner": "20240608_2030_COMPUTOS_DIP_FED.zip",
        "votes": "DIP_FED_2024.csv",
        "candidates": "DIP_FED_CANDIDATURAS_2024.csv",
        "acta": "TIPO_ACTA",
        "state": "ID_ENTIDAD",
        "district": "ID_DISTRITO_FEDERAL",
        "mr": {"4", "4EMR"},
    },
}
STATES = {
    1: "Aguascalientes",
    2: "Baja California",
    3: "Baja California Sur",
    4: "Campeche",
    5: "Coahuila de Zaragoza",
    6: "Colima",
    7: "Chiapas",
    8: "Chihuahua",
    9: "Ciudad de México",
    10: "Durango",
    11: "Guanajuato",
    12: "Guerrero",
    13: "Hidalgo",
    14: "Jalisco",
    15: "México",
    16: "Michoacán de Ocampo",
    17: "Morelos",
    18: "Nayarit",
    19: "Nuevo León",
    20: "Oaxaca",
    21: "Puebla",
    22: "Querétaro",
    23: "Quintana Roo",
    24: "San Luis Potosí",
    25: "Sinaloa",
    26: "Sonora",
    27: "Tabasco",
    28: "Tamaulipas",
    29: "Tlaxcala",
    30: "Veracruz de Ignacio de la Llave",
    31: "Yucatán",
    32: "Zacatecas",
}
# (key, Spanish name, English name, first year, last year)
PARTIES = {
    "PAN": ("Partido Acción Nacional", "National Action Party"),
    "PRI": ("Partido Revolucionario Institucional", "Institutional Revolutionary Party"),
    "PRD": ("Partido de la Revolución Democrática", "Party of the Democratic Revolution"),
    "PVEM": ("Partido Verde Ecologista de México", "Ecologist Green Party of Mexico"),
    "PT": ("Partido del Trabajo", "Labor Party"),
    "MC": ("Movimiento Ciudadano", "Citizens' Movement"),
    "NA": ("Nueva Alianza", "New Alliance"),
    "MORENA": ("Morena", "Morena"),
    "PES-2018": ("Encuentro Social", "Social Encounter"),
    "PES-2021": ("Encuentro Solidario", "Solidarity Encounter"),
    "RSP": ("Redes Sociales Progresistas", "Progressive Social Networks"),
    "FXM": ("Fuerza por México", "Force for Mexico"),
}
COALITIONS = {
    (2018, "MORENA+PES+PT"): ("Juntos Haremos Historia", "Together We Will Make History"),
    (2018, "MC+PAN+PRD"): ("Por México al Frente", "For Mexico to the Front"),
    (2018, "NA+PRI+PVEM"): ("Todos por México", "Everyone for Mexico"),
    (2021, "MORENA+PT+PVEM"): ("Juntos Hacemos Historia", "Together We Make History"),
    (2021, "PAN+PRD+PRI"): ("Va por México", "Go for Mexico"),
    (2024, "MORENA+PT+PVEM"): ("Sigamos Haciendo Historia", "Let's Keep Making History"),
    (2024, "PAN+PRD+PRI"): ("Fuerza y Corazón por México", "Strength and Heart for Mexico"),
}
ALIASES = {"MOVIMIENTO CIUDADANO": "MC", "NUEVA ALIANZA": "NA", "ENCUENTRO SOCIAL": "PES", "CNR": "CNR"}
INDEPENDENT = re.compile(r"CI|CANDIDATO/A INDEPENDIENTE|CAND_IND_\d+")
NOT_LINES = {
    "CLAVE_CASILLA",
    "CLAVE_ACTA",
    "ID_ESTADO",
    "NOMBRE_ESTADO",
    "ID_ENTIDAD",
    "ENTIDAD",
    "ID_DISTRITO",
    "NOMBRE_DISTRITO",
    "ID_DISTRITO_FEDERAL",
    "DISTRITO_FEDERAL",
    "SECCION",
    "ID_CASILLA",
    "TIPO_CASILLA",
    "EXT_CONTIGUA",
    "CASILLA",
    "NUM_ACTA_IMPRESO",
    "TIPO_ACTA",
}
UNREGISTERED = ("CNR", "CANDIDATO/A NO REGISTRADO/A")
NULLS = ("VN", "VOTOS NULOS")
TOTAL = "TOTAL_VOTOS_CALCULADOS"
ROLL = ("LISTA_NOMINAL", "LISTA_NOMINAL_CASILLA")
TRAILING = {TOTAL, *ROLL, "OBSERVACIONES", "MECANISMOS_TRASLADO", "FECHA_HORA", ""}

METHOD = (
    "Votes by casilla (polling station) from the district counts of the Programa de Cómputos Web, the file INE "
    "publishes with every acta de escrutinio y cómputo once all expected actas are counted. A voter marks one party "
    "or several parties of a coalition on the ballot, and the count keeps each marked combination as its own line: "
    "each line sits under the candidate it counts for, and the candidate's votes are the sum of the lines. Valid "
    "votes are the casilla's total less its null votes, so they include votes for unregistered candidates. Registered "
    "voters are the nominal list of the casilla, which special casillas, serving voters in transit, leave at zero or "
    "below the votes cast. Special casillas also hold a separate acta for the proportional-representation ballot; "
    "those actas belong to the list vote and are not part of this contest."
)
ACCENTS = "The file spells names in capitals and drops or damages some accents; names are kept as the file prints them."

CHAMBER = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fbody\x1fchamber"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


INE_SOURCE = key("source", "ine.computos")
SYSTEM = key("electoral_system", "district")
STATE_UNIT = {code: key("unit", "state", code) for code in STATES}


def packages() -> list[str]:
    return ["mx.computos/registry", *(f"mx.computos/{config['day']}" for config in ELECTIONS.values())]


def fetch() -> None:
    for config in ELECTIONS.values():
        raw.fetch(SOURCE, str(config["url"]), suffix=".zip")


def parse() -> None:
    registry().write()
    for year in ELECTIONS:
        build(year).write()


def emit() -> None:
    package.run("load", packages())


def clean(text: str) -> str:
    return text.strip().strip("='\"").strip()


def read(year: int) -> tuple[dict[str, str], pl.DataFrame, list[dict[str, str]]]:
    config = ELECTIONS[year]
    artifact = raw.cached(SOURCE, str(config["url"]))
    with (
        zipfile.ZipFile(artifact.path) as outer,
        zipfile.ZipFile(io.BytesIO(outer.read(str(config["inner"])))) as inner,
    ):
        names = {name.rsplit("/", 1)[-1]: name for name in inner.namelist()}
        votes = inner.read(names[str(config["votes"])]).decode("latin-1").splitlines()
        candidates = inner.read(names[str(config["candidates"])]).decode("latin-1").splitlines()
    head = next(i for i, line in enumerate(votes) if line.startswith("CLAVE_CASILLA"))
    summary = next(i for i, line in enumerate(votes) if line.startswith("ACTAS_ESPERADAS"))
    printed = dict(zip(votes[summary].split("|"), votes[summary + 1].split("|"), strict=True))
    reader = csv.DictReader(io.StringIO("\n".join(votes[head:])), delimiter="|")
    rows = [{k: clean(v or "") for k, v in row.items() if k is not None} for row in reader]
    start = next(i for i, line in enumerate(candidates) if line.startswith(("ESTADO|", "ID_ENTIDAD|")))
    people = [
        {k: clean(v or "") for k, v in row.items()}
        for row in csv.DictReader(io.StringIO("\n".join(candidates[start:])), delimiter="|")
    ]
    return printed, pl.DataFrame(rows, infer_schema_length=None, schema_overrides={}), people


def number(text: str) -> int:
    return int(text.replace(",", ""))


def label(column: str) -> str:
    column = column.replace("-", "_").strip()
    if column.startswith("FS X M"):
        return "FXM"
    return ALIASES.get(column, column)


def party(abbreviation: str, year: int) -> str:
    return key("party", f"PES-{year}" if abbreviation == "PES" else abbreviation)


def bloc(year: int, members: tuple[str, ...]) -> str:
    return key("party", f"coalition:{year}:{'+'.join(sorted(members))}")


def registry() -> Package:
    p = Package(
        "mx.computos/registry",
        SOURCE,
        "Mexican parties, the Chamber of Deputies, the district electoral system, and the state units, from INE's "
        "district count files",
    )
    p.external.add(MEXICO)
    p.add(
        "source",
        id=INE_SOURCE,
        kind="official_body",
        publisher="Instituto Nacional Electoral",
        title="Cómputos distritales: bases de datos del Programa de Cómputos Web",
        url=PAGE,
        license="Public information INE publishes for download (Ley General de Transparencia, art. 70); no license stated",
        license_url=PAGE,
        attribution="Instituto Nacional Electoral, Programa de Cómputos Web",
    )
    for config in ELECTIONS.values():
        p.artifact(raw.cached(SOURCE, str(config["url"])), INE_SOURCE)
    p.add("scheme", code="mx.computos_entidad", polity_id=MEXICO, kind="administrative", rank=1, noun="state")
    p.add(
        "scheme",
        code="mx.computos_distrito",
        polity_id=MEXICO,
        kind="electoral_district",
        rank=2,
        noun="federal electoral district",
    )
    p.add("scheme", code="mx.computos_casilla", polity_id=MEXICO, kind="precinct", rank=3, noun="casilla")
    for code, name in STATES.items():
        unit = STATE_UNIT[code]
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=MEXICO,
            scheme_code="mx.computos_entidad",
            code=f"{code:02d}",
            area_relation="unlocated",
            area_id=None,
            kind="aggregate",
        )
        p.name(unit, name, "es", "official")
    p.add("body", id=CHAMBER, polity_id=MEXICO, kind="lower_house")
    p.name(CHAMBER, "Chamber of Deputies", "en", "official")
    p.name(CHAMBER, "Cámara de Diputados", "es", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=MEXICO,
        valid_from=date(1996, 1, 1),
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "Each of the 300 federal electoral districts elects one deputy by plurality. The other 200 seats of the "
            "Chamber are filled from regional party lists in five circumscriptions, a separate contest."
        ),
        lang="en",
    )
    for abbreviation, (native, english) in PARTIES.items():
        id = key("party", abbreviation)
        p.add("party", id=id, kind="party", polity_id=MEXICO)
        p.add("external_id", entity_id=id, scheme="mx.ine.party", value=abbreviation)
        p.name(id, native, "es", "official")
        p.name(id, english, "en", "english", False)
        p.name(id, abbreviation.split("-")[0], "es", "abbreviation", False)
    return p


class Entry(NamedTuple):
    name: str
    substitute: str
    lines: list[str]


def candidates(people: list[dict[str, str]]) -> dict[tuple[int, int], list[Entry]]:
    """Group each district's candidacy lines by the candidate they count for, in ballot order."""
    state_key = "ESTADO" if "ESTADO" in people[0] else "ID_ENTIDAD"
    district_key = "DISTRITO" if "DISTRITO" in people[0] else "ID_DISTRITO_FEDERAL"
    districts: dict[tuple[int, int], dict[str, Entry]] = {}
    for row in people:
        where = (int(row[state_key]), int(row[district_key]))
        name = row["CANDIDATURA_PROPIETARIA"]
        found = districts.setdefault(where, {}).setdefault(name, Entry(name, row["CANDIDATURA_SUPLENTE"], []))
        found.lines.append(row["PARTIDO_CI"])
    return {where: list(group.values()) for where, group in districts.items()}


def build(year: int) -> Package:
    config = ELECTIONS[year]
    day = config["day"]
    assert isinstance(day, date)
    p = Package(
        f"mx.computos/{day}",
        SOURCE,
        f"The {year} federal Chamber of Deputies district contests of Mexico, by casilla, from INE's district counts",
        depends=["mx.computos/registry"],
    )
    p.external.add(MEXICO)
    p.uses(raw.cached(SOURCE, str(config["url"])))
    printed, frame, people = read(year)
    columns = frame.columns
    first = max(columns.index(c) for c in NOT_LINES if c in columns) + 1
    stop = min(columns.index(c) for c in (*UNREGISTERED, *NULLS) if c in columns)
    lines = columns[first:stop]
    unregistered = next(c for c in UNREGISTERED if c in columns)
    null = next(c for c in NULLS if c in columns)
    roll = next(c for c in ROLL if c in columns)
    acta, state_column, district_column = str(config["acta"]), str(config["state"]), str(config["district"])
    mr = config["mr"]
    assert isinstance(mr, set)
    left = frame.filter(~pl.col(acta).is_in(sorted(mr)))
    frame = frame.filter(pl.col(acta).is_in(sorted(mr)))
    cast = {
        c: pl.col(c).replace({"-": None, "": None}).cast(pl.Int64) for c in [*lines, unregistered, null, TOTAL, roll]
    }
    frame = frame.with_columns(
        state=pl.col(state_column).cast(pl.Int32),
        district=pl.col(district_column).cast(pl.Int32),
        section=pl.col("SECCION").cast(pl.Int32),
    ).with_columns(**{c: expr for c, expr in cast.items()})
    left = left.with_columns(pl.col(TOTAL).cast(pl.Int64))
    printed_total = number(printed["TOTAL_VOTOS"])
    shown = int(frame[TOTAL].sum() or 0) + int(left[TOTAL].sum() or 0)
    if shown != printed_total or frame.height + left.height != number(printed["ACTAS_ESPERADAS"]):
        raise ValueError(f"{year}: actas add to {shown} votes, the file prints {printed_total}")
    summed = frame.select(
        total=pl.sum_horizontal(*(pl.col(c).fill_null(0) for c in [*lines, unregistered, null])),
        printed=pl.col(TOTAL),
    )
    if (summed["total"] != summed["printed"]).any():
        raise ValueError(f"{year}: {(summed['total'] != summed['printed']).sum()} actas whose lines miss their total")

    election = key("election", year)
    p.add("election", id=election, polity_id=MEXICO, kind="general", date_from=day, date_to=day, status="held")
    path = f"mx/{day}-deputies"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, f"{year} Mexican federal election: Chamber of Deputies districts", "en", "official")
    p.name(election, f"Elección federal de diputaciones de mayoría relativa {year}", "es", "native", False)

    groups = candidates(people)
    known: set[str] = set()

    def people_of(state: int, district: int, name: str) -> str:
        person = key("person", year, state, district, name)
        if person not in known:
            known.add(person)
            p.add("person", id=person)
            p.name(person, name, "es", "official")
        return person

    independent_columns = sorted(c for c in lines if INDEPENDENT.fullmatch(c))
    by_label = {label(c): c for c in lines}
    option_rows: list[dict[str, object]] = []
    map_rows: list[dict[str, object]] = []
    for (state, district), group in sorted(groups.items()):
        contest = key("contest", year, state, district)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=CHAMBER,
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        english = f"{year} Chamber of Deputies election, {STATES[state]} district {district}"
        p.name(contest, english, "en", "official")
        p.name(contest, f"Diputación federal {year}, {STATES[state]}, distrito {district}", "es", "native", False)
        p.add("slug", path=f"{path}/{state:02d}-{district:02d}", entity_id=contest, is_current=True)
        result_set = key("result_set", year, state, district)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=INE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=f"{METHOD} {ACCENTS}",
            lang="en",
        )
        codes = sorted(
            (c for entry in group for c in entry.lines if c.startswith("CI_")),
            key=lambda c: int(c.split("_")[1]),
        )
        taken = dict(zip(codes, independent_columns, strict=False))
        for position, entry in enumerate(group, 1):
            members = [str(c) for c in entry.lines]
            parties = sorted({part for c in members if not c.startswith("CI_") for part in label(c).split("_")})
            head = key("option", year, state, district, entry.name)
            nominator: str | None
            if any(c.startswith("CI_") for c in members):
                nominator = None
            elif len(parties) == 1:
                nominator = party(parties[0], year)
            else:
                nominator = bloc(year, tuple(parties))
            p.add(
                "contest_option",
                id=head,
                contest_id=contest,
                kind="candidate",
                party_id=nominator,
                ballot_position=position,
                ballot_label=entry.name,
            )
            person = people_of(state, district, entry.name)
            p.add(
                "candidacy",
                id=key("candidacy", year, state, district, entry.name, "head"),
                person_id=person,
                option_id=head,
                role="head",
                ballot_name=entry.name,
                is_independent=nominator is None,
            )
            if entry.substitute:
                substitute = people_of(state, district, entry.substitute)
                p.add(
                    "candidacy",
                    id=key("candidacy", year, state, district, entry.name, "substitute"),
                    person_id=substitute,
                    option_id=head,
                    role="substitute",
                    ballot_name=entry.substitute,
                    is_independent=nominator is None,
                )
            for member in members:
                column = taken[member] if member.startswith("CI_") else by_label[label(member)]
                tag = label(column)
                line = key("option", year, state, district, entry.name, tag)
                single = tag in PARTIES or tag == "PES"
                text = "Candidatura independiente" if member.startswith("CI_") else tag.replace("_", "-")
                option_rows.append(
                    {
                        "id": line,
                        "contest_id": contest,
                        "kind": "candidate",
                        "parent_option_id": head,
                        "party_id": party(tag, year) if single else None,
                        "ballot_label": text,
                    }
                )
                map_rows.append({"state": state, "district": district, "column": column, "option_id": line})
        write_in = key("option", year, state, district, "unregistered")
        p.add(
            "contest_option",
            id=write_in,
            contest_id=contest,
            kind="write_in",
            ballot_label="Candidaturas no registradas",
        )
        map_rows.append({"state": state, "district": district, "column": unregistered, "option_id": write_in})
    p.frame("contest_option", pl.DataFrame(option_rows))

    seen: set[str] = set()
    affiliated: set[str] = set()
    for group in groups.values():
        for entry in group:
            members = [str(c) for c in entry.lines]
            if any(c.startswith("CI_") for c in members):
                continue
            parties = sorted({part for c in members for part in label(c).split("_")})
            if len(parties) < 2 or bloc(year, tuple(parties)) in seen:
                continue
            id = bloc(year, tuple(parties))
            seen.add(id)
            named = COALITIONS.get((year, "+".join(parties)))
            fallback = "-".join(parties)
            p.add("party", id=id, kind="bloc", polity_id=MEXICO)
            p.name(id, named[0] if named else f"Coalición {fallback} ({year})", "es", "official")
            p.name(id, named[1] if named else f"{fallback} coalition ({year})", "en", "english", False)
            for member in parties:
                member_id = party(member, year)
                if member_id in affiliated:
                    continue
                affiliated.add(member_id)
                p.add(
                    "party_affiliation",
                    party_id=member_id,
                    parent_party_id=id,
                    kind="coalition",
                    valid_from=day,
                    valid_from_precision="day",
                    valid_to=day + timedelta(days=1),
                    valid_to_precision="day",
                )

    unit_rows: list[dict[str, object]] = []
    district_units: dict[tuple[int, int], str] = {}
    for state, district in sorted(groups):
        unit = district_units[(state, district)] = key("unit", year, "district", state, district)
        unit_rows.append(
            {
                "id": unit,
                "polity_id": MEXICO,
                "scheme_code": "mx.computos_distrito",
                "code": f"{year}:{state:02d}-{district:02d}",
                "parent_id": STATE_UNIT[state],
                "area_relation": "unlocated",
                "kind": "aggregate",
            }
        )
        p.name(unit, f"{STATES[state]}, distrito {district}", "es", "official")
    letters = frame["TIPO_CASILLA"].str.slice(0, 1)
    number_part = pl.col("ID_CASILLA").cast(pl.Int32).cast(pl.String)
    contiguous = pl.col("EXT_CONTIGUA").cast(pl.Int32)
    frame = frame.with_columns(
        letter=letters,
        code=pl.concat_str(
            pl.col("TIPO_CASILLA"),
            number_part,
            pl.when(contiguous > 0).then(pl.concat_str(pl.lit("C"), contiguous.cast(pl.String))).otherwise(pl.lit("")),
        ),
    ).with_columns(
        full=pl.concat_str(
            pl.lit(f"{year}:"),
            pl.col("state").cast(pl.String).str.zfill(2),
            pl.lit("-"),
            pl.col("district").cast(pl.String).str.zfill(2),
            pl.lit("-"),
            pl.col("section").cast(pl.String).str.zfill(4),
            pl.lit("-"),
            pl.col("code"),
        )
    )
    duplicates = frame.group_by("full").len().filter(pl.col("len") > 1)
    if duplicates.height:
        raise ValueError(f"{year}: casillas appear twice: {duplicates.head()}")
    unit_ids = {code: key("unit", year, "casilla", code) for code in frame["full"].to_list()}
    frame = frame.with_columns(unit_id=pl.col("full").replace_strict(unit_ids))
    unit_frame = frame.select(
        id="unit_id",
        polity_id=pl.lit(MEXICO),
        scheme_code=pl.lit("mx.computos_casilla"),
        code="full",
        parent_id=pl.struct("state", "district").map_elements(
            lambda row: district_units[(row["state"], row["district"])], return_dtype=pl.String
        ),
        area_relation=pl.lit("unlocated"),
        kind=pl.col("letter").replace_strict(KIND, default="standard"),
    )
    p.frame("reporting_unit", pl.concat([pl.DataFrame(unit_rows, schema=unit_frame.schema), unit_frame]))
    channel = pl.col("letter").replace_strict(CHANNEL, default=0, return_dtype=pl.Int16)
    districts = frame.select("state", "district").unique()
    missing = districts.join(
        pl.DataFrame(map_rows).select("state", "district").unique(), on=["state", "district"], how="anti"
    )
    if missing.height:
        raise ValueError(f"{year}: districts without candidates: {missing.head()}")
    result_sets = {(s, d): key("result_set", year, s, d) for s, d in groups}
    frame = frame.with_columns(
        result_set_id=pl.struct("state", "district").map_elements(
            lambda row: result_sets[(row["state"], row["district"])], return_dtype=pl.String
        ),
        channel=channel,
    )
    base = ["result_set_id", "unit_id", "channel", "state", "district"]
    accounting = pl.concat(
        [
            frame.filter(pl.col(roll) > 0).select(
                *base, option_id=pl.lit(None, pl.String), measure=pl.lit(REGISTERED), value=roll
            ),
            frame.select(*base, option_id=pl.lit(None, pl.String), measure=pl.lit(BALLOTS), value=TOTAL),
            frame.select(
                *base, option_id=pl.lit(None, pl.String), measure=pl.lit(INVALID), value=pl.col(null).fill_null(0)
            ),
            frame.select(
                *base,
                option_id=pl.lit(None, pl.String),
                measure=pl.lit(VALID),
                value=pl.col(TOTAL) - pl.col(null).fill_null(0),
            ),
        ]
    )
    mapping = pl.DataFrame(
        map_rows, schema={"state": pl.Int32, "district": pl.Int32, "column": pl.String, "option_id": pl.String}
    )
    long = frame.unpivot(on=[*lines, unregistered], index=base, variable_name="column", value_name="value").drop_nulls(
        "value"
    )
    joined = long.join(mapping, on=["state", "district", "column"], how="left")
    stray = joined.filter(pl.col("option_id").is_null() & (pl.col("value") != 0))
    if stray.height:
        raise ValueError(f"{year}: {stray.height} votes on lines with no candidate: {stray.head()}")
    votes = joined.filter(pl.col("option_id").is_not_null()).select(
        *base, "option_id", measure=pl.lit(VOTES), value="value"
    )
    facts = pl.concat([accounting.select(votes.columns), votes]).select(
        "result_set_id",
        "unit_id",
        "option_id",
        channel=pl.col("channel").cast(pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        flags=pl.lit(0, pl.Int16),
        value=pl.col("value").cast(pl.Int64),
    )
    p.frame("result_value", facts)
    print(
        f"{year}: {frame.height:,} casilla actas, {int(frame[TOTAL].sum() or 0):,} votes in the district contests, "
        f"{left.height:,} proportional-representation actas ({int(left[TOTAL].sum() or 0):,} votes) set aside; "
        f"file prints {printed_total:,}",
        flush=True,
    )
    p.accept(
        "accounting.turnout",
        "a few dozen casillas whose acta counts more votes than the casilla's nominal list, as the file prints them: "
        "party representatives and voters in transit may vote where they are not on the list, and the actas are kept "
        "as counted",
    )
    return p
