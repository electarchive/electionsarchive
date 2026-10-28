import csv
import io
import json
import shutil
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.br.tse import SEATS, UFS, proper
from ea.store import DATA, connect

SOURCE = "br.tse_history"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele/"
YEARS = (1994, 1998, 2002, 2006, 2010, 2014, 2018, 2022, 2026)
FILES = {
    (year, kind): f"{TSE}{folder}/{folder}_{year}.zip"
    for year in YEARS
    for kind, folder in (
        ("candidates", "votacao_candidato_munzona"),
        ("parties", "votacao_partido_munzona"),
        ("detail", "detalhe_votacao_munzona"),
        ("roster", "consulta_cand"),
    )
}
POLLING_PLACES = {
    year: f"{TSE}eleitorado_locais_votacao/eleitorado_local_votacao_{year}.zip"
    for year in (2010, 2014, 2018, 2022, 2026)
}
COUNTRIES = {
    "BOSNIAEHERZ": "BIH",
    "BRAZZAVILLE": "COG",
    "CINGAPURA": "SGP",
    "COREIA": "KOR",
    "EMIRADOSARABES": "ARE",
    "GUIANAFRANCESA": "FRA",
    "INGLATERRA": "GBR",
    "EDIMBURGO": "GBR",
    "IRLANDA": "IRL",
    "IRA": "IRN",
    "KATMANDU": "NPL",
    "KAZAQUISTAO": "KAZ",
    "KUAITE": "KWT",
    "KUWAIT": "KWT",
    "KENIA": "KEN",
    "NAGOYA": "JPN",
    "REPDOMINICANA": "DOM",
    "REPUBLICADOMINICANA": "DOM",
    "REPUBLICAESLOVACA": "SVK",
    "REPUBLICATCHECA": "CZE",
    "TERRITPALESTINOS": "PSE",
    "TRINIDADTOBAGO": "TTO",
    "PORTORICO": "PRI",
    "DACCA": "BGD",
    "MARSELHA": "FRA",
    "ORLANDO": "USA",
    "ATLANTA": "USA",
    "PYONGYANG": "PRK",
    "SANTAELENADEUAIREN": "VEN",
}
ABROAD_CODES = {
    "29874": "COD",
    "30120": "ITA",
    "30287": "FRA",
    "30457": "NLD",
    "30481": "CHL",
    "30627": "JPN",
    "30848": "CHN",
    "30864": "CHE",
    "30139": "JPN",
    "30236": "JPN",
    "30724": "JPN",
    "99104": "GEO",
    "99139": "ALB",
    "99147": "ATG",
    "99155": "ARG",
    "99171": "IRQ",
    "99180": "BHS",
    "99198": "NGA",
    "99210": "BOL",
    "99236": "BOL",
    "99244": "URY",
    "99252": "VAT",
    "99287": "ZMB",
    "99317": "EST",
    "99325": "ETH",
    "99333": "FRA",
    "99341": "MWI",
    "99350": "MLI",
    "99376": "MMR",
    "99384": "LCA",
    "99406": "SLE",
    "99422": "GUY",
    "99430": "JAM",
    "99473": "BHR",
}
OFFICES = {"1": "president", "3": "governor", "5": "senate", "6": "chamber", "7": "assembly", "8": "assembly"}
ELECTED = ["ELEITO", "ELEITO POR QP", "ELEITO POR MÉDIA", "MÉDIA"]
WITHDRAWN = ["RENÚNCIA", "FALECIDO", "CANCELAMENTO DO PEDIDO", "HOMOLOGAÇÃO DE DESISTÊNCIA", "CANCELADO"]
DROPPED = {
    "2018:1:5:MT": (
        "The electoral court's regenerated files leave out Selma Arruda, elected to one of Mato Grosso's two seats and "
        "removed by the Superior Electoral Court in December 2019; a supplementary election filled her seat in "
        "November 2020. Her votes are missing here."
    )
}
SENATORS = {1994: 2, 1998: 1, 2002: 2, 2006: 1, 2010: 2, 2014: 1, 2018: 2, 2022: 1, 2026: 2}
SCHEDULED = {2026: date(2026, 10, 25)}
FEED = "https://resultados.tse.jus.br/oficial/ele2026/"
RUNOFFS = {"6258": "1", "6260": "3"}
ASSEMBLIES = {
    "DF": ("Legislative Chamber of the Federal District", "Câmara Legislativa do Distrito Federal"),
}
SOURCE_NOTE = (
    "The electoral court publishes each candidate's and each party's votes by municipality and electoral zone, and "
    "turnout, blank, and null votes for the same units. Its files were regenerated from the final totals, after "
    "later court decisions."
)
GOVERNOR = (
    "A candidate wins in the first round with more than half of the valid votes, not counting blank and null votes. "
    "Otherwise the two leading candidates meet in a second round, and the one with more valid votes wins."
)
SENATE = (
    "Each state elects one or two senators, alternately, for eight-year terms. Voters cast as many votes as there are "
    "seats, and the candidates with the most votes win."
)
QUOTIENT = (
    "Each state elects its deputies at large from open lists. Voters choose a candidate or a party (a legend vote). "
    "Parties may join a coalition, which runs as one list. The electoral quotient is the valid votes divided by the "
    "seats, and only lists that reach it take seats: one per full quotient, then the remaining seats one at a time "
    "to the list with the highest average, its votes divided by its seats plus one. Each list's seats go to its "
    "candidates in order of personal votes. Until 1997, blank votes counted as valid for the quotient."
)
REMAINDERS = (
    "Each state elects its deputies at large from open lists. Voters choose a candidate or a party (a legend vote). "
    "Parties may join a coalition, which runs as one list. The electoral quotient is the valid votes divided by the "
    "seats. Each list gets one seat per full quotient, and the remaining seats go one at a time to the list with the "
    "highest average, its votes divided by its seats plus one; from 2018, lists below the quotient compete for them "
    "too. A list's seats go to its candidates in order of personal votes, and a candidate needs at least 10 percent "
    "of the quotient."
)
EXCESS = (
    "The electoral court's candidate totals here exceed its count of valid nominal votes by {excess} votes. The "
    "difference is votes for candidates who withdrew, died, or were rejected, which the final totals no longer count "
    "as valid."
)


