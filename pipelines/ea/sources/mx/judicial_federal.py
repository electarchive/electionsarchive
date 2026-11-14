import csv
import io
import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.mx import ine
from ea.store import DATA, connect

SOURCE = "mx.judicial_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

SITE = "https://computospj2025.ine.mx/"
ZIP = f"{SITE}assets/20250609_2050_COMPUTOS.zip"
DOF = "https://dof.gob.mx/2025/INE/"
WAYBACK = "https://web.archive.org/web/{stamp}id_/https://repositoriodocumental.ine.mx/xmlui/bitstream/handle/123456789/{handle}/CGex202508-31-ap-2-{n}.pdf"
DAY = date(2025, 6, 1)
TITLE = "Cómputos distritales de la Elección del Poder Judicial de la Federación 2025, por acta de casilla"
LICENSE = "Public information INE publishes for download (Ley General de Transparencia, art. 70); no license stated"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
EARLY = 3
OFFICES = {
    "scjn": ("SCJN", "MIN"),
    "tdj": ("TDJ", "MAG_TDJ"),
    "ss": ("TEPJF_SS", "MAG_SS"),
    "sr": ("TEPJF_SR", "MAG_SR"),
    "tcc": ("TCCA", "MAG_TC"),
    "jd": ("JD", "JUZ"),
}
ACUERDOS = {
    "scjn": (1, 2, 563, 564),
    "tdj": (3, 4, 565, 566),
    "ss": (5, 6, 567, 568),
    "sr": (7, 8, 569, 570),
    "tcc": (9, 10, 571, 572),
    "jd": (11, 12, 573, 574),
}
COMPLIANCE = {
    1138: (1, "20260116182134", 185268),
    1139: (2, "20260116191522", 185269),
    1141: (4, "20260123013241", 185271),
    1142: (5, "20251004102242", 185272),
}
CG1140 = f"{DOF}CGext202508_31_ap_2_3.pdf"


def acuerdo_url(n: int) -> str:
    return f"{DOF}CGext202506_15_ap_2_{n}.pdf"


def compliance_url(number: int) -> str:
    if number == 1140:
        return CG1140
    n, stamp, handle = COMPLIANCE[number]
    return WAYBACK.format(stamp=stamp, handle=handle, n=n)


def fetch() -> None:
    raw.fetch(SOURCE, ZIP, suffix=".zip")
    for numbers in ACUERDOS.values():
        for n in numbers[:2]:
            raw.fetch(SOURCE, acuerdo_url(n), suffix=".pdf")
    for number in (1138, 1139, 1140, 1141, 1142):
        raw.fetch(SOURCE, compliance_url(number), suffix=".pdf")


def members(office: str) -> tuple[list[str], list[str]]:
    folder, stem = OFFICES[office]
    with zipfile.ZipFile(raw.cached(SOURCE, ZIP).path) as outer:
        inner = next(n for n in outer.namelist() if n.endswith(f"_{folder}.zip"))
        with zipfile.ZipFile(io.BytesIO(outer.read(inner))) as archive:
            names = archive.namelist()
            body = archive.read(next(n for n in names if n.endswith(f"{stem}_2025.csv"))).decode("utf-8-sig")
            roster = archive.read(next(n for n in names if n.endswith(f"{stem}_CANDIDATURAS_2025.csv")))
    return body.splitlines(), roster.decode("utf-8-sig").splitlines()


