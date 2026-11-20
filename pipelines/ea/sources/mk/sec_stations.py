"""North Macedonia: polling-station results of the 2016 and 2024 Assembly elections and the 2024 presidential runoff.

The State Election Commission counts every vote at its polling station and publishes the counts on its results
service, which does not answer outside North Macedonia. An academic compilation of the Commission's published
returns (Noah Dasanaike, Harvard, version 1.4) carries them for three elections, and this module reads that file:

- the Assembly election of 11 December 2016, in the six electoral units at home (the compilation leaves out the
  units abroad);
- the Assembly election of 8 May 2024, in the six units and at the polling stations abroad;
- the second round of the presidential election of 7 May 2024, nationwide and abroad.

Each polling station becomes a reporting unit, and each election gets a result set of its own, never the default,
attached to the contests and options that ``mk.sec`` and ``mk.sec_president`` already hold. The Commission's decisions
stay the default. The compilation files a station under an electoral unit by its own reckoning, and in the split
municipalities its unit differs from the Commission's, so each unit's sums differ from the decision's; the notes on
each result set give the differences.
"""

import io
import zipfile
from collections import defaultdict
from datetime import date
from typing import cast
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.mk import sec, sec_president

SOURCE = "mk.sec_stations"
FILE = "https://storage.googleapis.com/sage-archive/csv/North%20Macedonia/SAGE_North%20Macedonia_all_years_csv.zip"
MACEDONIA = sec.MACEDONIA
COUNTRY = sec.COUNTRY
VOTES, REGISTERED, VALID, INVALID = sec.VOTES, sec.REGISTERED, sec.VALID, sec.INVALID

PARL_2016 = date(2016, 12, 11)
PARL_2024 = date(2024, 5, 8)
RUNOFF_2024 = date(2024, 5, 7)
RUNOFF_TAG = "2024-05-07"

# The compilation's English names for the 2016 lists, with the list's number on the ballot.
LISTS_2016 = {
    "For a Better Macedonia": "1",
    "Democratic Party of Albanians": "2",
    "Democratic Union for Integration": "3",
    "For Life in Macedonia": "4",
    "Coalition for Change and Justice - Third Bloc": "5",
    "Party for Democratic Prosperity": "6",
    "Alliance for Albanians": "7",
    "Liberal Party": "8",
    "VMRO for Macedonia": "9",
    "Besa Movement": "10",
    "The Left": "11",
}
# The lists of 2016 as ``mk.sec`` holds them: the party it keys the list's party by, and the label on the ballot.
BALLOT_2016 = {
    "1": ("2016:1", "Коалиција ВМРО–ДПМНЕ"),
    "2": ("dpa", "Демократска партија на Албанците – ДПА"),
    "3": ("dui", "Демократска унија за интеграција – ДУИ"),
    "4": ("2016:4", "Коалиција Социјалдемократски сојуз на Македонија – СДСМ"),
    "5": ("2016:5", "Коалиција за промени и правда – Трет блок"),
    "6": ("2016:6", "Партија за демократски просперитет – ПДП"),
    "7": ("2016:7", "Коалиција „Алијанса за Албанците“"),
    "8": ("2016:8", "Либерална партија на Македонија"),
    "9": ("2016:9", "Коалиција ВМРО за Македонија"),
    "10": ("2016:10", "Движење Беса"),
    "11": ("levica", "Левица"),
}
# The six lists of 2024 that ``mk.sec`` holds, by the compilation's English name.
LISTS_2024 = {
    "Your Macedonia": "vmro",
    "For a European Future": "sdsm",
    "European Front": "dui",
    "VLEN Coalition": "vlen",
    "The Left": "levica",
    "For Our Macedonia": "znam",
}
CANDIDATES = {
    "PENDAROVSKI, Stevo": sec_president.PENDAROVSKI,
    "SILJANOVSKA-DAVKONA, Gordana": sec_president.SILJANOVSKA,
}
# The Commission's national figures of the runoff, from its final report (as ``mk.sec_president`` holds them).
RUNOFF_DECLARED = {
    sec_president.PENDAROVSKI: 251_899,
    sec_president.SILJANOVSKA: 561_000,
}
RUNOFF_VALID = 812_899
RUNOFF_INVALID = 48_289
# The Commission's valid votes in the Assembly election of 2024, nationwide.
PARL_2024_VALID = 978_980


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "stations")


