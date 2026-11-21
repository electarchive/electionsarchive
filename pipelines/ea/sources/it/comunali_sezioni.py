"""Italian municipal elections since 2024 by polling section, with council candidates' preference votes.

The Interior Ministry's open data zips for each round of municipal elections (`it.comunali`) also carry files by
polling section and, from 2026, each council candidate's preference votes by municipality and by section. This module
adds them under the contests, options, and elections `it.comunali` already published: one result set per contest at
the grain of the polling section, and for 2026 the council candidates, their preference votes, and who was elected.
"""

import re
import zipfile
from datetime import date

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.it import comunali, interno, istat, regionali
from ea.sources.it.interno import BLANK, INVALID, REGISTERED, VOTERS, VOTES, proper
from ea.store import DATA

SOURCE = "it.comunali_sezioni"
PARSED = DATA / "parsed" / SOURCE
REFERENCE = DATA / "reference" / "it"
DAYS = [day for day in comunali.DAYS if day.year >= 2024]
FIRST = DAYS[0]
# it.interno published the polling sections of the June 8-9, 2025 referendums as open-ended units from that day on.
SECTIONS_FROM = date(2025, 6, 8)
ITALY = "01a13bf7-2f71-7303-964e-fdd9421ca0b6"
SCHEME = "it.sezione"
PREFERENCES = 7
COLUMNS = {
    **comunali.COLUMNS,
    "registered": (*comunali.COLUMNS["registered"], "ELETTORI_TOTALI"),
    "voters": (*comunali.COLUMNS["voters"], "VOTANTI_TOTALI"),
    "preferences": ("PREFERENZE",),
    "gender": ("SESSO",),
    "date": ("DATAELEZIONE",),
}
NUMBERS = (*comunali.NUMBERS, "preferences")
SECTION_FILE = re.compile(r"(?i)livsez|_sez\.csv|scrutini_\dturno")
PREFERENCE_FILE = re.compile(r"(?i)preferenze_liv(sez|comune)")
TURNOUT_FILE = re.compile(r"(?i)votanti")
ROUND_FILE = re.compile(r"(?i)scrutini_\dturno")
# Files of list votes by section with no round column, which repeat a runoff's finalists, with their runoff votes,
# after or before their first-round rows.
MIXED_FILE = re.compile(r"(?i)listelivsez")
ELECTED = {"E"}
PLACE = ["day", "code"]

SECTION_NOTE = (
    "Results are by polling section, from the Interior Ministry's open data file by section for this round of "
    "municipal elections. The results by municipality are the ones the archive shows by default."
)
PREFERENCE_NOTE = (
    "Each council candidate's preference votes, by municipality, from the Interior Ministry's open data file of "
    "preferences for this round of municipal elections. A voter may mark preferences for candidates of the list they "
    "vote for; preference votes don't add to the list's votes. The elected councillors are the candidates the file "
    "marks as elected; they do not include the mayor or defeated candidates for mayor who took a council seat."
)
SECTION_PREFERENCE_NOTE = (
    " Council candidates' preference votes are given by section too, from the ministry's file of preferences by "
    "section for the region."
)
MISMATCH = (
    "As published, the votes by section here do not add up to the votes the municipal file gives for every option."
)


def url(day: date) -> str:
    return comunali.url(day)


def fetch() -> None:
    for day in DAYS:
        raw.fetch(comunali.SOURCE, url(day), suffix=".zip")
    raw.fetch(interno.SOURCE, interno.ISTAT, suffix=".csv")
    raw.fetch(interno.SOURCE, interno.ISTAT_CURRENT, suffix=".xlsx")
    raw.fetch(istat.SOURCE, istat.VARIATIONS, suffix=".zip")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", f"{SOURCE}/geo", *(f"{SOURCE}/{day}" for day in DAYS)]


def emit() -> None:
    package.run("load", packages())


# Reading


def section_number(column: str) -> pl.Expr:
    return pl.col(column).str.extract(r"(\d+)").cast(pl.Int64).cast(pl.String)


def election_day(text: str) -> date:
    head = text.strip().split(" ")[0]
    if "/" in head:
        day, month, year = (int(part) for part in head.split("/"))
        return date(year, month, day)
    return date.fromisoformat(head)


