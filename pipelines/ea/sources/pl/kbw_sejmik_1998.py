"""Polish voivodeship assembly (sejmik) elections of 11 October 1998 by constituency and polling district.

The Krajowe Biuro Wyborcze publishes four files for the election. The candidate file of the 1998 local elections
holds every candidate of every voivodeship assembly list with the committee, the candidate's votes in the constituency,
the list's votes, the valid votes of the constituency, and whether the candidate was elected. One workbook per
constituency, zipped, holds the registered voters, ballots, and valid votes of each polling district and the list's
votes and every candidate's votes in it. One workbook of all polling districts holds the registered voters, ballots,
invalid and valid votes, and every list's votes. One workbook for the Masovian Voivodeship holds the constituencies'
totals, the lists, and the candidates with the seats.

Each of 16 voivodeship assemblies was elected in constituencies from open lists: a voter marked one candidate, and the
list's votes are the sum of its candidates' votes. Committees whose lists won 5 percent of the valid votes in the whole
voivodeship shared the seats of each constituency by the D'Hondt method.
"""

import re
import zipfile
from collections import Counter, defaultdict
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl

from ea import package
from ea.names import display
from ea.package import Package
from ea.sources.pl import kbw
from ea.sources.pl.kbw import BALLOTS, INVALID, OBWOD, PREFERENCE, REGISTERED, VALID, clean, count, slug
from ea.sources.pl.kbw_gmina import _dhondt, _name
from ea.sources.pl.kbw_gmina_2010_2014 import _frame, _frame_values
from ea.sources.pl.prg import VOIVODESHIPS

SOURCE = "pl.kbw_sejmik_1998"
POLAND = kbw.POLAND
VOTES = 1
DAY = date(1998, 10, 11)
CANDIDATES = "1998/1998-kand-rady.xlsx"
DISTRICTS = "1998/sejmik/1998woj.zip"
LISTS = "1998/sejmik/1998.zip"
MAZOWIECKIE = "1998/sejmik/1998mazowieckie.xls"
FILES = [CANDIDATES, DISTRICTS, LISTS, MAZOWIECKIE]
POLLING_LISTS = "sejmik_listy_98.xls"
COUNTY_LISTS = "sejmik-listy-po-powiatach-1998.xls"
LIST_NAMES = "wykaz-list.xls"
HEADER = re.compile(r"^(\d+)\.\s+(.*?)\s*\(([A-Z])\)$")
ROOT = f"pl/{DAY}-voivodeship-assemblies"
THRESHOLD = 5


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def region(code: str) -> str:
    return str(uuid5(package.NAMESPACE, f"polity\x1fPL-{code}"))


ELECTION = key("election")
SYSTEM = key("electoral_system")


def packages() -> list[str]:
    return [f"{SOURCE}/{DAY}"]


def fetch() -> None:
    for path in FILES:
        kbw.fetch_one(path)


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", packages())


def teryt(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.String).str.strip_chars().str.replace(r"\.0$", "").str.zfill(6)


def _number(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.String).str.strip_chars().str.replace(r"\.0$", "").cast(pl.Int64)


def _candidates() -> pl.DataFrame:
    """Every candidate of a voivodeship assembly list: constituency, list, position, committee, votes, and seat."""
    frame = _frame(str(kbw.cached(CANDIDATES).path)).filter(pl.col("Szczebel") == "województwo")
    return frame.select(
        woj=pl.col("TERYT").str.slice(0, 2),
        okr=_number("Nr okr."),
        list=_number("Nr listy"),
        pos=_number("Poz."),
        committee=pl.col("Komitet").map_elements(clean, return_dtype=pl.String),
        name=pl.concat_str(["Nazwisko", "Imiona"], separator=" ", ignore_nulls=True).map_elements(
            clean, return_dtype=pl.String
        ),
        sex=pl.col("Płeć"),
        votes=count("Gł. kand."),
        list_votes=count("Gł. na listę"),
        valid=count("Gł. ważne"),
        elected=pl.col("Mand.") == "W",
    )


def _sheets(data: bytes) -> dict[str, pl.DataFrame]:
    book = fastexcel.read_excel(data)
    return {
        name: book.load_sheet(name, header_row=0, dtypes="string").to_polars().rename(lambda c: clean(c))
        for name in book.sheet_names
    }