def fetch() -> None:
    for url in [*FILES.values(), *POLLING_PLACES.values()]:
        raw.fetch(SOURCE, url, suffix=".zip")

    def get(path: str) -> bytes:
        for attempt in range(5):
            try:
                response = httpx.get(f"{FEED}{path}", headers={"User-Agent": raw.USER_AGENT}, timeout=60)
                response.raise_for_status()
                return response.content
            except httpx.HTTPError:
                if attempt == 4:
                    raise
                time.sleep(2**attempt)
        raise AssertionError

    for election, cargo in RUNOFFS.items():
        url = f"{FEED}{election}/dados/"
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        scope = {row["cdabr"] for row in json.loads(get(f"{election}/dados/br/br-e00{election}-ab.json"))["abr"]}
        config = get(f"{election}/config/mun-e00{election}-cm.json")
        towns = [
            f"{election}/dados/{state['cd']}/{state['cd']}{town['cd']}-c{cargo.zfill(4)}-e00{election}-u.json"
            for state in json.loads(config)["abr"]
            if cargo == "1" or state["cd"] in scope
            for town in state["mu"]
        ]
        retrieved_at = datetime.now(UTC)
        buffer = io.BytesIO()
        with ThreadPoolExecutor(8) as pool, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"config/mun-e00{election}-cm.json", config)
            for path, content in zip(towns, pool.map(get, towns), strict=True):
                archive.writestr(path.split("/", 1)[1], content)
        raw.store(
            SOURCE, url, buffer.getvalue(), suffix=".zip", retrieved_at=retrieved_at, media_type="application/zip"
        )