def fold(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^A-Z]", " ", text).split())


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    for office in OFFICES:
        lines, roster = members(office)
        summary = next(i for i, line in enumerate(lines) if line.startswith("ACTAS_ESPERADAS"))
        printed = dict(zip(lines[summary].split(","), lines[summary + 1].split(","), strict=True))
        head = next(i for i, line in enumerate(lines) if line.startswith("CLAVE_CASILLA"))
        reader = csv.DictReader(io.StringIO("\n".join(lines[head:])))
        columns = [c for c in reader.fieldnames or [] if re.fullmatch(r"CAND\d+", c)]
        casillas, votes = [], []
        for row in reader:
            counts = {c: ine.number(row[c]) for c in columns if ine.clean(row[c]) not in ("-", "")}
            null, unused, total = (
                ine.number(row[c]) for c in ("VOTOS_NULOS", "RECUADROS_NO_UTILIZADOS", "TOTAL_VOTOS_CASILLA")
            )
            if sum(counts.values()) + null + unused != total:
                raise ValueError(
                    f"{office} {row['CLAVE_CASILLA']}: marks add to {sum(counts.values()) + null + unused}"
                )
            unit = {
                "state": int(row["ID_ENTIDAD"]),
                "district": int(row["ID_DISTRITO_FEDERAL"]),
                "section": int(row["SECCION"]),
                "kind": ine.clean(row["TIPO_CASILLA_SECCIONAL"]),
                "number": int(row["ID_CASILLA"]),
                "clave": ine.clean(row["CLAVE_CASILLA"]),
            }
            region = {
                "circuit": int(row.get("CIRCUITO_JUDICIAL") or 0),
                "dje": int(row.get("DISTRITO_JUDICIAL_ELECTORAL") or 0),
                "circ": int(row.get("CIRCUNSCRIPCION") or 0),
            }
            casillas.append(
                unit
                | region
                | {
                    "null": null,
                    "unused": unused,
                    "total": total,
                    "voters": ine.number(row["NUMERO_PERSONAS_VOTARON"]),
                    "registered": ine.number(row["LISTA_NOMINAL_CASILLA"]),
                    "status": ine.clean(row["ESTATUS_CASILLA"]),
                }
            )
            for column, value in counts.items():
                votes.append(unit | region | {"cand": column, "votes": value})
        frame = pl.DataFrame(casillas)
        if frame.height != int(printed["ACTAS_COMPUTADAS"]) or frame["total"].sum() != int(printed["TOTAL_VOTOS"]):
            raise ValueError(f"{office}: {frame.height} actas and {frame['total'].sum()} marks against {printed}")
        if frame["voters"].sum() != int(printed["TOTAL_PERSONAS_VOTARON"]):
            raise ValueError(f"{office}: {frame['voters'].sum()} voters against {printed}")
        if frame.group_by("state", "district", "section", "kind", "number", "clave").len()["len"].max() != 1:
            raise ValueError(f"{office}: duplicate casillas")
        people = list(csv.DictReader(io.StringIO("\n".join(roster))))
        candidates = pl.DataFrame(
            [
                {
                    "circuit": int(p.get("CIRCUITO_JUDICIAL") or 0),
                    "dje": int(p.get("DISTRITO_JUDICIAL_ELECTORAL") or 0),
                    "circ": int(p.get("CIRCUNSCRIPCION") or 0),
                    "cand": ine.clean(p["NO_CANDIDATO"]),
                    "name": " ".join(p["NOMBRE_CANDIDATO"].split()),
                    "gender": p["GENERO"].strip(),
                    "materia": (p.get("MATERIA") or "").strip(),
                    "branch": p["PODER_POSTULANTE"].strip(),
                    "canceled": p["ESTATUS_CANCELADO"].strip() != "No",
                }
                for p in people
            ]
        )
        frame.write_parquet(PARSED / f"{office}_casillas.parquet")
        pl.DataFrame(votes).write_parquet(PARSED / f"{office}_votes.parquet")
        candidates.write_parquet(PARSED / f"{office}_candidates.parquet")
        print(office, frame.height, "actas", len(votes), "cells", candidates.height, "candidates", printed)


