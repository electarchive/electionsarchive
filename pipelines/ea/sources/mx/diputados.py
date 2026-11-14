import csv
import io
import os
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.mx import ine
from ea.store import DATA, connect

SOURCE = "mx.diputados"
RAW = ine.SOURCE
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

YEARS = (1991, 1994, 1997, 2000, 2003, 2006, 2009, 2012, 2015)
KINDS = ("MR", "RP")
DIFFERS = {
    (2006, "7", "6", "2", "8", "B"),
    (2006, "7", "6", "86", "1238", "E01"),
    (2006, "15", "35", "89", "4452", "E01"),
    (2006, "28", "2", "32", "1119", "C02"),
    (2006, "32", "2", "20", "699", "B"),
    (2012, "5", "1", "2", "10", "C04"),
    (2012, "15", "10", "34", "1306", "B"),
    (2012, "21", "6", "115", "1306", "B"),
}


def siceef_url(kind: str, year: int) -> str:
    return f"{ine.SICEEF}Diputados{kind}{year}.csv"


def fetch() -> None:
    for year in YEARS:
        for kind in KINDS:
            raw.fetch(RAW, siceef_url(kind, year), suffix=".csv")
    for config in ELECTIONS.values():
        raw.fetch(RAW, config["acuerdo"][0], suffix=".pdf")


def read(kind: str, year: int) -> tuple[list[str], list[dict[str, str]]]:
    text = raw.cached(RAW, siceef_url(kind, year)).path.read_bytes().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = [
        {
            "state": row["ID_ESTADO"],
            "district": row["ID_DISTRITO"],
            "municipality": row["ID_MUNICIPIO"],
            "section": row["SECCION"],
            "casilla": row["CASILLA"],
            "circunscripcion": row["CIRCUNSCRIPCION"],
            **row,
        }
        for row in reader
    ]
    columns = list(reader.fieldnames or [])
    return columns[columns.index("CASILLA") + 1 :], rows


def unit_key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(ine.clean(row[k]).lstrip("0") or "0" if k != "casilla" else ine.clean(row[k]) for k in ine.KEYS)