def read_sections() -> dict[str, pl.DataFrame]:
    """Section rows by kind (accounting, lists, mayors, and the rows of files that mix rounds) and preference rows (by
    municipality and by section), each with the place's ISTAT code. Section rows carry the day of the zip they come
    from, which is the day in `it.comunali`'s keys; preference rows carry the election day they state, since one zip
    holds files by section of the round before it."""
    accounting: list[pl.DataFrame] = []
    lists: list[pl.DataFrame] = []
    mayors: list[pl.DataFrame] = []
    preferences: list[pl.DataFrame] = []
    by_section: list[pl.DataFrame] = []
    mixed: list[pl.DataFrame] = []
    place = ["day", "region", "PROVINCIA", "COMUNE"]
    for day in DAYS:
        archive = zipfile.ZipFile(raw.cached(comunali.SOURCE, url(day)).path)
        members = [m for m in archive.namelist() if not m.endswith("/") and not TURNOUT_FILE.search(m)]
        # When the municipal results themselves come by section, the ministry's extra file of list votes by section
        # repeats them; read the file the municipal results were summed from.
        rounds = any(ROUND_FILE.search(m) for m in members)
        for member in members:
            if PREFERENCE_FILE.search(member):
                frame = comunali.read(day, archive, member, COLUMNS, NUMBERS)
                frame = frame.with_columns(
                    file_day=pl.col("day"),
                    day=pl.col("date").map_elements(election_day, return_dtype=pl.Date),
                    nth=pl.int_range(pl.len()).over(
                        "date", "region", "PROVINCIA", "COMUNE", *(["SEZIONE"] if "SEZIONE" in frame.columns else []),
                        "list", "surname", "forename", "birth",
                    ),
                )  # fmt: skip
                if "SEZIONE" in frame.columns:
                    by_section.append(frame.with_columns(section=section_number("SEZIONE")))
                else:
                    preferences.append(frame)
                continue
            if not SECTION_FILE.search(member) or (rounds and not ROUND_FILE.search(member)):
                continue
            frame = comunali.read(day, archive, member, COLUMNS, NUMBERS).with_columns(
                section=section_number("SEZIONE")
            )
            if frame["section"].null_count():
                raise ValueError(f"{day} {member}: a section without a number")
            if MIXED_FILE.search(member):
                mixed.append(
                    frame.select(
                        *place, "section", "surname", "forename", "list", "list_votes", "candidate_votes"
                    ).with_columns(block=pl.Series(blocks(frame), dtype=pl.Int64))
                )
                continue
            keys = [*place, "section", "round"]
            counts = [c for c in comunali.COUNTS if c in frame.columns]
            if counts:
                rows = frame.select(*keys, *counts).unique()
                if rows.select(keys).is_duplicated().any():
                    raise ValueError(f"{day} {member}: accounting differs within a section")
                accounting.append(rows)
            if "list" in frame.columns:
                rows = frame.filter(pl.col("list") != "", pl.col("round") == 1).with_columns(
                    leader_surname=pl.col("surname") if "surname" in frame.columns else pl.lit(""),
                    leader_forename=pl.col("forename") if "forename" in frame.columns else pl.lit(""),
                )
                rows = rows.with_columns(
                    nth=pl.int_range(pl.len()).over(*keys, "leader_surname", "leader_forename", "list")
                )
                lists.append(rows.select(*keys, "leader_surname", "leader_forename", "list", "nth", "list_votes"))
            if "surname" in frame.columns and "candidate_votes" in frame.columns:
                rows = (
                    frame.filter(pl.col("surname") != "")
                    .select(*keys, "surname", "forename", "candidate_votes")
                    .unique()
                )
                if rows.select(*keys, "surname", "forename").is_duplicated().any():
                    raise ValueError(f"{day} {member}: candidate votes differ within a section")
                mayors.append(rows)
    frames = {
        "accounting": pl.concat(accounting, how="diagonal_relaxed"),
        "lists": pl.concat(lists, how="diagonal_relaxed"),
        "mayors": pl.concat(mayors, how="diagonal_relaxed"),
        "preferences": pl.concat(preferences, how="diagonal_relaxed"),
        "preferences_section": pl.concat(by_section, how="diagonal_relaxed"),
        "mixed": pl.concat(mixed, how="diagonal_relaxed"),
    }
    placed, unplaced = comunali.locate(frames)
    if unplaced.height:
        print(f"places without an ISTAT code, left out: {unplaced.rows()}", flush=True)
    return placed


