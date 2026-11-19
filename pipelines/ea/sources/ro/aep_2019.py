"""The Romanian presidential election of 2019 by polling station, from the results portal of the Electoral Bureau.

Each round is one file per county, with a row for every polling station's signed record, saved from a browser into a
zip, and a legend that names the candidates behind the printed columns g1, g2, and so on, in ballot order. Each
station's row prints the registered voters (a), the voters who took part (b), the valid votes (c), the null votes (d),
the ballots received (e), and the ballots left unused (f). The votes cast by post from abroad come from the portal's separate file for each
round, with a row for each postal bureau and country, and the same candidates in ballot order."""

import csv
import io
import json
import re
import zipfile
from datetime import date

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.ro import aep
from ea.sources.ro.aep import (
    AEP_SOURCE,
    ROMANIA,
    SOURCE,
    key,
    unit_rows,
    values,
)

PREFIX = "browser://20261118-201926-0ee8ff"
FILES = {
    "2019-11-10": f"{PREFIX}/ro-2019-11-10-presidential-r1-polling-stations-pv-csv.zip",
    "2019-11-24": f"{PREFIX}/ro-2019-11-24-presidential-r2-polling-stations-pv-csv.zip",
}
LEGEND = f"{PREFIX}/ro-2019-presidential-column-legend.txt"
POSTAL_HOST = "https://prezenta.bec.ro"
POSTAL_FILES = {
    "2019-11-10": f"{POSTAL_HOST}/prezidentiale10112019/data/pv/csv/pv_SR_PRSD-C_FINAL.csv",
    "2019-11-24": f"{POSTAL_HOST}/prezidentiale24112019/data/pv/csv/pv_SR_PRSD-C_FINAL.csv",
}
DAYS = {"2019-11-10": 1, "2019-11-24": 2}
REGISTRY = "ro.aep/2019-presidential-registry"
PACKAGES = [REGISTRY, *(f"ro.aep/{day}-presidential" for day in DAYS)]
PRESIDENT = key("office", "president")
TWO_ROUND = key("electoral_system", "two-round")
DISPLAY = str.maketrans({"Ş": "Ș", "ş": "ș", "Ţ": "Ț", "ţ": "ț"})
METHOD = (
    "Counted per polling station and published by the Electoral Bureau on its results portal, from each station's "
    "signed record. Registered voters are the voters on the permanent lists, ballots cast are the voters who took "
    "part, valid votes are the votes for candidates, and invalid ballots are the null votes. Stations abroad print no "
    "permanent list, so their registered voters are left empty. Votes cast by post from abroad are counted by "
    "postal bureaus and countries apart from the stations; for them the ballots cast are the ballots counted."
)


def title(text: str) -> str:
    return " ".join(text.translate(DISPLAY).title().split())


def legend(round_number: int) -> dict[str, str]:
    """The candidates behind the printed columns of a round, by column."""
    text = raw.cached(SOURCE, LEGEND).path.read_text(encoding="utf-8")
    marker = "// round 10" if round_number == 1 else "// round 24"
    block = text[text.index(marker) :].split("pt={", 1)[1].split("}", 1)[0]
    found = {f"g{number}": json.loads(f'"{name}"') for number, name in re.findall(r'(\d+):"((?:[^"\\]|\\.)*)"', block)}
    if not found:
        raise ValueError("no candidates in the legend")
    return found


def stations(day: str) -> pl.DataFrame:
    """Every polling station of a round, one row each."""
    rows: list[dict[str, str]] = []
    with zipfile.ZipFile(raw.cached(SOURCE, FILES[day]).path) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith(".csv"):
                text = archive.read(name).decode("utf-8-sig")
                rows.extend(csv.DictReader(io.StringIO(text)))
    names = legend(DAYS[day])
    columns = list(names)
    for row in rows:
        if set(columns) - set(row) or any(c not in names and re.fullmatch(r"g\d+", c) for c in row):
            raise ValueError(f"{day}: the file's candidate columns do not match the legend")
    frame = pl.DataFrame(rows, infer_schema_length=None)
    return frame.select(
        pl.col("Cod birou electoral").cast(pl.Int64).alias("circ"),
        pl.col("Județ").alias("cname"),
        pl.col("Nr").str.strip_chars().alias("nr"),
        pl.concat_str(["Uat", "Localitate", "Secție"], separator=" — ", ignore_nulls=True).alias("place"),
        pl.lit(False).alias("postal"),
        pl.when(pl.col("a").cast(pl.Int64) > 0).then(pl.col("a").cast(pl.Int64)).alias("registered"),
        pl.col("b").cast(pl.Int64).alias("cast"),
        pl.col("c").cast(pl.Int64).alias("valid"),
        pl.col("d").cast(pl.Int64).alias("invalid"),
        pl.lit(None, pl.Int64).alias("blank"),
        *(pl.col(c).cast(pl.Int64) for c in columns),
    )


