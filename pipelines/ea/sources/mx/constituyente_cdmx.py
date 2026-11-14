import csv
import io
import tarfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.mx import ine
from ea.sources.mx.ine import BALLOTS, INVALID, REGISTERED, VALID, VOTES
from ea.store import DATA, connect

SOURCE = "mx.constituyente_cdmx"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE

DAY = date(2016, 6, 5)
BASE = "https://computos2016-cdmex.ine.mx"
PAGE = f"{BASE}/BaseDeDatos.html"
ARCHIVE = f"{BASE}/20160609_2332_BD.tar.gz"
DIPUTADOS = "CIUDAD_DE_MEXICO_DIPUTADOS_2016.csv"
CANDIDATOS = "CIUDAD_DE_MEXICO_CANDIDATOS_2016.csv"
CG601_URL = (
    "https://repositoriodocumental.ine.mx/xmlui/bitstream/handle/123456789/78066/CGex201608-23-ap-unico_ou8uU3C.pdf"
)
CG601_WAYBACK = f"https://web.archive.org/web/2id_/{CG601_URL}"
CG601_TITLE = (
    "INE/CG601/2016: acuerdo del Consejo General por el que se efectúa el cómputo total, se declara la validez de la "
    "elección y se asignan diputados por el principio de representación proporcional a la Asamblea Constituyente de "
    "la Ciudad de México (23 de agosto de 2016)"
)
COUNT_TITLE = (
    "Cómputos Distritales 2016: elección de diputados a la Asamblea Constituyente de la Ciudad de México, base de "
    "datos por casilla (corte del 9 de junio de 2016, 23:32 horas)"
)

PARTY_COLUMNS = {
    "PAN": "PAN",
    "PRI": "PRI",
    "PRD": "PRD",
    "PVEM": "PVEM",
    "PT": "PT",
    "MOVIMIENTO_CIUDADANO": "MC",
    "NUEVA_ALIANZA": "NA",
    "MORENA": "MORENA",
    "ENCUENTRO_SOCIAL": "PES",
}
LIST_NAMES = {
    "PAN": "PAN",
    "PRI": "PRI",
    "PRD": "PRD",
    "PVEM": "PVEM",
    "PT": "PT",
    "MOVIMIENTO CIUDADANO": "MC",
    "NUEVA ALIANZA": "NA",
    "MORENA": "MORENA",
    "ENCUENTRO SOCIAL": "PES",
}
INDEPENDENTS = [f"CI_{n:02d}" for n in range(1, 22)]
OFFICIAL = {
    "PAN": 203249,
    "PRI": 152370,
    "PRD": 568795,
    "PT": 18257,
    "PVEM": 30361,
    "MC": 41912,
    "NA": 54940,
    "MORENA": 649537,
    "PES": 68336,
    "CI_01": 21971,
    "CI_02": 12182,
    "CI_03": 11366,
    "CI_04": 5234,
    "CI_05": 10942,
    "CI_06": 5549,
    "CI_07": 9850,
    "CI_08": 9639,
    "CI_09": 6006,
    "CI_10": 6732,
    "CI_11": 4964,
    "CI_12": 13116,
    "CI_13": 8579,
    "CI_14": 6211,
    "CI_15": 2755,
    "CI_16": 6862,
    "CI_17": 3067,
    "CI_18": 2893,
    "CI_19": 8787,
    "CI_20": 9876,
    "CI_21": 9723,
}
OFFICIAL_NULL = 171886
OFFICIAL_TOTAL = 2135947
SEATS = {"PAN": 7, "PRI": 5, "PRD": 19, "PVEM": 1, "MC": 1, "NA": 2, "MORENA": 22, "PES": 2, "CI_01": 1}
REGISTERED_VOTERS = 7481591