def blocks(frame: pl.DataFrame) -> list[int]:
    """Number the runs of each candidate's rows within a section: a new run begins when one of its lists comes again.
    The rows of a section need not be next to each other in the file."""
    out: list[int] = []
    lists: dict[tuple[object, ...], set[str]] = {}
    counter: dict[tuple[object, ...], int] = {}
    for row in frame.select("PROVINCIA", "COMUNE", "section", "surname", "forename", "list").iter_rows():
        key = row[:5]
        if key not in counter or row[5] in lists[key]:
            counter[key] = counter.get(key, -1) + 1
            lists[key] = set()
        lists[key].add(row[5])
        out.append(counter[key])
    return out


def unmix(mixed: pl.DataFrame, candidates: pl.DataFrame, report: list[str]) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split the rows of a file that mixes rounds into first-round lists and mayoral votes by round.

    A finalist's first and second runs in a section are its two rounds; which is which is the order whose section
    votes add up to the municipal votes of both rounds for every finalist. A municipality where neither order does, or
    where a candidate without a runoff runs twice in a section, is left out."""
    finalists = candidates.filter(pl.col("round") == 2).select(*PLACE, "surname", "forename").unique()
    totals = candidates.select(*PLACE, "round", "surname", "forename", "candidate_votes")
    votes = mixed.group_by(*PLACE, "section", "surname", "forename", "block").agg(
        pl.col("candidate_votes").first(), pl.col("candidate_votes").n_unique().alias("n")
    )
    marked = votes.join(finalists.with_columns(final=pl.lit(True)), on=[*PLACE, "surname", "forename"], how="left")
    marked = marked.with_columns(pl.col("final").fill_null(False))
    bad = marked.filter((pl.col("n") > 1) | (~pl.col("final") & (pl.col("block") > 0)) | (pl.col("block") > 1))
    sums = (
        marked.filter(pl.col("final"))
        .group_by(*PLACE, "surname", "forename", "block")
        .agg(pl.col("candidate_votes").sum())
    )
    orders: dict[tuple[date, str], int] = {}
    for (day, code), group in sums.group_by(*PLACE):
        assert isinstance(day, date) and isinstance(code, str)
        want = totals.filter(pl.col("day") == day, pl.col("code") == code)
        for order in (0, 1):
            check = group.with_columns(
                round=pl.when(pl.col("block") == order).then(pl.lit(1, pl.Int8)).otherwise(pl.lit(2, pl.Int8))
            ).join(want, on=[*PLACE, "round", "surname", "forename"], how="left", suffix="_declared")
            fits = check.filter(pl.col("candidate_votes") != pl.col("candidate_votes_declared")).height == 0
            if fits and check["candidate_votes_declared"].null_count() == 0 and group["block"].n_unique() == 2:
                orders[(day, code)] = order
                break
    order = pl.DataFrame(
        [(day, code, first) for (day, code), first in orders.items()],
        schema={"day": pl.Date, "code": pl.String, "first": pl.Int64},
        orient="row",
    )
    runoff = finalists.select(*PLACE).unique().join(mixed.select(*PLACE).unique(), on=PLACE, how="semi")
    dropped = pl.concat([bad.select(*PLACE), runoff.join(order.select(*PLACE), on=PLACE, how="anti")]).unique()
    for day, code in dropped.sort(PLACE).iter_rows():
        report.append(f"{day} {code}: the file of votes by section does not split into rounds; left out")
    kept = marked.join(dropped, on=PLACE, how="anti").join(order, on=PLACE, how="left")
    kept = kept.with_columns(
        round=pl.when(~pl.col("final") | (pl.col("block") == pl.col("first")))
        .then(pl.lit(1, pl.Int8))
        .otherwise(pl.lit(2, pl.Int8))
    )
    mayors = kept.select(*PLACE, "section", "round", "surname", "forename", "candidate_votes")
    first = kept.filter(pl.col("round") == 1).select(*PLACE, "section", "surname", "forename", "block")
    lists = (
        mixed.join(first, on=[*PLACE, "section", "surname", "forename", "block"], how="inner")
        .with_columns(round=pl.lit(1, pl.Int8))
        .rename({"surname": "leader_surname", "forename": "leader_forename"})
    )
    lists = lists.with_columns(
        nth=pl.int_range(pl.len()).over(*PLACE, "section", "leader_surname", "leader_forename", "list")
    ).select(
        *PLACE, "region", "PROVINCIA", "COMUNE", "section", "round", "leader_surname", "leader_forename", "list",
        "nth", "list_votes",
    )  # fmt: skip
    return lists, mayors


# Production's rows


def external_ids() -> dict[str, dict[str, str]]:
    """it.comunali's entities from the reference partition, by scheme and source key, for the days loaded here."""
    days = "|".join(str(day) for day in DAYS)
    frame = (
        pl.scan_parquet(REFERENCE / "external_id.parquet")
        .filter(
            pl.col("pipeline") == comunali.SOURCE,
            pl.col("scheme").str.starts_with(f"{comunali.SOURCE}."),
            pl.col("value").str.contains(f"^({days}):"),
        )
        .select("scheme", "value", "entity_id")
        .collect()
    )
    found: dict[str, dict[str, str]] = {}
    for scheme, value, entity in frame.iter_rows():
        found.setdefault(scheme.removeprefix(f"{comunali.SOURCE}."), {})[value] = entity
    return found


