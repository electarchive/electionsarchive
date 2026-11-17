"""Polish gmina and city council elections of 11 October 1998, 27 October 2002, and 12 November 2006.

The Krajowe Biuro Wyborcze publishes one candidate file for each election: every candidate of every council, with the
constituency, the committee, the candidate's total in the constituency, and whether the candidate was elected. For
1998 it also publishes, for the gminas of 20,000 inhabitants or fewer, one workbook for each voivodeship with the
registered voters, ballots, and valid and invalid votes of each polling district and each candidate's votes in it. For
the other gminas of 1998 and every gmina of 2002 and 2006 the candidates' votes are published only as totals for the
constituency. The 2006 candidate file adds the constituency's registered voters, voters, ballots, and valid votes. The
2006 list of polling districts holds addresses only and no results.

The Warsaw district councils of 2002 and 2006 are in ``pl.kbw_dzielnice``. Councils were elected in gminas of 20,000
or fewer inhabitants by plurality in constituencies of one to five seats, and in the other gminas and the cities with
county rights from open lists by D'Hondt.
"""

import re
import zipfile
from collections import Counter
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package
from ea.names import display
from ea.package import Package
from ea.sources.pl import kbw
from ea.sources.pl.kbw import BALLOTS, INVALID, OBWOD, PREFERENCE, REGISTERED, VALID, VOTERS, clean, count, slug
from ea.sources.pl.kbw_gmina import _dhondt, _name
from ea.sources.pl.kbw_gmina_2010_2014 import (
    BLOCK_RULES,
    LIST_RULES,
    SINGLE_RULES,
    TACIT_METHOD,
    _frame,
    _frame_values,
)

SOURCE = "pl.kbw_gmina_1998_2006"
POLAND = kbw.POLAND
VOTES = 1
DAYS = {1998: date(1998, 10, 11), 2002: date(2002, 10, 27), 2006: date(2006, 11, 12)}
CANDIDATES = {
    1998: "1998/1998-kand-rady.xlsx",
    2002: "2002/2002-kand-rady.xlsx",
    2006: "2006/2006-kand-rady-new.xlsx",
}
DISTRICTS_1998 = "1998/rada_gminy/1998_rady_gmin.zip"
FILES = {
    1998: [CANDIDATES[1998], DISTRICTS_1998],
    2002: [CANDIDATES[2002]],
    2006: [CANDIDATES[2006]],
}
ELECTED = ["W", "L", "B", "P", "G", "T"]
COLUMNS: dict[int, dict[str, str]] = {
    1998: {
        "okr": "Nr okr.",
        "list": "Nr listy",
        "pos": "Poz.",
        "votes": "Gł. kand.",
        "seat": "Mand.",
        "valid": "Gł. ważne",
    },
    2002: {
        "okr": "Okręg",
        "list": "Nr listy",
        "pos": "Nr pozycji",
        "votes": "Głosy",
        "seat": "Mandat",
    },
    2006: {
        "okr": "Okręg",
        "list": "Lista",
        "pos": "Pozycja",
        "votes": "Gł. na kand.",
        "seat": "Mandat",
        "valid": "Ważne",
        "registered": "Upr.",
        "voters": "Frekw.",
        "ballots": "Odd.",
    },
}
GIVEN = {1998: ["Imiona"], 2002: ["1. imię", "2. imię"], 2006: ["Imiona"]}
LOCAL_1998 = re.compile(r"wyborców|\bkww\b|^komitet wyborczy wyborców", re.IGNORECASE)
LOCAL_TYPES = {2002: {"W"}, 2006: {"W", "G"}}


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


def _level(year: int, text: str) -> str | None:
    """The kind of council a candidate file's level names: ``small`` (plurality), ``list`` (D'Hondt), or ``district``."""
    folded = text.lower()
    if year == 2006:
        return {"GW": "small", "GP": "list", "GM": "list"}.get(text)
    if folded.startswith("gmina do 20"):
        return "small"
    if folded.startswith(("gmina pow", "miasto na prawach", "rada m. st.")):
        return "list"
    if folded.startswith("dzielnica w gminie"):
        return "district"
    return None


