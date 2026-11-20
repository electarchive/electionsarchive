import json
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import polars as pl

from ea import package, raw
from ea.package import Draft
from ea.sources.mx import ine
from ea.sources.mx.ine import INVALID, VOTES
from ea.sources.mx.judicial_federal import fold
from ea.store import connect

SOURCE = "mx.judicial_local"
KEY = f"{SOURCE}/all"
VERSION = "1"
PARSED = raw.parsed(SOURCE)
DAY = date(2025, 6, 1)
SUMMARY = (
    "Mexican state judicial elections of 1 June 2025, at the finest unit each state electoral institute publishes "
    "(casilla in Nayarit, local electoral district in Yucatán), with the state counts and winners as declared"
)

IEPAC = "Instituto Electoral y de Participación Ciudadana de Yucatán (IEPAC)"
IEEN = "Instituto Estatal Electoral de Nayarit (IEEN)"
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
ROMAN |= {"XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15}
ORDINALS = {
    "PRIMERA": 1,
    "SEGUNDA": 2,
    "TERCERA": 3,
    "CUARTA": 4,
    "QUINTA": 5,
    "SEXTA": 6,
    "SÉPTIMA": 7,
    "OCTAVA": 8,
    "NOVENA": 9,
    "DÉCIMA": 10,
    "UNDÉCIMA": 11,
    "DUODÉCIMA": 12,
    "DECIMOTERCERA": 13,
    "DECIMOCUARTA": 14,
    "DECIMOQUINTA": 15,
}
SPANISH_ORDINALS = {number: word.capitalize() for word, number in ORDINALS.items()}

COURTS = {
    "tsj": ("Superior Court of Justice", "Tribunal Superior de Justicia", "superior-court"),
    "tdj": ("Judicial Discipline Tribunal", "Tribunal de Disciplina Judicial", "judicial-discipline-tribunal"),
    "jue": ("courts of first instance", "Juzgados de Primera Instancia", "judges"),
}
OFFICES = {
    "tsj": (
        "Magistrate of the Superior Court of Justice of {}",
        "Magistrada o Magistrado del Tribunal Superior de Justicia del Estado de {}",
    ),
    "tdj": (
        "Magistrate of the Judicial Discipline Tribunal of {}",
        "Magistrada o Magistrado del Tribunal de Disciplina Judicial del Estado de {}",
    ),
    "jue": ("Judge of first instance of {}", "Jueza o Juez de Primera Instancia del Estado de {}"),
}
# Nayarit's judges are elected by specialty: (code, English, Spanish, slug) for each specialty its count file heads.
SPECIALTIES = {
    "SISTEMA TRADICIONAL": ("tradicional", "traditional system", "Sistema Tradicional", "traditional-system"),
    "SISTEMA PENAL ACUSATORIO Y ORAL": (
        "penal",
        "adversarial criminal system",
        "Sistema Penal Acusatorio y Oral",
        "adversarial-criminal-system",
    ),
    "SISTEMA INTEGRAL DE JUSTICIA PENAL PARA ADOLESCENTES": (
        "adolescentes",
        "juvenile criminal justice system",
        "Sistema Integral de Justicia Penal para Adolescentes",
        "juvenile-justice-system",
    ),
    "TRIBUNAL LABORAL": ("laboral", "labor court", "Tribunal Laboral", "labor-court"),
}

YUCATAN: dict[str, Any] = {
    "code": "31",
    "english": "Yucatán",
    "spanish": "Yucatán",
    "districts": 21,
    "page": "https://www.iepac.mx/peepj-2025/computos",
    "viewer": "https://iepac.mx/{}-Candidatos.html",
    "title": "Cómputos distritales del Proceso Electoral Extraordinario 2025 para la elección de integrantes del Poder "
    "Judicial del Estado, por candidatura y distrito local",
    "acuerdo": "https://www.iepac.mx/public/documentos-del-consejo-general/acuerdos/iepac/2025/"
    "ACUERDO-C.G.{:03d}-2025.pdf",
    # Each magistracy (magistratura) is a separate race, reserved for women or for men; `women` lists the ones the
    # district count records head "Cargos de mujeres", the rest are "Cargos de hombres".
    "courts": {
        "tsj": {"viewer": "TSJ", "acuerdo": 64, "women": {1, 2, 7, 10, 13}},
        "tdj": {"viewer": "TDJ", "acuerdo": 65, "women": {1, 3, 5}},
    },
}
NAYARIT: dict[str, Any] = {
    "code": "18",
    "english": "Nayarit",
    "spanish": "Nayarit",
    "page": "https://ieenayarit.org/elecciones",
    "title": "Cómputos parciales y estatal por casilla del Proceso Electoral Extraordinario 2025 para la elección de "
    "integrantes del Poder Judicial del Estado",
    "workbook": "https://ieenayarit.org/PDF/elecciones/2025/{}25.xlsx",
    "candidates_page": "https://ieenayarit.org/eleccion-poder-judicial",
    "candidates_title": "Bases de datos de candidaturas registradas y electas del Proceso Electoral Extraordinario "
    "2025 del Poder Judicial del Estado",
    "registered": "https://ieenayarit.org/PDF/2025/Candidaturas/BD_CANDIDATURAS_REGISTRADAS_2025.xlsx",
    "elected": "https://ieenayarit.org/PDF/2025/Candidaturas/BD_CANDIDATURAS_ELECTAS_2025.xlsx",
    "courts": {"tsj": "MTSJ", "tdj": "MTDJ", "jue": "JJ"},
    "elections": {
        "tsj": "MAGISTRATURAS DEL TRIBUNAL SUPERIOR DE JUSTICIA DEL ESTADO DE NAYARIT",
        "tdj": "MAGISTRATURAS DEL TRIBUNAL DE DISCIPLINA JUDICIAL",
        "jue": "JUEZAS O JUECES",
    },
}
STATES = {"NAY": NAYARIT, "YUC": YUCATAN}
# Spellings the state count prints differently from the count viewer (folded, viewer first).
SPELLINGS = {("ANCONA XIU LIZBETH VANESA", "ANCONA XIU LIZBETH VANESSA")}
CASILLA = re.compile(r"(Básica|Contigua|Especial|Extraordinaria)(?: (\d+))?(?: \(Adicional (JAO|Estatal)\))?")
KINDS = {"Básica": "B", "Contigua": "C", "Especial": "S", "Extraordinaria": "E"}

PLURALITY = (
    "Under the 2025 reform of the state constitution, the magistrates of the {court} of {state} are elected by "
    "popular vote. Each magistracy (magistratura) is a separate race on the court's ballot, reserved for women or "
    "for men under the institute's gender parity criteria; voters mark one candidate in each, and the candidate with "
    "the most votes wins it."
)
BLOCK = (
    "Under the 2025 reform of the state constitution, the {court} of {state} is elected by popular vote in one "
    "statewide race{by}: voters mark candidates on the ballot, and the institute assigns the seats to the candidates "
    "with the most votes, applying its gender parity rules. The winners are stored as the institute declared them."
)
YUC_DECLARED = (
    "The state count (cómputo estatal) as the Consejo General of IEPAC declared it in acuerdo CG/{acuerdo:03d}/2025, "
    "the sum of the {districts} district count records. It gives {nulls:,} null votes for the {court} ballot as a "
    "whole, not by magistracy, so no invalid or total figure is stored for this contest."
)
YUC_DISTRICT = (
    "Votes by local electoral district from the institute's count viewer, whose {districts} districts add up to the "
    "state count of acuerdo CG/{acuerdo:03d}/2025 for every candidate. The institute publishes no count by casilla "
    "for this election, and the viewer gives no turnout or null votes by district."
)
NAY_STATE = (
    "The state count (cómputo estatal) of the institute's count workbook, the sum of its {rows:,} casilla rows. "
    "Votes are marks: each voter may mark several candidates, so votes add to more than the number of voters, and "
    "the workbook gives no turnout.{nulls}"
)
NAY_CASILLA = (
    "Votes by casilla from the institute's count workbook: {rows:,} rows, of which {extra} are additional count "
    "records the workbook lists beside a casilla (marked Adicional JAO or Adicional Estatal) and are kept as units of "
    "their own in the casilla's section.{nulls}"
)
PARITY = (
    "The institute's list of elected candidates seats {seated} in place of {passed}, who received more votes; the "
    "institute assigns the seats under its gender parity rules, and they are stored as declared."
)
NAY_NULLS = " Null votes are stored as invalid."
NAY_SHARED = (
    " The four judges' specialties share one ballot, whose {nulls:,} null votes the workbook gives for the ballot as "
    "a whole, so no invalid figure is stored for this contest."
)


def fetch() -> None:
    raw.fetch(SOURCE, YUCATAN["page"], suffix=".html")
    for court in YUCATAN["courts"].values():
        raw.fetch(SOURCE, YUCATAN["viewer"].format(court["viewer"]), suffix=".html")
        raw.fetch(SOURCE, YUCATAN["acuerdo"].format(court["acuerdo"]), suffix=".pdf")
    raw.fetch(SOURCE, NAYARIT["page"], suffix=".html")
    for stem in NAYARIT["courts"].values():
        raw.fetch(SOURCE, NAYARIT["workbook"].format(stem), suffix=".xlsx")
    raw.fetch(SOURCE, NAYARIT["registered"], suffix=".xlsx")
    raw.fetch(SOURCE, NAYARIT["elected"], suffix=".xlsx")


def viewer(url: str, districts: int) -> list[dict]:
    """The candidate table of an IEPAC count viewer: one row per candidate, with its votes by local district."""
    text = raw.cached(SOURCE, url).path.read_text(encoding="utf-8")
    [widget] = re.findall(r'<script type="application/json" data-for="[^"]+">(.*?)</script>', text)
    data = json.loads(widget)["x"]["tag"]["attribs"]["data"]
    rows = []
    for i, label in enumerate(data["nombre"]):
        by_district = {d: int(data[f"{d:02d}"][i]) for d in range(1, districts + 1)}
        if sum(by_district.values()) != int(data["Total"][i]):
            raise ValueError(f"{url}: {label} districts add to {sum(by_district.values())}, not {data['Total'][i]}")
        rows.append({"seat": ROMAN[data["magistratura"][i]], "name": " ".join(label.split()), "votes": by_district})
    return rows


def pdf_text(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout


def declared(url: str) -> tuple[list[dict], int, int]:
    """The state count table of an IEPAC acuerdo: its candidate rows by magistracy, the null votes, and the page
    the table starts on."""
    rows: list[dict] = []
    seat = nulls = first = None
    for page, text in enumerate(pdf_text(url).split("\f"), 1):
        for line in text.splitlines():
            heading = re.fullmatch(r"\s*MAGISTRATURA\s+(\S+)\s*", line)
            if heading is not None and heading.group(1) in ORDINALS:
                seat = ORDINALS[heading.group(1)]
                first = first or page
                continue
            found = re.fullmatch(r"\s*VOTOS NULOS\s+([\d,]+)\s*", line)
            if found is not None and seat is not None:
                nulls = int(found.group(1).replace(",", ""))
                break
            row = re.fullmatch(r"\s+(\d{2})\s+(\S.*?)\s{2,}([\d,]+)\s*", line)
            if row is not None and seat is not None:
                rows.append({"seat": seat, "name": row.group(2), "votes": int(row.group(3).replace(",", ""))})
        if nulls is not None:
            break
    if nulls is None or first is None or not rows:
        raise ValueError(f"{url}: no state count table")
    return rows, nulls, first


def yucatan(contests: list[dict], candidates: list[dict], fine: list[dict]) -> None:
    for court, config in YUCATAN["courts"].items():
        counted = viewer(YUCATAN["viewer"].format(config["viewer"]), YUCATAN["districts"])
        stated, nulls, page = declared(YUCATAN["acuerdo"].format(config["acuerdo"]))
        if len(counted) != len(stated):
            raise ValueError(f"YUC {court}: {len(counted)} candidates in the viewer, {len(stated)} declared")
        positions: dict[int, int] = {}
        race: dict[int, list[dict]] = {}
        for mine, theirs in zip(counted, stated, strict=True):
            total = sum(mine["votes"].values())
            names = (fold(mine["name"]), fold(theirs["name"]))
            if mine["seat"] != theirs["seat"] or total != theirs["votes"]:
                raise ValueError(f"YUC {court}: {mine['name']} {total} against {theirs}")
            if names[0] != names[1] and names not in SPELLINGS:
                raise ValueError(f"YUC {court}: {mine['name']} is {theirs['name']} in the state count")
            seat = mine["seat"]
            position = positions[seat] = positions.get(seat, 0) + 1
            row = {
                "iso": "YUC",
                "court": court,
                "contest": str(seat),
                "position": position,
                "name": mine["name"],
                "gender": "female" if seat in config["women"] else "male",
                "votes": total,
            }
            race.setdefault(seat, []).append(row)
            fine += [
                {
                    "iso": "YUC",
                    "court": court,
                    "contest": str(seat),
                    "unit": f"{district:02d}",
                    "position": position,
                    "measure": VOTES,
                    "votes": value,
                }
                for district, value in mine["votes"].items()
            ]
        for seat, rows in race.items():
            ranked = sorted(row["votes"] for row in rows)
            if ranked[-1] == ranked[-2]:
                raise ValueError(f"YUC {court} {seat}: tie for first place")
            candidates += [row | {"elected": row["votes"] == ranked[-1]} for row in rows]
            contests.append(
                {
                    "iso": "YUC",
                    "court": court,
                    "contest": str(seat),
                    "magnitude": 1,
                    "nulls": None,
                    "shared": nulls,
                    "page": page,
                    "rows": YUCATAN["districts"],
                    "extra": 0,
                }
            )
        print("YUC", court, len(race), "magistracies", len(counted), "candidates", nulls, "null votes")


def casilla(kind: str) -> str:
    match = CASILLA.fullmatch(kind)
    if match is None:
        raise ValueError(f"unknown casilla kind {kind!r}")
    letter = KINDS[match.group(1)]
    # The judges' workbook numbers the one special casilla 0; the others number it 1.
    number = max(int(match.group(2) or 1), 1)
    return f"{letter}{number}" + (f"-{match.group(3).upper()}" if match.group(3) else "")


def nayarit(contests: list[dict], candidates: list[dict], fine: list[dict]) -> None:
    sheets = pl.read_excel(
        raw.cached(SOURCE, NAYARIT["registered"]).path, sheet_name="CONCENTRADO", infer_schema_length=0
    )
    elected = pl.read_excel(
        raw.cached(SOURCE, NAYARIT["elected"]).path, sheet_name="CONCENTRADO", infer_schema_length=0
    )

    def person(row: dict) -> str:
        return fold(f"{row['NOMBRE/S']} {row['APELLIDO PATERNO']} {row['APELLIDO MATERNO']}")

    for court, stem in NAYARIT["courts"].items():
        election = NAYARIT["elections"][court]
        genders = {
            person(row): {"MUJER": "female", "HOMBRE": "male"}[row["SEXO"]]
            for row in sheets.filter(pl.col("ELECCIÓN") == election).iter_rows(named=True)
        }
        winners = {person(row) for row in elected.filter(pl.col("ELECCIÓN") == election).iter_rows(named=True)}
        book = pl.read_excel(
            raw.cached(SOURCE, NAYARIT["workbook"].format(stem)).path,
            sheet_id=0,
            has_header=False,
            infer_schema_length=0,
        )
        summary = book.pop("ConcentradoMR").rows()
        head = next(i for i, row in enumerate(summary) if row[0] == "Municipio")
        header = summary[head]
        nulls_at, total_at = header.index("Votos nulos"), header.index("Total")
        names = [" ".join(str(c).split()) for c in header[2:nulls_at]]
        # The judges' workbook heads each specialty's first column on the row above the names.
        groups: list[tuple[str, int]] = [("", 2)]
        if court == "jue":
            groups = [(str(label), i) for i, label in enumerate(summary[head - 1]) if label is not None]
        bounds = [
            (label, start, next_start) for (label, start), (_, next_start) in zip(groups, [*groups[1:], ("", nulls_at)])
        ]
        state_row = next(row for row in summary if row[0] == "Total")
        stated = [int(c) for c in state_row[2:nulls_at]]
        stated_nulls = int(state_row[nulls_at])
        sums = [0] * len(names)
        null_sum = rows = extra = 0
        for municipality, frame in book.items():
            for row in frame.rows():
                if row[0] is None or not str(row[0]).isdigit():
                    continue
                section, tail = int(row[0]), casilla(str(row[1]))
                marks = [int(c) for c in row[2:nulls_at]]
                nulls = int(row[nulls_at])
                if sum(marks) + nulls != int(row[total_at]):
                    raise ValueError(f"NAY {court} {municipality} {section} {tail}: marks do not add to the total")
                unit = f"{section:04d}-{tail}"
                rows += 1
                extra += "-" in tail
                null_sum += nulls
                for i, value in enumerate(marks):
                    sums[i] += value
                for label, start, end in bounds:
                    contest = SPECIALTIES[label][0] if label else ""
                    fine += [
                        {
                            "iso": "NAY",
                            "court": court,
                            "contest": contest,
                            "unit": unit,
                            "position": i - start + 1,
                            "measure": VOTES,
                            "votes": marks[i - 2],
                        }
                        for i in range(start, end)
                        if marks[i - 2]
                    ]
                    if court != "jue":
                        fine.append(
                            {
                                "iso": "NAY",
                                "court": court,
                                "contest": contest,
                                "unit": unit,
                                "position": 0,
                                "measure": INVALID,
                                "votes": nulls,
                            }
                        )
        if sums != stated or null_sum != stated_nulls:
            raise ValueError(f"NAY {court}: casillas add to {sums} and {null_sum}, the state count prints {stated}")
        for label, start, end in bounds:
            contest = SPECIALTIES[label][0] if label else ""
            race = [
                {
                    "iso": "NAY",
                    "court": court,
                    "contest": contest,
                    "position": i - start + 1,
                    "name": names[i - 2],
                    "gender": genders[fold(names[i - 2])],
                    "votes": stated[i - 2],
                    "elected": fold(names[i - 2]) in winners,
                }
                for i in range(start, end)
            ]
            seats = sum(1 for i in range(start, end) if fold(names[i - 2]) in winners)
            candidates += race
            contests.append(
                {
                    "iso": "NAY",
                    "court": court,
                    "contest": contest,
                    "magnitude": seats,
                    "nulls": None if court == "jue" else stated_nulls,
                    "shared": stated_nulls if court == "jue" else None,
                    "page": None,
                    "rows": rows,
                    "extra": extra,
                }
            )
            print("NAY", court, contest or "-", len(race), "candidates", seats, "seats")
        found = {fold(name) for name in names}
        if winners - found:
            raise ValueError(f"NAY {court}: winners not on the ballot: {sorted(winners - found)}")
        print("NAY", court, rows, "casilla rows", extra, "additional", stated_nulls, "null votes")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    contests: list[dict] = []
    candidates: list[dict] = []
    fine: list[dict] = []
    yucatan(contests, candidates, fine)
    nayarit(contests, candidates, fine)
    pl.DataFrame(contests, schema_overrides={"nulls": pl.Int64, "shared": pl.Int64, "page": pl.Int64}).write_parquet(
        PARSED / "contests.parquet"
    )
    pl.DataFrame(candidates).write_parquet(PARSED / "candidates.parquet")
    pl.DataFrame(fine).write_parquet(PARSED / "fine.parquet")
    print(build())


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package mx.judicial_local/all from the parsed tables. It reads the database, through Draft, for the
    states' areas and units and for the keys the changeset path mints, so elections and offices share their ids."""
    with connect() as conn:
        cs = Draft(conn, pipeline=SOURCE, version=VERSION, summary=SUMMARY, polity="MX")
        for scheme in ("mx.entidad", "mx.distrito_local", "mx.seccion", "mx.casilla"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError(f"publish {scheme} before {SOURCE}")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def source(publisher: str, title: str, url: str, files: list[str]) -> UUID:
            id = cs.source(publisher, title)
            cs.add("source", id=id, kind="official_body", publisher=publisher, title=title, url=url, license=ine.PUBLIC)
            for file in files:
                cs.artifact(raw.cached(SOURCE, file), id)
            return id

        mexico = cs.polity("MX")
        contests = pl.read_parquet(PARSED / "contests.parquet")
        candidates = pl.read_parquet(PARSED / "candidates.parquet")
        fine = pl.read_parquet(PARSED / "fine.parquet")
        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'mx.seccion'"
            ).fetchall()
        )
        day = DAY.isoformat()
        units: list[dict[str, str]] = []
        values: list[tuple[str, str, str | None, int, int, int]] = []
        for iso, state in STATES.items():
            code, english, spanish = state["code"], state["english"], state["spanish"]
            polity = cs.polity(f"MX-{iso}")
            election = cs.key("mx.election", f"{day}:{iso}")
            cs.add("election", id=election, polity_id=polity, kind="general", date_from=DAY, date_to=DAY, status="held")
            name(election, f"{DAY.year} {english} judicial election")
            name(
                election,
                f"Proceso Electoral Extraordinario {DAY.year} para la elección de integrantes del Poder Judicial del "
                f"Estado de {spanish}",
                "es",
                "native",
            )
            path = f"mx/{iso.lower()}/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            state_area, state_unit = cs.area("mx.entidad", code), cs.unit("mx.entidad", code)

            fine_units: dict[str, str] = {}
            if iso == "YUC":
                counts = source(
                    IEPAC,
                    state["title"],
                    state["page"],
                    [state["page"], *(state["viewer"].format(c["viewer"]) for c in state["courts"].values())],
                )
                acuerdos = {
                    court: source(
                        IEPAC,
                        f"Acuerdo CG/{config['acuerdo']:03d}/2025 del Consejo General: cómputo estatal, constancias "
                        f"de mayoría y declaración de validez de la elección de Magistraturas del {COURTS[court][1]}",
                        state["acuerdo"].format(config["acuerdo"]),
                        [state["acuerdo"].format(config["acuerdo"])],
                    )
                    for court, config in state["courts"].items()
                }
                for district in range(1, state["districts"] + 1):
                    unit_code = f"{DAY.year}:{code}-{district:02d}"
                    unit = cs.unit("mx.distrito_local", unit_code)
                    fine_units[f"{district:02d}"] = str(unit)
                    units.append(
                        {
                            "id": str(unit),
                            "polity_id": str(mexico),
                            "scheme_code": "mx.distrito_local",
                            "code": unit_code,
                            "parent_id": str(state_unit),
                            "area_relation": "equals",
                            "area_id": str(cs.area("mx.distrito_local", f"{code}{district:02d}")),
                            "kind": "standard",
                        }
                    )
            else:
                counts = source(
                    IEEN,
                    state["title"],
                    state["page"],
                    [state["page"], *(state["workbook"].format(stem) for stem in state["courts"].values())],
                )
                declarer = source(
                    IEEN, state["candidates_title"], state["candidates_page"], [state["registered"], state["elected"]]
                )
                federal: dict[str, str] = {}
                for unit_code, unit in conn.execute(
                    "select code, id from reporting_unit where scheme_code = 'mx.casilla' and code like %s",
                    (f"2025PJ:{code}-%",),
                ).fetchall():
                    _, _, section, tail = unit_code.split(":", 1)[1].split("-")
                    if tail.startswith("A"):
                        continue
                    if f"{section}-{tail}" in federal:
                        raise ValueError(f"{unit_code}: two federal judicial units for one casilla")
                    federal[f"{section}-{tail}"] = str(unit)
                for key in sorted(set(fine.filter(pl.col("iso") == iso)["unit"])):
                    if key in federal:
                        fine_units[key] = federal[key]
                        continue
                    unit_code = f"{DAY.year}:{code}-pj-{key}"
                    unit = cs.unit("mx.casilla", unit_code)
                    fine_units[key] = str(unit)
                    area = sections.get(f"{code}{key[:4]}") or state_area
                    units.append(
                        {
                            "id": str(unit),
                            "polity_id": str(mexico),
                            "scheme_code": "mx.casilla",
                            "code": unit_code,
                            "parent_id": str(state_unit),
                            "area_relation": "within",
                            "area_id": str(area),
                            "kind": "standard",
                        }
                    )
                print(iso, sum(1 for key in fine_units if key in federal), "casillas share a federal unit")

            offices: dict[str, UUID] = {}
            systems: dict[str, UUID] = {}
            for race in contests.filter(pl.col("iso") == iso).iter_rows(named=True):
                court, contest_code = race["court"], race["contest"]
                court_en, court_es, tail = COURTS[court]
                if court not in offices:
                    office = offices[court] = cs.key("mx.office", f"{court}:{iso}")
                    cs.add("office", id=office, polity_id=polity, kind="other")
                    name(office, OFFICES[court][0].format(english))
                    name(office, OFFICES[court][1].format(spanish), "es", "native")
                    system = systems[court] = cs.key("mx.electoral_system", f"judicial-{court}:{iso}")
                    formula = "fptp" if iso == "YUC" else "block"
                    cs.add("electoral_system", id=system, polity_id=polity, rules={"formula": formula}, rules_version=1)
                    name(system, f"{english} judicial election, {court_en}")
                    body = (
                        PLURALITY.format(court=court_es, state=english)
                        if formula == "fptp"
                        else BLOCK.format(
                            court=court_es, state=english, by=", one race per specialty" if court == "jue" else ""
                        )
                    )
                    cs.add("note", entity_id=system, kind="methodology", body_md=body)
                label = f"{day}:{iso}:{court}" + (f":{contest_code}" if contest_code else "")
                if iso == "YUC":
                    seat = int(contest_code)
                    contest_en = f"{DAY.year} election of the {english} {court_en}, magistracy {seat}"
                    contest_es = (
                        f"Elección de la Magistratura {SPANISH_ORDINALS[seat]} del {court_es} de {spanish}, 2025"
                    )
                    contest_slug = f"{path}/{tail}-{seat}"
                elif court == "jue":
                    _, specialty_en, specialty_es, specialty_slug = next(
                        v for v in SPECIALTIES.values() if v[0] == contest_code
                    )
                    contest_en = f"{DAY.year} election of {english} judges, {specialty_en}"
                    contest_es = f"Elección de Juezas y Jueces de {spanish}, {specialty_es}, 2025"
                    contest_slug = f"{path}/{tail}-{specialty_slug}"
                else:
                    contest_en = f"{DAY.year} election of the {english} {court_en}"
                    contest_es = f"Elección de Magistraturas del {court_es} de {spanish}, 2025"
                    contest_slug = f"{path}/{tail}"
                contest = cs.key("mx.contest", label)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=offices[court],
                    district_area_id=state_area,
                    magnitude=race["magnitude"],
                    electoral_system_id=systems[court],
                    default_result_kind="counted",
                )
                name(contest, contest_en)
                name(contest, contest_es, "es", "native")
                cs.add("slug", path=contest_slug, entity_id=contest)

                if iso == "YUC":
                    acuerdo = YUCATAN["courts"][court]["acuerdo"]
                    top = (
                        cs.key("mx.result_set", f"{label}:declared"),
                        acuerdos[court],
                        YUC_DECLARED.format(
                            acuerdo=acuerdo, districts=state["districts"], nulls=race["shared"], court=court_es
                        ),
                    )
                    bottom = (
                        cs.key("mx.result_set", f"{label}:distrito"),
                        counts,
                        YUC_DISTRICT.format(districts=state["districts"], acuerdo=acuerdo),
                    )
                    winners_source, locator = acuerdos[court], {"page": race["page"]}
                else:
                    nulls = NAY_NULLS if race["nulls"] is not None else NAY_SHARED.format(nulls=race["shared"])
                    top = (
                        cs.key("mx.result_set", f"{label}:state"),
                        counts,
                        NAY_STATE.format(rows=race["rows"], nulls=nulls),
                    )
                    bottom = (
                        cs.key("mx.result_set", f"{label}:casilla"),
                        counts,
                        NAY_CASILLA.format(rows=race["rows"], extra=race["extra"], nulls=nulls),
                    )
                    winners_source, locator = declarer, {"sheet": "CONCENTRADO"}
                for (result_set, origin, note), default in ((top, True), (bottom, False)):
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
                top_set, bottom_set = str(top[0]), str(bottom[0])
                cs.add("citation", entity_id=top[0], source_id=winners_source, locator=locator)

                pool = candidates.filter(
                    (pl.col("iso") == iso) & (pl.col("court") == court) & (pl.col("contest") == contest_code)
                ).sort("position")
                options: dict[int, str] = {}
                for row in pool.iter_rows(named=True):
                    key = f"{label}:{row['position']}"
                    option = cs.key("mx.option", key)
                    options[row["position"]] = str(option)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_position=row["position"],
                        ballot_label=row["name"],
                    )
                    person = cs.key("mx.person", key)
                    cs.add("person", id=person, gender=row["gender"])
                    name(person, row["name"], "es")
                    cs.add(
                        "candidacy",
                        id=cs.key("mx.candidacy", key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=row["name"],
                        is_independent=True,
                    )
                    values.append((top_set, str(state_unit), str(option), VOTES, row["votes"], 0))
                    if row["elected"]:
                        cs.add("outcome", result_set_id=top[0], option_id=option, kind="elected", seats=1)
                if race["nulls"] is not None:
                    values.append((top_set, str(state_unit), None, INVALID, race["nulls"], 0))
                for row in fine.filter(
                    (pl.col("iso") == iso) & (pl.col("court") == court) & (pl.col("contest") == contest_code)
                ).iter_rows(named=True):
                    if row["votes"] or row["measure"] == INVALID:
                        values.append(
                            (
                                bottom_set,
                                fine_units[row["unit"]],
                                options[row["position"]] if row["position"] else None,
                                row["measure"],
                                row["votes"],
                                0,
                            )
                        )
                elected = pool.filter(pl.col("elected"))
                top_n = set(pool.sort("votes", descending=True)["name"][: elected.height])
                seated, passed = set(elected["name"]) - top_n, top_n - set(elected["name"])
                if seated:
                    cs.add(
                        "note",
                        entity_id=top[0],
                        kind="caveat",
                        body_md=PARITY.format(seated=", ".join(sorted(seated)), passed=", ".join(sorted(passed))),
                    )
                print(iso, label, "won by", ", ".join(elected["name"]), list(elected["votes"]))

        minted = [unit for unit in units if UUID(unit["id"]) in cs.minted]
        if minted:
            cs.frame("reporting_unit", pl.DataFrame(minted))
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