def leader(surname: str | None, forename: str | None, votes: int | None) -> str:
    return " ".join(str(part or "") for part in (surname, forename, votes)).strip()


def council_options(lists: pl.DataFrame, ids: dict[str, str]) -> pl.DataFrame:
    """Each municipal list row with the id of the option `it.comunali` made for it.

    A list whose name the ministry has since changed in its file (a few `#NOME?` cells in 2024) is matched to the one
    option of its contest no current row claims, when there is exactly one."""
    rows = lists.with_columns(
        key=pl.struct(
            "day", "code", "leader_surname", "leader_forename", "leader_candidate_votes", "list", "nth"
        ).map_elements(
            lambda r: (
                f"{r['day']}:{r['code']}:list:"
                f"{leader(r['leader_surname'], r['leader_forename'], r['leader_candidate_votes'])}:"
                f"{r['list']}:{r['nth']}"
            ),
            return_dtype=pl.String,
        )
    ).with_columns(option_id=pl.col("key").replace_strict(ids, default=None))
    claimed = set(rows["key"])
    spare: dict[str, list[str]] = {}
    for value, entity in ids.items():
        if value not in claimed and ":list:" in value:
            day, code, *_ = value.split(":")
            spare.setdefault(f"{day}:{code}", []).append(entity)
    missing = rows.filter(pl.col("option_id").is_null())
    fixes: dict[str, str] = {}
    for (day, code), group in missing.group_by("day", "code"):
        left = spare.get(f"{day}:{code}", [])
        if group.height == 1 and len(left) == 1:
            fixes[group["key"][0]] = left[0]
    return rows.with_columns(
        option_id=pl.coalesce("option_id", pl.col("key").replace_strict(fixes, default=None))
    ).drop("key")


def mayor_options(candidates: pl.DataFrame, ids: dict[str, str]) -> pl.DataFrame:
    """Each mayoral candidate row with the id of the option `it.comunali` made for it, in the order it numbered
    namesakes."""
    rows = candidates.sort(
        "day", "code", "round", "candidate_votes", "surname", "forename", descending=[False] * 3 + [True, False, False]
    ).with_columns(nth=pl.int_range(pl.len()).over("day", "code", "round", "surname", "forename"))
    return rows.with_columns(
        option_id=pl.concat_str(
            pl.col("day").cast(pl.String),
            pl.lit(":"),
            "code",
            pl.lit(":mayor:"),
            pl.col("round").cast(pl.String),
            pl.lit(":"),
            pl.concat_str("surname", pl.lit(" "), "forename").str.strip_chars(),
            pl.lit(":"),
            pl.col("nth").cast(pl.String),
        ).replace_strict(ids, default=None)
    )