def fetch() -> None:
    raw.fetch(SOURCE, FILE, suffix=".zip")
    raw.fetch("mk.sec", sec.FILES["pdf24"], suffix=".pdf")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in (PARL_2016, PARL_2024, RUNOFF_2024))]


def parse() -> None:
    registry().write()
    build_2016().write()
    build_2024().write()
    build_runoff().write()


def emit() -> None:
    package.run("load", packages())


def table(year: int) -> pl.DataFrame:
    archive = zipfile.ZipFile(raw.cached(SOURCE, FILE).path)
    data = archive.read(f"SAGE_North Macedonia_{year}.csv")
    return pl.read_csv(io.BytesIO(data), infer_schema_length=0).with_columns(
        pl.col("votes", "total_votes").cast(pl.Int64),
        pl.col("reg").cast(pl.Float64, strict=False).cast(pl.Int64, strict=False),
    )


def municipality(cyrillic: str) -> str:
    """The municipality of a station by the official list's Macedonian name, which the compilation spells with
    stray capitals and spaces in a few places."""
    return sec.municipality(cyrillic)


def new_parties() -> dict[str, tuple[str, str]]:
    """The 2024 lists that won no seat, by the compilation's English name: the ballot label and the kind."""
    found: dict[str, tuple[str, str]] = {}
    frame = table(2024).filter(pl.col("election_type") == "Legislative (unicameral)")
    for row in frame.select("party", "party_b").unique().sort("party").to_dicts():
        if row["party"] in LISTS_2024:
            continue
        label = sec.clean(row["party_b"])
        found[row["party"]] = (label, "list_vehicle" if label.startswith("Коалиција") else "party")
    return found


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The source of the polling-station results of the 2016 and 2024 Assembly elections and the 2024 "
        "presidential runoff, and the 2024 lists that won no seat",
    )
    p.external |= {MACEDONIA}
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Државна изборна комисија",
        title="Results by polling station of the Assembly and presidential elections",
        url="https://www.sec.mk/",
        license="No license stated; published by the State Election Commission for public use",
        attribution="Државна изборна комисија на Република Северна Македонија",
        notes="The Commission's counts by polling station, as carried in the compilation of small-area election "
        "returns by Noah Dasanaike (Harvard), version 1.4, which is cited as the file each result set comes from. "
        "The compilation carries each station's list or candidate votes, its valid votes, and where known its "
        "registered voters; it carries no invalid ballots.",
    )
    artifact = raw.cached(SOURCE, FILE)
    p.artifact(artifact, SOURCE_ID)
    for english, (label, kind) in new_parties().items():
        id = party(english)
        p.add("party", id=id, kind=kind, polity_id=MACEDONIA)
        p.name(id, label, "mk", "official")
        p.name(id, english, "en", "english", False)
    return p


def party(english: str) -> str:
    return key("party", english)