NAMES = {
    "scjn": (
        "Minister of the Supreme Court of Justice of the Nation",
        "Ministra o Ministro de la Suprema Corte de Justicia de la Nación",
        "supreme-court",
    ),
    "tdj": (
        "Magistrate of the Judicial Discipline Tribunal",
        "Magistrada o Magistrado del Tribunal de Disciplina Judicial",
        "judicial-discipline-tribunal",
    ),
    "ss": (
        "Magistrate of the Superior Chamber of the Electoral Tribunal of the Federal Judiciary",
        "Magistrada o Magistrado de la Sala Superior del Tribunal Electoral del Poder Judicial de la Federación",
        "electoral-tribunal-superior-chamber",
    ),
    "sr": (
        "Magistrate of a Regional Chamber of the Electoral Tribunal of the Federal Judiciary",
        "Magistrada o Magistrado de Sala Regional del Tribunal Electoral del Poder Judicial de la Federación",
        "electoral-tribunal-regional-chamber",
    ),
}
CIRCUNSCRIPCIONES = {1: "Primera", 2: "Segunda", 3: "Tercera", 4: "Cuarta", 5: "Quinta"}
SEDES = {1: "Guadalajara", 2: "Monterrey", 3: "Xalapa", 4: "Ciudad de México", 5: "Toluca"}
WINNERS: dict[str, dict[int, tuple[str, ...]]] = {
    "scjn": {
        0: (
            "AGUILAR ORTIZ HUGO",
            "BATRES GUADARRAMA LENIA",
            "ESQUIVEL MOSSA YASMIN",
            "ORTIZ AHLF LORETTA",
            "RIOS GONZALEZ MARIA ESTELA",
            "FIGUEROA MEJIA GIOVANNI AZAEL",
            "ESPINOSA BETANZO IRVING",
            "GUERRERO GARCIA ARISTIDES RODRIGO",
            "HERRERIAS GUERRA SARA IRENE",
        )
    },
    "tdj": {
        0: (
            "MAYA GARCIA CELIA",
            "BATIZ VAZQUEZ BERNARDO",
            "DE GYVES ZARATE EVA VERONICA",
            "H LEON TOVAR RUFINO",
            "GARCIA PEREZ INDIRA ISABEL",
        )
    },
    "ss": {0: ("BATIZ GARCIA GILBERTO DE GUZMAN", "VALLE AGUILASOCHO CLAUDIA")},
    "sr": {
        1: ("BARRERA AMADOR REBECA", "GUERRERO OLVERA SERGIO ARTURO", "CERVANTES BRAVO IRINA GRACIELA"),
        2: ("LOPEZ LOZA MARIA DOLORES", "DIAZ RENDON SERGIO", "VAZQUEZ OROZCO MARIA GUADALUPE"),
        3: ("BUSTILLO MARIN ROSELIA", "TRONCOSO AVILA JOSE ANTONIO", "BARRIENTOS ZEPEDA EVA"),
        4: ("GUEVARA Y HERRERA MARIA CECILIA", "CEBALLOS DAZA JOSE LUIS", "MENDOZA ARAGON IXEL"),
        5: ("AVALOS VAZQUEZ NEREIDA BERENICE", "HERNANDEZ ESQUIVEL OMAR", "FERNANDEZ DOMINGUEZ MARCELA ELENA"),
    },
}
REPLACED = {("sr", 2, "VAZQUEZ OROZCO MARIA GUADALUPE"): (1138, "FIGUEROA GAMEZ MADELEYNE IVETT")}
MARKS = {
    "scjn": "up to five women and four men",
    "tdj": "up to three women and two men",
    "ss": "up to one woman and one man",
}
SYSTEM = (
    "Federal judicial offices filled by popular vote under the 2024 constitutional reform. Each voter marks as many "
    "candidates as the ballot allows ({marks}), and the seats go to the candidates with the most votes. Gender "
    "parity is applied when seats are assigned: the Consejo General of INE alternates women and men from the top of "
    "each list, starting with a woman, and the parity adjustment is recorded in the declared winners, not in this "
    "rule."
)
SYSTEM_SR = (
    "Each of the five regional chambers of the Electoral Tribunal is filled in its circunscripción plurinominal. "
    "Voters mark up to two women and one man, the seats go to the candidates with the most votes, and the Consejo "
    "General of INE alternates women and men from the top when it assigns them, so each chamber seats two women and "
    "one man."
)
DECLARED_NOTE = (
    "The national count (sumatoria nacional) as the Consejo General of INE declared it in {acuerdo}: the district "
    "counts of {actas:,} actas, less the votes of the {inviable:,} marks it ruled could not be added to the national "
    "count (casillas with turnout at or above 100 percent, unfolded ballots, and other irregularities listed in its "
    "fourth considerando). Votes are marks: each voter marks several candidates, so votes add to more than the "
    "number of voters. Null marks are invalid; unused boxes are not counted."
)
CANCELED = (
    " The candidacy of {names} was canceled after the ballots were printed; the acuerdo counts the marks for it as "
    "null, and the casilla set keeps them as the count file records them."
)
CASILLA_NOTE = (
    "Polling-station (casilla) results from INE's district count file of 9 June 2025: {actas:,} actas and "
    "{marks:,} marks, including the early-vote actas each district counted. These are the district councils' "
    "figures, before the local councils added late-found actas and before the Consejo General set aside the votes "
    "it ruled inviable, so they differ from the declared count. "
    "Ballots are the people who voted and invalid are the null marks; valid marks are not summed because each "
    "voter marks several candidates, and unused boxes are not counted."
)