def matched(sections: pl.DataFrame, municipal: pl.DataFrame) -> pl.DataFrame:
    """Section list rows with the option id of their municipal list.

    Lists pair on their leader, name, and rank among namesakes; then, for those still unpaired, on name and rank alone;
    then on leader and votes, where the section votes add up to the municipal ones. Each step pairs only rows that are
    unique on both sides. A municipality with a list left unpaired is marked `unpaired`."""
    keys = ["leader_surname", "leader_forename", "list", "nth"]
    left = sections.group_by(*PLACE, *keys).agg(pl.col("list_votes").sum().alias("votes"))
    right = municipal.select(
        *PLACE,
        pl.col("leader_surname").fill_null(""),
        pl.col("leader_forename").fill_null(""),
        "list",
        "nth",
        "option_id",
        pl.col("list_votes").alias("votes"),
        pl.col("list_votes").alias("declared"),
    ).filter(pl.col("option_id").is_not_null())
    found: list[pl.DataFrame] = []
    for on in (keys, ["list", "nth"], ["leader_surname", "leader_forename", "votes"]):
        a = left.filter(pl.len().over(*PLACE, *on) == 1)
        b = right.filter(pl.len().over(*PLACE, *on) == 1).select(*PLACE, *on, "option_id", "declared")
        pair = a.join(b, on=[*PLACE, *on], how="inner")
        found.append(pair.select(*PLACE, *keys, "option_id", pl.col("votes").alias("summed"), "declared"))
        left = left.join(pair.select(*PLACE, *keys), on=[*PLACE, *keys], how="anti")
        right = right.join(pair.select(*PLACE, "option_id"), on=[*PLACE, "option_id"], how="anti")
    unpaired = left.select(*PLACE).unique().with_columns(unpaired=pl.lit(True))
    return (
        sections.join(pl.concat(found), on=[*PLACE, *keys], how="left")
        .join(unpaired, on=PLACE, how="left")
        .with_columns(pl.col("unpaired").fill_null(False))
    )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    municipal, _ = comunali.collect(DAYS)
    ids = external_ids()
    lists = council_options(municipal["lists"], ids["option"])
    candidates = mayor_options(municipal["candidates"], ids["option"])
    sections = read_sections()
    report: list[str] = []
    split_lists, split_mayors = unmix(sections["mixed"], municipal["candidates"], report)
    sections["lists"] = pl.concat([sections["lists"], split_lists], how="diagonal_relaxed")
    sections["mayors"] = pl.concat([sections["mayors"], split_mayors], how="diagonal_relaxed")
    council = sections["lists"].join(
        lists.select(*PLACE).unique().with_columns(held=pl.lit(True)), on=PLACE, how="left"
    )
    council = matched(council.filter(pl.col("held").fill_null(False)), lists)
    single = candidates.filter(pl.len().over(*PLACE, "round", "surname", "forename") == 1)
    mayors = sections["mayors"].join(
        single.select(*PLACE, "round", "surname", "forename", "option_id", "candidate_votes").rename(
            {"candidate_votes": "declared"}
        ),
        on=[*PLACE, "round", "surname", "forename"],
        how="left",
    )
    lists.write_parquet(PARSED / "lists.parquet")
    candidates.write_parquet(PARSED / "candidates.parquet")
    council.write_parquet(PARSED / "council.parquet")
    mayors.write_parquet(PARSED / "mayors.parquet")
    for stem in ("accounting", "preferences", "preferences_section"):
        sections[stem].write_parquet(PARSED / f"{stem}.parquet")
    (PARSED / "unmixed.txt").write_text("\n".join(report) + "\n")
    build(ids)


# Building


def registry() -> tuple[Package, str]:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Interior Ministry's open data on Italian municipal elections by polling section and preference vote",
    )
    source = p.id("source", "eligendo-comunali")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Ministero dell'Interno",
        title="Eligendo: open data delle elezioni comunali, per sezione e preferenze",
        url=comunali.ARCHIVE,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Ministero dell'Interno, Dipartimento per gli Affari Interni e Territoriali",
    )
    return p, source


def comune_areas() -> dict[str, list[tuple[str, date | None, date | None]]]:
    areas = pl.scan_parquet(REFERENCE / "area.parquet").filter(pl.col("scheme_code") == "it.comune").select("id")
    codes = (
        pl.scan_parquet(REFERENCE / "area_code.parquet")
        .join(areas, left_on="area_id", right_on="id")
        .select("code", "area_id", "valid_from", "valid_to")
        .collect()
    )
    found: dict[str, list[tuple[str, date | None, date | None]]] = {}
    for code, area, start, end in codes.iter_rows():
        found.setdefault(code, []).append((area, start, end))
    return found


def area_on(areas: dict[str, list[tuple[str, date | None, date | None]]], code: str, day: date) -> str | None:
    hits = [a for a, start, end in areas.get(code, []) if (start or date.min) <= day < (end or date.max)]
    return hits[0] if len(hits) == 1 else None


def comune_names() -> dict[str, str]:
    names = {code: row[5] for code, row in interno.comuni().items()}
    _, extinct, _ = regionali.municipalities()
    for code, label, _, _ in extinct.iter_rows():
        names.setdefault(code, label)
    return names