def _candidates(year: int) -> pl.DataFrame:
    """Every candidate of a gmina council: constituency, list number, position, committee, votes, and whether elected."""
    spec = COLUMNS[year]
    frame = _frame(str(kbw.cached(CANDIDATES[year]).path))
    frame = frame.with_columns(level=pl.col("Szczebel").map_elements(lambda x: _level(year, x), return_dtype=pl.String))
    frame = frame.filter(pl.col("level").is_not_null())
    committee = pl.col("Komitet").map_elements(clean, return_dtype=pl.String)
    if year == 1998:
        local = committee.map_elements(lambda x: bool(LOCAL_1998.search(x)), return_dtype=pl.Boolean)
    else:
        local = pl.col("Typ").is_in(sorted(LOCAL_TYPES[year]))
    none = pl.lit(None, pl.Int64)
    return frame.select(
        gmina=kbw_teryt("TERYT"),
        okr=pl.col(spec["okr"]).cast(pl.Int64),
        level="level",
        place=pl.col("Jednostka"),
        list=pl.col(spec["list"]).cast(pl.Int64),
        pos=pl.col(spec["pos"]).cast(pl.Int64),
        name=pl.concat_str(["Nazwisko", *GIVEN[year]], separator=" ", ignore_nulls=True).map_elements(
            clean, return_dtype=pl.String
        ),
        committee=committee,
        local=local,
        sex=pl.col("Płeć"),
        votes=count(spec["votes"]),
        valid=count(spec["valid"]) if "valid" in spec else none,
        registered=count(spec["registered"]) if "registered" in spec else none,
        voters=count(spec["voters"]) if "voters" in spec else none,
        ballots=count(spec["ballots"]) if "ballots" in spec else none,
        elected=pl.col(spec["seat"]).is_in(ELECTED),
        tacit=pl.col(spec["seat"]) == "B",
    ).with_columns(dup=pl.int_range(pl.len()).over("gmina", "okr", "list", "pos"))


def kbw_teryt(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.String).str.strip_chars().str.replace(r"\.0$", "").str.zfill(6)


def _constituencies(candidates: pl.DataFrame) -> pl.DataFrame:
    """One row per constituency: its seats (the members elected), kind of contest, and whether it was decided unvoted."""
    keys = ["gmina", "okr"]
    frame = candidates.group_by(keys).agg(
        pl.col("level").first(),
        pl.col("place").first(),
        pl.col("elected").sum().alias("seats"),
        pl.col("tacit").all().alias("tacit"),
        pl.col("tacit").any().alias("some_tacit"),
        pl.col("list").n_unique().alias("lists"),
        pl.col("valid").first().alias("valid"),
        pl.col("registered").first().alias("registered"),
        pl.col("voters").first().alias("voters"),
        pl.col("ballots").first().alias("ballots"),
    )
    empty = frame.filter(pl.col("seats") == 0)
    if empty.join(candidates.filter(pl.col("votes") > 0), on=keys).height:
        raise ValueError("constituencies with votes but no member elected")
    # A constituency where nobody voted and nobody was elected has no result and no known number of seats.
    for row in empty.iter_rows(named=True):
        print(f"left out: constituency {row['okr']} of {row['gmina']} has no votes and no member elected", flush=True)
    frame = frame.filter(pl.col("seats") > 0)
    if frame.filter(pl.col("some_tacit") & ~pl.col("tacit")).height:
        raise ValueError("constituencies where only some candidates were declared elected without a vote")
    return (
        frame.drop("some_tacit")
        .with_columns(
            mode=pl.when(pl.col("level") != "small")
            .then(pl.lit("list"))
            .when(pl.col("seats") == 1)
            .then(pl.lit("single"))
            .otherwise(pl.lit("block"))
        )
        .sort(keys)
    )


def _districts_1998() -> tuple[pl.DataFrame, pl.DataFrame]:
    """The 1998 workbooks: the accounting of each polling district of each constituency and each candidate's votes."""
    archive = zipfile.ZipFile(kbw.cached(DISTRICTS_1998).path)
    frames = [_frame(archive.read(name)) for name in sorted(archive.namelist())]
    frame = pl.concat(frames).with_row_index("row")
    found = frame.select(
        "row",
        unit=kbw_teryt("Kod gminy"),
        gmina=kbw_teryt("Kod gminy"),
        okr=pl.col("Okręg").cast(pl.Int64),
        commission=pl.col("Obwód").cast(pl.Int64),
        seat=pl.col("Adres").map_elements(clean, return_dtype=pl.String),
        registered=count("Upr."),
        ballots=count("Odd."),
        invalid=count("Nieważne"),
        valid=count("Ważne"),
        list=pl.col("Nr listy").cast(pl.Int64),
        pos=pl.col("Pozycja").cast(pl.Int64),
        votes=count("Głosy"),
    )
    accounting = (
        found.drop("list", "pos", "votes", "row")
        .unique(["gmina", "okr", "commission"])
        .with_columns(kind=pl.lit("standard"), voters=pl.lit(None, pl.Int64))
        .sort("gmina", "okr", "commission")
    )
    return accounting, found.select("gmina", "okr", "commission", "list", "pos", "votes")


