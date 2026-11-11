import io
import os
import shutil
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.br.tse import UFS, proper
from ea.sources.br.tse_history import ELECTED, WITHDRAWN, party_key
from ea.store import DATA, connect

SOURCE = "br.municipais"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele/"
YEARS = (1996, 2000, 2004, 2008, 2012, 2016, 2020, 2024)
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
MAYOR, COUNCIL = "11", "13"
RUNOFF_ELECTORATE = 200_000
SOURCE_NOTE = (
    "The electoral court publishes each candidate's and each party's votes in municipal elections by municipality "
    "and electoral zone, and turnout, blank, and null votes for the same units. Its files were regenerated from the "
    "final totals, after later court decisions."
)
MAYOR_TWO_ROUND = (
    "In municipalities with more than 200,000 registered voters, a candidate for mayor wins in the first round with "
    "more than half of the valid votes, not counting blank and null votes. Otherwise the two leading candidates meet "
    "in a second round, and the one with more valid votes wins."
)
MAYOR_PLURALITY = (
    "In municipalities with 200,000 registered voters or fewer, the candidate for mayor with the most valid votes "
    "wins in a single round."
)
COUNCIL_QUOTIENT = (
    "Each municipality elects its councilors at large from open lists. Voters choose a candidate or a party (a "
    "legend vote). Until 2016, parties could join a coalition, which runs as one list. The electoral quotient is the "
    "valid votes divided by the seats, and only lists that reach it take seats: one per full quotient, then the "
    "remaining seats one at a time to the list with the highest average, its votes divided by its seats plus one. "
    "Each list's seats go to its candidates in order of personal votes. In 1996, blank votes counted as valid for the "
    "quotient."
)
COUNCIL_2020 = (
    "Each municipality elects its councilors at large from open lists, and from 2020 parties can no longer join "
    "coalitions for council seats. Voters choose a candidate or a party (a legend vote). The electoral quotient is "
    "the valid votes divided by the seats. Each list gets one seat per full quotient, and the remaining seats go one "
    "at a time to the list with the highest average, its votes divided by its seats plus one; lists below the "
    "quotient compete for them too. A list's seats go to its candidates in order of personal votes, and a candidate "
    "needs at least 10 percent of the quotient."
)
COUNCIL_2024 = (
    "Each municipality elects its councilors at large from open lists of parties or party federations. Voters choose "
    "a candidate or a party (a legend vote). The electoral quotient is the valid votes divided by the seats. Each "
    "list gets one seat per full quotient, and a candidate needs at least 10 percent of the quotient. The remaining "
    "seats go one at a time to the list with the highest average, its votes divided by its seats plus one, among "
    "lists with at least 80 percent of the quotient and candidates with at least 20 percent of it."
)
SHORT = (
    "The electoral court's candidate totals here fall {short} votes short of its count of valid nominal votes for "
    "this contest."
)
REPLACED = ["CASSAÇÃO DO REGISTRO", "HOMOLOGAÇÃO DE RENÚNCIA", "SUBSTITUÍDO"]


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def scan(year: int, kind: str) -> pl.LazyFrame:
    folder = PARSED / "tmp"
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw.cached(SOURCE, FILES[(year, kind)]).path) as archive:
        member = next(name for name in archive.namelist() if name.endswith("_BRASIL.csv"))
        with archive.open(member) as source, (folder / member).open("w", encoding="utf-8") as target:
            shutil.copyfileobj(io.TextIOWrapper(source, encoding="latin-1"), target, 16 << 20)
    frame = pl.scan_csv(folder / member, separator=";", infer_schema=False)
    present = frame.collect_schema().names()
    return frame.with_columns(
        pl.lit(None, pl.String).alias(column)
        for column in ("NR_FEDERACAO", "NM_FEDERACAO", "NM_SOCIAL_CANDIDATO")
        if column not in present
    )


def coalition(year: int) -> pl.Expr:
    kind = pl.col("TP_AGREMIACAO").str.to_uppercase()
    return pl.when(kind.is_in(["COLIGAÇÃO", "FEDERAÇÃO"])).then(
        pl.when(kind == "FEDERAÇÃO")
        .then(pl.concat_str(pl.lit(f"{year}:F"), "NR_FEDERACAO"))
        .otherwise(
            pl.concat_str(
                pl.lit(str(year)), pl.col("CD_MUNICIPIO").str.zfill(5), "CD_CARGO", "SQ_COLIGACAO", separator=":"
            )
        )
    )


