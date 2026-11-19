import io
import shutil
import zipfile
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.br import municipais, tse_history, tse_results
from ea.sources.br.identity import CANDIDACY, TITLES, identities, unambiguous
from ea.store import DATA, connect

SOURCE = "br.tse_people"
VERSION = "3"
PARSED = DATA / "parsed" / SOURCE
RESULTS = PARSED / "results.parquet"
ROSTERS = [(year, tse_results.munzona("consulta_cand", year)) for year in tse_results.YEARS]
MOST = 200_000
DIGITS = r"^0*[1-9][0-9]*$"
MISREAD = (2002, 2006)


def valid(title: str) -> bool:
    digits = title.zfill(12)
    state = int(digits[8:10])
    special = state in (1, 2)
    first = sum(int(digit) * weight for digit, weight in zip(digits[:8], range(2, 10), strict=True)) % 11
    first = 0 if first == 10 else 1 if first == 0 and special else first
    second = (int(digits[8]) * 7 + int(digits[9]) * 8 + first * 9) % 11
    second = 0 if second == 10 else 1 if second == 0 and special else second
    return len(digits) == 12 and 1 <= state <= 28 and digits[10:] == f"{first}{second}"


def parse() -> None:
    folder = PARSED / "tmp"
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    results = []
    for year, url in ROSTERS:
        with zipfile.ZipFile(raw.cached(tse_results.SOURCE, url).path) as archive:
            member = next(name for name in archive.namelist() if name.endswith("_BRASIL.csv"))
            with archive.open(member) as file, (folder / member).open("w", encoding="utf-8") as target:
                shutil.copyfileobj(io.TextIOWrapper(file, encoding="latin-1"), target, 16 << 20)
        frame = pl.scan_csv(folder / member, separator=";", infer_schema=False)
        present = frame.collect_schema().names()
        place = (
            pl.when(pl.col("SG_UE").str.contains(r"^\d+$"))
            .then(pl.col("SG_UE").str.zfill(5))
            .otherwise(pl.col("SG_UE"))
        )
        office = pl.col("CD_CARGO").cast(pl.Int64)
        results.append(
            frame.filter(pl.col("SQ_CANDIDATO").is_not_null())
            .select(
                year=pl.lit(year, pl.Int32),
                place=place,
                candidate=pl.col("SQ_CANDIDATO"),
                contest=pl.format(
                    "{}:{}:{}",
                    pl.col("CD_ELEICAO").cast(pl.Int64),
                    office,
                    pl.when(office == 1)
                    .then(pl.lit("BR"))
                    .when(office.is_in(tse_results.STATE_OFFICES))
                    .then(pl.col("SG_UF"))
                    .otherwise(pl.col("SG_UE").cast(pl.Int64, strict=False).cast(pl.String)),
                ),
                number=pl.col("NR_CANDIDATO"),
            )
            .unique()
            .collect()
        )
        if "NR_TITULO_ELEITORAL_CANDIDATO" not in present:
            continue
        cpf = pl.col("NR_CPF_CANDIDATO") if "NR_CPF_CANDIDATO" in present else pl.lit(None, pl.String)
        frames.append(
            frame.select(
                year=pl.lit(year, pl.Int32),
                place=place,
                candidate=pl.col("SQ_CANDIDATO"),
                title=pl.col("NR_TITULO_ELEITORAL_CANDIDATO").str.strip_chars(),
                cpf=cpf.str.strip_chars(),
                name=pl.col("NM_CANDIDATO").str.strip_chars(),
                birth=pl.col("DT_NASCIMENTO").str.to_date("%d/%m/%Y", strict=False),
            )
            .filter(pl.col("title").str.contains(DIGITS))
            .with_columns(
                title=pl.col("title").str.strip_chars_start("0"),
                cpf=pl.when(pl.col("cpf").str.contains(DIGITS)).then(pl.col("cpf").str.strip_chars_start("0")),
                name=pl.col("name").map_elements(municipais.slug, return_dtype=pl.String).replace("", None),
            )
            .unique()
            .collect()
        )
        (folder / member).unlink()
    titles = pl.concat(frames).unique()
    checked = titles.select("title").unique().filter(pl.col("title").map_elements(valid, return_dtype=pl.Boolean))
    print(f"titles {titles['title'].n_unique()}, passing their check digits {checked.height}")
    titles.join(checked, on="title").write_parquet(TITLES)
    pl.concat(results).unique().select(
        *CANDIDACY,
        candidacy=pl.struct("contest", "number").map_elements(
            lambda row: tse_results.key("candidacy", row["contest"], row["number"]), return_dtype=pl.String
        ),
    ).write_parquet(RESULTS)


