import csv
import io
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "es.rioja"
VERSION = "1"
PUBLISHER = "Gobierno de La Rioja"
TITLE = "Elecciones autonómicas: resultados por municipio y reparto de escaños (datos abiertos de La Rioja)"
PORTAL = "https://web.larioja.org/dato-abierto/datoabierto?n=opd-392"
MUNICIPALITIES = "https://ias1.larioja.org/opendata/download?r=Y2Q9MzczfGNmPTAz"
SEATS = "https://ias1.larioja.org/opendata/download?r=Y2Q9MzkyfGNmPTAz"
LAW = "Ley 3/1991, de 21 de marzo, de Elecciones a la Diputación General de La Rioja"
STATUTE = "Ley Orgánica 3/1982, de 9 de junio, de Estatuto de Autonomía de La Rioja"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ABROAD = 4
THRESHOLD = 0.05
PARSED = DATA / "parsed" / SOURCE
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
}
KEYS = {
    "AP": "ap",
    "PR": "pr",
    "PR+": "pr",
    "PR+E": "pr",
    "I.U.": "iu",
    "IU": "iu",
    "AR.": "ar",
    "IZQ.": "izq",
    "C's": "cs",
    "Cs": "cs",
    "UPYD": "upyd",
    "UPyD": "upyd",
    "P.C.P.E.": "pcpe",
    "PCPE": "pcpe",
    "PUM+J": "pumj",
    "ESCAÑOS EN BLANCO": "eb",
    "EB": "eb",
    "PODEMOS-I.U.": "podemos-iu",
    "PODEMOS-IU-EQUO": "podemos-iu-equo",
}
PARTIES = {
    "psoe": ("Partido Socialista Obrero Español", "Q138198"),
    "pp": ("Partido Popular", "Q185088"),
    "ap": ("Alianza Popular", "Q2838243"),
    "cp": ("Coalición Popular", "Q3393021"),
    "pr": ("Partido Riojano", None),
    "cds": ("Centro Democrático y Social", "Q788345"),
    "pce": ("Partido Comunista de España", "Q478811"),
    "pdp": ("Partido Demócrata Popular", None),
    "pte-uc": ("Partido de los Trabajadores de España-Unidad Comunista", "Q3133165"),
    "iu": ("Izquierda Unida de La Rioja", None),
    "ar": ("Alternativa Riojana", None),
    "verdes": ("Los Verdes de La Rioja", None),
    "mash": ("Movimiento al Socialismo Humanista", None),
    "izq": ("Coalición Electoral IZQ: Izquierda Unida, Iniciativa Ciudadana, Los Verdes", None),
    "upyd": ("Unión Progreso y Democracia", "Q1144342"),
    "ecolo-verde": ("Coalición Verdes de La Rioja", None),
    "pumj": ("Por un Mundo Más Justo", None),
    "pacma": ("Partido Animalista con el Medio Ambiente", "Q82558"),
    "pcpe": ("Partido Comunista de los Pueblos de España", None),
    "podemos": ("Podemos", "Q16059622"),
    "cs": ("Ciudadanos-Partido de la Ciudadanía", "Q1393123"),
    "cr-iu-equo": ("Cambia La Rioja-IU-Equo", None),
    "eb": ("Escaños en Blanco", None),
    "podemos-iu-equo": ("Unidas Podemos Izquierda Unida-Equo", None),
    "vox": ("Vox", "Q15630787"),
    "podemos-iu": ("Podemos-Izquierda Unida", None),
    "plri": ("Por La Rioja", None),
    "vinea": ("Vinea La Rioja", None),
}
SYSTEM = (
    "Closed party lists in a single constituency, the whole of La Rioja. Seats go by the D'Hondt method to the lists "
    "with at least 5% of the valid votes cast in the community, blank ballots counted as valid. The Diputación General "
    "(the Parliament of La Rioja) had 35 members in 1983 and has had 33 since 1987 (" + STATUTE + "; " + LAW + ")."
)
RIOJA_METHOD = (
    "List votes, registered voters, ballots cast, blank ballots, and null ballots for the whole of La Rioja from the "
    "Government of La Rioja's open-data results by municipality (the community row), with the seats each list won "
    "from its open-data seat allocation, whose list votes are the same. Valid ballots here are the votes to "
    "candidacies, with blank ballots stored apart."
)
MUNICIPIO_METHOD = (
    "List votes, registered voters, ballots cast, blank ballots, and null ballots by municipality from the Government "
    "of La Rioja's open-data results, on the INE municipality codes. {abroad}The municipality figures add up to "
    "{votes:,} list votes, against {official:,} in the La Rioja result."
)
ABROAD_ROW = "From 2007 the votes of residents abroad (CERA) are on a separate row. "
DHONDT_DIFFERS = (
    " D'Hondt with a 5% threshold applied to these votes gives a different split from the declared seats, which are "
    "stored as published."
)


