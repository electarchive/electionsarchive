"""Brazil's electoral court (TSE): every election its open data publishes, 1994 to 2026, one package per election date.

Each election package holds the contests of one election day at the finest unit the court publishes for them:
polling sections where a section file exists (1998 and later), municipality-by-zone totals otherwise (1994 and 1996,
and the deputies of 1998 to 2024). The court's municipality-by-zone totals also give the declared figures that the
section sums are checked against.
"""

import codecs
import io
import json
import re
import shutil
import time
import unicodedata
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import uuid5

import httpx
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "br.tse_results"
TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele/"
YEARS = tuple(range(1994, 2027, 2))
SECTION_YEARS = tuple(year for year in YEARS if year >= 1998)
STATES = [
    "AC",
    "AL",
    "AM",
    "AP",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MG",
    "MS",
    "MT",
    "PA",
    "PB",
    "PE",
    "PI",
    "PR",
    "RJ",
    "RN",
    "RO",
    "RR",
    "RS",
    "SC",
    "SE",
    "SP",
    "TO",
]
SECTION_FILES = ("ZZ", "VT", "BR", *STATES)
RETRIES = 8


def munzona(kind: str, year: int) -> str:
    return f"{TSE}{kind}/{kind}_{year}.zip"


def section(year: int, state: str) -> str:
    return f"{TSE}votacao_secao/votacao_secao_{year}_{state}.zip"


def section_detail(year: int) -> str:
    return f"{TSE}detalhe_votacao_secao/detalhe_votacao_secao_{year}.zip"


def year_urls(year: int) -> list[tuple[str, bool]]:
    """The URLs a year needs, each with whether the court is known to publish it for every year."""
    urls = [(munzona(kind, year), True) for kind in ("votacao_candidato_munzona", "votacao_partido_munzona")]
    urls += [(munzona("detalhe_votacao_munzona", year), True), (munzona("consulta_cand", year), True)]
    if year in SECTION_YEARS:
        urls.append((section_detail(year), True))
        urls += [(section(year, state), False) for state in SECTION_FILES]
    return urls


def get(url: str) -> bool:
    """Fetch one file into the raw cache, retrying the court's intermittent refusals; False when it does not exist."""
    try:
        raw.cached(SOURCE, url)
        return True
    except LookupError:
        pass
    for attempt in range(RETRIES):
        try:
            raw.fetch(SOURCE, url, suffix=".zip")
            return True
        except httpx.HTTPStatusError as failure:
            if failure.response.status_code == 404:
                return False
            time.sleep(2 + 3 * attempt)
        except httpx.TransportError:
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"{url} was refused {RETRIES} times")


def fetch(years: tuple[int, ...] = YEARS) -> None:
    for year in years:
        for url, required in year_urls(year):
            if not get(url) and required:
                raise RuntimeError(f"{url} does not exist")


PARSED = DATA / "parsed" / SOURCE
BLANK, NULL = "95", "96"
LIST_OFFICES = (6, 7, 8, 13)
STATE_OFFICES = (3, 5, 6, 7, 8)
MUNICIPAL_OFFICES = (11, 13)
INTS = {"CD_MUNICIPIO", "NR_ZONA", "NR_SECAO", "CD_CARGO", "NR_TURNO", "CD_ELEICAO", "CD_TIPO_ELEICAO"}
VOTES = (
    "QT_VOTOS",
    "QT_VOTOS_NOMINAIS",
    "QT_VOTOS_NOMINAIS_VALIDOS",
    "QT_VOTOS_LEGENDA",
    "QT_VOTOS_LEGENDA_VALIDOS",
    "QT_APTOS",
    "QT_COMPARECIMENTO",
    "QT_VOTOS_BRANCOS",
    "QT_VOTOS_NULOS",
    "QT_VOTOS_ANULADOS_APU_SEP",
    "QT_TOTAL_VOTOS_VALIDOS",
    "QT_VOTOS_LEG_VALIDOS",
    "QT_VOTOS_NOM_CONVR_LEG_VALIDOS",
    "QT_VOTOS_NOMINAIS_CONVR_LEG",
    "QT_TOTAL_VOTOS_LEG_VALIDOS",
    "QT_VOTOS_CONCORRENTES",
    "QT_VOTOS_NULOS_TECNICOS",
    "QT_TOTAL_VOTOS_NULOS",
    "QT_TOTAL_VOTOS_ANULADOS",
    "QT_VOTOS_NOMINAIS_ANULADOS",
    "QT_VOTOS_LEGENDA_ANULADOS",
    "QT_TOTAL_VOTOS_ANUL_SUBJUD",
    "QT_VOTOS_NOMINAIS_ANUL_SUBJUD",
    "QT_VOTOS_LEGENDA_ANUL_SUBJUD",
    "QT_ABSTENCOES",
    "QT_SECOES_NAO_INSTALADAS",
    "QT_TOTAL_SECOES",
)
KEEP = {
    "section": [
        "DT_ELEICAO",
        "NR_TURNO",
        "CD_ELEICAO",
        "CD_TIPO_ELEICAO",
        "DS_ELEICAO",
        "SG_UF",
        "CD_MUNICIPIO",
        "NM_MUNICIPIO",
        "NR_ZONA",
        "NR_SECAO",
        "CD_CARGO",
        "NR_VOTAVEL",
        "NM_VOTAVEL",
        "QT_VOTOS",
        "NR_LOCAL_VOTACAO",
    ],
    "section_detail": [
        "DT_ELEICAO",
        "NR_TURNO",
        "CD_ELEICAO",
        "CD_TIPO_ELEICAO",
        "DS_ELEICAO",
        "SG_UF",
        "CD_MUNICIPIO",
        "NM_MUNICIPIO",
        "NR_ZONA",
        "NR_SECAO",
        "CD_CARGO",
        "QT_APTOS",
        "QT_COMPARECIMENTO",
        "QT_VOTOS_NOMINAIS",
        "QT_VOTOS_BRANCOS",
        "QT_VOTOS_NULOS",
        "QT_VOTOS_LEGENDA",
        "QT_VOTOS_ANULADOS_APU_SEP",
        "NR_LOCAL_VOTACAO",
    ],
    "candidates": [
        "DT_ELEICAO",
        "NR_TURNO",
        "CD_ELEICAO",
        "CD_TIPO_ELEICAO",
        "DS_ELEICAO",
        "SG_UF",
        "SG_UE",
        "NM_UE",
        "CD_MUNICIPIO",
        "NM_MUNICIPIO",
        "NR_ZONA",
        "CD_CARGO",
        "SQ_CANDIDATO",
        "NR_CANDIDATO",
        "NM_CANDIDATO",
        "NM_URNA_CANDIDATO",
        "DS_SITUACAO_CANDIDATURA",
        "TP_AGREMIACAO",
        "NR_PARTIDO",
        "SG_PARTIDO",
        "NM_PARTIDO",
        "NR_FEDERACAO",
        "SG_FEDERACAO",
        "NM_FEDERACAO",
        "SQ_COLIGACAO",
        "NM_COLIGACAO",
        "DS_COMPOSICAO_COLIGACAO",
        "QT_VOTOS_NOMINAIS",
        "QT_VOTOS_NOMINAIS_VALIDOS",
        "DS_SIT_TOT_TURNO",
    ],
    "parties": [
        "DT_ELEICAO",
        "NR_TURNO",
        "CD_ELEICAO",
        "CD_TIPO_ELEICAO",
        "SG_UF",
        "SG_UE",
        "CD_MUNICIPIO",
        "NR_ZONA",
        "CD_CARGO",
        "TP_AGREMIACAO",
        "NR_PARTIDO",
        "SG_PARTIDO",
        "NM_PARTIDO",
        "NR_FEDERACAO",
        "SG_FEDERACAO",
        "NM_FEDERACAO",
        "SQ_COLIGACAO",
        "NM_COLIGACAO",
        "DS_COMPOSICAO_COLIGACAO",
        "QT_VOTOS_LEGENDA",
        "QT_VOTOS_LEGENDA_VALIDOS",
        "QT_VOTOS_NOM_CONVR_LEG_VALIDOS",
        "QT_TOTAL_VOTOS_LEG_VALIDOS",
        "QT_VOTOS_NOMINAIS",
        "QT_VOTOS_NOMINAIS_VALIDOS",
    ],
    "detail": [
        "DT_ELEICAO",
        "NR_TURNO",
        "CD_ELEICAO",
        "CD_TIPO_ELEICAO",
        "DS_ELEICAO",
        "SG_UF",
        "SG_UE",
        "CD_MUNICIPIO",
        "NM_MUNICIPIO",
        "NR_ZONA",
        "CD_CARGO",
        "QT_APTOS",
        "QT_COMPARECIMENTO",
        "QT_VOTOS_BRANCOS",
        "QT_VOTOS_NULOS",
        "QT_TOTAL_VOTOS_VALIDOS",
        "QT_VOTOS_NOMINAIS_VALIDOS",
        "QT_TOTAL_VOTOS_LEG_VALIDOS",
        "QT_VOTOS_LEG_VALIDOS",
        "QT_VOTOS_ANULADOS_APU_SEP",
        "QT_TOTAL_VOTOS_ANULADOS",
        "QT_TOTAL_VOTOS_ANUL_SUBJUD",
        "QT_VOTOS_NULOS_TECNICOS",
    ],
    "roster": [
        "SG_UF",
        "SG_UE",
        "CD_CARGO",
        "NR_TURNO",
        "SQ_CANDIDATO",
        "NR_CANDIDATO",
        "DT_NASCIMENTO",
        "DS_GENERO",
        "NR_TITULO_ELEITORAL_CANDIDATO",
        "DS_SITUACAO_CANDIDATURA",
    ],
}
NAMES = {
    "section": "votacao_secao",
    "section_detail": "detalhe_votacao_secao",
    "candidates": "votacao_candidato_munzona",
    "parties": "votacao_partido_munzona",
    "detail": "detalhe_votacao_munzona",
    "roster": "consulta_cand",
}


