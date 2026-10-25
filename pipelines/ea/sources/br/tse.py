import io
import re
import shutil
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "br.tse"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele/"
FILES = {
    "president": f"{TSE}votacao_secao/votacao_secao_2022_BR.zip",
    "acre": f"{TSE}votacao_secao/votacao_secao_2022_AC.zip",
    "detail": f"{TSE}detalhe_votacao_secao/detalhe_votacao_secao_2022.zip",
    "candidates": f"{TSE}consulta_cand/consulta_cand_2022.zip",
    "places": f"{TSE}eleitorado_locais_votacao/eleitorado_local_votacao_2022.zip",
    "deputies": f"{TSE}votacao_candidato_munzona/votacao_candidato_munzona_2022.zip",
    "legends": f"{TSE}votacao_partido_munzona/votacao_partido_munzona_2022.zip",
}
MEMBERS = (
    ("president", "votacao_secao_2022_BR.csv", "1"),
    ("acre", "votacao_secao_2022_AC.csv", "6"),
    ("detail", "detalhe_votacao_secao_2022_BR.csv", "1"),
    ("detail", "detalhe_votacao_secao_2022_AC.csv", "6"),
)
CANDIDATE = [
    "CD_ELEICAO",
    "CD_CARGO",
    "SG_UF",
    "SQ_CANDIDATO",
    "NR_CANDIDATO",
    "NM_CANDIDATO",
    "NM_SOCIAL_CANDIDATO",
    "NM_URNA_CANDIDATO",
    "DS_SITUACAO_CANDIDATURA",
    "TP_AGREMIACAO",
    "NR_PARTIDO",
    "SG_PARTIDO",
    "NM_PARTIDO",
    "NR_FEDERACAO",
    "SG_FEDERACAO",
    "NM_FEDERACAO",
    "DS_COMPOSICAO_FEDERACAO",
    "SQ_COLIGACAO",
    "NM_COLIGACAO",
    "DS_COMPOSICAO_COLIGACAO",
    "DT_NASCIMENTO",
    "DS_GENERO",
    "DS_SIT_TOT_TURNO",
]
FIRST, SECOND, STATE = "544", "545", "546"
DAYS = {FIRST: date(2022, 10, 2), SECOND: date(2022, 10, 30), STATE: date(2022, 10, 2)}
CLOSED = date(2022, 10, 31)
SEATS = {
    "SP": 70,
    "MG": 53,
    "RJ": 46,
    "BA": 39,
    "RS": 31,
    "PR": 30,
    "PE": 25,
    "CE": 22,
    "MA": 18,
    "GO": 17,
    "PA": 17,
    "SC": 16,
    "PB": 12,
    "ES": 10,
    "PI": 10,
    "AL": 9,
    "AC": 8,
    "AM": 8,
    "AP": 8,
    "DF": 8,
    "MS": 8,
    "MT": 8,
    "RN": 8,
    "RO": 8,
    "RR": 8,
    "SE": 8,
    "TO": 8,
}
COORDINATES = {"NR_LATITUDE": (-34.0, 6.0), "NR_LONGITUDE": (-74.5, -28.5)}
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
}
PARTICLES = {
    "a",
    "à",
    "as",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "para",
    "por",
}
SOURCE_NOTE = (
    "The electoral court publishes results for each polling section. It numbers municipalities with its own codes, "
    "not IBGE's, and gives polling-place addresses but no coordinates."
)
PRESIDENT = (
    "A candidate wins in the first round with more than half of the valid votes. Blank and null votes do not count "
    "as valid. If no candidate gets there, the two leading candidates meet in a second round on the last Sunday of "
    "October, and the one with more valid votes wins."
)
CHAMBER = (
    "Each state elects its federal deputies at large from open lists. Voters choose either a candidate or a party; a "
    "vote for a party is a legend vote. The electoral quotient is the state's valid votes (candidate plus legend) "
    "divided by its seats. Each list first gets one seat per full quotient. Remaining seats go one at a time to the "
    "list with the highest average, meaning its votes divided by its seats plus one. From 2022, only lists with at "
    "least 80 percent of the quotient, and candidates with at least 20 percent of it, may take them. A list's seats "
    "go to its candidates in order of personal votes, and a candidate needs at least 10 percent of the quotient for a "
    "quotient seat. Parties in a federation run as a single list."
)
DISQUALIFIED = (
    "The electoral court rejected this candidate's registration. The name stayed on the voting machines, but votes "
    "cast for it were annulled and count for neither the candidate nor the party."
)
RESIDUAL = (
    "Outside Acre, the electoral court's municipal totals leave out votes for some candidates whose registrations it "
    "rejected. Those votes count as null here, so each municipality's null votes are its section totals minus every "
    "vote the municipal files assign."
)
NOT_OPENED = "This section did not open on {day}."
ANNULLED = "The electoral court annulled this section's vote on {day}."


