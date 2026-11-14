import io
import shutil
import unicodedata
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.br.tse import ANNULLED, NOT_OPENED, UFS, proper
from ea.sources.br.tse_history import GOVERNOR, SENATE, SOURCE_NOTE, party_key
from ea.store import DATA, connect

SOURCE = "br.supplementary"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele/"
HOLDERS = (SOURCE, "br.tse_history", "br.tse")


class Spec(NamedTuple):
    year: int
    uf: str
    cargo: str
    election: str
    day: date


CONTESTS = {
    "mt-senate": Spec(2018, "MT", "5", "438", date(2020, 11, 15)),
    "rr-governor": Spec(2022, "RR", "3", "6278", date(2026, 6, 21)),
}
RESIDUAL = 50
GAP = (
    "The electoral court's section file lists no candidate votes for {sections} sections in {place}, whose "
    "section details record {nominal} nominal votes. The municipal totals count them, so the section values fall "
    "short of the municipal ones by that many votes. Registered voters, turnout, blank votes, and null votes are "
    "complete."
)
CAVEATS = {
    "rr-governor": (
        "The electoral court's results list no candidate as elected. The 160,004 votes for Arthur Henrique, the most "
        "voted candidate, count as annulled while the court decides on his candidacy registration, which a regional "
        "court rejected."
    )
}


def url(kind: str, year: int, uf: str = "") -> str:
    folder = {
        "candidates": "votacao_candidato_munzona",
        "detail": "detalhe_votacao_munzona",
        "roster": "consulta_cand",
        "sections": "votacao_secao",
        "section_detail": "detalhe_votacao_secao",
    }[kind]
    return f"{TSE}{folder}/{folder}_{year}{f'_{uf}' if kind == 'sections' else ''}.zip"


def held(address: str) -> raw.Artifact:
    for source in HOLDERS:
        try:
            return raw.cached(source, address)
        except LookupError:
            continue
    raise LookupError(address)


def wanted() -> list[str]:
    return [
        url(kind, spec.year, spec.uf)
        for spec in CONTESTS.values()
        for kind in ("candidates", "detail", "roster", "sections", "section_detail")
    ]


def fetch() -> None:
    for address in wanted():
        try:
            held(address)
        except LookupError:
            raw.fetch(SOURCE, address, suffix=".zip")


def unpack(address: str, suffix: str) -> Path:
    with zipfile.ZipFile(held(address).path) as archive:
        member = next(name for name in archive.namelist() if name.endswith(suffix))
        with archive.open(member) as source, (PARSED / member).open("w", encoding="utf-8") as target:
            shutil.copyfileobj(io.TextIOWrapper(source, encoding="latin-1"), target, 16 << 20)
    return PARSED / member