class Data:
    """One election's files as frames keyed by council (gmina TERYT), constituency, and polling district."""

    def __init__(self, year: int) -> None:
        self.year = year
        self.candidates = _candidates(year)
        self.constituencies = _constituencies(self.candidates)
        self.candidates = self.candidates.join(self.constituencies.select("gmina", "okr"), on=["gmina", "okr"])
        empty = pl.DataFrame(
            schema={
                "unit": pl.String,
                "gmina": pl.String,
                "okr": pl.Int64,
                "commission": pl.Int64,
                "kind": pl.String,
                "seat": pl.String,
                "registered": pl.Int64,
                "voters": pl.Int64,
                "ballots": pl.Int64,
                "invalid": pl.Int64,
                "valid": pl.Int64,
            }
        )
        self.accounting = empty
        self.district_votes = pl.DataFrame(
            schema={
                "gmina": pl.String,
                "okr": pl.Int64,
                "commission": pl.Int64,
                "list": pl.Int64,
                "pos": pl.Int64,
                "votes": pl.Int64,
            }
        )
        if year == 1998:
            accounting, votes = _districts_1998()
            keys = self.constituencies.select("gmina", "okr")
            self.accounting = accounting.select(empty.columns).join(keys, on=["gmina", "okr"])
            self.district_votes = votes.join(keys, on=["gmina", "okr"])


def label_of(year: int, place: str, gmina: str, level: str) -> str:
    """The place's name without the file's decoration (``gm.``, ``m.``, and the county)."""
    text = place.split(", pow.")[0].strip()
    text = re.sub(r"^(gm\.|m\. st\.|m\.)\s+", "", text)
    text = re.sub(r"\s+gm\.$", "", text)
    return text


def english(year: int, place: str, gmina: str, level: str) -> str:
    """The council's English name; a city has a TERYT code ending in 1 or is a city with county rights."""
    label = label_of(year, place, gmina, level)
    if level == "district":
        return f"{label} district council"
    if gmina == "146501" or gmina == "149900":
        return "Warsaw City Council"
    return f"{label} {'city' if gmina.endswith('1') else 'gmina'} council"


def _dhondt_exact(data: Data) -> tuple[int, int]:
    cands, cons = data.candidates, data.constituencies
    exact = total = 0
    lists = cands.filter(pl.col("level") != "small")
    seats = {(r["gmina"], r["okr"]): r["seats"] for r in cons.filter(pl.col("mode") == "list").iter_rows(named=True)}
    for (gmina, okr), part in lists.group_by(["gmina", "okr"]):
        totals = part.group_by("list").agg(pl.col("votes").sum())
        declared = Counter(part.filter(pl.col("elected"))["list"].to_list())
        total += 1
        exact += _dhondt(dict(zip(totals["list"], totals["votes"], strict=True)), seats[(gmina, okr)]) == dict(declared)
    return exact, total


def check_sources(data: Data) -> pl.DataFrame:
    """Refuse a parse whose files disagree: the figures must add up the way the authority's own totals say."""
    cands, cons, acc = data.candidates, data.constituencies, data.accounting
    keys = ["gmina", "okr"]
    voted = cons.filter(~pl.col("tacit"))
    if cands.join(voted.select(keys), on=keys).filter(pl.col("votes").is_null()).height:
        raise ValueError("candidates of a voted constituency without votes")
    if data.year == 1998:
        mine = acc.group_by(keys).agg(pl.col("valid").sum().alias("sum"))
        got = voted.filter(pl.col("mode") != "list").join(mine, on=keys, how="left").filter(pl.col("sum").is_not_null())
        if got.filter(pl.col("sum") != pl.col("valid")).height:
            raise ValueError("constituencies whose polling-district valid votes differ from the candidate file")
        sums = data.district_votes.group_by(*keys, "list", "pos").agg(pl.col("votes").sum().alias("sum"))
        off = (
            cands.join(voted.select(keys), on=keys)
            .join(sums, on=[*keys, "list", "pos"], how="inner")
            .filter(pl.col("sum") != pl.col("votes"))
        )
        if off.height:
            raise ValueError(f"{off.height} candidates whose polling-district votes differ from the candidate file")
    listed = cands.join(voted.filter(pl.col("mode") == "list").select(keys), on=keys)
    sums = listed.group_by(keys).agg(pl.col("votes").sum().alias("cand"))
    off = voted.filter(pl.col("mode") == "list").join(sums, on=keys).filter(pl.col("valid").is_not_null())
    off = off.filter(pl.col("cand") != pl.col("valid"))
    print(
        f"{data.year}: {off.height} list constituencies whose candidates' votes differ from the valid votes", flush=True
    )
    return off.with_columns(gap=pl.col("valid") - pl.col("cand"))


