import csv
import io
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.mx import ine
from ea.sources.mx.ine import ABROAD, BALLOTS, EARLY, INVALID, PRISON, REGISTERED, VALID, VOTES
from ea.store import DATA, connect

SOURCE = "mx.gubernatura"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

IEPAC = "Instituto Electoral y de Participación Ciudadana de Yucatán (IEPAC)"
IEEM = "Instituto Electoral del Estado de México (IEEM)"
CONGRESO_YUC = "H. Congreso del Estado de Yucatán"

PERSONS = {
    "Q5931901": ("Joaquín Jesús Díaz Mena", "male"),
    "Q6105629": ("Renán Alberto Barrera Concha", "male"),
}
GENDERS = {
    "Vida Aravari Gómez Herrera": "female",
    "Yamil Jasmín López Manrique": "female",
    "Paulina Alejandra Del Moral Vela": "female",
    "Delfina Gómez Álvarez": "female",
}
LOCAL_PARTIES = {
    "NAY": ("YUC", "Nueva Alianza Yucatán", "New Alliance Yucatán"),
    "NAEM": ("MEX", "Nueva Alianza Estado de México", "New Alliance State of Mexico"),
}

ELECTIONS: dict[str, dict] = {
    "MEX-2023": {
        "state": "15",
        "iso": "MEX",
        "day": date(2023, 6, 4),
        "publisher": IEEM,
        "title": "Resultados definitivos por casilla de la elección de Gubernatura 2023",
        "page": "https://www.ieem.org.mx/procesos-electorales/historico-de-procesos-electorales/elecciones-locales.html",
        "url": "https://www.ieem.org.mx/assets/docs/procesos-electorales/resultados/2023/"
        "RESULTADOS%20DEFINITIVOS%20%20GUBERNATURA%202023%20POR%20CASILLA.xlsx",
        "header": 3,
        "first": "CASILLA",
        "candidates": [
            (
                "Paulina Alejandra Del Moral Vela",
                ("PAN", "PRI", "PRD", "NAEM"),
                "Coalición Va por el Estado de México",
            ),
            (
                "Delfina Gómez Álvarez",
                ("PVEM", "PT", "MORENA"),
                "Candidatura común Juntos Hacemos Historia en el Estado de México",
            ),
        ],
        "registrations": {
            "url": "https://ieem.org.mx/consejo_general/cg/2023/AC_23/a{:03d}_23.pdf",
            "title": "Acuerdo IEEM/CG/{}/2023 del Consejo General del IEEM: registro de la candidatura a la "
            "Gubernatura",
            "acuerdos": {
                "Paulina Alejandra Del Moral Vela": ((52, 42),),
                "Delfina Gómez Álvarez": ((53, 42),),
            },
        },
        "declaration": {
            "url": "https://ieem.org.mx/consejo_general/cg/2023/AC_23/a081_23.pdf",
            "suffix": ".pdf",
            "publisher": IEEM,
            "title": "Acuerdo IEEM/CG/81/2023 del Consejo General del IEEM: declaración de validez de la elección "
            "a la Gubernatura y constancia de mayoría",
            "locator": {"page": 44, "section": "Cómputo final, and acuerdos segundo to cuarto, pages 58 and 59"},
            "winner": "Delfina Gómez Álvarez",
            "total": 6375763,
            "votes": 3360589,
        },
        "caveats": [
            (
                "The count file lists 9 casillas annulled by the Tribunal Electoral del Estado de México (rulings "
                "JI/1/2023, JI/2/2023, and JI/3/2023) and 1 casilla that was not installed, all with zero votes. "
                "The parties of the candidatura común Juntos Hacemos Historia en el Estado de México shared one "
                "emblem on the ballot, so their votes are counted as one line."
            )
        ],
    },
    "YUC-2024": {
        "state": "31",
        "iso": "YUC",
        "day": date(2024, 6, 2),
        "publisher": IEPAC,
        "title": "Resultados de los cómputos de la elección de Gubernatura 2024, por casilla",
        "page": "https://iepac.mx/micrositios/resultados-electorales",
        "url": "https://iepac.mx/public/micrositios/resultados-electorales/procesos-electorales/2024/"
        "RESULTADOS-GUBERNATURA.zip",
        "member": "2024_GOBERNADOR_CASILLA.csv",
        "state_file": "2024_GOBERNADOR_ENTIDAD.csv",
        "candidates": [
            ("Q5931901", ("PVEM", "PT", "MORENA"), "Coalición Sigamos Haciendo Historia en Yucatán"),
            ("Q6105629", ("PAN", "PRI", "NAY"), "Candidatura común PAN-PRI-Nueva Alianza Yucatán"),
            ("Vida Aravari Gómez Herrera", ("MC",), None),
            ("Yamil Jasmín López Manrique", ("PRD",), None),
        ],
        "registrations": {
            "url": "https://iepac.mx/public/documentos-del-consejo-general/acuerdos/iepac/2024/ACUERDO-C.G.{:03d}-2024.pdf",
            "title": "Acuerdo C.G.-{:03d}/2024 del Consejo General del IEPAC: registro de la candidatura a la Gubernatura",
            "acuerdos": {
                "Q5931901": ((22, 7),),
                "Q6105629": ((19, 6), (20, 6), (24, 6)),
                "Vida Aravari Gómez Herrera": ((23, 7),),
                "Yamil Jasmín López Manrique": ((21, 7),),
            },
        },
        "declaration": {
            "url": "https://www.congresoyucatan.gob.mx/storage/uploadCey/484f48_BANDO%20SOLEMNE%20-VF.docx",
            "suffix": ".docx",
            "publisher": CONGRESO_YUC,
            "title": "Bando Solemne para dar a conocer en todo el Estado de Yucatán la declaración de Gobernador "
            "electo (dictamen A.G.-006/2024 del Tribunal Electoral del Estado de Yucatán)",
            "locator": {"section": "Bando Solemne, resolutivos primero a tercero"},
            "winner": "Q5931901",
            "total": 1222593,
            "votes": 629394,
        },
    },
}

