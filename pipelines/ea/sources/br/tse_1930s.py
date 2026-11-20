"""Brazil's electoral court (TSE): the 1933 Constituent Assembly and 1934 federal, state, and Federal District
council elections, from the court's open-data files for 1933 to 1937.

The court keyed these results from the Boletim Eleitoral of the Tribunal Superior de Justiça Eleitoral into its
open-data repository: each candidate's first-turn and second-turn votes by state, each legend's ballots,
the seats per state, and each state's registered voters, voters, and valid ballots. The state is the finest unit
the files publish.
"""

import io
import unicodedata
import zipfile
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "br.tse_1930s"
TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele/"
# Brazil entered the archive through the database path, so its record carries a database id (see br.tse_results).
COUNTRY = "01a13ba1-cd2c-7065-8397-2903884f736e"
CHAMBER = "01a13ba1-d3a7-7589-a5ab-43245491657a"
DAYS = {1933: date(1933, 5, 3), 1934: date(1934, 10, 14)}
KINDS = ("votacao_candidato_uf", "votacao_partido_uf", "detalhe_votacao_uf", "consulta_vagas", "consulta_legendas")
FILES = {(year, kind): f"{TSE}{kind}/{kind}_{year}_br.zip" for year in DAYS for kind in KINDS}
# The state reporting units and areas earlier pipelines hold: the Federal District of 1891 to 1960 is the city of
# Rio de Janeiro, and Goiás, Mato Grosso, and Rio de Janeiro take the outlines they had before their later splits.
UNITS = {
    "AC": ("fdb7d37f-ffc0-5a7b-bcc8-016015d5de43", "01a13ba1-cd85-748e-9cef-84c1029227d1"),
    "AL": ("3dd68b01-ff20-55cb-b28e-c442b40bc155", "01a13ba1-cd85-748e-9cef-84c389394a1e"),
    "AM": ("f21c5446-ebe2-5656-9d75-834d55c25e74", "01a13ba1-cd85-748e-9cef-84c74a5f3cc2"),
    "BA": ("ca6354ac-c0f7-5b5b-9f08-8e2876e410ed", "01a13ba1-cd85-748e-9cef-84c90d0f0d30"),
    "CE": ("135c5c98-b577-5f47-9ac4-3f3d5cd6cc5b", "01a13ba1-cd85-748e-9cef-84cb1f4a0d4a"),
    "DF": ("e662e828-4d27-5df4-8722-b65b457ad8fa", "db0421b0-8c21-5bb4-a08a-9fb6e646c31f"),
    "ES": ("f28727d4-ff8a-56a3-ac26-adbe83c93709", "01a13ba1-cd85-748e-9cef-84cf761ab275"),
    "GO": ("ae9ddd23-0e60-5be8-9e92-31780286ef69", "ca67e6b7-2ff0-53de-8e65-01d238d6e6d5"),
    "MA": ("a9f08461-4699-5b8f-85b8-6eccc6b50c1b", "01a13ba1-cd85-748e-9cef-84d342668844"),
    "MG": ("26d44ced-e531-5f76-8c39-445c8eb02c12", "01a13ba1-cd85-748e-9cef-84d959c8210e"),
    "MT": ("f2b8d43c-0b66-5aae-bc3f-6d54747a6c89", "07f33ec3-8002-55b0-98eb-15afa12ea6b1"),
    "PA": ("8281ba64-00c3-5c6b-b7d6-9d1e45f5b34d", "01a13ba1-cd85-748e-9cef-84db970d4b65"),
    "PB": ("6ddd2658-e35c-59d7-9915-0dfad35386e1", "01a13ba1-cd85-748e-9cef-84dd583896c2"),
    "PE": ("dbea6155-fe2d-5f49-8fc1-fcb91f51a9e5", "01a13ba1-cd85-748e-9cef-84e17da3b876"),
    "PI": ("020c90be-91bb-5bff-ae75-072179866a08", "01a13ba1-cd85-748e-9cef-84e3c3c13a9f"),
    "PR": ("29fa59a1-9008-519b-b197-50a8bb29adc8", "01a13ba1-cd85-748e-9cef-84dfa705e83f"),
    "RJ": ("0f0da47f-b103-58c6-b929-12c071213de4", "b4eebe02-b0fe-54c2-96b1-e3e39f070e8d"),
    "RN": ("ed54e274-f54f-5d41-8b20-7e17390793fd", "01a13ba1-cd85-748e-9cef-84e7759b9615"),
    "RS": ("7586c965-2392-58a5-9a97-e70e3693eed0", "01a13ba1-cd85-748e-9cef-84e998dd6d8f"),
    "SC": ("52fdbe94-4e8d-5bd5-8667-1c6904af5572", "01a13ba1-cd85-748e-9cef-84efc348fb8e"),
    "SE": ("36e7ba26-bb2b-56b9-aca6-edf0a5b9934c", "01a13ba1-cd85-748e-9cef-84f30989b7d4"),
    "SP": ("18a6b749-cee1-5fdf-a0b6-d002d76915af", "01a13ba1-cd85-748e-9cef-84f12d54c7d1"),
}
# Each state's legislative assembly, as the 1947 state elections already hold it.
ASSEMBLIES = {
    "AL": "0f9fd0ff-2674-5ade-bbf4-55dad8d2eb54",
    "AM": "46d2a0ad-1f45-5254-b75c-d2b47e3b4d3c",
    "BA": "49a2cf82-4317-5678-b9f0-4c90d4c0f90a",
    "CE": "a7131086-8d4f-503a-980b-57b31a251012",
    "ES": "ef386b9b-5405-5ff7-be17-08021be2c26f",
    "GO": "976b011e-79c4-5828-857b-a8af53acdbfe",
    "MA": "442f0748-fb34-5cad-b334-624f97ad6392",
    "MT": "dbcf0b66-7673-5e7f-b82c-1a3d397d9887",
    "MG": "58f91c52-cf9e-5c7b-ad3c-7c1ff1ef93d4",
    "PR": "f6a185f6-8e17-529d-bf42-94c9fe744799",
    "PB": "1961e18d-13c6-5fbe-a3a2-9dfdebf7fe71",
    "PA": "919c94ef-2012-50df-953f-5d156c87ed17",
    "PE": "c78a2f4b-7c2b-5704-85d3-7088d2571561",
    "PI": "b4fe1000-bd57-59e8-90c8-44e502732c63",
    "RN": "26a00990-d51d-5ff2-801f-9fb458cada4c",
    "RS": "674b4266-99b6-5d8a-80e2-e01d6afb22af",
    "RJ": "2e03316c-2777-57f3-8172-ab86c8bd946b",
    "SC": "3e09aae5-3ac0-5a19-80ee-f13a36f38e8c",
    "SE": "0b8771ef-fa48-5fa2-be9d-6161f035846f",
    "SP": "c4800a55-ba2b-5d7b-b70c-5c5a63bbea21",
}
STATES = {
    "AC": "Acre",
    "AL": "Alagoas",
    "AM": "Amazonas",
    "BA": "Bahia",
    "CE": "Ceará",
    "DF": "the Federal District",
    "ES": "Espírito Santo",
    "GO": "Goiás",
    "MA": "Maranhão",
    "MG": "Minas Gerais",
    "MT": "Mato Grosso",
    "PA": "Pará",
    "PB": "Paraíba",
    "PE": "Pernambuco",
    "PI": "Piauí",
    "PR": "Paraná",
    "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul",
    "SC": "Santa Catarina",
    "SE": "Sergipe",
    "SP": "São Paulo",
}
NO_LIST = {"#AVULSO#", "#NULO#", "#NE#"}
PARTY_KINDS = {
    "PARTIDO POLÍTICO": "party",
    "ALIANÇA DE PARTIDOS": "bloc",
    "GRUPO DE ELEITORES": "independent_group",
}
RENEWED = {"ES", "MT", "SC"}
METHOD = (
    "Keyed by the electoral court from the Boletim Eleitoral of the Tribunal Superior de Justiça Eleitoral into its "
    "open-data repository for 1933 to 1937. The 1932 Electoral Code ran proportional representation in two "
    "simultaneous turns counted from the same ballots: in the first turn each ballot counted for its legend and for "
    "the first name written on it, and the candidates who reached the electoral quotient, then the most-voted names "
    "of each legend up to its party quotient, were elected; the seats left over went in the second turn to the "
    "candidates with the most votes counting every name on the ballots. Each candidate's first-turn total, one vote "
    "per ballot, is stored as the count (count round 0, repeated as count round 1) where the court's file has it, and "
    "the second-turn total, which counts every name on each ballot, as count round 2. Candidates who stood alone are "
    "grouped as independents, and those whose legend the court leaves blank as others. A candidate who stood on several legends appears under the legend where the court records "
    "most of the votes. Legend ballots are stored as list votes on the legend's list. Valid votes are the "
    "net votes the court reports for the state, voters are those who came to vote, and registered voters are those "
    "on the rolls."
)
RENEWED_NOTE = (
    "The court annulled the vote of 3 May 1933 in this state and called a new election; these figures are those of "
    "the renewed election, as the Boletim Eleitoral of 13 June 1934 consolidates them."
)
CLASS_NOTE = (
    "Forty further members of the Constituent Assembly were chosen by unions, employers, liberal professions, and "
    "civil servants under Decree 22,653 of 1933; the court does not publish that vote, so it is not held here."
)
SHORT_NOTE = (
    "The court's file gives first-turn votes for only {have} of the {all} candidates in this contest, so the count "
    "shown (the first turn) is incomplete; every candidate's second-turn votes are under count round 2."
)
NO_FIRST_NOTE = (
    "The court's file gives no first-turn votes in this contest; every candidate's second-turn votes are under count "
    "round 2."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return "-".join("".join(c if c.isalnum() else " " for c in folded).split())


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def read(year: int, kind: str) -> pl.DataFrame:
    archive = zipfile.ZipFile(raw.cached(SOURCE, FILES[(year, kind)]).path)
    member = next(n for n in archive.namelist() if n.lower().endswith(".txt"))
    text = archive.read(member).decode("latin-1")
    header = kind != "consulta_vagas" or text.startswith('"DATA_GERACAO"')
    frame = pl.read_csv(io.StringIO(text), separator=";", has_header=header, infer_schema_length=0)
    if kind == "consulta_vagas" and not header:
        frame = frame.select(pl.nth(4).alias("SIGLA_UF"), pl.nth(7).alias("COD_CARGO"), pl.nth(9).alias("QTDE_VAGAS"))
    return frame.rename(
        {
            column: target
            for column, target in {
                "CODIGO_CARGO": "COD_CARGO",
                "SQ_CANDIDATO": "SEQ_CANDIDATO",
                "CODIGO_SIT_CAND_TOT": "COD_SIT_CAND_TOT",
            }.items()
            if column in frame.columns
        }
    )


def number(value: str | None) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    return parsed if parsed >= 0 else None


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS.values())]