def parse() -> None:
    years = [int(year) for year in os.environ.get("BR_YEARS", ",".join(map(str, YEARS))).split(",")]
    for year in years:
        folder = PARSED / str(year)
        folder.mkdir(parents=True, exist_ok=True)
        municipality = pl.col("CD_MUNICIPIO").str.zfill(5)
        contest = pl.concat_str(pl.lit(str(year)), "NR_TURNO", "CD_CARGO", municipality, separator=":")
        ordinary = (pl.col("CD_TIPO_ELEICAO") == "2") & pl.col("CD_CARGO").is_in([MAYOR, COUNCIL])

        candidates = scan(year, "candidates").filter(ordinary)
        columns = candidates.collect_schema().names()
        valid = "QT_VOTOS_NOMINAIS_VALIDOS" if "QT_VOTOS_NOMINAIS_VALIDOS" in columns else "QT_VOTOS_NOMINAIS"
        void = (
            pl.col("QT_VOTOS_NOMINAIS").cast(pl.Int64) - pl.col(valid).cast(pl.Int64)
            if valid != "QT_VOTOS_NOMINAIS"
            else pl.lit(0, pl.Int64)
        )
        votes = (
            candidates.group_by(contest=contest, candidate=pl.col("SQ_CANDIDATO"))
            .agg(valid=pl.col(valid).cast(pl.Int64).sum(), void=void.sum())
            .collect()
        )
        detail = scan(year, "detail").filter(ordinary)
        places = pl.concat(
            [
                frame.select(
                    uf="SG_UF",
                    municipality=municipality,
                    label="NM_MUNICIPIO",
                    round="NR_TURNO",
                    day=pl.col("DT_ELEICAO").str.to_date("%d/%m/%Y"),
                ).collect()
                for frame in (candidates, detail)
            ]
        ).unique(["municipality", "round"])
        roster = (
            scan(year, "roster").select("SQ_CANDIDATO", "DT_NASCIMENTO", "DS_GENERO").unique("SQ_CANDIDATO").collect()
        )
        everyone = (
            candidates.unique(["NR_TURNO", "CD_CARGO", "CD_MUNICIPIO", "SQ_CANDIDATO"])
            .select(
                contest=contest,
                municipality=municipality,
                round="NR_TURNO",
                office=pl.col("CD_CARGO"),
                candidate="SQ_CANDIDATO",
                number="NR_CANDIDATO",
                name=pl.when(
                    pl.col("NM_SOCIAL_CANDIDATO").is_not_null()
                    & ~pl.col("NM_SOCIAL_CANDIDATO").str.starts_with("#")
                    & (pl.col("NM_SOCIAL_CANDIDATO").str.strip_chars() != "")
                )
                .then(pl.col("NM_SOCIAL_CANDIDATO"))
                .otherwise(pl.col("NM_CANDIDATO"))
                .str.strip_chars(),
                ballot=pl.col("NM_URNA_CANDIDATO").str.strip_chars(),
                party_number="NR_PARTIDO",
                party_abbreviation=pl.col("SG_PARTIDO").str.strip_chars(),
                party_label="NM_PARTIDO",
                coalition=coalition(year),
                coalition_label=pl.when(pl.col("TP_AGREMIACAO").str.to_uppercase() == "FEDERAÇÃO")
                .then(pl.col("NM_FEDERACAO"))
                .otherwise(pl.col("NM_COLIGACAO"))
                .str.strip_chars(),
                coalition_members=pl.col("DS_COMPOSICAO_COLIGACAO").str.replace_all(
                    r"(?i)FEDERAÇÃO[^(/]*\(([^)]*)\)", "$1"
                ),
                status="DS_SIT_TOT_TURNO",
                situation="DS_SITUACAO_CANDIDATURA",
            )
            .unique(["contest", "candidate"])
            .collect()
            .join(roster, left_on="candidate", right_on="SQ_CANDIDATO", how="left")
            .with_columns(
                birth=pl.col("DT_NASCIMENTO").str.to_date("%d/%m/%Y", strict=False),
                gender=pl.col("DS_GENERO").replace_strict(
                    {"MASCULINO": "male", "FEMININO": "female"}, default=None, return_dtype=pl.String
                ),
                outcome=pl.when(pl.col("status").is_in(ELECTED) & ~pl.col("situation").is_in(WITHDRAWN))
                .then(pl.lit("elected"))
                .when(pl.col("status") == "2º TURNO")
                .then(pl.lit("advanced")),
            )
            .drop("DT_NASCIMENTO", "DS_GENERO")
        )
        standing = (
            everyone.sort(pl.col("situation").is_in([*WITHDRAWN, *REPLACED]), "candidate")
            .unique(["contest", "number"], keep="first", maintain_order=True)
            .drop("situation")
        )
        dropped = votes.join(standing, on=["contest", "candidate"], how="anti").join(
            everyone.select("contest", "candidate", "number"), on=["contest", "candidate"]
        )
        kept = votes.join(standing.select("contest", "candidate", "number"), on=["contest", "candidate"])
        differing = dropped.join(kept, on=["contest", "number"], how="left").filter(
            pl.col("valid_right").is_null() | (pl.col("valid") != pl.col("valid_right"))
        )
        votes = kept.drop("number")

        parties = scan(year, "parties").filter(ordinary & (pl.col("CD_CARGO") == COUNCIL))
        names = parties.collect_schema().names()
        legend = "QT_VOTOS_LEGENDA_VALIDOS" if "QT_VOTOS_LEGENDA_VALIDOS" in names else "QT_VOTOS_LEGENDA"
        legends = (
            parties.group_by(contest=contest, party_number=pl.col("NR_PARTIDO"))
            .agg(
                votes=pl.col(legend).cast(pl.Int64).sum(),
                party_abbreviation=pl.col("SG_PARTIDO").str.strip_chars().first(),
                party_label=pl.col("NM_PARTIDO").first(),
                coalition=coalition(year).first(),
            )
            .collect()
        )
        tallies = (
            detail.group_by(contest=contest)
            .agg(
                registered=pl.col("QT_APTOS").cast(pl.Int64).sum(),
                voters=pl.col("QT_COMPARECIMENTO").cast(pl.Int64).sum(),
                blank=pl.col("QT_VOTOS_BRANCOS").cast(pl.Int64).sum(),
                null=pl.col("QT_VOTOS_NULOS").cast(pl.Int64).sum(),
                nominal=pl.col("QT_VOTOS_NOMINAIS_VALIDOS").cast(pl.Int64).sum(),
                legend=pl.col("QT_VOTOS_LEG_VALIDOS").cast(pl.Int64).sum(),
            )
            .collect()
        )
        shutil.rmtree(PARSED / "tmp")

        failures: list[str] = []
        unknown = votes.join(standing, on=["contest", "candidate"], how="anti")
        if differing.height:
            failures.append(f"{year}: a ballot number carries different votes for two candidates: {differing}")
        if unknown.height:
            failures.append(f"{year}: votes for unknown candidates: {unknown.head(5)}")
        mayors = standing.filter((pl.col("office") == MAYOR) & (pl.col("outcome") == "elected"))
        doubled = mayors.group_by("contest").len().filter(pl.col("len") > 1)
        if doubled.height:
            failures.append(f"{year}: several mayors elected: {doubled}")
        if failures:
            raise ValueError("\n".join(failures))
        excess = (
            votes.group_by("contest")
            .agg(pl.col("valid").sum())
            .join(tallies.select("contest", "nominal"), on="contest")
            .select("contest", excess=pl.col("valid") - pl.col("nominal"))
            .filter(pl.col("excess") != 0)
        )
        standing.write_parquet(folder / "candidates.parquet")
        votes.write_parquet(folder / "votes.parquet")
        legends.write_parquet(folder / "legends.parquet")
        tallies.write_parquet(folder / "detail.parquet")
        places.write_parquet(folder / "places.parquet")
        excess.write_parquet(folder / "excess.parquet")
        print(
            year,
            f"{standing.filter(pl.col('office') == MAYOR).height} mayoral and "
            f"{standing.filter(pl.col('office') == COUNCIL).height} council candidacies,",
            f"{tallies.height} contests tallied, {standing['contest'].n_unique()} with candidates,",
            f"{mayors.height} mayors elected, excess in {excess.height} ({excess.filter(pl.col('excess') < 0).height} short)",
        )


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return "-".join("".join(character if character.isalnum() else " " for character in folded).split())


