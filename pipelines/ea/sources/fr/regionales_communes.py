import re
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.fr import regionales
from ea.sources.fr.cdsp import AGGREGATED, AGGREGATED_TITLE
from ea.sources.fr.cdsp import SOURCE as CDSP
from ea.store import DATA, connect

SOURCE = "fr.regionales_communes"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
STATIC = "https://static.data.gouv.fr"
WORKBOOKS = {
    2004: (
        f"{STATIC}/ad/c8652a8852538174350b935394c83065da995f9c2d611ae863ec06c09c38c2.xls",
        {1: "Tour 1", 2: "Tour 2"},
    ),
    2010: (
        f"{STATIC}/b0/0dbaa8911697ba5f962abe6ab2407092ced31528482924224cf55d957d3a0d.xls",
        {1: "Tour 1", 2: "Tour 2"},
    ),
}
BUREAUX = (2015, 2021)
TOTALS = {
    "Inscrits": "registered",
    "Votants": "voters",
    "Blancs et nuls": "invalid",
    "Exprimés": "valid",
}
MEASURES = {"registered": 2, "voters": 14, "valid": 4, "invalid": 5, "null": 5, "blank": 6}
GENERAL = {
    "inscrits": "registered",
    "votants": "voters",
    "exprimes": "valid",
    "blancs": "blank",
    "nuls": "null",
}
METHOD = (
    "Results by commune from the Interior Ministry's results file for the election, which the ministry published "
    "separately from its file by département."
)
METHOD_BUREAUX = (
    "Results by commune, added up from the Interior Ministry's results by polling station on data.gouv.fr. The "
    "communes of a département add up to the département's result set."
)
DRIFT = (
    "The ministry's file by commune and its file by département are separate publications and differ slightly: no "
    "total or list's votes here differs from the département result set by up to {votes} votes."
)


def fetch() -> None:
    for url, _ in WORKBOOKS.values():
        raw.fetch(regionales.SOURCE, url, suffix=Path(url).suffix)
    for url in AGGREGATED.values():
        raw.fetch(CDSP, url, suffix=".parquet")


def commune_code(department: str, number: str) -> str:
    if department in regionales.OVERSEAS:
        return regionales.OVERSEAS[department][:2] + number.zfill(3)
    return department.zfill(2) + number.zfill(3)


def fold(text: object) -> str:
    plain = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]+", " ", plain.lower()).strip()


def clean(text: object) -> str | None:
    if text is None or str(text).strip() == "":
        return None
    return re.sub(r"\s+", " ", str(text)).strip()


def read_workbook(year: int, round_: int) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    url, sheets = WORKBOOKS[year]
    frame = pl.read_excel(
        raw.cached(regionales.SOURCE, url).path,
        sheet_name=sheets[round_],
        engine="calamine",
        has_header=False,
        infer_schema_length=0,
    )
    rows = [list(row) for row in frame.iter_rows()]
    header = [str(cell).strip() if cell is not None else "" for cell in rows[0]]
    first = header.index("% Exp/Vot") + 1
    repeat = header.index(header[first], first + 1)
    width = repeat - first
    names = {"Nuance Liste": "nuance", "Libellé Abrégé Liste": "short", "Voix": "votes"}
    layout = [names.get(cell) for cell in header[first:repeat]]
    totals: list[dict[str, object]] = []
    votes: list[dict[str, object]] = []
    for row in rows[1:]:
        if row[0] is None or row[2] is None:
            continue
        code = commune_code(str(row[0]).strip(), str(row[2]).strip())
        entry: dict[str, object] = {
            "year": year,
            "round": round_,
            "commune": code,
            "label": clean(row[3]),
            "registered": None,
            "voters": None,
            "invalid": None,
            "valid": None,
            "blank": None,
            "null": None,
        }
        for field, key in TOTALS.items():
            entry[key] = regionales.number(row[header.index(field)])
        totals.append(entry)
        for offset in range(first, len(row) - width + 1, width):
            block = dict(zip(layout, row[offset : offset + width], strict=False))
            if clean(block.get("votes")) is None:
                continue
            votes.append(
                {
                    "year": year,
                    "round": round_,
                    "commune": code,
                    "nuance": clean(block["nuance"]),
                    "short": clean(block["short"]),
                    "head": None,
                    "votes": regionales.number(block["votes"]),
                }
            )
    return totals, votes