def _constituency_files() -> dict[tuple[str, int], dict[str, pl.DataFrame]]:
    archive = zipfile.ZipFile(kbw.cached(DISTRICTS).path)
    found: dict[tuple[str, int], dict[str, pl.DataFrame]] = {}
    for member in sorted(archive.namelist()):
        match = re.fullmatch(r"woj/(\d\d)/\d{6}-(\d\d)\.xls", member)
        if match is None:
            continue
        found[(match.group(1), int(match.group(2)))] = _sheets(archive.read(member))
    return found


def _districts() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """The per-constituency workbooks: the accounting of each polling district, list votes, and candidate votes."""
    accounting: list[pl.DataFrame] = []
    lists: list[pl.DataFrame] = []
    candidates: list[pl.DataFrame] = []
    for (woj, okr), sheets in _constituency_files().items():
        for name, sheet in sheets.items():
            if name.endswith("-obwody"):
                accounting.append(
                    sheet.select(
                        woj=pl.lit(woj),
                        okr=pl.lit(okr, pl.Int64),
                        sheet_okr=_number("Nr okr."),
                        gmina=teryt("Kod gminy"),
                        commission=_number("Nr obw."),
                        address=pl.col("Adres").map_elements(clean, return_dtype=pl.String),
                        registered=count("Upr."),
                        ballots=count("Gł. odd. (frekw.)"),
                        valid=count("Gł. ważne"),
                    )
                )
                continue
            number = int(name)
            columns = [(m, c) for c in sheet.columns if (m := HEADER.match(c))]
            base = sheet.select(
                woj=pl.lit(woj),
                okr=pl.lit(okr, pl.Int64),
                list=pl.lit(number, pl.Int64),
                gmina=teryt("Kod gminy"),
                commission=_number("Nr obw."),
                votes=count("Gł. na listę"),
            )
            lists.append(base)
            for header, column in columns:
                position, _, flag = header.groups()
                candidates.append(
                    base.select("woj", "okr", "list", "gmina", "commission")
                    .with_columns(
                        pos=pl.lit(int(position), pl.Int64),
                        flag=pl.lit(flag),
                        votes=sheet.select(count(column)).to_series(),
                    )
                    .filter(pl.col("votes").is_not_null())
                )
    return pl.concat(accounting), pl.concat(lists), pl.concat(candidates)


def _polling_lists() -> pl.DataFrame:
    """The workbook of all polling districts: accounting and every list's votes."""
    archive = zipfile.ZipFile(kbw.cached(LISTS).path)
    frame = _frame(archive.read(POLLING_LISTS))
    numbers = [c for c in frame.columns if re.fullmatch(r"\d+(\.0)?", c)]
    return frame.select(
        woj=pl.col("Kod rady").str.slice(0, 2),
        okr=_number("Nr okr."),
        gmina=teryt("Kod gminy"),
        commission=_number("Nr obw."),
        registered=count("Upr."),
        ballots=count("Frekwencja"),
        invalid=count("Nieważne"),
        valid=count("Ważne"),
        **{f"list{int(float(c))}": count(c) for c in numbers},
    )


def _designations() -> dict[int, str]:
    """The ballot designations of the five national lists and the sixth, which every voivodeship printed alike."""
    archive = zipfile.ZipFile(kbw.cached(LISTS).path)
    frame = _frame(archive.read(LIST_NAMES)).filter(pl.col("Sejmik").is_null())
    found = {int(row["Nr listy"]): clean(str(row["Oznaczenie"]).strip('"')) for row in frame.iter_rows(named=True)}
    if sorted(found) != [1, 2, 3, 4, 5, 6]:
        raise ValueError("the list of designations no longer starts with six national lists")
    return found


class Data:
    """The files as frames keyed by voivodeship, constituency, list, candidate position, and polling district."""

    def __init__(self) -> None:
        self.candidates = _candidates()
        self.accounting, self.lists, self.district_candidates = _districts()
        self.polling = _polling_lists()
        self.designations = _designations()
        self.constituencies = (
            self.candidates.group_by("woj", "okr")
            .agg(pl.col("elected").sum().alias("seats"), pl.col("valid").first().alias("valid"))
            .sort("woj", "okr")
        )