def table(kind: str, year: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    columns, rows = read(kind, year)
    options = [c for c in columns if c not in ine.TRAILING]
    total = "TOTAL_VOTOS"
    roll = "LISTA_NOMINAL" if "LISTA_NOMINAL" in columns else None
    units, votes = [], []
    for row in rows:
        counts = {option: ine.number(row[option]) for option in options}
        key = (year, *unit_key(row))
        if (sum(counts.values()) != ine.number(row[total])) != (key in DIFFERS):
            raise ValueError(f"{year} {kind} {key}: options add to {sum(counts.values())}, total {row[total]}")
        unit = {k: ine.clean(row[k]).lstrip("0") or "0" if k != "casilla" else ine.clean(row[k]) for k in ine.KEYS}
        unit["circunscripcion"] = ine.clean(row["circunscripcion"])
        units.append(
            unit
            | {
                "total": sum(counts.values()),
                "registered": ine.number(row[roll]) if roll else None,
                "status": ine.clean(row.get("ESTATUS_ACTA") or ""),
                "tepjf": ine.clean(row.get("TEPJF") or ""),
            }
        )
        for option, value in counts.items():
            votes.append(
                unit
                | {
                    "option": option,
                    "kind": "unregistered"
                    if option in ine.UNREGISTERED
                    else "invalid"
                    if option in ine.NONVOTES
                    else "list",
                    "votes": value,
                }
            )
    frame = pl.DataFrame(units, schema_overrides={"registered": pl.Int64})
    duplicates = frame.group_by(ine.KEYS).len().filter(pl.col("len") > 1)
    if duplicates.height:
        raise ValueError(f"{year} {kind}: duplicate casillas {duplicates.head()}")
    return frame, pl.DataFrame(votes)


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        for kind in KINDS:
            try:
                units, votes = table(kind, year)
            except ValueError as error:
                print("FAILED", year, kind, str(error)[:300])
                continue
            units.write_parquet(PARSED / f"{kind.lower()}_{year}_casillas.parquet")
            votes.write_parquet(PARSED / f"{kind.lower()}_{year}_votes.parquet")
            national = votes.group_by("option").agg(pl.col("votes").sum()).sort("votes", descending=True)
            print(year, kind, units.height, "casillas", units["total"].sum(), "votes", dict(national.rows()))


EXTRA_PARTIES = {
    "PH": ("Q17624582", "Partido Humanista", "Humanist Party"),
}
PARTIES = ine.PARTIES | EXTRA_PARTIES
INDEPENDENT = "IND"
CPEUM = "Constitución Política de los Estados Unidos Mexicanos"
CPEUM_URL = "https://www.diputados.gob.mx/LeyesBiblio/pdf/CPEUM.pdf"
BODY_SINCE = date(1988, 9, 1)
MR_TITLE = "Cómputos distritales de la elección de Diputados por mayoría relativa, por casilla (1994-2015)"
THRESHOLD = 2

ELECTIONS: dict[int, dict] = {
    2015: {
        "day": date(2015, 6, 7),
        "columns": {
            "PAN": ("PAN",),
            "PRI": ("PRI",),
            "PRD": ("PRD",),
            "PVEM": ("PVEM",),
            "PT": ("PT",),
            "MC": ("MC",),
            "NVA_ALIANZA": ("NA",),
            "MORENA": ("MORENA",),
            "PH": ("PH",),
            "ES": ("PES",),
            "PAN_NVA_ALIANZA": ("PAN", "NA"),
            "PRI_PVEM": ("PRI", "PVEM"),
            "PRD_PT": ("PRD", "PT"),
            "CAND_IND1": (INDEPENDENT,),
            "CAND_IND2": (INDEPENDENT,),
        },
        "acuerdo": (
            "https://repositoriodocumental.ine.mx/xmlui/bitstream/handle/123456789/79670/CGex201508-23_ap_unico.pdf",
            (
                "INE/CG804/2015: Acuerdo del Consejo General del Instituto Nacional Electoral por el que se efectúa "
                "el cómputo total, se declara la validez de la elección de Diputados por el principio de "
                "representación proporcional y se asignan a los partidos políticos nacionales los Diputados que "
                "les corresponden para el periodo 2015-2018"
            ),
            {"votes": 14, "seats": 64},
        ),
        "votes": {
            "PAN": 8377535,
            "PRI": 11636957,
            "PRD": 4335321,
            "PT": 1134101,
            "PVEM": 2757170,
            "MC": 2431063,
            "NA": 1486626,
            "MORENA": 3345712,
            "PH": 856716,
            "PES": 1325032,
            "unregistered": 52371,
            INDEPENDENT: 225029,
            "null": 1900449,
            "total": 39864082,
        },
        "seats": {
            "PAN": (13, 15, 8, 8, 9),
            "PRI": (10, 11, 10, 6, 11),
            "PRD": (2, 3, 6, 8, 8),
            "PVEM": (2, 4, 7, 3, 2),
            "MC": (7, 2, 1, 3, 2),
            "NA": (2, 2, 2, 2, 2),
            "MORENA": (2, 2, 5, 8, 4),
            "PES": (2, 1, 1, 2, 2),
        },
    },
}


CIRCUNSCRIPCIONES = {
    1: "Baja California, Baja California Sur, Chihuahua, Durango, Jalisco, Nayarit, Sinaloa, Sonora",
    2: "Aguascalientes, Coahuila, Guanajuato, Nuevo León, Querétaro, San Luis Potosí, Tamaulipas, Zacatecas",
    3: "Campeche, Chiapas, Oaxaca, Quintana Roo, Tabasco, Veracruz, Yucatán",
    4: "Ciudad de México, Guerrero, Morelos, Puebla, Tlaxcala",
    5: "Colima, Hidalgo, Estado de México, Michoacán",
}
MR_RULES = {"formula": "fptp"}
RP_RULES = {
    "formula": "hare",
    "threshold": {"percent": THRESHOLD, "level": "party", "scope": "national"},
    "lists": {"order": "closed", "vacancy": "next_on_list"},
}
MR_SYSTEM = (
    "Each of the 300 single-member federal districts elects one deputy by plurality of the votes cast, with no "
    "runoff. The Chamber of Deputies has 500 members: these 300, and 200 more elected by proportional "
    "representation from closed regional lists."
)
RP_SYSTEM = (
    "The 200 list seats are filled from closed lists in five regional constituencies (circunscripciones "
    "plurinominales) of 40 seats each. A party takes part in the allocation if it reaches 2 percent of the national "
    "vote (3 percent from 2015) and runs district candidates in at least 200 districts. Seats go by the natural "
    "quotient and largest remainders on the national vote, subject to two limits: no party may hold more than "
    "300 seats from both tiers, and none may hold a share of the Chamber more than 8 points above its share of the "
    "national vote. The model records the formula and the threshold; the two limits are in the declared seats."
)
MR_METHOD = (
    "Polling-station (casilla) counts from INE's district count files, summed to each district. The winner is the "
    "line or coalition candidate with the most votes in the district count, which is the count on which the "
    "district council awards the certificate of majority. Votes marked for two or more parties of one coalition "
    "are counted as that coalition's candidate. The file prints no candidate names."
)
CASILLA_NOTE = (
    "Polling-station results from INE's district count file for the district. Each line is a column of the file: "
    "a party, or a combination of the parties of one coalition that a voter marked."
)
RP_NATIONAL_METHOD = (
    "National vote for the party lists as INE's Consejo General declared it in its acuerdo on the proportional "
    "allocation (page {page} of the document), after the district counts, the rulings on challenges, and the "
    "recomposition ordered by the electoral tribunal. Votes marked for several parties of a coalition are "
    "distributed among them."
)
RP_SEATS_METHOD = (
    "Seats by party as the Consejo General assigned them to this circunscripción, from the table on page {page} of "
    "its acuerdo."
)
REGISTERED, BALLOTS, VALID, INVALID, VOTES = ine.REGISTERED, ine.BALLOTS, ine.VALID, ine.INVALID, ine.VOTES


def groups_of(columns: dict[str, tuple[str, ...]], present: list[str]) -> list[list[str]]:
    parent = {column: column for column in present}

    def find(column: str) -> str:
        while parent[column] != column:
            parent[column] = parent[parent[column]]
            column = parent[column]
        return column

    for column in present:
        members = set(columns[column])
        if len(members) < 2:
            continue
        for other in present:
            if set(columns[other]) <= members:
                parent[find(other)] = find(column)
    result: dict[str, list[str]] = {}
    for column in present:
        result.setdefault(find(column), []).append(column)
    return list(result.values())


def district_frame(
    joined: pl.DataFrame,
    located: pl.DataFrame,
    options: dict[str, str],
    write_in: str,
    casilla_set: str,
    district_unit: str,
    district_set: str,
) -> pl.DataFrame:
    empty = pl.lit(None, pl.String)
    parts = [
        located.filter(pl.col("registered") > 0).select(
            "unit_id", "channel", option_id=empty, measure=pl.lit(REGISTERED), value="registered"
        ),
        located.select("unit_id", "channel", option_id=empty, measure=pl.lit(BALLOTS), value="total"),
        joined.filter(pl.col("kind") == "invalid").select(
            "unit_id", "channel", option_id=empty, measure=pl.lit(INVALID), value="votes"
        ),
        joined.filter(pl.col("kind") != "invalid")
        .group_by("unit_id", "channel")
        .agg(value=pl.col("votes").sum())
        .select("unit_id", "channel", option_id=empty, measure=pl.lit(VALID), value="value"),
        joined.filter((pl.col("kind") != "invalid") & (pl.col("votes") != 0)).select(
            "unit_id",
            "channel",
            option_id=pl.when(pl.col("kind") == "unregistered")
            .then(pl.lit(write_in))
            .otherwise(pl.col("option").replace_strict(options, default=None)),
            measure=pl.lit(VOTES),
            value="votes",
        ),
    ]
    rows = pl.concat(
        [
            part.select(
                unit_id="unit_id",
                option_id="option_id",
                measure=pl.col("measure").cast(pl.Int16),
                value=pl.col("value").cast(pl.Int64),
                channel=pl.col("channel").cast(pl.Int16),
            )
            for part in parts
        ]
    )
    casilla = rows.with_columns(result_set_id=pl.lit(casilla_set))
    district = (
        rows.group_by("option_id", "measure", "channel")
        .agg(value=pl.col("value").sum())
        .with_columns(result_set_id=pl.lit(district_set), unit_id=pl.lit(district_unit))
    )
    return pl.concat([casilla, district.select(casilla.columns)])


def emit() -> Path:
    year = int(os.environ.get("MX_DIPUTADOS", "2015"))
    config = ELECTIONS[year]
    day: date = config["day"]
    columns: dict[str, tuple[str, ...]] = config["columns"]
    url, title, pages = config["acuerdo"]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Mexican Chamber of Deputies {year}: the 300 district contests by polling station from INE's "
            "district counts, and the 200 list seats by circunscripción from the Consejo General's acuerdo",
            polity="MX",
        )
        for scheme in ("mx.entidad", "mx.seccion", "mx.casilla", "mx.distrito_federal"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish mx.mge before mx.diputados")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        mexico = cs.polity("MX")
        ine_source = cs.source(ine.INE, MR_TITLE)
        cs.add(
            "source",
            id=ine_source,
            kind="official_body",
            publisher=ine.INE,
            title=MR_TITLE,
            url=ine.INE_PAGE,
            license=ine.INE_LICENSE,
            license_url=ine.INE_PAGE,
            attribution="Instituto Nacional Electoral, cómputos distritales",
        )
        for kind in KINDS:
            cs.artifact(raw.cached(RAW, siceef_url(kind, year)), ine_source)
        acuerdo = cs.source(ine.INE, title)
        cs.add("source", id=acuerdo, kind="official_body", publisher=ine.INE, title=title, url=url, license=ine.PUBLIC)
        cs.artifact(raw.cached(RAW, url), acuerdo)
        law = cs.source(ine.CAMARA, CPEUM)
        cs.add(
            "source", id=law, kind="official_body", publisher=ine.CAMARA, title=CPEUM, url=CPEUM_URL, license=ine.PUBLIC
        )

        body = cs.key("mx.body", "chamber-of-deputies")
        if body in cs.minted:
            cs.add("body", id=body, polity_id=mexico, kind="lower_house")
            name(body, "Chamber of Deputies of Mexico")
            name(body, "Cámara de Diputados del H. Congreso de la Unión", "es", "native")
            cs.add("body_size", body_id=body, seats=500, valid_from=BODY_SINCE)
            cs.add("citation", entity_id=body, source_id=law, locator={"article": 52})
        mr_system = cs.key("mx.electoral_system", "deputies-mr")
        if mr_system in cs.minted:
            cs.add("electoral_system", id=mr_system, polity_id=mexico, rules=MR_RULES, rules_version=1)
            name(mr_system, "Mexican federal district plurality system")
            cs.add("note", entity_id=mr_system, kind="methodology", body_md=MR_SYSTEM)
        rp_system = cs.key("mx.electoral_system", "deputies-rp")
        if rp_system in cs.minted:
            cs.add("electoral_system", id=rp_system, polity_id=mexico, rules=RP_RULES, rules_version=1)
            name(rp_system, "Mexican federal regional closed-list proportional system")
            cs.add("note", entity_id=rp_system, kind="methodology", body_md=RP_SYSTEM)

        parties: dict[str, UUID] = {}

        def party(abbrev: str) -> UUID:
            if abbrev not in parties:
                item, native, english = PARTIES[abbrev]
                parties[abbrev] = cs.key("mx.party", item)
                if parties[abbrev] in cs.minted:
                    cs.add("party", id=parties[abbrev], kind="party", polity_id=mexico)
                    name(parties[abbrev], native, "es")
                    name(parties[abbrev], english, "en", "english")
                    name(parties[abbrev], abbrev, "es", "abbreviation")
                    cs.add("external_id", entity_id=parties[abbrev], scheme="wikidata", value=item)
            return parties[abbrev]

        election = cs.key("mx.election", f"{day}:general")
        path = f"mx/{day}-general"
        if election in cs.minted:
            cs.add("election", id=election, polity_id=mexico, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} Mexican general election")
            name(election, f"Elecciones federales de {year}", "es", "native")
            cs.add("slug", path=path, entity_id=election)

        states = {
            code: text
            for code, text in conn.execute(
                "select c.code, n.text from area a join area_code c on c.area_id = a.id "
                "join name n on n.entity_id = a.id and n.is_preferred where a.scheme_code = 'mx.entidad'"
            ).fetchall()
        }
        nation_unit = cs.unit("mx.nation", "MX")
        unit_rows: dict[UUID, tuple[str, str, UUID | None, str, UUID, str]] = {
            nation_unit: ("mx.nation", "MX", None, "equals", cs.area("mx.nation", "MX"), "standard")
        }
        state_units: dict[str, UUID] = {}
        for code in range(1, 33):
            state = f"{code:02d}"
            unit = state_units[state] = cs.unit("mx.entidad", state)
            unit_rows[unit] = ("mx.entidad", state, nation_unit, "equals", cs.area("mx.entidad", state), "standard")

        def make_set(contest: UUID, key: str, default: bool, source: UUID, completeness: float, note: str) -> UUID:
            result_set = cs.key("mx.result_set", f"{day}:dip:{key}")
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
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            return result_set

        alliances: dict[str, UUID] = {}

        def alliance_of(members: list[str]) -> UUID:
            key = "-".join(members)
            if key not in alliances:
                alliances[key] = cs.key("mx.alliance", f"{day}:{key}")
                cs.add("alliance", id=alliances[key], kind="electoral_alliance", election_id=election)
                name(alliances[key], "–".join(members) + " coalition")
                name(alliances[key], "Coalición " + "–".join(members), "es", "native")
                for member in members:
                    cs.add("alliance_member", alliance_id=alliances[key], party_id=party(member))
            return alliances[key]

        votes = pl.read_parquet(PARSED / f"mr_{year}_votes.parquet")
        casillas = pl.read_parquet(PARSED / f"mr_{year}_casillas.parquet")
        casillas = casillas.filter((pl.col("casilla") != "") | (pl.col("total") > 0))
        unknown = set(votes.filter(pl.col("kind") == "list")["option"]) - set(columns)
        if unknown:
            raise ValueError(f"{year}: columns without a party: {sorted(unknown)}")

        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'mx.seccion'"
            ).fetchall()
        )
        towns = dict(
            conn.execute(
                "select x.value, x.entity_id from external_id x join area a on a.id = x.entity_id "
                "where x.scheme = 'mx.ine.municipio' and a.scheme_code = 'mx.municipio'"
            ).fetchall()
        )
        placed = []
        for row in casillas.iter_rows(named=True):
            state = f"{int(row['state']):02d}"
            district = int(row["district"]) if row["district"].isdigit() else 0
            section = int(row["section"])
            letter = row["casilla"][:1] or "X"
            code = f"{year}:{state}-{district:02d}-{section:04d}-{ine.casilla_code(year, re.sub(r'^S(?:MR|RP)', 'S', row['casilla']) or 'X')}"
            channel, kind, area = 0, "standard", None
            if letter in "MV" or section == 0:
                channel, kind = ine.ABROAD, "aggregate"
            elif letter == "A":
                channel, kind = ine.EARLY, "aggregate"
            elif letter == "P":
                channel, kind = ine.PRISON, "prison"
            else:
                area = sections.get(f"{state}{section:04d}")
                if area is None and row["municipality"] not in ("", "0"):
                    area = towns.get(f"{state}{int(row['municipality']):03d}")
            unit_id = cs.unit("mx.casilla", code)
            if unit_id in unit_rows:
                raise ValueError(f"{year}: casilla {code} appears twice")
            unit_rows[unit_id] = (
                "mx.casilla",
                code,
                state_units[state],
                "within",
                area or cs.area("mx.entidad", state),
                kind,
            )
            placed.append((*(row[k] for k in ine.KEYS), str(unit_id), channel))
        units = pl.DataFrame(
            placed,
            schema=[*((k, pl.String) for k in ine.KEYS), ("unit_id", pl.String), ("channel", pl.Int16)],
            orient="row",
        )
        located = casillas.join(units, on=ine.KEYS)
        joined = votes.join(units, on=ine.KEYS)
        totals = (
            joined.filter(pl.col("kind") == "list").group_by("state", "district", "option").agg(pl.col("votes").sum())
        )

        frames: list[pl.DataFrame] = []
        winners: dict[str, int] = {}
        decided = 0
        by_district = {key: rows for key, rows in totals.group_by(["state", "district"])}
        for (state, district), rows in sorted(by_district.items(), key=lambda item: (int(item[0][0]), int(item[0][1]))):
            ss, dd = f"{int(state):02d}", int(district)
            contest_key = f"{day}:dip:mr:{ss}{dd:02d}"
            contest = cs.key("mx.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                magnitude=1,
                electoral_system_id=mr_system,
                default_result_kind="counted",
            )
            name(contest, f"{year} Chamber of Deputies, {states[ss]}, district {dd}")
            name(contest, f"Diputación federal {year}, {states[ss]}, distrito {dd}", "es", "native")
            cs.add("slug", path=f"{path}/{slug(states[ss])}-district-{dd}", entity_id=contest)
            totals_by = {r["option"]: r["votes"] for r in rows.iter_rows(named=True) if r["votes"] > 0}
            standing = {tuple(sorted(g)): sum(totals_by[c] for c in g) for g in groups_of(columns, list(totals_by))}
            ordered = sorted(standing.items(), key=lambda item: -item[1])
            if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
                raise ValueError(f"{year} {states[ss]} {dd}: tie for first place")
            district_unit = cs.unit("mx.distrito_federal", f"{year}:{ss}-{dd:02d}")
            unit_rows[district_unit] = (
                "mx.distrito_federal",
                f"{year}:{ss}-{dd:02d}",
                state_units[ss],
                "within",
                cs.area("mx.entidad", ss),
                "aggregate",
            )
            district_set = make_set(contest, f"mr:{ss}{dd:02d}:district", True, ine_source, 1, MR_METHOD)
            casilla_set = make_set(contest, f"mr:{ss}{dd:02d}:casilla", False, ine_source, 1, CASILLA_NOTE)
            options: dict[str, str] = {}
            for position, (group, _) in enumerate(ordered, 1):
                members = sorted({m for c in group for m in columns[c]} - {INDEPENDENT})
                head = cs.key("mx.option", f"{contest_key}:{'+'.join(group)}")
                cs.add(
                    "contest_option",
                    id=head,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party(members[0]) if len(members) == 1 else None,
                    alliance_id=alliance_of(members) if len(members) > 1 else None,
                    ballot_position=position,
                    ballot_label="–".join(members) if members else "Candidatura independiente",
                )
                if position == 1:
                    cs.add("outcome", result_set_id=district_set, option_id=head, kind="elected", seats=1)
                    label = "–".join(members) if members else INDEPENDENT
                    winners[label] = winners.get(label, 0) + 1
                for column in group:
                    if len(group) == 1:
                        options[column] = str(head)
                        continue
                    line = cs.key("mx.option", f"{contest_key}:{'+'.join(group)}:{column}")
                    mine = [m for m in columns[column] if m != INDEPENDENT]
                    cs.add(
                        "contest_option",
                        id=line,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=head,
                        party_id=party(mine[0]) if len(mine) == 1 else None,
                        alliance_id=alliance_of(mine) if len(mine) > 1 else None,
                        ballot_label="–".join(mine),
                    )
                    options[column] = str(line)
            write_in = cs.key("mx.option", f"{contest_key}:unregistered")
            cs.add(
                "contest_option",
                id=write_in,
                contest_id=contest,
                kind="write_in",
                ballot_label="Candidatos no registrados",
            )
            mine = joined.filter((pl.col("state") == state) & (pl.col("district") == district))
            mine_units = located.filter((pl.col("state") == state) & (pl.col("district") == district))
            frames.append(
                district_frame(
                    mine, mine_units, options, str(write_in), str(casilla_set), str(district_unit), str(district_set)
                )
            )
            decided += 1
        print(year, "district winners", dict(sorted(winners.items(), key=lambda item: -item[1])), decided)

        national = cs.key("mx.contest", f"{day}:dip:rp")
        cs.add(
            "contest",
            id=national,
            election_id=election,
            kind="list_allocation",
            body_id=body,
            magnitude=200,
            electoral_system_id=rp_system,
            default_result_kind="counted",
        )
        name(national, f"{year} Chamber of Deputies, proportional representation lists")
        name(national, f"Diputaciones federales {year}, representación proporcional", "es", "native")
        cs.add("slug", path=f"{path}/proportional", entity_id=national)
        declared = config["votes"]
        unregistered, null, total = declared["unregistered"], declared["null"], declared["total"]
        listed = {
            abbrev: count for abbrev, count in declared.items() if abbrev not in ("unregistered", "null", "total")
        }
        if sum(listed.values()) + unregistered + null != total:
            raise ValueError(f"{year}: the declared figures do not add to {total}")
        rp_options: dict[str, UUID] = {}
        for position, abbrev in enumerate(sorted(listed, key=lambda a: -listed[a]), 1):
            rp_options[abbrev] = cs.key("mx.option", f"{day}:dip:rp:{abbrev}")
            cs.add(
                "contest_option",
                id=rp_options[abbrev],
                contest_id=national,
                kind="list",
                party_id=party(abbrev) if abbrev != INDEPENDENT else None,
                ballot_position=position,
                ballot_label=abbrev if abbrev != INDEPENDENT else "Candidaturas independientes",
            )
        rp_write_in = cs.key("mx.option", f"{day}:dip:rp:unregistered")
        cs.add(
            "contest_option",
            id=rp_write_in,
            contest_id=national,
            kind="write_in",
            ballot_label="Candidatos no registrados",
        )
        national_set = make_set(
            national, "rp:national", True, acuerdo, 1, RP_NATIONAL_METHOD.format(page=pages["votes"])
        )
        cs.add("citation", entity_id=national_set, source_id=acuerdo, locator={"page": pages["votes"]})
        rows: list[tuple[str, str, str | None, int, int, int]] = []
        unit = str(nation_unit)
        for abbrev, count in listed.items():
            rows.append((str(national_set), unit, str(rp_options[abbrev]), VOTES, count, 0))
        rows.append((str(national_set), unit, str(rp_write_in), VOTES, unregistered, 0))
        for measure, value in ((BALLOTS, total), (VALID, total - null), (INVALID, null)):
            rows.append((str(national_set), unit, None, measure, value, 0))
        seat_total = 0
        for number in range(1, 6):
            contest = cs.key("mx.contest", f"{day}:dip:rp:{number}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                magnitude=40,
                electoral_system_id=rp_system,
                allocation_parent_id=national,
                default_result_kind="counted",
            )
            name(contest, f"{year} Chamber of Deputies, proportional representation, circunscripción {number}")
            name(contest, f"Diputaciones federales {year}, circunscripción {number}", "es", "native")
            cs.add("slug", path=f"{path}/proportional-{number}", entity_id=contest)
            cs.add("note", entity_id=contest, kind="methodology", body_md=f"States: {CIRCUNSCRIPCIONES[number]}.")
            seat_set = make_set(contest, f"rp:{number}", True, acuerdo, 1, RP_SEATS_METHOD.format(page=pages["seats"]))
            cs.add("citation", entity_id=seat_set, source_id=acuerdo, locator={"page": pages["seats"]})
            circ_options: dict[str, UUID] = {}
            for position, abbrev in enumerate(listed, 1):
                circ_options[abbrev] = cs.key("mx.option", f"{day}:dip:rp:{number}:{abbrev}")
                cs.add(
                    "contest_option",
                    id=circ_options[abbrev],
                    contest_id=contest,
                    kind="list",
                    party_id=party(abbrev) if abbrev != INDEPENDENT else None,
                    ballot_position=position,
                    ballot_label=abbrev,
                )
            won = 0
            for abbrev, per in config["seats"].items():
                if per[number - 1]:
                    won += per[number - 1]
                    cs.add(
                        "outcome",
                        result_set_id=seat_set,
                        option_id=circ_options[abbrev],
                        kind="won",
                        seats=per[number - 1],
                    )
            if won != 40:
                raise ValueError(f"{year}: circunscripción {number} seats add to {won}, not 40")
            seat_total += won
        print(year, "district winners", dict(sorted(winners.items(), key=lambda item: -item[1])), decided, seat_total)

        minted = [
            {
                "id": str(unit_id),
                "polity_id": str(mexico),
                "scheme_code": scheme,
                "code": code,
                "parent_id": None if parent is None else str(parent),
                "area_relation": relation,
                "area_id": str(area),
                "kind": kind,
            }
            for unit_id, (scheme, code, parent, relation, area, kind) in unit_rows.items()
            if unit_id in cs.minted
        ]
        cs.frame("reporting_unit", pl.DataFrame(minted, schema_overrides={"parent_id": pl.String}))
        national_values = pl.DataFrame(
            rows,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
                "channel": pl.Int16,
            },
            orient="row",
        )
        cs.frame(
            "result_value",
            pl.concat([national_values, *(frame.select(national_values.columns) for frame in frames)]).with_columns(
                count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)
            ),
        )
        return cs.write()