def units(seen: pl.DataFrame) -> tuple[Package, dict[tuple[str, str, date], str], set[str]]:
    """The geo package: a polling-section unit for each section a file names that production lacks. Sections before
    June 8, 2025 get units of their own, valid from the first election here to that day; later ones reuse it.interno's
    unit for the same code, and get a new open-ended one only where it has none."""
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Polling sections of Italian municipal elections from 2024 on, from the Interior Ministry's open data",
    )
    p.external |= {ITALY, SCHEME}
    held = dict(
        pl.scan_parquet(REFERENCE / "reporting_unit.parquet")
        .filter(pl.col("scheme_code") == SCHEME, pl.col("valid_from") == SECTIONS_FROM, pl.col("valid_to").is_null())
        .select("code", "id")
        .collect()
        .iter_rows()
    )
    areas = comune_areas()
    names = comune_names()
    found: dict[tuple[str, str, date], str] = {}
    external: set[str] = set()
    first_late: dict[str, date] = {}
    rows = seen.select("day", "code", "section").unique().sort("day", "code", "section")
    for day, code, section in rows.iter_rows():
        unit_code = f"{code}/{section}"
        if day >= SECTIONS_FROM and unit_code in held:
            found[(code, section, day)] = held[unit_code]
            external.add(held[unit_code])
            continue
        if day >= SECTIONS_FROM:
            first_late.setdefault(unit_code, day)
    minted: dict[tuple[str, date | None], str] = {}
    left_out = 0
    for day, code, section in rows.iter_rows():
        unit_code = f"{code}/{section}"
        if (code, section, day) in found:
            continue
        start, end = (FIRST, SECTIONS_FROM) if day < SECTIONS_FROM else (first_late[unit_code], None)
        if (unit_code, start) not in minted:
            area = area_on(areas, code, day)
            if area is None:
                left_out += 1
                continue
            unit = p.id("reporting_unit", SCHEME, unit_code, start)
            p.external.add(area)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=ITALY,
                scheme_code=SCHEME,
                code=unit_code,
                area_relation="within",
                area_id=area,
                kind="standard",
                valid_from=start,
                valid_to=end,
            )
            p.name(unit, f"{names.get(code, code)}, sezione {section}", "it", "official")
            minted[(unit_code, start)] = unit
        found[(code, section, day)] = minted[(unit_code, start)]
    if left_out:
        print(f"sections left out for want of a municipal area: {left_out}", flush=True)
    return p, found, external


def comune_units() -> dict[str, list[tuple[str, date | None, date | None]]]:
    rows = (
        pl.scan_parquet(REFERENCE / "reporting_unit.parquet")
        .filter(pl.col("scheme_code") == "it.comune")
        .select("code", "id", "valid_from", "valid_to")
        .collect()
    )
    found: dict[str, list[tuple[str, date | None, date | None]]] = {}
    for code, unit, start, end in rows.iter_rows():
        found.setdefault(code, []).append((unit, start, end))
    return found