def _compare(what: str, mine: pl.DataFrame, theirs: pl.DataFrame, keys: list[str], columns: list[str]) -> None:
    joined = mine.join(theirs, on=keys, how="full", coalesce=True, suffix="_theirs")
    off = joined.filter(
        pl.any_horizontal(
            pl.col(column).is_null()
            | pl.col(f"{column}_theirs").is_null()
            | (pl.col(column) != pl.col(f"{column}_theirs"))
            for column in columns
        )
    )
    if off.height:
        raise ValueError(f"{what}: {off.height} rows differ\n{off.head()}")


def check_sources(data: Data) -> dict[tuple[str, int, int], tuple[int, int]]:
    """Refuse a parse whose files disagree. Returns each list's candidate sum and list votes where they differ."""
    cands, acc, lists, polling = data.candidates, data.accounting, data.lists, data.polling
    if cands.select("woj", "okr", "list", "pos").is_duplicated().any():
        raise ValueError("two candidates share a list position")
    if acc.filter(pl.col("okr") != pl.col("sheet_okr")).height:
        raise ValueError("a constituency workbook holds another constituency's polling districts")
    if acc.select("gmina", "commission").is_duplicated().any():
        raise ValueError("a polling district appears in two constituencies")
    # The two district files agree on every polling district.
    keys = ["gmina", "commission"]
    _compare(
        "polling-district accounting",
        acc.select(*keys, "woj", "okr", "registered", "ballots", "valid"),
        polling.select(*keys, "woj", "okr", "registered", "ballots", "valid"),
        keys,
        ["woj", "okr", "registered", "ballots", "valid"],
    )
    if polling.filter(pl.col("ballots") != pl.col("valid") + pl.col("invalid")).height:
        raise ValueError("polling districts whose ballots differ from their valid and invalid votes")
    numbers = [c for c in polling.columns if c.startswith("list")]
    long = (
        polling.select(*keys, *numbers)
        .unpivot(index=keys, variable_name="list", value_name="votes")
        .with_columns(list=pl.col("list").str.slice(4).cast(pl.Int64))
        .filter(pl.col("votes").is_not_null())
    )
    _compare("list votes by polling district", lists.select(*keys, "list", "votes"), long, [*keys, "list"], ["votes"])
    if (
        polling.join(long.group_by(keys).agg(pl.col("votes").sum()), on=keys)
        .filter(pl.col("valid") != pl.col("votes"))
        .height
    ):
        raise ValueError("polling districts whose list votes differ from their valid votes")
    # The candidate file agrees with the sums of the district files.
    cand_sums = data.district_candidates.group_by("woj", "okr", "list", "pos").agg(pl.col("votes").sum())
    _compare(
        "candidate votes",
        cands.select("woj", "okr", "list", "pos", "votes"),
        cand_sums,
        ["woj", "okr", "list", "pos"],
        ["votes"],
    )
    flags = data.district_candidates.select("woj", "okr", "list", "pos", "flag").unique()
    if cands.join(flags, on=["woj", "okr", "list", "pos"]).filter((pl.col("flag") == "W") != pl.col("elected")).height:
        raise ValueError("candidates whose elected flag differs between the files")
    list_sums = lists.group_by("woj", "okr", "list").agg(pl.col("votes").sum())
    declared = cands.group_by("woj", "okr", "list").agg(pl.col("list_votes").first())
    joined = declared.join(list_sums, on=["woj", "okr", "list"], how="full", coalesce=True)
    if joined.filter(pl.col("list_votes") != pl.col("votes")).height or joined["list_votes"].null_count():
        raise ValueError("list votes differ between the candidate file and the district files")
    accounted = acc.group_by("woj", "okr").agg(pl.col("valid").sum().alias("sum"))
    if data.constituencies.join(accounted, on=["woj", "okr"]).filter(pl.col("valid") != pl.col("sum")).height:
        raise ValueError("constituencies whose polling-district valid votes differ from the candidate file")
    # The county workbook's totals.
    names = {name: code for code, (name, _) in VOIVODESHIPS.items()}
    book = _frame(zipfile.ZipFile(kbw.cached(LISTS).path).read(COUNTY_LISTS))
    county = (
        book.select(
            woj=pl.col(book.columns[0]).replace_strict(names),
            okr=_number("Nr okr."),
            registered=count("Upr. do głosowania"),
            ballots=count("Głosy oddane"),
            valid=count("Głosy ważne"),
        )
        .group_by("woj", "okr")
        .agg(pl.col("registered").sum(), pl.col("ballots").sum(), pl.col("valid").sum())
    )
    mine = acc.group_by("woj", "okr").agg(pl.col("registered").sum(), pl.col("ballots").sum(), pl.col("valid").sum())
    _compare("county workbook totals", mine, county, ["woj", "okr"], ["registered", "ballots", "valid"])
    _check_mazowieckie(data)
    print(
        f"{len(data.constituencies)} constituencies, {int(data.constituencies['seats'].sum())} seats, "
        f"{acc.height:,} polling districts, {int(acc['registered'].sum()):,} registered, "
        f"{int(acc['ballots'].sum()):,} cast, {int(acc['valid'].sum()):,} valid",
        flush=True,
    )
    short: dict[tuple[str, int, int], tuple[int, int]] = {}
    by_list = cands.group_by("woj", "okr", "list").agg(pl.col("votes").sum().alias("cand"))
    for row in joined.join(by_list, on=["woj", "okr", "list"]).iter_rows(named=True):
        if row["cand"] != row["votes"]:
            short[(row["woj"], row["okr"], row["list"])] = (row["cand"], row["votes"])
    return short