def proper(text: str) -> str:
    words = text.strip().lower().split()
    return " ".join(
        word if index and word in PARTICLES else "'".join(part[:1].upper() + part[1:] for part in word.split("'"))
        for index, word in enumerate(words)
    )


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    scans: dict[str, list[pl.LazyFrame]] = {"votes": [], "detail": []}
    for key, member, office in MEMBERS:
        with (
            zipfile.ZipFile(raw.cached(SOURCE, FILES[key]).path) as archive,
            archive.open(member) as source,
            (PARSED / member).open("w", encoding="utf-8") as target,
        ):
            shutil.copyfileobj(io.TextIOWrapper(source, encoding="latin-1"), target, 16 << 20)
        scans["detail" if key == "detail" else "votes"].append(
            pl.scan_csv(PARSED / member, separator=";", infer_schema=False).filter(pl.col("CD_CARGO") == office)
        )
    municipality = pl.col("CD_MUNICIPIO").str.zfill(5)
    section = pl.concat_str(
        municipality, pl.col("NR_ZONA").cast(pl.Int64), pl.col("NR_SECAO").cast(pl.Int64), separator=":"
    )
    contest = (
        pl.when(pl.col("CD_ELEICAO") == STATE)
        .then(pl.concat_str("CD_ELEICAO", "SG_UF", separator=":"))
        .otherwise(pl.col("CD_ELEICAO"))
    )
    deputies = pl.col("contest").str.starts_with(STATE)
    place = {
        "contest": contest,
        "uf": pl.col("SG_UF"),
        "municipality": municipality,
        "municipality_label": pl.col("NM_MUNICIPIO"),
        "zone": pl.col("NR_ZONA").cast(pl.Int64),
        "number": pl.col("NR_SECAO").cast(pl.Int64),
        "section": section,
    }
    votes = (
        pl.concat(scans["votes"])
        .select(
            **place,
            choice=pl.col("NR_VOTAVEL"),
            candidate=pl.col("SQ_CANDIDATO"),
            votes=pl.col("QT_VOTOS").cast(pl.Int64),
        )
        .collect()
    )
    counts = {
        "registered": "QT_APTOS",
        "voters": "QT_COMPARECIMENTO",
        "nominal": "QT_VOTOS_NOMINAIS",
        "blank": "QT_VOTOS_BRANCOS",
        "null": "QT_VOTOS_NULOS",
        "legend": "QT_VOTOS_LEGENDA",
    }
    detail = (
        pl.concat(scans["detail"])
        .select(
            **place,
            **{field: pl.col(column).cast(pl.Int64) for field, column in counts.items()},
            opened=pl.col("ST_SECAO_INSTALADA") == "Sim",
            annulled=pl.col("ST_SECAO_ANULADA") == "Sim",
        )
        .collect()
    )
    for _, member, _ in MEMBERS:
        (PARSED / member).unlink()

    votes = votes.with_columns(
        kind=pl.when(pl.col("choice") == "95")
        .then(pl.lit("blank"))
        .when(pl.col("choice") == "96")
        .then(pl.lit("null"))
        .when(deputies & (pl.col("choice").str.len_chars() <= 2))
        .then(pl.lit("legend"))
        .otherwise(pl.lit("nominal"))
    )
    keys = ["contest", "section", "kind"]
    tallies = votes.group_by(keys).agg(pl.col("votes").sum())
    expected = detail.unpivot(
        on=["nominal", "blank", "null", "legend"], index=["contest", "section"], variable_name="kind", value_name="sum"
    )
    mismatched = expected.join(tallies, on=keys, how="left").filter(pl.col("sum") != pl.col("votes").fill_null(0))
    unmatched = tallies.join(expected, on=keys, how="anti")
    if mismatched.height or unmatched.height:
        raise ValueError(f"section votes differ from the section details: {mismatched.head(5)} {unmatched.head(5)}")

    with zipfile.ZipFile(raw.cached(SOURCE, FILES["places"]).path) as archive:
        registry = pl.read_csv(
            io.TextIOWrapper(archive.open("eleitorado_local_votacao_2022.csv"), encoding="latin-1").read().encode(),
            separator=";",
            infer_schema=False,
            columns=["NR_TURNO", "CD_MUNICIPIO", "NR_ZONA", "NR_SECAO", "DS_TIPO_SECAO_AGREGADA"] + list(COORDINATES),
        )
    points = (
        registry.filter(pl.col("DS_TIPO_SECAO_AGREGADA") == "Principal")
        .select("NR_TURNO", *(pl.col(column).cast(pl.Float64) for column in COORDINATES), section=section)
        .filter(
            pl.col("NR_LATITUDE").is_between(*COORDINATES["NR_LATITUDE"])
            & pl.col("NR_LONGITUDE").is_between(*COORDINATES["NR_LONGITUDE"])
        )
        .sort("NR_TURNO")
        .unique("section", keep="first")
        .select("section", location=pl.format("SRID=4326;POINT({} {})", "NR_LONGITUDE", "NR_LATITUDE"))
    )
    located = pl.concat([frame.select(column for column in place if column != "contest") for frame in (votes, detail)])
    sections = located.unique("section", keep="first").join(points, on="section", how="left")
    if sections.filter(pl.col("uf") != "ZZ")["uf"].unique().len() != len(UFS):
        raise ValueError("sections do not cover every state")

    municipal: dict[str, pl.DataFrame] = {}
    for key, columns in (
        (
            "deputies",
            {
                "candidate": pl.col("SQ_CANDIDATO"),
                "choice": pl.col("NR_CANDIDATO"),
                "void": pl.col("NM_TIPO_DESTINACAO_VOTOS").str.starts_with("Anulado"),
                "votes": pl.col("QT_VOTOS_NOMINAIS").cast(pl.Int64),
            },
        ),
        (
            "legends",
            {
                "choice": pl.col("NR_PARTIDO"),
                "valid": pl.col("QT_VOTOS_LEGENDA_VALIDOS").cast(pl.Int64),
                "void": pl.col("QT_VOTOS_LEGENDA_ANULADOS").cast(pl.Int64)
                + pl.col("QT_VOTOS_LEGENDA_ANUL_SUBJUD").cast(pl.Int64),
            },
        ),
        ("detail", {field: pl.col(column).cast(pl.Int64) for field, column in counts.items()}),
    ):
        frames: list[pl.DataFrame] = []
        with zipfile.ZipFile(raw.cached(SOURCE, FILES[key]).path) as archive:
            for uf in UFS:
                if uf == "AC":
                    continue
                member = next(name for name in archive.namelist() if name.endswith(f"_2022_{uf}.csv"))
                with archive.open(member) as source, (PARSED / member).open("w", encoding="utf-8") as target:
                    shutil.copyfileobj(io.TextIOWrapper(source, encoding="latin-1"), target, 16 << 20)
                frames.append(
                    pl.scan_csv(PARSED / member, separator=";", infer_schema=False)
                    .filter(pl.col("CD_CARGO") == "6")
                    .select(contest=contest, municipality=municipality, **columns)
                    .collect()
                )
                (PARSED / member).unlink()
        frame = pl.concat(frames)
        keys = [column for column in ("contest", "municipality", "candidate", "choice") if column in frame.columns]
        keys += ["void"] if key == "deputies" else []
        municipal[key] = frame.group_by(keys).agg(pl.exclude(keys).sum())
    assigned = (
        pl.concat(
            [
                municipal["deputies"].select("contest", "municipality", "votes"),
                municipal["legends"].select("contest", "municipality", votes=pl.col("valid") + pl.col("void")),
            ]
        )
        .group_by("contest", "municipality")
        .agg(assigned=pl.col("votes").sum())
    )
    municipal["detail"] = (
        municipal["detail"]
        .join(assigned, on=["contest", "municipality"], how="full", coalesce=True)
        .with_columns(pl.col("assigned").fill_null(0))
    )
    residual = pl.col("nominal") + pl.col("legend") - pl.col("assigned")
    if municipal["detail"].filter(residual.is_null() | (residual < 0)).height:
        raise ValueError(f"municipal votes exceed the section details: {municipal['detail'].filter(residual < 0)}")
    municipal["detail"] = municipal["detail"].with_columns(null=pl.col("null") + residual)

    with zipfile.ZipFile(raw.cached(SOURCE, FILES["candidates"]).path) as archive:
        roster = pl.read_csv(
            io.TextIOWrapper(archive.open("consulta_cand_2022_BRASIL.csv"), encoding="latin-1").read().encode(),
            separator=";",
            infer_schema=False,
            columns=CANDIDATE,
        ).with_columns(contest=contest)
    parties = roster.select(number="NR_PARTIDO", abbreviation="SG_PARTIDO", label="NM_PARTIDO").unique().sort("number")
    federations = (
        roster.filter(pl.col("NR_FEDERACAO") != "-1")
        .select(
            number="NR_FEDERACAO",
            abbreviation="SG_FEDERACAO",
            label="NM_FEDERACAO",
            members=pl.col("DS_COMPOSICAO_FEDERACAO").str.split("/"),
        )
        .unique("number")
        .sort("number")
    )
    if parties["number"].is_duplicated().any() or parties["abbreviation"].is_duplicated().any():
        raise ValueError(f"party numbers or abbreviations repeat: {parties}")

    voted = (
        pl.concat([votes.filter(pl.col("kind") == "nominal"), municipal["deputies"]], how="diagonal_relaxed")
        .group_by("contest", "candidate", "choice")
        .agg(pl.col("votes").sum())
    )
    standing = roster.filter(
        (pl.col("CD_ELEICAO").is_in([FIRST, SECOND]) & pl.col("CD_CARGO").is_in(["1", "2"]))
        | ((pl.col("CD_ELEICAO") == STATE) & (pl.col("CD_CARGO") == "6"))
    ).join(
        voted.select("contest", "candidate", "votes"),
        left_on=["contest", "SQ_CANDIDATO"],
        right_on=["contest", "candidate"],
        how="left",
    )
    heads = standing.filter(
        ((pl.col("CD_CARGO") == "1") & pl.col("votes").is_not_null())
        | (
            (pl.col("CD_CARGO") == "6")
            & ((pl.col("DS_SITUACAO_CANDIDATURA") == "APTO") | pl.col("votes").is_not_null())
        )
    )
    mates = standing.filter((pl.col("CD_CARGO") == "2") & (pl.col("DS_SITUACAO_CANDIDATURA") == "APTO")).join(
        heads.select("contest", "NR_CANDIDATO"), on=["contest", "NR_CANDIDATO"], how="semi"
    )
    unknown = voted.join(
        heads.select("contest", "SQ_CANDIDATO", "NR_CANDIDATO"),
        left_on=["contest", "candidate", "choice"],
        right_on=["contest", "SQ_CANDIDATO", "NR_CANDIDATO"],
        how="anti",
    )
    if unknown.height:
        raise ValueError(f"votes for unknown candidates: {unknown}")
    if mates.height != heads.filter(pl.col("CD_CARGO") == "1").height:
        raise ValueError("every presidential candidate needs exactly one running mate")
    if heads.filter(deputies).select("contest", "NR_CANDIDATO").is_duplicated().any():
        raise ValueError("two candidates for the same state's deputies share a number")
    lists = (
        pl.concat(
            [
                votes.filter(pl.col("kind") == "legend").select("contest", number="choice"),
                municipal["legends"].filter(pl.col("valid") + pl.col("void") > 0).select("contest", number="choice"),
                heads.filter(deputies).select("contest", number="NR_PARTIDO"),
            ]
        )
        .unique()
        .join(parties, on="number", how="left")
        .join(
            federations.explode("members").select(abbreviation="members", federation="number"),
            on="abbreviation",
            how="left",
        )
        .select(
            "contest",
            party=pl.concat_str("number", "abbreviation", separator=":"),
            party_number="number",
            party_abbreviation="abbreviation",
            federation="federation",
        )
    )
    if lists["party"].is_null().any():
        raise ValueError(f"legend votes for unknown parties: {lists.filter(pl.col('party').is_null())}")

    outcome = (
        pl.when(pl.col("DS_SIT_TOT_TURNO").is_in(["ELEITO", "ELEITO POR QP", "ELEITO POR MÉDIA"]))
        .then(pl.lit("elected"))
        .when(pl.col("DS_SITUACAO_CANDIDATURA") != "APTO")
        .then(pl.lit("disqualified"))
        .when(pl.col("DS_SIT_TOT_TURNO") == "2º TURNO")
        .then(pl.lit("advanced"))
    )
    candidates = pl.concat([heads, mates], how="diagonal").select(
        "contest",
        role=pl.col("CD_CARGO").replace_strict({"1": "head", "2": "running_mate", "6": "list_member"}),
        candidate="SQ_CANDIDATO",
        number="NR_CANDIDATO",
        name=pl.when(pl.col("NM_SOCIAL_CANDIDATO") != "#NULO")
        .then(pl.col("NM_SOCIAL_CANDIDATO"))
        .otherwise(pl.col("NM_CANDIDATO")),
        ballot="NM_URNA_CANDIDATO",
        birth=pl.col("DT_NASCIMENTO").str.to_date("%d/%m/%Y", strict=False),
        gender=pl.col("DS_GENERO").replace_strict({"MASCULINO": "male", "FEMININO": "female"}, default=None),
        party=pl.concat_str("NR_PARTIDO", "SG_PARTIDO", separator=":"),
        party_number="NR_PARTIDO",
        party_abbreviation="SG_PARTIDO",
        federation=pl.when(pl.col("NR_FEDERACAO") != "-1").then(pl.col("NR_FEDERACAO")),
        coalition=pl.when(pl.col("TP_AGREMIACAO") == "COLIGAÇÃO").then(pl.col("SQ_COLIGACAO")),
        coalition_label=pl.col("NM_COLIGACAO"),
        coalition_members=pl.col("DS_COMPOSICAO_COLIGACAO"),
        outcome=outcome,
        votes=pl.col("votes").fill_null(0),
    )

    president = candidates.filter((pl.col("role") == "head") & ~deputies)
    valid = detail.group_by("contest").agg(pl.col("nominal").sum())
    ranked = president.join(valid, on="contest", suffix="_valid").with_columns(
        rank=pl.col("votes").rank("ordinal", descending=True).over("contest")
    )
    problems = {
        "a first-round majority": ranked.filter(
            (pl.col("contest") == FIRST) & (pl.col("votes") * 2 > pl.col("nominal"))
        ),
        "first-round advancers other than the top two": ranked.filter(
            (pl.col("contest") == FIRST) & ((pl.col("rank") <= 2) != (pl.col("outcome") == "advanced"))
        ),
        "a second-round winner other than the leader": ranked.filter(
            (pl.col("contest") == SECOND) & ((pl.col("rank") == 1) != (pl.col("outcome") == "elected"))
        ),
        "a second round without the first round's advancers": ranked.filter(pl.col("contest") == SECOND).join(
            ranked.filter(pl.col("outcome") == "advanced"), on="candidate", how="anti"
        ),
    }
    failures = [f"{label}: {frame.head(3)}" for label, frame in problems.items() if frame.height]
    for (code,), frame in candidates.filter(deputies & (pl.col("outcome") == "elected")).group_by("contest"):
        if frame.height != SEATS[str(code)[-2:]]:
            failures.append(f"{frame.height} deputies elected in {code}, expected {SEATS[str(code)[-2:]]}")
    if candidates.filter(deputies & (pl.col("outcome") == "elected")).height != sum(SEATS.values()):
        failures.append("the states' deputies do not add up to the chamber")
    if failures:
        raise ValueError("\n".join(failures))

    coalitions: list[tuple[str, str, str]] = []
    federation_numbers = {label: number for number, _, label, _ in federations.iter_rows()}
    party_keys = {abbreviation: f"{number}:{abbreviation}" for number, abbreviation, _ in parties.iter_rows()}
    for coalition, members in (
        president.filter(pl.col("coalition").is_not_null()).select("coalition", "coalition_members").unique().rows()
    ):
        for member in re.split(r" / (?![^(]*\))", members):
            if member.startswith("Federação"):
                coalitions.append((coalition, "federation", federation_numbers[member.split(" (")[0]]))
            else:
                coalitions.append((coalition, "party", party_keys[member]))

    disqualified = candidates.filter(pl.col("outcome") == "disqualified").select("contest", "candidate")
    values = pl.concat(
        [
            votes.join(disqualified.with_columns(void=pl.lit(True)), on=["contest", "candidate"], how="left").select(
                "contest",
                "section",
                option=pl.when(pl.col("kind") == "nominal")
                .then(pl.col("candidate"))
                .when(pl.col("kind") == "legend")
                .then(pl.lit("legend:") + pl.col("choice")),
                measure=pl.when(pl.col("kind") == "blank")
                .then(6)
                .when((pl.col("kind") == "null") | pl.col("void").fill_null(False))
                .then(5)
                .otherwise(1),
                value="votes",
            ),
            detail.unpivot(on=["registered", "voters"], index=["contest", "section"]).select(
                "contest",
                "section",
                option=pl.lit(None, pl.String),
                measure=pl.col("variable").replace_strict({"registered": 2, "voters": 14}),
                value="value",
            ),
            detail.filter(~deputies).select(
                "contest", "section", option=pl.lit(None, pl.String), measure=pl.lit(4), value="nominal"
            ),
            municipal["deputies"]
            .filter(pl.col("votes") > 0)
            .select(
                "contest",
                section="municipality",
                option="candidate",
                measure=pl.when(pl.col("void")).then(5).otherwise(1),
                value="votes",
            ),
            municipal["legends"]
            .unpivot(on=["valid", "void"], index=["contest", "municipality", "choice"])
            .filter(pl.col("value") > 0)
            .select(
                "contest",
                section="municipality",
                option=pl.lit("legend:") + pl.col("choice"),
                measure=pl.col("variable").replace_strict({"valid": 1, "void": 5}),
                value="value",
            ),
            municipal["detail"]
            .unpivot(on=["registered", "voters", "blank", "null"], index=["contest", "municipality"])
            .select(
                "contest",
                section="municipality",
                option=pl.lit(None, pl.String),
                measure=pl.col("variable").replace_strict({"registered": 2, "voters": 14, "blank": 6, "null": 5}),
                value="value",
            ),
        ],
        how="vertical_relaxed",
    ).with_columns(pl.col("measure").cast(pl.Int16))

    sections.sort("section").write_parquet(PARSED / "sections.parquet")
    parties.write_parquet(PARSED / "parties.parquet")
    used = (
        {member for _, kind, member in coalitions if kind == "federation"}
        | set(candidates["federation"].drop_nulls())
        | set(lists["federation"].drop_nulls())
    )
    federations.filter(pl.col("number").is_in(list(used))).write_parquet(PARSED / "federations.parquet")
    pl.DataFrame(coalitions, schema=["coalition", "kind", "member"], orient="row").write_parquet(
        PARSED / "coalitions.parquet"
    )
    candidates.write_parquet(PARSED / "candidates.parquet")
    lists.write_parquet(PARSED / "lists.parquet")
    detail.filter(~deputies & (~pl.col("opened") | pl.col("annulled"))).select(
        "contest", "section", "opened", "annulled"
    ).write_parquet(PARSED / "events.parquet")
    values.write_parquet(PARSED / "values.parquet")