def members(url: str) -> Iterator[str]:
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        names = sorted(n for n in archive.namelist() if n.lower().endswith(".csv"))
        whole = [n for n in names if n.upper().endswith("_BRASIL.CSV")]
        yield from whole or names


def unpack(url: str, name: str, target: Path) -> Path:
    """Copy one CSV out of a cached archive as UTF-8, whatever encoding the court used."""
    with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
        with archive.open(name) as source:
            head = source.read(4 << 20)
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            decoder.decode(head, final=False)
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "latin-1"
        with (
            archive.open(name) as source,
            target.open("w", encoding="utf-8", newline="") as out,
        ):
            shutil.copyfileobj(io.TextIOWrapper(source, encoding=encoding, newline=""), out, 16 << 20)
    return target


def table(path: Path, kind: str) -> pl.LazyFrame:
    wanted = KEEP[kind]
    frame = pl.scan_csv(path, separator=";", infer_schema=False, null_values=["#NULO", "#NE", ""])
    present = frame.collect_schema().names()
    frame = frame.select([c for c in wanted if c in present])
    frame = frame.with_columns(pl.lit(None, pl.String).alias(c) for c in wanted if c not in present)
    return frame.select(
        [
            pl.col("DT_ELEICAO").str.to_date("%d/%m/%Y")
            if c == "DT_ELEICAO"
            else pl.col(c).cast(pl.Int64, strict=False)
            if c in INTS or c in VOTES
            else pl.col(c)
            for c in wanted
        ]
    )


def stage(year: int, only: tuple[str, ...] = ()) -> None:
    """Convert a year's cached files into typed Parquet pieces under data/parsed."""
    folder = PARSED / str(year)
    done = folder / ".done"
    if done.exists() and not only:
        return
    folder.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[str, str, str]] = [
        ("candidates", munzona("votacao_candidato_munzona", year), "mz"),
        ("parties", munzona("votacao_partido_munzona", year), "mz"),
        ("detail", munzona("detalhe_votacao_munzona", year), "mz"),
        ("roster", munzona("consulta_cand", year), "mz"),
    ]
    if year in SECTION_YEARS:
        jobs.append(("section_detail", section_detail(year), "sec"))
        jobs += [("section", section(year, state), state) for state in SECTION_FILES]
    for kind, url, tag in jobs:
        if only and kind not in only:
            continue
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            continue
        if only:
            for old in folder.glob(f"{kind}.{tag}.*.parquet"):
                old.unlink()
        elif list(folder.glob(f"{kind}.{tag}.*.parquet")):
            continue
        for number, name in enumerate(members(url)):
            scratch = folder / f".{kind}.{number}.csv"
            unpack(url, name, scratch)
            target = folder / f"{kind}.{tag}.{number:02d}.parquet"
            partial = folder / f".{target.name}.partial"
            table(scratch, kind).sink_parquet(partial, statistics=False)
            scratch.unlink()
            partial.rename(target)
            print(f"{year} {name}: {pl.scan_parquet(target).select(pl.len()).collect().item():,}", flush=True)
    done.write_text("")


# Brazil came into the archive through the database path before packages existed, so its record carries a
# database id rather than the uuid5 of its ISO code; packages reference that record directly. The court's own
# states and sections get schemes of their own here, because br.uf and br.secao belong to that earlier record.
COUNTRY = "01a13ba1-cd2c-7065-8397-2903884f736e"
TSE_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1ftse"))
UFS = {
    "AC": "Acre",
    "AL": "Alagoas",
    "AP": "Amapá",
    "AM": "Amazonas",
    "BA": "Bahia",
    "CE": "Ceará",
    "DF": "Distrito Federal",
    "ES": "Espírito Santo",
    "GO": "Goiás",
    "MA": "Maranhão",
    "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais",
    "PA": "Pará",
    "PB": "Paraíba",
    "PR": "Paraná",
    "PE": "Pernambuco",
    "PI": "Piauí",
    "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul",
    "RO": "Rondônia",
    "RR": "Roraima",
    "SC": "Santa Catarina",
    "SP": "São Paulo",
    "SE": "Sergipe",
    "TO": "Tocantins",
    "ZZ": "Exterior",
    "VT": "Votos em trânsito",
}
OFFICES = {
    1: ("President", "Presidente da República"),
    3: ("Governor", "Governador"),
    5: ("Senator", "Senador"),
    6: ("Federal Deputy", "Deputado Federal"),
    7: ("State Deputy", "Deputado Estadual"),
    8: ("District Deputy", "Deputado Distrital"),
    11: ("Mayor", "Prefeito"),
    13: ("Municipal Councillor", "Vereador"),
}
SENATORS = {1994: 2, 1998: 1, 2002: 2, 2006: 1, 2010: 2, 2014: 1, 2018: 2, 2022: 1, 2026: 2}
ELECTED = r"^(ELEITO|MÉDIA|MEDIA)"
RUNOFF_VOTERS = 200_000


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def area(scheme: str, code: object) -> str:
    return key("area", scheme, code)


