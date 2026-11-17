"""Polish gmina and city council elections of 21 November 2010 and 16 November 2014, by constituency and polling district.

The Krajowe Biuro Wyborcze publishes one candidate file for each election: every candidate of every council, with the
constituency, the committee, the candidate's total in the constituency, and whether the candidate was elected. For
the cities with county rights it also publishes the committees' votes in each polling district (2010: one workbook per
city in a zip; 2014: one workbook), and for 2014 a protocol workbook with the accounting of each polling district of the
other gminas. The candidate votes of the gminas other than cities with county rights, and of the 2010 gminas of more
than 20,000 inhabitants, are published only as totals for the constituency.

Councils were elected in 2014 in single-member constituencies (gminas other than cities with county rights) or from
open lists by D'Hondt (cities with county rights), and in 2010 by plurality in constituencies of one to five seats
(gminas of 20,000 or fewer inhabitants) or from open lists by D'Hondt.
"""

import zipfile
from collections import Counter
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl

from ea import package
from ea.names import display
from ea.package import Package
from ea.sources.pl import kbw
from ea.sources.pl.kbw import BALLOTS, INVALID, OBWOD, PREFERENCE, REGISTERED, VALID, VOTERS, clean, count, slug
from ea.sources.pl.kbw_gmina import _dhondt, _name, council_of, teryt

SOURCE = "pl.kbw_gmina_2010_2014"
POLAND = kbw.POLAND
VOTES = 1
DAYS = {2010: date(2010, 11, 21), 2014: date(2014, 11, 16)}
CANDIDATES = {2010: "2010/samo2010/2010-kand-rady.xlsx", 2014: "2014/samorzad/2014-kand-rady.xlsx"}
DISTRICTS = {
    2010: "2010/samo2010/samo2010mpp.zip",
    2014: "2014/samorzad/1461141888_debd211b0dd6b702ec81a9da369c64fa.xlsx",
}
SMALL_2014 = "2014/samorzad/1461141888_21c47074c739bbc66642da3ab1690146.xls"
FILES = {
    2010: [CANDIDATES[2010], DISTRICTS[2010]],
    2014: [CANDIDATES[2014], DISTRICTS[2014], SMALL_2014],
}
LEVELS = {
    2010: {"gmina do 20 tyś.": "small", "gmina pow. 20 tyś.": "list", "miasto na prawach powiatu": "list"},
    2014: {"gmina niebędąca m.p.p.": "single", "miasto na prawach powiatu": "list"},
}
PRISON = {"A", "K", "O"}


def key(year: int, kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, str(year), kind, *(str(part) for part in parts)])))


def packages() -> list[str]:
    return [f"{SOURCE}/{day}" for day in DAYS.values()]


def parse() -> None:
    for year in DAYS:
        build(year).write()


def emit() -> None:
    package.run("load", packages())


def fetch() -> None:
    for files in FILES.values():
        for path in files:
            kbw.fetch_one(path)


def _frame(data: bytes | str) -> pl.DataFrame:
    """Read a workbook's only sheet as text, with the line breaks in its headers folded into spaces."""
    frame = fastexcel.read_excel(data).load_sheet(0, header_row=0, dtypes="string").to_polars()
    return frame.rename({column: clean(column) for column in frame.columns})


def _candidates(year: int) -> pl.DataFrame:
    """Every candidate of a gmina council: constituency, list number, position, committee, votes, and whether elected."""
    frame = _frame(str(kbw.cached(CANDIDATES[year]).path)).filter(pl.col("Szczebel").is_in(list(LEVELS[year])))
    names = {
        2010: {
            "okr": "Okręg",
            "pos": "Pozycja",
            "ctype": "Typ",
            "sex": "Plec",
            "votes": "L. głosów",
            "seat": "Wybrany",
        },
        2014: {"okr": "Nr okr.", "pos": "Poz.", "ctype": "Typ kom.", "sex": "Płeć", "votes": "Głosy", "seat": "Mandat"},
    }[year]
    return frame.select(
        gmina=teryt("TERYT"),
        okr=pl.col(names["okr"]).cast(pl.Int64),
        level=pl.col("Szczebel").replace(LEVELS[year]),
        place=pl.col("Jednostka"),
        list=pl.col("Nr listy").cast(pl.Int64),
        pos=pl.col(names["pos"]).cast(pl.Int64),
        name=pl.concat_str(["Nazwisko", "Imiona"], separator=" ", ignore_nulls=True).map_elements(
            clean, return_dtype=pl.String
        ),
        committee=pl.col("Komitet").map_elements(clean, return_dtype=pl.String),
        local=pl.col(names["ctype"]).is_in(["W", "G", "wyborczy", "wyborczy w gminie do 20 tys."]),
        sex=pl.col(names["sex"]),
        votes=count(names["votes"]),
        valid=count("Ważne") if year == 2014 else pl.lit(None, pl.Int64),
        elected=pl.col(names["seat"]).is_in(["T", "B"]),
        tacit=pl.col(names["seat"]) == "B",
    ).with_columns(
        # The source repeats a position within one list once (Police, 2010); the repeat keeps its own identity.
        dup=pl.int_range(pl.len()).over("gmina", "okr", "list", "pos")
    )


