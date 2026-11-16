"""Uruguay's Corte Electoral: every election with open data at the circuit (comisión receptora de votos) level.

The Corte publishes, per election, a file of totals per circuit and a file of the votes each ballot sheet (hoja)
received in each circuit, on the national open-data catalog. A circuit is the finest reporting unit.
"""

import functools
import io
import re
import unicodedata
from datetime import date
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "uy.corte"
CATALOG = "https://catalogodatos.gub.uy/dataset/"
URUGUAY = str(uuid5(package.NAMESPACE, "polity\x1fUY"))
LICENSE = "Licencia de Datos Abiertos de Uruguay"
LICENSE_URL = (
    "https://www.gub.uy/agencia-gobierno-electronico-sociedad-informacion-conocimiento/sites/"
    "agencia-gobierno-electronico-sociedad-informacion-conocimiento/files/documentos/publicaciones/"
    "licencia_de_datos_abiertos_0.pdf"
)

DEPARTMENTS = {
    "AR": "Artigas",
    "CA": "Canelones",
    "CL": "Cerro Largo",
    "CO": "Colonia",
    "DU": "Durazno",
    "FD": "Florida",
    "FS": "Flores",
    "LA": "Lavalleja",
    "MA": "Maldonado",
    "MO": "Montevideo",
    "PA": "Paysandú",
    "RN": "Río Negro",
    "RO": "Rocha",
    "RV": "Rivera",
    "SA": "Salto",
    "SJ": "San José",
    "SO": "Soriano",
    "TA": "Tacuarembó",
    "TT": "Treinta y Tres",
}

# Resource URLs on the open-data catalog, by election date.
# totals: ballots per circuit; breakdown: votes per ballot sheet and circuit; municipal: the municipal breakdown when
# the Corte publishes it as its own file.
FILES: dict[str, dict[str, str]] = {
    "2014-10-26": {
        "dataset": "corte-electoral-elecciones_nacionales_2014",
        "totals": "1d025bf7-5b0d-42c5-8a9a-0696e24f7634/resource/1e851dea-cd81-4ca5-82d2-55fa8920fc1f/download/totales-generales-por-comision-receptora-de-votos.csv",
        "breakdown": "1d025bf7-5b0d-42c5-8a9a-0696e24f7634/resource/91bc8eeb-227a-47a3-96e9-c2fe4e4a3e1c/download/desglose-de-votos.csv",
    },
    "2019-10-27": {
        "dataset": "corte-electoral-elecciones-nacionales-2019",
        "totals": "7c3caf30-b59e-43de-b928-547030cc682b/resource/4e6c2564-a178-4802-b7ed-59be68485146/download/totales-generales-por-comision-receptora-de-votos.csv",
        "breakdown": "7c3caf30-b59e-43de-b928-547030cc682b/resource/fae2be5e-44db-4763-8b32-9c18679d3aad/download/desglose-de-votos.csv",
    },
    "2024-10-27": {
        "dataset": "corte-electoral-elecciones-nacionales-2024",
        "totals": "7aaa1ff0-8ea5-4302-a3e6-c2d589b484a3/resource/0f149bb6-4fff-463b-bc3a-8ab671bcdf34/download/totales-generales-por-comision-receptora-de-votos-y-plebiscitos-constitucionales.csv",
        "breakdown": "7aaa1ff0-8ea5-4302-a3e6-c2d589b484a3/resource/f74aa981-47d3-4165-81be-5d2abcc5f6d5/download/desglose-de-votos.csv",
    },
    "2014-11-30": {
        "dataset": "corte-electoral-balotaje_2014",
        "totals": "6455b729-be2e-47d7-9205-b9355a593252/resource/5a0b3b75-890b-401e-a44a-08546a043576/download/balotaje-2014.csv",
    },
    "2019-11-24": {
        "dataset": "corte-electoral-balotaje_2019",
        "totals": "8a20dcc5-77c7-499d-9932-5d0ebea4df37/resource/a71df2ea-b075-40e7-80b5-1df4c781bb99/download/balotaje-2019.csv",
    },
    "2024-11-24": {
        "dataset": "corte-electoral-balotaje_2024",
        "totals": "2dc312e4-f3f7-41aa-b56d-74389083e7e1/resource/ac4cbd7c-dc15-4d3f-87ba-c00fb47604b7/download/balotaje-2024.csv",
    },
    "2019-06-30": {
        "dataset": "corte-electoral-elecciones_internas_2019",
        "totals": "e749ad5e-ef5a-4f56-9b16-96868cbfa460/resource/ec401b7f-bd99-43c7-aed8-e88fc3775923/download/totales-generales-por-comisin-receptora-de-votos.csv",
        "breakdown": "e749ad5e-ef5a-4f56-9b16-96868cbfa460/resource/912bcb58-3b3a-4a4c-aa5d-e633c87fcb97/download/desglose-de-votos-.csv",
    },
    "2024-06-30": {
        "dataset": "corte-electoral-elecciones-internas-de-los-partidos-politicos-2024",
        "totals": "48a4e7f5-6909-41a0-aa8a-95dc42cc201f/resource/f7dd08cf-5978-43c2-acc5-b2854d7029dd/download/totales-generales.csv",
        "breakdown": "48a4e7f5-6909-41a0-aa8a-95dc42cc201f/resource/dec32eb5-1754-4810-af7a-8c1121259206/download/desglose-de-votos.csv",
    },
    "2020-09-27": {
        "dataset": "corte-electoral-eleccionesdepartamentales2020",
        "totals": "e2ae6343-352d-4468-8343-12b7d8837056/resource/b195ebdb-23fa-40e1-8380-aa020d10011b/download/totales-generales-por-comisin-receptora-de-votos.csv",
        "breakdown": "e2ae6343-352d-4468-8343-12b7d8837056/resource/39ecc252-cd4f-49a6-9617-0863023a65cb/download/desglose-de-votos-eleccin-departamental.csv",
        "municipal": "e2ae6343-352d-4468-8343-12b7d8837056/resource/c61c004d-265a-4c1c-8106-c4ea12f1efa8/download/desglose-de-votos-eleccin-municipal.csv",
    },
    "2025-05-11": {
        "dataset": "corte-electoral-elecciones_departamentales_y_municipales_2025",
        "totals": "2d5eb299-0d95-478b-9271-c62f601e7bc1/resource/a27ceda4-4486-4329-9e67-b7c36e9dfa59/download/totales-generales-por-comision-receptora-de-votos.csv",
        "breakdown": "2d5eb299-0d95-478b-9271-c62f601e7bc1/resource/92d7ca7b-32f1-4cde-b607-d94aef825c4d/download/desglose-de-votos.csv",
    },
}
NATIONAL = ["2014-10-26", "2019-10-27", "2024-10-27"]
RUNOFF = ["2014-11-30", "2019-11-24", "2024-11-24"]
PRIMARY = ["2019-06-30", "2024-06-30"]
LOCAL = ["2020-09-27", "2025-05-11"]
DAYS = sorted(FILES)
KIND_PATH = {**dict.fromkeys(NATIONAL, "general"), **dict.fromkeys(RUNOFF, "runoff")}
KIND_PATH |= {**dict.fromkeys(PRIMARY, "primary"), **dict.fromkeys(LOCAL, "local")}