SYSTEM = (
    "Each state elects its governor (in Mexico City, the Head of Government) by plurality in a single statewide "
    "contest for a six-year term, without reelection. A candidate may run for one party, for a coalition, or for "
    "a candidatura común; votes marked for two or more of the parties behind the same candidate count for that "
    "candidate."
)
CASILLA_METHOD = (
    "The state electoral institute's count file, one row per casilla and per mesa de escrutinio for votes cast "
    "abroad: {casillas:,} rows and {votes:,} votes. Each candidate's votes are split into the party lines and the "
    "combinations of the parties behind them, as printed on the acta."
)
STATE_METHOD = (
    "Sums of the {casillas:,} rows of the state electoral institute's count file by casilla ({votes:,} votes), "
    "with each candidate's party lines and combinations added together."
)
DECLARED = (
    "The declaring body states a total of {total:,} votes and {votes:,} for the winner, {name}; the count file "
    "adds to {counted:,} and {won:,}. It does not give the other candidates' figures, so the counts stay as the "
    "file prints them and the declared figures are kept here."
)

OVER = (
    "In {count} casillas the count file prints more votes than voters on the list ({listed}). The figures are "
    "stored as printed."
)


def name_of(candidate: str) -> str:
    return PERSONS[candidate][0] if candidate in PERSONS else candidate


def fetch() -> None:
    for config in ELECTIONS.values():
        raw.fetch(SOURCE, config["url"], suffix="." + config["url"].rsplit(".", 1)[1])
        raw.fetch(SOURCE, config["declaration"]["url"], suffix=config["declaration"]["suffix"])
        registrations = config["registrations"]
        for numbers in registrations["acuerdos"].values():
            for number, _ in numbers:
                raw.fetch(SOURCE, registrations["url"].format(number), suffix=".pdf")


def read(config: dict, member: str | None) -> list[dict[str, str]]:
    if member is None:
        grid = pl.read_excel(raw.cached(SOURCE, config["url"]).path, has_header=False).rows()
        header = [str(c) for c in grid[config["header"]]]
        return [
            {k: "" if v is None else str(v) for k, v in zip(header, row, strict=True) if k != "None"}
            for row in grid[config["header"] + 1 :]
        ]
    with zipfile.ZipFile(raw.cached(SOURCE, config["url"]).path) as archive:
        inner = next(n for n in archive.namelist() if n.endswith(member))
        text = archive.read(inner).decode("latin-1")
    return list(csv.DictReader(io.StringIO(text)))


def tail(row: dict[str, str]) -> str:
    kind = row["TIPO_CASILLA"].strip()
    if kind == "MEC" and "ACTA_CASILLA-MEC" in row:
        return row["ACTA_CASILLA-MEC"].strip().upper()
    if kind == "B":
        return "B"
    extension = int(row["EXT_CONTIGUA"] or 0)
    return f"{kind}{int(row['ID_CASILLA'])}" + (f"C{extension}" if extension else "")