def pdf_text(url: str) -> str:
    import subprocess

    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout


def triples(text: str) -> dict[int, tuple[int, int]]:
    less: dict[int, set[tuple[int, int]]] = {}
    more: dict[int, set[int]] = {}
    for line in text.splitlines():
        match = re.search(r"(?<![\w,.])(\d{1,3}(?:[ ,]\d{3})*(?:\s+\d{1,3}(?:[ ,]\d{3})*){2})\s*$", line)
        if match is None:
            continue
        groups = re.split(r"[ ,]+", match.group(1).strip())
        for i in range(1, len(groups) - 1):
            for j in range(i + 1, len(groups)):
                parts = (groups[:i], groups[i:j], groups[j:])
                if all(1 <= len(p[0]) <= 3 and all(len(g) == 3 for g in p[1:]) for p in parts):
                    a, b, c = (int("".join(p)) for p in parts)
                    if a - b == c and b < a:
                        less.setdefault(a, set()).add((b, c))
                    if a + b == c and b < a:
                        more.setdefault(a, set()).add(c)
    final = {a: next(iter(v)) for a, v in less.items() if len(v) == 1}
    counted = {}
    for a, v in more.items():
        if len(v) == 1 and next(iter(v)) in final:
            counted[a] = next(iter(v))
    return {a: (c - a + final[c][0], final[c][1]) for a, c in counted.items()} | {
        a: v for a, v in final.items() if a not in counted
    }


def nulls(text: str) -> dict[int, int]:
    found: dict[int, int] = {}
    for line in text.splitlines():
        match = re.match(r"\s*(?:\d+\s+)?(?:(\d)\s+)?NULOS\s", line)
        if match is None:
            continue
        for a, (b, c) in triples(line).items():
            if b < a and c == a - b:
                found[int(match.group(1) or 0)] = c
    return found