def _check_mazowieckie(data: Data) -> None:
    """The Masovian workbook prints each constituency's seats, lists, candidates, and totals, and the voivodeship's."""
    book = fastexcel.read_excel(kbw.cached(MAZOWIECKIE).path)
    sheet = book.load_sheet(0, header_row=None, dtypes="string").to_polars()
    rows = [r for r in sheet.iter_rows() if r[0] == "województwo mazowieckie"]
    total = next(r for r in rows if r[1] == "-")
    printed = [r for r in rows if r[1] not in ("-", None) and r[2] is not None]
    mine = pl.DataFrame(
        {
            "okr": [int(r[1]) for r in printed],
            "seats": [int(r[2]) for r in printed],
            "lists": [int(r[3]) for r in printed],
            "candidates": [int(r[4]) for r in printed],
            "registered": [int(r[5]) for r in printed],
            "ballots": [int(r[6]) for r in printed],
            "invalid": [int(r[8]) for r in printed],
            "valid": [int(r[12]) for r in printed],
        }
    )
    cands, acc = data.candidates.filter(pl.col("woj") == "14"), data.accounting.filter(pl.col("woj") == "14")
    ours = (
        cands.group_by("okr")
        .agg(
            pl.col("elected").sum().alias("seats"),
            pl.col("list").n_unique().alias("lists"),
            pl.len().alias("candidates"),
        )
        .join(
            acc.group_by("okr").agg(pl.col("registered").sum(), pl.col("ballots").sum(), pl.col("valid").sum()),
            on="okr",
        )
        .with_columns(invalid=pl.col("ballots") - pl.col("valid"))
    )
    _compare(
        "Masovian workbook",
        ours,
        mine,
        ["okr"],
        ["seats", "lists", "candidates", "registered", "ballots", "invalid", "valid"],
    )
    got = (int(acc["registered"].sum()), int(acc["ballots"].sum()), int(acc["valid"].sum()))
    if got != (int(total[5]), int(total[6]), int(total[12])) or int(total[2]) != int(ours["seats"].sum()):
        raise ValueError("the Masovian totals differ from the workbook's")


def threshold_check(data: Data) -> tuple[int, int]:
    """How many constituencies D'Hondt reproduces among the lists that won 5 percent of their voivodeship's votes."""
    cands = data.candidates.with_columns(
        ident=pl.when(pl.col("list") <= 6)
        .then(pl.col("list").cast(pl.String))
        .otherwise(pl.col("committee").map_elements(slug, return_dtype=pl.String))
    )
    exact = total = 0
    for (woj,), part in cands.group_by("woj"):
        valid = int(data.constituencies.filter(pl.col("woj") == woj)["valid"].sum())
        sums = part.group_by("ident").agg(pl.col("votes").sum())
        passed = set(sums.filter(pl.col("votes") * 100 >= THRESHOLD * valid)["ident"])
        for (okr,), seats in part.group_by("okr"):
            lists = seats.group_by("list", "ident").agg(pl.col("votes").sum(), pl.col("elected").sum().alias("won"))
            votes = {r["list"]: r["votes"] for r in lists.iter_rows(named=True) if r["ident"] in passed}
            declared = {r["list"]: r["won"] for r in lists.iter_rows(named=True) if r["won"]}
            total += 1
            exact += _dhondt(votes, int(seats["elected"].sum())) == declared
    return exact, total