# The two tickets in each second round: the totals column, the ticket's label, and the party that nominated it.
# `elected` is the ticket that the second round's own totals show ahead; it is checked against the totals when built.
RUNOFF_TICKETS: dict[str, tuple[list[tuple[str, str, str]], str]] = {
    "2014-11-30": (
        [
            ("TOTALVAZQUEZSENDIC", "Vázquez – Sendic", "FRENTE AMPLIO"),
            ("TOTALLACALLEPOULARRANAGA", "Lacalle Pou – Larrañaga", "NACIONAL"),
        ],
        "FRENTE AMPLIO",
    ),
    "2019-11-24": (
        [
            ("TOTALMARTINEZVILLAR", "Martínez – Villar", "FRENTE AMPLIO"),
            ("TOTALLACALLEPOUARGIMON", "Lacalle Pou – Argimón", "NACIONAL"),
        ],
        "NACIONAL",
    ),
    "2024-11-24": (
        [
            ("TOTALORSICOSSE", "Orsi – Cosse", "FRENTE AMPLIO"),
            ("TOTALDELGADORIPOLL", "Delgado – Ripoll", "NACIONAL"),
        ],
        "FRENTE AMPLIO",
    ),
}
# The 2024 constitutional plebiscites, by the totals column that counts their "Sí" ballots.
PLEBISCITES = {
    "2024-10-27": [
        (
            "SIART11",
            "Plebiscite on night-time raids (Article 11)",
            "Plebiscito sobre allanamientos nocturnos (art. 11)",
        ),
        ("SIART67", "Plebiscite on social security (Article 67)", "Plebiscito sobre seguridad social (art. 67)"),
    ]
}
LOWER = {"de", "la", "los", "las", "del", "y", "el"}
BREAKDOWN_METHOD = (
    "Counted by the Corte Electoral per circuit (comisión receptora de votos), as published in the national open-data "
    "catalog. Each circuit is one reporting unit and sits in its department. The breakdown file counts the ballots "
    "of voters who voted in their own circuit; ballots cast in a circuit by voters registered elsewhere are 'observed' "
    "ballots, which the totals file counts but the breakdown does not split by ballot sheet. Registered voters are the "
    "electoral roll of the circuit, ballots cast are the ballots in the box (observed ones included), and observed, "
    "invalid, and blank ballots are stored as published. Valid votes are not published per circuit, so none are stored."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


UY_SOURCE = key("source", SOURCE)
NATION = key("area", "uy.nacion", "UY")
SENATE_BODY = key("body", "senate")
CHAMBER_BODY = key("body", "chamber")
PRESIDENCY = key("office", "president")
SYSTEM_PRESIDENT = key("electoral_system", "president", 1997)


def url(path: str) -> str:
    return path if path.startswith("https://") else f"{CATALOG}{path}"


def files(day: str) -> dict[str, str]:
    return {name: url(path) for name, path in FILES[day].items() if name != "dataset"}


def packages() -> list[str]:
    return ["uy.corte/geo", "uy.corte/registry", *(f"uy.corte/{day}" for day in DAYS)]


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, f"{CATALOG}{FILES[day]['dataset']}", suffix=".html")
        for link in files(day).values():
            raw.fetch(SOURCE, link, suffix=".csv")


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", text).upper())