def table(config: dict) -> tuple[pl.DataFrame, pl.DataFrame]:
    rows = read(config, config.get("member"))
    header = list(rows[0])
    columns = header[header.index(config.get("first", "URNA_ELECTRONICA")) + 1 : header.index("NUM_VOTOS_VALIDOS")]
    lines = {column for _, parties, _ in config["candidates"] for column in combinations(parties)}
    if set(columns) - lines:
        raise ValueError(f"columns without a candidate: {sorted(set(columns) - lines)}")
    units, votes = [], []
    for row in rows:
        counts = {column: ine.number(row[column]) for column in columns}
        unregistered = ine.number(row["NUM_VOTOS_CAN_NREG"])
        null = ine.number(row["NUM_VOTOS_NULOS"])
        total = ine.number(row["TOTAL_VOTOS"])
        if sum(counts.values()) != ine.number(row["NUM_VOTOS_VALIDOS"]):
            raise ValueError(f"valid votes differ: {row}")
        if sum(counts.values()) + unregistered + null != total:
            raise ValueError(f"total differs: {row}")
        unit = {
            "section": ine.number(row["SECCION"]),
            "casilla": tail(row),
            "district": ine.number(row["ID_DISTRITO_LOCAL"]),
            "municipality": ine.number(row["ID_MUNICIPIO"]),
        }
        units.append(
            unit
            | {
                "total": total,
                "valid": sum(counts.values()) + unregistered,
                "null": null,
                "registered": ine.number(row["LISTA_NOMINAL"]),
                "status": row["ESTATUS_ACTA"].strip(),
            }
        )
        votes += [unit | {"option": column, "votes": value} for column, value in counts.items()]
        votes.append(unit | {"option": "NUM_VOTOS_CAN_NREG", "votes": unregistered})
    frame = pl.DataFrame(units)
    if frame.group_by("section", "casilla").len().filter(pl.col("len") > 1).height:
        raise ValueError("duplicate casillas")
    return frame, pl.DataFrame(votes)


def combinations(parties: tuple[str, ...]) -> list[str]:
    found = []
    for size in range(1, len(parties) + 1):
        found += ["_".join(c) for c in _choose(parties, size)]
    return found