def _constituencies(candidates: pl.DataFrame) -> pl.DataFrame:
    """One row per constituency: its seats (the members elected), kind of contest, and whether it was decided unvoted."""
    keys = ["gmina", "okr"]
    frame = candidates.group_by(keys).agg(
        pl.col("level").first(),
        pl.col("place").first(),
        pl.col("elected").sum().alias("seats"),
        pl.col("tacit").all().alias("tacit"),
        pl.col("list").n_unique().alias("lists"),
        pl.col("valid").first().alias("valid"),
    )
    if frame.filter(pl.col("seats") == 0).height:
        raise ValueError("constituencies with no member elected")
    return frame.with_columns(
        mode=pl.when(pl.col("level") == "list")
        .then(pl.lit("list"))
        .when(pl.col("seats") == 1)
        .then(pl.lit("single"))
        .otherwise(pl.lit("block"))
    ).sort(keys)


def _small_2014() -> pl.DataFrame:
    """The accounting of each polling district of each constituency of the gminas other than cities with county rights."""
    frame = _frame(str(kbw.cached(SMALL_2014).path))
    return frame.select(
        unit=teryt("TERYT"),
        gmina=teryt("TERYT"),
        okr=pl.col("Nr okr.").cast(pl.Int64),
        commission=pl.col("Nr obw.").cast(pl.Int64),
        kind=pl.when(pl.col("Typ").is_in(list(PRISON))).then(pl.lit("prison")).otherwise(pl.lit("standard")),
        seat=pl.col("Adres").map_elements(clean, return_dtype=pl.String),
        registered=count("Liczba wyborców"),
        voters=count("Wydane karty"),
        ballots=count("Karty wyjęte z urny"),
        invalid_cards=count("Karty nieważne"),
        invalid_votes=count("Głosy nieważne"),
        valid=count("Głosy ważne"),
    )


def _long(frame: pl.DataFrame, committees: list[str]) -> pl.DataFrame:
    return (
        frame.select("row", *committees)
        .unpivot(index="row", on=committees, variable_name="column", value_name="text")
        .drop_nulls("text")
        .filter(pl.col("text").str.strip_chars() != "")
        .with_columns(value=count("text"))
        .drop("text")
    )


def _cities_2014() -> tuple[pl.DataFrame, pl.DataFrame]:
    """The accounting and the committees' votes (long form, by committee name) of the 2014 city protocol."""
    frame = _frame(str(kbw.cached(DISTRICTS[2014]).path)).with_row_index("row")
    first = frame.columns.index("Głosy ważne") + 1
    last = frame.columns.index("Pkt 14")
    committees = frame.columns[first:last]
    accounting = frame.select(
        "row",
        unit=teryt("TERYT"),
        okr=pl.col("Nr okr.").cast(pl.Int64),
        commission=pl.col("Nr obw.").cast(pl.Int64),
        kind=pl.when(pl.col("Typ").is_in(list(PRISON))).then(pl.lit("prison")).otherwise(pl.lit("standard")),
        seat=pl.col("Adres").map_elements(clean, return_dtype=pl.String),
        registered=count("Liczba wyborców"),
        voters=count("Wydane karty"),
        ballots=count("Karty wyjęte z urny"),
        invalid_cards=count("Karty nieważne"),
        invalid_votes=count("Głosy nieważne"),
        valid=count("Głosy ważne"),
    ).with_columns(gmina=council_of(pl.col("unit")))
    long = (
        _long(frame, committees)
        .with_columns(
            committee=pl.col("column").map_elements(clean, return_dtype=pl.String), number=pl.lit(None, pl.Int64)
        )
        .join(accounting.select("row", "unit", "gmina", "okr", "commission"), on="row")
    )
    return accounting.drop("row"), long.drop("row", "column")