def build(ids: dict[str, dict[str, str]]) -> None:
    lists = pl.read_parquet(PARSED / "lists.parquet")
    council = pl.read_parquet(PARSED / "council.parquet")
    mayors = pl.read_parquet(PARSED / "mayors.parquet")
    accounting = pl.read_parquet(PARSED / "accounting.parquet")
    preferences = pl.read_parquet(PARSED / "preferences.parquet")
    by_section = pl.read_parquet(PARSED / "preferences_section.parquet")
    reg, source = registry()
    reg.write()
    seen = pl.concat(
        [f.select("day", "code", "section") for f in (council, mayors, accounting, by_section)], how="vertical_relaxed"
    )
    geo, unit_ids, interno_units = units(seen)
    geo.write()
    contests, elections, sets = ids["contest"], ids["election"], ids["result_set"]
    municipal_units = comune_units()

    def comune_unit(code: str, day: date) -> str | None:
        return area_on(municipal_units, code, day)

    report: list[str] = []
    for day in DAYS:
        p = Package(
            f"{SOURCE}/{day}",
            SOURCE,
            f"Italian municipal elections of {day:%B} {day.day}, {day.year} by polling section"
            + (", with council candidates' preference votes" if day.year >= 2026 else "")
            + ", from the Interior Ministry's open data",
            depends=[f"{SOURCE}/registry", f"{SOURCE}/geo"],
        )
        p.artifact(raw.cached(comunali.SOURCE, url(day)), source)
        extra = sorted({d for d in by_section.filter(pl.col("day") == day)["file_day"].unique()} - {day})
        for other in extra:
            p.artifact(raw.cached(comunali.SOURCE, url(other)), source)
        values: list[dict[str, object]] = []
        council_sets: dict[str, str] = {}

        def unit_of(code: str, section: str) -> str | None:
            unit = unit_ids.get((code, section, day))
            if unit is not None and unit in interno_units:
                p.external.add(unit)
            return unit

        def result_set(key: str, election: str, contest: str | None, default: bool, notes: list[str]) -> str:
            id = p.id("result_set", key)
            p.external.add(election)
            if contest is not None:
                p.external.add(contest)
            p.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=default,
                completeness=1.0,
            )
            p.add("note", id=p.id("note", key), entity_id=id, kind="context", body_md=" ".join(notes), lang="en")
            return id

        def put(result: str, unit: str, option: str | None, measure: int, count: int | None) -> None:
            if count is not None:
                values.append(
                    {"result_set_id": result, "unit_id": unit, "option_id": option, "measure": measure, "value": count}
                )

        # Accounting: one set per municipal election round, by section.
        for (code, round_), rows in accounting.filter(pl.col("day") == day).group_by("code", "round"):
            assert isinstance(code, str) and isinstance(round_, int)
            election = elections.get(f"{day}:{code}:{round_}")
            if election is None:
                report.append(f"{day} {code} round {round_}: accounting without an election")
                continue
            have = f"{day}:{code}:{round_}:election" in sets
            result = result_set(f"{day}:{code}:{round_}:election", election, None, not have, [SECTION_NOTE])
            for row in rows.iter_rows(named=True):
                unit = unit_of(code, row["section"])
                if unit is None:
                    continue
                for measure, column in (
                    (REGISTERED, "registered"),
                    (VOTERS, "voters"),
                    (BLANK, "blank"),
                    (INVALID, "null"),
                ):
                    put(result, unit, None, measure, row.get(column))
        # Council lists, and in 2026 the preference votes, by section.
        prefs_day = preferences.filter(pl.col("day") == day)
        sections_day = by_section.filter(pl.col("day") == day)
        for (code,), rows in council.filter(pl.col("day") == day).group_by("code"):
            assert isinstance(code, str)
            contest = contests.get(f"{day}:{code}:council")
            election = elections.get(f"{day}:{code}:1")
            if contest is None or election is None or rows["unpaired"].any() or rows["option_id"].null_count():
                report.append(f"{day} {code} council: lists by section not matched to the municipal lists")
                continue
            sums = rows.select("option_id", "summed", "declared").unique()
            differs = sums.filter(pl.col("summed") != pl.col("declared")).height > 0
            notes = [SECTION_NOTE]
            if sections_day.filter(pl.col("code") == code).height:
                notes.append(SECTION_PREFERENCE_NOTE)
            if differs:
                notes.append(MISMATCH)
                report.append(f"{day} {code} council: section sums differ from the municipal list votes")
            result = result_set(f"{day}:{code}:council:sections", election, contest, False, notes)
            for row in rows.iter_rows(named=True):
                unit = unit_of(code, row["section"])
                if unit is None:
                    continue
                p.external.add(row["option_id"])
                put(result, unit, row["option_id"], VOTES, row["list_votes"])
            council_sets[code] = result
        # Mayoral candidates by section, each round.
        for (code, round_), rows in mayors.filter(pl.col("day") == day).group_by("code", "round"):
            assert isinstance(code, str) and isinstance(round_, int)
            contest = contests.get(f"{day}:{code}:mayor:{round_}")
            election = elections.get(f"{day}:{code}:{round_}")
            if contest is None or election is None or rows["option_id"].null_count():
                report.append(f"{day} {code} mayor round {round_}: candidates by section not matched")
                continue
            sums = rows.group_by("option_id").agg(pl.col("candidate_votes").sum(), pl.col("declared").first())
            notes = [SECTION_NOTE]
            if sums.filter(pl.col("candidate_votes") != pl.col("declared")).height:
                notes.append(MISMATCH)
                report.append(f"{day} {code} mayor round {round_}: section sums differ from the municipal votes")
            result = result_set(f"{day}:{code}:mayor:{round_}:sections", election, contest, False, notes)
            for row in rows.iter_rows(named=True):
                unit = unit_of(code, row["section"])
                if unit is None:
                    continue
                p.external.add(row["option_id"])
                put(result, unit, row["option_id"], VOTES, row["candidate_votes"])
        # Council candidates and their preference votes (2026 files only).
        council_lists = lists.filter(pl.col("day") == day, pl.col("option_id").is_not_null())
        parents = (
            council_lists.group_by("code", "list")
            .agg(pl.col("option_id").first().alias("parent"), pl.len().alias("n"))
            .filter(pl.col("n") == 1)
            .drop("n")
        )
        who = ["list", "surname", "forename", "birth", "nth"]
        # A list the preference file names differently from the municipal file (one in 2026) is paired with the one
        # list of its municipality that no label claims, when there is exactly one of each.
        labels = prefs_day.select("code", "list").unique()
        odd = labels.join(parents, on=["code", "list"], how="anti").filter(pl.len().over("code") == 1)
        free = parents.join(labels, on=["code", "list"], how="anti").filter(pl.len().over("code") == 1)
        parents = pl.concat([parents, odd.join(free.drop("list"), on="code", how="inner")])
        running = prefs_day.join(parents, on=["code", "list"], how="left")
        for code, label in running.filter(pl.col("parent").is_null()).select("code", "list").unique().iter_rows():
            report.append(f"{day} {code}: preferences for list {label!r}, which has no single option")
        running = running.filter(pl.col("parent").is_not_null()).with_columns(
            key=pl.concat_str(pl.lit(str(day)), "code", *who, separator="\x1f", ignore_nulls=True)
        )
        candidates: dict[tuple[str, ...], str] = {}
        for (code,), rows in running.group_by("code"):
            assert isinstance(code, str)
            contest = contests.get(f"{day}:{code}:council")
            election = elections.get(f"{day}:{code}:1")
            unit = comune_unit(code, day)
            if contest is None or election is None or unit is None:
                report.append(f"{day} {code}: preferences without a council contest or municipal unit")
                continue
            result = result_set(f"{day}:{code}:council:preferences", election, contest, False, [PREFERENCE_NOTE])
            p.external.add(unit)
            for row in rows.sort(*who).iter_rows(named=True):
                parts = (day, code, *(row[c] for c in who))
                p.external.add(row["parent"])
                ballot = f"{row['surname']} {row['forename']}".strip()
                option = p.id("option", *parts)
                person = p.id("person", *parts)
                candidacy = p.id("candidacy", *parts)
                born = comunali.birth(row["birth"]) if row["birth"] else None
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    parent_option_id=row["parent"],
                    ballot_label=ballot,
                )
                p.add(
                    "person",
                    id=person,
                    birth_date=born,
                    birth_precision="day" if born is not None else None,
                    gender={"M": "male", "F": "female"}.get(row.get("gender") or ""),
                )
                p.name(person, f"{proper(row['forename'])} {proper(row['surname'])}".strip(), "it", "official")
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=row["parent"],
                    role="list_member",
                    ballot_name=ballot,
                )
                put(result, unit, option, PREFERENCES, row["preferences"])
                if row["elected"] in ELECTED:
                    p.add(
                        "outcome", result_set_id=result, option_id=row["parent"], candidacy_id=candidacy, kind="elected"
                    )
                candidates[(code, *(str(row[c]) for c in who))] = option
        placed = 0
        for row in sections_day.iter_rows(named=True):
            section_set = council_sets.get(row["code"])
            option = candidates.get((row["code"], *(str(row[c]) for c in who)))
            if section_set is None or option is None:
                continue
            unit = unit_of(row["code"], row["section"])
            if unit is not None:
                put(section_set, unit, option, PREFERENCES, row["preferences"])
                placed += 1
        if sections_day.height and placed < sections_day.height:
            report.append(f"{day}: {sections_day.height - placed} preference rows by section not placed")
        counts: dict[tuple[object, object], dict[int, int]] = {}
        for v in values:
            measure, count = v["measure"], v["value"]
            if v["option_id"] is None and isinstance(measure, int) and isinstance(count, int):
                counts.setdefault((v["result_set_id"], v["unit_id"]), {})[measure] = count
        over = [c for c in counts.values() if REGISTERED in c and VOTERS in c and c[VOTERS] > c[REGISTERED]]
        if over:
            p.accept(
                "accounting.turnout",
                f"As published: {len(over)} polling sections report more voters than registered electors; "
                f"{sum(c[REGISTERED] == 0 for c in over)} of them report no registered electors, as special sections "
                "whose voters are registered in other sections do.",
            )
        if values:
            p.frame(
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
                ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
            )
        p.write()
    (PARSED / "report.txt").write_text("\n".join(report) + "\n")
