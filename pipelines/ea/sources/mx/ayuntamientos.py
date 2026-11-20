import os
import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import fastexcel
import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.names import display
from ea.package import Draft
from ea.sources.mx import ine
from ea.store import DATA, connect

SOURCE = "mx.ayuntamientos"
VERSION = "1"
KEY = f"{SOURCE}/all"
PARSED = DATA / "parsed" / SOURCE
IEPC_DGO = "Instituto Electoral y de Participación Ciudadana del Estado de Durango (IEPC)"
IEPC_DGO_PAGE = "https://www.iepcdurango.mx/instituto_electoral/estadistica"
IEPC_LICENSE = "Public information the institute publishes for download; no license stated"
ELECTIONS: dict[str, dict] = {
    "DUR:2022": {
        "state": "10",
        "iso": "MX-DUR",
        "day": date(2022, 6, 5),
        "until": date(2025, 6, 1),
        "workbook": (
            (
                "https://www.iepcdurango.mx/IEPC_DURANGO/documentos/2022/estadistica/"
                "Eleccion_de_Proceso_Electoral_2021_2022_Ayuntamientos.xlsb"
            ),
            (
                "Elección de presidencias municipales, sindicaturas y regidurías 2021-2022: resultados por casilla y "
                "sección (Sistema de Cómputos del Proceso Electoral 2021-2022)"
            ),
        ),
        "members": (
            (
                "https://www.iepcdurango.mx/IEPC_DURANGO/documentos/2022/proceso_electoral_2021_2022/"
                "Candidaturas_Ganadoras_15_06_2022.pdf"
            ),
            (
                "Elección de presidencias municipales, sindicaturas y regidurías 2022: integración de las fórmulas "
                "electas por los partidos políticos y candidatura independiente (15 June 2022)"
            ),
        ),
        "law": (
            (
                "https://www.iepcdurango.mx/IEPC_DURANGO/documentos/2026/normatividad/221026/"
                "LEY_DE_INSTITUCIONES_Y_PROCEDIMIENTOS_ELECTORALES.pdf"
            ),
            "Ley de Instituciones y Procedimientos Electorales para el Estado de Durango",
            267,
        ),
        "fixes": {("MORENA+PVEM+RSPD", 2): "MORENA+PT+RSPD"},
        "labels": {30: {11: "CAND. NO REG.", 12: "VOTOS NULOS"}},
        "unnamed": {35},
    },
}
TOKENS = {"MOR": "MORENA", "RSP": "RSPD"}
PARTIES = {
    **{abbrev: ine.PARTIES[abbrev] for abbrev in ("PAN", "PRI", "PRD", "PVEM", "PT", "MC", "MORENA")},
    "RSPD": ("Q100367185", "Redes Sociales Progresistas", "Progressive Social Networks"),
}
INDEPENDENT = "IND"
GENDERS = {"MUJER": "female", "HOMBRE": "male"}
MEMBER = re.compile(r"(PRESIDENCIA|SINDICATURA|(\d+)ª\s*REGIDUR[IÍ]A)\s+(\S+)\s+(MUJER|HOMBRE)\s+(.+?)(?:\s{2,}|$)")
MR_RULES = {"formula": "fptp"}
RP_RULES = {"formula": "hare", "threshold": {"percent": 3, "level": "party", "scope": "contest"}}
MR_SYSTEM = (
    "Each municipality elects a planilla of a municipal president and a síndico, with their substitutes, by "
    "plurality of the votes cast in the municipality, with no runoff. Parties in a coalition or common candidacy "
    "run one planilla."
)
RP_SYSTEM = (
    "The regidurías of each ayuntamiento are all filled by proportional representation (Durango electoral law, "
    "art. 267). A party takes part if it ran a planilla for president and síndico and won at least 3 percent of "
    "the valid municipal vote. The votes of the parties below the threshold are deducted, the remainder is "
    "divided by the number of regidurías to give a common factor (factor común), each party takes a seat for each "
    "whole factor in its vote, and the seats left go by largest remainder (resto mayor). Seats are filled from "
    "each party's registered list, subject to the parity rules."
)
MR_METHOD = (
    "Municipal totals as the institute's count workbook prints them, which equal the sum of its polling-station "
    "(casilla) counts unless a note says otherwise. Votes marked for two or more "
    "parties of one coalition count for the coalition's planilla. The winner is the planilla with the most votes, "
    "which matches the elected president and síndico in the institute's list of elected members."
)
CASILLA_NOTE = (
    "Polling-station results from the institute's municipal count workbook. Each line is a column of the "
    "workbook: a party, or a combination of the parties of one coalition that a voter marked."
)
RP_METHOD = (
    "Municipal vote by party as the count workbook prints it after splitting the votes marked for several "
    "parties of a coalition among them, and the regidurías by party from the institute's list of elected members, "
    "which marks each seat as won on the common factor or by largest remainder."
)
RSPD_NOTE = (
    "RSPD in the institute's files is Redes Sociales Progresistas, which lost its national registration after "
    "2021 and kept its accreditation in Durango."
)
REGISTERED, BALLOTS, VALID, INVALID, VOTES = ine.REGISTERED, ine.BALLOTS, ine.VALID, ine.INVALID, ine.VOTES