def _cities_2010() -> tuple[pl.DataFrame, pl.DataFrame]:
    """The accounting and the committees' votes (long form, by list number) of the 2010 city workbooks."""
    archive = zipfile.ZipFile(kbw.cached(DISTRICTS[2010]).path)
    accounting: list[pl.DataFrame] = []
    votes: list[pl.DataFrame] = []
    for name in sorted(archive.namelist()):
        frame = _frame(archive.read(name)).with_row_index("row")
        committees = frame.columns[frame.columns.index("Uniew. lista") + 1 :]
        found = frame.select(
            "row",
            unit=teryt("TERYT"),
            okr=pl.col("Nr okr.").cast(pl.Int64),
            commission=pl.col("Nr obw.").cast(pl.Int64),
            kind=pl.when(pl.col("Typ").is_in(list(PRISON))).then(pl.lit("prison")).otherwise(pl.lit("standard")),
            seat=pl.col("Adres").map_elements(clean, return_dtype=pl.String),
            registered=count("Upr."),
            voters=count("Wyd."),
            ballots=count("Odd."),
            invalid_cards=pl.lit(0, pl.Int64),
            invalid_votes=count("Gł. niew."),
            valid=count("Gł. ważne"),
        ).with_columns(gmina=council_of(pl.col("unit")))
        long = (
            _long(frame, committees)
            .with_columns(
                number=pl.col("column").str.extract(r"^(\d+) - ").cast(pl.Int64),
                committee=pl.col("column").str.extract(r"^\d+ - (.*)$").map_elements(clean, return_dtype=pl.String),
            )
            .join(found.select("row", "unit", "gmina", "okr", "commission"), on="row")
        )
        accounting.append(found.drop("row"))
        votes.append(long.drop("row", "column"))
    return pl.concat(accounting), pl.concat(votes)


class Data:
    """One election's files as frames keyed by council (gmina TERYT), constituency, and polling district."""

    def __init__(self, year: int) -> None:
        self.year = year
        self.candidates = _candidates(year)
        self.constituencies = _constituencies(self.candidates)
        if year == 2014:
            small = _small_2014().with_columns(mpp=pl.lit(False))
            city, self.votes = _cities_2014()
            city = city.with_columns(mpp=pl.lit(True))
            self.accounting = pl.concat([small, city.select(small.columns)])
        else:
            city, self.votes = _cities_2010()
            self.accounting = city.with_columns(mpp=pl.lit(True))


def english(place: str, gmina: str) -> str:
    """The council's English name; a unit that is a city is marked ", m." in 2014 and has a TERYT code ending in 1."""
    if gmina == "146501":
        return "Warsaw City Council"
    label, comma, kind = place.rpartition(", ")
    city = kind == "m." if comma else gmina.endswith("1")
    return f"{label if comma else place} {'city' if city else 'gmina'} council"


def _party_key(year: int, committee: str, local: bool, gmina: str) -> str:
    """Committees of voters are local to one gmina; party and coalition committees are the same everywhere."""
    if local:
        return key(year, "party", slug(committee), gmina)
    return key(year, "party", slug(committee))