def _choose(items: tuple[str, ...], size: int) -> list[tuple[str, ...]]:
    if size == 0:
        return [()]
    return [(items[i], *rest) for i in range(len(items)) for rest in _choose(items[i + 1 :], size - 1)]


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for key, config in ELECTIONS.items():
        units, votes = table(config)
        if "state_file" in config:
            printed = read(config, config["state_file"])[0]
            inland = units.filter(pl.col("section") > 0)
            if inland["total"].sum() != ine.number(printed["TOTAL_VOTOS"]):
                raise ValueError(f"{key}: casillas add to {inland['total'].sum()}, the state file prints {printed}")
        elif units["total"].sum() != config["declaration"]["total"]:
            raise ValueError(f"{key}: casillas add to {units['total'].sum()}, not the declared total")
        units.write_parquet(PARSED / f"{key}_casillas.parquet")
        votes.write_parquet(PARSED / f"{key}_votes.parquet")
        by_option = votes.group_by("option").agg(pl.col("votes").sum()).sort("votes", descending=True)
        print(key, units.height, "casillas", units["total"].sum(), "votes", dict(by_option.rows()))


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mexican state governor elections from the state electoral institutes' count files by casilla, "
            "with the governors-elect as declared: " + ", ".join(ELECTIONS),
            polity="MX",
        )
        for scheme in ("mx.entidad", "mx.seccion", "mx.casilla"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish mx.mge before mx.gubernatura")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        mexico = cs.polity("MX")
        system = cs.key("mx.electoral_system", "governor")
        if system in cs.minted:
            cs.add("electoral_system", id=system, polity_id=mexico, rules={"formula": "fptp"}, rules_version=1)
            name(system, "Mexican state governor plurality system")
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        parties: dict[str, UUID] = {}

        def party(abbrev: str) -> UUID:
            if abbrev not in parties:
                if abbrev in ine.PARTIES:
                    item, native, english = ine.PARTIES[abbrev]
                    parties[abbrev] = cs.key("mx.party", item)
                    owner = mexico
                else:
                    iso, native, english = LOCAL_PARTIES[abbrev]
                    item = None
                    parties[abbrev] = cs.key("mx.party", f"MX-{iso}:{abbrev}")
                    owner = cs.polity(f"MX-{iso}")
                if parties[abbrev] in cs.minted:
                    cs.add("party", id=parties[abbrev], kind="party", polity_id=owner)
                    name(parties[abbrev], native, "es")
                    name(parties[abbrev], english, "en", "english")
                    name(parties[abbrev], abbrev, "es", "abbreviation")
                    if item is not None:
                        cs.add("external_id", entity_id=parties[abbrev], scheme="wikidata", value=item)
            return parties[abbrev]

        persons: dict[str, UUID] = {}

        def person(candidate: str) -> UUID:
            if candidate not in persons:
                persons[candidate] = cs.key("mx.person", candidate)
                if persons[candidate] in cs.minted:
                    if candidate in PERSONS:
                        full, gender = PERSONS[candidate]
                        cs.add("person", id=persons[candidate], gender=gender)
                        cs.add("external_id", entity_id=persons[candidate], scheme="wikidata", value=candidate)
                    else:
                        full = candidate
                        cs.add("person", id=persons[candidate], gender=GENDERS.get(candidate))
                    name(persons[candidate], full)
            return persons[candidate]

        names = {
            code: dict(rows)
            for code, rows in (
                (
                    f"{code:02d}",
                    conn.execute(
                        "select n.kind::text, n.text from area a join area_code c on c.area_id = a.id "
                        "join name n on n.entity_id = a.id where a.scheme_code = 'mx.entidad' and c.code = %s",
                        (f"{code:02d}",),
                    ).fetchall(),
                )
                for code in range(1, 33)
            )
        }
        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'mx.seccion'"
            ).fetchall()
        )
        unit_rows: dict[UUID, tuple[str, UUID, UUID, str]] = {}
        frames: list[pl.DataFrame] = []
        for key, config in ELECTIONS.items():
            state, iso, day = config["state"], config["iso"], config["day"]
            year = day.year
            spanish = names[state]["official"]
            english = names[state].get("english") or spanish
            polity = cs.polity(f"MX-{iso}")
            office = cs.key("mx.office", f"governor:{iso}")
            if office in cs.minted:
                cs.add("office", id=office, polity_id=polity, kind="head_of_government" if iso == "CMX" else "governor")
                name(office, f"Governor of {english}")
                name(office, f"Gubernatura del Estado de {spanish}", "es", "native")

            source = cs.source(config["publisher"], config["title"])
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=config["publisher"],
                title=config["title"],
                url=config["page"],
                license=ine.PUBLIC,
            )
            cs.artifact(raw.cached(SOURCE, config["url"]), source)
            declared = config["declaration"]
            declarer = cs.source(declared["publisher"], declared["title"])
            cs.add(
                "source",
                id=declarer,
                kind="official_body",
                publisher=declared["publisher"],
                title=declared["title"],
                url=declared["url"],
                license=ine.PUBLIC,
            )
            cs.artifact(raw.cached(SOURCE, declared["url"]), declarer)

            election = cs.key("mx.election", f"{day}:{iso}")
            path = f"mx/{iso.lower()}/{day}-general"
            if election in cs.minted:
                cs.add(
                    "election", id=election, polity_id=polity, kind="general", date_from=day, date_to=day, status="held"
                )
                name(election, f"{year} {english} state election")
                name(election, f"Elecciones locales de {spanish} de {year}", "es", "native")
                cs.add("slug", path=path, entity_id=election)
            contest = cs.key("mx.contest", f"{day}:{iso}:governor")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=cs.area("mx.entidad", state),
                magnitude=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"{year} {english} gubernatorial election")
            name(contest, f"Elección de Gubernatura de {spanish} de {year}", "es", "native")
            cs.add("slug", path=f"{path}/governor", entity_id=contest)

            casillas = pl.read_parquet(PARSED / f"{key}_casillas.parquet")
            votes = pl.read_parquet(PARSED / f"{key}_votes.parquet")
            counted, rows_count = int(casillas["total"].sum()), casillas.height
            printed = set(votes["option"])
            heads: dict[str, UUID] = {}
            lines: dict[str, UUID] = {}
            for position, (candidate, group, label) in enumerate(config["candidates"], 1):
                alliance = None
                if len(group) > 1:
                    alliance = cs.key("mx.alliance", f"{day}:{iso}:{'+'.join(group)}")
                    cs.add("alliance", id=alliance, kind="electoral_alliance", election_id=election)
                    name(alliance, label or "-".join(group), "es")
                    for member in group:
                        cs.add("alliance_member", alliance_id=alliance, party_id=party(member))
                head = heads[candidate] = cs.key("mx.option", f"{day}:{iso}:governor:{candidate}")
                cs.add(
                    "contest_option",
                    id=head,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party(group[0]) if len(group) == 1 else None,
                    alliance_id=alliance,
                    ballot_position=position,
                    ballot_label=name_of(candidate),
                )
                candidacy = cs.key("mx.candidacy", f"{day}:{iso}:governor:{candidate}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person(candidate),
                    option_id=head,
                    role="head",
                    ballot_name=name_of(candidate),
                    is_independent=False,
                )
                for member in group:
                    cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party(member), kind="nominated")
                registrations = config["registrations"]
                for number, page in registrations["acuerdos"][candidate]:
                    url, title = registrations["url"].format(number), registrations["title"].format(number)
                    acuerdo = cs.source(config["publisher"], title)
                    cs.add(
                        "source",
                        id=acuerdo,
                        kind="official_body",
                        publisher=config["publisher"],
                        title=title,
                        url=url,
                        license=ine.PUBLIC,
                    )
                    cs.artifact(raw.cached(SOURCE, url), acuerdo)
                    cs.add("citation", entity_id=candidacy, source_id=acuerdo, locator={"page": page})
                columns = [column for column in combinations(group) if column in printed]
                for column in columns:
                    if len(columns) == 1:
                        lines[column] = head
                        continue
                    parts = column.split("_")
                    line = lines[column] = cs.key("mx.option", f"{day}:{iso}:governor:{candidate}:{column}")
                    cs.add(
                        "contest_option",
                        id=line,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=head,
                        party_id=party(parts[0]) if len(parts) == 1 else None,
                        alliance_id=alliance if len(parts) > 1 else None,
                        ballot_label="-".join(parts),
                    )
            write_in = cs.key("mx.option", f"{day}:{iso}:governor:unregistered")
            cs.add(
                "contest_option",
                id=write_in,
                contest_id=contest,
                kind="write_in",
                ballot_label="Candidatos no registrados",
            )
            lines["NUM_VOTOS_CAN_NREG"] = write_in
            line_head = {
                column: heads[c]
                for c, group, _ in config["candidates"]
                for column in combinations(group)
                if column in printed
            }
            line_head["NUM_VOTOS_CAN_NREG"] = write_in

            def make_set(label: str, default: bool, note: str) -> UUID:
                result_set = cs.key("mx.result_set", f"{day}:{iso}:governor:{label}")
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
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                return result_set

            state_set = make_set("state", True, STATE_METHOD.format(casillas=rows_count, votes=counted))
            casilla_set = make_set("casilla", False, CASILLA_METHOD.format(casillas=rows_count, votes=counted))
            totals = {
                candidate: int(votes.filter(pl.col("option").is_in(combinations(group)))["votes"].sum())
                for candidate, group, _ in config["candidates"]
            }
            winner = declared["winner"]
            if max(totals, key=lambda c: totals[c]) != winner:
                raise ValueError(f"{key}: the declared winner does not lead the count")
            cs.add("outcome", result_set_id=state_set, option_id=heads[winner], kind="elected", seats=1)
            cs.add("citation", entity_id=state_set, source_id=declarer, locator=declared["locator"])
            if declared.get("total") != counted or declared.get("votes") != totals[winner]:
                cs.add(
                    "note",
                    entity_id=state_set,
                    kind="caveat",
                    body_md=DECLARED.format(
                        total=declared["total"],
                        votes=declared["votes"],
                        name=name_of(winner),
                        counted=counted,
                        won=totals[winner],
                    ),
                )
            over = casillas.filter(
                (pl.col("section") > 0) & (pl.col("registered") > 0) & (pl.col("total") > pl.col("registered"))
            ).sort("section", "casilla")
            for caveat in config.get("caveats", []):
                for result_set in (state_set, casilla_set):
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=caveat)
            if over.height:
                listed = "; ".join(
                    f"sección {row['section']} {row['casilla']}, {row['total']:,} votes for {row['registered']:,}"
                    for row in over.iter_rows(named=True)
                )
                for result_set in (state_set, casilla_set):
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=OVER.format(count=over.height, listed=listed),
                    )

            state_unit = str(cs.unit("mx.entidad", state))
            federal = {}
            for code, unit in conn.execute(
                "select code, id from reporting_unit where scheme_code = 'mx.casilla' and code like %s",
                (f"{year}:{state}-%",),
            ).fetchall():
                _, _, section, kind = code.split(":", 1)[1].split("-", 3)
                federal.setdefault((int(section), kind), []).append(unit)
            placed, shared = [], 0
            for row in casillas.iter_rows(named=True):
                section, tail_code = row["section"], row["casilla"]
                channel = (
                    PRISON
                    if tail_code.startswith(("P", "VPPP"))
                    else EARLY
                    if tail_code.startswith("VA")
                    else ABROAD
                    if section == 0
                    else 0
                )
                found = federal.get((section, tail_code), [])
                if section > 0 and len(found) == 1:
                    unit, shared = found[0], shared + 1
                else:
                    code = f"{year}:{state}-gub-{section:04d}-{tail_code}"
                    unit = cs.unit("mx.casilla", code)
                    area = sections.get(f"{state}{section:04d}") if section else None
                    unit_rows[unit] = (
                        code,
                        cs.unit("mx.entidad", state),
                        area or cs.area("mx.entidad", state),
                        "aggregate" if section == 0 else "standard",
                    )
                placed.append((section, tail_code, str(unit), channel))
            units = pl.DataFrame(
                placed,
                schema=[("section", pl.Int64), ("casilla", pl.String), ("unit_id", pl.String), ("channel", pl.Int16)],
                orient="row",
            )
            print(key, shared, "casillas share a federal unit")
            located = casillas.join(units, on=["section", "casilla"])
            joined = votes.join(units, on=["section", "casilla"]).with_columns(
                line=pl.col("option").replace_strict({k: str(v) for k, v in lines.items()}, return_dtype=pl.String),
                head=pl.col("option").replace_strict({k: str(v) for k, v in line_head.items()}, return_dtype=pl.String),
            )
            none = pl.lit(None, pl.String)
            casilla_parts = [
                located.filter(pl.col("registered") > 0).select(
                    "unit_id", "channel", option_id=none, measure=pl.lit(REGISTERED), value="registered"
                ),
                located.select("unit_id", "channel", option_id=none, measure=pl.lit(BALLOTS), value="total"),
                located.select("unit_id", "channel", option_id=none, measure=pl.lit(VALID), value="valid"),
                located.select("unit_id", "channel", option_id=none, measure=pl.lit(INVALID), value="null"),
                joined.filter(pl.col("votes") != 0).select(
                    "unit_id", "channel", option_id="line", measure=pl.lit(VOTES), value="votes"
                ),
            ]
            state_parts = [
                joined.group_by("head")
                .agg(value=pl.col("votes").sum())
                .select(option_id="head", measure=pl.lit(VOTES), value="value"),
                *(
                    pl.DataFrame(
                        {"option_id": [None], "measure": [measure], "value": [int(located[column].sum())]},
                        schema_overrides={"option_id": pl.String},
                    )
                    for measure, column in ((BALLOTS, "total"), (VALID, "valid"), (INVALID, "null"))
                ),
                pl.DataFrame(
                    {
                        "option_id": [None],
                        "measure": [REGISTERED],
                        "value": [int(located.filter(pl.col("section") > 0)["registered"].sum())],
                    },
                    schema_overrides={"option_id": pl.String},
                ),
            ]
            for part in casilla_parts:
                frames.append(
                    part.select(
                        result_set_id=pl.lit(str(casilla_set)),
                        unit_id="unit_id",
                        option_id="option_id",
                        measure=pl.col("measure").cast(pl.Int16),
                        value=pl.col("value").cast(pl.Int64),
                        channel=pl.col("channel").cast(pl.Int16),
                    )
                )
            for part in state_parts:
                frames.append(
                    part.select(
                        result_set_id=pl.lit(str(state_set)),
                        unit_id=pl.lit(state_unit),
                        option_id="option_id",
                        measure=pl.col("measure").cast(pl.Int16),
                        value=pl.col("value").cast(pl.Int64),
                        channel=pl.lit(0, pl.Int16),
                    )
                )
            print(key, rows_count, "casillas", counted, "votes", totals)

        minted = [
            {
                "id": str(unit),
                "polity_id": str(mexico),
                "scheme_code": "mx.casilla",
                "code": code,
                "parent_id": str(parent),
                "area_relation": "within",
                "area_id": str(area),
                "kind": kind,
            }
            for unit, (code, parent, area, kind) in unit_rows.items()
            if unit in cs.minted
        ]
        if minted:
            cs.frame("reporting_unit", pl.DataFrame(minted))
        cs.frame(
            "result_value",
            pl.concat(frames).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