def scan(address: str, spec: Spec, suffix: str) -> pl.DataFrame:
    path = unpack(address, suffix)
    frame = (
        pl.scan_csv(path, separator=";", infer_schema=False)
        .filter(
            (pl.col("CD_TIPO_ELEICAO") == "1")
            & (pl.col("CD_ELEICAO") == spec.election)
            & (pl.col("CD_CARGO") == spec.cargo)
        )
        .collect()
    )
    path.unlink()
    if not frame.height:
        raise ValueError(f"no supplementary rows in {address}")
    return frame


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    frames: dict[str, list[pl.DataFrame]] = {
        name: [] for name in ("candidates", "votes", "detail", "sections", "uncounted")
    }
    sheets: list[pl.DataFrame] = []
    for code, spec in CONTESTS.items():
        year, uf = spec.year, spec.uf
        municipality = pl.col("CD_MUNICIPIO").str.zfill(5)
        section = pl.concat_str(
            municipality, pl.col("NR_ZONA").cast(pl.Int64), pl.col("NR_SECAO").cast(pl.Int64), separator=":"
        )
        votes = scan(url("candidates", year), spec, f"_{uf}.csv")
        roster = (
            scan(url("roster", year), spec, "_BRASIL.csv")
            .filter(pl.col("SG_UF") == uf)
            .join(votes.select("SQ_CANDIDATO").unique(), on="SQ_CANDIDATO", how="semi")
        )
        frames["candidates"].append(
            roster.select(
                contest=pl.lit(code),
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
                birth=pl.col("DT_NASCIMENTO").str.to_date("%d/%m/%Y", strict=False),
                gender=pl.col("DS_GENERO").replace_strict(
                    {"MASCULINO": "male", "FEMININO": "female"}, default=None, return_dtype=pl.String
                ),
                outcome=pl.when(pl.col("DS_SIT_TOT_TURNO") == "ELEITO").then(pl.lit("elected")),
            ).unique(["contest", "candidate"])
        )

        frames["votes"].append(
            votes.group_by(contest=pl.lit(code), municipality=municipality, candidate=pl.col("SQ_CANDIDATO")).agg(
                valid=pl.col("QT_VOTOS_NOMINAIS_VALIDOS").cast(pl.Int64).sum(),
                void=(
                    pl.col("QT_VOTOS_NOMINAIS").cast(pl.Int64) - pl.col("QT_VOTOS_NOMINAIS_VALIDOS").cast(pl.Int64)
                ).sum(),
            )
        )
        detail = scan(url("detail", year), spec, f"_{uf}.csv")
        frames["detail"].append(
            detail.group_by(contest=pl.lit(code), municipality=municipality).agg(
                registered=pl.col("QT_APTOS").cast(pl.Int64).sum(),
                voters=pl.col("QT_COMPARECIMENTO").cast(pl.Int64).sum(),
                blank=pl.col("QT_VOTOS_BRANCOS").cast(pl.Int64).sum(),
                null=pl.col("QT_VOTOS_NULOS").cast(pl.Int64).sum(),
            )
        )

        raw_sections = scan(url("sections", year, uf), spec, f"_{uf}.csv")
        tally = raw_sections.select(
            contest=pl.lit(code),
            municipality=municipality,
            section=section,
            choice="NR_VOTAVEL",
            candidate=pl.col("SQ_CANDIDATO") if "SQ_CANDIDATO" in raw_sections.columns else pl.lit(None, pl.String),
            votes=pl.col("QT_VOTOS").cast(pl.Int64),
        )
        counts = {
            "registered": "QT_APTOS",
            "voters": "QT_COMPARECIMENTO",
            "nominal": "QT_VOTOS_NOMINAIS",
            "blank": "QT_VOTOS_BRANCOS",
            "null": "QT_VOTOS_NULOS",
            "separate": "QT_VOTOS_ANULADOS_APU_SEP",
        }
        sheet = scan(url("section_detail", year), spec, f"_{uf}.csv").select(
            contest=pl.lit(code),
            municipality=municipality,
            label="NM_MUNICIPIO",
            zone=pl.col("NR_ZONA").cast(pl.Int64),
            number=pl.col("NR_SECAO").cast(pl.Int64),
            section=section,
            **{field: pl.col(column).cast(pl.Int64) for field, column in counts.items()},
            opened=pl.col("ST_SECAO_INSTALADA") != "Não",
            annulled=pl.col("ST_SECAO_ANULADA") == "Sim",
        )
        if sheet.select("section").is_duplicated().any():
            raise ValueError(f"{code}: a section's detail repeats")
        pairs = roster.select(choice="NR_CANDIDATO", candidate="SQ_CANDIDATO")
        if tally["candidate"].is_null().all():
            if pairs.select("choice").is_duplicated().any():
                raise ValueError(f"{code}: a candidate number repeats in the roster")
            tally = (
                tally.join(pairs, on="choice", how="left", suffix="_roster")
                .with_columns(candidate=pl.col("candidate_roster"))
                .drop("candidate_roster")
            )
        tally = tally.with_columns(
            kind=pl.when(pl.col("choice") == "95")
            .then(pl.lit("blank"))
            .when(pl.col("choice").is_in(["96", "97"]))
            .then(pl.lit("null"))
            .otherwise(pl.lit("nominal"))
        )
        stray = tally.filter((pl.col("kind") == "nominal") & pl.col("candidate").is_null() & (pl.col("votes") > 0))
        if stray.height:
            raise ValueError(f"{code}: section votes for numbers outside the roster: {stray.head(5)}")
        if tally.select("section", "choice").is_duplicated().any():
            raise ValueError(f"{code}: a section reports the same choice twice")
        lacking = sheet.join(tally.select("section").unique(), on="section", how="anti")
        reported = sheet.join(lacking.select("section"), on="section", how="anti")
        summed = tally.group_by("section", "kind").agg(pl.col("votes").sum())
        expected = reported.with_columns(null=pl.col("null") + pl.col("separate")).unpivot(
            on=["nominal", "blank", "null"], index="section", variable_name="kind", value_name="sum"
        )
        mismatch = expected.join(summed, on=["section", "kind"], how="left").filter(
            pl.col("sum") != pl.col("votes").fill_null(0)
        )
        if mismatch.height or summed.join(expected, on=["section", "kind"], how="anti").height:
            raise ValueError(f"{code}: section votes differ from the section details: {mismatch.head(5)}")
        rolled = (
            tally.filter((pl.col("kind") == "nominal") & (pl.col("votes") > 0))
            .group_by("contest", "municipality", "candidate")
            .agg(pl.col("votes").sum())
        )
        municipal = (
            frames["votes"][-1]
            .select("contest", "municipality", "candidate", municipal=pl.col("valid") + pl.col("void"))
            .filter(pl.col("municipal") > 0)
        )
        differ = municipal.join(
            rolled, on=["contest", "municipality", "candidate"], how="full", coalesce=True
        ).with_columns(gap=pl.col("municipal").fill_null(0) - pl.col("votes").fill_null(0))
        owed = lacking.group_by("municipality").agg(nominal=pl.col("nominal").sum())
        short = differ.group_by("municipality").agg(gap=pl.col("gap").sum(), least=pl.col("gap").min())
        unexplained = short.join(owed, on="municipality", how="full", coalesce=True).filter(
            (pl.col("gap").fill_null(0) != pl.col("nominal").fill_null(0)) | (pl.col("least").fill_null(0) < 0)
        )
        if unexplained.height:
            raise ValueError(f"{code}: section sums differ from the municipal totals: {unexplained.head(10)}")
        measures = ["registered", "voters", "blank", "null"]
        totals = sheet.group_by("contest", "municipality").agg(pl.col(measures).sum())
        apart = (
            frames["detail"][-1]
            .join(totals, on=["contest", "municipality"], how="full", coalesce=True, suffix="_sections")
            .filter(
                pl.any_horizontal(
                    pl.col(field).fill_null(-1) != pl.col(f"{field}_sections").fill_null(-1)
                    for field in ("voters", "blank")
                )
                | ~(pl.col("null") - pl.col("null_sections")).is_between(0, 0)
                | ~(pl.col("registered") - pl.col("registered_sections")).is_between(0, RESIDUAL)
            )
        )
        if apart.height:
            raise ValueError(f"{code}: section sums differ from the municipal details: {apart.head(10)}")
        both = frames["votes"][-1].filter((pl.col("valid") > 0) & (pl.col("void") > 0))
        if both.height:
            raise ValueError(f"{code}: candidates with both valid and annulled votes in a municipality: {both.head(5)}")
        frames["sections"].append(
            pl.concat(
                [
                    tally.filter((pl.col("kind") == "nominal") & (pl.col("votes") > 0)).select(
                        "contest", "section", "candidate", measure=pl.lit(1), value="votes"
                    ),
                    sheet.with_columns(null=pl.col("null") + pl.col("separate"))
                    .unpivot(on=measures, index=["contest", "section"])
                    .filter(pl.col("value") > 0)
                    .select(
                        "contest",
                        "section",
                        candidate=pl.lit(None, pl.String),
                        measure=pl.col("variable").replace_strict(
                            {"registered": 2, "voters": 14, "blank": 6, "null": 5}
                        ),
                        value="value",
                    ),
                ],
                how="vertical_relaxed",
            )
        )
        frames["uncounted"].append(
            lacking.group_by("contest", "municipality").agg(
                label=pl.col("label").first(), sections=pl.len(), nominal=pl.col("nominal").sum()
            )
        )
        sheets.append(sheet.with_columns(uf=pl.lit(uf), day=pl.lit(spec.day)))

    for name, parts in frames.items():
        pl.concat(parts, how="vertical_relaxed").write_parquet(PARSED / f"{name}.parquet")
    pl.concat(sheets).write_parquet(PARSED / "sheet.parquet")


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    sheet = pl.read_parquet(PARSED / "sheet.parquet")
    values = pl.read_parquet(PARSED / "sections.parquet")
    voided = votes.group_by("contest", "candidate").agg(pl.col("void").sum())
    voided = voided.filter(pl.col("void") > 0).with_columns(annulled=pl.lit(True))
    values = values.join(voided.select("contest", "candidate", "annulled"), on=["contest", "candidate"], how="left")
    values = values.with_columns(
        measure=pl.when(pl.col("annulled").fill_null(False) & (pl.col("measure") == 1))
        .then(pl.lit(5))
        .otherwise(pl.col("measure"))
        .cast(pl.Int16)
    ).drop("annulled")
    values = values.group_by("contest", "section", "candidate", "measure").agg(pl.col("value").sum())
    detail = pl.read_parquet(PARSED / "detail.parquet")
    uncounted = pl.read_parquet(PARSED / "uncounted.parquet")
    official = dict(votes.group_by("contest").agg(pl.col("valid").sum()).iter_rows())
    counted = dict(values.filter(pl.col("measure") == 1).group_by("contest").agg(pl.col("value").sum()).iter_rows())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Brazil's supplementary elections for Senator in Mato Grosso (2020) and Governor of Roraima (2026), by polling section",
            polity="BR",
        )

        def name(entity: UUID, text: str, lang: str = "pt", kind: str = "official") -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def slug(text: str) -> str:
            return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace(" ", "-")

        sources: dict[int, UUID] = {}
        for year in {spec.year for spec in CONTESTS.values()}:
            title = f"Resultados - {year}"
            sources[year] = cs.source("Tribunal Superior Eleitoral", title)
            if sources[year] in cs.minted:
                cs.add(
                    "source",
                    id=sources[year],
                    kind="official_body",
                    publisher="Tribunal Superior Eleitoral",
                    title=title,
                    url=f"https://dadosabertos.tse.jus.br/dataset/resultados-{year}",
                    license="Creative Commons Attribution",
                    license_url="http://www.opendefinition.org/licenses/cc-by",
                    notes=SOURCE_NOTE,
                )
            for spec in CONTESTS.values():
                if spec.year == year:
                    for kind in ("candidates", "detail", "roster", "sections", "section_detail"):
                        cs.artifact(held(url(kind, year, spec.uf)), sources[year])

        brazil = cs.polity("BR")
        senate = cs.key("br.body", "senate")
        systems = {
            "governor": cs.key("br.electoral_system", "governor-two-round"),
            "senate": cs.key("br.electoral_system", "senate-plurality"),
        }
        for key, label, methodology in (
            ("governor", "Brazilian gubernatorial two-round system", GOVERNOR),
            ("senate", "Brazilian Senate plurality system", SENATE),
        ):
            if systems[key] in cs.minted:
                raise ValueError(f"the {key} electoral system is not in the archive")
        if senate in cs.minted:
            raise ValueError("the Senate is not in the archive")

        parties: dict[str, UUID] = {}
        alliances: dict[str, UUID] = {}
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
        persons.update(
            dict(
                conn.execute(
                    """
                    select x.value, x.entity_id from external_id x
                    where x.scheme = 'br.tse_history.person'
                      and not exists (select from entity_merge m where m.entity_id = x.entity_id)
                    """
                ).fetchall()
            )
        )

        areas = {code: cs.key("br.tse.municipality", code) for code in sheet["municipality"].unique()}
        absent = [code for code, area in areas.items() if area in cs.minted]
        if absent:
            raise ValueError(f"municipalities not in the archive: {absent[:10]}")

        by_day = {code: spec.day for code, spec in CONTESTS.items()}
        section_ids: dict[tuple[str, str], str] = {}
        rows: list[dict[str, object]] = []
        label_rows: list[dict[str, object]] = []
        for contest, code, municipality, label, zone, number in sheet.select(
            "contest", "section", "municipality", "label", "zone", "number"
        ).iter_rows():
            day = by_day[contest]
            unit = cs.unit("br.secao", code, day)
            section_ids[(contest, code)] = str(unit)
            if unit in cs.minted:
                rows.append(
                    {
                        "id": str(unit),
                        "polity_id": str(brazil),
                        "scheme_code": "br.secao",
                        "code": code,
                        "area_relation": "within",
                        "area_id": str(areas[municipality]),
                        "location": None,
                        "kind": "standard",
                        "valid_from": day,
                        "valid_to": day + timedelta(days=1),
                    }
                )
                label_rows.append(
                    {
                        "entity_id": str(unit),
                        "text": f"{proper(label)}, zona {zone}, seção {number}",
                        "lang": "pt",
                        "kind": "official",
                        "is_preferred": True,
                    }
                )
        cs.frame("reporting_unit", pl.DataFrame(rows, schema_overrides={"location": pl.String}))
        cs.frame("name", pl.DataFrame(label_rows))
        for contest, code, annulled in (
            sheet.filter(~pl.col("opened") | pl.col("annulled")).select("contest", "section", "annulled").iter_rows()
        ):
            day = by_day[contest]
            cs.add(
                "note",
                entity_id=UUID(section_ids[(contest, code)]),
                kind="caveat",
                body_md=(ANNULLED if annulled else NOT_OPENED).format(day=f"{day.day} {day:%B %Y}"),
            )

        town_ids: dict[tuple[str, str], str] = {}
        towns: list[dict[str, object]] = []
        for contest, code in detail.select("contest", "municipality").iter_rows():
            day = by_day[contest]
            unit = cs.unit("br.municipality", code, day)
            town_ids[(contest, code)] = str(unit)
            if unit in cs.minted:
                towns.append(
                    {
                        "id": str(unit),
                        "polity_id": str(brazil),
                        "scheme_code": "br.municipality",
                        "code": code,
                        "area_relation": "equals",
                        "area_id": str(areas[code]),
                        "kind": "standard",
                        "valid_from": day,
                        "valid_to": day + timedelta(days=1),
                    }
                )
        if towns:
            cs.frame("reporting_unit", pl.DataFrame(towns))

        options: dict[str, str] = {}
        result_sets: dict[str, str] = {}
        section_sets: dict[str, str] = {}
        for contest_code, spec in CONTESTS.items():
            uf, cargo, day, year = spec.uf, spec.cargo, spec.day, spec.year
            office = "governor" if cargo == "3" else "senate"
            label = UFS[uf]
            election = cs.key("br.election", f"{day}:by-election:{uf}")
            cs.add(
                "election",
                id=election,
                polity_id=brazil if office == "senate" else cs.polity(f"BR-{uf}"),
                kind="by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            title = (
                f"{day.year} Senate supplementary election in {label}"
                if office == "senate"
                else f"{day.year} gubernatorial supplementary election in {label}"
            )
            name(election, title, "en")
            name(
                election,
                f"Eleição suplementar para {'o Senado' if office == 'senate' else 'o governo'} de {label}",
                "pt",
                "native",
            )
            cs.add("slug", path=f"br/{day}-by-election/{office}-{slug(label)}", entity_id=election)
            contest = cs.key("br.contest", f"{day}:{office}:{uf}:supplementary")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats" if office == "senate" else "office",
                office_id=cs.key("br.office", f"governor:{uf}") if office == "governor" else None,
                body_id=senate if office == "senate" else None,
                district_area_id=cs.area("br.uf", uf),
                magnitude=1,
                round_number=1,
                electoral_system_id=systems[office],
                default_result_kind="counted",
            )
            name(contest, title, "en")
            cs.add("slug", path=f"br/{day}-by-election/{office}-{slug(label)}/{office}", entity_id=contest)
            if contest_code in CAVEATS:
                cs.add("note", entity_id=contest, kind="caveat", body_md=CAVEATS[contest_code])
            sets: dict[str, UUID] = {}
            for grain, default in (("municipality", True), ("section", False)):
                sets[grain] = cs.key(f"{SOURCE}.result_set", f"{day}:{office}:{uf}:{grain}")
                cs.add(
                    "result_set",
                    id=sets[grain],
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources[year],
                    is_default=default,
                    completeness=1 if default else min(1, counted[contest_code] / official[contest_code]),
                )
            result_set = sets["municipality"]
            result_sets[contest_code] = str(result_set)
            section_sets[contest_code] = str(sets["section"])
            for gap in uncounted.filter(pl.col("contest") == contest_code).iter_rows(named=True):
                cs.add(
                    "note",
                    entity_id=sets["section"],
                    kind="caveat",
                    body_md=GAP.format(
                        sections=gap["sections"], place=proper(gap["label"]), nominal=f"{gap['nominal']:,}"
                    ),
                )

            standing = candidates.filter(pl.col("contest") == contest_code)
            for member in sorted({part for row in standing["coalition"].drop_nulls().unique() for part in [row]}):
                row = standing.filter(pl.col("coalition") == member).row(0, named=True)
                alliances[member] = cs.key(f"{SOURCE}.coalition", member)
                cs.add("alliance", id=alliances[member], kind="electoral_alliance", election_id=election)
                name(alliances[member], proper(row["coalition_label"]))
            for row in standing.sort("number").iter_rows(named=True):
                key = party_key(row["party_number"], row["party_abbreviation"], year)
                if key not in parties:
                    parties[key] = cs.key("br.tse.party", key)
                    if parties[key] in cs.minted:
                        cs.add("party", id=parties[key], kind="party", polity_id=brazil)
                        name(parties[key], proper(row["party_label"]))
                        name(parties[key], row["party_abbreviation"], kind="abbreviation")
                person_key = (
                    f"{slug(row['name'])}|{row['birth']}" if row["birth"] is not None else f"{year}:{row['candidate']}"
                )
                if person_key not in persons:
                    persons[person_key] = cs.key("br.tse_history.person", person_key)
                    if persons[person_key] in cs.minted:
                        cs.add(
                            "person",
                            id=persons[person_key],
                            gender=row["gender"],
                            birth_date=row["birth"],
                            birth_precision="day" if row["birth"] is not None else None,
                        )
                        name(persons[person_key], proper(row["name"]))
                option = cs.key("br.option", f"{day}:{office}:{uf}:{row['candidate']}")
                options[f"{contest_code}:{row['candidate']}"] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="ticket" if office == "governor" else "candidate",
                    party_id=parties[key],
                    alliance_id=alliances[row["coalition"]] if row["coalition"] is not None else None,
                    ballot_label=proper(row["ballot"]),
                )
                cs.add(
                    "candidacy",
                    id=cs.key("br.candidacy", f"{day}:{office}:{uf}:{row['candidate']}"),
                    person_id=persons[person_key],
                    option_id=option,
                    role="head",
                    ballot_name=row["ballot"],
                    member_of_party_id=parties[key],
                )
                if row["outcome"] is not None:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=row["outcome"])

        common = {"channel": pl.lit(0, pl.Int16), "count_round": pl.lit(0, pl.Int16), "flags": pl.lit(0, pl.Int16)}
        cs.frame(
            "result_value",
            pl.concat(
                [
                    votes.unpivot(on=["valid", "void"], index=["contest", "municipality", "candidate"])
                    .filter(pl.col("value") > 0)
                    .select(
                        "contest",
                        "municipality",
                        candidate="candidate",
                        measure=pl.col("variable").replace_strict({"valid": 1, "void": 5}, return_dtype=pl.Int16),
                        value="value",
                    ),
                    detail.unpivot(
                        on=["registered", "voters", "blank", "null"], index=["contest", "municipality"]
                    ).select(
                        "contest",
                        "municipality",
                        candidate=pl.lit(None, pl.String),
                        measure=pl.col("variable").replace_strict(
                            {"registered": 2, "voters": 14, "blank": 6, "null": 5}, return_dtype=pl.Int16
                        ),
                        value="value",
                    ),
                ]
            ).select(
                result_set_id=pl.col("contest").replace_strict(result_sets).cast(pl.Categorical),
                unit_id=pl.struct("contest", "municipality")
                .map_elements(lambda row: town_ids[(row["contest"], row["municipality"])], return_dtype=pl.String)
                .cast(pl.Categorical),
                option_id=pl.when(pl.col("candidate").is_not_null())
                .then(pl.col("contest") + ":" + pl.col("candidate"))
                .replace_strict(options, default=None)
                .cast(pl.Categorical),
                measure="measure",
                value="value",
                **common,
            ),
        )
        cs.frame(
            "result_value",
            values.select(
                result_set_id=pl.col("contest").replace_strict(section_sets).cast(pl.Categorical),
                unit_id=pl.struct("contest", "section")
                .map_elements(lambda row: section_ids[(row["contest"], row["section"])], return_dtype=pl.String)
                .cast(pl.Categorical),
                option_id=pl.when(pl.col("candidate").is_not_null())
                .then(pl.col("contest") + ":" + pl.col("candidate"))
                .replace_strict(options, default=None)
                .cast(pl.Categorical),
                measure="measure",
                value="value",
                **common,
            ),
        )
        return cs.write()