SYSTEM = (
    "Sixty of the 100 members of the Constituent Assembly of Mexico City were elected from closed party lists and "
    "independent formulas in a single Mexico City constituency (Constitution, seventh transitional article of the "
    "decree of 29 January 2016). Independent formulas with at least the natural quotient (valid votes divided by "
    "sixty) took seats first. Parties shared the rest under a new quotient (votes minus the independents' votes, "
    "divided by the seats left), in list order, and the largest remainders of parties and independents filled any "
    "seats still open. The other 40 members were appointed. The archive stores the system as closed-list Hare "
    "largest remainder; the first step for independents is not expressed in its rules."
)
OFFICIAL_METHOD = (
    "The total count of INE's Consejo General after the district and circumscription counts: {votes:,} votes, of "
    "which {valid:,} were for parties and independent formulas and {null:,} were null. The seats are the "
    "assignment of INE/CG601/2016: 59 to parties and 1 to the independent formula headed by Ismael Figueroa "
    "Flores. The assignment was subject to the Sala Superior's review of pending challenges."
)
CASILLA_METHOD = (
    "The district count file by casilla, cut on 9 June 2016 at 23:32 with all {casillas:,} actas counted: "
    "{votes:,} votes. The file predates the total count of INE/CG601/2016, which counts {official:,} votes, so its "
    "figures differ from the official totals ({valid:,} valid votes against {official_valid:,}). Casillas sit "
    "within their electoral section where it is mapped, and otherwise within the state."
)
OVER = (
    "In {count} casillas the file prints more votes than voters on the list ({listed}). The figures are stored as "
    "printed."
)


def read_archive() -> tuple[list[dict[str, str]], list[list[str]]]:
    with tarfile.open(raw.cached(SOURCE, ARCHIVE).path) as archive:
        members = {Path(m.name).name: m for m in archive.getmembers()}
        texts = []
        for name in (DIPUTADOS, CANDIDATOS):
            member = archive.extractfile(members[name])
            if member is None:
                raise ValueError(f"{name} is not a file in the archive")
            texts.append(member.read().decode("latin-1"))
        votes, candidates = texts
    lines = votes.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("ESTADO|"))
    rows = [
        {k: (v or "").strip() for k, v in row.items() if k is not None}
        for row in csv.DictReader(io.StringIO("\n".join(lines[start:])), delimiter="|")
    ]
    return rows, [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(candidates))][1:]


def tail(row: dict[str, str]) -> str:
    kind, number, extension = row["TIPO_CASILLA"], int(row["ID_CASILLA"]), int(row["EXT_CONTIGUA"] or 0)
    letter = {"Básica": "B", "Contigua": "C", "Extraordinaria": "E", "Especial": "S"}[kind]
    return ("B" if letter == "B" else f"{letter}{number}") + (f"C{extension}" if extension else "")


def fetch() -> None:
    raw.fetch(SOURCE, ARCHIVE, suffix=".tar.gz")
    raw.fetch(SOURCE, PAGE, suffix=".html")
    try:
        raw.cached(SOURCE, CG601_URL)
    except LookupError:
        import httpx

        response = httpx.get(CG601_WAYBACK, follow_redirects=True, timeout=120, headers={"User-Agent": raw.USER_AGENT})
        response.raise_for_status()
        raw.store(
            SOURCE,
            CG601_URL,
            response.content,
            suffix=".pdf",
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf",
        )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows, candidates = read_archive()
    if len(rows) != 12789:
        raise ValueError(f"{len(rows)} casilla rows, the file prints 12,789")
    columns = [*INDEPENDENTS, *PARTY_COLUMNS]
    units, votes = [], []
    for row in rows:
        counts = {c: ine.number(row[c]) if row[c] else 0 for c in columns}
        null = ine.number(row["NULOS"]) if row["NULOS"] else 0
        total = ine.number(row["TOTAL_VOTOS"])
        if sum(counts.values()) + null != total:
            raise ValueError(f"total differs: {row}")
        unit = {"section": ine.number(row["SECCION"]), "casilla": tail(row), "district": ine.number(row["DISTRITO"])}
        units.append(
            unit
            | {
                "total": total,
                "valid": sum(counts.values()),
                "null": null,
                "registered": ine.number(row["LISTA_NOMINAL"]),
                "status": row["OBSERVACION"],
            }
        )
        votes += [unit | {"option": PARTY_COLUMNS.get(c, c), "votes": v} for c, v in counts.items()]
    frame = pl.DataFrame(units)
    if frame.group_by("section", "casilla").len().filter(pl.col("len") > 1).height:
        raise ValueError("duplicate casillas")
    if int(frame["registered"].sum()) != REGISTERED_VOTERS:
        raise ValueError("registered voters differ from the file's printed state total")
    frame.write_parquet(PARSED / "casillas.parquet")
    pl.DataFrame(votes).write_parquet(PARSED / "votes.parquet")
    pl.DataFrame(candidates, schema=["list", "owner", "substitute"], orient="row").write_parquet(
        PARSED / "candidates.parquet"
    )
    by = dict(pl.DataFrame(votes).group_by("option").agg(pl.col("votes").sum()).rows())
    print(frame.height, "casillas", int(frame["total"].sum()), "votes", dict(sorted(by.items())))