def read_corse(round_: int) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    detail = regionales.sheet(regionales.CORSE_FILES[round_], 2)
    header_at = next(i for i, row in enumerate(detail) if row[0] == "Code du département")
    header = detail[header_at]
    heads = [regionales.corse_head(str(cell)) for cell in detail[header_at - 1] if cell not in (None, "")]
    first = header.index("Exprimés") + 1
    totals: list[dict[str, object]] = []
    votes: list[dict[str, object]] = []
    for row in detail[header_at + 1 :]:
        if row[0] not in ("2A", "2B"):
            continue
        code = commune_code(str(row[0]), str(row[2]).strip())
        totals.append(
            {
                "year": 2017,
                "round": round_,
                "commune": code,
                "label": clean(row[3]),
                "registered": regionales.number(row[header.index("Inscrits")]),
                "voters": regionales.number(row[header.index("Votants")]),
                "invalid": None,
                "valid": regionales.number(row[header.index("Exprimés")]),
                "blank": regionales.number(row[header.index("Blancs")]),
                "null": regionales.number(row[header.index("Nuls")]),
            }
        )
        for index, head in enumerate(heads):
            votes.append(
                {
                    "year": 2017,
                    "round": round_,
                    "commune": code,
                    "nuance": None,
                    "short": None,
                    "head": fold(head),
                    "votes": regionales.number(row[first + 2 * index]),
                }
            )
    return totals, votes


def read_bureaux(year: int, round_: int) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    election = f"{year}_regi_t{round_}"
    general = (
        pl.scan_parquet(raw.cached(CDSP, AGGREGATED["general"]).path)
        .filter(pl.col("id_election") == election)
        .group_by("code_commune")
        .agg(
            pl.col("libelle_commune").first().alias("label"),
            *[pl.col(source).sum().alias(target) for source, target in GENERAL.items()],
            *[pl.col(source).is_not_null().sum().alias(f"{target}_n") for source, target in GENERAL.items()],
        )
        .collect()
    )
    totals = [
        {
            "year": year,
            "round": round_,
            "commune": row["code_commune"],
            "label": row["label"],
            "registered": row["registered"],
            "voters": row["voters"],
            "invalid": None,
            "valid": row["valid"],
            "blank": row["blank"] if row["blank_n"] else None,
            "null": row["null"] if row["null_n"] else None,
        }
        for row in general.iter_rows(named=True)
    ]
    candidates = (
        pl.scan_parquet(raw.cached(CDSP, AGGREGATED["candidates"]).path)
        .filter(pl.col("id_election") == election)
        .group_by("code_commune", "nom", "prenom")
        .agg(pl.col("voix").sum().alias("votes"))
        .collect()
    )
    votes = [
        {
            "year": year,
            "round": round_,
            "commune": row["code_commune"],
            "nuance": None,
            "short": None,
            "head": fold(f"{row['nom']} {row['prenom']}"),
            "votes": row["votes"],
        }
        for row in candidates.iter_rows(named=True)
    ]
    return totals, votes


def parse() -> None:
    totals: list[dict[str, object]] = []
    votes: list[dict[str, object]] = []
    for year in WORKBOOKS:
        for round_ in (1, 2):
            found_totals, found_votes = read_workbook(year, round_)
            totals += found_totals
            votes += found_votes
    for round_ in (1, 2):
        found_totals, found_votes = read_corse(round_)
        totals += found_totals
        votes += found_votes
    for year in BUREAUX:
        for round_ in (1, 2):
            found_totals, found_votes = read_bureaux(year, round_)
            totals += found_totals
            votes += found_votes
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(totals, infer_schema_length=None).write_parquet(PARSED / "totals.parquet")
    pl.DataFrame(votes, infer_schema_length=None).write_parquet(PARSED / "votes.parquet")


def department_of(code: str) -> str:
    return code[:3] if code.startswith(("97", "98")) else code[:2]