def plan(titles: pl.DataFrame, held: pl.DataFrame) -> pl.DataFrame:
    """Group the persons of the held candidacies by identity, survivor first, and write what the merge skips."""
    candidacies = titles.select(CANDIDACY).unique().height
    titles = unambiguous(titles)
    ambiguous = candidacies - titles.select(CANDIDACY).unique().height
    joined = held.join(titles.unique(CANDIDACY, keep="first", maintain_order=True), on=CANDIDACY).join(
        identities(titles), on=CANDIDACY
    )
    people = joined.group_by("title").agg(
        identities=pl.col("identity").n_unique(),
        cpfs=pl.col("cpf").drop_nulls().n_unique(),
        years=pl.col("year").n_unique(),
        candidacies=pl.struct(CANDIDACY).n_unique(),
    )
    merging = joined.group_by("identity").agg(pl.col("person").n_unique()).filter(pl.col("person") > 1)
    split = (
        joined.group_by("person")
        .agg(pl.col("identity").n_unique(), titles=pl.col("title").n_unique())
        .filter(pl.col("identity") > 1)
    )
    skipped = people.filter(pl.col("identities") > 1)
    print(
        f"titles {people.height}, identities merging {merging.height}, "
        f"titles split between identities {skipped.height}, "
        f"of which with several tax numbers {skipped.filter(pl.col('cpfs') > 1).height}, "
        f"persons in several identities {split.height}, candidacies with two titles {ambiguous}"
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    split.write_parquet(PARSED / "split.parquet")
    skipped.write_parquet(PARSED / "skipped.parquet")
    groups = (
        joined.join(merging.select("identity"), on="identity")
        .filter(~pl.col("person").is_in(split["person"].implode()))
        .group_by("identity", "person")
        .agg(pl.col("misread").all(), pl.col("rank").min(), pl.col("birth").first(), first=pl.col("year").min())
        .sort("identity", "misread", "rank", "first", "person")
    )
    births = (
        groups.filter(~pl.col("misread"))
        .group_by("identity")
        .agg(pl.col("birth").drop_nulls().n_unique())
        .filter(pl.col("birth") > 1)
    )
    print(f"merged identities whose persons' birth dates differ {births.height}")
    births.write_parquet(PARSED / "births.parquet")
    return groups


def emit() -> Path:
    titles = pl.read_parquet(TITLES)
    with connect() as conn:
        held = pl.DataFrame(
            conn.execute(
                """
                with ranks as (
                    select entity_id, min(case scheme when 'br.tse.person' then 0
                        when 'br.tse_history.person' then 1 else 2 end) as rank
                    from external_id where scheme in ('br.tse.person', 'br.tse_history.person', 'br.municipais.person')
                    group by entity_id
                )
                select x.value, s.pipeline, c.person_id::text, coalesce(r.rank, 3), p.birth_date
                from external_id x
                join candidacy c on c.id = x.entity_id
                join person p on p.id = c.person_id
                join changeset s on s.id = x.changeset_id
                left join ranks r on r.entity_id = c.person_id
                where x.scheme = 'br.candidacy' and s.pipeline in ('br.municipais', 'br.tse_history', 'br.tse')
                """
            ).fetchall(),
            schema={"key": pl.String, "pipeline": pl.String, "person": pl.String, "rank": pl.Int32, "birth": pl.Date},
            orient="row",
        ).select(
            year=pl.col("key").str.slice(0, 4).cast(pl.Int32),
            place=pl.when(pl.col("key").str.count_matches(":") == 3)
            .then(pl.col("key").str.split(":").list.get(2))
            .otherwise(pl.lit("BR")),
            candidate=pl.col("key").str.extract(r":(\d+)$"),
            person="person",
            rank="rank",
            birth="birth",
            misread=(pl.col("pipeline") == tse_history.SOURCE)
            & pl.col("key").str.slice(0, 4).cast(pl.Int32).is_in(MISREAD),
        )
        results = pl.read_parquet(RESULTS)
        found = pl.DataFrame(
            conn.execute(
                """select c.id::text, c.person_id::text, p.birth_date from candidacy c
                   join changeset s on s.id = c.changeset_id join person p on p.id = c.person_id
                   where s.pipeline = %s""",
                [tse_results.SOURCE],
            ).fetchall(),
            schema={"candidacy": pl.String, "person": pl.String, "birth": pl.Date},
            orient="row",
        )
        placed = results.join(found, on="candidacy").select(
            *CANDIDACY, "person", rank=pl.lit(4, pl.Int32), birth="birth", misread=pl.lit(False)
        )
        print(f"{tse_results.SOURCE} candidacies {found.height}, placed on a roster row {placed['person'].n_unique()}")
        held = pl.concat([held, placed])
        groups = plan(titles, held)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Brazilian candidates recorded more than once, including once per race by the electoral court's "
                "results files, merged by their voter registration and tax numbers"
            ),
            polity="BR",
        )
        merged = 0
        for group in groups.partition_by("identity", maintain_order=True):
            if merged + group.height - 1 > MOST:
                break
            survivor, *duplicates = [UUID(person) for person in group["person"]]
            for duplicate in duplicates:
                cs.merge(duplicate, survivor)
            merged += len(duplicates)
        print(f"merged {merged} of {groups.height - groups['identity'].n_unique()}")
        return cs.write()
