import html
import re
import struct
import subprocess
import unicodedata
from datetime import date
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.sources.br.tse import UFS
from ea.store import DATA, connect

SOURCE = "br.referendums"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
VOTES, REGISTERED, INVALID, BLANK, VOTERS = 1, 2, 5, 6, 14
DIGITIZED = 8
PUBLISHER = "Tribunal Superior Eleitoral"
GLOSSARY_PLEBISCITE = "https://www.tse.jus.br/servicos-eleitorais/glossario/termos/plebiscito"
GLOSSARY_REFERENDUM = "https://www.tse.jus.br/servicos-eleitorais/glossario/termos/referendo"
TABLE_1963 = "https://www.justicaeleitoral.jus.br/arquivos/referendo-de-1963/@@download/file/planilha-referendo.pdf"
TABLE_1993 = "https://www.justicaeleitoral.jus.br/arquivos/plebiscito-de-1993/@@download/file/plebiscito-de-1993.pdf"
PAGE_1963 = "https://www.tse.jus.br/eleicoes/plebiscitos-e-referendos/referendo-1963"
PAGE_1993 = "https://www.tse.jus.br/eleicoes/plebiscitos-e-referendos/plebiscito-1993"
REPORT_2005 = "https://www.senado.gov.br/Relatorios_SGM/RelPresi/2005/P2_J_REFERENDO_2005.pdf"
MANAGEMENT_2005 = (
    "https://web.archive.org/web/20101011191123id_/http://www.tse.gov.br/internet/CatalogoPublicacoes/pdf/"
    "relatorio_gestao_referendo_2005/relatorio_gestao_2005.pdf"
)
STATES_2005 = Path(__file__).with_name("referendum_2005_states.tsv")
MUNICIPALITIES_2005 = Path(__file__).with_name("referendum_2005_municipalities.tsv")
PAGE_2005 = 194
PAGES_2005 = (PAGE_2005, 196, 197, 198)
GRID_2005 = {
    196: (
        False,
        (1042, 1104, 1166, 1228, 1290, 1352, 1414, 1476, 1538, 1600, 1662, 1724, 1786)
        + (1848, 1910, 1972, 2034, 2097, 2160, 2222, 2284, 2346, 2408, 2470, 2532, 2594),
    ),
    197: (
        True,
        (714, 778, 838, 901, 964, 1026, 1088, 1150, 1212, 1274, 1336, 1398, 1460, 1522, 1584)
        + (1646, 1708, 1770, 1832, 1894, 1956, 2018, 2080, 2142, 2204, 2266, 2328, 2390, 2452, 2515, 2578),
    ),
    198: (
        True,
        (714, 776, 838, 902, 964, 1025, 1088, 1150, 1212, 1274, 1336, 1398, 1460, 1522, 1584)
        + (1646, 1708, 1770, 1832, 1894, 1956),
    ),
}
GRID_COLUMNS_2005 = {
    "registered": (1300, 1482),
    "voters": (1482, 1662),
    "yes": (2386, 2604),
    "no": (2712, 2888),
    "blank": (3002, 3146),
    "null": (3290, 3436),
}
GRID_WIDTH, GRID_HEIGHT = 4678, 3306
MUNICIPAL_ALIASES_2005 = {("RJ", "Campos"): "Campos dos Goytacazes"}
ROWS_2005 = (
    0.3117,
    0.3286,
    0.3453,
    0.3622,
    0.3792,
    0.3961,
    0.413,
    0.43,
    0.4469,
    0.4639,
    0.4808,
    0.4977,
    0.5147,
    0.5316,
    0.5485,
    0.5655,
    0.5824,
    0.5994,
    0.6163,
    0.6332,
    0.65,
    0.6671,
    0.6841,
    0.7008,
    0.7178,
    0.7347,
    0.7517,
    0.7685,
)
COLUMNS_2005 = {
    "registered": (0.2449, 0.2797),
    "voters": (0.2797, 0.3285),
    "yes": (0.4677, 0.5001),
    "no": (0.524, 0.5651),
    "blank": (0.589, 0.6211),
    "null": (0.6471, 0.6827),
}
FIGURE = r"(\d{1,3}(?:\.\d{3})*)"
D1963, D1993, D2005 = date(1963, 1, 6), date(1993, 4, 21), date(2005, 10, 23)
STATES_1963 = {
    "Acre": "AC",
    "Alagoas": "AL",
    "Amazonas": "AM",
    "Bahia": "BA",
    "Brasília": "DF",
    "Ceará": "CE",
    "Espírito Santo": "ES",
    "Goiás": "GO",
    "Guanabara": "GB",
    "Maranhão": "MA",
    "Mato Grosso": "MT",
    "Minas Gerais": "MG",
    "Pará": "PA",
    "Paraíba": "PB",
    "Paraná": "PR",
    "Pernambuco": "PE",
    "Piauí": "PI",
    "Rio de Janeiro": "RJ",
    "Rio Grande do Norte": "RN",
    "Rio Grande do Sul": "RS",
    "Santa Catarina": "SC",
    "São Paulo": "SP",
    "Sergipe": "SE",
    "Território do Amapá": "AP",
    "Território de Roraima": "RR",
    "Território de Rondônia": "RO",
}
NAMES_1963 = {
    "Brasília": "Brasília (Federal District)",
    "Território do Amapá": "Territory of Amapá",
    "Território de Roraima": "Territory of Roraima",
    "Território de Rondônia": "Territory of Rondônia",
}
STATES_1993 = {label: uf for uf, label in UFS.items()} | {"Distrito Federal": "DF", "Exterior": "ZZ"}
SYSTEM = (
    "A national vote on a question put to the whole electorate. Voting is compulsory for literate voters aged 18 to "
    "70, and the winning option is the one with the most votes. The Superior Electoral Court (TSE) ran the 1963, "
    "1993, and 2005 consultations."
)
NOTE_1963 = (
    "Results by state, federal district, and territory from the TSE Archive Section's table, built from the "
    "tabulation maps, minutes, and reports kept in its archive. The table's national total equals the sum of the "
    "26 rows. The state rows are the Union's units of 1963: Guanabara was a state of its own (merged into Rio de "
    "Janeiro in 1975), Brasília is the Federal District, and Amapá, Roraima, and Rondônia were federal territories. "
    "Blank and null ballots are shown separately."
)
CAVEAT_1963 = (
    "The TSE Glossary prints 480,701 null ballots and 12,286,175 voters for the national result. The Archive "
    "Section's table prints 470,701 null ballots, which with the Yes, No, and blank votes adds up to the glossary's "
    "12,286,175, and shows 12,286,355 voters. The 180 extra voters are in Guanabara, whose ballots cast (1,047,539) "
    "are 180 more than its Yes, No, blank, and null votes. Brasília's ballots cast (48,637) exceed its registered "
    "voters (34,010). All figures are kept as the table prints them, and the glossary's 480,701 is read as a typing "
    "error."
)
NOTE_1993_NATIONAL = (
    "National totals as the TSE Glossary prints them for the 21 April 1993 plebiscite. Each question's options, "
    "blank, and null votes add up to the 67,010,241 ballots cast. Voters could answer each question separately."
)
NOTE_1993_STATES = (
    "Results by state and for voters abroad from the TSE Archive Section's table, built from the tabulation maps, "
    "minutes, and reports in its archive. Its national total differs from the glossary's final figures (for "
    "example 43,881,747 for the Republic against 44,266,433), and the table says the Exterior data came from the "
    "Federal District's regional electoral court without registered voters. Only rows whose Monarchy, Republic, "
    "blank, and null votes (or Presidentialism, Parliamentarism, blank, and null votes) add up to the ballots cast "
    "are loaded; the other rows have a typing error that the table does not locate. The transit-voter column is not "
    "loaded."
)
OMITTED_FORM = "Ceará, Pará, Paraíba, Pernambuco, Rio Grande do Norte, Roraima, and Mato Grosso do Sul"
OMITTED_SYSTEM = "Pernambuco, Rio Grande do Norte, Rio Grande do Sul, and Mato Grosso do Sul"
NOTE_2005 = (
    "National totals from the Superior Electoral Court's proclamation of 16 November 2005 (published in the Diário "
    "da Justiça), as the Federal Senate's 2005 report quotes it. Registered voters are the 122,042,825 the "
    "proclamation gives. The TSE Glossary prints 122,042,615, which is the ballots cast plus abstentions: 210 "
    "registered voters could not vote because the polling station in Vila de Urucarazinho, Amazonas, was not "
    "installed."
)
NOTE_2005_STATES = (
    'Results by state and the Federal District from the table "Quadro geral dos resultados no Brasil por estado" '
    "(last updated 22 November 2005) on page 192 of the TSE's management report on the 2005 referendum "
    "(Relatório de Gestão – Referendo 2005, January 2006), read from the scanned table. In every row the "
    "registered voters equal the ballots cast plus abstentions and the ballots cast equal the Yes, No, blank, and "
    "null votes, and each column adds up to the table's national row: 33,333,045 Yes, 59,109,265 No, 1,329,207 "
    "blank, and 1,604,307 null votes, the same as the proclamation. The table's 122,042,615 registered voters are "
    "210 fewer than the proclamation's 122,042,825. No ballots were annulled or counted separately."
)
NOTE_2005_MUNICIPALITIES = (
    'Results for the state capitals and the municipalities with more than 200,000 voters from the table "Quadro '
    'geral/Capitais e municípios com mais de 200.000 eleitores" (last updated 22 November 2005) on pages 194 to 196 '
    "of the TSE's management report on the 2005 referendum, read from the scanned table. It lists 75 municipalities: "
    "the 26 capitals and Brasília, and 48 other municipalities. In every row the registered voters equal the ballots "
    "cast plus abstentions, the ballots cast equal the valid, blank, and null votes, and the valid votes equal the "
    "Yes and No votes. No ballots were annulled or counted separately. Municipalities with fewer voters are not in "
    "the table."
)
TEXT_1963 = "Do you approve the Additional Act that institutes the parliamentary system?"
TEXT_1963_PT = "Aprova o ato adicional que institui o Parlamentarismo?"
TEXT_2005 = "Should the trade in firearms and ammunition be banned in Brazil?"
TEXT_2005_PT = "O comércio de armas de fogo e munição deve ser proibido no Brasil?"