def stations(
    p: Package,
    frame: pl.DataFrame,
    day: str,
    scheme: str,
    parents: dict[str, str],
    abroad_parent: str | None,
) -> dict[str, str]:
    """A reporting unit for each polling station of the frame, by its code. ``parents`` gives its parent by unit."""
    found: dict[str, str] = {}
    unique = frame.select("NAME1", "NAME2_b", "NAME4", "NAME4_b", "special_type", "special_type_b").unique(
        subset=["NAME4", "NAME1"]
    )
    for row in unique.sort("NAME4").to_dicts():
        code = row["NAME4"]
        id = key("unit", day, scheme, code)
        if code in found:
            raise ValueError(f"station {code} repeats on {day}")
        found[code] = id
        if row["NAME1"] == "Abroad" or row["special_type"] == "Abroad" or row["special_type_b"] == "Abroad":
            p.add(
                "reporting_unit",
                id=id,
                polity_id=MACEDONIA,
                scheme_code=scheme,
                code=f"{day}:{code}",
                parent_id=abroad_parent,
                area_relation="unlocated",
                area_id=None,
                kind="abroad_mission",
            )
        else:
            p.add(
                "reporting_unit",
                id=id,
                polity_id=MACEDONIA,
                scheme_code=scheme,
                code=f"{day}:{code}",
                parent_id=parents[row["NAME1"]],
                area_relation="within",
                area_id=sec.muni_area(municipality(row["NAME2_b"])),
                kind="standard",
            )
            p.external.add(sec.muni_area(municipality(row["NAME2_b"])))
        label = row["NAME4_b"]
        if label and row["NAME1"] != "Abroad":
            p.name(id, sec.clean(label), "mk", "official")
        elif row["NAME1"] == "Abroad":
            p.name(id, sec.clean(code), "mk", "official")
    return found


def values(
    day: str,
    rs: str,
    frame: pl.DataFrame,
    found: dict[str, str],
    options: dict[str, str],
    column: str,
) -> list[dict[str, object]]:
    """The result values of one result set: each option's votes, and each station's valid and registered voters."""
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in frame.select("NAME4", column, "votes", "total_votes", "reg").to_dicts():
        unit = found[row["NAME4"]]
        rows.append(sec.fact(rs, unit, options[row[column]], VOTES, row["votes"]))
        if row["NAME4"] not in seen:
            seen.add(row["NAME4"])
            rows.append(sec.fact(rs, unit, None, VALID, row["total_votes"]))
            if row["reg"] is not None:
                rows.append(sec.fact(rs, unit, None, REGISTERED, row["reg"]))
    return rows


def assembly_package(day: date) -> Package:
    return Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day.year} Assembly election of North Macedonia by polling station, from the State Election Commission's "
        "counts",
        depends=[f"{SOURCE}/registry"],
    )


def result_set(
    p: Package, election: str, cid: str, label: str, completeness: float | None, methodology: str, caveat: str
) -> str:
    rs = key("result_set", label)
    p.add(
        "result_set",
        id=rs,
        election_id=election,
        contest_id=cid,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=False,
        completeness=completeness,
    )
    sec.note(p, rs, "methodology", methodology)
    sec.note(p, rs, "caveat", caveat, "caveat")
    return rs