def table(link: str) -> pl.DataFrame:
    data = raw.cached(SOURCE, link).path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    frame = pl.read_csv(io.StringIO(text), infer_schema_length=0)
    frame = frame.rename({column: norm(column) for column in frame.columns})
    return frame.with_columns(pl.col(pl.String).str.strip_chars())


def lema_key(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    plain = re.sub(r"\(.*?\)", "", plain)
    plain = re.sub(r"\s+-\s+[A-Z]+$", "", plain)
    plain = re.sub(r"^PARTIDO ", "", plain.strip())
    return " ".join(plain.split())


def lema_name(text: str) -> str:
    text = re.sub(r"\s+-\s+PDU$", "", text.strip())
    if text.isupper():
        text = " ".join(w.lower() if w.lower() in LOWER and i else w.capitalize() for i, w in enumerate(text.split()))
    return text


def circuit(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(pl.col("CRV").cast(pl.Int64).cast(pl.String))


def breakdown(day: str, name: str = "breakdown") -> pl.DataFrame:
    frame = circuit(table(files(day)[name]))
    return frame.with_columns(
        pl.col("CANTIDADVOTOS").cast(pl.Int64),
        pl.col("LEMA").map_elements(lema_key, return_dtype=pl.String).alias("LEMAKEY"),
    )


def totals(day: str) -> pl.DataFrame:
    return circuit(table(files(day)["totals"]))


@functools.cache
def names() -> dict[str, str]:
    found: dict[str, str] = {}
    for day in DAYS:
        if "breakdown" not in FILES[day]:
            continue
        for name in ("breakdown", "municipal"):
            if name in FILES[day]:
                for text in table(files(day)[name])["LEMA"].unique().to_list():
                    found[lema_key(text)] = lema_name(text)
    for tickets, _ in RUNOFF_TICKETS.values():
        for _, _, lema in tickets:
            found.setdefault(lema, lema_name(lema))
    return found


def party(lema: str) -> str:
    return key("party", lema)


@functools.cache
def municipalities() -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for day in LOCAL:
        for name in ("breakdown", "municipal"):
            if name not in FILES[day]:
                continue
            frame = table(files(day)[name]).filter(pl.col("TIPOREGISTRO") == "HOJA_EM")
            frame = frame.with_columns(pl.col("DESCRIPCION1").str.extract(r"-(\S+)$", 1).alias("LETTER"))
            for dept, letter, text in frame.select("DEPARTAMENTO", "LETTER", "DESCRIPCION2").unique().iter_rows():
                if letter:
                    found[(dept, letter)] = lema_name(text) if text.isupper() else text
    return found


def source_row(p: Package) -> None:
    p.add(
        "source",
        id=UY_SOURCE,
        kind="official_body",
        publisher="Corte Electoral",
        title="Resultados electorales: totales y desglose de votos por circuito (catálogo nacional de datos abiertos)",
        url="https://www.gub.uy/corte-electoral/datos-y-estadisticas/datos-abiertos",
        license=LICENSE,
        license_url=LICENSE_URL,
        attribution="Corte Electoral de Uruguay",
    )


def geo() -> Package:
    p = Package(
        "uy.corte/geo",
        SOURCE,
        "Uruguay's 19 departments and the municipalities and circuits that the Corte Electoral reports votes for",
    )
    p.external.add(URUGUAY)
    for code, rank, kind, noun in (
        ("uy.nacion", 0, "administrative", "country"),
        ("uy.departamento", 1, "administrative", "department"),
        ("uy.municipio", 2, "administrative", "municipality"),
        ("uy.circuito", 3, "precinct", "circuit"),
    ):
        p.add("scheme", code=code, polity_id=URUGUAY, kind=kind, rank=rank, noun=noun, code_system=None)
    p.add("area", id=NATION, scheme_code="uy.nacion")
    p.add("area_code", area_id=NATION, code="UY")
    p.name(NATION, "Uruguay", "es", "official")
    p.name(NATION, "Uruguay", "en", "official", False)
    for code, text in DEPARTMENTS.items():
        id = key("area", "uy.departamento", code)
        p.add("area", id=id, scheme_code="uy.departamento")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=NATION)
        p.name(id, text, "es", "official")
    for (dept, letter), text in sorted(municipalities().items()):
        id = key("area", "uy.municipio", dept, letter)
        p.add("area", id=id, scheme_code="uy.municipio")
        p.add("area_code", area_id=id, code=f"{dept}-{letter}")
        p.add("area_parent", area_id=id, parent_id=key("area", "uy.departamento", dept))
        p.name(id, text, "es", "official")
    return p


def registry() -> Package:
    p = Package(
        "uy.corte/registry",
        SOURCE,
        "Uruguayan lemas (parties), the Senate, the Chamber of Representatives, and the presidency from the Corte "
        "Electoral",
        depends=["uy.corte/geo"],
    )
    p.external.add(URUGUAY)
    source_row(p)
    for day in DAYS:
        p.artifact(raw.cached(SOURCE, f"{CATALOG}{FILES[day]['dataset']}"), UY_SOURCE)
        for link in files(day).values():
            p.artifact(raw.cached(SOURCE, link), UY_SOURCE)
    for code, text in (
        (CHAMBER_BODY, "Chamber of Representatives"),
        (SENATE_BODY, "Senate"),
    ):
        p.add("body", id=code, polity_id=URUGUAY, kind="lower_house" if code == CHAMBER_BODY else "upper_house")
        p.name(code, text, "en", "official")
    p.name(CHAMBER_BODY, "Cámara de Representantes", "es", "native", False)
    p.name(SENATE_BODY, "Cámara de Senadores", "es", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=URUGUAY, kind="head_of_state")
    p.name(PRESIDENCY, "President of Uruguay", "en", "official")
    p.name(PRESIDENCY, "Presidente de la República", "es", "native", False)
    for dept, text in DEPARTMENTS.items():
        id = key("office", "intendente", dept)
        p.add("office", id=id, polity_id=URUGUAY, kind="governor")
        p.name(id, f"Intendente of {text}", "en", "official")
        p.name(id, f"Intendente de {text}", "es", "native", False)
        council = key("body", "junta", dept)
        p.add("body", id=council, polity_id=URUGUAY, kind="council")
        p.name(council, f"Departmental Board of {text}", "en", "official")
        p.name(council, f"Junta Departamental de {text}", "es", "native", False)
    for (dept, letter), text in sorted(municipalities().items()):
        id = key("office", "alcalde", dept, letter)
        p.add("office", id=id, polity_id=URUGUAY, kind="mayor")
        p.name(id, f"Mayor of {text}, {DEPARTMENTS[dept]}", "en", "official")
        p.name(id, f"Alcalde de {text}, {DEPARTMENTS[dept]}", "es", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM_PRESIDENT,
        polity_id=URUGUAY,
        valid_from=date(1997, 1, 1),
        valid_to=None,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM_PRESIDENT),
        entity_id=SYSTEM_PRESIDENT,
        kind="methodology",
        body_md="A ticket that wins an absolute majority of valid votes in the national election is elected; "
        "otherwise the two leading tickets go to a second round in November.",
        lang="en",
    )
    for lema, text in sorted(names().items()):
        id = party(lema)
        p.add("party", id=id, kind="party", polity_id=URUGUAY)
        p.add("external_id", entity_id=id, scheme="uy.corte.lema", value=lema)
        p.name(id, text, "es", "official")
    return p