GENITIVE = {
    "02": "Dolnośląskiego",
    "04": "Kujawsko-Pomorskiego",
    "06": "Lubelskiego",
    "08": "Lubuskiego",
    "10": "Łódzkiego",
    "12": "Małopolskiego",
    "14": "Mazowieckiego",
    "16": "Opolskiego",
    "18": "Podkarpackiego",
    "20": "Podlaskiego",
    "22": "Pomorskiego",
    "24": "Śląskiego",
    "26": "Świętokrzyskiego",
    "28": "Warmińsko-Mazurskiego",
    "30": "Wielkopolskiego",
    "32": "Zachodniopomorskiego",
}
RULES: dict[str, object] = {
    "formula": "dhondt",
    "lists": {"order": "open", "vacancy": "next_on_list"},
    "threshold": {"level": "list", "scope": "national", "percent": THRESHOLD},
}
SYSTEM_NOTE = (
    "Each voivodeship assembly was elected in constituencies from open lists that election committees registered. A "
    "voter marked one candidate, and a list's votes are the sum of its candidates' votes. Seats went to the lists by "
    "the D'Hondt method among the committees whose lists won 5 percent of the valid votes in the whole voivodeship "
    "(Ordynacja wyborcza do rad gmin, rad powiatów i sejmików województw of 16 July 1998), and within each list to the "
    "candidates with the most votes. The threshold counts votes across the voivodeship's constituencies, so it is "
    "stored with scope national, which here means all sibling constituencies of one assembly. D'Hondt with that "
    "threshold reproduces the declared seats in %s of the %s constituencies."
)
METHOD = (
    "Counted by the polling-district commissions and published by the Krajowe Biuro Wyborcze. Registered voters, "
    "ballots cast, and valid and invalid votes are published for each polling district (obwód) of the constituency, "
    "with each list's votes and each candidate's votes in it. A polling district serves one constituency. Invalid "
    "ballots are the ballots marked invalid, and valid votes are the sum of the lists' votes. Each candidate's votes "
    "are stored as preference votes within the list. Declared seats and elected members are from the candidate "
    "file, which agrees with the polling-district files on every candidate's total, every list's votes, and the "
    "valid votes of every constituency."
)
SHORT = (
    " The votes the polling-district files print for list %d are %s, and its candidates' votes sum to %s; both are "
    "stored as printed."
)


def english(woj: str) -> str:
    return VOIVODESHIPS[woj][1].removesuffix(" Voivodeship")


def build() -> Package:
    data = Data()
    short = check_sources(data)
    exact, total = threshold_check(data)
    print(f"D'Hondt with the 5 percent bar reproduces {exact} of {total} constituencies", flush=True)
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The 1998 voivodeship assembly elections in Poland by constituency and polling district from the Krajowe "
        "Biuro Wyborcze",
        depends=["pl.kbw/registry"],
    )
    p.external |= {POLAND, OBWOD, *(region(woj) for woj in VOIVODESHIPS)}
    for path in FILES:
        p.artifact(kbw.cached(path), kbw.KBW)
    p.add("election", id=ELECTION, polity_id=POLAND, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(ELECTION, "1998 voivodeship assembly elections in Poland", "en", "official")
    p.name(ELECTION, "Wybory do sejmików województw 1998", "pl", "native", False)
    p.add("slug", path=ROOT, entity_id=ELECTION, is_current=True)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=POLAND,
        valid_from=date(1998, 1, 1),
        valid_to=date(1998, 12, 31),
        rules=RULES,
        rules_version=1,
    )
    p.name(SYSTEM, "Voivodeship assembly open-list D'Hondt system with a voivodeship threshold", "en", "official")
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=SYSTEM_NOTE % (exact, total),
        lang="en",
    )
    bodies = []
    names: list[dict[str, object]] = []
    for woj in sorted(VOIVODESHIPS):
        body = key("body", woj)
        bodies.append({"id": body, "polity_id": region(woj), "kind": "council"})
        names.append(_name(p, body, f"Assembly of {english(woj)} Voivodeship", "en", "official", True))
        names.append(_name(p, body, f"Sejmik Województwa {GENITIVE[woj]}", "pl", "native", False))
    p.frame("body", pl.DataFrame(bodies))
    over = data.accounting.filter(pl.col("ballots") > pl.col("registered"))
    if over.height:
        raise ValueError("polling districts with more ballots than registered voters")
    return finish(p, data, short, names)