def parse() -> None:
    registry().write()
    for year in DAYS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def source_id(year: int) -> str:
    return key("source", year)


def party_id(uf: str, text: str) -> str:
    return key("party", uf, slug(text))


def legends() -> dict[tuple[str, str], str]:
    """Every legend the court lists, by state and name, with the kind of party the 1932 code names it."""
    kinds: dict[tuple[str, str], str] = {}
    for year in DAYS:
        for kind in ("consulta_legendas", "votacao_partido_uf", "votacao_candidato_uf"):
            frame = read(year, kind)
            names = "NOME_PARTIDO" if "NOME_PARTIDO" in frame.columns else "NOME_LEGENDA"
            label = "TIPO_LEGENDA" if "TIPO_LEGENDA" in frame.columns else None
            for row in frame.iter_rows(named=True):
                text = row[names]
                if text is None or text in NO_LIST:
                    continue
                found = PARTY_KINDS.get(row[label] or "") if label else None
                current = kinds.get((row["SIGLA_UF"], text))
                if current is None or current == "list_vehicle":
                    kinds[(row["SIGLA_UF"], text)] = found or "list_vehicle"
    return kinds


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Brazil's legends of 1933 and 1934, the 1933 Constituent Assembly, the Federal District's council, and the "
        "1932 Electoral Code, with the electoral court's files",
    )
    p.external.add(COUNTRY)
    for year in DAYS:
        p.add(
            "source",
            id=source_id(year),
            kind="official_body",
            publisher="Tribunal Superior Eleitoral",
            title=f"Resultados - {year}",
            url=f"https://dadosabertos.tse.jus.br/dataset/resultados-{year}",
            license="Creative Commons Attribution",
            license_url="http://www.opendefinition.org/licenses/cc-by",
            attribution="Tribunal Superior Eleitoral",
            notes=METHOD,
        )
        for kind in KINDS:
            p.artifact(raw.cached(SOURCE, FILES[(year, kind)]), source_id(year))
    constituent = key("body", "constituent-assembly-1933")
    p.add("body", id=constituent, polity_id=COUNTRY, kind="constituent_assembly")
    p.name(constituent, "National Constituent Assembly (1933–1934)", "en", "official")
    p.name(constituent, "Assembleia Nacional Constituinte", "pt", "native", False)
    council = key("body", "council-df")
    p.add("body", id=council, polity_id=COUNTRY, kind="council")
    p.name(council, "Municipal Council of the Federal District (1892–1937)", "en", "official")
    p.name(council, "Câmara Municipal do Distrito Federal", "pt", "native", False)
    system = key("electoral_system", "1932")
    p.add(
        "electoral_system",
        id=system,
        polity_id=COUNTRY,
        valid_from=date(1932, 2, 24),
        valid_to=date(1937, 11, 10),
        rules={"formula": "hare", "leftovers": "most_votes", "lists": {"order": "open", "vacancy": "substitute"}},
        rules_version=1,
    )
    p.name(system, "Brazilian two-turn proportional representation, 1932–1937", "en", "official")
    p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=METHOD, lang="en")
    seen: set[str] = set()
    for (uf, text), kind in sorted(legends().items()):
        id = party_id(uf, text)
        if id in seen:
            continue
        seen.add(id)
        p.add("party", id=id, kind=kind, polity_id=COUNTRY)
        p.name(id, text, "pt", "official")
    return p


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "Brazil's 1933 Constituent Assembly election by state"
        if year == 1933
        else "Brazil's 1934 Chamber of Deputies, state constituent assembly, and Federal District council elections "
        "by state",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {COUNTRY, CHAMBER}
    p.accept(
        "accounting.votes_valid",
        "The court's 1930s files leave first-turn votes empty (-1) for some or all candidates in most contests, and "
        "where they are complete the court's first-turn sums differ slightly from the net valid votes it reports for "
        "the state; both figures are stored exactly as the court gives them.",
    )
    for kind in KINDS:
        p.uses(raw.cached(SOURCE, FILES[(year, kind)]))
    election = key("election", day)
    p.add("election", id=election, polity_id=COUNTRY, kind="general", date_from=day, date_to=day, status="held")
    if year == 1933:
        p.name(election, "1933 Brazilian Constituent Assembly election", "en", "official")
        p.name(election, "Eleição para a Assembleia Nacional Constituinte de 1933", "pt", "native", False)
        path = f"br/{day}-constituent-assembly-election"
    else:
        p.name(election, "1934 Brazilian general election", "en", "official")
        p.name(election, "Eleições de 1934", "pt", "native", False)
        path = f"br/{day}-general-elections"
    p.add("slug", path=path, entity_id=election, is_current=True)
    if year == 1933:
        p.add("note", id=key("note", election), entity_id=election, kind="caveat", body_md=CLASS_NOTE, lang="en")

    votes = read(year, "votacao_candidato_uf")
    parties = read(year, "votacao_partido_uf")
    detail = read(year, "detalhe_votacao_uf")
    seats = {
        (row["SIGLA_UF"], row["COD_CARGO"]): int(row["QTDE_VAGAS"])
        for row in read(year, "consulta_vagas").rows(named=True)
    }
    system = key("electoral_system", "1932")
    persons: set[str] = set()
    for (uf, cargo), rows in sorted(votes.group_by("SIGLA_UF", "COD_CARGO"), key=lambda item: str(item[0])):
        uf, cargo = str(uf), str(cargo)
        unit, area = UNITS[uf]
        p.external |= {unit, area}
        where = STATES[uf]
        place = slug(where.removeprefix("the "))
        if year == 1933:
            body, title, part = (
                key("body", "constituent-assembly-1933"),
                f"1933 Constituent Assembly election in {where}",
                f"constituent-assembly-{place}",
            )
        elif cargo == "6":
            body, title, part = CHAMBER, f"1934 Chamber of Deputies election in {where}", f"chamber-of-deputies-{place}"
        elif cargo == "7":
            body, title, part = (
                ASSEMBLIES[uf],
                f"1934 state constituent assembly election in {where}",
                f"legislative-assembly-{place}",
            )
            p.external.add(body)
        else:
            body, title, part = (
                key("body", "council-df"),
                "1934 Municipal Council election in the Federal District",
                "municipal-council-federal-district",
            )
        tag = f"{year}:{cargo}:{uf}"
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=body,
            district_area_id=area,
            magnitude=seats[(uf, cargo)],
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, title, "en", "official")
        p.add("slug", path=f"{path}/{part}", entity_id=contest, is_current=True)
        if year == 1933 and uf in RENEWED:
            p.add(
                "note",
                id=key("note", contest, "renewed"),
                entity_id=contest,
                kind="caveat",
                body_md=RENEWED_NOTE,
                lang="en",
            )
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source_id(year),
            status="active",
            is_default=True,
            completeness=1.0,
        )

        def value(option: str | None, measure: int, amount: int, count_round: int = 0) -> None:
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=count_round,
                flags=0,
                value=amount,
            )

        totals = detail.filter((pl.col("SIGLA_UF") == uf) & (pl.col("COD_CARGO") == cargo)).row(0, named=True)
        for measure, column in ((2, "QTD_APTOS"), (14, "QTD_COMPARECIMENTO"), (4, "QTD_VOTOS_LIQUIDOS")):
            amount = number(totals[column])
            if amount is not None:
                value(None, measure, amount)

        ballots = {
            str(text): number(count)
            for text, count in parties.filter((pl.col("SIGLA_UF") == uf) & (pl.col("COD_CARGO") == cargo))
            .select("NOME_PARTIDO", "QTDE_VOTOS_LEGENDA")
            .iter_rows()
            if text not in NO_LIST
        }
        names = sorted(set(ballots) | {str(text) for text in rows["NOME_PARTIDO"] if text not in NO_LIST})
        lists: dict[str, str] = {}
        for text in names:
            if slug(text) in lists:
                continue
            option = lists[slug(text)] = key("option", tag, "list", slug(text))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_id(uf, text),
                ballot_label=text,
            )
        for text, amount in sorted(ballots.items()):
            if amount:
                value(lists[slug(text)], 8, amount)
        for legend, kind, label in (
            ("#AVULSO#", "independents", "Candidatos avulsos"),
            ("#NULO#", "others", "Legenda não registrada"),
        ):
            if legend in set(rows["NOME_PARTIDO"]):
                lists[legend] = key("option", tag, kind)
                p.add("contest_option", id=lists[legend], contest_id=contest, kind=kind, ballot_label=label)

        turns = {
            (str(row["NOME_CANDIDATO"]), str(row["NUM_TURNO"])): number(row["TOTAL_VOTOS"])
            for row in rows.iter_rows(named=True)
        }
        candidates = rows.sort("NUM_TURNO", descending=True).unique("NOME_CANDIDATO", keep="first", maintain_order=True)
        won: dict[str, int] = {}
        first = 0
        for row in candidates.sort("NOME_CANDIDATO").iter_rows(named=True):
            text = str(row["NOME_CANDIDATO"])
            legend = str(row["NOME_PARTIDO"])
            group = legend if legend in NO_LIST else slug(legend)
            parent = lists[group]
            listed = legend not in NO_LIST
            option = key("option", tag, "candidate", slug(text))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                parent_option_id=parent,
                party_id=party_id(uf, legend) if listed else None,
                ballot_label=text,
            )
            person = key("person", year, uf, slug(text))
            if person not in persons:
                persons.add(person)
                p.add("person", id=person)
                p.name(person, text, "pt", "official")
            p.add(
                "candidacy",
                id=key("candidacy", tag, slug(text)),
                person_id=person,
                option_id=option,
                role="list_member",
                ballot_name=text,
                member_of_party_id=party_id(uf, legend) if listed else None,
                is_independent=legend == "#AVULSO#",
            )
            earlier = turns.get((text, "1"))
            if earlier is not None:
                first += 1
                value(option, 1, earlier)
                value(option, 1, earlier, 1)
            second = turns.get((text, "2"))
            if second is not None:
                value(option, 1, second, 2)
            if str(row["DESC_SIT_CAND_TOT"]).startswith("ELEITO"):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                won[group] = won.get(group, 0) + 1
        for group, count in sorted(won.items()):
            p.add("outcome", result_set_id=result_set, option_id=lists[group], kind="won", seats=count)
        if first < candidates.height:
            p.add(
                "note",
                id=key("note", contest, "first-turn"),
                entity_id=contest,
                kind="caveat",
                body_md=SHORT_NOTE.format(have=first, all=candidates.height) if first else NO_FIRST_NOTE,
                lang="en",
            )
    return p