def postal(day: str) -> pl.DataFrame:
    """The postal bureaus and countries of a round, one row each, with the candidates under the stations' columns."""
    text = raw.cached(SOURCE, POSTAL_FILES[day]).path.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    names = legend(DAYS[day])
    found = [c for c in rows[0] if re.fullmatch(r"e\d+", c)]
    if len(found) != len(names):
        raise ValueError(f"{day}: the postal file's candidate columns do not match the legend")
    frame = pl.DataFrame(rows, infer_schema_length=None)
    return frame.select(
        pl.col("Cod birou electoral").cast(pl.Int64).alias("circ"),
        pl.col("Județ").alias("cname"),
        pl.col("Nr").str.strip_chars().alias("nr"),
        pl.concat_str(["Uat", "Localitate", "Secție"], separator=" — ", ignore_nulls=True).alias("place"),
        pl.lit(True).alias("postal"),
        pl.lit(None, pl.Int64).alias("registered"),
        pl.col("d").cast(pl.Int64).alias("cast"),
        pl.col("d1").cast(pl.Int64).alias("valid"),
        pl.col("d2").cast(pl.Int64).alias("invalid"),
        pl.lit(None, pl.Int64).alias("blank"),
        *(pl.col(f"e{number}").cast(pl.Int64).alias(f"g{number}") for number in range(1, len(names) + 1)),
    )


def person(name: str) -> str:
    return key("person", aep.canon(name))


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The President of Romania, the two-round system, and the candidates of the presidential election of 2019, "
        "from the results portal of the Electoral Bureau",
        depends=["ro.aep/registry"],
    )
    p.external.add(ROMANIA)
    p.artifact(raw.cached(SOURCE, LEGEND), AEP_SOURCE)
    p.add("office", id=PRESIDENT, polity_id=ROMANIA, kind="head_of_state")
    p.name(PRESIDENT, "President of Romania", "en", "official")
    p.name(PRESIDENT, "Președintele României", "ro", "native", False)
    p.add(
        "electoral_system",
        id=TWO_ROUND,
        polity_id=ROMANIA,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", TWO_ROUND),
        entity_id=TWO_ROUND,
        kind="methodology",
        body_md="A candidate with more than half of the valid votes in the first round is elected; otherwise the two "
        "candidates with the most votes face each other in a second round, and the one with more votes is elected.",
        lang="en",
    )
    for name in sorted({*legend(1).values(), *legend(2).values()}):
        who = person(name)
        p.add("person", id=who)
        p.name(who, title(name), "ro", "official")
    return p


def build(day: str) -> Package:
    round_number = DAYS[day]
    runoff = round_number == 2
    first = date.fromisoformat(day)
    first_day = next(d for d, n in DAYS.items() if n == 1)
    p = Package(
        f"ro.aep/{day}-presidential",
        SOURCE,
        f"The Romanian presidential election of {first}"
        + (", second round," if runoff else "")
        + " by polling station, from the results portal of the Electoral Bureau",
        depends=["ro.aep/registry", REGISTRY, *([f"ro.aep/{first_day}-presidential"] if runoff else [])],
    )
    p.external.add(ROMANIA)
    p.artifact(raw.cached(SOURCE, FILES[day]), AEP_SOURCE)
    p.uses(raw.cached(SOURCE, LEGEND))
    p.artifact(raw.cached(SOURCE, POSTAL_FILES[day]), AEP_SOURCE)
    names = legend(round_number)
    frame = unit_rows(p, pl.concat([stations(day), postal(day)], how="vertical_relaxed"), day)
    year = first.year
    election = key("election", f"president-{day}")
    p.add(
        "election",
        id=election,
        polity_id=ROMANIA,
        kind="runoff" if runoff else "general",
        date_from=first,
        date_to=first,
        status="held",
    )
    p.add("slug", path=f"ro/{day}-presidential" + ("-runoff" if runoff else ""), entity_id=election, is_current=True)
    p.name(election, f"{year} Romanian presidential election" + (", second round" if runoff else ""), "en", "official")
    p.name(
        election,
        f"Alegerea Președintelui României din {first.day:02d}.{first.month:02d}.{year}"
        + (", turul al doilea" if runoff else ""),
        "ro",
        "native",
        False,
    )
    contest = key("contest", f"president-{day}")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=PRESIDENT,
        magnitude=1,
        electoral_system_id=TWO_ROUND,
        round_number=round_number,
        previous_round_id=key("contest", f"president-{first_day}") if runoff else None,
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Romanian presidential election" + (", second round" if runoff else ""), "en", "official")
    p.name(
        contest, "Președintele României" + (", turul al doilea" if runoff else "") + f", {year}", "ro", "native", False
    )
    p.add(
        "slug",
        path=f"ro/{day}-presidential" + ("-runoff" if runoff else "") + "/president",
        entity_id=contest,
        is_current=True,
    )
    result_set = key("result_set", f"president-{day}")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=AEP_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    options: dict[str, str] = {}
    for position, (column, name) in enumerate(names.items(), 1):
        option = key("option", f"president-{day}", position)
        options[column] = option
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=title(name),
        )
        p.add(
            "candidacy",
            id=key("candidacy", f"president-{day}", position),
            person_id=person(name),
            option_id=option,
            role="head",
            ballot_name=title(name),
        )
    values(p, result_set, frame, options)
    votes = {column: frame[column].sum() for column in options}
    ranked = sorted(votes, key=lambda column: -votes[column])
    for column in ranked[:1] if runoff else ranked[:2]:
        p.add("outcome", result_set_id=result_set, option_id=options[column], kind="elected" if runoff else "advanced")
    p.accept(
        "accounting.ballots_sum",
        "some stations print valid and null votes that do not add up to the voters who took part; stored as printed",
    )
    p.accept(
        "accounting.turnout",
        "some stations print more voters than registered voters, mostly abroad and on special lists; stored as printed",
    )
    return p


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()