def finish(
    p: Package, data: Data, short: dict[tuple[str, int, int], tuple[int, int]], names: list[dict[str, object]]
) -> Package:
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

    def party(number: int, committee: str) -> tuple[str, str]:
        """The party of a list and the label the list carried: the five national lists are the same everywhere."""
        if number in data.designations:
            id, label = key("party", number), data.designations[number]
        else:
            id, label = key("party", slug(committee)), committee
        if id not in parties:
            parties.add(id)
            rows["party"].append({"id": id, "kind": "list_vehicle", "polity_id": POLAND})
            names.append(_name(p, id, label, "pl", "official", True))
        return id, label

    by_okr: dict[tuple[str, int], list[dict[str, object]]] = defaultdict(list)
    for row in data.candidates.sort("woj", "okr", "list", "pos").iter_rows(named=True):
        by_okr[(str(row["woj"]), int(row["okr"]))].append(row)
    for cons in data.constituencies.iter_rows(named=True):
        woj, okr, seats = str(cons["woj"]), int(cons["okr"]), int(cons["seats"])
        tag = (woj, okr)
        contest, result_set = key("contest", *tag), key("result_set", *tag)
        title = f"{english(woj)} assembly, constituency {okr}"
        rows["contest"].append(
            {
                "id": contest,
                "election_id": ELECTION,
                "kind": "list_allocation",
                "body_id": key("body", woj),
                "magnitude": seats,
                "round_number": 1,
                "electoral_system_id": SYSTEM,
                "default_result_kind": "counted",
            }
        )
        names.append(_name(p, contest, title, "en", "official", True))
        names.append(
            _name(p, contest, f"Sejmik Województwa {GENITIVE[woj]}, okręg wyborczy nr {okr}", "pl", "native", False)
        )
        rows["slug"].append(
            {"path": f"{ROOT}/{slug(VOIVODESHIPS[woj][0])}/{okr}", "entity_id": contest, "is_current": True}
        )
        rows["result_set"].append(
            {
                "id": result_set,
                "election_id": ELECTION,
                "contest_id": contest,
                "stage": "official",
                "kind": "counted",
                "source_id": kbw.KBW,
                "status": "active",
                "is_default": True,
                "completeness": 1.0,
            }
        )
        method = METHOD + "".join(
            SHORT % (number, f"{votes:,}", f"{cand:,}")
            for (w, o, number), (cand, votes) in sorted(short.items())
            if (w, o) == tag
        )
        rows["note"].append(
            {
                "id": key("note", result_set),
                "entity_id": result_set,
                "kind": "methodology",
                "body_md": method,
                "lang": "en",
            }
        )
        won: Counter[int] = Counter()
        list_options: dict[int, str] = {}
        for member in by_okr[tag]:
            number, pos = int(str(member["list"])), int(str(member["pos"]))
            if number not in list_options:
                option = list_options[number] = key("option", *tag, "list", number)
                party_id, label = party(number, str(member["committee"]))
                rows["contest_option"].append(
                    {
                        "id": option,
                        "contest_id": contest,
                        "kind": "list",
                        "party_id": party_id,
                        "ballot_position": number,
                        "ballot_label": label,
                    }
                )
                rows["declared"].append(
                    {
                        "result_set_id": result_set,
                        "option_id": option,
                        "measure": VOTES,
                        "value": int(str(member["list_votes"])),
                    }
                )
            ptag = (*tag, number, pos)
            person = key("person", *ptag)
            rows["person"].append({"id": person, "gender": {"M": "male", "K": "female"}[str(member["sex"])]})
            shown = display(str(member["name"]), "pl")
            names.append(_name(p, person, shown, "pl", "official", True))
            candidate_option, candidacy = key("option", *ptag), key("candidacy", *ptag)
            rows["contest_option"].append(
                {
                    "id": candidate_option,
                    "contest_id": contest,
                    "kind": "candidate",
                    "parent_option_id": list_options[number],
                    "ballot_position": pos,
                    "ballot_label": shown,
                }
            )
            rows["candidacy"].append(
                {
                    "id": candidacy,
                    "person_id": person,
                    "option_id": list_options[number],
                    "role": "list_member",
                    "list_position": pos,
                    "ballot_name": str(member["name"]),
                    "is_independent": False,
                }
            )
            rows["declared"].append(
                {
                    "result_set_id": result_set,
                    "option_id": candidate_option,
                    "measure": PREFERENCE,
                    "value": int(str(member["votes"])),
                }
            )
            if member["elected"]:
                won[number] += 1
                rows["outcome"].append(
                    {
                        "result_set_id": result_set,
                        "option_id": list_options[number],
                        "candidacy_id": candidacy,
                        "kind": "elected",
                    }
                )
        for number, count_won in sorted(won.items()):
            rows["outcome"].append(
                {"result_set_id": result_set, "option_id": list_options[number], "kind": "won", "seats": count_won}
            )
        rows["declared"].append(
            {"result_set_id": result_set, "option_id": None, "measure": VALID, "value": int(str(cons["valid"]))}
        )
    p.frame("name", pl.DataFrame(names))
    for table, items in rows.items():
        p.frame(table, pl.DataFrame(items, infer_schema_length=None))
    values(p, data)
    return p


