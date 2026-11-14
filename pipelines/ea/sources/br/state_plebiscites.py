import io
import re
import subprocess
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.sources.br.tse import UFS
from ea.store import DATA, connect

SOURCE = "br.state_plebiscites"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, INVALID, BLANK, VOTERS = 1, 2, 5, 6, 14
WAYBACK = "https://web.archive.org/web"
ARQUIVOS = "http://www.justicaeleitoral.jus.br/arquivos"
PA_PAGE = (
    "http://www.tre-pa.jus.br/eleicoes/eleicoes-anteriores/plebiscito-2011/relatorios-da-votacao-dos-plebiscitos-2011"
)
AC_PAGE = "http://www.tre-ac.jus.br/eleicoes/plebiscitos-e-referendos"
PA_STATE = {
    "carajas": f"{WAYBACK}/20120216064804id_/{ARQUIVOS}/tre-pa-resultado-geral-da-votacao-carajas-plebiscito-2011",
    "tapajos": f"{WAYBACK}/20190908081835id_/{ARQUIVOS}/tre-pa-resultado-da-votacao-por-estado-tapajos-plebiscito-2011",
}
PA_ZONES = {
    "carajas": f"{WAYBACK}/20190908081814id_/{ARQUIVOS}/tre-pa-resultado-da-votacao-por-municipio-carajas-plebiscito-2011",
    "tapajos": f"{WAYBACK}/20190908081823id_/{ARQUIVOS}/tre-pa-resultado-da-votacao-por-municipio-tapajos-plebiscito-2011",
}
PA_SECTIONS = (
    f"{WAYBACK}/20210211082252id_/https://www.justicaeleitoral.jus.br/arquivos/"
    "tre-pa-resultado-da-votacao-por-secao-referente-aos-plebiscitos-2011"
)
AC_RESULT = f"{WAYBACK}/20141006071947id_/{ARQUIVOS}/tre-ac-referendo-2010-fuso-horario-acre-resultado"
D_PA, D_AC = date(2011, 12, 11), date(2010, 10, 31)
QUESTIONS = {
    "carajas": (
        "Plebiscito Carajás",
        "Creation of the State of Carajás",
        "Criação do Estado do Carajás",
        "Você é a favor da divisão do Estado do Pará para a criação do Estado do Carajás?",
        "Are you in favor of dividing the State of Pará to create the State of Carajás?",
        "Decreto Legislativo nº 136/2011",
    ),
    "tapajos": (
        "Plebiscito Tapajós",
        "Creation of the State of Tapajós",
        "Criação do Estado do Tapajós",
        "Você é a favor da divisão do Estado do Pará para a criação do Estado do Tapajós?",
        "Are you in favor of dividing the State of Pará to create the State of Tapajós?",
        "Decreto Legislativo nº 137/2011",
    ),
}
AC_QUESTION = "Você é a favor da recente alteração do horário legal promovida no seu Estado?"
AC_QUESTION_EN = "Are you in favor of the recent change to the legal time in your state?"
SYSTEM = (
    "Consultations of one state's voters called by the National Congress through a decreto legislativo and run by "
    "the Electoral Justice on electronic ballot boxes (Lei nº 9.709/1998). Each question is answered Sim or Não; "
    "blank and null votes are counted but not valid, and the answer with more valid votes prevails."
)
NOTE_PA_STATE = (
    "State totals from the TRE-PA report 'Resultado de votação por UF' (Gerenciamento, Plebiscito 2011, oficial), "
    "printed 12 December 2011 from the result of 12/12/2011 00:41:03. Registered voters are the 4,848,495 eleitores "
    "aptos; 14,249 sections with a ballot box were totaled, with 1,632 more sections aggregated into them."
)
NOTE_PA_ZONES = (
    "Municipality totals summed from the TRE-PA report 'Resultado de votação por zona eleitoral/município' "
    "(Plebiscito 2011, oficial), which gives each municipality within each electoral zone. Registered voters are "
    "turnout plus abstention, the report's own denominator; they sum to the state's 4,848,495 eleitores aptos."
)
NOTE_PA_SECTIONS = (
    "Sim and Não votes by section from the TRE-PA spreadsheet 'Votação por seção' (Plebiscito 2011). The file has "
    "no blank, null, turnout, or registered figures, and sections aggregated into another section report under the "
    "receiving section."
)
NOTE_AC = (
    "State totals from the TRE-AC 'Relatório do Resultado da Totalização' and its Anexo II (Referendo 2010, "
    "oficial), printed 1 November 2010. The referendum shared the 31 October 2010 presidential runoff's sections "
    "on a second electronic ballot box. On this ballot 55 was Sim and 77 was Não."
)
FIGURE = r"(\d{1,3}(?:\.\d{3})*)"


def number(text: str) -> int:
    return int(text.replace(".", ""))