def emit() -> Path:
    sections = pl.read_parquet(PARSED / "sections.parquet")
    parties = pl.read_parquet(PARSED / "parties.parquet")
    federations = pl.read_parquet(PARSED / "federations.parquet")
    coalitions = pl.read_parquet(PARSED / "coalitions.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    slates = pl.read_parquet(PARSED / "lists.parquet")
    events = pl.read_parquet(PARSED / "events.parquet")
    values = pl.read_parquet(PARSED / "values.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "2022 Brazilian presidential election, both rounds, and federal deputies, by polling section in Acre "
                "and by municipality elsewhere"
            ),
            polity="BR",
        )

        def name(
            entity: UUID, text: str, lang: str = "pt", kind: str = "official", preferred: bool | None = None
        ) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official" if preferred is None else preferred,
            )

        sources = {
            key: cs.source("Tribunal Superior Eleitoral", title)
            for key, title in (
                ("results", "Resultados - 2022"),
                ("candidates", "Candidatos - 2022"),
                ("places", "Eleitorado - 2022"),
            )
        }
        results = sources["results"]
        for source, title, url, notes in (
            (results, "Resultados - 2022", "https://dadosabertos.tse.jus.br/dataset/resultados-2022", SOURCE_NOTE),
            (
                sources["candidates"],
                "Candidatos - 2022",
                "https://dadosabertos.tse.jus.br/dataset/candidatos-2022",
                None,
            ),
            (sources["places"], "Eleitorado - 2022", "https://dadosabertos.tse.jus.br/dataset/eleitorado-2022", None),
        ):
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher="Tribunal Superior Eleitoral",
                title=title,
                url=url,
                license="Creative Commons Attribution",
                license_url="http://www.opendefinition.org/licenses/cc-by",
                notes=notes,
            )
        for key, url in FILES.items():
            cs.artifact(raw.cached(SOURCE, url), sources.get(key, results))

        brazil = cs.polity("BR")
        cs.add("polity", id=brazil, kind="sovereign_state", iso_code="BR")
        name(brazil, "Brazil", "en")
        name(brazil, "Brasil", "pt", "native")
        cs.add("slug", path="br", entity_id=brazil)
        cs.add("external_id", entity_id=brazil, scheme="wikidata", value="Q155")
        for code, rank, noun, kind in (
            ("br.nation", 0, "country", "administrative"),
            ("br.uf", 1, "state", "administrative"),
            ("br.municipality", 2, "municipality", "administrative"),
            ("br.secao", 3, "section", "precinct"),
        ):
            cs.add("scheme", code=code, polity_id=brazil, kind=kind, rank=rank, noun=noun)
        nation = cs.area("br.nation", "BR")
        cs.add("area", id=nation, scheme_code="br.nation")
        cs.add("area_code", area_id=nation, code="BR", valid_from=None)
        name(nation, "Brazil", "en")
        cs.add("polity_territory", polity_id=brazil, area_id=nation)
        states: dict[str, UUID] = {}
        for code, label in UFS.items():
            polity = cs.polity(f"BR-{code}")
            cs.add("polity", id=polity, kind="federal_unit", iso_code=f"BR-{code}")
            cs.add("slug", path=f"br/{code.lower()}", entity_id=polity)
            cs.add("polity_parent", polity_id=polity, parent_id=brazil)
            states[code] = cs.area("br.uf", code)
            cs.add("area", id=states[code], scheme_code="br.uf")
            cs.add("area_code", area_id=states[code], code=code, valid_from=None)
            cs.add("area_parent", area_id=states[code], parent_id=nation, valid_from=None)
            cs.add("polity_territory", polity_id=polity, area_id=states[code])
            for entity in (polity, states[code]):
                name(entity, label)
                if code == "DF":
                    name(entity, "Federal District", "en", "english")

        labels = {
            code: proper(label)
            for code, label in sections.select("municipality", "municipality_label").unique("municipality").rows()
        }
        abroad = pl.col("uf") == "ZZ"
        domestic = sections.filter(~abroad).unique("municipality").sort("municipality")
        municipalities = {code: str(cs.key(f"{SOURCE}.municipality", code)) for code in domestic["municipality"]}
        municipality = pl.col("municipality").replace_strict(municipalities, default=None)
        cs.frame("area", domestic.select(id=municipality, scheme_code=pl.lit("br.municipality")))
        cs.frame(
            "area_parent",
            domestic.select(
                area_id=municipality,
                parent_id=pl.col("uf").replace_strict({code: str(id) for code, id in states.items()}),
                valid_from=pl.lit(None, pl.Date),
            ),
        )
        cs.frame(
            "name",
            domestic.select(
                entity_id=municipality,
                text=pl.col("municipality").replace_strict(labels),
                lang=pl.lit("pt"),
                kind=pl.lit("official"),
                is_preferred=pl.lit(True),
            ),
        )

        unit_ids = {code: str(cs.unit("br.secao", code, DAYS[FIRST])) for code in sections["section"]}
        counted = values.filter(~pl.col("section").str.contains(":")).select(municipality="section").unique()
        unit_ids |= {code: str(cs.unit("br.municipality", code, DAYS[FIRST])) for code in counted["municipality"]}
        cs.frame(
            "reporting_unit",
            counted.select(
                id=pl.col("municipality").replace_strict(unit_ids),
                polity_id=pl.lit(str(brazil)),
                scheme_code=pl.lit("br.municipality"),
                code="municipality",
                area_relation=pl.lit("equals"),
                area_id=municipality,
                kind=pl.lit("standard"),
                valid_from=pl.lit(DAYS[FIRST]),
                valid_to=pl.lit(CLOSED),
            ),
        )
        section = pl.col("section").replace_strict(unit_ids)
        cs.frame(
            "reporting_unit",
            sections.select(
                id=section,
                polity_id=pl.lit(str(brazil)),
                scheme_code=pl.lit("br.secao"),
                code="section",
                area_relation=pl.when(abroad).then(pl.lit("unlocated")).otherwise(pl.lit("within")),
                area_id=municipality,
                location="location",
                kind=pl.when(abroad).then(pl.lit("abroad_mission")).otherwise(pl.lit("standard")),
                valid_from=pl.lit(DAYS[FIRST]),
                valid_to=pl.lit(CLOSED),
            ),
        )
        cs.frame(
            "name",
            sections.select(
                entity_id=section,
                text=pl.format(
                    "{}, zona {}, seção {}", pl.col("municipality").replace_strict(labels), "zone", "number"
                ),
                lang=pl.lit("pt"),
                kind=pl.lit("official"),
                is_preferred=pl.lit(True),
            ),
        )

        party_ids: dict[str, UUID] = {}
        for number, abbreviation, label in parties.iter_rows():
            key = f"{number}:{abbreviation}"
            party_ids[key] = cs.key(f"{SOURCE}.party", key)
            cs.add("party", id=party_ids[key], kind="party", polity_id=brazil)
            name(party_ids[key], proper(label))
            name(party_ids[key], abbreviation, kind="abbreviation")
        party_abbreviations = {key.split(":")[1]: id for key, id in party_ids.items()}
        federation_ids: dict[str, UUID] = {}
        federation_abbreviations: dict[str, str] = {}
        for number, abbreviation, label, members in federations.iter_rows():
            federation_ids[number] = cs.key(f"{SOURCE}.federation", f"{number}:{abbreviation}")
            federation_abbreviations[number] = abbreviation
            cs.add("party", id=federation_ids[number], kind="federation", polity_id=brazil)
            name(federation_ids[number], label)
            name(federation_ids[number], abbreviation, kind="abbreviation")
            for member in members:
                cs.add(
                    "party_affiliation",
                    party_id=party_abbreviations[member],
                    parent_party_id=federation_ids[number],
                    kind="federation",
                    valid_from=date(2022, 5, 1),
                    valid_from_precision="month",
                )

        office = cs.key("br.office", "president")
        cs.add("office", id=office, polity_id=brazil, kind="head_of_state")
        name(office, "President of Brazil", "en")
        name(office, "Presidente da República", "pt", "native")
        chamber = cs.key("br.body", "chamber-of-deputies")
        cs.add("body", id=chamber, polity_id=brazil, kind="lower_house")
        name(chamber, "Chamber of Deputies", "en")
        name(chamber, "Câmara dos Deputados", "pt", "native")
        cs.add("body_size", body_id=chamber, seats=513, valid_from=date(1995, 2, 1))
        systems: dict[str, UUID] = {}
        for key, rules, label, methodology in (
            ("presidential-two-round", {"formula": "two_round"}, "Brazilian presidential two-round system", PRESIDENT),
            (
                "chamber-open-list",
                {"formula": "dhondt", "lists": {"order": "open", "individual": 0.1, "vacancy": "next_by_votes"}},
                "Brazilian open-list proportional representation",
                CHAMBER,
            ),
        ):
            systems[key] = cs.key("br.electoral_system", key)
            cs.add("electoral_system", id=systems[key], polity_id=brazil, rules=rules, rules_version=1)
            name(systems[key], label, "en")
            cs.add("note", entity_id=systems[key], kind="methodology", body_md=methodology)

        cycle = cs.key("br.election", f"{DAYS[FIRST]}:general-elections")
        cs.add(
            "election",
            id=cycle,
            polity_id=brazil,
            kind="general",
            date_from=DAYS[FIRST],
            date_to=DAYS[SECOND],
            status="held",
        )
        name(cycle, "2022 Brazilian general election", "en")
        name(cycle, "Eleições gerais de 2022", "pt", "native")
        cs.add("slug", path=f"br/{DAYS[FIRST]}-general-elections", entity_id=cycle)
        elections: dict[str, UUID] = {}
        for code, ordinal, stage in ((FIRST, "first", "general"), (SECOND, "second", "runoff")):
            elections[code] = cs.key("br.election", str(DAYS[code]))
            cs.add(
                "election",
                id=elections[code],
                polity_id=brazil,
                parent_id=cycle,
                kind=stage,
                date_from=DAYS[code],
                date_to=DAYS[code],
                status="held",
            )
            name(elections[code], f"2022 Brazilian general election, {ordinal} round", "en")
            cs.add("slug", path=f"br/{DAYS[code]}-{stage}", entity_id=elections[code])
        elections |= {f"{STATE}:{uf}": elections[FIRST] for uf in UFS}

        coalition_ids: dict[str, UUID] = {}
        for coalition, label in (
            candidates.filter(pl.col("coalition").is_not_null()).select("coalition", "coalition_label").unique().rows()
        ):
            coalition_ids[coalition] = cs.key(f"{SOURCE}.coalition", coalition)
            cs.add("alliance", id=coalition_ids[coalition], kind="electoral_alliance", election_id=cycle)
            name(coalition_ids[coalition], proper(label))
        for coalition, kind, member in coalitions.iter_rows():
            cs.add(
                "alliance_member",
                alliance_id=coalition_ids[coalition],
                party_id=federation_ids[member] if kind == "federation" else party_ids[member],
            )

        contests: dict[str, UUID] = {}
        result_sets: dict[str, str] = {}
        options: dict[str, str] = {}
        persons: dict[str, UUID] = {}
        for code, key, slug_path, label in (
            (FIRST, f"{DAYS[FIRST]}:president", f"br/{DAYS[FIRST]}-general/president", "first round"),
            (SECOND, f"{DAYS[SECOND]}:president", f"br/{DAYS[SECOND]}-runoff/president", "second round"),
            *(
                (
                    f"{STATE}:{uf}",
                    f"{DAYS[STATE]}:chamber-of-deputies:{uf}",
                    f"br/{DAYS[STATE]}-general/chamber-of-deputies-"
                    + unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower().replace(" ", "-"),
                    "the Federal District" if uf == "DF" else label,
                )
                for uf, label in UFS.items()
            ),
        ):
            contests[code] = cs.key("br.contest", key)
            if code.startswith(STATE):
                cs.add(
                    "contest",
                    id=contests[code],
                    election_id=elections[code],
                    kind="list_allocation",
                    body_id=chamber,
                    district_area_id=states[code[-2:]],
                    magnitude=SEATS[code[-2:]],
                    round_number=1,
                    electoral_system_id=systems["chamber-open-list"],
                    rules_override={"threshold": {"percent": 80 / SEATS[code[-2:]], "level": "list"}},
                    default_result_kind="counted",
                )
                name(contests[code], f"2022 Chamber of Deputies election in {label}", "en")
                if code[-2:] != "AC":
                    cs.add("note", entity_id=contests[code], kind="caveat", body_md=RESIDUAL)
            else:
                cs.add(
                    "contest",
                    id=contests[code],
                    election_id=elections[code],
                    kind="office",
                    office_id=office,
                    magnitude=1,
                    round_number=1 if code == FIRST else 2,
                    previous_round_id=contests[FIRST] if code == SECOND else None,
                    electoral_system_id=systems["presidential-two-round"],
                    default_result_kind="counted",
                )
                name(contests[code], f"2022 Brazilian presidential election, {label}", "en")
            cs.add("slug", path=slug_path, entity_id=contests[code])
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[code],
                contest_id=contests[code],
                stage="official",
                kind="counted",
                status="active",
                source_id=results,
                is_default=True,
                completeness=1,
            )
            if result_set in cs.minted:
                result_sets[code] = str(result_set)

            standing = candidates.filter(pl.col("contest") == code)
            lists: dict[str, UUID] = {}
            if code.startswith(STATE):
                for party, number, abbreviation, federation in (
                    slates.filter(pl.col("contest") == code)
                    .select("party", "party_number", "party_abbreviation", "federation")
                    .sort("party")
                    .rows()
                ):
                    group = f"federation:{federation}" if federation is not None else party
                    if group not in lists:
                        lists[group] = cs.key("br.option", f"{key}:list:{group}")
                        cs.add(
                            "contest_option",
                            id=lists[group],
                            contest_id=contests[code],
                            kind="list",
                            party_id=party_ids[party] if federation is None else federation_ids[federation],
                            ballot_label=abbreviation if federation is None else federation_abbreviations[federation],
                        )
                    legend = cs.key("br.option", f"{key}:legend:{number}")
                    options[f"{code}:legend:{number}"] = str(legend)
                    cs.add(
                        "contest_option",
                        id=legend,
                        contest_id=contests[code],
                        kind="legend",
                        parent_option_id=lists[group],
                        party_id=party_ids[party],
                        ballot_label=abbreviation,
                    )
            seats: dict[str, int] = {}
            tickets: dict[str, UUID] = {}
            for row in standing.sort("role", "number").iter_rows(named=True):
                person_key = f"2022:{row['candidate']}"
                if person_key not in persons:
                    persons[person_key] = cs.key(f"{SOURCE}.person", person_key)
                    cs.add(
                        "person",
                        id=persons[person_key],
                        gender=row["gender"],
                        birth_date=row["birth"],
                        birth_precision="day" if row["birth"] is not None else None,
                    )
                    civil, urna = proper(row["name"]), proper(row["ballot"])
                    if urna in ("", "#Nulo") or urna == civil:
                        name(persons[person_key], civil)
                    else:
                        name(persons[person_key], urna, kind="ballot", preferred=True)
                        name(persons[person_key], civil, preferred=False)
                group = f"federation:{row['federation']}" if row["federation"] is not None else row["party"]
                if row["role"] == "running_mate":
                    option = tickets[row["number"]]
                elif code.startswith(STATE):
                    option = cs.key("br.option", f"{key}:{row['candidate']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contests[code],
                        kind="candidate",
                        parent_option_id=lists[group],
                        party_id=party_ids[row["party"]],
                        ballot_label=proper(row["ballot"]),
                    )
                else:
                    option = cs.key("br.option", f"{key}:{row['number']}")
                    tickets[row["number"]] = option
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contests[code],
                        kind="ticket",
                        party_id=party_ids[row["party"]],
                        alliance_id=coalition_ids[row["coalition"]] if row["coalition"] is not None else None,
                        ballot_label=proper(row["ballot"]),
                    )
                if row["role"] != "running_mate":
                    options[f"{code}:{row['candidate']}"] = str(option)
                cs.add(
                    "candidacy",
                    id=cs.key("br.candidacy", f"{key}:{row['candidate']}"),
                    person_id=persons[person_key],
                    option_id=option,
                    role=row["role"],
                    ballot_name=row["ballot"],
                    member_of_party_id=party_ids[row["party"]],
                )
                if row["role"] != "running_mate" and row["outcome"] is not None:
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind=row["outcome"],
                        notes=DISQUALIFIED if row["outcome"] == "disqualified" else None,
                    )
                    if code.startswith(STATE) and row["outcome"] == "elected":
                        seats[group] = seats.get(group, 0) + 1
            for group, count in seats.items():
                cs.add("outcome", result_set_id=result_set, option_id=lists[group], kind="won", seats=count)

        for code, section_code, annulled in events.select("contest", "section", "annulled").iter_rows():
            cs.add(
                "note",
                entity_id=UUID(unit_ids[section_code]),
                kind="caveat",
                body_md=(ANNULLED if annulled else NOT_OPENED).format(day=f"{DAYS[code].day} {DAYS[code]:%B %Y}"),
            )

        cs.frame(
            "result_value",
            values.filter(pl.col("contest").is_in(list(result_sets))).select(
                result_set_id=pl.col("contest").replace_strict(result_sets),
                unit_id=section,
                option_id=pl.when(pl.col("option").is_not_null())
                .then(pl.col("contest") + ":" + pl.col("option"))
                .replace_strict(options, default=None),
                channel=pl.lit(0, pl.Int16),
                measure="measure",
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value="value",
            ),
        )
        return cs.write()