LIST_RULES: dict[str, object] = {"formula": "dhondt", "lists": {"order": "open", "vacancy": "next_on_list"}}
SINGLE_RULES: dict[str, object] = {"formula": "fptp"}
BLOCK_RULES: dict[str, object] = {"formula": "block"}
LIST_SYSTEM_NOTE = (
    "The council of a city with county rights%s is elected in constituencies of five or more members from open lists "
    "that election committees register. A voter marks one candidate. Seats go to the lists by the D'Hondt method on "
    "the sum of their candidates' votes, with no threshold, and within each list to the candidates with the most "
    "votes. D'Hondt reproduces the declared seats in %s of the %s constituencies."
)
LIST_SYSTEM_NOTE_2010 = " and of a gmina with more than 20,000 inhabitants"
SINGLE_SYSTEM_NOTE = (
    "The council of a gmina that is not a city with county rights is elected in single-member constituencies. A voter "
    "marks one candidate, and the candidate with the most votes wins. A constituency with no more candidates than "
    "seats holds no vote, and its candidate is declared elected."
)
SINGLE_SYSTEM_NOTE_2010 = (
    "The council of a gmina with 20,000 inhabitants or fewer is elected in constituencies of one to five members. A "
    "voter marks up to as many candidates as there are seats, and the candidates with the most votes win. A "
    "constituency with no more candidates than seats holds no vote, and its candidates are declared elected."
)
LIST_METHOD = (
    "Counted by the polling-district commissions and published by the Krajowe Biuro Wyborcze. A voter marks one "
    "candidate, so a list's votes are the sum of its candidates' votes. %s Declared seats and elected members are "
    "from the candidate file."
)
LIST_DISTRICTS = (
    "The committees' votes, the registered voters, the ballots taken from the box, and the valid and invalid votes "
    "are published for each polling district (obwód głosowania), where a list with no figure received no votes; each "
    "candidate's votes are published only as a total for the constituency and are stored as preference votes within "
    "the list. Invalid ballots are the invalid ballot papers plus the invalid votes on valid ones."
)
LIST_TOTALS = (
    "The source publishes votes only as each candidate's total for the constituency, with no polling-district "
    "breakdown. Each list's votes here are the sum of its candidates' votes, and each candidate's votes are stored "
    "as preference votes within the list."
)
SINGLE_METHOD = (
    "Counted by the polling-district commissions and published by the Krajowe Biuro Wyborcze. %s Elected members "
    "are from the candidate file."
)
SINGLE_DISTRICTS = (
    "Registered voters, ballots taken from the box, and valid and invalid votes are published for each polling "
    "district (obwód) of the constituency, where a district that serves several constituencies appears once in each "
    "with that constituency's own figures; each candidate's votes are published only as a total for the constituency. "
    "Invalid ballots are the invalid ballot papers plus the invalid votes on valid ones."
)
SINGLE_TOTALS = "The source publishes only each candidate's total votes in the constituency."
TACIT_METHOD = (
    "The constituency had no more candidates than seats, so no vote was held and its candidates were declared "
    "elected (Krajowe Biuro Wyborcze, candidate file)."
)


def check_sources(data: Data) -> None:
    """Refuse a parse whose files disagree: the figures must add up the way the authority's own totals say."""
    cands, cons, acc, year = data.candidates, data.constituencies, data.accounting, data.year
    keys = ["gmina", "okr"]
    voted = cons.filter(~pl.col("tacit"))
    if acc.select(keys).unique().join(voted.select(keys), on=keys, how="anti").height:
        raise ValueError("polling districts in constituencies that held no vote")
    # Votes of a voted constituency are all present, and the list sums in the district files equal the candidates'.
    if cands.join(voted.select(keys), on=keys).filter(pl.col("votes").is_null()).height:
        raise ValueError("candidates of a voted constituency without votes")
    if year == 2014:
        mine = acc.group_by(keys).agg(pl.col("valid").sum().alias("sum"))
        got = voted.join(mine, on=keys, how="left")
        off = got.filter(pl.col("sum") != pl.col("valid"))
        if off.height:
            raise ValueError(
                f"{off.height} constituencies whose polling-district valid votes differ from the candidate file"
            )
        sums = cands.join(voted.select(keys), on=keys).group_by(keys).agg(pl.col("votes").sum().alias("cand"))
        off = got.join(sums, on=keys).filter(pl.col("cand") != pl.col("valid"))
        singles = off.filter(pl.col("mode") == "single")
        if singles.height:
            raise ValueError(
                f"{singles.height} single-member constituencies whose candidates' votes differ from valid votes"
            )
    lists = _list_votes(data)
    if lists.filter(pl.col("number").is_null()).height:
        raise ValueError("committee columns that match no list of their constituency")
    declared = (
        cands.filter(pl.col("level") == "list")
        .group_by("gmina", "okr", "list")
        .agg(pl.col("votes").sum().alias("declared"))
    )
    own = lists.group_by("gmina", "okr", "number").agg(pl.col("value").sum().alias("sum"))
    off = own.join(declared, left_on=["gmina", "okr", "number"], right_on=["gmina", "okr", "list"], how="left").filter(
        pl.col("sum") != pl.col("declared")
    )
    if off.filter(pl.col("declared") > pl.col("sum")).height:
        raise ValueError("candidates' votes above the district list votes of a list")
    print(
        f"{year}: {off.height} lists whose candidates' votes in the candidate file fall short of the districts' list votes",
        flush=True,
    )