def build_2016() -> Package:
    year = 2016
    day = str(PARL_2016)
    p = assembly_package(PARL_2016)
    election = sec.key("election", PARL_2016)
    p.external |= {MACEDONIA, COUNTRY, election, sec.S_STATION}
    frame = table(2016).with_columns(
        pl.col("NAME1").str.replace("Изборна Единица ", "").alias("unit"),
        pl.col("party").replace_strict(LISTS_2016).alias("list"),
    )
    official = {(cast(int, r["unit"]), str(r["list"])): cast(int, r["votes"]) for r in sec.scans()["2016"]}
    parents = {f"Изборна Единица {n}": sec.unit_id(year, f"d{n}") for n in range(1, 7)}
    p.external |= set(parents.values())
    found = stations(p, frame, day, sec.S_STATION, parents, None)
    rows: list[dict[str, object]] = []
    for number in range(1, 7):
        code = str(number)
        mine = frame.filter(pl.col("unit") == code)
        cid = sec.key("contest", PARL_2016, code)
        p.external.add(cid)
        options: dict[str, str] = {}
        for list_id in sorted(set(mine["list"]), key=int):
            if (number, list_id) in official:
                options[list_id] = sec.key("option", PARL_2016, code, list_id)
                p.external.add(options[list_id])
            else:
                party_code, label = BALLOT_2016[list_id]
                options[list_id] = key("option", day, code, list_id)
                p.external.add(sec.party(party_code))
                p.add(
                    "contest_option",
                    id=options[list_id],
                    contest_id=cid,
                    kind="list",
                    party_id=sec.party(party_code),
                    ballot_position=int(list_id),
                    ballot_label=label,
                )
        votes = mine.group_by("list").agg(pl.col("votes").sum())
        total = sum(v for (u, _), v in official.items() if u == number)
        summed = int(votes["votes"].sum())
        differences = ", ".join(
            f"list {row['list']} {row['votes'] - official.get((number, row['list']), 0):+,}"
            for row in votes.sort(pl.col("list").cast(pl.Int64)).to_dicts()
        )
        rs = result_set(
            p,
            election,
            cid,
            f"{day}:{code}",
            round(min(summed / total, 1.0), 6),
            "Each list's votes at each polling station of the unit, with its valid votes and, where the source gives "
            "it, its registered voters. The stations' list votes add up to "
            f"{summed:,}; the Commission's decision of 28 December 2016 gives {total:,} for the unit.",
            "The stations are filed under this unit as the compilation files them, which differs from the "
            "Commission's own placing in the split municipalities, so the unit's sums do not equal the decision's "
            f"(compilation less decision: {differences}). The file carries no invalid ballots, and the units abroad "
            "are not in it.",
        )
        rows += values(day, rs, mine, found, options, "list")
    return sec.finish(p, rows, [])


def build_2024() -> Package:
    year = 2024
    day = str(PARL_2024)
    p = assembly_package(PARL_2024)
    election = sec.key("election", PARL_2024)
    p.external |= {MACEDONIA, COUNTRY, election, sec.S_STATION, sec.ASSEMBLY}
    p.artifact(raw.cached("mk.sec", sec.FILES["pdf24"]), SOURCE_ID)
    frame = table(2024).filter(pl.col("election_type") == "Legislative (unicameral)")
    decision = sec.conclusion_2024()
    ballots = {ballot.id: ballot for ballot in sec.ballots(year)}
    added = new_parties()
    parents = {f"Изборна Единица {n}": sec.unit_id(year, f"d{n}") for n in range(1, 7)}
    p.external |= set(parents.values())
    found = stations(p, frame, day, sec.S_STATION, parents, None)
    rows: list[dict[str, object]] = []
    for code in [*(str(n) for n in range(1, 7)), "abroad"]:
        abroad = code == "abroad"
        mine = frame.filter(pl.col("NAME1") == ("Abroad" if abroad else f"Изборна Единица {code}"))
        if abroad:
            cid = key("contest", day, code)
            p.add(
                "contest",
                id=cid,
                election_id=election,
                kind="list_allocation",
                body_id=sec.ASSEMBLY,
                district_area_id=COUNTRY,
                round_number=1,
                default_result_kind="counted",
            )
            p.name(cid, "Polling stations abroad", "en", "official")
            p.add("slug", path=f"mk/{day}-general/abroad", entity_id=cid, is_current=True)
        else:
            cid = sec.key("contest", PARL_2024, code)
            p.external.add(cid)
        options: dict[str, str] = {}
        decided = {list_id: votes for list_id, votes, _ in decision[int(code)]} if not abroad else {}
        for english in sorted(set(mine["party"])):
            if english in LISTS_2024:
                list_id = LISTS_2024[english]
                if list_id in decided:
                    options[english] = sec.key("option", PARL_2024, code, list_id)
                    p.external.add(options[english])
                    continue
                ballot = ballots[list_id]
                party_id, label = sec.party(ballot.party), ballot.label
                p.external.add(party_id)
            else:
                party_id, label = party(english), added[english][0]
            options[english] = key("option", day, code, english)
            p.add(
                "contest_option",
                id=options[english],
                contest_id=cid,
                kind="list",
                party_id=party_id,
                ballot_label=label,
            )
        summed = int(mine["votes"].sum())
        methodology = (
            "Each list's votes at each polling station, with its valid votes. The file carries no registered voters "
            f"or invalid ballots. The stations' list votes add up to {summed:,}."
        )
        if abroad:
            rs = result_set(
                p,
                election,
                cid,
                f"{day}:{code}",
                None,
                methodology,
                "The file gives these polling stations abroad apart from the six units and does not say which unit "
                "the Commission counts their votes in, so the contest holds them on their own.",
            )
        else:
            totals = {
                list_id: int(mine.filter(pl.col("party") == english)["votes"].sum())
                for english, list_id in LISTS_2024.items()
                if list_id in decided
            }
            official = sum(decided.values())
            differences = ", ".join(f"{list_id} {totals[list_id] - decided[list_id]:+,}" for list_id in decided)
            rs = result_set(
                p,
                election,
                cid,
                f"{day}:{code}",
                round(min(sum(totals.values()) / official, 1.0), 6),
                methodology + f" The decision of 18 May 2024 gives {official:,} for the lists that won seats here, "
                f"and the stations give {sum(totals.values()):,} for them.",
                "The stations are filed under this unit as the compilation files them, which differs from the "
                "Commission's own placing in the split municipalities, so the unit's sums do not equal the "
                f"decision's (compilation less decision, lists with seats: {differences}).",
            )
        rows += values(day, rs, mine, found, options, "party")
    return sec.finish(p, rows, [])