def scan(year: int, kind: str) -> pl.LazyFrame:
    with zipfile.ZipFile(raw.cached(SOURCE, FILES[(year, kind)]).path) as archive:
        member = next(name for name in archive.namelist() if name.endswith("_BRASIL.csv"))
        with archive.open(member) as source, (PARSED / member).open("w", encoding="utf-8") as target:
            shutil.copyfileobj(io.TextIOWrapper(source, encoding="latin-1"), target, 16 << 20)
    frame = pl.scan_csv(PARSED / member, separator=";", infer_schema=False)
    present = frame.collect_schema().names()
    return frame.with_columns(
        pl.lit(None, pl.String).alias(column) for column in ("NR_FEDERACAO", "NM_FEDERACAO") if column not in present
    )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames: dict[str, list[pl.DataFrame]] = {"candidates": [], "votes": [], "legends": [], "detail": [], "places": []}
    for year in YEARS:
        contest = pl.concat_str(
            pl.lit(str(year)),
            "NR_TURNO",
            "CD_CARGO",
            pl.when(pl.col("CD_CARGO") == "1").then(pl.lit("BR")).otherwise(pl.col("SG_UF")),
            separator=":",
        )
        municipality = pl.col("CD_MUNICIPIO").str.zfill(5)
        ordinary = (pl.col("CD_TIPO_ELEICAO") == "2") & pl.col("CD_CARGO").is_in(
            [cargo for cargo in OFFICES if year != 2022 or cargo not in ("1", "6")]
        )

        candidates = scan(year, "candidates").filter(ordinary)
        columns = candidates.collect_schema().names()
        valid = "QT_VOTOS_NOMINAIS_VALIDOS" if "QT_VOTOS_NOMINAIS_VALIDOS" in columns else "QT_VOTOS_NOMINAIS"
        void = (
            pl.col("QT_VOTOS_NOMINAIS").cast(pl.Int64) - pl.col(valid).cast(pl.Int64)
            if valid != "QT_VOTOS_NOMINAIS" and year != 1998
            else pl.lit(0, pl.Int64)
        )
        frames["votes"].append(
            candidates.group_by(contest=contest, municipality=municipality, candidate=pl.col("SQ_CANDIDATO"))
            .agg(valid=pl.col(valid).cast(pl.Int64).sum(), void=void.sum())
            .collect()
        )
        detail = scan(year, "detail").filter(ordinary)
        for frame in (candidates, detail):
            frames["places"].append(
                frame.select(
                    year=pl.lit(year),
                    uf="SG_UF",
                    municipality=municipality,
                    label="NM_MUNICIPIO",
                    day=pl.col("DT_ELEICAO").str.to_date("%d/%m/%Y"),
                )
                .unique(["municipality", "day"])
                .collect()
            )
        roster = (
            scan(year, "roster").select("SQ_CANDIDATO", "DT_NASCIMENTO", "DS_GENERO").unique("SQ_CANDIDATO").collect()
        )
        frames["candidates"].append(
            candidates.unique(["NR_TURNO", "CD_CARGO", "SG_UF", "SQ_CANDIDATO"])
            .select(
                contest=contest,
                year=pl.lit(year),
                round="NR_TURNO",
                office=pl.col("CD_CARGO").replace_strict(OFFICES),
                uf=pl.col("SG_UF"),
                candidate="SQ_CANDIDATO",
                number="NR_CANDIDATO",
                name=pl.when(
                    ~pl.col("NM_SOCIAL_CANDIDATO").str.starts_with("#") & (pl.col("NM_SOCIAL_CANDIDATO") != "")
                )
                .then(pl.col("NM_SOCIAL_CANDIDATO"))
                .otherwise(pl.col("NM_CANDIDATO")),
                ballot="NM_URNA_CANDIDATO",
                party_number="NR_PARTIDO",
                party_abbreviation=pl.col("SG_PARTIDO").str.strip_chars(),
                party_label="NM_PARTIDO",
                coalition=pl.when(pl.col("TP_AGREMIACAO").str.to_uppercase().is_in(["COLIGAÇÃO", "FEDERAÇÃO"])).then(
                    pl.when(pl.col("TP_AGREMIACAO").str.to_uppercase() == "FEDERAÇÃO")
                    .then(pl.concat_str(pl.lit(f"{year}:F"), "NR_FEDERACAO"))
                    .otherwise(pl.concat_str(pl.lit(str(year)), "SQ_COLIGACAO", separator=":"))
                ),
                coalition_label=pl.when(pl.col("TP_AGREMIACAO").str.to_uppercase() == "FEDERAÇÃO")
                .then(pl.col("NM_FEDERACAO"))
                .otherwise(pl.col("NM_COLIGACAO")),
                coalition_members=pl.col("DS_COMPOSICAO_COLIGACAO").str.replace_all(
                    r"FEDERAÇÃO[^(/]*\(([^)]*)\)", "$1"
                ),
                status="DS_SIT_TOT_TURNO",
                situation="DS_SITUACAO_CANDIDATURA",
            )
            .unique(["contest", "candidate"])
            .collect()
            .join(roster, left_on="candidate", right_on="SQ_CANDIDATO", how="left")
            .with_columns(
                pl.when(pl.col("contest").str.split(":").list.get(2) == "1")
                .then(pl.lit("BR"))
                .otherwise(pl.col("uf"))
                .alias("uf"),
                birth=pl.col("DT_NASCIMENTO").str.to_date("%d/%m/%Y", strict=False),
                gender=pl.col("DS_GENERO").replace_strict(
                    {"MASCULINO": "male", "FEMININO": "female"}, default=None, return_dtype=pl.String
                ),
                outcome=pl.when(pl.col("status").is_in(ELECTED) & ~pl.col("situation").is_in(WITHDRAWN))
                .then(pl.lit("elected"))
                .when(pl.col("status") == "2º TURNO")
                .then(pl.lit("advanced")),
            )
            .drop("DT_NASCIMENTO", "DS_GENERO", "status", "situation")
        )

        parties = scan(year, "parties").filter(ordinary & pl.col("CD_CARGO").is_in(["6", "7", "8"]))
        names = parties.collect_schema().names()
        legend = (
            "QT_TOTAL_VOTOS_LEG_VALIDOS"
            if year == 1998
            else "QT_VOTOS_LEGENDA_VALIDOS"
            if "QT_VOTOS_LEGENDA_VALIDOS" in names
            else "QT_VOTOS_LEGENDA"
        )
        frames["legends"].append(
            parties.group_by(contest=contest, municipality=municipality, party_number=pl.col("NR_PARTIDO"))
            .agg(
                votes=pl.col(legend).cast(pl.Int64).sum(),
                party_abbreviation=pl.col("SG_PARTIDO").str.strip_chars().first(),
                party_label=pl.col("NM_PARTIDO").first(),
                coalition=pl.when(pl.col("TP_AGREMIACAO").str.to_uppercase().is_in(["COLIGAÇÃO", "FEDERAÇÃO"]))
                .then(
                    pl.when(pl.col("TP_AGREMIACAO").str.to_uppercase() == "FEDERAÇÃO")
                    .then(pl.concat_str(pl.lit(f"{year}:F"), "NR_FEDERACAO"))
                    .otherwise(pl.concat_str(pl.lit(str(year)), "SQ_COLIGACAO", separator=":"))
                )
                .first(),
            )
            .collect()
        )
        frames["detail"].append(
            detail.group_by(contest=contest, municipality=municipality)
            .agg(
                registered=pl.col("QT_APTOS").cast(pl.Int64).sum(),
                voters=pl.col("QT_COMPARECIMENTO").cast(pl.Int64).sum(),
                blank=pl.col("QT_VOTOS_BRANCOS").cast(pl.Int64).sum(),
                null=pl.col("QT_VOTOS_NULOS").cast(pl.Int64).sum(),
                nominal=pl.col("QT_VOTOS_NOMINAIS_VALIDOS").cast(pl.Int64).sum(),
            )
            .collect()
        )
        for path in PARSED.glob("*.csv"):
            path.unlink()

    if not frames["candidates"][-1].filter(pl.col("round") == "2").height:
        rows: dict[str, list[dict[str, object]]] = {"votes": [], "detail": [], "places": []}
        for election, cargo in RUNOFFS.items():
            with zipfile.ZipFile(raw.cached(SOURCE, f"{FEED}{election}/dados/").path) as archive:
                labels = {
                    town["cd"]: town["nm"]
                    for state in json.loads(archive.read(f"config/mun-e00{election}-cm.json"))["abr"]
                    for town in state["mu"]
                }
                for member in archive.namelist():
                    if not member.startswith("dados/"):
                        continue
                    town = json.loads(archive.read(member))
                    uf = member.split("/")[1].upper()
                    contest = f"2026:2:{cargo}:{'BR' if cargo == '1' else uf}"
                    municipality = town["cdabr"]
                    if town["tf"] != "s":
                        raise ValueError(f"{member} is not fully counted")
                    for group in town["carg"][0]["agr"]:
                        for party in group["par"]:
                            for candidate in party["cand"]:
                                if candidate["dvt"] != "Válido":
                                    raise ValueError(f"{member}: {candidate['nm']} is {candidate['dvt']}")
                                rows["votes"].append(
                                    {
                                        "contest": contest,
                                        "municipality": municipality,
                                        "candidate": candidate["sqcand"],
                                        "valid": int(candidate["vap"]),
                                        "void": 0,
                                    }
                                )
                    rows["detail"].append(
                        {
                            "contest": contest,
                            "municipality": municipality,
                            "registered": int(town["e"]["te"]),
                            "voters": int(town["e"]["c"]),
                            "blank": int(town["v"]["vb"]),
                            "null": int(town["v"]["tvn"]),
                            "nominal": int(town["v"]["vnom"]),
                        }
                    )
                    rows["places"].append(
                        {
                            "year": 2026,
                            "uf": uf,
                            "municipality": municipality,
                            "label": labels[municipality],
                            "day": SCHEDULED[2026],
                        }
                    )
        for kind, frame_rows in rows.items():
            template = frames[kind][-1]
            frames[kind].append(pl.DataFrame(frame_rows, schema=template.schema))

    candidates = pl.concat(frames["candidates"])
    pending = candidates.filter(
        (pl.col("round") == "1") & (pl.col("outcome") == "advanced") & pl.col("year").is_in(list(SCHEDULED))
    )
    candidates = pl.concat(
        [
            candidates,
            pending.with_columns(
                contest=pl.col("contest").str.replace(r"^(\d+):1:", "$1:2:"), round=pl.lit("2"), outcome=pl.lit(None)
            ).join(candidates, on=["contest", "candidate"], how="anti"),
        ]
    )
    votes = pl.concat(frames["votes"])
    legends = pl.concat(frames["legends"])
    detail = pl.concat(frames["detail"])
    places = pl.concat(frames["places"]).unique(["year", "municipality", "day"])

    totals = votes.group_by("contest", "candidate").agg(pl.col("valid").sum())
    race = pl.col("contest").str.replace(r"^(\d+):\d:", "$1:")
    runoffs = candidates.filter((pl.col("round") == "2") | (pl.col("outcome") == "advanced")).select(race=race).unique()
    rank = pl.col("votes").rank("ordinal", descending=True).over("contest")
    majority = (
        candidates.filter(pl.col("office").is_in(["president", "governor", "senate"]))
        .join(totals, on=["contest", "candidate"], how="left")
        .with_columns(votes=pl.col("valid").fill_null(0), race=race)
        .join(runoffs.with_columns(runoff=pl.lit(True)), on="race", how="left")
        .with_columns(
            derived=pl.when((pl.col("round") == "1") & pl.col("runoff").fill_null(False))
            .then(pl.when(rank <= 2).then(pl.lit("advanced")))
            .when(
                rank <= pl.when(pl.col("office") == "senate").then(pl.col("year").replace_strict(SENATORS)).otherwise(1)
            )
            .then(pl.lit("elected"))
        )
    )
    disputed = majority.filter(
        pl.col("outcome").is_not_null()
        & (pl.col("outcome") != pl.col("derived").fill_null(""))
        & ~pl.col("contest").is_in(list(DROPPED))
        & ~((pl.col("outcome") == "elected") & (pl.col("derived") == "advanced"))
    )
    if disputed.height:
        raise ValueError(f"declared outcomes differ from the vote ranking: {disputed}")
    candidates = (
        candidates.join(
            majority.filter(~pl.col("contest").is_in(list(DROPPED))).select("contest", "candidate", "derived"),
            on=["contest", "candidate"],
            how="left",
        )
        .with_columns(
            outcome=pl.when(
                pl.col("office").is_in(["president", "governor", "senate"]) & ~pl.col("contest").is_in(list(DROPPED))
            )
            .then(pl.col("derived"))
            .otherwise(pl.col("outcome"))
        )
        .drop("derived")
    )

    failures: list[str] = []
    elected = candidates.filter(pl.col("outcome") == "elected")
    for (code,), frame in elected.group_by("contest"):
        year, _, office, uf = str(code).split(":")
        expected = (
            SEATS[uf]
            if office == "6"
            else SENATORS[int(year)]
            if office == "5"
            else None
            if office in ("7", "8")
            else 1
        )
        if expected is not None and frame.height != expected - (str(code) in DROPPED):
            failures.append(f"{code}: {frame.height} elected, expected {expected}")
    for year in YEARS:
        if year == 2022:
            continue
        chamber = elected.filter((pl.col("year") == year) & (pl.col("office") == "chamber")).height
        if chamber != sum(SEATS.values()):
            failures.append(f"{year}: {chamber} deputies elected")
    unknown = votes.join(candidates, on=["contest", "candidate"], how="anti")
    if unknown.height:
        failures.append(f"votes for unknown candidates: {unknown.head(5)}")
    if failures:
        raise ValueError("\n".join(failures))

    excess = (
        votes.group_by("contest")
        .agg(pl.col("valid").sum())
        .join(detail.group_by("contest").agg(pl.col("nominal").sum()), on="contest")
        .select("contest", excess=pl.col("valid") - pl.col("nominal"))
        .filter(pl.col("excess") != 0)
    )
    if excess.filter(pl.col("excess") < 0).height:
        raise ValueError(
            f"candidate totals fall short of the valid nominal votes: {excess.filter(pl.col('excess') < 0)}"
        )

    candidates.write_parquet(PARSED / "candidates.parquet")
    votes.write_parquet(PARSED / "votes.parquet")
    legends.write_parquet(PARSED / "legends.parquet")
    detail.write_parquet(PARSED / "detail.parquet")
    places.write_parquet(PARSED / "places.parquet")
    abroad: list[tuple[int, str, str, str]] = []
    for year, url in POLLING_PLACES.items():
        with zipfile.ZipFile(raw.cached(SOURCE, url).path) as archive:
            member = next(name for name in archive.namelist() if name.endswith(".csv"))
            with archive.open(member) as source:
                reader = csv.DictReader(io.TextIOWrapper(source, encoding="latin-1"), delimiter=";")
                abroad.extend(
                    (year, row["CD_MUNICIPIO"].zfill(5), row["NM_MUNICIPIO"], row["NM_BAIRRO"])
                    for row in reader
                    if row["SG_UF"] == "ZZ"
                )
    pl.DataFrame(abroad, schema=["year", "municipality", "label", "country"], orient="row").unique().write_parquet(
        PARSED / "abroad_places.parquet"
    )
    excess.write_parquet(PARSED / "excess.parquet")


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    legends = pl.read_parquet(PARSED / "legends.parquet")
    detail = pl.read_parquet(PARSED / "detail.parquet")
    places = pl.read_parquet(PARSED / "places.parquet")
    excess = dict(pl.read_parquet(PARSED / "excess.parquet").iter_rows())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Brazil's general elections from 1994 to 2022 and in 2026 by municipality: president, governors, "
                "senators, federal deputies, and state deputies"
            ),
            polity="BR",
        )

        def name(entity: UUID, text: str, lang: str = "pt", kind: str = "official") -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def slug(text: str) -> str:
            return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace(" ", "-")

        results: dict[int, UUID] = {}
        for year in YEARS:
            for kind, title, dataset in (
                ("results", f"Resultados - {year}", f"resultados-{year}"),
                ("candidates", f"Candidatos - {year}", f"candidatos-{year}"),
            ):
                source = cs.source("Tribunal Superior Eleitoral", title)
                if kind == "results":
                    results[year] = source
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher="Tribunal Superior Eleitoral",
                    title=title,
                    url=f"https://dadosabertos.tse.jus.br/dataset/{dataset}",
                    license="Creative Commons Attribution",
                    license_url="http://www.opendefinition.org/licenses/cc-by",
                    notes=SOURCE_NOTE if kind == "results" else None,
                )
                for file in ("roster",) if kind == "candidates" else ("candidates", "parties", "detail"):
                    cs.artifact(raw.cached(SOURCE, FILES[(year, file)]), source)
        feed = None
        if votes.filter(pl.col("contest").str.starts_with("2026:2:")).height:
            feed = cs.source("Tribunal Superior Eleitoral", "Resultados - 2026, 2º turno")
            cs.add(
                "source",
                id=feed,
                kind="official_body",
                publisher="Tribunal Superior Eleitoral",
                title="Resultados - 2026, 2º turno",
                url="https://resultados.tse.jus.br/oficial/app/index.html",
                notes=(
                    "The electoral court's results service publishes each municipality's final totals for the second "
                    "round, for president and for governor in the states that held a runoff. Its open-data files did "
                    "not yet carry the second round when these were loaded."
                ),
            )
            for election in RUNOFFS:
                cs.artifact(raw.cached(SOURCE, f"{FEED}{election}/dados/"), feed)

        brazil = cs.polity("BR")
        states = {uf: cs.area("br.uf", uf) for uf in UFS}
        labels = {uf: "the Federal District" if uf == "DF" else label for uf, label in UFS.items()}

        president = cs.key("br.office", "president")
        chamber = cs.key("br.body", "chamber-of-deputies")
        senate = cs.key("br.body", "senate")
        if senate in cs.minted:
            cs.add("body", id=senate, polity_id=brazil, kind="upper_house")
            name(senate, "Federal Senate", "en")
            name(senate, "Senado Federal", "pt", "native")
            cs.add("body_size", body_id=senate, seats=81, valid_from=date(1991, 2, 1))
        governors: dict[str, UUID] = {}
        assemblies: dict[str, UUID] = {}
        for uf, label in UFS.items():
            governors[uf] = cs.key("br.office", f"governor:{uf}")
            if governors[uf] in cs.minted:
                cs.add("office", id=governors[uf], polity_id=cs.polity(f"BR-{uf}"), kind="governor")
                name(governors[uf], f"Governor of {labels[uf]}", "en")
            assemblies[uf] = cs.key("br.body", f"assembly:{uf}")
            if assemblies[uf] in cs.minted:
                cs.add("body", id=assemblies[uf], polity_id=cs.polity(f"BR-{uf}"), kind="unicameral")
                english, native = ASSEMBLIES.get(
                    uf, (f"Legislative Assembly of {label}", f"Assembleia Legislativa de {label}")
                )
                name(assemblies[uf], english, "en")
                name(assemblies[uf], native, "pt", "native")

        systems: dict[str, UUID] = {}
        for key, rules, label, methodology in (
            ("presidential-two-round", None, None, None),
            ("chamber-open-list", None, None, None),
            ("governor-two-round", {"formula": "two_round"}, "Brazilian gubernatorial two-round system", GOVERNOR),
            ("senate-plurality", {"formula": "block"}, "Brazilian Senate plurality system", SENATE),
            (
                "open-list-quotient",
                {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}},
                "Brazilian open-list proportional representation, 1994–2014",
                QUOTIENT,
            ),
            (
                "open-list-2018",
                {"formula": "dhondt", "lists": {"order": "open", "individual": 0.1, "vacancy": "next_by_votes"}},
                "Brazilian open-list proportional representation, 2018",
                REMAINDERS,
            ),
        ):
            systems[key] = cs.key("br.electoral_system", key)
            if systems[key] in cs.minted and rules is not None and label is not None:
                cs.add("electoral_system", id=systems[key], polity_id=brazil, rules=rules, rules_version=1)
                name(systems[key], label, "en")
                cs.add("note", entity_id=systems[key], kind="methodology", body_md=methodology)

        days = places.group_by("year").agg(first=pl.col("day").min(), last=pl.col("day").max())
        held = set(places["day"])
        rounds: dict[str, UUID] = {}
        cycles: dict[int, UUID] = {}
        dates: dict[str, date] = {}
        for year, first, last in days.sort("year").iter_rows():
            last = SCHEDULED.get(year, last)
            cycles[year] = cs.key("br.election", f"{first}:general-elections")
            cs.add(
                "election",
                id=cycles[year],
                polity_id=brazil,
                kind="general",
                date_from=first,
                date_to=last,
                status="held",
            )
            name(cycles[year], f"{year} Brazilian general election", "en")
            name(cycles[year], f"Eleições gerais de {year}", "pt", "native")
            cs.add("slug", path=f"br/{first}-general-elections", entity_id=cycles[year])
            for round, day, ordinal, stage in (("1", first, "first", "general"), ("2", last, "second", "runoff")):
                code = f"{year}:{round}"
                dates[code] = day
                rounds[code] = cs.key("br.election", str(day))
                cs.add(
                    "election",
                    id=rounds[code],
                    polity_id=brazil,
                    parent_id=cycles[year],
                    kind=stage,
                    date_from=day,
                    date_to=day,
                    status="held" if day in held else "scheduled",
                )
                name(rounds[code], f"{year} Brazilian general election, {ordinal} round", "en")
                cs.add("slug", path=f"br/{day}-{stage}", entity_id=rounds[code])

        abroad = pl.col("uf").is_in(["ZZ", "VT"])
        domestic = places.filter(~abroad).sort("year").unique("municipality", keep="last")
        areas = {code: cs.key("br.tse.municipality", code) for code in domestic["municipality"]}
        for code, uf, label in domestic.select("municipality", "uf", "label").iter_rows():
            if areas[code] in cs.minted:
                cs.add("area", id=areas[code], scheme_code="br.municipality")
                cs.add("area_parent", area_id=areas[code], parent_id=states[uf], valid_from=None)
                name(areas[code], proper(label))

        def fold(text: str) -> str:
            return "".join(
                character
                for character in unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
                if character.isalnum()
            )

        world: dict[str, UUID] = {}
        for code, text, area in conn.execute(
            """select ac.code, n.text, a.id from area a join area_code ac on ac.area_id = a.id
               join name n on n.entity_id = a.id where a.scheme_code = 'world.country' and n.lang in ('pt', 'en')
               order by n.lang desc"""
        ).fetchall():
            world.setdefault(fold(text), area)
            world.setdefault(code, area)

        def country_of(text: str) -> UUID | None:
            return world.get(COUNTRIES.get(fold(text), fold(text)))

        countries: dict[str, UUID] = {}
        for code, named in (
            pl.read_parquet(PARSED / "abroad_places.parquet")
            .group_by("municipality")
            .agg(pl.col("country").unique().sort())
            .iter_rows()
        ):
            found = {country for country in map(country_of, named) if country is not None}
            if len(found) > 1:
                raise ValueError(f"abroad municipality {code} sits in several countries: {named}")
            if found:
                countries[code] = found.pop()
        countries |= {code: world[country] for code, country in ABROAD_CODES.items()}
        units: dict[str, str] = {}
        rows: list[dict[str, object]] = []
        for year, code, uf, label in (
            places.unique(["year", "municipality"]).select("year", "municipality", "uf", "label").iter_rows()
        ):
            first, last = dates[f"{year}:1"], dates[f"{year}:2"]
            unit = cs.unit("br.municipality", code, first)
            units[f"{year}:{code}"] = str(unit)
            if uf in ("ZZ", "VT"):
                cs.add("name", entity_id=unit, text=proper(label), lang="pt", kind="official", is_preferred=True)
            country = countries.get(code) or (country_of(label) if uf == "ZZ" else None)
            if country is not None:
                cs.add(
                    "reporting_unit_area",
                    unit_id=unit,
                    area_id=country,
                    valid_from=first,
                    valid_to=last + timedelta(days=1),
                    method="source_code",
                )
            rows.append(
                {
                    "id": str(unit),
                    "polity_id": str(brazil),
                    "scheme_code": "br.municipality",
                    "code": code,
                    "area_relation": "unlocated" if uf in ("ZZ", "VT") else "equals",
                    "area_id": None if uf in ("ZZ", "VT") else str(areas[code]),
                    "kind": "abroad_mission" if uf == "ZZ" else "standard",
                    "valid_from": first,
                    "valid_to": last + timedelta(days=1),
                }
            )
        cs.frame("reporting_unit", pl.DataFrame(rows))
        for code, section in conn.execute(
            "select code, id from reporting_unit where scheme_code = 'br.secao' and kind = 'abroad_mission'"
        ).fetchall():
            if code.split(":")[0] in countries:
                cs.add(
                    "reporting_unit_area",
                    unit_id=section,
                    area_id=countries[code.split(":")[0]],
                    valid_from=None,
                    method="source_code",
                )

        parties: dict[str, UUID] = {}
        by_abbreviation: dict[tuple[int, str], UUID] = {}
        for year, number, abbreviation, label in (
            pl.concat(
                [
                    candidates.select("year", "party_number", "party_abbreviation", "party_label"),
                    legends.select(
                        year=pl.col("contest").str.slice(0, 4).cast(pl.Int32),
                        party_number="party_number",
                        party_abbreviation="party_abbreviation",
                        party_label="party_label",
                    ),
                ],
                how="vertical_relaxed",
            )
            .unique(["year", "party_number", "party_abbreviation"])
            .sort("year", "party_number")
            .iter_rows()
        ):
            key = f"{number}:{abbreviation}"
            if key not in parties:
                parties[key] = cs.key("br.tse.party", key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind="party", polity_id=brazil)
                    name(parties[key], proper(label))
                    name(parties[key], abbreviation, kind="abbreviation")
            by_abbreviation[(year, abbreviation.replace(" ", "").upper())] = parties[key]

        alliances: dict[str, UUID] = {}
        for coalition, year, label, members in (
            candidates.filter(pl.col("coalition").is_not_null())
            .select("coalition", "year", "coalition_label", "coalition_members")
            .unique("coalition")
            .sort("coalition")
            .iter_rows()
        ):
            alliances[coalition] = cs.key(f"{SOURCE}.coalition", coalition)
            cs.add("alliance", id=alliances[coalition], kind="electoral_alliance", election_id=cycles[year])
            name(alliances[coalition], proper(label))
            for member in {part.strip().replace(" ", "").upper() for part in (members or "").split("/")}:
                if (year, member) in by_abbreviation:
                    cs.add(
                        "alliance_member", alliance_id=alliances[coalition], party_id=by_abbreviation[(year, member)]
                    )

        valid = (
            votes.group_by("contest")
            .agg(pl.col("valid").sum())
            .join(legends.group_by("contest").agg(legend=pl.col("votes").sum()), on="contest", how="left")
            .join(detail.group_by("contest").agg(pl.col("blank").sum()), on="contest", how="left")
        )
        totals = {code: (votes + (legend or 0), blank or 0) for code, votes, legend, blank in valid.iter_rows()}
        slates = (
            pl.concat(
                [
                    candidates.filter(pl.col("office").is_in(["chamber", "assembly"])).select(
                        "contest", "party_number", "party_abbreviation", "coalition"
                    ),
                    legends.select("contest", "party_number", "party_abbreviation", "coalition"),
                ]
            )
            .sort("coalition", nulls_last=True)
            .unique(["contest", "party_number"], keep="first")
        )

        persons: dict[str, UUID] = {
            f"{slug(text)}|{birth}": entity
            for entity, text, birth in conn.execute(
                """
                select p.id, n.text, p.birth_date from person p
                join external_id x on x.entity_id = p.id and x.scheme = 'br.tse.person'
                join name n on n.entity_id = p.id and n.kind = 'official'
                where p.birth_date is not null and not exists (select from entity_merge m where m.entity_id = p.id)
                """
            ).fetchall()
        }
        for value, entity in conn.execute(
            """
            select x.value, x.entity_id from external_id x
            where x.scheme = 'br.tse_history.person'
              and not exists (select from entity_merge m where m.entity_id = x.entity_id)
            """
        ).fetchall():
            if value in persons and persons[value] != entity:
                cs.merge(entity, persons[value])
        options: dict[str, str] = {}
        result_sets: dict[str, str] = {}
        contests: dict[str, UUID] = {}
        tallied = set(detail["contest"].unique())
        for code in sorted(candidates["contest"].unique()):
            year, round, cargo, uf = code.split(":")
            office = OFFICES[cargo]
            day = dates[f"{year}:{round}"]
            standing = candidates.filter(pl.col("contest") == code)
            elected = standing.filter(pl.col("outcome") == "elected").height
            label = labels.get(uf, "")
            runoff = f"{year}:2:{cargo}:{uf}" in candidates["contest"]
            suffix = f", {'second' if round == '2' else 'first'} round" if runoff or round == "2" else ""
            stage = "runoff" if round == "2" else "general"
            area = None if uf == "BR" else states[uf]
            place = slug(UFS.get(uf, ""))
            if office == "president":
                key, path, title = f"{day}:president", "president", f"{year} Brazilian presidential election{suffix}"
            elif office == "governor":
                key, path, title = (
                    f"{day}:governor:{uf}",
                    f"governor-{place}",
                    f"{year} gubernatorial election in {label}{suffix}",
                )
            elif office == "senate":
                key, path, title = f"{day}:senate:{uf}", f"senate-{place}", f"{year} Senate election in {label}"
            elif office == "chamber":
                key, path, title = (
                    f"{day}:chamber-of-deputies:{uf}",
                    f"chamber-of-deputies-{place}",
                    f"{year} Chamber of Deputies election in {label}",
                )
            else:
                key, path, title = (
                    f"{day}:assembly:{uf}",
                    f"legislative-{'chamber' if uf == 'DF' else 'assembly'}-{place}",
                    f"{year} Legislative {'Chamber' if uf == 'DF' else 'Assembly'} election in {label}",
                )
            contest = contests[code] = cs.key("br.contest", key)
            magnitude = (
                1 if office in ("president", "governor") else SENATORS[int(year)] if office == "senate" else elected
            )
            override = None
            if office in ("chamber", "assembly") and int(year) in SCHEDULED:
                override = {"threshold": {"percent": 80 / magnitude, "level": "list"}}
            elif office in ("chamber", "assembly") and year not in ("2018", "2022"):
                counted, blank = totals[code]
                share = (counted + blank) / counted if year == "1994" else 1
                override = {"threshold": {"percent": float(f"{100 * share / magnitude:.6f}"), "level": "list"}}
            cs.add(
                "contest",
                id=contest,
                election_id=rounds[f"{year}:{round}"],
                kind={"president": "office", "governor": "office", "senate": "body_seats"}.get(
                    office, "list_allocation"
                ),
                office_id=president if office == "president" else governors[uf] if office == "governor" else None,
                body_id=senate
                if office == "senate"
                else chamber
                if office == "chamber"
                else assemblies[uf]
                if office == "assembly"
                else None,
                district_area_id=area,
                magnitude=magnitude,
                round_number=int(round),
                previous_round_id=contests[f"{year}:1:{cargo}:{uf}"] if round == "2" else None,
                electoral_system_id=systems[
                    "presidential-two-round"
                    if office == "president"
                    else "governor-two-round"
                    if office == "governor"
                    else "senate-plurality"
                    if office == "senate"
                    else "chamber-open-list"
                    if int(year) in SCHEDULED
                    else "open-list-2018"
                    if year in ("2018", "2022")
                    else "open-list-quotient"
                ],
                rules_override=override,
                default_result_kind="counted",
            )
            name(contest, title, "en")
            cs.add("slug", path=f"br/{day}-{stage}/{path}", entity_id=contest)
            if code in excess:
                cs.add("note", entity_id=contest, kind="caveat", body_md=EXCESS.format(excess=f"{excess[code]:,}"))
            if code in DROPPED:
                cs.add("note", entity_id=contest, kind="caveat", body_md=DROPPED[code])
            result_set = cs.key(f"{SOURCE}.result_set", key) if code in tallied else None
            if result_set is not None:
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=rounds[f"{year}:{round}"],
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=feed if feed is not None and code.startswith("2026:2:") else results[int(year)],
                    is_default=True,
                    completeness=1,
                )
            if result_set is not None and result_set in cs.minted:
                result_sets[code] = str(result_set)

            lists: dict[str, UUID] = {}
            if office in ("chamber", "assembly"):
                for number, abbreviation, coalition in (
                    slates.filter(pl.col("contest") == code)
                    .select("party_number", "party_abbreviation", "coalition")
                    .sort("party_number")
                    .iter_rows()
                ):
                    party = parties[f"{number}:{abbreviation}"]
                    group = coalition or f"{number}:{abbreviation}"
                    if group not in lists:
                        lists[group] = cs.key("br.option", f"{key}:list:{group}")
                        cs.add(
                            "contest_option",
                            id=lists[group],
                            contest_id=contest,
                            kind="list",
                            party_id=None if coalition else party,
                            alliance_id=alliances[coalition] if coalition else None,
                            ballot_label=abbreviation if coalition is None else None,
                        )
                    legend = cs.key("br.option", f"{key}:legend:{number}")
                    options[f"{code}:legend:{number}"] = str(legend)
                    cs.add(
                        "contest_option",
                        id=legend,
                        contest_id=contest,
                        kind="legend",
                        parent_option_id=lists[group],
                        party_id=party,
                        ballot_label=abbreviation,
                    )
            seats: dict[str, int] = {}
            for row in standing.sort("number").iter_rows(named=True):
                person_key = (
                    f"{slug(row['name'])}|{row['birth']}" if row["birth"] is not None else f"{year}:{row['candidate']}"
                )
                if person_key not in persons:
                    persons[person_key] = cs.key(f"{SOURCE}.person", person_key)
                    if persons[person_key] in cs.minted:
                        cs.add(
                            "person",
                            id=persons[person_key],
                            gender=row["gender"],
                            birth_date=row["birth"],
                            birth_precision="day" if row["birth"] is not None else None,
                        )
                        name(persons[person_key], proper(row["name"]))
                party = parties[f"{row['party_number']}:{row['party_abbreviation']}"]
                option = cs.key("br.option", f"{key}:{row['candidate']}")
                options[f"{code}:{row['candidate']}"] = str(option)
                group = row["coalition"] or f"{row['party_number']}:{row['party_abbreviation']}"
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="ticket" if office in ("president", "governor") else "candidate",
                    parent_option_id=lists.get(group),
                    party_id=party,
                    alliance_id=alliances[row["coalition"]] if row["coalition"] is not None and not lists else None,
                    ballot_label=proper(row["ballot"]),
                )
                cs.add(
                    "candidacy",
                    id=cs.key("br.candidacy", f"{key}:{row['candidate']}"),
                    person_id=persons[person_key],
                    option_id=option,
                    role="list_member" if lists else "head",
                    ballot_name=row["ballot"],
                    member_of_party_id=party,
                )
                if row["outcome"] is not None:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=row["outcome"])
                    if lists and row["outcome"] == "elected":
                        seats[group] = seats.get(group, 0) + 1
            for group, count in seats.items():
                cs.add("outcome", result_set_id=result_set, option_id=lists[group], kind="won", seats=count)

        counted = list(result_sets)
        year = pl.col("contest").str.slice(0, 4)
        unit = (year + ":" + pl.col("municipality")).replace_strict(units)
        common = {"channel": pl.lit(0, pl.Int16), "count_round": pl.lit(0, pl.Int16), "flags": pl.lit(0, pl.Int16)}
        live = pl.col("contest").is_in(counted)
        cs.frame(
            "result_value",
            pl.concat(
                [
                    votes.filter(live)
                    .unpivot(on=["valid", "void"], index=["contest", "municipality", "candidate"])
                    .filter(pl.col("value") > 0)
                    .select(
                        "contest",
                        "municipality",
                        option=pl.col("contest") + ":" + pl.col("candidate"),
                        measure=pl.col("variable").replace_strict({"valid": 1, "void": 5}, return_dtype=pl.Int16),
                        value="value",
                    ),
                    legends.filter(live & (pl.col("votes") > 0)).select(
                        "contest",
                        "municipality",
                        option=pl.col("contest") + ":legend:" + pl.col("party_number"),
                        measure=pl.lit(1, pl.Int16),
                        value="votes",
                    ),
                    detail.filter(live)
                    .unpivot(on=["registered", "voters", "blank", "null"], index=["contest", "municipality"])
                    .select(
                        "contest",
                        "municipality",
                        option=pl.lit(None, pl.String),
                        measure=pl.col("variable").replace_strict(
                            {"registered": 2, "voters": 14, "blank": 6, "null": 5}, return_dtype=pl.Int16
                        ),
                        value="value",
                    ),
                ]
            ).select(
                result_set_id=pl.col("contest").replace_strict(result_sets),
                unit_id=unit,
                option_id=pl.col("option").replace_strict(options, default=None),
                measure="measure",
                value="value",
                **common,
            ),
        )
        return cs.write()