def _list_votes(data: Data) -> pl.DataFrame:
    """The committees' votes by polling district, with each committee matched to its list number in the constituency."""
    votes = data.votes
    if not votes.height:
        return votes
    numbers = (
        data.candidates.filter(pl.col("level") == "list")
        .select("gmina", "okr", number="list", committee="committee")
        .unique()
        .with_columns(match=pl.col("committee").str.to_lowercase())
    )
    if data.year == 2010:
        found = votes.join(numbers.select("gmina", "okr", "number", "match"), on=["gmina", "okr", "number"], how="left")
        if found.filter(pl.col("match").is_null()).height:
            raise ValueError("list numbers in the district file that the candidate file lacks")
        wrong = found.filter(pl.col("committee").str.to_lowercase() != pl.col("match"))
        if wrong.height:
            raise ValueError(f"{wrong.height} committee names that differ between the district and candidate files")
        return found.drop("match")
    found = votes.drop("number").with_columns(match=pl.col("committee").str.to_lowercase())
    return found.join(numbers.select("gmina", "okr", "number", "match"), on=["gmina", "okr", "match"], how="left").drop(
        "match"
    )


def _dhondt_exact(data: Data) -> tuple[int, int]:
    cands, cons = data.candidates, data.constituencies
    exact = total = 0
    lists = cands.filter(pl.col("level") == "list")
    seats = {(r["gmina"], r["okr"]): r["seats"] for r in cons.filter(pl.col("mode") == "list").iter_rows(named=True)}
    for (gmina, okr), part in lists.group_by(["gmina", "okr"]):
        totals = part.group_by("list").agg(pl.col("votes").sum())
        declared = Counter(part.filter(pl.col("elected"))["list"].to_list())
        total += 1
        exact += _dhondt(dict(zip(totals["list"], totals["votes"], strict=True)), seats[(gmina, okr)]) == dict(declared)
    return exact, total


def build(year: int) -> Package:
    data = Data(year)
    day = DAYS[year]
    check_sources(data)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {year} gmina and city council elections in Poland by constituency and polling district from the "
        "Krajowe Biuro Wyborcze",
        depends=["pl.kbw/registry"],
    )
    p.external |= {POLAND, OBWOD}
    for path in FILES[year]:
        p.artifact(kbw.cached(path), kbw.KBW)
    cons = data.constituencies
    election = key(year, "election")
    root = f"pl/{day}-gmina-councils"
    p.add("election", id=election, polity_id=POLAND, kind="local", date_from=day, date_to=day, status="held")
    p.name(election, f"{year} gmina and city council elections in Poland", "en", "official")
    p.name(election, f"Wybory do rad gmin i rad miast {year}", "pl", "native", False)
    p.add("slug", path=root, entity_id=election, is_current=True)
    modes = set(cons["mode"].to_list())
    exact, total = _dhondt_exact(data)
    print(f"{year}: D'Hondt reproduces {exact:,} of {total:,} list constituencies", flush=True)
    systems = {
        "list": (key(year, "electoral_system", "list"), LIST_RULES, "Gmina council open-list D'Hondt system"),
        "single": (key(year, "electoral_system", "single"), SINGLE_RULES, "Gmina council single-member system"),
        "block": (key(year, "electoral_system", "block"), BLOCK_RULES, "Gmina council multi-member plurality system"),
    }
    notes = {
        "list": LIST_SYSTEM_NOTE % (LIST_SYSTEM_NOTE_2010 if year == 2010 else "", f"{exact:,}", f"{total:,}"),
        "single": SINGLE_SYSTEM_NOTE_2010 if year == 2010 else SINGLE_SYSTEM_NOTE,
        "block": SINGLE_SYSTEM_NOTE_2010,
    }
    for mode in sorted(modes):
        system, rules, title = systems[mode]
        p.add(
            "electoral_system", id=system, polity_id=POLAND, valid_from=date(year, 1, 1), rules=rules, rules_version=1
        )
        p.name(system, title, "en", "official")
        p.add(
            "note", id=key(year, "note", system), entity_id=system, kind="methodology", body_md=notes[mode], lang="en"
        )
    if year == 2014:
        p.accept(
            "accounting.votes_valid",
            "For the gminas other than cities with county rights, the protocol workbook gives the valid votes of each "
            "polling district but the candidates' votes only as totals for the constituency, so a district's valid "
            "votes have no candidate votes at the district to sum against.",
        )
    over = data.accounting.filter(pl.col("ballots") > pl.col("registered"))
    for row in over.iter_rows(named=True):
        p.accept(
            "accounting.turnout",
            f"In the protocol for polling district {row['commission']} of constituency {row['okr']} of the gmina "
            f"with TERYT code {row['unit']}, the commission reports {row['ballots']} ballots taken from the box "
            f"against {row['registered']} registered voters; the figures are the source's own.",
        )
    labels = {
        row["gmina"]: english(row["place"], row["gmina"])
        for row in cons.group_by("gmina").agg(pl.col("place").first()).iter_rows(named=True)
    }
    bodies = [{"id": key(year, "body", gmina), "polity_id": POLAND, "kind": "council"} for gmina in sorted(labels)]
    p.frame("body", pl.DataFrame(bodies))
    p.frame(
        "name",
        pl.DataFrame(
            [_name(p, key(year, "body", gmina), text, "en", "official", True) for gmina, text in sorted(labels.items())]
        ),
    )
    return finish(p, data, labels, {mode: systems[mode][0] for mode in modes})