def emit() -> Path:
    year = int(os.environ["BR_YEAR"])
    folder = PARSED / str(year)
    candidates = pl.read_parquet(folder / "candidates.parquet")
    votes = pl.read_parquet(folder / "votes.parquet")
    legends = pl.read_parquet(folder / "legends.parquet")
    detail = pl.read_parquet(folder / "detail.parquet")
    places = pl.read_parquet(folder / "places.parquet")
    short = {code: -excess for code, excess in pl.read_parquet(folder / "excess.parquet").iter_rows() if excess < 0}
    every = places.sort("round").unique("municipality", keep="first")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Brazil's {year} municipal elections, mayors and municipal chambers, by municipality",
            polity="BR",
        )

        def name(entity: UUID, text: str, lang: str = "pt", kind: str = "official") -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        title = f"Resultados - {year}"
        results = cs.source("Tribunal Superior Eleitoral", title)
        cs.add(
            "source",
            id=results,
            kind="official_body",
            publisher="Tribunal Superior Eleitoral",
            title=title,
            url=f"https://dadosabertos.tse.jus.br/dataset/resultados-{year}",
            license="Creative Commons Attribution",
            license_url="http://www.opendefinition.org/licenses/cc-by",
            notes=SOURCE_NOTE,
        )
        roster = cs.source("Tribunal Superior Eleitoral", f"Candidatos - {year}")
        cs.add(
            "source",
            id=roster,
            kind="official_body",
            publisher="Tribunal Superior Eleitoral",
            title=f"Candidatos - {year}",
            url=f"https://dadosabertos.tse.jus.br/dataset/candidatos-{year}",
            license="Creative Commons Attribution",
            license_url="http://www.opendefinition.org/licenses/cc-by",
        )
        for kind in ("candidates", "parties", "detail"):
            cs.artifact(raw.cached(SOURCE, FILES[(year, kind)]), results)
        cs.artifact(raw.cached(SOURCE, FILES[(year, "roster")]), roster)

        brazil = cs.polity("BR")
        systems: dict[str, UUID] = {}
        for key, rules, label, methodology in (
            ("mayor-two-round", {"formula": "two_round"}, "Brazilian mayoral two-round system", MAYOR_TWO_ROUND),
            ("mayor-plurality", {"formula": "fptp"}, "Brazilian mayoral plurality system", MAYOR_PLURALITY),
            (
                "council-open-list-quotient",
                {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_by_votes"}},
                "Brazilian municipal open-list proportional representation, 1996–2016",
                COUNCIL_QUOTIENT,
            ),
            (
                "council-open-list-2020",
                {"formula": "dhondt", "lists": {"order": "open", "individual": 0.1, "vacancy": "next_by_votes"}},
                "Brazilian municipal open-list proportional representation, 2020",
                COUNCIL_2020,
            ),
            (
                "council-open-list-2024",
                {"formula": "dhondt", "lists": {"order": "open", "individual": 0.1, "vacancy": "next_by_votes"}},
                "Brazilian municipal open-list proportional representation, 2024",
                COUNCIL_2024,
            ),
        ):
            systems[key] = cs.key("br.electoral_system", key)
            if systems[key] in cs.minted:
                cs.add("electoral_system", id=systems[key], polity_id=brazil, rules=rules, rules_version=1)
                name(systems[key], label, "en")
                cs.add("note", entity_id=systems[key], kind="methodology", body_md=methodology)
        council_system = systems[
            "council-open-list-quotient"
            if year <= 2016
            else "council-open-list-2020"
            if year == 2020
            else "council-open-list-2024"
        ]

        dates = dict(places.group_by("round").agg(pl.col("day").min()).iter_rows())
        first, last = dates["1"], dates.get("2", dates["1"])
        umbrella = cs.key("br.election", f"{first}:municipal-elections")
        cs.add("election", id=umbrella, polity_id=brazil, kind="local", date_from=first, date_to=last, status="held")
        name(umbrella, f"{year} Brazilian municipal elections", "en")
        name(umbrella, f"Eleições municipais de {year}", "pt", "native")
        cs.add("slug", path=f"br/{first}-local", entity_id=umbrella)

        taken = {
            path
            for (path,) in conn.execute(
                "select path from slug where path like 'br/%%/%%' and path not like 'br/%%/%%/%%'"
            )
        }
        states = {uf: cs.area("br.uf", uf) for uf in UFS}
        polities: dict[str, UUID] = {}
        prefixes: dict[str, str] = {}
        bodies: dict[str, UUID] = {}
        offices: dict[str, UUID] = {}
        for code, uf, label in every.sort("municipality").select("municipality", "uf", "label").iter_rows():
            town = proper(label)
            area = cs.key("br.tse.municipality", code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code="br.municipality")
                cs.add("area_parent", area_id=area, parent_id=states[uf], valid_from=None)
                name(area, town)
            polity = polities[code] = cs.key(f"{SOURCE}.polity", code)
            path = f"br/{uf.lower()}/{slug(label)}"
            if polity in cs.minted:
                if path in taken:
                    path = f"{path}-{code}"
                taken.add(path)
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                name(polity, town)
                cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"BR-{uf}"))
                cs.add("polity_territory", polity_id=polity, area_id=area)
                cs.add("slug", path=path, entity_id=polity)
            else:
                (path,) = conn.execute(
                    "select path from slug where entity_id = %s order by length(path) limit 1", [polity]
                ).fetchone() or (path,)
            prefixes[code] = path
            bodies[code] = cs.key(f"{SOURCE}.body", code)
            if bodies[code] in cs.minted:
                cs.add("body", id=bodies[code], polity_id=polity, kind="unicameral")
                name(bodies[code], f"Municipal Chamber of {town}", "en")
            offices[code] = cs.key(f"{SOURCE}.office", code)
            if offices[code] in cs.minted:
                cs.add("office", id=offices[code], polity_id=polity, kind="mayor")
                name(offices[code], f"Mayor of {town}", "en")

        elections: dict[tuple[str, str], UUID] = {}
        days: dict[tuple[str, str], date] = {}
        for code, round, day in places.sort("round").select("municipality", "round", "day").iter_rows():
            election = elections[(code, round)] = cs.key(f"{SOURCE}.election", f"{year}:{code}:{round}")
            days[(code, round)] = day
            cs.add(
                "election",
                id=election,
                polity_id=polities[code],
                parent_id=umbrella if round == "1" else elections[(code, "1")],
                kind="local" if round == "1" else "runoff",
                date_from=day,
                date_to=day,
                status="held",
            )
            cs.add(
                "slug",
                path=f"{prefixes[code]}/{day}-{'local' if round == '1' else 'runoff'}",
                entity_id=election,
            )
        labels = dict(every.select("municipality", "label").iter_rows())
        for (code, round), election in elections.items():
            name(
                election,
                f"{year} {proper(labels[code])} municipal election" + (", second round" if round == "2" else ""),
                "en",
            )

        units: dict[str, str] = {}
        rows: list[dict[str, object]] = []
        for code in sorted(labels):
            opened = days[(code, "1")]
            unit = cs.unit("br.municipality", code, opened)
            units[code] = str(unit)
            rows.append(
                {
                    "id": str(unit),
                    "polity_id": str(brazil),
                    "scheme_code": "br.municipality",
                    "code": code,
                    "area_relation": "equals",
                    "area_id": str(cs.key("br.tse.municipality", code)),
                    "kind": "standard",
                    "valid_from": opened,
                    "valid_to": last + timedelta(days=1),
                }
            )
        cs.frame("reporting_unit", pl.DataFrame(rows))

        parties: dict[str, UUID] = {}
        by_abbreviation: dict[str, UUID] = {}
        for number, abbreviation, label in (
            pl.concat(
                [
                    candidates.select("party_number", "party_abbreviation", "party_label"),
                    legends.select("party_number", "party_abbreviation", "party_label"),
                ]
            )
            .unique(["party_number", "party_abbreviation"])
            .sort("party_number")
            .iter_rows()
        ):
            key = party_key(number, abbreviation, year)
            if key not in parties:
                parties[key] = cs.key("br.tse.party", key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind="party", polity_id=brazil)
                    name(parties[key], proper(label))
                    name(parties[key], abbreviation, kind="abbreviation")
            by_abbreviation[abbreviation.replace(" ", "").upper()] = parties[key]

        alliances: dict[str, UUID] = {}
        for coalition_code, label, members in (
            candidates.filter(pl.col("coalition").is_not_null())
            .select("coalition", "coalition_label", "coalition_members")
            .unique("coalition")
            .sort("coalition")
            .iter_rows()
        ):
            alliance = alliances[coalition_code] = cs.key(f"{SOURCE}.coalition", coalition_code)
            parts = coalition_code.split(":")
            cs.add(
                "alliance",
                id=alliance,
                kind="electoral_alliance",
                election_id=umbrella if parts[1].startswith("F") else elections[(parts[1], "1")],
            )
            if label and not label.startswith("#") and label.upper() != "PARTIDO ISOLADO":
                name(alliance, proper(label))
            for member in {
                part.strip().split("-", 1)[-1].replace(" ", "").upper() for part in (members or "").split("/")
            }:
                if member in by_abbreviation:
                    cs.add("alliance_member", alliance_id=alliance, party_id=by_abbreviation[member])

        persons: dict[str, UUID] = dict(
            conn.execute(
                """
                select x.value, x.entity_id from external_id x
                where x.scheme = 'br.tse_history.person'
                  and not exists (select from entity_merge m where m.entity_id = x.entity_id)
                """
            ).fetchall()
        )
        mayor_registered = dict(
            detail.filter(pl.col("contest").str.starts_with(f"{year}:1:{MAYOR}:"))
            .select(pl.col("contest").str.slice(-5), "registered")
            .iter_rows()
        )
        valid = (
            votes.group_by("contest")
            .agg(pl.col("valid").sum())
            .join(legends.group_by("contest").agg(legend=pl.col("votes").sum()), on="contest", how="left")
            .join(detail.select("contest", "blank"), on="contest", how="left")
        )
        totals = {code: (counted + (legend or 0), blank or 0) for code, counted, legend, blank in valid.iter_rows()}
        slates = (
            pl.concat(
                [
                    candidates.filter(pl.col("office") == COUNCIL).select(
                        "contest", "party_number", "party_abbreviation", "coalition"
                    ),
                    legends.select("contest", "party_number", "party_abbreviation", "coalition"),
                ]
            )
            .sort("coalition", nulls_last=True)
            .unique(["contest", "party_number"], keep="first")
        )
        tallied = set(detail["contest"])
        options: dict[str, str] = {}
        result_sets: dict[str, str] = {}
        contests: dict[str, UUID] = {}
        by_contest = candidates.sort("number").partition_by("contest", as_dict=True)
        by_slate = slates.sort("party_number").partition_by("contest", as_dict=True)
        for (code,), standing in sorted(by_contest.items()):
            _, round, cargo, municipality = str(code).split(":")
            day = days[(municipality, round)]
            town = proper(labels[municipality])
            mayor = cargo == MAYOR
            elected = standing.filter(pl.col("outcome") == "elected").height
            runoff = f"{year}:2:{cargo}:{municipality}" in by_contest
            key = f"{day}:{'mayor' if mayor else 'council'}:{municipality}"
            contest = contests[str(code)] = cs.key("br.contest", key)
            magnitude = 1 if mayor else elected or None
            override = None
            if not mayor and magnitude is not None and year <= 2016:
                counted, blank = totals[str(code)]
                share = (counted + blank) / counted if year == 1996 and counted else 1
                override = {"threshold": {"percent": float(f"{100 * share / magnitude:.6f}"), "level": "list"}}
            elif not mayor and magnitude is not None and year >= 2024:
                override = {"threshold": {"percent": float(f"{80 / magnitude:.6f}"), "level": "list"}}
            two_round = mayor_registered.get(municipality, 0) > RUNOFF_ELECTORATE or round == "2" or runoff
            cs.add(
                "contest",
                id=contest,
                election_id=elections[(municipality, round)],
                kind="office" if mayor else "list_allocation",
                office_id=offices[municipality] if mayor else None,
                body_id=None if mayor else bodies[municipality],
                district_area_id=cs.key("br.tse.municipality", municipality),
                magnitude=magnitude,
                round_number=int(round),
                previous_round_id=contests.get(f"{year}:1:{cargo}:{municipality}") if round == "2" else None,
                electoral_system_id=(systems["mayor-two-round" if two_round else "mayor-plurality"])
                if mayor
                else council_system,
                rules_override=override,
                default_result_kind="counted",
            )
            suffix = f", {'second' if round == '2' else 'first'} round" if mayor and (runoff or round == "2") else ""
            name(
                contest,
                f"{year} {town} mayoral election{suffix}" if mayor else f"{year} {town} Municipal Chamber election",
                "en",
            )
            stage = "local" if round == "1" else "runoff"
            cs.add(
                "slug",
                path=f"{prefixes[municipality]}/{day}-{stage}/{'mayor' if mayor else 'municipal-chamber'}",
                entity_id=contest,
            )
            if str(code) in short:
                cs.add("note", entity_id=contest, kind="caveat", body_md=SHORT.format(short=f"{short[str(code)]:,}"))
            result_set = cs.key(f"{SOURCE}.result_set", key) if str(code) in tallied else None
            if result_set is not None:
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=elections[(municipality, round)],
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=results,
                    is_default=True,
                    completeness=1,
                )
                result_sets[str(code)] = str(result_set)

            lists: dict[str, UUID] = {}
            if not mayor and (str(code),) in by_slate:
                for number, abbreviation, coalition_code in (
                    by_slate[(str(code),)].select("party_number", "party_abbreviation", "coalition").iter_rows()
                ):
                    party = parties[party_key(number, abbreviation, year)]
                    group = coalition_code or f"{number}:{abbreviation}"
                    if group not in lists:
                        lists[group] = cs.key("br.option", f"{key}:list:{group}")
                        cs.add(
                            "contest_option",
                            id=lists[group],
                            contest_id=contest,
                            kind="list",
                            party_id=None if coalition_code else party,
                            alliance_id=alliances[coalition_code] if coalition_code else None,
                            ballot_label=abbreviation if coalition_code is None else None,
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
            for row in standing.iter_rows(named=True):
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
                party = parties[party_key(row["party_number"], row["party_abbreviation"], year)]
                option = cs.key("br.option", f"{key}:{row['candidate']}")
                options[f"{code}:{row['candidate']}"] = str(option)
                group = row["coalition"] or f"{row['party_number']}:{row['party_abbreviation']}"
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
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
                if row["outcome"] is not None and result_set is not None:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=row["outcome"])
                    if lists and row["outcome"] == "elected":
                        seats[group] = seats.get(group, 0) + 1
            for group, count in seats.items():
                cs.add("outcome", result_set_id=result_set, option_id=lists[group], kind="won", seats=count)

        live = pl.col("contest").is_in(list(result_sets))
        common = {"channel": pl.lit(0, pl.Int16), "count_round": pl.lit(0, pl.Int16), "flags": pl.lit(0, pl.Int16)}
        cs.frame(
            "result_value",
            pl.concat(
                [
                    votes.filter(live)
                    .unpivot(on=["valid", "void"], index=["contest", "candidate"])
                    .filter(pl.col("value") > 0)
                    .select(
                        "contest",
                        option=pl.col("contest") + ":" + pl.col("candidate"),
                        measure=pl.col("variable").replace_strict({"valid": 1, "void": 5}, return_dtype=pl.Int16),
                        value="value",
                    ),
                    legends.filter(live & (pl.col("votes") > 0)).select(
                        "contest",
                        option=pl.col("contest") + ":legend:" + pl.col("party_number"),
                        measure=pl.lit(1, pl.Int16),
                        value="votes",
                    ),
                    detail.filter(live)
                    .unpivot(on=["registered", "voters", "blank", "null"], index=["contest"])
                    .select(
                        "contest",
                        option=pl.lit(None, pl.String),
                        measure=pl.col("variable").replace_strict(
                            {"registered": 2, "voters": 14, "blank": 6, "null": 5}, return_dtype=pl.Int16
                        ),
                        value="value",
                    ),
                ]
            ).select(
                result_set_id=pl.col("contest").replace_strict(result_sets).cast(pl.Categorical),
                unit_id=pl.col("contest").str.slice(-5).replace_strict(units).cast(pl.Categorical),
                option_id=pl.col("option").replace_strict(options, default=None).cast(pl.Categorical),
                measure="measure",
                value="value",
                **common,
            ),
        )
        return cs.write()