def emit() -> Path:
    casillas = pl.read_parquet(PARSED / "casillas.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet").rows()
    if sum(OFFICIAL.values()) + OFFICIAL_NULL != OFFICIAL_TOTAL:
        raise ValueError("official figures do not add to the declared total")
    if sum(SEATS.values()) != 60:
        raise ValueError("seats do not add to sixty")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2016 election of 60 members of the Constituent Assembly of Mexico City: the INE total count "
            "with the declared seats and the district count by casilla",
            polity="MX",
        )
        for scheme in ("mx.entidad", "mx.seccion", "mx.casilla"):
            if conn.execute("select 1 from scheme where code = %s", (scheme,)).fetchone() is None:
                raise ValueError("publish mx.mge before mx.constituyente_cdmx")

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        mexico = cs.polity("MX")
        city = cs.polity("MX-CMX")
        count_source = cs.source(ine.INE, COUNT_TITLE)
        cs.add(
            "source",
            id=count_source,
            kind="official_body",
            publisher=ine.INE,
            title=COUNT_TITLE,
            url=PAGE,
            license=ine.INE_LICENSE,
            license_url=ine.INE_PAGE,
            attribution="Instituto Nacional Electoral, Cómputos Distritales 2016",
        )
        cs.artifact(raw.cached(SOURCE, ARCHIVE), count_source)
        declarer = cs.source(ine.INE, CG601_TITLE)
        cs.add(
            "source",
            id=declarer,
            kind="official_body",
            publisher=ine.INE,
            title=CG601_TITLE,
            url=CG601_URL,
            license=ine.PUBLIC,
        )
        cs.artifact(raw.cached(SOURCE, CG601_URL), declarer)

        system = cs.key("mx.electoral_system", "constituyente-cdmx-2016")
        if system in cs.minted:
            cs.add(
                "electoral_system",
                id=system,
                polity_id=city,
                rules={"formula": "hare", "lists": {"order": "closed", "vacancy": "substitute"}},
                rules_version=1,
            )
            name(system, "Mexico City Constituent Assembly list system, 2016")
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        body = cs.key("mx.body", "constituyente-cdmx")
        cs.add("body", id=body, polity_id=city, kind="constituent_assembly")
        name(body, "Constituent Assembly of Mexico City")
        name(body, "Asamblea Constituyente de la Ciudad de México", "es", "native")
        cs.add("body_size", body_id=body, seats=100, valid_from=date(2016, 9, 15), valid_to=date(2017, 1, 31))

        election = cs.key("mx.election", f"{DAY}:CMX:constituyente")
        path = f"mx/cmx/{DAY}-constituent-assembly"
        cs.add("election", id=election, polity_id=city, kind="special", date_from=DAY, date_to=DAY, status="held")
        name(election, "2016 Mexico City Constituent Assembly election")
        name(election, "Elección de la Asamblea Constituyente de la Ciudad de México de 2016", "es", "native")
        cs.add("slug", path=path, entity_id=election)

        contest = cs.key("mx.contest", f"{DAY}:CMX:constituyente")
        cs.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body,
            district_area_id=cs.area("mx.entidad", "09"),
            magnitude=60,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        name(contest, "2016 Mexico City Constituent Assembly election")
        name(contest, "Elección de diputados constituyentes de la Ciudad de México de 2016", "es", "native")
        cs.add("slug", path=f"{path}/constituent-assembly", entity_id=contest)

        parties: dict[str, UUID] = {}

        def party(abbrev: str) -> UUID:
            if abbrev not in parties:
                item, native, english = ine.PARTIES[abbrev]
                parties[abbrev] = cs.key("mx.party", item)
                if parties[abbrev] in cs.minted:
                    cs.add("party", id=parties[abbrev], kind="party", polity_id=mexico)
                    name(parties[abbrev], native, "es")
                    name(parties[abbrev], english, "en", "english")
                    name(parties[abbrev], abbrev, "es", "abbreviation")
                    cs.add("external_id", entity_id=parties[abbrev], scheme="wikidata", value=item)
            return parties[abbrev]

        def person(key: str, full: str) -> UUID:
            entity = cs.key("mx.person", key)
            if entity in cs.minted:
                cs.add("person", id=entity)
                name(entity, full)
            return entity

        options: dict[str, UUID] = {}
        position = 0
        lists = {LIST_NAMES[row[0].strip().upper()]: [] for row in candidates if row[0].strip().upper() in LIST_NAMES}
        for row in candidates:
            label = row[0].strip().upper()
            if label in LIST_NAMES:
                lists[LIST_NAMES[label]].append(row)
        independents = {row[0].strip(): row for row in candidates if row[0].startswith("CI_")}
        if set(independents) != set(INDEPENDENTS) or any(len(rows) != 60 for rows in lists.values()):
            raise ValueError("candidate file does not hold 9 lists of 60 and 21 independent formulas")
        for abbrev in PARTY_COLUMNS.values():
            position += 1
            options[abbrev] = cs.key("mx.option", f"{DAY}:CMX:constituyente:{abbrev}")
            cs.add(
                "contest_option",
                id=options[abbrev],
                contest_id=contest,
                kind="list",
                party_id=party(abbrev),
                ballot_position=position,
                ballot_label=ine.PARTIES[abbrev][2],
            )
            for number, (_, owner, substitute) in enumerate(lists[abbrev], 1):
                for role, text, suffix in (("list_member", owner, "p"), ("substitute", substitute, "s")):
                    candidacy = cs.key("mx.candidacy", f"{DAY}:CMX:constituyente:{abbrev}:{number}:{suffix}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person(f"{DAY}:CMX:constituyente:{abbrev}:{number}:{suffix}", text),
                        option_id=options[abbrev],
                        role=role,
                        list_position=number,
                        ballot_name=text,
                        member_of_party_id=party(abbrev),
                        is_independent=False,
                    )
                    cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party(abbrev), kind="nominated")
        for code in INDEPENDENTS:
            position += 1
            _, owner, substitute = independents[code]
            options[code] = cs.key("mx.option", f"{DAY}:CMX:constituyente:{code}")
            cs.add(
                "contest_option",
                id=options[code],
                contest_id=contest,
                kind="candidate",
                ballot_position=position,
                ballot_label=owner.title(),
            )
            for role, text, suffix in (("head", owner, "p"), ("substitute", substitute, "s")):
                cs.add(
                    "candidacy",
                    id=cs.key("mx.candidacy", f"{DAY}:CMX:constituyente:{code}:{suffix}"),
                    person_id=person(f"{DAY}:CMX:constituyente:{code}:{suffix}", text),
                    option_id=options[code],
                    role=role,
                    ballot_name=text,
                    is_independent=True,
                )
        total_by_option = dict(votes.group_by("option").agg(pl.col("votes").sum()).rows())
        if set(total_by_option) != set(options):
            raise ValueError("vote columns and contest options differ")

        counted = int(casillas["total"].sum())
        valid = int(casillas["valid"].sum())
        official_valid = OFFICIAL_TOTAL - OFFICIAL_NULL

        def make_set(label: str, default: bool, declared: bool, completeness: float, note: str) -> UUID:
            result_set = cs.key("mx.result_set", f"{DAY}:CMX:constituyente:{label}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=declarer if declared else count_source,
                is_default=default,
                completeness=completeness,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            return result_set

        state_set = make_set(
            "state",
            True,
            True,
            1,
            OFFICIAL_METHOD.format(votes=OFFICIAL_TOTAL, valid=official_valid, null=OFFICIAL_NULL),
        )
        casilla_set = make_set(
            "casilla",
            False,
            False,
            round(valid / official_valid, 4),
            CASILLA_METHOD.format(
                casillas=casillas.height,
                votes=counted,
                official=OFFICIAL_TOTAL,
                valid=valid,
                official_valid=official_valid,
            ),
        )
        cs.add("citation", entity_id=state_set, source_id=declarer, locator={"page": 12})
        for option, seats in SEATS.items():
            cs.add("outcome", result_set_id=state_set, option_id=options[option], kind="elected", seats=seats)
        cs.add("citation", entity_id=state_set, source_id=declarer, locator={"page": 26})

        over = casillas.filter((pl.col("registered") > 0) & (pl.col("total") > pl.col("registered"))).sort(
            "section", "casilla"
        )
        if over.height:
            listed = "; ".join(
                f"sección {r['section']} {r['casilla']}, {r['total']:,} votes for {r['registered']:,}"
                for r in over.iter_rows(named=True)
            )
            cs.add("note", entity_id=casilla_set, kind="caveat", body_md=OVER.format(count=over.height, listed=listed))
        silent = casillas.filter(pl.col("status") == "Casilla no instalada")
        if silent.height:
            cs.add(
                "note",
                entity_id=casilla_set,
                kind="caveat",
                body_md=f"{silent.height} casilla was not installed and is stored with zero votes.",
            )

        sections = dict(
            conn.execute(
                "select c.code, a.id from area a join area_code c on c.area_id = a.id where a.scheme_code = 'mx.seccion'"
            ).fetchall()
        )
        state_unit = cs.unit("mx.entidad", "09")
        state_area = cs.area("mx.entidad", "09")
        unit_rows: dict[UUID, tuple[str, UUID]] = {}
        placed = []
        for row in casillas.iter_rows(named=True):
            code = f"2016:09-cc-{row['section']:04d}-{row['casilla']}"
            unit = cs.unit("mx.casilla", code)
            if unit in unit_rows:
                raise ValueError(f"casilla {code} appears twice")
            unit_rows[unit] = (code, sections.get(f"09{row['section']:04d}") or state_area)
            placed.append((row["section"], row["casilla"], str(unit)))
        units = pl.DataFrame(
            placed,
            schema=[("section", pl.Int64), ("casilla", pl.String), ("unit_id", pl.String)],
            orient="row",
        )
        line = {abbrev: str(option) for abbrev, option in options.items()}
        located = casillas.join(units, on=["section", "casilla"])
        joined = votes.join(units, on=["section", "casilla"]).with_columns(
            line=pl.col("option").replace_strict(line, return_dtype=pl.String)
        )
        none = pl.lit(None, pl.String)
        frames = [
            part.select(
                result_set_id=pl.lit(str(casilla_set)),
                unit_id="unit_id",
                option_id="option_id",
                measure=pl.col("measure").cast(pl.Int16),
                value=pl.col("value").cast(pl.Int64),
                channel=pl.lit(0, pl.Int16),
            )
            for part in (
                located.filter(pl.col("registered") > 0).select(
                    "unit_id", option_id=none, measure=pl.lit(REGISTERED), value="registered"
                ),
                located.select("unit_id", option_id=none, measure=pl.lit(BALLOTS), value="total"),
                located.select("unit_id", option_id=none, measure=pl.lit(VALID), value="valid"),
                located.select("unit_id", option_id=none, measure=pl.lit(INVALID), value="null"),
                joined.filter(pl.col("votes") != 0).select(
                    "unit_id", option_id="line", measure=pl.lit(VOTES), value="votes"
                ),
            )
        ]
        official = [(str(options[option]), VOTES, value) for option, value in OFFICIAL.items()] + [
            (None, BALLOTS, OFFICIAL_TOTAL),
            (None, VALID, official_valid),
            (None, INVALID, OFFICIAL_NULL),
        ]
        frames.append(
            pl.DataFrame(
                official, schema=[("option_id", pl.String), ("measure", pl.Int16), ("value", pl.Int64)], orient="row"
            ).select(
                result_set_id=pl.lit(str(state_set)),
                unit_id=pl.lit(str(state_unit)),
                option_id="option_id",
                measure="measure",
                value="value",
                channel=pl.lit(0, pl.Int16),
            )
        )
        minted = [
            {
                "id": str(unit),
                "polity_id": str(mexico),
                "scheme_code": "mx.casilla",
                "code": code,
                "parent_id": str(state_unit),
                "area_relation": "within",
                "area_id": str(area),
                "kind": "standard",
            }
            for unit, (code, area) in unit_rows.items()
            if unit in cs.minted
        ]
        if minted:
            cs.frame("reporting_unit", pl.DataFrame(minted))
        cs.frame(
            "result_value",
            pl.concat(frames).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        print(casillas.height, "casillas", counted, "votes; official", OFFICIAL_TOTAL)
        return cs.write()