SYSTEMS = {
    "list": ("Gmina council open-list D'Hondt system", LIST_RULES),
    "single": ("Gmina council single-member system", SINGLE_RULES),
    "block": ("Gmina council multi-member plurality system", BLOCK_RULES),
}
NOTES = {
    "list": (
        "The council of a gmina with more than 20,000 inhabitants or a city with county rights is elected in "
        "constituencies from open lists that election committees register. A voter marks one candidate. Seats go "
        "to the lists by the D'Hondt method on the sum of their candidates' votes, with no threshold, and within "
        "each list to the candidates with the most votes.%s D'Hondt reproduces the declared seats in %s of the %s "
        "constituencies."
    ),
    "single": (
        "The council of a gmina with 20,000 inhabitants or fewer is elected in constituencies of one to five "
        "members. A voter marks up to as many candidates as there are seats, and the candidates with the most votes "
        "win. A constituency with no more candidates than seats holds no vote, and its candidates are declared "
        "elected."
    ),
}
NOTES["block"] = NOTES["single"]
BLOCKS_2006 = (
    " Lists could join in blocks that took seats as one list before sharing them (Ordynacja wyborcza do rad gmin, "
    "rad powiatów i sejmików województw, as amended on 6 September 2006). The file does not say which lists formed "
    "blocks, so the declared seats are stored as printed."
)
LIST_METHOD = (
    "Counted by the polling-district commissions and published by the Krajowe Biuro Wyborcze. A voter marks one "
    "candidate, so a list's votes are the sum of its candidates' votes. The source publishes votes only as each "
    "candidate's total for the constituency, with no polling-district breakdown, and each candidate's votes are "
    "stored as preference votes within the list. Declared seats and elected members are from the candidate file."
)
SINGLE_METHOD = (
    "Counted by the polling-district commissions and published by the Krajowe Biuro Wyborcze. %s Elected members "
    "are from the candidate file."
)
SINGLE_DISTRICTS = (
    "Registered voters, ballots taken from the box, and valid and invalid votes are published for each polling "
    "district (obwód) of the constituency, with each candidate's votes in it; a district that serves several "
    "constituencies appears once in each with that constituency's own figures. Invalid ballots are the ballots "
    "marked invalid."
)
SINGLE_TOTALS = "The source publishes only each candidate's total votes in the constituency."


def build(year: int) -> Package:
    data = Data(year)
    day = DAYS[year]
    short = check_sources(data)
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
    systems = {mode: key(year, "electoral_system", mode) for mode in modes}
    for mode in sorted(modes):
        title, base = SYSTEMS[mode]
        rules = dict(base)
        if mode == "list" and year == 2006:
            rules["pooling"] = "alliance"
        p.add(
            "electoral_system",
            id=systems[mode],
            polity_id=POLAND,
            valid_from=date(year, 1, 1),
            rules=rules,
            rules_version=1,
        )
        p.name(
            systems[mode], title + (" with blocks (2006)" if mode == "list" and year == 2006 else ""), "en", "official"
        )
        note = (
            NOTES[mode] % (BLOCKS_2006 if year == 2006 else "", f"{exact:,}", f"{total:,}")
            if mode == "list"
            else NOTES[mode]
        )
        p.add(
            "note",
            id=key(year, "note", systems[mode]),
            entity_id=systems[mode],
            kind="methodology",
            body_md=note,
            lang="en",
        )
    if short.height:
        if short.filter(pl.col("gap") < 0).height:
            raise ValueError("candidates' votes above the valid votes of a list constituency")
        p.accept(
            "accounting.votes_valid",
            f"In {short.height} of the {total:,} list constituencies the candidate file's valid votes exceed the sum "
            f"of its candidates' votes, by {short['gap'].min()} to {short['gap'].max()} votes; the source publishes "
            "the valid votes and each candidate's total and does not say where the difference lies.",
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
        row["gmina"]: english(year, row["place"], row["gmina"], row["level"])
        for row in cons.group_by("gmina").agg(pl.col("place").first(), pl.col("level").first()).iter_rows(named=True)
    }
    bodies = [{"id": key(year, "body", gmina), "polity_id": POLAND, "kind": "council"} for gmina in sorted(labels)]
    p.frame("body", pl.DataFrame(bodies))
    p.frame(
        "name",
        pl.DataFrame(
            [_name(p, key(year, "body", gmina), text, "en", "official", True) for gmina, text in sorted(labels.items())]
        ),
    )
    return finish(p, data, labels, systems)