# ---------------------------------------------------------------------------------------------------------------------


class Builder:
    """One election's package, with its circuits as reporting units and its totals as unit accounting."""

    def __init__(self, day: str, summary: str) -> None:
        self.day = day
        self.p = Package(f"uy.corte/{day}", SOURCE, summary, depends=["uy.corte/geo", "uy.corte/registry"])
        self.p.external.add(URUGUAY)
        for link in files(day).values():
            self.p.uses(raw.cached(SOURCE, link))
        self.path = f"uy/{day}-{KIND_PATH[day]}"
        self.totals = totals(day)
        self.units: dict[tuple[str, str], str] = {}
        self.sets: list[str] = []
        self.slugs: set[str] = set()

    def unit(self, dept: str, crv: str) -> str:
        found = self.units.get((dept, crv))
        if found is None:
            found = self.units[(dept, crv)] = key("unit", self.day, dept, crv)
            self.p.add(
                "reporting_unit",
                id=found,
                polity_id=URUGUAY,
                scheme_code="uy.circuito",
                code=f"{self.day}:{dept}:{crv}",
                area_relation="within",
                area_id=key("area", "uy.departamento", dept),
                kind="standard",
            )
        return found

    def add_units(self, frame: pl.DataFrame) -> None:
        for dept, crv in frame.select("DEPARTAMENTO", "CRV").unique().sort("DEPARTAMENTO", "CRV").iter_rows():
            self.unit(dept, crv)

    def unit_frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            [(d, c, u) for (d, c), u in self.units.items()],
            schema={"DEPARTAMENTO": pl.String, "CRV": pl.String, "UNIT": pl.String},
            orient="row",
        )

    def accounting(self) -> pl.DataFrame:
        """The circuit totals in long form: DEPARTAMENTO, CRV, MEASURE, VALUE."""
        measures: dict[str, int] = {}
        for column in self.totals.columns:
            if column.startswith("TOTALHABILITADOS"):
                measures[column] = 2
            elif column == "TOTALVOTOSEMITIDOS":
                measures[column] = 3
            elif column == "TOTALVOTOSOBSERVADOS":
                measures[column] = 15
            elif column in ("TOTALANULADOS", "TOTALVOTOSANULADOS"):
                measures[column] = 5
            elif column in ("TOTALENBLANCO", "TOTALVOTOSENBLANCO"):
                measures[column] = 6
        found = self.totals.select(
            "DEPARTAMENTO",
            "CRV",
            *(pl.col(column).cast(pl.Int64).alias(str(measure)) for column, measure in measures.items()),
        )
        return found.unpivot(index=["DEPARTAMENTO", "CRV"], variable_name="MEASURE", value_name="VALUE").with_columns(
            pl.col("MEASURE").cast(pl.Int64)
        )

    def election(self, kind: str, en: str, es: str) -> str:
        election = key("election", self.day)
        day = date.fromisoformat(self.day)
        self.p.add("election", id=election, polity_id=URUGUAY, kind=kind, date_from=day, date_to=day, status="held")
        self.p.name(election, en, "en", "official")
        self.p.name(election, es, "es", "native", False)
        self.p.add("slug", path=self.path, entity_id=election, is_current=True)
        self.election_id = election
        return election

    def contest(
        self,
        tag: str,
        kind: str,
        title: str,
        slug: str,
        *,
        office: str | None = None,
        body: str | None = None,
        district: str = NATION,
        round_number: int | None = None,
        previous: str | None = None,
        system: str | None = None,
        es: str | None = None,
    ) -> tuple[str, str]:
        contest = key("contest", self.day, tag)
        self.p.add(
            "contest",
            id=contest,
            election_id=self.election_id,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district,
            round_number=round_number,
            previous_round_id=previous,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        self.p.name(contest, title, "en", "official")
        if es:
            self.p.name(contest, es, "es", "native", False)
        path = f"{self.path}/{changeset.slug(slug)}"
        if path in self.slugs:
            raise ValueError(f"duplicate contest slug {path}")
        self.slugs.add(path)
        self.p.add("slug", path=path, entity_id=contest, is_current=True)
        result_set = key("result_set", self.day, tag)
        self.sets.append(result_set)
        self.p.add(
            "result_set",
            id=result_set,
            election_id=self.election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=UY_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        self.p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=BREAKDOWN_METHOD,
            lang="en",
        )
        return contest, result_set

    def option(
        self,
        contest: str,
        kind: str,
        label: str,
        *parts: object,
        lema: str | None = None,
        parent: str | None = None,
        position: int | None = None,
    ) -> str:
        option = key("option", self.day, contest, *parts)
        self.p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind=kind,
            parent_option_id=parent,
            party_id=party(lema) if lema is not None else None,
            ballot_position=position,
            ballot_label=label,
        )
        return option

    def values(self, result_set: str, frame: pl.DataFrame) -> None:
        """Stage values from a frame with UNIT, OPTION (null for unit accounting), MEASURE, and VALUE columns."""
        self.p.frame(
            "result_value",
            frame.group_by("UNIT", "OPTION", "MEASURE", maintain_order=True)
            .agg(pl.col("VALUE").sum())
            .select(
                result_set_id=pl.lit(result_set),
                unit_id="UNIT",
                option_id="OPTION",
                channel=pl.lit(0),
                measure="MEASURE",
                count_round=pl.lit(0),
                flags=pl.lit(0),
                value="VALUE",
            ),
        )

    def accounting_values(
        self, result_set: str, depts: set[str] | None = None, crvs: pl.DataFrame | None = None
    ) -> None:
        found = self.accounting()
        if depts is not None:
            found = found.filter(pl.col("DEPARTAMENTO").is_in(sorted(depts)))
        if crvs is not None:
            found = found.join(crvs.select("DEPARTAMENTO", "CRV").unique(), on=["DEPARTAMENTO", "CRV"], how="semi")
        found = found.join(self.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left")
        self.values(result_set, found.select("UNIT", OPTION=pl.lit(None, pl.String), MEASURE="MEASURE", VALUE="VALUE"))

    def tree(
        self,
        contest: str,
        result_set: str,
        votes: pl.DataFrame,
        *,
        parts: tuple[object, ...] = (),
        lema_kind: str = "legend",
        leaf_kind: str = "list",
    ) -> dict[str, str]:
        """Options for each lema, each ballot sheet beneath it, and a residual option for votes that name no sheet.

        `votes` has DEPARTAMENTO, CRV, LEMAKEY, SHEET (null for votes for a lema alone), and VOTES. Returns the lema
        option of each lema key.
        """
        lemas: dict[str, str] = {}
        sheets: dict[tuple[str, str], str] = {}
        ordered = sorted(votes["LEMAKEY"].unique().to_list())
        labels = names()
        for position, lema in enumerate(ordered, 1):
            lemas[lema] = self.option(contest, lema_kind, labels[lema], *parts, lema, lema=lema, position=position)
        found = votes.filter(pl.col("SHEET").is_not_null()).select("LEMAKEY", "SHEET").unique()
        for lema, sheet in found.sort("LEMAKEY", "SHEET").iter_rows():
            sheets[(lema, sheet)] = self.option(
                contest, leaf_kind, f"List {sheet}", *parts, lema, sheet, lema=lema, parent=lemas[lema]
            )
        option_ids = pl.DataFrame(
            [(lema, sheet, option) for (lema, sheet), option in sheets.items()],
            schema={"LEMAKEY": pl.String, "SHEET": pl.String, "OPTION": pl.String},
            orient="row",
        )
        legend_ids = pl.DataFrame(
            [(lema, option) for lema, option in lemas.items()],
            schema={"LEMAKEY": pl.String, "LEGEND": pl.String},
            orient="row",
        )
        joined = votes.join(self.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left")
        if joined["UNIT"].null_count():
            raise ValueError(f"{self.day}: breakdown rows for circuits missing from the totals")
        joined = joined.join(option_ids, on=["LEMAKEY", "SHEET"], how="left", nulls_equal=True).join(
            legend_ids, on="LEMAKEY", how="left"
        )
        sheet_votes = joined.filter(pl.col("SHEET").is_not_null())
        self.values(
            result_set,
            sheet_votes.select("UNIT", "OPTION", MEASURE=pl.lit(1), VALUE="VOTES"),
        )
        alone = joined.filter(pl.col("SHEET").is_null())
        if alone.height:
            self.values(
                result_set,
                alone.select("UNIT", OPTION="LEGEND", MEASURE=pl.lit(9), VALUE="VOTES"),
            )
        return lemas

    def lema_totals(
        self,
        contest: str,
        result_set: str,
        votes: pl.DataFrame,
        kind: str,
        *,
        parts: tuple[object, ...] = (),
    ) -> dict[str, str]:
        """One option per lema, holding all the votes of its ballot sheets and of the lema alone."""
        labels = names()
        lemas = {
            lema: self.option(contest, kind, labels[lema], *parts, lema, lema=lema, position=position)
            for position, lema in enumerate(sorted(votes["LEMAKEY"].unique().to_list()), 1)
        }
        legend_ids = pl.DataFrame(list(lemas.items()), schema={"LEMAKEY": pl.String, "OPTION": pl.String}, orient="row")
        joined = votes.join(self.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left").join(
            legend_ids, on="LEMAKEY", how="left"
        )
        self.values(result_set, joined.select("UNIT", "OPTION", MEASURE=pl.lit(1), VALUE="VOTES"))
        return lemas


def sheets(frame: pl.DataFrame, record: str, alone: str) -> pl.DataFrame:
    """The votes of one kind of ballot, with the sheet number and null for votes for the lema alone."""
    sheet = frame.filter(pl.col("TIPOREGISTRO") == record).select(
        "DEPARTAMENTO", "CRV", "LEMAKEY", SHEET=pl.col("DESCRIPCION1"), VOTES="CANTIDADVOTOS"
    )
    legend = frame.filter(pl.col("TIPOREGISTRO") == alone).select(
        "DEPARTAMENTO", "CRV", "LEMAKEY", SHEET=pl.lit(None, pl.String), VOTES="CANTIDADVOTOS"
    )
    return pl.concat([sheet, legend])


def election(day: str) -> Package:
    if day in NATIONAL:
        p = national(day)
    elif day in RUNOFF:
        p = runoff(day)
    elif day in PRIMARY:
        p = primary(day)
    else:
        p = local(day)
    reason = (
        "In some circuits the totals file counts more ballots than the circuit's roll lists. In every such circuit "
        "the excess is within the observed ballots (voters registered in another circuit), which the Corte counts "
        "in the circuit's ballots; stored as published"
    )
    if day == "2024-06-30":
        reason = (
            "In 82 circuits the Corte publishes a roll of zero with ballots cast, and in 7 more the excess is "
            "within the observed ballots (voters registered elsewhere); stored as published"
        )
    p.accept("accounting.turnout", reason)
    return p


def national(day: str) -> Package:
    year = day[:4]
    b = Builder(day, f"The {year} Uruguayan national election by circuit from the Corte Electoral")
    b.election(
        "general",
        f"{year} Uruguayan general election",
        f"Elecciones nacionales {year}",
    )
    frame = breakdown(day)
    b.add_units(b.totals)
    votes = sheets(frame, "HOJA_EN", "VOTO_LEMA")
    lema_votes = votes.group_by("DEPARTAMENTO", "CRV", "LEMAKEY").agg(pl.col("VOTES").sum())
    contest, result_set = b.contest(
        "president",
        "office",
        f"{year} Uruguayan presidential election, first round",
        "president",
        office=PRESIDENCY,
        round_number=1,
        system=SYSTEM_PRESIDENT,
        es=f"Elección de Presidente de la República {year}, primera vuelta",
    )
    options = b.lema_totals(contest, result_set, lema_votes, "ticket")
    b.accounting_values(result_set)
    tickets, _ = RUNOFF_TICKETS[next(d for d in RUNOFF if d[:4] == year)]
    ahead = {lema for _, _, lema in tickets}
    for lema, option in options.items():
        b.p.add(
            "outcome", result_set_id=result_set, option_id=option, kind="advanced" if lema in ahead else "eliminated"
        )
    contest, result_set = b.contest(
        "senate",
        "list_allocation",
        f"{year} Uruguayan Senate election",
        "senate",
        body=SENATE_BODY,
        es=f"Elección de Senadores {year}",
    )
    b.lema_totals(contest, result_set, lema_votes, "list")
    b.accounting_values(result_set)
    for dept, text in DEPARTMENTS.items():
        here = votes.filter(pl.col("DEPARTAMENTO") == dept)
        contest, result_set = b.contest(
            f"chamber:{dept}",
            "list_allocation",
            f"{year} Uruguayan Chamber of Representatives election, {text}",
            f"chamber-{text}",
            body=CHAMBER_BODY,
            district=key("area", "uy.departamento", dept),
            es=f"Elección de Representantes {year}, {text}",
        )
        b.tree(contest, result_set, here, parts=(dept,))
        b.accounting_values(result_set, {dept})
    for column, en, es in PLEBISCITES.get(day, []):
        contest, result_set = b.contest(
            f"plebiscite:{column}", "ballot_question", f"{year} {en}", en.split(" (")[0], es=es
        )
        b.p.add("ballot_question", contest_id=contest, type="plebiscite")
        yes = b.option(contest, "answer", "Sí", "yes", position=1)
        found = b.totals.select("DEPARTAMENTO", "CRV", VALUE=pl.col(column).cast(pl.Int64))
        found = found.join(b.unit_frame(), on=["DEPARTAMENTO", "CRV"], how="left")
        b.values(result_set, found.select("UNIT", OPTION=pl.lit(yes), MEASURE=pl.lit(1), VALUE="VALUE"))
        b.accounting_values(result_set)
    b.p.add(
        "note",
        id=key("note", b.election_id, "solo-si"),
        entity_id=b.election_id,
        kind="caveat",
        body_md="The Senate and the Chamber of Representatives are elected with the same ballot sheets as the "
        "President, so each lema's votes are the same in every contest; the Chamber contests split them by sheet, "
        "the department's list. Votes counted for a lema alone, where a voter marked several sheets of one lema, "
        "are stored as legend votes on the lema option. Seats and the electoral system are not stored here.",
        lang="en",
    )
    return b.p


def runoff(day: str) -> Package:
    year = day[:4]
    first = next(d for d in NATIONAL if d[:4] == year)
    b = Builder(day, f"The {year} Uruguayan presidential runoff by circuit from the Corte Electoral")
    b.p.depends.append(f"uy.corte/{first}")
    b.election("runoff", f"{year} Uruguayan presidential runoff", f"Balotaje {year}")
    b.add_units(b.totals)
    contest, result_set = b.contest(
        "president",
        "office",
        f"{year} Uruguayan presidential election, second round",
        "president",
        office=PRESIDENCY,
        round_number=2,
        previous=key("contest", first, "president"),
        system=SYSTEM_PRESIDENT,
        es=f"Elección de Presidente de la República {year}, segunda vuelta",
    )
    tickets, elected = RUNOFF_TICKETS[day]
    sums = {column: int(b.totals[column].cast(pl.Int64).sum()) for column, _, _ in tickets}
    leader = max(tickets, key=lambda t: sums[t[0]])[2]
    if leader != elected:
        raise ValueError(f"{day}: the totals put {leader} ahead, not {elected}")
    unit = b.unit_frame()
    for position, (column, label, lema) in enumerate(tickets, 1):
        option = b.option(contest, "ticket", label, lema, lema=lema, position=position)
        found = b.totals.select("DEPARTAMENTO", "CRV", VALUE=pl.col(column).cast(pl.Int64)).join(
            unit, on=["DEPARTAMENTO", "CRV"], how="left"
        )
        b.values(result_set, found.select("UNIT", OPTION=pl.lit(option), MEASURE=pl.lit(1), VALUE="VALUE"))
        b.p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if lema == elected else "lost")
    b.accounting_values(result_set)
    return b.p


PRIMARY_RECORDS = {
    "ODN": ("HOJA_ODN", {"PREC_ODN", "SUBLEMA_ODN", "VOTOS_PREC"}),
    "ODD": ("HOJA_ODD", {"SUBLEMA_ODD"}),
}


def primary(day: str) -> Package:
    year = day[:4]
    b = Builder(day, f"The {year} Uruguayan party primaries by circuit from the Corte Electoral")
    b.election("primary", f"{year} Uruguayan party primaries", f"Elecciones internas {year}")
    b.add_units(b.totals)
    frame = breakdown(day)
    labels = names()
    for lema in sorted(frame["LEMAKEY"].unique().to_list()):
        for record, body_en, body_es in (
            ("ODN", "National Deliberative Body", "Órgano Deliberativo Nacional"),
            ("ODD", "Departmental Deliberative Body", "Órgano Deliberativo Departamental"),
        ):
            sheet_record, residual_records = PRIMARY_RECORDS[record]
            own = frame.filter(
                (pl.col("LEMAKEY") == lema) & pl.col("TIPOREGISTRO").is_in([sheet_record, *sorted(residual_records)])
            )
            for dept in [None] if record == "ODN" else sorted(DEPARTMENTS):
                found = own if dept is None else own.filter(pl.col("DEPARTAMENTO") == dept)
                if not found.height:
                    continue
                place = "" if dept is None else f", {DEPARTMENTS[dept]}"
                tag = f"{record}:{lema}" + ("" if dept is None else f":{dept}")
                contest, result_set = b.contest(
                    tag,
                    "delegate_allocation",
                    f"{year} {labels[lema]} primary, {body_en}{place}",
                    f"{record}-{labels[lema]}" + ("" if dept is None else f"-{DEPARTMENTS[dept]}"),
                    district=NATION if dept is None else key("area", "uy.departamento", dept),
                    es=f"Elecciones internas {year}, {labels[lema]}, {body_es}{place}",
                )
                sheet = found.filter(pl.col("TIPOREGISTRO") == sheet_record).select(
                    "DEPARTAMENTO", "CRV", "LEMAKEY", SHEET="DESCRIPCION2", VOTES="CANTIDADVOTOS"
                )
                residual = found.filter(pl.col("TIPOREGISTRO") != sheet_record).select(
                    "DEPARTAMENTO", "CRV", "LEMAKEY", SHEET=pl.lit(None, pl.String), VOTES="CANTIDADVOTOS"
                )
                b.tree(contest, result_set, pl.concat([sheet, residual]), parts=(tag,))
                b.accounting_values(result_set, None if dept is None else {dept}, found)
    b.p.add(
        "note",
        id=key("note", b.election_id, "primaries"),
        entity_id=b.election_id,
        kind="caveat",
        body_md="Each party's primary elects its deliberative bodies, and each ballot sheet names a presidential "
        "precandidate. Votes are stored per ballot sheet. Votes the Corte counts for a sublema, for a precandidate, "
        "or for several sheets without a single sheet are stored as legend votes on the party's option. The ballots "
        "in the unit accounting are the circuit's, shared by every party's primary. Presidential precandidate totals "
        "are not stored here.",
        lang="en",
    )
    return b.p


def letter(text: str) -> str | None:
    found = re.search(r"[^\s;-]+-([^\s;-]+)", text)
    return None if found is None else found.group(1)


def local(day: str) -> Package:
    year = day[:4]
    b = Builder(day, f"The {year} Uruguayan departmental and municipal elections by circuit from the Corte Electoral")
    b.election(
        "local",
        f"{year} Uruguayan departmental and municipal elections",
        f"Elecciones departamentales y municipales {year}",
    )
    b.add_units(b.totals)
    frame = breakdown(day)
    municipal = breakdown(day, "municipal") if "municipal" in FILES[day] else frame
    labels = municipalities()
    for dept, text in DEPARTMENTS.items():
        area = key("area", "uy.departamento", dept)
        here = sheets(frame.filter(pl.col("DEPARTAMENTO") == dept), "HOJA_ED", "VOTO_LEMA_ED")
        contest, result_set = b.contest(
            f"intendente:{dept}",
            "office",
            f"{year} election of the Intendente of {text}",
            f"intendente-{text}",
            office=key("office", "intendente", dept),
            district=area,
            es=f"Elección de Intendente de {text} {year}",
        )
        b.tree(contest, result_set, here, parts=(dept,))
        b.accounting_values(result_set, {dept})
        contest, result_set = b.contest(
            f"junta:{dept}",
            "list_allocation",
            f"{year} election of the Departmental Board of {text}",
            f"junta-departamental-{text}",
            body=key("body", "junta", dept),
            district=area,
            es=f"Elección de la Junta Departamental de {text} {year}",
        )
        lemas = here.group_by("DEPARTAMENTO", "CRV", "LEMAKEY").agg(pl.col("VOTES").sum())
        b.lema_totals(contest, result_set, lemas, "list", parts=(dept,))
        b.accounting_values(result_set, {dept})
    rows = municipal.filter(pl.col("TIPOREGISTRO").is_in(["HOJA_EM", "VOTO_LEMA_EM"])).with_columns(
        pl.col("DESCRIPCION1").map_elements(lambda s: letter(s) or "", return_dtype=pl.String).alias("LETTER")
    )
    if (rows["LETTER"] == "").any():
        raise ValueError(f"{day}: municipal rows without a municipality letter")
    for (dept, mark), found in sorted(rows.group_by("DEPARTAMENTO", "LETTER"), key=lambda item: item[0]):
        text = labels[(dept, mark)]
        contest, result_set = b.contest(
            f"alcalde:{dept}:{mark}",
            "office",
            f"{year} election of the Mayor of {text}, {DEPARTMENTS[dept]}",
            f"alcalde-{DEPARTMENTS[dept]}-{text}",
            office=key("office", "alcalde", dept, mark),
            district=key("area", "uy.municipio", dept, mark),
            es=f"Elección de Alcalde de {text}, {DEPARTMENTS[dept]} {year}",
        )
        votes = sheets(found, "HOJA_EM", "VOTO_LEMA_EM")
        b.tree(contest, result_set, votes, parts=(dept, mark))
        b.accounting_values(result_set, {dept}, found)
        b.p.add(
            "note",
            id=key("note", result_set, "municipal"),
            entity_id=result_set,
            kind="caveat",
            body_md="The ballots, observed, invalid, and blank counts are the circuit's totals for the departmental "
            "ballot, as the Corte publishes them; the municipal ballot is counted on the same circuit.",
            lang="en",
        )
    return b.p