def party_key(year: int, siglas: str) -> str:
    if siglas == "AP" and year == 1983:
        return "cp"
    key = KEYS.get(siglas, siglas.lower())
    if key not in PARTIES:
        raise KeyError(f"{year} {siglas!r} has no party")
    return key


def fetch() -> None:
    raw.fetch(SOURCE, MUNICIPALITIES, suffix=".csv")
    raw.fetch(SOURCE, SEATS, suffix=".csv")


def rows(link: str) -> list[dict[str, str]]:
    text = raw.cached(SOURCE, link).path.read_bytes().decode("cp1252")
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def dhondt(votes: dict[str, int], valid: int, seats: int) -> Counter[str]:
    eligible = {key: count for key, count in votes.items() if count >= THRESHOLD * valid}
    quotients = sorted(
        ((count / divisor, count, key) for key, count in eligible.items() for divisor in range(1, seats + 1)),
        reverse=True,
    )
    return Counter(key for *_, key in quotients[:seats])


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    declared: dict[int, list[dict[str, int | str]]] = {}
    for row in rows(SEATS):
        year = int(row["ANIO_ELECTORAL"])
        declared.setdefault(year, []).append(
            {
                "year": year,
                "key": party_key(year, row["SIGLAS_PARTIDO"]),
                "label": " ".join(row["DENOMINACION_PARTIDO"].split()),
                "votes": int(row["NUMERO_VOTOS"]),
                "seats": int(row["NUMERO_ESCANOS"]),
            }
        )
    units: list[dict[str, int | str]] = []
    votes: list[dict[str, int | str]] = []
    table = rows(MUNICIPALITIES)
    fields = list(table[0])
    columns = fields[fields.index("Porc_Escrutado") + 1 :]
    for year, lists in sorted(declared.items()):
        year_rows = [row for row in table if int(row["Anio_Electoral"]) == year]
        (total,) = [row for row in year_rows if row["Codigo_Municipio"] == "0"]
        by_votes = {int(entry["votes"]): str(entry["key"]) for entry in lists}
        if len(by_votes) != len(lists):
            raise ValueError(f"{year}: two lists with the same votes")
        mapping = {column: by_votes[int(total[column])] for column in columns if total[column]}
        if sorted(mapping.values()) != sorted(str(entry["key"]) for entry in lists):
            raise ValueError(f"{year}: the community row's lists differ from the seat allocation")
        seats = sum(int(str(entry["seats"])) for entry in lists)
        if seats != (35 if year == 1983 else 33):
            raise ValueError(f"{year}: {seats} seats")
        valid = int(total["Votos_Validos"])
        won = dhondt({str(e["key"]): int(str(e["votes"])) for e in lists}, valid, seats)
        if any(won[str(e["key"])] != e["seats"] for e in lists):
            print(f"{year}: D'Hondt with a 5% threshold does not give the declared seats")
        sums: Counter[str] = Counter()
        for row in year_rows:
            code = row["Codigo_Municipio"]
            unit = {
                "year": year,
                "mun": "" if code == "0" else f"{int(code):03d}",
                "name": row["Municipio"],
                "electors": int(row["Censo"]),
                "voters": int(row["Votos_Emitidos"]),
                "invalid": int(row["Votos_Nulos"]),
                "blank": int(row["Votos_en_blanco"]),
                "cand": int(row["Votos_Validos"]) - int(row["Votos_en_blanco"]),
            }
            units.append(unit)
            for column, key in mapping.items():
                if row[column]:
                    votes.append({"year": year, "mun": unit["mun"], "key": key, "votes": int(row[column])})
                    if code != "0":
                        sums[key] += int(row[column])
            stray = [column for column in columns if row[column] and column not in mapping]
            if stray:
                raise ValueError(f"{year} {code}: votes in {stray}")
        for entry in lists:
            if sums[str(entry["key"])] != entry["votes"]:
                print(f"{year} {entry['key']}: municipalities {sums[str(entry['key'])]:,} of {entry['votes']:,}")
    for name, frame in {
        "unit": pl.DataFrame(units),
        "votes": pl.DataFrame(votes),
        "seats": pl.DataFrame([entry for lists in declared.values() for entry in lists]),
    }.items():
        frame.write_parquet(PARSED / f"{name}.parquet")
        print(f"{name}: {frame.height} rows")