def finish(p: Package, data: Data, labels: dict[str, str], systems: dict[str, str]) -> Package:
    year, day = data.year, DAYS[data.year]
    election = key(year, "election")
    prefix = f"pl/{day}-gmina-councils"
    cands, cons = data.candidates, data.constituencies
    districted = set(map(tuple, data.accounting.select("gmina", "okr").unique().iter_rows()))
    names: list[dict[str, object]] = []
    rows: dict[str, list[dict[str, object]]] = {
        name: []
        for name in (
            "contest",
            "slug",
            "result_set",
            "note",
            "person",
            "candidacy",
            "outcome",
            "declared",
            "party",
            "contest_option",
        )
    }
    parties: set[str] = set()

    def party(committee: str, local: bool, gmina: str) -> str:
        id = _party_key(year, committee, local, gmina)
        if id not in parties:
            parties.add(id)
            rows["party"].append({"id": id, "kind": "list_vehicle", "polity_id": POLAND})
            names.append(_name(p, id, committee, "pl", "official", True))
        return id

    by_okr: dict[tuple[str, int], list[dict[str, object]]] = {}
    for row in cands.sort("gmina", "okr", "list", "pos", "dup").iter_rows(named=True):
        by_okr.setdefault((str(row["gmina"]), int(str(row["okr"]))), []).append(row)
    for row in cons.iter_rows(named=True):
        gmina, okr, mode = str(row["gmina"]), int(row["okr"]), str(row["mode"])
        tag = (gmina, okr)
        members = by_okr[tag]
        voted = not row["tacit"]
        listed = mode == "list"
        contest = key(year, "contest", *tag)
        result_set = key(year, "result_set", *tag)
        rows["contest"].append(
            {
                "id": contest,
                "election_id": election,
                "kind": "list_allocation" if listed else "body_seats",
                "body_id": key(year, "body", gmina),
                "magnitude": int(row["seats"]),
                "round_number": 1,
                "electoral_system_id": systems[mode],
                "default_result_kind": "counted" if voted else "tacit",
            }
        )
        names.append(_name(p, contest, f"{labels[gmina]}, constituency {okr}", "en", "official", True))
        rows["slug"].append(
            {"path": f"{prefix}/{gmina}-{slug(labels[gmina])}/{okr}", "entity_id": contest, "is_current": True}
        )
        rows["result_set"].append(
            {
                "id": result_set,
                "election_id": election,
                "contest_id": contest,
                "stage": "official",
                "kind": "counted" if voted else "tacit",
                "source_id": kbw.KBW,
                "status": "active",
                "is_default": True,
                "completeness": 1.0 if voted else None,
            }
        )
        detail = tag in districted
        if not voted:
            method = TACIT_METHOD
        elif listed:
            method = LIST_METHOD % (LIST_DISTRICTS if detail else LIST_TOTALS)
        else:
            method = SINGLE_METHOD % (SINGLE_DISTRICTS if detail else SINGLE_TOTALS)
        rows["note"].append(
            {
                "id": key(year, "note", result_set),
                "entity_id": result_set,
                "kind": "methodology",
                "body_md": method,
                "lang": "en",
            }
        )
        won: Counter[int] = Counter()
        list_options: dict[int, str] = {}
        for slot, member in enumerate(members, 1):
            number, pos = int(str(member["list"])), int(str(member["pos"]))
            committee, local = str(member["committee"]), bool(member["local"])
            if listed and number not in list_options:
                option = list_options[number] = key(year, "option", *tag, "list", number)
                rows["contest_option"].append(
                    {
                        "id": option,
                        "contest_id": contest,
                        "kind": "list",
                        "party_id": party(committee, local, gmina),
                        "ballot_position": number,
                        "ballot_label": committee,
                    }
                )
            ptag = (*tag, number, pos, member["dup"])
            person = key(year, "person", *ptag)
            rows["person"].append({"id": person, "gender": {"M": "male", "K": "female"}[str(member["sex"])]})
            shown = display(str(member["name"]), "pl")
            names.append(_name(p, person, shown, "pl", "official", True))
            candidate_option = key(year, "option", *ptag)
            rows["contest_option"].append(
                {
                    "id": candidate_option,
                    "contest_id": contest,
                    "kind": "candidate",
                    "parent_option_id": list_options[number] if listed else None,
                    "party_id": None if listed else party(committee, local, gmina),
                    "ballot_position": pos if listed else slot,
                    "ballot_label": shown,
                }
            )
            rows["candidacy"].append(
                {
                    "id": key(year, "candidacy", *ptag),
                    "person_id": person,
                    "option_id": list_options[number] if listed else candidate_option,
                    "role": "list_member" if listed else "head",
                    "list_position": pos if listed else None,
                    "ballot_name": str(member["name"]),
                    "is_independent": False,
                }
            )
            if member["elected"]:
                won[number] += 1
                rows["outcome"].append(
                    {
                        "result_set_id": result_set,
                        "option_id": list_options[number] if listed else candidate_option,
                        "candidacy_id": key(year, "candidacy", *ptag) if listed else None,
                        "kind": "elected",
                    }
                )
            if voted:
                rows["declared"].append(
                    {
                        "result_set_id": result_set,
                        "option_id": candidate_option,
                        "measure": PREFERENCE if listed else VOTES,
                        "value": int(str(member["votes"])),
                    }
                )
        if listed:
            for number, seats in sorted(won.items()):
                rows["outcome"].append(
                    {"result_set_id": result_set, "option_id": list_options[number], "kind": "won", "seats": seats}
                )
        if voted and row["valid"] is not None:
            rows["declared"].append(
                {"result_set_id": result_set, "option_id": None, "measure": VALID, "value": int(row["valid"])}
            )
    p.frame("name", pl.DataFrame(names))
    for table, items in rows.items():
        if items:
            p.frame(table, pl.DataFrame(items, infer_schema_length=None))
    values(p, data, labels)
    return p