def config() -> tuple[str, dict]:
    key = os.environ.get("MX_AYUNTAMIENTOS", "DUR:2022")
    return key, ELECTIONS[key]


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z]+", " ", plain.replace("MA.", "MARIA")).strip()


def number(value: object) -> int:
    if value is None or str(value).strip() == "":
        return 0
    return int(float(str(value)))


def column_key(label: str) -> str | None:
    text = " ".join(label.split()).upper()
    if text.startswith("CAND") and "IND" in text:
        return INDEPENDENT
    if "NO REG" in text:
        return "unregistered"
    if "NULOS" in text:
        return "null"
    if any(word in text for word in ("DIFERENCIA", "HACEMOS", "VA POR", "VOTACI", "LISTA", "PART.", "PARTICIPACI")):
        return None
    for abbrev, (_, native, _) in PARTIES.items():
        if fold(text) == fold(native):
            return abbrev
    members = {TOKENS.get(token, token) for token in re.split(r"[\s\-]+", text) if token}
    unknown = members - set(PARTIES)
    if unknown:
        raise ValueError(f"column {label!r}: {sorted(unknown)}")
    return "+".join(sorted(members))


def is_casilla(row: tuple) -> bool:
    return row[0] is not None and str(row[0]).replace(".0", "").isdigit() and row[2] is not None


def read_sheet(
    rows: list[tuple], fixes: dict[tuple[str, int], str], labels: dict[int, str], unnamed: bool, town: int
) -> dict[str, list[dict]]:
    header = next(i for i, row in enumerate(rows) if row[0] == "NÚMERO")
    total_at = next(
        j for i in range(header - 3, header) for j, cell in enumerate(rows[i]) if cell == "VOTACIÓN TOTAL EMITIDA"
    )
    columns: dict[int, str] = {}
    seen: dict[str, int] = {}
    for j, cell in enumerate(rows[header]):
        if j < 4 or cell is None or (key := column_key(str(cell))) is None:
            continue
        seen[key] = seen.get(key, 0) + 1
        key = fixes.get((key, seen[key]), key)
        if key in columns.values():
            raise ValueError(f"municipio {town}: column {key} twice")
        columns[j] = key
    out: dict[str, list[dict]] = {
        "casillas": [],
        "votes": [],
        "planillas": [],
        "parties": [],
        "annulled": [],
        "totals": [],
    }
    headers = [i for i, row in enumerate(rows) if row[0] == "NÚMERO"]
    annulled: set[tuple[int, str]] = set()
    for start in headers[1:-1]:
        title = " ".join(str(cell) for row in rows[start - 6 : start] for cell in row if cell is not None)
        if "NULIDAD" not in title:
            continue
        ruling = re.search(r"SENTENCIA ([A-Z]+-[A-Z]+-\d+/\d{4})", title)
        for row in rows[start + 1 :]:
            if not is_casilla(row):
                break
            annulled.add((number(row[1]), re.sub(r"\s+", "", str(row[2]))))
            out["annulled"].append(
                {
                    "town": town,
                    "section": number(row[1]),
                    "casilla": re.sub(r"\s+", "", str(row[2])),
                    "ruling": ruling.group(1) if ruling else None,
                }
            )
    for row in rows[header + 1 : headers[1]]:
        if not is_casilla(row) or (number(row[1]), re.sub(r"\s+", "", str(row[2]))) in annulled:
            continue
        section, casilla = number(row[1]), re.sub(r"\s+", "", str(row[2]))
        total = number(row[total_at])
        counted = 0
        for j, key in columns.items():
            votes = number(row[j])
            counted += votes
            out["votes"].append({"town": town, "section": section, "casilla": casilla, "column": key, "votes": votes})
        out["casillas"].append(
            {
                "town": town,
                "section": section,
                "casilla": casilla,
                "total": total,
                "counted": counted,
                "registered": number(row[total_at + 1]),
            }
        )
    if len(headers) > 2:
        block = headers[-2]
        seen = {}
        for j, cell in enumerate(rows[block]):
            if j < 4 or cell is None or (key := column_key(str(cell))) is None:
                continue
            seen[key] = seen.get(key, 0) + 1
            out["totals"].append(
                {"town": town, "column": fixes.get((key, seen[key]), key), "votes": number(rows[block + 1][j])}
            )
    summary = headers[-1]
    totals = rows[summary + 1]
    known = set(TOKENS) | set(PARTIES) | {"CAND.", "INDEP.", "NO", "REG."}
    natives = {fold(native) for _, native, _ in PARTIES.values()}
    for j, (cell, total) in enumerate(zip(rows[summary], totals, strict=True)):
        cell = labels.get(j, cell)
        if j < 4 or cell is None:
            continue
        parts = [line.strip() for line in str(cell).split("\n") if line.strip()]
        lines = [
            line
            for line in parts
            if len(line.split()) > 1
            and not set(line.split()) <= known
            and fold(line) not in natives
            and not re.search(r"NO REG|NULOS", " ".join(str(cell).split()).upper())
            and not re.search(r"HACEMOS|VA POR", line.upper())
        ]
        label = " ".join(line for line in parts if line not in lines)
        if lines:
            out["planillas"].append(
                {
                    "town": town,
                    "president": None if unnamed else lines[0],
                    "sindic": None if unnamed or len(lines) < 2 else lines[1],
                    "votes": number(total),
                }
            )
        if (
            label
            and (key := column_key(label)) is not None
            and not any(p["party"] == key for p in out["parties"])
            and (key not in (INDEPENDENT, "unregistered", "null") or not lines)
        ):
            out["parties"].append({"town": town, "party": key, "votes": number(total)})
    return out