def text_of(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def totals(text: str) -> dict[str, int]:
    found = {}
    for key, label in (
        ("no", r"\*?\d\d - NÃO"),
        ("yes", r"\*?\d\d - SIM"),
        ("voters", "Total de votos apurados:"),
        ("blank", "Votos em branco:"),
        ("null", "Votos nulos:"),
        ("abstention", "Abstenção:"),
    ):
        match = re.search(rf"{label}\s+{FIGURE}", text)
        if match is None:
            raise ValueError(f"no {key} in {text[:200]!r}")
        found[key] = number(match.group(1))
    if found["yes"] + found["no"] + found["blank"] + found["null"] != found["voters"]:
        raise ValueError(f"votes do not add up: {found}")
    found["registered"] = found["voters"] + found["abstention"]
    return found


def fetch() -> None:
    for url in (*PA_STATE.values(), *PA_ZONES.values(), AC_RESULT):
        raw.fetch(SOURCE, url, suffix=".pdf")
    raw.fetch(SOURCE, PA_SECTIONS, suffix=".zip")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    states, zones = [], []
    for question in QUESTIONS:
        text = text_of(PA_STATE[question])
        aptos = re.search(rf"PA - PARÁ\s+{FIGURE}", text)
        state = totals(text)
        if aptos is None or number(aptos.group(1)) != state["registered"]:
            raise ValueError(f"{question}: registered voters do not match eleitores aptos")
        states.append({"uf": "PA", "question": question, **state})
        blocks: dict[tuple[int, str, str], str] = {}
        text = text_of(PA_ZONES[question])
        heads = list(re.finditer(r"(?:Zona eleitoral: (\d+)\s*\n)?\s*Município: (\d{5}) - (.+)", text))
        zone = None
        for head, after in zip(heads, [*heads[1:], None], strict=True):
            zone = int(head.group(1)) if head.group(1) else zone
            if zone is None:
                raise ValueError(f"{question}: no zone before {head.group(0)!r}")
            key = (zone, head.group(2), head.group(3).strip())
            blocks[key] = blocks.get(key, "") + text[head.end() : None if after is None else after.start()]
        for (zone, municipality, label), block in blocks.items():
            zones.append(
                {"question": question, "zone": zone, "municipality": municipality, "label": label, **totals(block)}
            )
    ac = text_of(AC_RESULT)
    aptos = re.search(rf"AC - ACRE\s+{FIGURE}", ac)
    state = totals(ac[ac.index("Anexo II") :])
    if aptos is None or number(aptos.group(1)) != state["registered"]:
        raise ValueError("Acre: registered voters do not match eleitores aptos")
    states.append({"uf": "AC", "question": "time-zone", **state})
    states_frame = pl.DataFrame(states)
    zones_frame = pl.DataFrame(zones)
    for question in QUESTIONS:
        state = states_frame.filter(pl.col("question") == question).drop("uf", "question")
        summed = zones_frame.filter(pl.col("question") == question).select(state.columns).sum()
        if summed.row(0) != state.row(0):
            raise ValueError(f"{question}: zone report does not add to the state report: {summed} {state}")
    if zones_frame.select("question", "zone", "municipality").is_duplicated().any():
        raise ValueError("duplicate zone and municipality blocks")

    archive = zipfile.ZipFile(raw.cached(SOURCE, PA_SECTIONS).path)
    (member,) = archive.namelist()
    sheet = pl.read_excel(io.BytesIO(archive.read(member)), engine="calamine", infer_schema_length=0)
    labels = {label: question for question, (label, *_) in QUESTIONS.items()}
    sections = (
        sheet.select(
            question=pl.col("CARGO").replace_strict(labels),
            zone=pl.col("NR_ZONA").cast(pl.Int64),
            municipality=pl.col("LOCALIDADE").str.slice(0, 5),
            section=pl.col("NR_SECAO").cast(pl.Int64),
            answer=pl.col("CANDIDATO").replace_strict({"55 - NÃO": "no", "77 - SIM": "yes"}),
            votes=pl.col("QT_VOTOS").cast(pl.Int64),
        )
        .pivot("answer", index=["question", "zone", "municipality", "section"], values="votes")
        .with_columns(pl.col("yes", "no").fill_null(0))
    )
    check = (
        sections.group_by("question", "zone", "municipality")
        .agg(pl.col("yes", "no").sum())
        .join(zones_frame, on=["question", "zone", "municipality"], how="full", suffix="_zone", coalesce=True)
        .filter((pl.col("yes") != pl.col("yes_zone")) | (pl.col("no") != pl.col("no_zone")) | pl.col("yes").is_null())
    )
    if not check.is_empty():
        print(check.select("question", "zone", "municipality", "label", "yes", "yes_zone", "no", "no_zone"))
    states_frame.write_parquet(PARSED / "states.parquet")
    zones_frame.write_parquet(PARSED / "zones.parquet")
    sections.write_parquet(PARSED / "sections.parquet")
    print(f"{len(zones_frame)} zone blocks, {sections.height} section rows, {check.height} zone blocks unmatched")


def emit() -> Path:
    states = pl.read_parquet(PARSED / "states.parquet")
    zones = pl.read_parquet(PARSED / "zones.parquet")
    sections = pl.read_parquet(PARSED / "sections.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Brazilian state consultations: the 2011 Pará plebiscites on creating Carajás and Tapajós, by state, "
                "municipality, and section, and the 2010 Acre time zone referendum"
            ),
            polity="BR",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def add_source(publisher: str, title: str, url: str, artifacts: tuple[str, ...]) -> UUID:
            source = cs.source(publisher, title)
            cs.add("source", id=source, kind="official_body", publisher=publisher, title=title, url=url)
            for artifact in artifacts:
                cs.artifact(raw.cached(SOURCE, artifact), source)
            return source

        tre_pa, tre_ac = "Tribunal Regional Eleitoral do Pará", "Tribunal Regional Eleitoral do Acre"
        pa_state = add_source(
            tre_pa, "Plebiscitos 2011: resultado de votação por UF", PA_PAGE, tuple(PA_STATE.values())
        )
        pa_zones = add_source(
            tre_pa,
            "Plebiscitos 2011: resultado de votação por zona eleitoral e município",
            PA_PAGE,
            tuple(PA_ZONES.values()),
        )
        pa_sections = add_source(tre_pa, "Plebiscitos 2011: votação por seção", PA_PAGE, (PA_SECTIONS,))
        ac_state = add_source(tre_ac, "Referendo 2010: resultado da totalização, Acre", AC_PAGE, (AC_RESULT,))

        values: list[tuple[str, str, str | None, int, int]] = []

        def put(result_set: UUID, unit: UUID, options: dict[str, UUID], row: dict[str, int]) -> None:
            for answer, option in options.items():
                values.append((str(result_set), str(unit), str(option), VOTES, row[answer]))
            for column, measure in (
                ("registered", REGISTERED),
                ("voters", VOTERS),
                ("blank", BLANK),
                ("null", INVALID),
            ):
                if column in row:
                    values.append((str(result_set), str(unit), None, measure, row[column]))

        def consultation(uf: str, day: date, slug: str, english: str, portuguese: str) -> tuple[UUID, UUID, UUID, str]:
            polity = cs.polity(f"BR-{uf}")
            system = cs.key("br.electoral_system", f"state-consultation:{uf}")
            cs.add("electoral_system", id=system, polity_id=polity, rules={"formula": "referendum"}, rules_version=1)
            name(system, f"State consultations in {UFS[uf]}")
            cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)
            election = cs.key("br.election", f"{day}:{uf}:{slug}")
            cs.add(
                "election", id=election, polity_id=polity, kind="referendum", date_from=day, date_to=day, status="held"
            )
            name(election, english)
            name(election, portuguese, "pt", "native")
            path = f"br/{uf.lower()}/{day}-{slug}"
            cs.add("slug", path=path, entity_id=election)
            return polity, system, election, path

        def question_for(
            election: UUID,
            system: UUID,
            path: str,
            key: str,
            kind: str,
            names: tuple[str, str],
            texts: tuple[str, str],
            source: UUID,
            url: str,
            labels: dict[str, int],
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
            name(contest, names[0])
            name(contest, names[1], "pt", "native")
            cs.add("slug", path=f"{path}/{key.rsplit(':', 1)[-1]}", entity_id=contest)
            for lang, body in (("pt", texts[0]), ("en", texts[1])):
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{lang}"),
                    contest_id=contest,
                    kind="text",
                    lang=lang,
                    body=body,
                    url=url,
                    source_id=source,
                    locator=None,
                )
            options = {}
            for position, (answer, label, english) in enumerate((("yes", "Sim", "Yes"), ("no", "Não", "No")), 1):
                options[answer] = cs.key("br.option", f"{key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=f"{labels[answer]} - {label}",
                )
                name(options[answer], english)
            return contest, options

        def result_set_for(
            election: UUID,
            contest: UUID,
            key: str,
            source: UUID,
            default: bool,
            note: str,
            options: dict[str, UUID],
            winner: str,
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
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
            for answer, option in options.items():
                cs.add(
                    "outcome", result_set_id=result_set, option_id=option, kind="won" if answer == winner else "lost"
                )
            return result_set

        areas = {
            code: (area, label)
            for code, area, label in conn.execute(
                "select distinct on (ru.code) ru.code, ru.area_id, n.text from reporting_unit ru "
                "join name n on n.entity_id = ru.area_id and n.is_preferred "
                "join area_parent ap on ap.area_id = ru.area_id join area_code uf on uf.area_id = ap.parent_id "
                "where ru.scheme_code = 'br.municipality' and ru.area_relation = 'equals' and uf.code = 'PA' "
                "order by ru.code, ru.valid_from desc"
            ).fetchall()
        }
        missing = set(zones["municipality"]) - set(areas)
        if missing:
            raise ValueError(f"no Pará municipality areas for {sorted(missing)}")
        until = D_PA + timedelta(days=1)
        added: set[UUID] = set()

        def municipal_unit(code: str) -> UUID:
            unit = cs.unit("br.municipality", code, D_PA)
            if unit in cs.minted and unit not in added:
                added.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=cs.polity("BR"),
                    scheme_code="br.municipality",
                    code=code,
                    area_relation="equals",
                    area_id=areas[code][0],
                    kind="standard",
                    valid_from=D_PA,
                    valid_to=until,
                )
            return unit

        def section_unit(municipality: str, zone: int, number: int) -> UUID:
            code = f"{municipality}:{zone}:{number}"
            unit = cs.unit("br.secao", code, D_PA)
            if unit in cs.minted and unit not in added:
                added.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=cs.polity("BR"),
                    scheme_code="br.secao",
                    code=code,
                    area_relation="within",
                    area_id=areas[municipality][0],
                    kind="standard",
                    valid_from=D_PA,
                    valid_to=until,
                )
                name(unit, f"{areas[municipality][1]}, zona {zone}, seção {number}", "pt")
            return unit

        _, system, election, path = consultation(
            "PA",
            D_PA,
            "plebiscite",
            "11 December 2011 Pará plebiscites",
            "Plebiscitos de 11 de dezembro de 2011 no Pará",
        )
        sums = ["yes", "no", "registered", "voters", "blank", "null"]
        municipal = zones.group_by("question", "municipality").agg(pl.col(sums).sum())
        for question, (_, english, portuguese, text, text_en, decree) in QUESTIONS.items():
            contest, options = question_for(
                election,
                system,
                path,
                f"{D_PA}:PA:{question}",
                "plebiscite",
                (english, portuguese),
                (text, text_en),
                pa_state,
                PA_PAGE,
                {"yes": 77, "no": 55},
            )
            cs.add(
                "note", entity_id=contest, kind="context", body_md=f"Called by {decree}; TSE Resolução nº 23.355/2011."
            )
            state = states.filter(pl.col("question") == question).row(0, named=True)
            winner = "yes" if state["yes"] > state["no"] else "no"
            result_set = result_set_for(
                election, contest, f"{D_PA}:{question}:state", pa_state, True, NOTE_PA_STATE, options, winner
            )
            put(result_set, cs.unit("br.uf", "PA"), options, state)
            result_set = result_set_for(
                election, contest, f"{D_PA}:{question}:municipalities", pa_zones, False, NOTE_PA_ZONES, options, winner
            )
            for row in municipal.filter(pl.col("question") == question).sort("municipality").iter_rows(named=True):
                put(result_set, municipal_unit(row["municipality"]), options, row)
            result_set = result_set_for(
                election, contest, f"{D_PA}:{question}:sections", pa_sections, False, NOTE_PA_SECTIONS, options, winner
            )
            for row in (
                sections.filter(pl.col("question") == question)
                .sort("municipality", "zone", "section")
                .iter_rows(named=True)
            ):
                put(
                    result_set,
                    section_unit(row["municipality"], row["zone"], row["section"]),
                    options,
                    {"yes": row["yes"], "no": row["no"]},
                )

        _, system, election, path = consultation(
            "AC",
            D_AC,
            "referendum",
            "31 October 2010 Acre time zone referendum",
            "Referendo de 31 de outubro de 2010 sobre o fuso horário do Acre",
        )
        contest, options = question_for(
            election,
            system,
            path,
            f"{D_AC}:AC:time-zone",
            "referendum",
            ("Legal time change of 2008", "Alteração do horário legal"),
            (AC_QUESTION, AC_QUESTION_EN),
            ac_state,
            AC_PAGE,
            {"yes": 55, "no": 77},
        )
        cs.add(
            "note",
            entity_id=contest,
            kind="context",
            body_md=(
                "Lei nº 11.662/2008 moved Acre from two to one hour behind Brasília; voters were asked whether they "
                "favored that change."
            ),
        )
        state = states.filter(pl.col("question") == "time-zone").row(0, named=True)
        result_set = result_set_for(
            election,
            contest,
            f"{D_AC}:time-zone:state",
            ac_state,
            True,
            NOTE_AC,
            options,
            "yes" if state["yes"] > state["no"] else "no",
        )
        put(result_set, cs.unit("br.uf", "AC"), options, state)

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
            ).with_columns(flags=pl.lit(0, pl.Int16), channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
        )
        return cs.write()