def _frame_values(figures: pl.DataFrame, measure: int, column: str) -> pl.DataFrame:
    return figures.select(
        "result_set_id",
        "unit_id",
        option_id=pl.lit(None, pl.String),
        channel=pl.lit(0, pl.Int16),
        measure=pl.lit(measure, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        flags=pl.lit(0, pl.Int16),
        value=pl.col(column),
    )


def values(p: Package, data: Data, labels: dict[str, str]) -> None:
    year, day = data.year, DAYS[data.year]
    cands, cons, acc = data.candidates, data.constituencies, data.accounting
    voted = cons.filter(~pl.col("tacit")).with_columns(
        result_set_id=pl.struct("gmina", "okr").map_elements(
            lambda row: key(year, "result_set", row["gmina"], row["okr"]), return_dtype=pl.String
        )
    )
    # One unit per polling district, shared by every constituency the district serves.
    districts = (
        acc.select("unit", "commission", "kind", "seat").unique(["unit", "commission"]).sort("unit", "commission")
    )
    districts = districts.with_columns(
        unit_id=pl.struct("unit", "commission").map_elements(
            lambda row: key(year, "unit", row["unit"], row["commission"]), return_dtype=pl.String
        )
    )
    p.frame(
        "reporting_unit",
        districts.select(
            id="unit_id",
            polity_id=pl.lit(POLAND),
            scheme_code=pl.lit(OBWOD),
            code=pl.format("{}:{}:{}:rada", pl.lit(str(day)), pl.col("unit"), pl.col("commission")),
            area_relation=pl.lit("unlocated"),
            kind="kind",
        ),
    )
    p.frame(
        "name",
        pl.DataFrame(
            [_name(p, row["unit_id"], row["seat"], "pl", "official", True) for row in districts.iter_rows(named=True)]
        ),
    )
    # The candidates' votes are published only as totals for the constituency: one aggregate unit each.
    aggregates = voted.select("gmina", "okr").with_columns(
        unit_id=pl.struct("gmina", "okr").map_elements(
            lambda row: key(year, "unit", row["gmina"], row["okr"], "total"), return_dtype=pl.String
        )
    )
    p.frame(
        "reporting_unit",
        aggregates.select(
            id="unit_id",
            polity_id=pl.lit(POLAND),
            scheme_code=pl.lit(OBWOD),
            code=pl.format("{}:{}:{}:okreg", pl.lit(str(day)), pl.col("gmina"), pl.col("okr")),
            area_relation=pl.lit("unlocated"),
            kind=pl.lit("aggregate"),
        ),
    )
    p.frame(
        "name",
        pl.DataFrame(
            [
                _name(p, row["unit_id"], f"{labels[row['gmina']]}, constituency {row['okr']}", "en", "official", True)
                for row in aggregates.iter_rows(named=True)
            ]
        ),
    )
    ids = (
        cands.join(voted.select("gmina", "okr", "result_set_id", "mode"), on=["gmina", "okr"])
        .join(aggregates, on=["gmina", "okr"])
        .with_columns(
            option=pl.struct("gmina", "okr", "list", "pos", "dup").map_elements(
                lambda row: key(year, "option", row["gmina"], row["okr"], row["list"], row["pos"], row["dup"]),
                return_dtype=pl.String,
            ),
            measure=pl.when(pl.col("mode") == "list").then(pl.lit(PREFERENCE)).otherwise(pl.lit(VOTES)).cast(pl.Int16),
        )
    )
    p.frame(
        "result_value",
        ids.select(
            "result_set_id",
            "unit_id",
            option_id="option",
            channel=pl.lit(0, pl.Int16),
            measure="measure",
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            value="votes",
        ),
    )
    # A list's votes are the sum of its candidates' votes where no polling-district breakdown exists.
    detailed = acc.select("gmina", "okr").unique()
    lists = (
        ids.filter(pl.col("mode") == "list")
        .join(detailed, on=["gmina", "okr"], how="anti")
        .group_by("result_set_id", "unit_id", "gmina", "okr", "list")
        .agg(pl.col("votes").sum())
        .with_columns(
            option=pl.struct("gmina", "okr", "list").map_elements(
                lambda row: key(year, "option", row["gmina"], row["okr"], "list", row["list"]), return_dtype=pl.String
            )
        )
    )
    p.frame(
        "result_value",
        lists.select(
            "result_set_id",
            "unit_id",
            option_id="option",
            channel=pl.lit(0, pl.Int16),
            measure=pl.lit(VOTES, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            value="votes",
        ),
    )
    base = acc.join(voted.select("gmina", "okr", "result_set_id"), on=["gmina", "okr"]).join(
        districts.select("unit", "commission", "unit_id"), on=["unit", "commission"]
    )
    if base.height != acc.height:
        raise ValueError("protocol rows outside the voted constituencies")
    figures = base.with_columns(invalid=pl.col("invalid_cards") + pl.col("invalid_votes"))
    for measure, column in (
        (REGISTERED, "registered"),
        (VOTERS, "voters"),
        (BALLOTS, "ballots"),
        (VALID, "valid"),
        (INVALID, "invalid"),
    ):
        p.frame("result_value", _frame_values(figures, measure, column))
    if data.votes.height:
        cast = (
            _list_votes(data)
            .join(
                districts.select("unit", "commission", "unit_id"),
                on=["unit", "commission"],
            )
            .join(voted.select("gmina", "okr", "result_set_id"), on=["gmina", "okr"])
            .with_columns(
                option=pl.struct("gmina", "okr", "number").map_elements(
                    lambda row: key(year, "option", row["gmina"], row["okr"], "list", row["number"]),
                    return_dtype=pl.String,
                )
            )
        )
        p.frame(
            "result_value",
            cast.select(
                "result_set_id",
                "unit_id",
                option_id="option",
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(VOTES, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value="value",
            ),
        )