def folded(label: str) -> str:
    return re.sub(r"[^a-z]+", " ", unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower())


def text_of(path: Path) -> str:
    page = path.read_text(errors="replace")
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.DOTALL)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page))).replace("\xa0", " ")


def number(text: str) -> int:
    return int(text.replace(".", ""))


def layout(path: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


def table_rows(path: Path, minimum: int) -> dict[str, list[int | None]]:
    rows: dict[str, list[int | None]] = {}
    for line in layout(path).splitlines():
        found = re.match(r"^([A-Za-zÀ-ú][A-Za-zÀ-ú ]*?)\s{2,}(\d.*)$", line)
        if found is None:
            continue
        figures = [
            None if token == "#VALOR!" else number(token)
            for token in re.findall(r"#VALOR!|\d[\d.]*(?:,\d+%)?", found[2])
            if not token.endswith("%")
        ]
        if len(figures) >= minimum:
            rows[found[1].strip()] = figures
    return rows


def fetch() -> None:
    raw.fetch(SOURCE, REPORT_2005, suffix=".pdf")
    raw.fetch(SOURCE, MANAGEMENT_2005, suffix=".pdf")
    for url in (TABLE_1963, TABLE_1993, GLOSSARY_PLEBISCITE, GLOSSARY_REFERENDUM):
        raw.cached(SOURCE, url)


def parse() -> None:
    plebiscite = text_of(raw.cached(SOURCE, GLOSSARY_PLEBISCITE).path)
    found = re.search(
        rf"FORMA DE GOVERNO República: {FIGURE} Monarquia: {FIGURE} Votos em branco: {FIGURE} Votos nulos: {FIGURE} "
        rf"SISTEMA DE GOVERNO Presidencialismo: {FIGURE} Parlamentarismo: {FIGURE} Votos em branco: {FIGURE} "
        rf"Votos nulos: {FIGURE} Eleitorado: {FIGURE} Comparecimento: {FIGURE}",
        plebiscite,
    )
    if found is None:
        raise ValueError("1993: no result in the TSE Glossary")
    republic, monarchy, form_blank, form_null, presidential, parliamentary, system_blank, system_null = (
        number(value) for value in found.groups()[:8]
    )
    registered, voters = number(found[9]), number(found[10])
    if monarchy + republic + form_blank + form_null != voters or (
        presidential + parliamentary + system_blank + system_null != voters
    ):
        raise ValueError("1993: the glossary's figures do not add up")
    national = [
        (D1993, "form", "republic", republic, "monarchy", monarchy, form_blank, form_null, registered, voters),
        (
            D1993,
            "system",
            "presidential",
            presidential,
            "parliamentary",
            parliamentary,
            system_blank,
            system_null,
            registered,
            voters,
        ),
    ]

    proclamation = re.sub(r"\s+", " ", layout(raw.cached(SOURCE, REPORT_2005).path))
    found = re.search(
        rf"apto a votar foi da ordem de {FIGURE} eleitores\. Compareceram {FIGURE}\. .*?Votos nulos: {FIGURE}\. "
        rf"Votos em branco: {FIGURE}\. Votos válidos: {FIGURE}\. Votos na opção SIM: {FIGURE}\. "
        rf"Votos na opção NÃO: {FIGURE}\.",
        proclamation,
    )
    if found is None:
        raise ValueError("2005: no proclamation in the Senate's report")
    registered, voters, null, blank, valid, yes, no = (number(value) for value in found.groups())
    if yes + no != valid or valid + null + blank != voters:
        raise ValueError("2005: the proclamation's figures do not add up")
    national.append((D2005, "question", "yes", yes, "no", no, blank, null, registered, voters))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        national,
        schema=[
            "day",
            "question",
            "first",
            "first_votes",
            "second",
            "second_votes",
            "blank",
            "null",
            "registered",
            "voters",
        ],
        orient="row",
    ).write_parquet(PARSED / "national.parquet")

    states_2005 = pl.read_csv(STATES_2005, separator="\t")
    if sorted(states_2005["uf"]) != sorted(UFS) or states_2005.height != len(ROWS_2005) - 1:
        raise ValueError("2005: the state table does not have one row per state")
    for row in states_2005.iter_rows(named=True):
        if (
            row["registered"] != row["voters"] + row["abstentions"]
            or row["voters"] != row["valid"] + row["blank"] + row["null"]
            or row["valid"] != row["yes"] + row["no"]
        ):
            raise ValueError(f"2005: the {row['uf']} row does not add up")
    sums = states_2005.select(pl.all().exclude("uf").sum()).row(0, named=True)
    if (sums["yes"], sums["no"], sums["blank"], sums["null"], sums["voters"]) != (yes, no, blank, null, voters):
        raise ValueError("2005: the state table does not add up to the proclamation")
    if registered - sums["registered"] != 210:
        raise ValueError("2005: the registered voters differ from the proclamation by other than 210")
    management = raw.cached(SOURCE, MANAGEMENT_2005)
    if (
        "Quadro geral dos resultados no Brasil por estado"
        not in subprocess.run(
            ["pdftotext", "-f", str(PAGE_2005), "-l", str(PAGE_2005), str(management.path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    ):
        raise ValueError("2005: the state table is not on the expected page")
    municipalities_2005 = pl.read_csv(MUNICIPALITIES_2005, separator="\t")
    if municipalities_2005.select(pl.col("uf", "municipality").n_unique()).row(0) != (27, 75):
        raise ValueError("2005: unexpected municipalities")
    if municipalities_2005.filter(
        (pl.col("registered") != pl.col("voters") + pl.col("abstentions"))
        | (pl.col("voters") != pl.col("valid") + pl.col("blank") + pl.col("null"))
        | (pl.col("valid") != pl.col("yes") + pl.col("no"))
    ).height:
        raise ValueError("2005: a municipal row does not add up")
    rows = []
    for page, (_, lines) in GRID_2005.items():
        rows += [(page, top, bottom) for top, bottom in pairwise(lines)]
    if municipalities_2005.height != len(rows) or [row[0] for row in rows] != municipalities_2005["page"].to_list():
        raise ValueError("2005: the municipal table does not have one row per grid line")
    municipalities_2005.with_columns(
        top=pl.Series([row[1] for row in rows]), bottom=pl.Series([row[2] for row in rows])
    ).write_parquet(PARSED / "municipalities_2005.parquet")
    PAGES.mkdir(parents=True, exist_ok=True)
    pages = []
    for page in PAGES_2005:
        image = PAGES / f"{management.sha256}-{page}.png"
        if not image.exists():
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    "100",
                    "-png",
                    "-f",
                    str(page),
                    "-l",
                    str(page),
                    "-singlefile",
                    str(management.path),
                    str(image.with_suffix("")),
                ],
                check=True,
            )
        width, height = struct.unpack(">II", image.read_bytes()[16:24])
        pages.append({"page": page, "image": f"pages/{SOURCE}/{image.name}", "width": width, "height": height})
    pl.DataFrame(pages).write_parquet(PARSED / "pages_2005.parquet")
    states_2005.with_columns(y0=pl.Series(ROWS_2005[:-1]), y1=pl.Series(ROWS_2005[1:])).write_parquet(
        PARSED / "states_2005.parquet"
    )
    states_2005.with_columns(y0=pl.Series(ROWS_2005[:-1]), y1=pl.Series(ROWS_2005[1:])).write_parquet(
        PARSED / "states_2005.parquet"
    )

    referendum = text_of(raw.cached(SOURCE, GLOSSARY_REFERENDUM).path)
    found = re.search(
        rf"6 de janeiro de 1963 Sim {FIGURE} Não {FIGURE} Votos em branco: {FIGURE} Votos nulos: {FIGURE} "
        rf"Eleitorado: {FIGURE} Votantes: {FIGURE}",
        referendum,
    )
    if found is None:
        raise ValueError("1963: no result in the TSE Glossary")
    rows_1963 = table_rows(raw.cached(SOURCE, TABLE_1963).path, 7)
    total = rows_1963.pop("TOTAL")
    if sorted(rows_1963) != sorted(STATES_1963):
        raise ValueError(f"1963: unexpected rows {sorted(set(rows_1963) ^ set(STATES_1963))}")
    columns = list(zip(*rows_1963.values(), strict=True))
    if [sum(column) for column in columns[:5] + columns[5:6]] != total[:6]:
        raise ValueError("1963: the rows do not add up to the table's total")
    glossary = [number(value) for value in found.groups()]
    if (glossary[0], glossary[1], glossary[2], glossary[4]) != (total[0], total[1], total[2], total[5]):
        raise ValueError("1963: the glossary disagrees with the table")
    if (glossary[3], total[3], glossary[5], total[4]) != (480701, 470701, 12286175, 12286355):
        raise ValueError("1963: the known discrepancies between the glossary and the table changed")
    pl.DataFrame(
        [(STATES_1963[name], *row) for name, row in rows_1963.items()],
        schema=["unit", "yes", "no", "blank", "null", "voters", "registered", "percent"],
        orient="row",
    ).drop("percent").write_parquet(PARSED / "states_1963.parquet")

    rows_1993 = table_rows(raw.cached(SOURCE, TABLE_1993).path, 12)
    rows_1993.pop("TOTAL")
    if sorted(rows_1993) != sorted(STATES_1993):
        raise ValueError(f"1993: unexpected rows {sorted(set(rows_1993) ^ set(STATES_1993))}")
    rows = []
    for name, line in rows_1993.items():
        if STATES_1993[name] == "ZZ":
            line = [*line[:8], None, line[9]]
        monarchy_, republic_, form_blank, form_null, parliamentary_, presidential_, system_blank, system_null = line[:8]
        registered_, voters_ = line[8], line[9]
        form = (monarchy_, republic_, form_blank, form_null)
        system = (parliamentary_, presidential_, system_blank, system_null)
        form_ok = None not in form and sum(value or 0 for value in form) == voters_
        system_ok = None not in system and sum(value or 0 for value in system) == voters_
        rows.append(
            (
                STATES_1993[name],
                *(form if form_ok else (None,) * 4),
                *(system if system_ok else (None,) * 4),
                registered_,
                voters_,
            )
        )
    states = pl.DataFrame(
        rows,
        schema=[
            "unit",
            "monarchy",
            "republic",
            "form_blank",
            "form_null",
            "parliamentary",
            "presidential",
            "system_blank",
            "system_null",
            "registered",
            "voters",
        ],
        orient="row",
    )
    omitted = states.filter(pl.col("republic").is_null() | pl.col("presidential").is_null())
    print(omitted.select("unit", form=pl.col("republic").is_not_null(), system=pl.col("presidential").is_not_null()))
    states.write_parquet(PARSED / "states_1993.parquet")


def emit() -> Path:
    national = pl.read_parquet(PARSED / "national.parquet")
    states_1963 = pl.read_parquet(PARSED / "states_1963.parquet")
    states_1993 = pl.read_parquet(PARSED / "states_1993.parquet")
    states_2005 = pl.read_parquet(PARSED / "states_2005.parquet")
    municipalities_2005 = pl.read_parquet(PARSED / "municipalities_2005.parquet")
    pages_2005 = pl.read_parquet(PARSED / "pages_2005.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Brazil's national consultations: the 1963 referendum on parliamentarism, the 1993 plebiscite on form "
                "and system of government, and the 2005 referendum on the firearms trade"
            ),
            polity="BR",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def add_source(title: str, url: str, publisher: str = PUBLISHER, notes: str | None = None) -> UUID:
            source = cs.source(publisher, title)
            cs.add("source", id=source, kind="official_body", publisher=publisher, title=title, url=url, notes=notes)
            return source

        brazil = cs.polity("BR")
        nation = cs.area("br.nation", "BR")
        if nation in cs.minted:
            raise ValueError("load br.tse before the referendums")
        archive = f"{PUBLISHER}, Seção de Arquivo"
        table_1963 = add_source("Resultado geral do Referendo de 1963", PAGE_1963, archive)
        table_1993 = add_source("Resultado geral do Plebiscito de 1993", PAGE_1993, archive)
        glossary_1 = add_source("Glossário Eleitoral: Plebiscito", GLOSSARY_PLEBISCITE)
        glossary_2 = add_source("Glossário Eleitoral: Referendo", GLOSSARY_REFERENDUM)
        report = add_source("Relatório da Presidência, 2005: J - Referendo de 2005", REPORT_2005, "Senado Federal")
        cs.artifact(raw.cached(SOURCE, TABLE_1963), table_1963)
        cs.artifact(raw.cached(SOURCE, TABLE_1993), table_1993)
        cs.artifact(raw.cached(SOURCE, GLOSSARY_PLEBISCITE), glossary_1)
        cs.artifact(raw.cached(SOURCE, GLOSSARY_REFERENDUM), glossary_2)
        cs.artifact(raw.cached(SOURCE, REPORT_2005), report)
        management = add_source(
            "Relatório de Gestão – Referendo 2005",
            MANAGEMENT_2005,
            notes=(
                "Quadro geral dos resultados no Brasil por estado and Quadro geral/Capitais e municípios com mais de "
                "200.000 eleitores, last updated 22 November 2005."
            ),
        )
        management_artifact = cs.artifact(raw.cached(SOURCE, MANAGEMENT_2005), management)
        management_pages = {}
        for page in pages_2005.iter_rows(named=True):
            management_pages[page["page"]] = cs.page(management_artifact, page["page"])
            cs.add(
                "source_page",
                id=management_pages[page["page"]],
                artifact_id=management_artifact,
                page_number=page["page"],
                image_key=page["image"],
                width=page["width"],
                height=page["height"],
            )

        system = cs.key("br.electoral_system", "national-consultation")
        cs.add("electoral_system", id=system, polity_id=brazil, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Brazilian national referendums and plebiscites")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        if conn.execute("select 1 from scheme where code = 'br.uf_1963'").fetchone() is None:
            cs.add(
                "scheme",
                code="br.uf_1963",
                polity_id=brazil,
                kind="administrative",
                rank=1,
                noun="state or territory",
            )

        def unit_for(scheme: str, code: str, label: str | None, relation: str, area: UUID | None, kind: str) -> UUID:
            unit = cs.unit(scheme, code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=brazil,
                    scheme_code=scheme,
                    code=code,
                    area_relation=relation,
                    area_id=area,
                    kind=kind,
                    valid_from=None,
                )
                if label is not None:
                    name(unit, label)
            return unit

        country = unit_for("br.nation", "BR", None, "equals", nation, "standard")
        units_1963 = {}
        for label, code in STATES_1963.items():
            area = cs.area("br.uf_1963", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="br.uf_1963")
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=nation, valid_from=None)
                name(area, NAMES_1963.get(label, label))
            units_1963[code] = unit_for("br.uf_1963", code, NAMES_1963.get(label, label), "equals", area, "standard")
        units_1993 = {}
        for code, label in {**UFS, "ZZ": "Voters abroad"}.items():
            abroad = code == "ZZ"
            area = None if abroad else cs.area("br.uf", code)
            units_1993[code] = unit_for(
                "br.uf",
                code,
                label if abroad else None,
                "unlocated" if abroad else "equals",
                area,
                "abroad_mission" if abroad else "standard",
            )

        values: list[tuple[str, str, str | None, int, int, int]] = []
        locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []

        def election_for(day: date, english: str, portuguese: str, kind: str) -> tuple[UUID, str]:
            election = cs.key("br.election", f"{day}:{kind}")
            cs.add(
                "election", id=election, polity_id=brazil, kind="referendum", date_from=day, date_to=day, status="held"
            )
            name(election, english)
            name(election, portuguese, "pt", "native")
            path = f"br/{day}-{kind}"
            cs.add("slug", path=path, entity_id=election)
            return election, path

        def contest_for(
            election: UUID,
            path: str,
            key: str,
            slug_tail: str,
            kind: str,
            english: str,
            portuguese: str,
            labels: tuple[tuple[str, str, str], tuple[str, str, str]],
        ) -> tuple[UUID, dict[str, UUID]]:
            contest = cs.key("br.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type=kind)
            name(contest, english)
            name(contest, portuguese, "pt", "native")
            cs.add("slug", path=f"{path}/{slug_tail}", entity_id=contest)
            options = {}
            for position, (answer, label, english_label) in enumerate(labels, 1):
                options[answer] = cs.key("br.option", f"{key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label,
                )
                name(options[answer], english_label)
            return contest, options

        def result_set_for(
            election: UUID, contest: UUID, key: str, source: UUID, default: bool, completeness: float, note: str
        ) -> UUID:
            result_set = cs.key(f"{SOURCE}.result_set", key)
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

        def put(
            result_set: UUID,
            unit: UUID,
            options: dict[str, UUID],
            figures: dict[str, int | None],
            accounting: dict[int, int | None],
        ) -> None:
            for answer, option in options.items():
                value = figures[answer]
                if value is not None:
                    values.append((str(result_set), str(unit), str(option), VOTES, value, 0))
            for measure, value in accounting.items():
                if value is not None:
                    values.append((str(result_set), str(unit), None, measure, value, 0))

        def outcomes(result_set: UUID, options: dict[str, UUID], winner: str) -> None:
            for answer, option in options.items():
                cs.add(
                    "outcome", result_set_id=result_set, option_id=option, kind="won" if answer == winner else "lost"
                )

        election, path = election_for(
            D1963, "6 January 1963 Brazilian referendum", "Referendo de 6 de janeiro de 1963", "referendum"
        )
        contest, options = contest_for(
            election,
            path,
            f"{D1963}:referendum",
            "parliamentary-system",
            "mandatory_referendum",
            "Parliamentary system (Additional Act)",
            "Sistema parlamentarista (Ato Adicional)",
            (("yes", "Sim", "Yes"), ("no", "Não", "No")),
        )
        for lang, body in (("en", TEXT_1963), ("pt", TEXT_1963_PT)):
            cs.add(
                "ballot_question_text",
                id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{lang}"),
                contest_id=contest,
                kind="text",
                lang=lang,
                body=body,
                url=PAGE_1963,
                source_id=table_1963,
                locator=None,
            )
        total = states_1963.select(pl.all().exclude("unit").sum()).row(0, named=True)
        result_set = result_set_for(
            election, contest, f"{D1963}:states", table_1963, True, 1, f"{NOTE_1963} {CAVEAT_1963}"
        )
        outcomes(result_set, options, "no" if total["no"] > total["yes"] else "yes")
        for row in states_1963.iter_rows(named=True):
            put(
                result_set,
                units_1963[row["unit"]],
                options,
                {"yes": row["yes"], "no": row["no"]},
                {REGISTERED: row["registered"], VOTERS: row["voters"], BLANK: row["blank"], INVALID: row["null"]},
            )

        election, path = election_for(
            D1993,
            "21 April 1993 Brazilian plebiscite on form and system of government",
            "Plebiscito de 21 de abril de 1993 sobre forma e sistema de governo",
            "plebiscite",
        )
        for question, slug_tail, english, portuguese, labels, omitted in (
            (
                "form",
                "form-of-government",
                "Form of government",
                "Forma de governo",
                (("republic", "República", "Republic"), ("monarchy", "Monarquia", "Monarchy")),
                OMITTED_FORM,
            ),
            (
                "system",
                "system-of-government",
                "System of government",
                "Sistema de governo",
                (
                    ("presidential", "Presidencialismo", "Presidentialism"),
                    ("parliamentary", "Parlamentarismo", "Parliamentarism"),
                ),
                OMITTED_SYSTEM,
            ),
        ):
            contest, options = contest_for(
                election, path, f"{D1993}:{question}", slug_tail, "plebiscite", english, portuguese, labels
            )
            row = national.filter((pl.col("day") == D1993) & (pl.col("question") == question)).row(0, named=True)
            result_set = result_set_for(
                election, contest, f"{D1993}:{question}:national", glossary_1, True, 1, NOTE_1993_NATIONAL
            )
            outcomes(result_set, options, row["first"])
            put(
                result_set,
                country,
                options,
                {row["first"]: row["first_votes"], row["second"]: row["second_votes"]},
                {REGISTERED: row["registered"], VOTERS: row["voters"], BLANK: row["blank"], INVALID: row["null"]},
            )
            prefix = "form" if question == "form" else "system"
            first, second = (answer for answer in options)
            rows = states_1993.filter(pl.col(first).is_not_null())
            result_set = result_set_for(
                election,
                contest,
                f"{D1993}:{question}:states",
                table_1993,
                False,
                min(1, round(rows["voters"].sum() / row["voters"], 4)),
                f"{NOTE_1993_STATES} Left out for this question: {omitted}.",
            )
            outcomes(result_set, options, row["first"])
            for state in rows.iter_rows(named=True):
                put(
                    result_set,
                    units_1993[state["unit"]],
                    options,
                    {first: state[first], second: state[second]},
                    {
                        REGISTERED: state["registered"],
                        VOTERS: state["voters"],
                        BLANK: state[f"{prefix}_blank"],
                        INVALID: state[f"{prefix}_null"],
                    },
                )

        election, path = election_for(
            D2005, "23 October 2005 Brazilian referendum", "Referendo de 23 de outubro de 2005", "referendum"
        )
        contest, options = contest_for(
            election,
            path,
            f"{D2005}:referendum",
            "firearms-trade",
            "mandatory_referendum",
            "Ban on the trade in firearms and ammunition",
            "Proibição do comércio de armas de fogo e munição",
            (("yes", "Sim", "Yes"), ("no", "Não", "No")),
        )
        for lang, body in (("en", TEXT_2005), ("pt", TEXT_2005_PT)):
            cs.add(
                "ballot_question_text",
                id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{lang}"),
                contest_id=contest,
                kind="text",
                lang=lang,
                body=body,
                url=REPORT_2005,
                source_id=report,
                locator=None,
            )
        row = national.filter(pl.col("day") == D2005).row(0, named=True)
        result_set = result_set_for(election, contest, f"{D2005}:national", report, True, 1, NOTE_2005)
        outcomes(result_set, options, row["first"] if row["first_votes"] > row["second_votes"] else row["second"])
        put(
            result_set,
            country,
            options,
            {row["first"]: row["first_votes"], row["second"]: row["second_votes"]},
            {REGISTERED: row["registered"], VOTERS: row["voters"], BLANK: row["blank"], INVALID: row["null"]},
        )

        result_set = result_set_for(election, contest, f"{D2005}:states", management, False, 1, NOTE_2005_STATES)
        outcomes(result_set, options, row["first"] if row["first_votes"] > row["second_votes"] else row["second"])
        for state in states_2005.iter_rows(named=True):
            unit = str(units_1993[state["uf"]])
            for column, option, measure in (
                ("yes", options["yes"], VOTES),
                ("no", options["no"], VOTES),
                ("registered", None, REGISTERED),
                ("voters", None, VOTERS),
                ("blank", None, BLANK),
                ("null", None, INVALID),
            ):
                key = (str(result_set), unit, None if option is None else str(option), measure)
                values.append((*key, state[column], DIGITIZED))
                x0, x1 = COLUMNS_2005[column]
                locators.append((*key, str(management_pages[PAGE_2005]), x0, state["y0"], x1, state["y1"]))

        areas = {}
        for code, area, uf, label in conn.execute(
            "select distinct on (ru.code) ru.code, ru.area_id, uf.code, n.text from reporting_unit ru "
            "join name n on n.entity_id = ru.area_id and n.is_preferred "
            "join area_parent ap on ap.area_id = ru.area_id join area_code uf on uf.area_id = ap.parent_id "
            "where ru.scheme_code = 'br.municipality' and ru.area_relation = 'equals' order by ru.code"
        ).fetchall():
            areas[uf, folded(label)] = (code, area)
        result_set = result_set_for(
            election,
            contest,
            f"{D2005}:municipalities",
            management,
            False,
            round(municipalities_2005["voters"].sum() / row["voters"], 4),
            NOTE_2005_MUNICIPALITIES,
        )
        outcomes(result_set, options, row["first"] if row["first_votes"] > row["second_votes"] else row["second"])
        for town in municipalities_2005.iter_rows(named=True):
            found = areas.get(
                (
                    town["uf"],
                    folded(MUNICIPAL_ALIASES_2005.get((town["uf"], town["municipality"]), town["municipality"])),
                )
            )
            if found is None:
                raise ValueError(f"2005: no municipality for {town['municipality']}, {town['uf']}")
            code, area = found
            unit = cs.unit("br.municipality", code, D2005)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=brazil,
                    scheme_code="br.municipality",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=D2005,
                    valid_to=date(2005, 11, 1),
                )
            rotated, _ = GRID_2005[town["page"]]
            for column, option, measure in (
                ("yes", options["yes"], VOTES),
                ("no", options["no"], VOTES),
                ("registered", None, REGISTERED),
                ("voters", None, VOTERS),
                ("blank", None, BLANK),
                ("null", None, INVALID),
            ):
                key = (str(result_set), str(unit), None if option is None else str(option), measure)
                values.append((*key, town[column], DIGITIZED))
                left, right = GRID_COLUMNS_2005[column]
                if rotated:
                    box = (
                        town["top"] / GRID_HEIGHT,
                        1 - right / GRID_WIDTH,
                        town["bottom"] / GRID_HEIGHT,
                        1 - left / GRID_WIDTH,
                    )
                else:
                    box = (
                        left / GRID_WIDTH,
                        town["top"] / GRID_HEIGHT,
                        right / GRID_WIDTH,
                        town["bottom"] / GRID_HEIGHT,
                    )
                locators.append((*key, str(management_pages[town["page"]]), *box))

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
                    "flags": pl.Int16,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
        )
        cs.frame(
            "value_locator",
            pl.DataFrame(
                locators,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "page_id": pl.String,
                    "x0": pl.Float64,
                    "y0": pl.Float64,
                    "x1": pl.Float64,
                    "y1": pl.Float64,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
        )
        return cs.write()