def values(p: Package, data: Data) -> None:
    acc = data.accounting
    units = acc.select("woj", "okr", "gmina", "commission", "address").with_columns(
        unit_id=pl.struct("gmina", "commission").map_elements(
            lambda row: key("unit", row["gmina"], row["commission"]), return_dtype=pl.String
        ),
        result_set_id=pl.struct("woj", "okr").map_elements(
            lambda row: key("result_set", row["woj"], row["okr"]), return_dtype=pl.String
        ),
    )
    p.frame(
        "reporting_unit",
        units.select(
            id="unit_id",
            polity_id=pl.lit(POLAND),
            scheme_code=pl.lit(OBWOD),
            code=pl.format("{}:{}:{}:sejmik", pl.lit(str(DAY)), pl.col("gmina"), pl.col("commission")),
            area_relation=pl.lit("unlocated"),
            kind=pl.lit("standard"),
        ),
    )
    p.frame(
        "name",
        pl.DataFrame(
            [_name(p, row["unit_id"], row["address"], "pl", "official", True) for row in units.iter_rows(named=True)]
        ),
    )
    ids = units.select("woj", "okr", "gmina", "commission", "unit_id", "result_set_id")
    figures = acc.join(ids, on=["woj", "okr", "gmina", "commission"]).join(
        data.polling.select("gmina", "commission", "invalid"), on=["gmina", "commission"]
    )
    for measure, column in ((REGISTERED, "registered"), (BALLOTS, "ballots"), (VALID, "valid"), (INVALID, "invalid")):
        p.frame("result_value", _frame_values(figures, measure, column))
    candidate_votes = data.district_candidates.join(ids, on=["woj", "okr", "gmina", "commission"]).with_columns(
        option=pl.struct("woj", "okr", "list", "pos").map_elements(
            lambda row: key("option", row["woj"], row["okr"], row["list"], row["pos"]), return_dtype=pl.String
        )
    )
    list_votes = data.lists.join(ids, on=["woj", "okr", "gmina", "commission"]).with_columns(
        option=pl.struct("woj", "okr", "list").map_elements(
            lambda row: key("option", row["woj"], row["okr"], "list", row["list"]), return_dtype=pl.String
        )
    )
    for measure, frame in ((VOTES, list_votes), (PREFERENCE, candidate_votes)):
        p.frame(
            "result_value",
            frame.select(
                "result_set_id",
                "unit_id",
                option_id="option",
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(measure, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
                value="votes",
            ),
        )