def build_runoff() -> Package:
    day = RUNOFF_TAG
    p = Package(
        f"{SOURCE}/{RUNOFF_2024}",
        SOURCE,
        "The second round of the 2024 presidential election of North Macedonia by polling station, from the State "
        "Election Commission's counts",
        depends=[f"{SOURCE}/registry"],
    )
    election = sec_president.key("election", day)
    cid = sec_president.key("contest", day)
    national = sec_president.key("unit", sec_president.S_NATION, f"{day}:MK")
    scheme = sec_president.S_STATION
    p.external |= {MACEDONIA, COUNTRY, election, cid, national, scheme}
    frame = table(2024).filter(pl.col("election_type") == "Presidential")
    parents = defaultdict(lambda: national)
    found = stations(p, frame, day, scheme, parents, national)
    options = {}
    for english, name in CANDIDATES.items():
        options[english] = sec_president.key("option", day, name)
        p.external.add(options[english])
    summed = int(frame["votes"].sum())
    differences = ", ".join(
        f"{name} {int(frame.filter(pl.col('party') == english)['votes'].sum()) - RUNOFF_DECLARED[name]:+,}"
        for english, name in CANDIDATES.items()
    )
    rs = result_set(
        p,
        election,
        cid,
        day,
        round(min(summed / RUNOFF_VALID, 1.0), 6),
        "Each candidate's votes at each polling station, with its valid votes. The file carries no registered "
        f"voters or invalid ballots. The stations' votes add up to {summed:,} of the {RUNOFF_VALID:,} valid votes "
        "the Commission's final report gives.",
        f"Compilation less final report: {differences}.",
    )
    rows = values(day, rs, frame, found, options, "party")
    for english, name in CANDIDATES.items():
        p.add("declared", result_set_id=rs, option_id=options[english], measure=VOTES, value=RUNOFF_DECLARED[name])
    p.add("declared", result_set_id=rs, option_id=None, measure=INVALID, value=RUNOFF_INVALID)
    p.add("declared", result_set_id=rs, option_id=None, measure=VALID, value=RUNOFF_VALID)
    p.accept(
        "accounting.declared",
        "The stations give 251,881 votes for Pendarovski and 561,001 for Siljanovska-Davkova against the final "
        "report's 251,899 and 561,000, 812,882 valid votes against its 812,899, and no invalid ballots against its "
        "48,289; the report's figures are the declared ones",
    )
    return sec.finish(p, rows, [])
