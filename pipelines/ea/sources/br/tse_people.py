import io
import shutil
import zipfile
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.br import municipais, tse_history
from ea.sources.br.identity import CANDIDACY, TITLES, identities, unambiguous
from ea.store import DATA, connect

SOURCE = "br.tse_people"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
ROSTERS = [
    *((municipais.SOURCE, year, municipais.FILES[(year, "roster")]) for year in municipais.YEARS),
    *((tse_history.SOURCE, year, tse_history.FILES[(year, "roster")]) for year in tse_history.YEARS),
]
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
    for source, year, url in ROSTERS:
        with zipfile.ZipFile(raw.cached(source, url).path) as archive:
            member = next(name for name in archive.namelist() if name.endswith("_BRASIL.csv"))
            with archive.open(member) as file, (folder / member).open("w", encoding="utf-8") as target:
                shutil.copyfileobj(io.TextIOWrapper(file, encoding="latin-1"), target, 16 << 20)
        frame = pl.scan_csv(folder / member, separator=";", infer_schema=False)
        present = frame.collect_schema().names()
        if "NR_TITULO_ELEITORAL_CANDIDATO" not in present:
            continue
        cpf = pl.col("NR_CPF_CANDIDATO") if "NR_CPF_CANDIDATO" in present else pl.lit(None, pl.String)
        frames.append(
            frame.select(
                year=pl.lit(year, pl.Int32),
                place=pl.when(pl.col("SG_UE").str.contains(r"^\d+$"))
                .then(pl.col("SG_UE").str.zfill(5))
                .otherwise(pl.col("SG_UE")),
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
        groups = plan(titles, held)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Brazilian candidates recorded more than once merged by their voter registration and tax numbers",
            polity="BR",
        )
        for group in groups.partition_by("identity", maintain_order=True):
            survivor, *duplicates = [UUID(person) for person in group["person"]]
            for duplicate in duplicates:
                cs.merge(duplicate, survivor)
        return cs.write()