def read_members(path: Path) -> list[dict]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True, text=True).stdout
    members, town = [], 0
    for line in text.splitlines():
        match = MEMBER.search(line)
        if match is None:
            continue
        office, seat, party, gender, person = match.groups()
        if office == "PRESIDENCIA":
            town += 1
        members.append(
            {
                "town": town,
                "office": "regiduria" if seat else office.lower(),
                "seat": int(seat) if seat else 0,
                "party": party,
                "gender": GENDERS[gender],
                "name": person.strip(),
                "how": line.rstrip().rsplit("  ", 1)[-1].strip(),
            }
        )
    return members


def parse() -> None:
    key, election = config()
    workbook = raw.fetch(SOURCE, election["workbook"][0], suffix=".xlsb")
    listing = raw.fetch(SOURCE, election["members"][0], suffix=".pdf")
    reader = fastexcel.read_excel(str(workbook.path))
    frames: dict[str, list[dict]] = {
        "casillas": [],
        "votes": [],
        "planillas": [],
        "parties": [],
        "annulled": [],
        "totals": [],
    }
    for sheet in reader.sheet_names:
        match = re.match(r"(\d+)\.-", sheet)
        if match is None:
            continue
        rows = list(reader.load_sheet(sheet, header_row=None).to_polars().iter_rows())
        town = int(match.group(1))
        parts = read_sheet(rows, election["fixes"], election["labels"].get(town, {}), town in election["unnamed"], town)
        for name, part in parts.items():
            frames[name].extend(part)
    members = read_members(listing.path)
    out = PARSED / key.replace(":", "_")
    out.mkdir(parents=True, exist_ok=True)
    for name, part in frames.items():
        pl.DataFrame(part).write_parquet(out / f"{name}.parquet")
    pl.DataFrame(members).write_parquet(out / "members.parquet")
    print(key, {name: len(part) for name, part in frames.items()}, "members", len(members))
    build()