def emit() -> Path:
    totals = pl.read_parquet(PARSED / "totals.parquet")
    votes = pl.read_parquet(PARSED / "votes.parquet")
    lists = pl.read_parquet(regionales.PARSED / "lists.parquet").filter(pl.col("level") == "region")
    department_totals = pl.read_parquet(regionales.PARSED / "totals.parquet").filter(pl.col("level") == "departement")
    department_lists = pl.read_parquet(regionales.PARSED / "lists.parquet").filter(pl.col("level") == "departement")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "French regional elections of 2004, 2010, 2015, and 2021 and the Corsican territorial election of 2017, both rounds, by commune, from the Interior "
                "Ministry's results files"
            ),
            polity="FR",
        )
        france = cs.polity("FR")
        sources: dict[int, UUID] = {}
        for year, (url, _) in WORKBOOKS.items():
            sources[year] = cs.source("Ministère de l'Intérieur", f"Élections régionales {year}")
            if sources[year] in cs.minted:
                raise ValueError(f"source for {year} is not in the database")
            cs.artifact(raw.cached(regionales.SOURCE, url), sources[year])
        aggregated = cs.source("data.gouv.fr", AGGREGATED_TITLE)
        if aggregated in cs.minted:
            raise ValueError("aggregated source is not in the database")
        for year in BUREAUX:
            sources[year] = aggregated
        sources[2017] = cs.source("Ministère de l'Intérieur", "Élections territoriales de Corse 2017")
        if sources[2017] in cs.minted:
            raise ValueError("source for 2017 is not in the database")
        for file_url in regionales.CORSE_FILES.values():
            cs.artifact(raw.cached(regionales.SOURCE, file_url), sources[2017])

        known = {
            code
            for (code,) in conn.execute("select code from reporting_unit where scheme_code = 'fr.commune'").fetchall()
        }
        labels = {
            (int(year), str(commune)): label
            for year, commune, label in totals.select("year", "commune", "label").unique().iter_rows()
        }
        units: dict[str, UUID] = {}
        for commune in sorted(totals["commune"].unique()):
            commune = str(commune)
            if commune not in known:
                label = next(
                    labels[year, commune] for year in (2010, 2004, 2015, 2017, 2021) if (year, commune) in labels
                )
                area = cs.area("fr.commune", commune)
                cs.add("area", id=area, scheme_code="fr.commune")
                cs.add("area_code", area_id=area, code=commune, valid_from=None, valid_to=None)
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=cs.area("fr.departement", department_of(commune), date(2024, 1, 1)),
                    valid_from=None,
                    valid_to=None,
                )
                cs.add(
                    "name",
                    entity_id=area,
                    text=label,
                    lang="fr",
                    kind="official",
                    is_preferred=True,
                )
                units[commune] = cs.unit("fr.commune", commune)
                cs.add(
                    "reporting_unit",
                    id=units[commune],
                    polity_id=france,
                    scheme_code="fr.commune",
                    code=commune,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                    valid_to=None,
                )
            else:
                units[commune] = cs.unit("fr.commune", commune)

        unit_ids = {commune: str(unit) for commune, unit in units.items()}
        rows: list[pl.DataFrame] = []
        problems: list[str] = []
        for year in regionales.ROUNDS:
            by_department = regionales.departements(year)
            regions = regionales.OLD_REGIONS if year < 2015 else regionales.NEW_REGIONS
            for round_ in (1, 2):
                held = totals.filter((pl.col("year") == year) & (pl.col("round") == round_))
                if held.is_empty():
                    continue
                held = held.with_columns(
                    department=pl.col("commune").map_elements(department_of, return_dtype=pl.String)
                ).with_columns(
                    region=pl.col("department").map_elements(lambda d: by_department.get(d), return_dtype=pl.String)
                )
                unmapped = held.filter(pl.col("region").is_null())
                if unmapped.height:
                    problems.append(f"{year} round {round_}: {unmapped.height} communes outside the regions")
                    continue
                for code in regions:
                    contest_key = f"{year}:{code}:{round_}"
                    if (
                        conn.execute(
                            "select from external_id where scheme = %s and value = %s",
                            [f"{regionales.SOURCE}.contest", contest_key],
                        ).fetchone()
                        is None
                    ):
                        continue
                    contest = cs.key(f"{regionales.SOURCE}.contest", contest_key)
                    region_set = cs.key(f"{regionales.SOURCE}.result_set", f"{contest_key}:region")
                    election = conn.execute(
                        "select e.id from election e join contest c on c.election_id = e.id where c.id = %s", [contest]
                    ).fetchone()
                    if election is None:
                        raise ValueError(f"{contest_key}: contest not in the database")
                    region_lists = lists.filter(
                        (pl.col("year") == year) & (pl.col("round") == round_) & (pl.col("unit") == code)
                    ).sort("order")
                    options = {
                        (clean(entry["nuance"]), clean(entry["short"])): cs.key(
                            f"{regionales.SOURCE}.option", f"{contest_key}:{entry['order']}"
                        )
                        for entry in region_lists.iter_rows(named=True)
                    }
                    by_head = {
                        fold(entry["head"]): cs.key(f"{regionales.SOURCE}.option", f"{contest_key}:{entry['order']}")
                        for entry in region_lists.iter_rows(named=True)
                    }
                    outcomes = conn.execute(
                        "select option_id, kind, seats from outcome where result_set_id = %s",
                        [region_set],
                    ).fetchall()
                    result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}:commune")
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election[0],
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        status="active",
                        source_id=sources[year],
                        is_default=False,
                        completeness=1,
                    )
                    for option, kind, seats in outcomes:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
                    mine = held.filter(pl.col("region") == code)
                    mine_votes = votes.filter(
                        (pl.col("year") == year)
                        & (pl.col("round") == round_)
                        & pl.col("commune").is_in(mine["commune"].to_list())
                    )
                    accounting = (
                        mine.select(
                            "commune",
                            *[
                                pl.col(field).alias(field)
                                for field in ("registered", "voters", "valid", "invalid", "null", "blank")
                            ],
                        )
                        .unpivot(index="commune", variable_name="field", value_name="value")
                        .drop_nulls("value")
                        .select(
                            result_set_id=pl.lit(str(result_set)),
                            unit_id=pl.col("commune").replace_strict(unit_ids, return_dtype=pl.String),
                            option_id=pl.lit(None, pl.String),
                            measure=pl.col("field").replace_strict(MEASURES, return_dtype=pl.Int16),
                            value=pl.col("value").cast(pl.Int64),
                        )
                    )
                    if year in WORKBOOKS:
                        keyed = mine_votes.with_columns(
                            option_id=pl.struct("nuance", "short").map_elements(
                                lambda s: (
                                    str(options.get((s["nuance"], s["short"])))
                                    if (s["nuance"], s["short"]) in options
                                    else None
                                ),
                                return_dtype=pl.String,
                            )
                        )
                    else:
                        keyed = mine_votes.with_columns(
                            option_id=pl.col("head").map_elements(
                                lambda h: str(by_head[h]) if h in by_head else None, return_dtype=pl.String
                            )
                        )
                    lost = keyed.filter(pl.col("option_id").is_null())
                    if lost.height:
                        problems.append(f"{contest_key}: {lost.height} commune list rows match no option")
                        keyed = keyed.filter(pl.col("option_id").is_not_null())
                    option_rows = keyed.select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id=pl.col("commune").replace_strict(unit_ids, return_dtype=pl.String),
                        option_id="option_id",
                        measure=pl.lit(1, pl.Int16),
                        value=pl.col("votes").cast(pl.Int64),
                    )
                    rows += [accounting, option_rows]
                    departments = sorted(set(mine["department"]))
                    expected = department_totals.filter(
                        (pl.col("year") == year) & (pl.col("round") == round_) & pl.col("unit").is_in(departments)
                    )
                    drift = 0
                    for field in ("registered", "voters", "valid"):
                        drift = max(drift, abs(int(expected[field].sum()) - int(mine[field].sum() or 0)))
                    department_votes = department_lists.filter(
                        (pl.col("year") == year) & (pl.col("round") == round_) & pl.col("unit").is_in(departments)
                    )
                    if year in WORKBOOKS:
                        wanted = department_votes.with_columns(
                            option_id=pl.struct("nuance", "short").map_elements(
                                lambda s: (
                                    str(options.get((clean(s["nuance"]), clean(s["short"]))))
                                    if (clean(s["nuance"]), clean(s["short"])) in options
                                    else None
                                ),
                                return_dtype=pl.String,
                            )
                        )
                    else:
                        wanted = department_votes.with_columns(
                            option_id=pl.col("head").map_elements(
                                lambda h: str(by_head[fold(h)]) if fold(h) in by_head else None, return_dtype=pl.String
                            )
                        )
                    if wanted["option_id"].null_count():
                        problems.append(f"{contest_key}: département lists match no option")
                        continue
                    diff = (
                        wanted.group_by("option_id")
                        .agg(pl.col("votes").sum())
                        .join(
                            keyed.group_by("option_id").agg(pl.col("votes").sum()),
                            on="option_id",
                            how="full",
                            suffix="_c",
                        )
                        .with_columns(delta=(pl.col("votes").fill_null(0) - pl.col("votes_c").fill_null(0)).abs())
                    )
                    drift = max(drift, *diff["delta"].to_list())
                    if drift * 200 > int(mine["valid"].sum() or 1):
                        problems.append(f"{contest_key}: communes and départements differ by up to {drift} votes")
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="methodology",
                        body_md=METHOD if year in WORKBOOKS or year == 2017 else METHOD_BUREAUX,
                    )
                    if drift:
                        cs.add("note", entity_id=result_set, kind="caveat", body_md=DRIFT.format(votes=drift))
        if problems:
            raise ValueError("commune results do not match:\n" + "\n".join(problems))
        cs.frame(
            "result_value",
            pl.concat(rows).with_columns(
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