def _party_key(year: int, committee: str, local: bool, gmina: str) -> str:
    """Committees of voters are local to one gmina; party and coalition committees are the same everywhere."""
    if local:
        return key(year, "party", slug(committee), gmina)
    return key(year, "party", slug(committee))


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
        if not voted:
            method = TACIT_METHOD
        elif listed:
            method = LIST_METHOD
        else:
            method = SINGLE_METHOD % (SINGLE_DISTRICTS if tag in districted else SINGLE_TOTALS)
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


def values(p: Package, data: Data, labels: dict[str, str]) -> None:
    year, day = data.year, DAYS[data.year]
    cands, cons, acc = data.candidates, data.constituencies, data.accounting
    voted = cons.filter(~pl.col("tacit")).with_columns(
        result_set_id=pl.struct("gmina", "okr").map_elements(
            lambda row: key(year, "result_set", row["gmina"], row["okr"]), return_dtype=pl.String
        )
    )
    detailed = acc.select("gmina", "okr").unique()
    if acc.height:
        # One unit per polling district, shared by every constituency the district serves.
        districts = (
            acc.select("unit", "commission", "kind", "seat")
            .unique(["unit", "commission"])
            .sort("unit", "commission")
            .with_columns(
                unit_id=pl.struct("unit", "commission").map_elements(
                    lambda row: key(year, "unit", row["unit"], row["commission"]), return_dtype=pl.String
                )
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
                [
                    _name(p, row["unit_id"], row["seat"], "pl", "official", True)
                    for row in districts.iter_rows(named=True)
                ]
            ),
        )
        base = acc.join(voted.select("gmina", "okr", "result_set_id"), on=["gmina", "okr"]).join(
            districts.select("unit", "commission", "unit_id"), on=["unit", "commission"]
        )
        if base.height != acc.height:
            raise ValueError("protocol rows outside the voted constituencies")
        for measure, column in (
            (REGISTERED, "registered"),
            (BALLOTS, "ballots"),
            (VALID, "valid"),
            (INVALID, "invalid"),
        ):
            p.frame("result_value", _frame_values(base, measure, column))
        cast = (
            data.district_votes.join(
                districts.select("unit", "commission", "unit_id"),
                left_on=["gmina", "commission"],
                right_on=["unit", "commission"],
            )
            .join(voted.select("gmina", "okr", "result_set_id"), on=["gmina", "okr"])
            .join(cands.select("gmina", "okr", "list", "pos", "dup"), on=["gmina", "okr", "list", "pos"])
            .with_columns(
                option=pl.struct("gmina", "okr", "list", "pos", "dup").map_elements(
                    lambda row: key(year, "option", row["gmina"], row["okr"], row["list"], row["pos"], row["dup"]),
                    return_dtype=pl.String,
                )
            )
        )
        if cast.height != data.district_votes.join(voted.select("gmina", "okr"), on=["gmina", "okr"]).height:
            raise ValueError("district votes of candidates that no candidate file row matches")
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
                value="votes",
            ),
        )
    # The candidates' votes of every other constituency are published only as totals: one aggregate unit each.
    totals = voted.join(detailed, on=["gmina", "okr"], how="anti").with_columns(
        unit_id=pl.struct("gmina", "okr").map_elements(
            lambda row: key(year, "unit", row["gmina"], row["okr"], "total"), return_dtype=pl.String
        )
    )
    if not totals.height:
        return
    p.frame(
        "reporting_unit",
        totals.select(
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
                for row in totals.iter_rows(named=True)
            ]
        ),
    )
    ids = cands.join(
        totals.select("gmina", "okr", "result_set_id", "unit_id", "mode"), on=["gmina", "okr"]
    ).with_columns(
        option=pl.struct("gmina", "okr", "list", "pos", "dup").map_elements(
            lambda row: key(year, "option", row["gmina"], row["okr"], row["list"], row["pos"], row["dup"]),
            return_dtype=pl.String,
        ),
        measure=pl.when(pl.col("mode") == "list").then(pl.lit(PREFERENCE)).otherwise(pl.lit(VOTES)).cast(pl.Int16),
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
    lists = (
        ids.filter(pl.col("mode") == "list")
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
    for measure, column in ((REGISTERED, "registered"), (VOTERS, "voters"), (BALLOTS, "ballots"), (VALID, "valid")):
        known = totals.filter(pl.col(column).is_not_null())
        if known.height:
            p.frame("result_value", _frame_values(known, measure, column))