def groups_of(present: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    for column in sorted(present, key=lambda c: -len(c.split("+"))):
        members = set(column.split("+"))
        for group in groups:
            if members <= set(group[0].split("+")):
                group.append(column)
                break
        else:
            groups.append([column])
    return groups


def emit() -> None:
    package.run("load", [KEY])


def build() -> Path:
    """Write the package mx.ayuntamientos/all from the parsed tables. It reads the database through Draft, so the
    rows it rebuilds keep the ids the changeset path gave them."""
    key, election = config()
    state, day, iso = election["state"], election["day"], election["iso"]
    year = day.year
    folder = PARSED / key.replace(":", "_")
    casillas = pl.read_parquet(folder / "casillas.parquet")
    votes = pl.read_parquet(folder / "votes.parquet")
    planillas = pl.read_parquet(folder / "planillas.parquet")
    party_votes = pl.read_parquet(folder / "parties.parquet")
    printed_totals = pl.read_parquet(folder / "totals.parquet")
    annulled = pl.read_parquet(folder / "annulled.parquet")
    members = pl.read_parquet(folder / "members.parquet")
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"{key}: municipal president elections by polling station and regidurías by party from the "
            "state electoral institute",
            polity="MX",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def source(title: str, url: str, publisher: str = IEPC_DGO) -> UUID:
            made = cs.source(publisher, title)
            cs.add(
                "source",
                id=made,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url,
                license=IEPC_LICENSE,
                license_url=IEPC_DGO_PAGE,
            )
            cs.artifact(raw.cached(SOURCE, url), made)
            return made

        mexico = cs.polity("MX")
        parent = cs.polity(iso)
        counts = source(election["workbook"][1], election["workbook"][0])
        listing = source(election["members"][1], election["members"][0])
        law_url, law_title, article = election["law"]
        law = cs.source(IEPC_DGO, law_title)
        cs.add(
            "source",
            id=law,
            kind="official_body",
            publisher=IEPC_DGO,
            title=law_title,
            url=law_url,
            license=ine.PUBLIC,
        )
        mr_system = cs.key("mx.electoral_system", "ayuntamiento-mr")
        if mr_system in cs.minted:
            cs.add("electoral_system", id=mr_system, polity_id=mexico, rules=MR_RULES, rules_version=1)
            name(mr_system, "Mexican municipal planilla plurality system")
            cs.add("note", entity_id=mr_system, kind="methodology", body_md=MR_SYSTEM)
        rp_system = cs.key("mx.electoral_system", f"ayuntamiento-rp:{iso}")
        if rp_system in cs.minted:
            cs.add("electoral_system", id=rp_system, polity_id=parent, rules=RP_RULES, rules_version=1)
            name(rp_system, "Durango regidurías proportional system")
            cs.add("note", entity_id=rp_system, kind="methodology", body_md=RP_SYSTEM)
            cs.add("citation", entity_id=rp_system, source_id=law, locator={"article": article})

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
                if abbrev == "RSPD":
                    cs.add("note", entity_id=parties[abbrev], kind="caveat", body_md=RSPD_NOTE)
            return parties[abbrev]

        alliances: dict[str, UUID] = {}

        def alliance_of(members_: list[str], umbrella: UUID) -> UUID:
            label = "-".join(members_)
            if label not in alliances:
                alliances[label] = cs.key("mx.alliance", f"{day}:{iso}:{label}")
                if alliances[label] in cs.minted:
                    cs.add("alliance", id=alliances[label], kind="electoral_alliance", election_id=umbrella)
                    name(alliances[label], "–".join(members_) + " coalition")
                    name(alliances[label], "Coalición " + "–".join(members_), "es", "native")
                    for member in members_:
                        cs.add("alliance_member", alliance_id=alliances[label], party_id=party(member))
            return alliances[label]

        umbrella = cs.key("mx.election", f"{day}:local:{iso}")
        state_name = dict(
            conn.execute(
                "select c.code, n.text from area a join area_code c on c.area_id = a.id join name n "
                "on n.entity_id = a.id and n.is_preferred where a.scheme_code = 'mx.entidad'"
            ).fetchall()
        )[state]
        state_path = f"mx/{iso[3:].lower()}"
        if umbrella in cs.minted:
            cs.add("election", id=umbrella, polity_id=parent, kind="local", date_from=day, date_to=day, status="held")
            name(umbrella, f"{year} {state_name} local elections")
            name(umbrella, f"Proceso electoral local {year - 1}-{year}, {state_name}", "es", "native")
            cs.add("slug", path=f"{state_path}/{day}-local", entity_id=umbrella)

        towns = {
            int(value[2:]): (entity, code, text)
            for value, entity, code, text in conn.execute(
                "select x.value, a.id, c.code, n.text from external_id x join area a on a.id = x.entity_id "
                "join area_code c on c.area_id = a.id join name n on n.entity_id = a.id and n.is_preferred "
                "where x.scheme = 'mx.ine.municipio' and a.scheme_code = 'mx.municipio' and x.value like %s",
                (f"{state}%",),
            ).fetchall()
        }
        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id "
                "where a.scheme_code = 'mx.seccion' and c.code like %s",
                (f"{state}%",),
            ).fetchall()
        )
        state_unit = cs.unit("mx.entidad", state)
        unit_rows: dict[UUID, tuple[str, str, UUID, str, UUID, str]] = {}
        values: list[tuple[str, str, str | None, int, int]] = []
        winners: dict[str, int] = {}
        seats_total = 0
        engine_differs: list[str] = []
        uneven_towns: list[str] = []
        uneven_towns_allowed = {
            towns[t][2] for t in casillas.filter(pl.col("total") != pl.col("counted"))["town"].unique()
        }

        def tally(result_set: UUID, unit: str, option: UUID | None, measure: int, value: int) -> None:
            values.append((str(result_set), unit, None if option is None else str(option), measure, value))

        def make_set(
            contest: UUID, label: str, default: bool, made_by: UUID, note: str, stage: str = "official"
        ) -> UUID:
            result_set = cs.key("mx.result_set", f"{day}:ayu:{label}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[label.split(":")[0]],
                contest_id=contest,
                stage=stage,
                kind="counted",
                status="active",
                source_id=made_by,
                is_default=default,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            return result_set

        people: dict[UUID, str | None] = {}

        def person_for(where: str, text: str, gender: str | None = None) -> UUID:
            person = cs.key("mx.person", f"{day}:{where}:{fold(text)}")
            if person in cs.minted and person not in people:
                name(person, display(text, "es"), "es")
            people[person] = people.get(person) or gender
            return person

        elections: dict[str, UUID] = {}
        for town in sorted(casillas["town"].unique()):
            area, inegi, label = towns[town]
            where = str(inegi)
            path = f"{state_path}/{slug(label)}"
            polity = cs.key("mx.polity", f"municipio:{inegi}")
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                name(polity, label, "es")
                cs.add("polity_parent", polity_id=polity, parent_id=parent)
                cs.add("polity_territory", polity_id=polity, area_id=area)
                cs.add("slug", path=path, entity_id=polity)
            election_id = elections[where] = cs.key("mx.election", f"{day}:local:{inegi}")
            cs.add(
                "election",
                id=election_id,
                polity_id=polity,
                parent_id=umbrella,
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election_id, f"{year} {label} municipal election")
            name(election_id, f"Elección del ayuntamiento de {label} {year}", "es", "native")
            cs.add("slug", path=f"{path}/{day}-local", entity_id=election_id)
            office = cs.key("mx.office", f"presidencia:{inegi}")
            if office in cs.minted:
                cs.add("office", id=office, polity_id=polity, kind="mayor", body_id=None)
                name(office, f"Municipal President of {label}")
                name(office, f"Presidencia Municipal de {label}", "es", "native")
            body = cs.key("mx.body", f"ayuntamiento:{inegi}")
            if body in cs.minted:
                cs.add("body", id=body, polity_id=polity, kind="council")
                name(body, f"Ayuntamiento of {label}")
                name(body, f"Ayuntamiento de {label}", "es", "native")
            mine = members.filter(pl.col("town") == town)
            councillors = mine.filter(pl.col("office") == "regiduria")
            cs.add(
                "body_size",
                body_id=body,
                seats=councillors.height + 2,
                valid_from=day,
                valid_to=election["until"],
            )
            town_unit = cs.unit("mx.municipio", where)
            unit_rows[town_unit] = ("mx.municipio", where, state_unit, "equals", area, "standard")

            contest = cs.key("mx.contest", f"{day}:ayu:{inegi}:mr")
            cs.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="office",
                office_id=office,
                district_area_id=area,
                magnitude=1,
                electoral_system_id=mr_system,
                default_result_kind="counted",
            )
            name(contest, f"{year} {label} municipal president election")
            name(contest, f"Presidencia municipal y sindicatura de {label} {year}", "es", "native")
            cs.add("slug", path=f"{path}/{day}-local/presidencia", entity_id=contest)
            voided = annulled.filter(pl.col("town") == town)
            town_set = make_set(
                contest, f"{where}:mr", True, counts, MR_METHOD, "court_revised" if voided.height else "official"
            )
            casilla_set = make_set(contest, f"{where}:mr:casilla", False, counts, CASILLA_NOTE)
            cs.add("citation", entity_id=town_set, source_id=listing, locator={"page": 1})
            if town in election["unnamed"]:
                cs.add(
                    "note",
                    entity_id=town_set,
                    kind="caveat",
                    body_md="The count workbook's municipal summary for this municipality prints the governor "
                    "candidates' names instead of the planillas', so only the elected president and síndico are "
                    "named, from the institute's list of elected members.",
                )

            town_votes = votes.filter(pl.col("town") == town)
            column_sums = dict(town_votes.group_by("column").agg(pl.col("votes").sum()).iter_rows())
            printed = dict(printed_totals.filter(pl.col("town") == town).select("column", "votes").iter_rows())
            if printed and set(printed) != set(column_sums):
                raise ValueError(f"{label}: printed total columns {sorted(printed)} differ from {sorted(column_sums)}")
            printed = printed or column_sums
            off = {c: printed[c] - column_sums[c] for c in printed if printed[c] != column_sums[c]}
            if off and not voided.height and label not in uneven_towns_allowed:
                raise ValueError(f"{label}: printed totals differ from the casillas {off}")
            sums = {column: total for column, total in printed.items() if column not in ("unregistered", "null")}
            standing = {tuple(group): sum(sums[c] for c in group) for group in groups_of(list(sums))}
            ordered = sorted(standing.items(), key=lambda item: -item[1])
            if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
                raise ValueError(f"{label}: tie for first place")
            named = {row["votes"]: row for row in planillas.filter(pl.col("town") == town).iter_rows(named=True)}
            president = mine.filter(pl.col("office") == "presidencia").row(0, named=True)
            sindic = mine.filter(pl.col("office") == "sindicatura").row(0, named=True)
            options: dict[str, UUID] = {}
            for position, (group, total) in enumerate(ordered, 1):
                ticket = named.get(total)
                if ticket is None:
                    raise ValueError(f"{label}: no planilla printed with {total} votes")
                lineup = sorted({m for c in group for m in c.split("+")} - {INDEPENDENT})
                head = cs.key("mx.option", f"{day}:ayu:{inegi}:mr:{'|'.join(group)}")
                single = len(lineup) == 1
                cs.add(
                    "contest_option",
                    id=head,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party(lineup[0]) if single else None,
                    alliance_id=alliance_of(lineup, umbrella) if len(lineup) > 1 else None,
                    ballot_position=position,
                    ballot_label=ticket["president"] or "–".join(lineup) or INDEPENDENT,
                )
                elected = position == 1
                if elected:
                    if ticket["president"] is None:
                        ticket = {**ticket, "president": president["name"], "sindic": sindic["name"]}
                    elif fold(ticket["president"]) != fold(president["name"]):
                        raise ValueError(f"{label}: {ticket['president']} won the count, {president['name']} listed")
                    if president["party"] not in lineup or sindic["party"] not in lineup:
                        raise ValueError(f"{label}: elected parties {president['party']} {sindic['party']}")
                    cs.add("outcome", result_set_id=town_set, option_id=head, kind="elected", seats=1)
                    winner = "–".join(lineup) or INDEPENDENT
                    winners[winner] = winners.get(winner, 0) + 1
                for role, person_name, record in (
                    ("head", ticket["president"], president if elected else None),
                    ("running_mate", ticket["sindic"], sindic if elected else None),
                ):
                    if person_name is None:
                        continue
                    cs.add(
                        "candidacy",
                        id=cs.key("mx.candidacy", f"{day}:ayu:{inegi}:mr:{'|'.join(group)}:{role}"),
                        person_id=person_for(where, person_name, None if record is None else record["gender"]),
                        option_id=head,
                        role=role,
                        ballot_name=person_name,
                        member_of_party_id=None
                        if record is None or record["party"] == INDEPENDENT
                        else party(record["party"]),
                        is_independent=not lineup,
                    )
                for column in group:
                    if len(group) == 1:
                        options[column] = head
                        continue
                    line = cs.key("mx.option", f"{day}:ayu:{inegi}:mr:{'|'.join(group)}:{column}")
                    own = [m for m in column.split("+") if m != INDEPENDENT]
                    cs.add(
                        "contest_option",
                        id=line,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=head,
                        party_id=party(own[0]) if len(own) == 1 else None,
                        alliance_id=alliance_of(own, umbrella) if len(own) > 1 else None,
                        ballot_label=column.replace("+", "–"),
                    )
                    options[column] = line
            write_in = cs.key("mx.option", f"{day}:ayu:{inegi}:mr:unregistered")
            cs.add(
                "contest_option",
                id=write_in,
                contest_id=contest,
                kind="write_in",
                ballot_label="Candidatos no registrados",
            )
            options["unregistered"] = write_in

            town_casillas = casillas.filter(pl.col("town") == town)
            casilla_units: dict[tuple[int, str], str] = {}
            for row in town_casillas.iter_rows(named=True):
                code = f"{year}:{state}-{inegi[2:]}-{row['section']:04d}-{ine.casilla_code(year, row['casilla'])}"
                unit_id = cs.unit("mx.casilla", code)
                if unit_id in unit_rows:
                    raise ValueError(f"{label}: casilla {code} twice")
                located = sections.get(f"{state}{row['section']:04d}")
                unit_rows[unit_id] = (
                    "mx.casilla",
                    code,
                    town_unit,
                    "within",
                    located or area,
                    "standard",
                )
                casilla_units[(row["section"], row["casilla"])] = str(unit_id)
                if row["registered"]:
                    tally(casilla_set, str(unit_id), None, REGISTERED, row["registered"])
                tally(casilla_set, str(unit_id), None, BALLOTS, row["total"])
            casilla_valid: dict[str, int] = {}
            for row in town_votes.iter_rows(named=True):
                unit = casilla_units[(row["section"], row["casilla"])]
                if row["column"] == "null":
                    tally(casilla_set, unit, None, INVALID, row["votes"])
                    continue
                casilla_valid[unit] = casilla_valid.get(unit, 0) + row["votes"]
                if row["votes"]:
                    tally(casilla_set, unit, options[row["column"]], VOTES, row["votes"])
            for unit, valid in casilla_valid.items():
                tally(casilla_set, unit, None, VALID, valid)
            unit = str(town_unit)
            for column, total in printed.items():
                if column == "null":
                    tally(town_set, unit, None, INVALID, total)
                elif total:
                    tally(town_set, unit, options[column], VOTES, total)
            null = printed.get("null", 0)
            counted = sum(printed.values())
            ballots = counted if voided.height else int(town_casillas["total"].sum())
            registered = int(town_casillas["registered"].sum())
            uneven = town_casillas.filter(pl.col("total") != pl.col("counted"))
            if uneven.height:
                listed_uneven = "; ".join(
                    f"section {r['section']} {r['casilla']}: {r['total']:,} printed, {r['counted']:,} in the columns"
                    for r in uneven.iter_rows(named=True)
                )
                cs.add(
                    "note",
                    entity_id=casilla_set,
                    kind="caveat",
                    body_md=f"The workbook's total votes cast differ from the sum of its columns at "
                    f"{uneven.height} polling station(s) ({listed_uneven}). The printed total is stored as the "
                    "ballots cast and the columns as the votes.",
                )
                uneven_towns.append(label)
            if off and not voided.height:
                cs.add(
                    "note",
                    entity_id=town_set,
                    kind="caveat",
                    body_md="The workbook's printed municipal totals differ from the sum of its polling stations ("
                    + "; ".join(
                        f"{c.replace('+', '–')}: {printed[c]:,} printed, {column_sums[c]:,} summed" for c in off
                    )
                    + "). The printed totals are stored here and the polling-station figures in the polling-station "
                    "result set.",
                )
            if voided.height:
                rulings = sorted({r for r in voided["ruling"].to_list() if r})
                listed_voided = ", ".join(
                    f"{r['section']} {r['casilla']}" for r in voided.sort("section", "casilla").iter_rows(named=True)
                )
                cs.add(
                    "note",
                    entity_id=town_set,
                    kind="methodology",
                    body_md=f"Totals after the electoral courts annulled the vote at {voided.height} polling stations "
                    f"({' and '.join(rulings)}): section and station {listed_voided}. The workbook prints these "
                    "totals as the final count. The polling-station result set leaves the annulled stations out; "
                    "at some of them the workbook's annulment table subtracts figures that differ from the station's "
                    "own row, so its sums differ slightly from these totals.",
                )
                cs.add(
                    "note",
                    entity_id=casilla_set,
                    kind="caveat",
                    body_md=f"Leaves out the {voided.height} polling stations the electoral courts annulled "
                    f"({' and '.join(rulings)}). The court-revised municipal totals are the default result set.",
                )
            tally(town_set, unit, None, VALID, counted - null)
            tally(town_set, unit, None, BALLOTS, ballots)
            if registered:
                tally(town_set, unit, None, REGISTERED, registered)

            rp = cs.key("mx.contest", f"{day}:ayu:{inegi}:rp")
            seats = councillors.height
            cs.add(
                "contest",
                id=rp,
                election_id=election_id,
                kind="list_allocation",
                body_id=body,
                district_area_id=area,
                magnitude=seats,
                electoral_system_id=rp_system,
                default_result_kind="counted",
            )
            name(rp, f"{year} {label} regidurías")
            name(rp, f"Regidurías de representación proporcional de {label} {year}", "es", "native")
            cs.add("slug", path=f"{path}/{day}-local/regidurias", entity_id=rp)
            rp_set = make_set(rp, f"{where}:rp", True, counts, RP_METHOD)
            cs.add("citation", entity_id=rp_set, source_id=listing, locator={"page": 1})
            split = {
                row["party"]: row["votes"] for row in party_votes.filter(pl.col("town") == town).iter_rows(named=True)
            }
            if sum(split.values()) not in (ballots, counted):
                raise ValueError(f"{label}: split party votes add to {sum(split.values())}, not {ballots}")
            won = dict(councillors.group_by("party").len().iter_rows())
            if set(won) - set(split):
                raise ValueError(f"{label}: seats for {set(won) - set(split)} without votes")
            lists: dict[str, UUID] = {}
            listed = sorted(
                (p for p in split if p not in ("unregistered", "null") and split[p]), key=lambda p: -split[p]
            )
            for position, abbrev in enumerate(listed, 1):
                lists[abbrev] = cs.key("mx.option", f"{day}:ayu:{inegi}:rp:{abbrev}")
                cs.add(
                    "contest_option",
                    id=lists[abbrev],
                    contest_id=rp,
                    kind="list",
                    party_id=None if abbrev == INDEPENDENT else party(abbrev),
                    ballot_position=position,
                    ballot_label="Candidatura independiente" if abbrev == INDEPENDENT else abbrev,
                )
                tally(rp_set, unit, lists[abbrev], VOTES, split[abbrev])
                if won.get(abbrev):
                    cs.add("outcome", result_set_id=rp_set, option_id=lists[abbrev], kind="won", seats=won[abbrev])
            rp_write_in = cs.key("mx.option", f"{day}:ayu:{inegi}:rp:unregistered")
            cs.add(
                "contest_option",
                id=rp_write_in,
                contest_id=rp,
                kind="write_in",
                ballot_label="Candidatos no registrados",
            )
            tally(rp_set, unit, rp_write_in, VOTES, split.get("unregistered", 0))
            tally(rp_set, unit, None, VALID, ballots - split.get("null", 0))
            tally(rp_set, unit, None, INVALID, split.get("null", 0))
            tally(rp_set, unit, None, BALLOTS, ballots)
            for row in councillors.iter_rows(named=True):
                cs.add(
                    "candidacy",
                    id=cs.key("mx.candidacy", f"{day}:ayu:{inegi}:rp:{row['seat']}"),
                    person_id=person_for(where, row["name"], row["gender"]),
                    option_id=lists[row["party"]],
                    role="list_member",
                    ballot_name=row["name"],
                    member_of_party_id=None if row["party"] == INDEPENDENT else party(row["party"]),
                    is_independent=row["party"] == INDEPENDENT,
                )
            seats_total += seats
            valid = sum(v for p, v in split.items() if p not in ("unregistered", "null"))
            eligible = {p: split[p] for p in listed if split[p] * 100 >= valid * 3}
            factor = sum(eligible.values()) / seats
            engine = {p: int(v // factor) for p, v in eligible.items()}
            for p in sorted(eligible, key=lambda p: -(eligible[p] - engine[p] * factor))[
                : seats - sum(engine.values())
            ]:
                engine[p] += 1
            if {p: s for p, s in engine.items() if s} != won:
                engine_differs.append(label)

        for person, gender in people.items():
            if person in cs.minted:
                cs.add("person", id=person, gender=gender)
        print(key, "winners", dict(sorted(winners.items(), key=lambda item: -item[1])), "regidurías", seats_total)
        print("hare differs from the declared seats in", len(engine_differs), engine_differs)
        print("printed totals differ from the columns in", uneven_towns)
        cs.frame(
            "reporting_unit",
            pl.DataFrame(
                [
                    {
                        "id": str(unit_id),
                        "polity_id": str(mexico),
                        "scheme_code": scheme,
                        "code": code,
                        "parent_id": str(parent_unit),
                        "area_relation": relation,
                        "area_id": str(area),
                        "kind": kind,
                    }
                    for unit_id, (scheme, code, parent_unit, relation, area, kind) in unit_rows.items()
                    if unit_id in cs.minted
                ]
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
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