def emit() -> Path:
    from ea.changeset import slug

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Mexico's federal judicial election of 1 June 2025: Supreme Court, Judicial Discipline Tribunal, "
            "and the Electoral Tribunal's superior and regional chambers, by casilla, with the winners as declared",
            polity="MX",
        )
        for scheme in ("mx.entidad", "mx.seccion", "mx.casilla", "mx.distrito_federal"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish mx.mge before mx.judicial_federal")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        mexico = cs.polity("MX")
        source = cs.source(ine.INE, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=ine.INE,
            title=TITLE,
            url=SITE,
            license=LICENSE,
            license_url=SITE,
            attribution="Instituto Nacional Electoral, cómputos de la elección judicial 2025",
        )
        cs.artifact(raw.cached(SOURCE, ZIP), source)
        acuerdos: dict[int, UUID] = {}

        def acuerdo(number: int, url: str, title: str) -> UUID:
            if number not in acuerdos:
                acuerdos[number] = cs.source(ine.INE, f"INE/CG{number}/2025")
                cs.add(
                    "source",
                    id=acuerdos[number],
                    kind="official_body",
                    publisher=ine.INE,
                    title=f"INE/CG{number}/2025: {title}",
                    url=url,
                    license=ine.PUBLIC,
                )
                cs.artifact(raw.cached(SOURCE, url), acuerdos[number])
            return acuerdos[number]

        day = DAY.isoformat()
        election = cs.key("mx.election", f"{day}:judicial")
        cs.add("election", id=election, polity_id=mexico, kind="general", date_from=DAY, date_to=DAY, status="held")
        name(election, "2025 Mexican judicial election")
        name(election, "Proceso Electoral Extraordinario del Poder Judicial de la Federación 2024-2025", "es", "native")
        path = f"mx/{day}-judicial"
        cs.add("slug", path=path, entity_id=election)

        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'mx.seccion'"
            ).fetchall()
        )
        nation_unit = cs.unit("mx.nation", "MX")
        unit_rows: dict[UUID, tuple[str, str, UUID | None, str, UUID, str]] = {}
        state_units = {code: cs.unit("mx.entidad", f"{code:02d}") for code in range(1, 33)}

        sr_casillas = pl.read_parquet(PARSED / "sr_casillas.parquet")
        circ_states = sr_casillas.group_by("circ").agg(pl.col("state").unique().sort())
        circ_of: dict[int, list[int]] = {row["circ"]: row["state"] for row in circ_states.iter_rows(named=True)}
        if sum(len(v) for v in circ_of.values()) != 32 or len({s for v in circ_of.values() for s in v}) != 32:
            raise ValueError(f"states do not fall in one circunscripción each: {circ_of}")
        cs.add(
            "scheme",
            code="mx.circunscripcion",
            polity_id=mexico,
            kind="electoral_district",
            rank=1,
            noun="circunscripción plurinominal",
            code_system="ine",
            kinds=None,
        )
        cs.add(
            "boundary_set", scheme_code="mx.circunscripcion", vintage="2023", valid_from=None, valid_to=None, as_of=DAY
        )
        circ_units: dict[int, UUID] = {}
        for number, states in sorted(circ_of.items()):
            area = cs.area("mx.circunscripcion", str(number))
            cs.add("area", id=area, scheme_code="mx.circunscripcion")
            cs.add("area_code", area_id=area, code=str(number), valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("mx.nation", "MX"), valid_from=None)
            name(area, f"{CIRCUNSCRIPCIONES[number]} circunscripción plurinominal", "es")
            name(area, f"Circunscripción {number} ({SEDES[number]})", "en", "english")
            shape = conn.execute(
                "select encode(st_asewkb(st_multi(st_collectionextract(st_makevalid(st_union(g.geom)), 3))), 'hex'), "
                "encode(st_asewkb(st_pointonsurface(st_union(g.geom))), 'hex'), min(g.source_id::text) from area_geometry g "
                "join area_code c on c.area_id = g.area_id join area a on a.id = g.area_id "
                "where a.scheme_code = 'mx.entidad' and g.valid_to is null and c.code = any(%s)",
                ([f"{s:02d}" for s in states],),
            ).fetchone()
            if shape is None:
                raise ValueError(f"no state shapes for circunscripción {number}")
            geom, point, shapes = shape
            cs.add(
                "area_geometry",
                area_id=area,
                geom=geom,
                label_point=point,
                quality="official",
                source_id=shapes,
                valid_from=None,
            )
            for state in states:
                cs.add(
                    "area_membership", area_id=cs.area("mx.entidad", f"{state:02d}"), parent_id=area, valid_from=None
                )
            unit = circ_units[number] = cs.unit("mx.circunscripcion", str(number))
            unit_rows[unit] = ("mx.circunscripcion", str(number), nation_unit, "equals", area, "standard")

        placed: dict[tuple[int, int, int, str, int], tuple[str, int]] = {}

        def locate(casillas: pl.DataFrame) -> pl.DataFrame:
            rows = []
            for row in casillas.iter_rows(named=True):
                key = (row["state"], row["district"], row["section"], row["kind"], row["number"])
                if key not in placed:
                    state, district, section = row["state"], row["district"], row["section"]
                    code = f"2025PJ:{state:02d}-{district:02d}-{section:04d}-{row['kind']}{row['number']}"
                    channel, kind = 0, "standard"
                    if row["kind"] == "A":
                        channel, kind = ine.EARLY, "aggregate"
                        area = cs.area("mx.distrito_federal", f"{state:02d}{district:02d}")
                    else:
                        area = sections.get(f"{state:02d}{section:04d}") or cs.area("mx.entidad", f"{state:02d}")
                    unit = cs.unit("mx.casilla", code)
                    if unit in unit_rows:
                        raise ValueError(f"casilla {code} appears twice")
                    unit_rows[unit] = ("mx.casilla", code, state_units[state], "within", area, kind)
                    placed[key] = (str(unit), channel)
                rows.append((*key, *placed[key]))
            return pl.DataFrame(
                rows,
                schema=[
                    ("state", pl.Int64),
                    ("district", pl.Int64),
                    ("section", pl.Int64),
                    ("kind", pl.String),
                    ("number", pl.Int64),
                    ("unit_id", pl.String),
                    ("channel", pl.Int16),
                ],
                orient="row",
            )

        keys = ["state", "district", "section", "kind", "number"]
        frames: list[pl.DataFrame] = []
        summary_rows: list[tuple[str, str, str | None, int, int, int]] = []
        persons = 0
        for office, (english, spanish, tail) in NAMES.items():
            first, second, sumatoria, validez = ACUERDOS[office]
            declared_source = acuerdo(sumatoria, acuerdo_url(first), f"sumatoria nacional y asignación, {spanish}")
            validity_source = acuerdo(validez, acuerdo_url(second), f"declaración de validez y constancias, {spanish}")
            text = pdf_text(acuerdo_url(first))
            declared, null_finals = triples(text), nulls(text)
            casillas = pl.read_parquet(PARSED / f"{office}_casillas.parquet")
            votes = pl.read_parquet(PARSED / f"{office}_votes.parquet")
            candidates = pl.read_parquet(PARSED / f"{office}_candidates.parquet")
            the_office = cs.key("mx.office", office)
            cs.add("office", id=the_office, polity_id=mexico, kind="other")
            name(the_office, english)
            name(the_office, spanish, "es", "native")
            regional = office == "sr"
            system = cs.key("mx.electoral_system", f"judicial-{office}")
            cs.add("electoral_system", id=system, polity_id=mexico, rules={"formula": "block"}, rules_version=1)
            name(system, f"Mexican judicial election, {english}")
            cs.add(
                "note",
                entity_id=system,
                kind="methodology",
                body_md=SYSTEM_SR if regional else SYSTEM.format(marks=MARKS[office]),
            )
            mapping: list[tuple[int, str, str]] = []
            set_rows: list[tuple[int, str, str]] = []
            for region, winners in WINNERS[office].items():
                label = f"{office}:{region}" if regional else office
                contest = cs.key("mx.contest", f"{day}:{label}")
                if regional:
                    area = cs.area("mx.circunscripcion", str(region))
                    total_unit = circ_units[region]
                    contest_en = f"2025 election of the Electoral Tribunal's regional chamber, {SEDES[region]}"
                    contest_es = (
                        f"Elección de Magistraturas de Sala Regional, {CIRCUNSCRIPCIONES[region]} circunscripción "
                        f"({SEDES[region]}), 2025"
                    )
                    contest_slug = f"{path}/{tail}-{slug(SEDES[region])}"
                    pool = candidates.filter(pl.col("circ") == region)
                    region_casillas = casillas.filter(pl.col("circ") == region)
                    region_votes = votes.filter(pl.col("circ") == region)
                else:
                    area = cs.area("mx.nation", "MX")
                    total_unit = nation_unit
                    contest_en = f"2025 election of {english}s".replace("Magistrate of", "Magistrates of")
                    contest_es = f"Elección de {spanish}, 2025"
                    contest_slug = f"{path}/{tail}"
                    pool, region_casillas, region_votes = candidates, casillas, votes
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=the_office,
                    district_area_id=area,
                    magnitude=len(winners),
                    electoral_system_id=system,
                    default_result_kind="counted",
                )
                name(contest, contest_en)
                name(contest, contest_es, "es", "native")
                cs.add("slug", path=contest_slug, entity_id=contest)
                totals = dict(
                    region_votes.group_by("cand").agg(pl.col("votes").sum()).select("cand", "votes").iter_rows()
                )
                declared_set = cs.key("mx.result_set", f"{day}:{label}:declared")
                casilla_set = cs.key("mx.result_set", f"{day}:{label}:casilla")
                inviable = 0
                canceled: list[str] = []
                options: dict[str, UUID] = {}
                by_name: dict[str, str] = {}
                for position, person_row in enumerate(pool.sort("cand").iter_rows(named=True), 1):
                    cand = person_row["cand"]
                    key = f"{day}:{label}:{cand}"
                    option = options[cand] = cs.key("mx.option", key)
                    by_name[fold(person_row["name"])] = cand
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_position=position,
                        ballot_label=person_row["name"],
                    )
                    person = cs.key("mx.person", key)
                    cs.add(
                        "person",
                        id=person,
                        gender={"M": "female", "H": "male"}[person_row["gender"]],
                    )
                    name(person, person_row["name"], "es")
                    persons += 1
                    cs.add(
                        "candidacy",
                        id=cs.key("mx.candidacy", key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=person_row["name"],
                        is_independent=True,
                    )
                    got = totals.get(cand, 0)
                    if person_row["canceled"]:
                        canceled.append(person_row["name"])
                        mapping.append((region, cand, str(option)))
                        continue
                    if got not in declared:
                        raise ValueError(f"{office} {region} {cand} {person_row['name']}: {got} not in the acuerdo")
                    lost, final = declared[got]
                    inviable += lost
                    summary_rows.append((str(declared_set), str(total_unit), str(option), VOTES, final, 0))
                    mapping.append((region, cand, str(option)))
                null_final = null_finals[region]
                finals = {
                    cand: declared[totals.get(cand, 0)][1]
                    for cand, gone in pool.select("cand", "canceled").iter_rows()
                    if not gone
                }
                valid_final = sum(finals.values())
                summary_rows += [
                    (str(declared_set), str(total_unit), None, VALID, valid_final, 0),
                    (str(declared_set), str(total_unit), None, INVALID, null_final, 0),
                ]
                for result_set, default, origin, note in (
                    (
                        declared_set,
                        True,
                        declared_source,
                        DECLARED_NOTE.format(
                            acuerdo=f"INE/CG{sumatoria}/2025",
                            actas=region_casillas.height,
                            inviable=inviable,
                        )
                        + (CANCELED.format(names=", ".join(canceled)) if canceled else ""),
                    ),
                    (
                        casilla_set,
                        False,
                        source,
                        CASILLA_NOTE.format(actas=region_casillas.height, marks=int(region_casillas["total"].sum())),
                    ),
                ):
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        status="active",
                        source_id=origin,
                        is_default=default,
                        completeness=1,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                set_rows.append((region, str(casilla_set), str(declared_set)))
                for winner in winners:
                    cand = by_name.get(fold(winner))
                    if cand is None:
                        raise ValueError(f"{office} {region}: winner {winner} is not on the ballot")
                    change = REPLACED.get((office, region, winner))
                    notes = None
                    if change is not None:
                        number, out = change
                        notes = (
                            f"Assigned by INE/CG{number}/2025 (31 August 2025) after the Sala Superior of the TEPJF "
                            f"annulled the vote in some casillas and changed the second place among women; it "
                            f"replaces {out}, whom INE/CG{sumatoria}/2025 had assigned."
                        )
                        cs.add(
                            "citation",
                            entity_id=declared_set,
                            source_id=acuerdo(number, compliance_url(number), "asignación en acatamiento"),
                            locator={"page": 51},
                        )
                    cs.add(
                        "outcome",
                        result_set_id=declared_set,
                        option_id=options[cand],
                        kind="elected",
                        seats=1,
                        notes=notes,
                    )
                cs.add("citation", entity_id=declared_set, source_id=validity_source, locator=None)
                ranked = sorted(finals.values(), reverse=True)
                print(office, region, "winners", [finals[by_name[fold(w)]] for w in winners], "top", ranked[:4])
            sets = pl.DataFrame(
                set_rows, schema=[("region", pl.Int64), ("set", pl.String), ("declared", pl.String)], orient="row"
            )
            optmap = pl.DataFrame(
                mapping, schema=[("region", pl.Int64), ("cand", pl.String), ("option_id", pl.String)], orient="row"
            )
            region_col = pl.col("circ") if regional else pl.lit(0, pl.Int64)
            units = locate(casillas)
            located = casillas.join(units, on=keys).with_columns(region=region_col).join(sets, on="region")
            joined = (
                votes.join(units, on=keys)
                .with_columns(region=region_col)
                .join(sets, on="region")
                .join(optmap, on=["region", "cand"])
            )
            if joined.height != votes.height:
                raise ValueError(f"{office}: {votes.height - joined.height} vote cells without a candidate")
            none = pl.lit(None, pl.String)
            parts = [
                located.filter(pl.col("registered") > 0).select(
                    "set", "unit_id", "channel", option_id=none, measure=pl.lit(REGISTERED), value="registered"
                ),
                located.select("set", "unit_id", "channel", option_id=none, measure=pl.lit(BALLOTS), value="voters"),
                located.select("set", "unit_id", "channel", option_id=none, measure=pl.lit(INVALID), value="null"),
                joined.filter(pl.col("votes") != 0).select(
                    "set", "unit_id", "channel", "option_id", measure=pl.lit(VOTES), value="votes"
                ),
            ]
            frames.append(
                pl.concat(
                    [
                        part.select(
                            result_set_id="set",
                            unit_id="unit_id",
                            option_id="option_id",
                            measure=pl.col("measure").cast(pl.Int16),
                            value=pl.col("value").cast(pl.Int64),
                            channel=pl.col("channel").cast(pl.Int16),
                        )
                        for part in parts
                    ]
                )
            )
            print(office, casillas.height, "casillas", joined.height, "cells")

        minted = [
            {
                "id": str(unit),
                "polity_id": str(mexico),
                "scheme_code": scheme,
                "code": code,
                "parent_id": None if parent is None else str(parent),
                "area_relation": relation,
                "area_id": str(area),
                "kind": kind,
            }
            for unit, (scheme, code, parent, relation, area, kind) in unit_rows.items()
            if unit in cs.minted
        ]
        if minted:
            cs.frame("reporting_unit", pl.DataFrame(minted, schema_overrides={"parent_id": pl.String}))
        summary = pl.DataFrame(
            summary_rows,
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
            pl.concat([summary, *frames]).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        print(persons, "candidates", len(minted), "new units")
        return cs.write()