def party_id(number: object, abbreviation: object) -> str:
    return key("party", f"{number}:{abbreviation}")


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def cleaned(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Count columns hold -3 where the court's file says 'not informed'; those are unknown, not negative."""
    names = frame.collect_schema().names()
    return frame.with_columns(
        pl.when(pl.col(c) < 0).then(None).otherwise(pl.col(c)).alias(c) for c in VOTES if c in names
    )


Years = int | tuple[int, ...]


def listed(years: Years) -> tuple[int, ...]:
    return (years,) if isinstance(years, int) else years


def scan(years: Years, kind: str) -> pl.LazyFrame:
    frames = []
    for year in listed(years):
        frame = pl.scan_parquet(sorted((PARSED / str(year)).glob(f"{kind}.*.parquet")))
        present = frame.collect_schema().names()
        frames.append(
            cleaned(
                frame.with_columns(
                    pl.lit(None, pl.Int64 if c in INTS or c in VOTES else pl.String).alias(c)
                    for c in KEEP[kind]
                    if c not in present
                )
            )
        )
    return frames[0] if len(frames) == 1 else pl.concat(frames, how="diagonal_relaxed")


def pieces(years: Years, kind: str) -> list[Path]:
    return [path for year in listed(years) for path in sorted((PARSED / str(year)).glob(f"{kind}.*.parquet"))]


def staged_years() -> list[int]:
    return [year for year in YEARS if (PARSED / str(year) / ".done").exists()]


def prepared(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Add the unit of election (`ue`) and the contest key to rows that carry an office."""
    ue = (
        pl.when(pl.col("CD_CARGO") == 1)
        .then(pl.lit("BR"))
        .when(pl.col("CD_CARGO").is_in(STATE_OFFICES))
        .then(pl.col("SG_UF"))
        .otherwise(pl.col("CD_MUNICIPIO").cast(pl.String))
    )
    names = frame.collect_schema().names()
    extra: list[pl.Expr] = []
    if "QT_VOTOS_NOMINAIS_VALIDOS" in names and "QT_VOTOS_NOMINAIS" in names:
        extra.append(pl.coalesce("QT_VOTOS_NOMINAIS_VALIDOS", "QT_VOTOS_NOMINAIS").alias("nominal"))
    if "QT_VOTOS_LEGENDA_VALIDOS" in names:
        extra.append(
            pl.coalesce(
                "QT_TOTAL_VOTOS_LEG_VALIDOS",
                pl.col("QT_VOTOS_LEGENDA_VALIDOS") + pl.col("QT_VOTOS_NOM_CONVR_LEG_VALIDOS").fill_null(0),
                "QT_VOTOS_LEGENDA_VALIDOS",
                "QT_VOTOS_LEGENDA",
            ).alias("legend")
        )
    return frame.with_columns(ue.alias("ue"), *extra, zone=pl.col("NR_ZONA").clip(lower_bound=0)).with_columns(
        ckey=pl.format("{}:{}:{}", pl.col("CD_ELEICAO"), pl.col("CD_CARGO"), pl.col("ue"))
    )


def days() -> dict[date, tuple[int, ...]]:
    found: dict[date, list[int]] = {}
    for year in staged_years():
        for (day,) in scan(year, "candidates").select("DT_ELEICAO").unique().collect().iter_rows():
            found.setdefault(day, []).append(year)
    return {day: tuple(years) for day, years in sorted(found.items())}


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in days())]


def parse() -> None:
    for year in YEARS:
        stage(year)
    geo().write()
    registry().write()
    for day in days():
        settle(election(day))


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Brazil's states, municipalities, and the electoral court's municipality codes, from its results files",
    )
    p.external.add(COUNTRY)
    for code, rank, kind, noun, system in (
        ("br.estado", 1, "administrative", "state", None),
        ("br.municipio", 2, "administrative", "municipality", "tse"),
        ("br.zona", 3, "electoral_district", "electoral zone", None),
        ("br.secao_eleitoral", 4, "precinct", "polling section", None),
    ):
        p.add("scheme", code=code, polity_id=COUNTRY, kind=kind, rank=rank, noun=noun, code_system=system)
    states: dict[str, str] = {}
    towns: dict[int, tuple[str, str, int]] = {}
    for year in staged_years():
        frame = (
            scan(year, "detail")
            .select("SG_UF", "CD_MUNICIPIO", "NM_MUNICIPIO")
            .drop_nulls(["SG_UF", "CD_MUNICIPIO"])
            .unique()
            .collect()
        )
        for uf, code, name in frame.iter_rows():
            towns[int(code)] = (str(uf), str(name), year)
            states[str(uf)] = UFS.get(str(uf), str(uf))
    for uf, name in sorted(states.items()):
        id = area("br.estado", uf)
        p.add("area", id=id, scheme_code="br.estado")
        p.add("area_code", area_id=id, code=uf)
        p.name(id, name, "pt", "official")
    for code, (uf, name, _) in sorted(towns.items()):
        id = area("br.municipio", code)
        p.add("area", id=id, scheme_code="br.municipio")
        p.add("area_code", area_id=id, code=str(code))
        p.add("area_parent", area_id=id, parent_id=area("br.estado", uf))
        p.name(id, name.title() if name.isupper() else name, "pt", "official")
    return p


PRESIDENCY = key("office", "president")
GOVERNOR = key("office", "governor")
MAYOR = key("office", "mayor")
CHAMBER = key("body", "chamber-of-deputies")
SENATE = key("body", "senate")
ASSEMBLY = key("body", "state-legislative-assemblies")
DISTRICT = key("body", "district-legislative-chamber")
COUNCIL = key("body", "municipal-chambers")
SYSTEM_TWO_ROUND = key("electoral_system", "two-round")
SYSTEM_PLURALITY = key("electoral_system", "plurality")
SYSTEM_LIST = key("electoral_system", "open-list-1994")
SYSTEM_LIST_2018 = key("electoral_system", "open-list-2018")
SYSTEM_LIST_2022 = key("electoral_system", "open-list-2022")
SYSTEMS = [
    (
        SYSTEM_TWO_ROUND,
        None,
        None,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A candidate wins in the first round with more than half of the valid votes, not counting blank and null "
            "votes. Otherwise the two leading candidates meet in a second round, and the one with more valid votes wins. "
            "The president, the governors, and the mayors of municipalities with more than 200,000 voters are elected "
            "this way."
        ),
    ),
    (
        SYSTEM_PLURALITY,
        None,
        None,
        {"formula": "block"},
        (
            "The candidates with the most votes win. Senators are elected this way, one or two at a time, and so are the "
            "mayors of municipalities with fewer than 200,000 voters."
        ),
    ),
    (
        SYSTEM_LIST,
        None,
        date(2018, 1, 1),
        {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        (
            "Deputies and councillors are elected from open lists. Voters choose a candidate or a party (a legend vote). "
            "Parties may join a coalition, which runs as one list. The electoral quotient is the valid votes divided by "
            "the seats, and only lists that reach it take seats: one per full quotient, then the remaining seats one at a "
            "time to the list with the highest average, its votes divided by its seats plus one. Each list's seats go to "
            "its candidates in order of personal votes."
        ),
    ),
    (
        SYSTEM_LIST_2018,
        date(2018, 1, 1),
        date(2022, 1, 1),
        {"formula": "dhondt", "lists": {"order": "open", "individual": 0.1, "vacancy": "next_by_votes"}},
        (
            "Open-list proportional representation as in earlier years, except that a candidate needs personal votes of "
            "at least 10 percent of the quotient to take a seat, and, from the 2020 municipal elections, parties cannot "
            "join coalitions for proportional contests."
        ),
    ),
    (
        SYSTEM_LIST_2022,
        date(2022, 1, 1),
        None,
        {"formula": "dhondt", "lists": {"order": "open", "individual": 0.2, "vacancy": "next_by_votes"}},
        (
            "Open-list proportional representation without coalitions, in which parties may form federations that run as "
            "one list. A list needs 80 percent of the quotient and a candidate 20 percent of the quotient to take a seat "
            "in the first distribution of seats; the remaining seats go to the highest averages."
        ),
    ),
]
METHOD = (
    "The electoral court (TSE) publishes each candidate's and each party's votes, and each unit's registered voters, "
    "ballots cast, blank, and null votes. Votes are stored as the court's files give them: candidate votes are the "
    "nominal votes, and legend votes are votes for a party label only. Blank and null votes are not options."
)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Brazil's parties, offices, legislative bodies, and electoral systems, with the electoral court's files",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(COUNTRY)
    p.add(
        "source",
        id=TSE_SOURCE,
        kind="official_body",
        publisher="Tribunal Superior Eleitoral",
        title="Dados abertos do TSE: resultados e candidaturas",
        url="https://dadosabertos.tse.jus.br/",
        license="Creative Commons Attribution",
        license_url="http://www.opendefinition.org/licenses/cc-by",
        attribution="Tribunal Superior Eleitoral",
        notes=METHOD,
    )
    cited: set[str] = set()
    for year in YEARS:
        for url, _ in year_urls(year):
            try:
                artifact = raw.cached(SOURCE, url)
            except LookupError:
                continue
            if artifact.storage_key not in cited:
                cited.add(artifact.storage_key)
                p.artifact(artifact, TSE_SOURCE)
    for id, kind, english, native in (
        (CHAMBER, "lower_house", "Chamber of Deputies", "Câmara dos Deputados"),
        (SENATE, "upper_house", "Federal Senate", "Senado Federal"),
        (ASSEMBLY, "unicameral", "State legislative assemblies", "Assembleias Legislativas"),
        (
            DISTRICT,
            "unicameral",
            "Legislative Chamber of the Federal District",
            "Câmara Legislativa do Distrito Federal",
        ),
        (COUNCIL, "council", "Municipal chambers", "Câmaras Municipais"),
    ):
        p.add("body", id=id, polity_id=COUNTRY, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, native, "pt", "native", False)
    for id, kind, english, native in (
        (PRESIDENCY, "head_of_state", "President of Brazil", "Presidente da República"),
        (GOVERNOR, "governor", "Governor", "Governador"),
        (MAYOR, "mayor", "Mayor", "Prefeito"),
    ):
        p.add("office", id=id, polity_id=COUNTRY, kind=kind)
        p.name(id, english, "en", "official")
        p.name(id, native, "pt", "native", False)
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=COUNTRY, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    names: dict[tuple[str, str], tuple[int, str]] = {}
    for year in staged_years():
        for kind in ("candidates", "parties"):
            frame = (
                scan(year, kind)
                .select("NR_PARTIDO", "SG_PARTIDO", "NM_PARTIDO")
                .drop_nulls(["NR_PARTIDO", "SG_PARTIDO"])
                .unique()
                .collect()
            )
            for number, abbreviation, name in frame.iter_rows():
                names[(str(number), str(abbreviation))] = (year, str(name) if name is not None else str(abbreviation))
    for (number, abbreviation), (_, name) in sorted(names.items()):
        id = party_id(number, abbreviation)
        p.add("party", id=id, kind="party", polity_id=COUNTRY)
        p.add("external_id", entity_id=id, scheme="br.tse_results.party", value=f"{number}:{abbreviation}")
        p.name(id, name, "pt", "official")
        p.name(id, abbreviation, "pt", "abbreviation", False)
    return p


PARTICLES = {"da", "de", "do", "das", "dos", "e", "di", "du", "del", "van", "von", "la", "le"}


def proper(text: str | None) -> str | None:
    if text is None:
        return None
    if text != text.upper():
        return text
    words = text.lower().split(" ")
    return " ".join(w if (i and w in PARTICLES) else w[:1].upper() + w[1:] for i, w in enumerate(words) if w)


def ids(kind: str, *columns: pl.Series) -> pl.Series:
    rows = zip(*(c.cast(pl.String).fill_null("").to_list() for c in columns), strict=True)
    return pl.Series([key(kind, *row) for row in rows], dtype=pl.String)


def listing(cargo: str = "CD_CARGO") -> pl.Expr:
    """The list an option stands on: a federation, a coalition, or a party running alone."""
    return (
        pl.when(pl.col("TP_AGREMIACAO").str.to_uppercase().str.contains("FEDERA"))
        .then(pl.format("F{}", pl.col("NR_FEDERACAO")))
        .when(
            pl.col("TP_AGREMIACAO").str.to_uppercase().str.contains("COLIGA")
            | (pl.col("TP_AGREMIACAO").is_null() & (pl.col("SQ_COLIGACAO").cast(pl.Int64, strict=False) > 0))
        )
        .then(pl.format("C{}", pl.col("SQ_COLIGACAO")))
        .otherwise(pl.format("P{}", pl.col("NR_PARTIDO")))
    )


def listing_label() -> pl.Expr:
    return (
        pl.when(pl.col("lkey").str.starts_with("F"))
        .then(pl.coalesce("NM_FEDERACAO", "SG_FEDERACAO"))
        .when(pl.col("lkey").str.starts_with("C"))
        .then(pl.col("NM_COLIGACAO"))
        .otherwise(pl.col("SG_PARTIDO"))
    )


def priority() -> pl.Expr:
    status = pl.col("DS_SIT_TOT_TURNO").fill_null("").str.to_uppercase()
    return (
        pl.when(status.str.contains(ELECTED))
        .then(0)
        .when(status.str.contains("TURNO"))
        .then(1)
        .when(status.str.contains("SUPLENTE|NÃO ELEITO|NAO ELEITO"))
        .then(2)
        .otherwise(3)
    )


def grains(year: Years, day: date, cands: pl.LazyFrame) -> dict[str, str]:
    """Whether each contest is built from polling sections or from municipality-by-zone totals."""
    wanted = {row[0] for row in cands.select("ckey").unique().collect().iter_rows()}
    zones = dict.fromkeys(wanted, "zone")
    files = pieces(year, "section")
    if not files:
        return zones
    totals = (
        prepared(
            cleaned(pl.scan_parquet(files))
            .filter((pl.col("DT_ELEICAO") == day) & ~pl.col("NR_VOTAVEL").is_in([BLANK, NULL]))
            .select("CD_ELEICAO", "CD_CARGO", "SG_UF", "CD_MUNICIPIO", "NR_ZONA", "QT_VOTOS")
        )
        .group_by("ckey")
        .agg(pl.col("QT_VOTOS").sum().alias("sections"))
        .collect()
    )
    official = cands.group_by("ckey").agg(pl.col("nominal").sum().alias("zones")).collect() if True else None
    legends = (
        prepared(scan(year, "parties").filter(pl.col("DT_ELEICAO") == day))
        .group_by("ckey")
        .agg(pl.col("legend").sum().alias("legend"))
        .collect()
    )
    joined = totals.join(official, on="ckey", how="left").join(legends, on="ckey", how="left")
    for row in joined.iter_rows(named=True):
        official_total = (row["zones"] or 0) + (row["legend"] or 0)
        if official_total and abs(row["sections"] - official_total) <= max(2, official_total // 1000):
            zones[row["ckey"]] = "section"
    return zones


def election(day: date) -> Package:
    year = days()[day]
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"]
    cands = prepared(scan(year, "candidates").filter(pl.col("DT_ELEICAO") == day))
    first = (
        prepared(scan(year, "candidates").filter((pl.col("NR_TURNO") == 1) & (pl.col("DT_ELEICAO") < day)))
        .group_by("CD_CARGO", "ue", "DT_ELEICAO")
        .agg(pl.col("ckey").first())
        .sort("DT_ELEICAO")
        .collect()
    )
    p = Package(
        f"{SOURCE}/{day}", SOURCE, f"Brazil's elections of {day} by polling section and electoral zone", depends
    )
    p.external.add(COUNTRY)
    for url, _ in (item for y in listed(year) for item in year_urls(y)):
        try:
            p.uses(raw.cached(SOURCE, url))
        except LookupError:
            continue
    grain = grains(year, day, cands)
    # candidates
    roster = (
        scan(year, "roster")
        .filter(pl.col("SQ_CANDIDATO").is_not_null())
        .unique(["SG_UE", "CD_CARGO", "SQ_CANDIDATO"], keep="first")
        .select("SG_UE", "CD_CARGO", "SQ_CANDIDATO", "DT_NASCIMENTO", "DS_GENERO")
    )
    people = (
        cands.with_columns(prio=priority(), lkey=listing())
        .sort("ckey", "NR_CANDIDATO", "prio", "SQ_CANDIDATO")
        .group_by("ckey", "NR_CANDIDATO", maintain_order=True)
        .agg(
            pl.col("CD_ELEICAO").first(),
            pl.col("NR_TURNO").first(),
            pl.col("CD_TIPO_ELEICAO").first(),
            pl.col("DS_ELEICAO").first(),
            pl.col("CD_CARGO").first(),
            pl.col("ue").first(),
            pl.col("SG_UF").first(),
            pl.col("SG_UE").first(),
            pl.col("NM_UE").first(),
            pl.col("NM_MUNICIPIO").first(),
            pl.col("SQ_CANDIDATO").first(),
            pl.col("NM_CANDIDATO").first(),
            pl.col("NM_URNA_CANDIDATO").first(),
            pl.col("DS_SITUACAO_CANDIDATURA").first(),
            pl.col("DS_SIT_TOT_TURNO").first(),
            pl.col("NR_PARTIDO").first(),
            pl.col("SG_PARTIDO").first(),
            pl.col("lkey").first(),
            pl.col("NM_COLIGACAO").first(),
            pl.col("NM_FEDERACAO").first(),
            pl.col("SG_FEDERACAO").first(),
            pl.col("prio").first(),
            pl.col("nominal").sum().alias("votes"),
        )
        .join(roster, on=["SG_UE", "CD_CARGO", "SQ_CANDIDATO"], how="left")
        .collect()
    )
    legends = (
        prepared(scan(year, "parties").filter(pl.col("DT_ELEICAO") == day))
        .with_columns(lkey=listing())
        .group_by("ckey", "NR_PARTIDO")
        .agg(
            pl.col("CD_ELEICAO").first(),
            pl.col("NR_TURNO").first(),
            pl.col("CD_TIPO_ELEICAO").first(),
            pl.col("CD_CARGO").first(),
            pl.col("ue").first(),
            pl.col("SG_UF").first(),
            pl.col("SG_PARTIDO").first(),
            pl.col("lkey").first(),
            pl.col("NM_COLIGACAO").first(),
            pl.col("NM_FEDERACAO").first(),
            pl.col("SG_FEDERACAO").first(),
            pl.col("legend").sum().alias("legend"),
        )
        .filter(pl.col("CD_CARGO").is_in(LIST_OFFICES))
        .collect()
    )
    return build(p, year, day, people, legends, grain, first, cands)


ZONE_VALID = (
    pl.when(pl.col("QT_VOTOS_NOMINAIS_VALIDOS").is_null())
    .then(pl.col("QT_TOTAL_VOTOS_VALIDOS"))
    .otherwise(pl.col("QT_VOTOS_NOMINAIS_VALIDOS").fill_null(0) + pl.col("QT_TOTAL_VOTOS_LEG_VALIDOS").fill_null(0))
    .sum()
)
FACT_COLUMNS = ["result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags", "value"]
MEASURES = {2: "registered", 3: "cast", 4: "valid", 5: "invalid", 6: "blank"}
SECTION_NOTE = (
    "Counted by the electoral court per polling section. Registered voters, ballots cast, blank votes, and null "
    "votes come from the court's section details; valid votes are the nominal and legend votes. Each option's total "
    "across the sections is checked against the court's totals by municipality and electoral zone."
)
ZONE_NOTE = (
    "Counted by the electoral court per municipality and electoral zone, the finest unit it publishes for this "
    "contest. Valid votes are the nominal votes and the legend votes."
)


def accounting(frame: pl.LazyFrame, group: list[str], valid: pl.Expr) -> pl.LazyFrame:
    """Unpivot a details table into one row per unit, contest, and accounting measure."""
    sums = frame.group_by(group).agg(
        pl.col("QT_APTOS").sum().alias("m2"),
        pl.col("QT_COMPARECIMENTO").sum().alias("m3"),
        valid.alias("m4"),
        (pl.col("QT_VOTOS_NULOS").sum() + pl.col("QT_VOTOS_ANULADOS_APU_SEP").fill_null(0).sum()).alias("m5"),
        pl.col("QT_VOTOS_BRANCOS").sum().alias("m6"),
    )
    return pl.concat(
        [
            sums.select(
                *group, pl.lit(measure, pl.Int16).alias("measure"), pl.col(f"m{measure}").alias("value")
            ).filter(pl.col("value").is_not_null())
            for measure in MEASURES
        ]
    )


def build(
    p: Package,
    year: Years,
    day: date,
    people: pl.DataFrame,
    legends: pl.DataFrame,
    grain: dict[str, str],
    first: pl.DataFrame,
    cands: pl.LazyFrame,
) -> Package:
    # elections
    contests = (
        people.group_by("ckey")
        .agg(
            pl.col("CD_ELEICAO").first(),
            pl.col("NR_TURNO").first().alias("round"),
            pl.col("CD_TIPO_ELEICAO").first().alias("tipo"),
            pl.col("DS_ELEICAO").first(),
            pl.col("CD_CARGO").first().alias("cargo"),
            pl.col("ue").first(),
            pl.col("SG_UF").first().alias("uf"),
            pl.col("NM_UE").first(),
            pl.col("NM_MUNICIPIO").first().alias("town"),
            (pl.col("prio") == 0).sum().alias("elected"),
        )
        .with_columns(
            group=pl.when(pl.col("tipo") == 2)
            .then(pl.format("ordinary:{}", pl.col("round")))
            .otherwise(pl.format("special:{}", pl.col("CD_ELEICAO")))
        )
        .sort("cargo", "ue")
    )
    elections: dict[str, str] = {}
    for group, cd, tipo, round_, cargo, title in (
        contests.group_by("group")
        .agg(
            pl.col("CD_ELEICAO").first(),
            pl.col("tipo").first(),
            pl.col("round").first(),
            pl.col("cargo").first(),
            pl.col("DS_ELEICAO").first(),
        )
        .sort("group")
        .iter_rows()
    ):
        id = elections[group] = key("election", day, group)
        municipal = cargo in MUNICIPAL_OFFICES
        kind = "special" if tipo != 2 else "runoff" if round_ > 1 else "local" if municipal else "general"
        p.add("election", id=id, polity_id=COUNTRY, kind=kind, date_from=day, date_to=day, status="held")
        label = "municipal elections" if municipal else "general election"
        english = (
            f"{day.year} Brazilian {label}" + (", second round" if round_ > 1 else "")
            if tipo == 2
            else f"{proper(str(title))} ({day})"
        )
        p.name(id, english, "en", "official")
        if title:
            p.name(id, str(proper(str(title))), "pt", "native", False)
        suffix = "second-round" if tipo == 2 and round_ > 1 else "first-round" if tipo == 2 else f"special-{cd}"
        p.add("slug", path=f"br/tse/{day}-{suffix}", entity_id=id, is_current=True)
    previous = {(int(cargo), str(ue)): (str(ckey), first_day) for cargo, ue, first_day, ckey in first.iter_rows()}
    # lists, legends, candidates
    lists = pl.concat(
        [
            people.filter(pl.col("CD_CARGO").is_in(LIST_OFFICES)).select(
                "ckey", "lkey", "NR_PARTIDO", "SG_PARTIDO", "NM_COLIGACAO", "NM_FEDERACAO", "SG_FEDERACAO"
            ),
            legends.select("ckey", "lkey", "NR_PARTIDO", "SG_PARTIDO", "NM_COLIGACAO", "NM_FEDERACAO", "SG_FEDERACAO"),
        ]
    ).unique(["ckey", "lkey"], keep="first")
    lists = lists.with_columns(
        label=listing_label(), option=ids("option", lists["ckey"], pl.Series(["L"] * lists.height), lists["lkey"])
    )
    legends = legends.with_columns(
        option=ids("option", legends["ckey"], pl.Series(["l"] * legends.height), legends["NR_PARTIDO"])
    ).join(lists.select("ckey", "lkey", pl.col("option").alias("parent")), on=["ckey", "lkey"], how="left")
    people = people.with_columns(
        option=ids("option", people["ckey"], pl.Series(["c"] * people.height), people["NR_CANDIDATO"]),
        person=ids("person", people["ckey"], people["NR_CANDIDATO"]),
        candidacy=ids("candidacy", people["ckey"], people["NR_CANDIDATO"]),
    ).join(lists.select("ckey", "lkey", pl.col("option").alias("parent")), on=["ckey", "lkey"], how="left")
    sets = {ckey: key("result_set", ckey) for ckey in contests["ckey"].to_list()}
    # section pass: distinct lines and units
    files = pieces(year, "section")
    seen: list[pl.DataFrame] = []
    sections: list[pl.DataFrame] = []
    section_set = [c for c, g in grain.items() if g == "section"]
    for path in files:
        lines = (
            prepared(cleaned(pl.scan_parquet(path)).filter(pl.col("DT_ELEICAO") == day))
            .filter(pl.col("ckey").is_in(section_set))
            .with_columns(
                letter=pl.when(pl.col("CD_CARGO").is_in(LIST_OFFICES) & (pl.col("NR_VOTAVEL").str.len_chars() == 2))
                .then(pl.lit("l"))
                .otherwise(pl.lit("c"))
            )
        )
        seen.append(
            lines.filter(~pl.col("NR_VOTAVEL").is_in([BLANK, NULL]))
            .group_by("ckey", "letter", "NR_VOTAVEL")
            .agg(pl.col("NM_VOTAVEL").first())
            .collect()
        )
        sections.append(lines.select("SG_UF", "CD_MUNICIPIO", "NM_MUNICIPIO", "zone", "NR_SECAO").unique().collect())
    lines = (
        pl.concat(seen).unique(["ckey", "letter", "NR_VOTAVEL"], keep="first")
        if seen
        else pl.DataFrame(
            schema={"ckey": pl.String, "letter": pl.String, "NR_VOTAVEL": pl.String, "NM_VOTAVEL": pl.String}
        )
    )
    known = pl.concat(
        [
            people.select("ckey", pl.lit("c").alias("letter"), pl.col("NR_CANDIDATO").alias("NR_VOTAVEL")),
            legends.select(
                "ckey", pl.lit("l").alias("letter"), pl.col("NR_PARTIDO").cast(pl.String).alias("NR_VOTAVEL")
            ),
        ]
    )
    extra = lines.join(known, on=["ckey", "letter", "NR_VOTAVEL"], how="anti")
    extra = extra.with_columns(
        option=ids("option", extra["ckey"], extra["letter"], extra["NR_VOTAVEL"]),
        cargo=extra["ckey"].str.split(":").list.get(1).cast(pl.Int64),
    )
    options = pl.concat(
        [
            people.select(
                "option",
                "ckey",
                pl.when(pl.col("CD_CARGO").is_in([1, 3, 11]))
                .then(pl.lit("ticket"))
                .otherwise(pl.lit("candidate"))
                .alias("kind"),
                "parent",
                pl.col("NR_PARTIDO").alias("pn"),
                pl.col("SG_PARTIDO").alias("ps"),
                pl.col("NR_CANDIDATO").cast(pl.Int64, strict=False).alias("position"),
                pl.col("NM_URNA_CANDIDATO").alias("label"),
                pl.col("NR_CANDIDATO").alias("number"),
                pl.lit("c").alias("letter"),
            ),
            legends.select(
                "option",
                "ckey",
                pl.lit("legend").alias("kind"),
                "parent",
                pl.col("NR_PARTIDO").alias("pn"),
                pl.col("SG_PARTIDO").alias("ps"),
                pl.lit(None, pl.Int64).alias("position"),
                pl.col("SG_PARTIDO").alias("label"),
                pl.col("NR_PARTIDO").alias("number"),
                pl.lit("l").alias("letter"),
            ),
            lists.select(
                "option",
                "ckey",
                pl.lit("list").alias("kind"),
                pl.lit(None, pl.String).alias("parent"),
                pl.when(pl.col("lkey").str.starts_with("P")).then(pl.col("NR_PARTIDO")).alias("pn"),
                pl.when(pl.col("lkey").str.starts_with("P")).then(pl.col("SG_PARTIDO")).alias("ps"),
                pl.lit(None, pl.Int64).alias("position"),
                "label",
                pl.lit(None, pl.String).alias("number"),
                pl.lit("L").alias("letter"),
            ),
            extra.select(
                "option",
                "ckey",
                pl.when(pl.col("letter") == "l")
                .then(pl.lit("legend"))
                .when(pl.col("cargo").is_in([1, 3, 11]))
                .then(pl.lit("ticket"))
                .otherwise(pl.lit("candidate"))
                .alias("kind"),
                pl.lit(None, pl.String).alias("parent"),
                pl.lit(None, pl.String).alias("pn"),
                pl.lit(None, pl.String).alias("ps"),
                pl.lit(None, pl.Int64).alias("position"),
                pl.col("NM_VOTAVEL").alias("label"),
                pl.col("NR_VOTAVEL").alias("number"),
                pl.col("letter"),
            ),
        ]
    )
    return finish(
        p, year, day, people, legends, contests, elections, previous, sets, options, lists, grain, sections, cands
    )


def finish(
    p: Package,
    year: Years,
    day: date,
    people: pl.DataFrame,
    legends_frame: pl.DataFrame,
    contests: pl.DataFrame,
    elections: dict[str, str],
    previous: dict[tuple[int, str], tuple[str, date]],
    sets: dict[str, str],
    options: pl.DataFrame,
    lists: pl.DataFrame,
    grain: dict[str, str],
    sections: list[pl.DataFrame],
    cands: pl.LazyFrame,
) -> Package:
    season = day.year
    registered = dict(
        prepared(scan(year, "detail").filter((pl.col("DT_ELEICAO") == day) & (pl.col("CD_CARGO") == 11)))
        .group_by("ckey")
        .agg(pl.col("QT_APTOS").sum())
        .collect()
        .iter_rows()
    )
    used: set[str] = set()
    for row in contests.iter_rows(named=True):
        ckey, cargo, uf, ue = row["ckey"], int(row["cargo"]), str(row["uf"]), str(row["ue"])
        round_ = int(row["round"])
        english, native = OFFICES[cargo]
        town = str(proper(row["town"]) or ue)
        state = UFS.get(uf, uf)
        place = {1: "Brazil", 3: state, 5: state, 6: state, 7: state, 8: state}.get(cargo, town)
        titles = {
            1: f"{season} presidential election",
            3: f"{season} gubernatorial election in {state}",
            5: f"{season} Senate election in {state}",
            6: f"{season} Chamber of Deputies election in {state}",
            7: f"{season} legislative assembly election in {state}",
            8: f"{season} Federal District legislative chamber election",
            11: f"{season} mayoral election in {town}, {uf}",
            13: f"{season} municipal council election in {town}, {uf}",
        }
        title = titles[cargo] + (", second round" if round_ > 1 else "")
        if row["tipo"] != 2:
            title = titles[cargo].replace(str(season), str(day), 1)
        magnitude = (
            1
            if cargo in (1, 3, 11)
            else (int(row["elected"]) or SENATORS.get(season, 1) if row["tipo"] == 2 else int(row["elected"]) or 1)
            if cargo == 5
            else int(row["elected"]) or None
        )
        system = (
            SYSTEM_TWO_ROUND
            if cargo in (1, 3)
            else SYSTEM_PLURALITY
            if cargo == 5
            else None
            if cargo == 11 and season < 2000
            else SYSTEM_TWO_ROUND
            if cargo == 11 and registered.get(ckey, 0) >= RUNOFF_VOTERS
            else SYSTEM_PLURALITY
            if cargo == 11
            else SYSTEM_LIST
            if season < 2018
            else SYSTEM_LIST_2018
            if season < 2022
            else SYSTEM_LIST_2022
        )
        id = key("contest", ckey)
        p.add(
            "contest",
            id=id,
            election_id=elections[row["group"]],
            kind="office" if cargo in (1, 3, 11) else "body_seats" if cargo == 5 else "list_allocation",
            office_id={1: PRESIDENCY, 3: GOVERNOR, 11: MAYOR}.get(cargo),
            body_id={5: SENATE, 6: CHAMBER, 7: ASSEMBLY, 8: DISTRICT, 13: COUNCIL}.get(cargo),
            district_area_id=None
            if cargo == 1
            else area("br.estado", uf)
            if cargo in STATE_OFFICES
            else area("br.municipio", int(ue)),
            magnitude=magnitude,
            round_number=round_,
            previous_round_id=key("contest", previous[(cargo, ue)][0])
            if round_ > 1 and (cargo, ue) in previous
            else None,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(id, title, "en", "official")
        p.name(id, f"{native} {place}" if cargo != 1 else native, "pt", "native", False)
        suffix = (
            "second-round"
            if row["tipo"] == 2 and round_ > 1
            else "first-round"
            if row["tipo"] == 2
            else f"special-{row['CD_ELEICAO']}"
        )
        path = f"br/tse/{day}-{suffix}/{slug(english)}/" + (
            "brazil" if cargo == 1 else slug(state) if cargo in STATE_OFFICES else f"{uf.lower()}-{slug(town)}"
        )
        if path in used:
            path = f"{path}-{ue}"
        used.add(path)
        p.add("slug", path=path, entity_id=id, is_current=True)
        result_set = sets[ckey]
        p.add(
            "result_set",
            id=result_set,
            election_id=elections[row["group"]],
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=TSE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=SECTION_NOTE if grain.get(ckey) == "section" else ZONE_NOTE,
            lang="en",
        )
    for cargo_ue in {(int(r["cargo"]), str(r["ue"])) for r in contests.iter_rows(named=True) if int(r["round"]) > 1}:
        if cargo_ue in previous:
            dependency = f"{SOURCE}/{previous[cargo_ue][1]}"
            if dependency not in p.depends:
                p.depends.append(dependency)
    # options, people, outcomes
    contest_ids = pl.DataFrame({"ckey": list(sets), "contest": [key("contest", c) for c in sets]})
    options = options.join(contest_ids, on="ckey")
    parties = {(str(n), str(a)) for n, a in people.select("NR_PARTIDO", "SG_PARTIDO").unique().iter_rows() if n and a}
    parties |= {(str(n), str(a)) for n, a in options.select("pn", "ps").unique().drop_nulls().iter_rows()}
    party_of = pl.DataFrame(
        {
            "pn": [n for n, _ in sorted(parties)],
            "ps": [a for _, a in sorted(parties)],
            "party": [party_id(n, a) for n, a in sorted(parties)],
        }
    )
    options = options.join(party_of, on=["pn", "ps"], how="left")
    p.frame(
        "contest_option",
        options.select(
            pl.col("option").alias("id"),
            pl.col("contest").alias("contest_id"),
            "kind",
            pl.col("parent").alias("parent_option_id"),
            pl.col("party").alias("party_id"),
            pl.col("position").alias("ballot_position"),
            pl.col("label").map_elements(lambda t: proper(t), return_dtype=pl.String).alias("ballot_label"),
        ),
    )
    members = people.with_columns(
        member=pl.col("NR_PARTIDO").cast(pl.String),
        role=pl.when(pl.col("CD_CARGO").is_in(LIST_OFFICES)).then(pl.lit("list_member")).otherwise(pl.lit("head")),
    ).join(party_of.rename({"pn": "NR_PARTIDO", "ps": "SG_PARTIDO"}), on=["NR_PARTIDO", "SG_PARTIDO"], how="left")
    born = members["DT_NASCIMENTO"].str.to_date("%d/%m/%Y", strict=False)
    born = pl.Series(
        [d if d is not None and 1880 <= d.year <= day.year - 16 else None for d in born.to_list()], dtype=pl.Date
    )
    p.frame(
        "person",
        members.select(
            pl.col("person").alias("id"),
            born.alias("birth_date"),
            pl.when(born.is_not_null()).then(pl.lit("day")).alias("birth_precision"),
            pl.when(pl.col("DS_GENERO") == "MASCULINO")
            .then(pl.lit("male"))
            .when(pl.col("DS_GENERO") == "FEMININO")
            .then(pl.lit("female"))
            .alias("gender"),
        ),
    )
    named = members.filter(pl.col("NM_CANDIDATO").is_not_null()).select(
        pl.col("person").alias("entity_id"),
        pl.col("NM_CANDIDATO").map_elements(lambda t: proper(t), return_dtype=pl.String).alias("text"),
    )
    p.frame(
        "name",
        named.with_columns(
            id=ids("name", named["entity_id"], named["text"]),
            lang=pl.lit("pt"),
            kind=pl.lit("official"),
            is_preferred=pl.lit(True),
        ),
    )
    p.frame(
        "candidacy",
        members.select(
            pl.col("candidacy").alias("id"),
            pl.col("person").alias("person_id"),
            pl.col("option").alias("option_id"),
            "role",
            pl.col("NM_URNA_CANDIDATO").map_elements(lambda t: proper(t), return_dtype=pl.String).alias("ballot_name"),
            pl.col("party").alias("member_of_party_id"),
        ),
    )
    wins = members.filter(pl.col("prio") == 0).select(
        pl.col("option").alias("option_id"), pl.lit("elected").alias("kind"), pl.lit(None, pl.Int32).alias("seats")
    )
    advanced = members.filter(pl.col("prio") == 1).select(
        pl.col("option").alias("option_id"), pl.lit("advanced").alias("kind"), pl.lit(None, pl.Int32).alias("seats")
    )
    seats = (
        members.filter((pl.col("prio") == 0) & pl.col("CD_CARGO").is_in(LIST_OFFICES))
        .group_by("ckey", "lkey")
        .len()
        .join(lists.select("ckey", "lkey", "option"), on=["ckey", "lkey"])
        .select(
            pl.col("option").alias("option_id"),
            pl.lit("won").alias("kind"),
            pl.col("len").cast(pl.Int32).alias("seats"),
        )
    )
    owner = options.select(pl.col("option").alias("option_id"), pl.col("ckey")).join(
        pl.DataFrame({"ckey": list(sets), "result_set_id": list(sets.values())}), on="ckey"
    )
    p.frame(
        "outcome",
        pl.concat([wins, advanced, seats]).join(owner.select("option_id", "result_set_id"), on="option_id"),
    )
    zones, secs = units(p, year, day, sections, cands)
    facts(p, year, day, people, legends_frame, options, sets, grain, zones, secs)
    return p


def units(
    p: Package, year: Years, day: date, sections: list[pl.DataFrame], cands: pl.LazyFrame
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Stage the electoral zones and polling sections of the day, returning their ids by place."""
    detail = [
        prepared(cleaned(pl.scan_parquet(path)).filter(pl.col("DT_ELEICAO") == day))
        .select("SG_UF", "CD_MUNICIPIO", "NM_MUNICIPIO", "zone", "NR_SECAO")
        .unique()
        .collect()
        for path in pieces(year, "section_detail")
    ]
    found = pl.concat([*sections, *detail], how="vertical_relaxed") if sections or detail else None
    secs = (
        found.drop_nulls(["SG_UF", "CD_MUNICIPIO", "NR_SECAO"]).unique(["SG_UF", "CD_MUNICIPIO", "zone", "NR_SECAO"])
        if found is not None
        else pl.DataFrame(schema={"SG_UF": pl.String, "CD_MUNICIPIO": pl.Int64, "zone": pl.Int64, "NR_SECAO": pl.Int64})
    )
    zone_sources = [
        cands.select("SG_UF", "CD_MUNICIPIO", "zone").unique().collect(),
        prepared(scan(year, "detail").filter(pl.col("DT_ELEICAO") == day))
        .select("SG_UF", "CD_MUNICIPIO", "zone")
        .unique()
        .collect(),
        secs.select("SG_UF", "CD_MUNICIPIO", "zone"),
    ]
    zones = pl.concat(zone_sources, how="vertical_relaxed").drop_nulls().unique().sort("SG_UF", "CD_MUNICIPIO", "zone")
    zones = zones.with_columns(
        unit=ids(
            "unit",
            pl.Series([str(day)] * zones.height),
            pl.Series(["z"] * zones.height),
            zones["SG_UF"],
            zones["CD_MUNICIPIO"],
            zones["zone"],
        )
    )
    secs = secs.sort("SG_UF", "CD_MUNICIPIO", "zone", "NR_SECAO")
    secs = secs.with_columns(
        unit=ids(
            "unit",
            pl.Series([str(day)] * secs.height),
            pl.Series(["s"] * secs.height),
            secs["SG_UF"],
            secs["CD_MUNICIPIO"],
            secs["zone"],
            secs["NR_SECAO"],
        )
    ).join(
        zones.select("SG_UF", "CD_MUNICIPIO", "zone", pl.col("unit").alias("parent")),
        on=["SG_UF", "CD_MUNICIPIO", "zone"],
    )
    town = pl.col("CD_MUNICIPIO").map_elements(lambda m: area("br.municipio", m), return_dtype=pl.String)
    kind = pl.when(pl.col("SG_UF") == "ZZ").then(pl.lit("abroad_mission")).otherwise(pl.lit("standard"))
    p.frame(
        "reporting_unit",
        zones.select(
            pl.col("unit").alias("id"),
            pl.lit(COUNTRY).alias("polity_id"),
            pl.lit("br.zona").alias("scheme_code"),
            pl.format("{}:{}:{}:{}", pl.lit(str(day)), "SG_UF", "CD_MUNICIPIO", "zone").alias("code"),
            pl.lit(None, pl.String).alias("parent_id"),
            pl.lit("within").alias("area_relation"),
            town.alias("area_id"),
            kind.alias("kind"),
        ),
    )
    p.frame(
        "reporting_unit",
        secs.select(
            pl.col("unit").alias("id"),
            pl.lit(COUNTRY).alias("polity_id"),
            pl.lit("br.secao_eleitoral").alias("scheme_code"),
            pl.format("{}:{}:{}:{}:{}", pl.lit(str(day)), "SG_UF", "CD_MUNICIPIO", "zone", "NR_SECAO").alias("code"),
            pl.col("parent").alias("parent_id"),
            pl.lit("within").alias("area_relation"),
            town.alias("area_id"),
            kind.alias("kind"),
        ),
    )
    return zones, secs


def shape(frame: pl.DataFrame | pl.LazyFrame) -> pl.LazyFrame:
    lazy = frame.lazy().filter(pl.col("value").is_not_null())
    return lazy.select(
        pl.col("result_set_id"),
        pl.col("unit_id"),
        pl.col("option_id"),
        pl.lit(0, pl.Int16).alias("channel"),
        pl.col("measure").cast(pl.Int16),
        pl.lit(0, pl.Int16).alias("count_round"),
        pl.lit(0, pl.Int16).alias("flags"),
        pl.col("value").cast(pl.Int64),
    )


def facts(
    p: Package,
    year: Years,
    day: date,
    people: pl.DataFrame,
    legends: pl.DataFrame,
    options: pl.DataFrame,
    sets: dict[str, str],
    grain: dict[str, str],
    zones: pl.DataFrame,
    secs: pl.DataFrame,
) -> None:
    folder = PARSED / "facts" / str(day)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    count = 0

    def emit(frame: pl.LazyFrame) -> None:
        nonlocal count
        path = folder / f"{count:03d}.parquet"
        count += 1
        frame.sink_parquet(path, statistics=False)
        p.part("result_value", path)

    by_section = [c for c, g in grain.items() if g == "section"]
    by_zone = [c for c, g in grain.items() if g == "zone"]
    sets_frame = pl.DataFrame({"ckey": list(sets), "result_set_id": list(sets.values())})
    opt_map = options.filter(pl.col("number").is_not_null()).select(
        pl.format("{}|{}|{}", "ckey", "letter", "number").alias("okey"), pl.col("option").alias("option_id")
    )
    section_units = secs.select("SG_UF", "CD_MUNICIPIO", "zone", "NR_SECAO", pl.col("unit").alias("unit_id"))
    zone_units = zones.select("SG_UF", "CD_MUNICIPIO", "zone", pl.col("unit").alias("unit_id"))
    for path in pieces(year, "section"):
        lines = (
            prepared(cleaned(pl.scan_parquet(path)).filter(pl.col("DT_ELEICAO") == day))
            .filter(pl.col("ckey").is_in(by_section) & ~pl.col("NR_VOTAVEL").is_in([BLANK, NULL]))
            .with_columns(
                letter=pl.when(pl.col("CD_CARGO").is_in(LIST_OFFICES) & (pl.col("NR_VOTAVEL").str.len_chars() == 2))
                .then(pl.lit("l"))
                .otherwise(pl.lit("c"))
            )
            .with_columns(okey=pl.format("{}|{}|{}", "ckey", "letter", "NR_VOTAVEL"))
            .group_by("ckey", "okey", "SG_UF", "CD_MUNICIPIO", "zone", "NR_SECAO")
            .agg(pl.col("QT_VOTOS").sum().alias("value"))
            .collect()
        )
        if not lines.height:
            continue
        joined = (
            lines.join(opt_map, on="okey")
            .join(section_units, on=["SG_UF", "CD_MUNICIPIO", "zone", "NR_SECAO"])
            .join(sets_frame, on="ckey")
            .with_columns(measure=pl.lit(1))
        )
        if joined.height != lines.height:
            raise ValueError(f"{path.name}: {lines.height - joined.height} section lines found no option or unit")
        emit(shape(joined))
    for path in pieces(year, "section_detail"):
        rows = accounting(
            prepared(cleaned(pl.scan_parquet(path)).filter(pl.col("DT_ELEICAO") == day)).filter(
                pl.col("ckey").is_in(by_section)
            ),
            ["ckey", "SG_UF", "CD_MUNICIPIO", "zone", "NR_SECAO"],
            (pl.col("QT_VOTOS_NOMINAIS").fill_null(0) + pl.col("QT_VOTOS_LEGENDA").fill_null(0)).sum(),
        ).collect()
        if rows.height:
            emit(
                shape(
                    rows.join(section_units, on=["SG_UF", "CD_MUNICIPIO", "zone", "NR_SECAO"])
                    .join(sets_frame, on="ckey")
                    .with_columns(option_id=pl.lit(None, pl.String))
                )
            )
    cands = prepared(scan(year, "candidates").filter(pl.col("DT_ELEICAO") == day))
    if by_zone:
        votes = (
            cands.filter(pl.col("ckey").is_in(by_zone))
            .group_by("ckey", "SG_UF", "CD_MUNICIPIO", "zone", "NR_CANDIDATO")
            .agg(pl.col("nominal").sum().alias("value"))
            .with_columns(okey=pl.format("{}|c|{}", "ckey", "NR_CANDIDATO"))
            .collect(engine="streaming")
        )
        board = (
            prepared(scan(year, "parties").filter(pl.col("DT_ELEICAO") == day))
            .filter(pl.col("ckey").is_in(by_zone) & pl.col("CD_CARGO").is_in(LIST_OFFICES))
            .group_by("ckey", "SG_UF", "CD_MUNICIPIO", "zone", "NR_PARTIDO")
            .agg(pl.col("legend").sum().alias("value"))
            .with_columns(okey=pl.format("{}|l|{}", "ckey", "NR_PARTIDO"))
            .collect()
        )
        for frame in (votes, board):
            joined = (
                frame.filter(pl.col("value").is_not_null())
                .join(opt_map, on="okey")
                .join(zone_units, on=["SG_UF", "CD_MUNICIPIO", "zone"])
                .join(sets_frame, on="ckey")
                .with_columns(measure=pl.lit(1))
            )
            if joined.height != frame.filter(pl.col("value").is_not_null()).height:
                raise ValueError(f"{day}: zone votes found no option or unit")
            emit(shape(joined))
        rows = accounting(
            prepared(scan(year, "detail").filter(pl.col("DT_ELEICAO") == day)).filter(pl.col("ckey").is_in(by_zone)),
            ["ckey", "SG_UF", "CD_MUNICIPIO", "zone"],
            ZONE_VALID,
        ).collect()
        emit(
            shape(
                rows.join(zone_units, on=["SG_UF", "CD_MUNICIPIO", "zone"])
                .join(sets_frame, on="ckey")
                .with_columns(option_id=pl.lit(None, pl.String))
            )
        )
    compare(year, day, sets, folder)
    # declared totals for the contests counted by section
    if by_section:
        declared_votes = people.filter(pl.col("ckey").is_in(by_section) & (pl.col("votes") > 0)).select(
            "ckey", "option", pl.lit(1).alias("measure"), pl.col("votes").alias("value")
        )
        declared_legends = legends.filter(pl.col("ckey").is_in(by_section) & (pl.col("legend") > 0)).select(
            "ckey", "option", pl.lit(1).alias("measure"), pl.col("legend").alias("value")
        )
        declared_totals = (
            accounting(
                prepared(scan(year, "detail").filter(pl.col("DT_ELEICAO") == day)).filter(
                    pl.col("ckey").is_in(by_section)
                ),
                ["ckey"],
                pl.when(pl.col("QT_VOTOS_NOMINAIS_VALIDOS").is_null())
                .then(pl.col("QT_TOTAL_VOTOS_VALIDOS"))
                .otherwise(
                    pl.col("QT_VOTOS_NOMINAIS_VALIDOS").fill_null(0) + pl.col("QT_TOTAL_VOTOS_LEG_VALIDOS").fill_null(0)
                )
                .sum(),
            )
            .collect()
            .select("ckey", pl.lit(None, pl.String).alias("option"), "measure", "value")
        )
        declared = pl.concat([declared_votes, declared_legends, declared_totals], how="vertical_relaxed").join(
            sets_frame, on="ckey"
        )
        p.frame(
            "declared",
            declared.select(
                "result_set_id",
                pl.col("option").alias("option_id"),
                pl.col("measure").cast(pl.Int16),
                pl.col("value").cast(pl.Int64),
            ),
        )


REASONS = {
    "accounting.votes_valid": (
        "In {n:,} units the electoral court's own files disagree about a contest's valid votes: the candidate and "
        "party votes it lists add up to a different figure than the valid-votes column of its section or "
        "municipality-by-zone details, typically because the party file's legend votes fall short of the details' "
        "legend total or its section lines differ from the section details by a few votes. Every value is stored as "
        "the court published it."
    ),
    "accounting.ballots_sum": (
        "In {n:,} units the court's ballots cast differ from the sum of its valid, blank, and null votes in the same "
        "details file, mostly by one to three ballots. Both figures are stored as the court published them."
    ),
    "accounting.turnout": (
        "In {n:,} units the court reports more ballots cast than registered voters in the same details file. Both "
        "figures are stored as the court published them."
    ),
    "accounting.declared": (
        "In {n:,} totals the sum of the court's polling-section figures differs from its municipality-by-zone totals "
        "for the same contest. The two files come from the court and neither is altered; the zone totals are stored "
        "as declared figures beside the section values."
    ),
}


def settle(p: Package) -> None:
    """Write the package, then accept the accounting findings that the court's own files cause."""
    path = p.write()
    for part in {file.parent for files in p.parts.values() for file in files}:
        shutil.rmtree(part, ignore_errors=True)
    for code, n, _ in package.check(path):
        if code in REASONS:
            p.accept(code, REASONS[code].format(n=n))
    manifest = json.loads((path / "package.json").read_text())
    manifest["accepted"] = p.accepted
    (path / "package.json").write_text(json.dumps(manifest, indent=1) + "\n")


def compare(year: Years, day: date, sets: dict[str, str], folder: Path) -> None:
    """Record how the day's valid-vote totals compare with the court's own municipality-by-zone totals."""
    ours = (
        pl.scan_parquet(sorted(folder.glob("*.parquet")))
        .filter((pl.col("measure") == 4) & pl.col("option_id").is_null())
        .group_by("result_set_id")
        .agg(pl.col("value").sum().alias("ours"))
        .collect()
    )
    court = (
        prepared(scan(year, "detail").filter(pl.col("DT_ELEICAO") == day))
        .group_by("ckey")
        .agg(ZONE_VALID.alias("court"))
        .collect()
    )
    frame = (
        pl.DataFrame({"ckey": list(sets), "result_set_id": list(sets.values())})
        .join(ours, on="result_set_id", how="left")
        .join(court, on="ckey", how="left")
        .with_columns(pl.col("ours").fill_null(0), cargo=pl.col("ckey").str.split(":").list.get(1).cast(pl.Int64))
    )
    rows = (
        frame.group_by("cargo")
        .agg(
            pl.len().alias("contests"),
            (pl.col("ours") == pl.col("court")).sum().alias("equal"),
            pl.col("ours").sum().alias("ours"),
            pl.col("court").sum().alias("court"),
        )
        .sort("cargo")
        .to_dicts()
    )
    target = PARSED / "totals"
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{day}.json").write_text(json.dumps(rows, indent=1) + "\n")