def emit() -> Path:
    units = pl.read_parquet(PARSED / "unit.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Parliament of La Rioja elections of 1983 to 2023 by municipality, with the seats per list, from the "
                "Government of La Rioja's open data"
            ),
            polity="ES",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        spain, rioja = cs.polity("ES"), cs.polity("ES-RI")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PORTAL,
            license="Reuse of public-sector information (Ley 37/2007), Government of La Rioja open data",
            license_url=None,
            attribution="Fuente: Gobierno de La Rioja, datos abiertos.",
        )
        cs.artifact(raw.cached(SOURCE, MUNICIPALITIES), source)
        cs.artifact(raw.cached(SOURCE, SEATS), source)

        known = dict(
            conn.execute(
                "select e.value, e.entity_id from external_id e join party p on p.id = e.entity_id "
                "where e.scheme = 'wikidata' and e.value = any(%s)",
                ([qid for _, qid in PARTIES.values() if qid],),
            ).fetchall()
        )
        parties: dict[str, UUID] = {}
        for key, (native, qid) in PARTIES.items():
            if qid is not None and qid in known:
                parties[key] = known[qid]
                continue
            parties[key] = cs.key(f"{SOURCE}.party", key)
            cs.add("party", id=parties[key], kind="party", polity_id=rioja)
            name(parties[key], native, "es")

        body = cs.key("es.body", "rioja-parliament")
        cs.add("body", id=body, polity_id=rioja, kind="unicameral")
        name(body, "Parliament of La Rioja")
        name(body, "Parlamento de La Rioja", "es", "native")
        cs.add("body_size", body_id=body, seats=35, valid_from=DAYS[1983], valid_to=DAYS[1987])
        cs.add("body_size", body_id=body, seats=33, valid_from=DAYS[1987], valid_to=None)
        system = cs.key("es.electoral_system", "rioja-parliament")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=rioja,
            rules={
                "formula": "dhondt",
                "lists": {"order": "closed", "vacancy": "next_on_list"},
                "threshold": {"level": "list", "scope": "contest", "percent": THRESHOLD * 100},
            },
            rules_version=1,
        )
        name(system, "Parliament of La Rioja closed-list D'Hondt system")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        municipality_areas = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'es.municipio'"
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[UUID, str, str, UUID | None, str, UUID | None, str]] = {}
        province = cs.unit("es.provincia", "26")
        unit_rows[province] = (
            province,
            "es.provincia",
            "26",
            None,
            "equals",
            cs.area("es.provincia", "26"),
            "standard",
        )
        municipality_units: dict[str, UUID] = {}

        def unit_of(mun: str) -> UUID:
            if not mun:
                return province
            code = "26" + mun
            if code not in municipality_units:
                unit = municipality_units[code] = cs.unit("es.municipio", code)
                if mun == "200":
                    area, relation, kind = cs.area("es.provincia", "26"), "within", "aggregate"
                else:
                    area = municipality_areas.get(code)
                    relation, kind = ("equals" if area is not None else "unlocated"), "standard"
                unit_rows[unit] = (unit, "es.municipio", code, province, relation, area, kind)
            return municipality_units[code]

        values: list[tuple[str, str, str | None, int, int, int]] = []

        for year, day in DAYS.items():
            election = cs.key("es.election", f"rioja:{day}")
            cs.add("election", id=election, polity_id=rioja, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} La Rioja regional election")
            name(election, f"Elecciones al Parlamento de La Rioja de {year}", "es", "native")
            path = f"es/ri/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            lists = seats.filter(pl.col("year") == year).sort("votes", descending=True)
            magnitude = int(lists["seats"].sum())
            contest = cs.key(f"{SOURCE}.contest", str(day))
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=cs.area("es.provincia", "26"),
                magnitude=magnitude,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, "La Rioja")
            cs.add("slug", path=f"{path}/la-rioja", entity_id=contest)
            options: dict[str, UUID] = {}
            for entry in lists.iter_rows(named=True):
                option = options[entry["key"]] = cs.key(f"{SOURCE}.option", f"{day}:{entry['key']}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=parties[entry["key"]],
                    ballot_label=entry["label"],
                )
            year_units = units.filter(pl.col("year") == year)
            year_votes = votes.filter(pl.col("year") == year)
            official = int(lists["votes"].sum())
            got = int(year_votes.filter(pl.col("mun") != "")["votes"].sum())
            total = year_units.filter(pl.col("mun") == "").row(0, named=True)
            note = RIOJA_METHOD
            won = dhondt(dict(zip(lists["key"], lists["votes"])), total["cand"] + total["blank"], magnitude)
            if any(won[key] != count for key, count in zip(lists["key"], lists["seats"])):
                note += DHONDT_DIFFERS
            for label, default, completeness, body_md, level in (
                ("rioja", True, 1.0, note, "community"),
                (
                    "municipio",
                    False,
                    min(got / official, 1),
                    MUNICIPIO_METHOD.format(abroad=ABROAD_ROW if year >= 2007 else "", votes=got, official=official),
                    "municipality",
                ),
            ):
                result_set = cs.key(f"{SOURCE}.result_set", f"{day}:{label}")
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
                cs.add("note", entity_id=result_set, kind="methodology", body_md=body_md)
                keep = (pl.col("mun") == "") if level == "community" else (pl.col("mun") != "")
                for row in year_units.filter(keep).iter_rows(named=True):
                    unit, channel = unit_of(row["mun"]), ABROAD if row["mun"] == "200" else 0
                    for measure, field in (
                        (REGISTERED, "electors"),
                        (BALLOTS, "voters"),
                        (VALID, "cand"),
                        (INVALID, "invalid"),
                        (BLANK, "blank"),
                    ):
                        values.append((str(result_set), str(unit), None, measure, row[field], channel))
                for row in year_votes.filter(keep).iter_rows(named=True):
                    values.append(
                        (
                            str(result_set),
                            str(unit_of(row["mun"])),
                            str(options[row["key"]]),
                            VOTES,
                            row["votes"],
                            ABROAD if row["mun"] == "200" else 0,
                        )
                    )
                if default:
                    for entry in lists.iter_rows(named=True):
                        if entry["seats"]:
                            cs.add(
                                "outcome",
                                result_set_id=result_set,
                                option_id=options[entry["key"]],
                                kind="won",
                                seats=entry["seats"],
                            )

        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
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
                ],
                schema_overrides={"parent_id": pl.String, "area_id": pl.String},
            ),
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
