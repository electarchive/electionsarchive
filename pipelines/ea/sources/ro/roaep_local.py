"""Romanian local elections of 1996, 2004, and 2008, from the Permanent Electoral Authority's archive.

The Authority's page of election databases keeps one archive per local election cycle with the FoxPro tables of the
application that counted it. Each table holds one row per unit, contest class, and ballot line, with the minutes of
the unit on a row of its own (position 0):

- 1996 holds one table per voting day (2 June, 16 June, and 30 June) at the level of the local electoral
  circumscription (a commune, town, municipality, or Bucharest sector), plus tables of the results the county
  bureaus declared (`alesi`), which carry the county totals and seats of the county councils and the date on which
  each contest was decided.
- 2004 and 2008 hold one table per round (`xbaza`, `xbaza2`, and the third-round tables) with rows for each county,
  each circumscription, and each polling station. Every county council (and, in 2008, county council president) is
  counted per polling station; mayors and local councils are counted per circumscription, and per polling station
  where the municipality asked the county statistics office to centralize its stations.

A contest holds the finest units the tables print. When it holds polling stations, the circumscription or county
totals are stored as declared totals; every station row sums to them exactly.

Mayors and the General Mayor of Bucharest are elected in two rounds; a candidate with a majority wins in the first,
otherwise the two leading candidates meet two weeks later. In 1996 the 2 June vote was not valid in some
circumscriptions, and the mayor and local council were elected afresh on 16 June, with their own second round on
30 June; the county councils of two counties were also elected again on 16 June. In 2004 and 2008 a few second
rounds that ended in a tie were repeated a week later. Each of these votes is an election of its own here, so a
contest's earlier round is always in an earlier package.

The 2000 archive holds only printed summaries by county and party, the list of mayors elected, and the county
codes, so it builds no package here.
"""

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date

import polars as pl

from ea import raw
from ea.package import Package
from ea.sources.ro import roaep
from ea.sources.ro.roaep import CAST, INVALID, REGISTERED, ROMANIA, SOURCE, VALID, VOTES, dbf, fold, key, tidy, title

STATIONS, CIRCUMSCRIPTIONS = "ro.sv", "ro.circumscription"
ARCHIVE = roaep.ARCHIVE
URLS = {year: f"{ARCHIVE}/locale_{year}.zip" for year in ("1996", "2004", "2008")}

LOCAL_SOURCE = key("source", "local-archive")
LOCAL_COUNCIL = key("body", "local", "local-council")
COUNTY_COUNCIL = key("body", "local", "county-council")
BUCHAREST_COUNCIL = key("body", "local", "bucharest-council")
MAYOR = key("office", "local", "mayor")
GENERAL_MAYOR = key("office", "local", "general-mayor")
COUNTY_PRESIDENT = key("office", "local", "county-council-president")
TWO_ROUND = key("electoral_system", "local", "two-round")
PLURALITY = key("electoral_system", "local", "plurality")
LISTS = key("electoral_system", "local", "lists")

BUCHAREST = {"1996": 50, "2004": 42, "2008": 42}
INDEPENDENT = 999

# The classes of contest: the table's code, our slug, the English and Romanian names, and whether it is an office.
P, CL, CJ, TOP = 1, 2, 3, 0


@dataclass(frozen=True)
class Vote:
    """One voting day: the package it builds, its date, its kind, and what it reruns or continues."""

    name: str
    year: str
    day: date
    kind: str
    english: str
    romanian: str
    slug: str
    rerun_of: str | None = None
    depends: tuple[str, ...] = ()


VOTES_BY_YEAR: dict[str, list[Vote]] = {
    "1996": [
        Vote(
            "1996-06-02",
            "1996",
            date(1996, 6, 2),
            "local",
            "1996 Romanian local elections",
            "Alegeri locale din 2 iunie 1996",
            "ro/1996-06-02-local",
        ),
        Vote(
            "1996-06-16",
            "1996",
            date(1996, 6, 16),
            "runoff",
            "1996 Romanian local elections, second round",
            "Alegeri locale, turul al doilea, 16 iunie 1996",
            "ro/1996-06-16-local-runoff",
            depends=("1996-06-02",),
        ),
        Vote(
            "1996-06-16-repeat",
            "1996",
            date(1996, 6, 16),
            "special",
            "1996 Romanian local elections, votes held again",
            "Alegeri locale, vot repetat, 16 iunie 1996",
            "ro/1996-06-16-local-repeat",
            rerun_of="1996-06-02",
            depends=("1996-06-02",),
        ),
        Vote(
            "1996-06-30",
            "1996",
            date(1996, 6, 30),
            "runoff",
            "1996 Romanian local elections, second round of the votes held again",
            "Alegeri locale, turul al doilea al votului repetat, 30 iunie 1996",
            "ro/1996-06-30-local-runoff",
            depends=("1996-06-02", "1996-06-16", "1996-06-16-repeat"),
        ),
    ],
    "2004": [
        Vote(
            "2004-06-06",
            "2004",
            date(2004, 6, 6),
            "local",
            "2004 Romanian local elections",
            "Alegeri locale din 6 iunie 2004",
            "ro/2004-06-06-local",
        ),
        Vote(
            "2004-06-20",
            "2004",
            date(2004, 6, 20),
            "runoff",
            "2004 Romanian local elections, second round",
            "Alegeri locale, turul al doilea, 20 iunie 2004",
            "ro/2004-06-20-local-runoff",
            depends=("2004-06-06",),
        ),
        Vote(
            "2004-06-27",
            "2004",
            date(2004, 6, 27),
            "special",
            "2004 Romanian local elections, second rounds held again",
            "Alegeri locale, turul al doilea repetat, 27 iunie 2004",
            "ro/2004-06-27-local-repeat",
            rerun_of="2004-06-20",
            depends=("2004-06-06", "2004-06-20"),
        ),
    ],
    "2008": [
        Vote(
            "2008-06-01",
            "2008",
            date(2008, 6, 1),
            "local",
            "2008 Romanian local elections",
            "Alegeri locale din 1 iunie 2008",
            "ro/2008-06-01-local",
        ),
        Vote(
            "2008-06-15",
            "2008",
            date(2008, 6, 15),
            "runoff",
            "2008 Romanian local elections, second round",
            "Alegeri locale, turul al doilea, 15 iunie 2008",
            "ro/2008-06-15-local-runoff",
            depends=("2008-06-01",),
        ),
        Vote(
            "2008-06-29",
            "2008",
            date(2008, 6, 29),
            "special",
            "2008 Romanian local elections, second rounds held again",
            "Alegeri locale, turul al doilea repetat, 29 iunie 2008",
            "ro/2008-06-29-local-repeat",
            rerun_of="2008-06-15",
            depends=("2008-06-01", "2008-06-15"),
        ),
    ],
}
ALL_VOTES = [v for votes in VOTES_BY_YEAR.values() for v in votes]
REGISTRY = f"{SOURCE}/local-registry"
PACKAGES = [REGISTRY, *(f"{SOURCE}/local-{v.name}" for v in ALL_VOTES)]

METHOD = {
    "1996": (
        "Counted per local electoral circumscription (a commune, town, municipality, or Bucharest sector), from the "
        "tables of the Authority's application for the 1996 local elections. Registered voters are the voters on the "
        "lists, ballots cast are the voters who took part, valid votes are the valid votes and invalid ballots the "
        "null votes. Seats and the mayors elected are those the tables mark; the county council totals and seats are "
        "those of the Authority's table of results declared by the county bureaus."
    ),
    "2004": (
        "From the tables of the Authority's application for the 2004 local elections. Registered voters are the "
        "voters on the permanent and supplementary lists, ballots cast are the voters who took part (with those who "
        "voted at the special ballot box), valid votes are the valid votes and invalid ballots the null votes. A "
        "contest counted per polling station stores its circumscription or county totals as declared. Seats and the "
        "mayors elected are those the tables mark."
    ),
    "2008": (
        "From the tables of the Authority's application for the 2008 local elections. Registered voters are the "
        "voters on the permanent, complementary, and supplementary lists, ballots cast are the voters who took part "
        "(with those who voted at the special ballot box), valid votes are the valid votes and invalid ballots the "
        "null votes. A contest counted per polling station stores its circumscription or county totals as declared. "
        "Seats and the candidates elected are those the tables mark."
    ),
}


# ---------------------------------------------------------------- reading


def book(year: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, URLS[year]).path.read_bytes()))


def read(year: str, path: str) -> pl.DataFrame:
    archive = book(year)
    for info in archive.infolist():
        if info.filename.lower() == path.lower():
            return dbf(archive.read(info))
    raise LookupError(path)


INTS = ["jud", "circ", "sv", "tip", "ta", "tapu", "tvve", "tvn", "npoz", "codu", "vve", "m1", "m2", "adv"]


def normal(frame: pl.DataFrame, columns: dict[str, str]) -> pl.DataFrame:
    """Rename a table's columns to ours (`columns` maps ours to the table's) and cast the counts to integers."""
    out = frame.select(
        *(
            (pl.col(columns[c]).replace("", "0").cast(pl.Float64).cast(pl.Int64) if c in columns else pl.lit(0))
            .cast(pl.Int64)
            .alias(c)
            for c in INTS
        ),
        pl.col(columns["place"]).map_elements(tidy, return_dtype=pl.String).alias("place"),
        pl.col(columns["county"]).map_elements(tidy, return_dtype=pl.String).alias("county"),
        pl.col("NUMEC").map_elements(tidy, return_dtype=pl.String).alias("name"),
        pl.col("DENP").map_elements(tidy, return_dtype=pl.String).alias("party"),
    )
    return out.with_columns((pl.col("npoz") == 0).alias("total"))


def rows_1996(file: str) -> pl.DataFrame:
    frame = read("1996", file)
    columns = {
        "jud": "JUD",
        "circ": "CE",
        "tip": "TIP_PV",
        "ta": "TA",
        "tapu": "TAPU",
        "tvve": "TVVE",
        "tvn": "TVN",
        "npoz": "NPOZ",
        "codu": "CODU",
        "vve": "VVE",
        "m1": "MCF1",
        "m2": "MCF2",
        "place": "DEN_CE",
        "county": "DEN_JUD",
    }
    out = normal(frame, columns)
    # A candidate row marks a mayor elected in MCF1 and a candidate who goes to the second round in MCF2.
    return out.with_columns(
        pl.when(pl.col("tip").is_in([P, TOP]) & ~pl.col("total")).then(pl.col("m2")).otherwise(0).alias("adv"),
        pl.when(pl.col("tip").is_in([P, TOP]) & ~pl.col("total")).then(0).otherwise(pl.col("m2")).alias("m2"),
    )


def rows_x(year: str, file: str) -> pl.DataFrame:
    frame = read(year, file)
    columns = {
        "jud": "JUD",
        "circ": "CIRC",
        "sv": "SV",
        "tip": "TIPPV",
        "ta": "TA",
        "tapu": "TAPU",
        "tvve": "TVVE",
        "tvn": "TVN",
        "npoz": "NPOZ",
        "codu": "CODU",
        "vve": "VVE",
        "m1": "MAN1",
        "m2": "MAN2",
        "adv": "SORTI",
        "place": "DEN_CIRC",
        "county": "DEN_JUD",
    }
    return normal(frame, columns)


def declared_1996() -> pl.DataFrame:
    """The county council and General Mayor rows of the Authority's table of results declared by the county bureaus."""
    parts = []
    for file, seats in (("alesi/CONS_JUD.dbf", "X_CONS_J"), ("alesi/PRIMARI.dbf", "X_PRIMARI")):
        frame = read("1996", file).filter(pl.col("CE") == "0")
        parts.append(
            frame.select(
                pl.col("JUD").cast(pl.Int64).alias("jud"),
                pl.col("TIP_PV").cast(pl.Int64).alias("tip"),
                pl.col("NPOZ").replace("", "0").cast(pl.Int64).alias("npoz"),
                pl.col("CODU").replace("", "0").cast(pl.Int64).alias("codu"),
                *(pl.col(c).replace("", "0").cast(pl.Int64).alias(c.lower()) for c in ("TA", "TAPU", "TVVE", "TVN")),
                pl.col("VVE").replace("", "0").cast(pl.Int64).alias("vve"),
                pl.col(seats).replace("", "0").cast(pl.Int64).alias("seats"),
                pl.col("DATA").str.strip_chars().alias("data"),
            )
        )
    return pl.concat(parts)


# ---------------------------------------------------------------- the model


@dataclass
class Contest:
    """One contest: its class, its county and circumscription (0 for a county-wide one), and its rows."""

    year: str
    vote: Vote
    tip: int
    jud: int
    circ: int
    county: str
    place: str
    units: pl.DataFrame  # the rows of the units it holds (minutes and ballot lines)
    totals: pl.DataFrame | None  # the rows of its own total, when the units are finer
    round_number: int = 1
    previous: str | None = None
    seats: dict[int, int] = field(default_factory=dict)  # by ballot position
    elected: set[int] = field(default_factory=set)
    advanced: set[int] = field(default_factory=set)
    note: str = ""

    @property
    def bucharest(self) -> bool:
        return self.jud == BUCHAREST[self.year]

    @property
    def name(self) -> str:
        return f"local-{self.vote.name}-{self.tip}-{self.jud}-{self.circ}"

    names: dict[int, str] = field(default_factory=dict)  # names of candidates the rows leave blank

    @property
    def office(self) -> bool:
        return self.tip in (P, TOP)

    def lines(self) -> pl.DataFrame:
        """One row per ballot line, with the first name and label any row of the contest prints for it."""
        columns = ["npoz", "codu", "name", "party", "total"]
        rows = self.units.select(columns)
        if self.totals is not None:
            totals = self.totals.with_columns(
                *(pl.lit("").alias(c) for c in ("name", "party") if c not in self.totals.columns)
            )
            rows = pl.concat([totals.select(columns), rows])
        lines = (
            rows.filter(~pl.col("total"))
            .group_by("npoz", maintain_order=True)
            .agg(
                pl.col("codu").first(),
                pl.col("name").filter(pl.col("name") != "").first().fill_null(""),
                pl.col("party").filter(pl.col("party") != "").first().fill_null(""),
            )
            .sort("npoz")
        )
        if self.names:
            lines = lines.with_columns(
                pl.when(pl.col("name") == "")
                .then(pl.col("npoz").replace_strict(self.names, default="", return_dtype=pl.String))
                .otherwise(pl.col("name"))
                .alias("name")
            )
        return lines


def county_name(text: str) -> str:
    value = re.sub(r"^(JUDETUL|JUDEȚUL)\s+", "", tidy(text).upper())
    return "București" if value.startswith("MUNICIPIUL BUCURESTI") else title(value)


def place_name(text: str) -> str:
    value = re.sub(r"^(MUNICIPIUL|ORAS|ORAȘ|COMUNA)\s+", "", tidy(text).upper())
    value = re.sub(r"^BUCURESTI\s+", "", value)
    return title(value)


def slugify(text: str) -> str:
    value = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def contests_of(year: str, vote: Vote, rows: pl.DataFrame) -> list[Contest]:
    """Split one table into contests, each holding the finest units the table prints for it."""
    found: list[Contest] = []
    names = rows.filter(pl.col("circ") > 0).unique(["jud", "circ"]).select("jud", "circ", "place", "county")
    places = {(j, c): (p, k) for j, c, p, k in names.iter_rows()}
    counties = {j: k for j, _, _, k in names.iter_rows()}
    for (tip,), part in rows.partition_by("tip", as_dict=True, maintain_order=True).items():
        tip = int(str(tip))
        wide = tip in (CJ, TOP)
        scopes = part.select("jud").unique() if wide else part.filter(pl.col("circ") > 0).select("jud", "circ").unique()
        for scope in scopes.sort(scopes.columns).iter_rows():
            jud = scope[0]
            circ = 0 if wide else scope[1]
            mine = part.filter(pl.col("jud") == jud)
            if not wide:
                mine = mine.filter(pl.col("circ") == circ)
            circs = mine.filter((pl.col("circ") > 0) & (pl.col("sv") == 0))
            stations = mine.filter(pl.col("sv") > 0)
            county_rows = mine.filter(pl.col("circ") == 0)
            if wide:
                with_stations = set(stations["circ"].unique().to_list())
                if stations.height and with_stations == set(circs["circ"].unique().to_list()):
                    units, totals = stations, county_rows if county_rows.height else None
                else:
                    units, totals = circs, county_rows if county_rows.height else None
            elif stations.height:
                units, totals = stations, circs
            else:
                units, totals = circs, None
            minutes = units.filter(pl.col("total"))
            if not minutes.height or (minutes["tvve"].sum() == 0 and units["vve"].sum() == 0):
                continue  # a vote the tables print as not held or not valid
            place, county = places.get((jud, circ), ("", counties.get(jud, "")))
            found.append(Contest(year, vote, tip, jud, circ, county, place, units, totals))
    return found


def outcome_rows(c: Contest) -> pl.DataFrame:
    """The rows that carry the seats and the winners: the contest's own total when it has one, else its units."""
    if c.totals is not None:
        return c.totals.filter(~pl.col("total"))
    return c.units.filter(~pl.col("total"))


def mark(c: Contest) -> None:
    lines = outcome_rows(c)
    if c.office:
        positions = lines.group_by("npoz").agg(pl.col("m1").max(), pl.col("adv").max())
        c.elected = set(positions.filter(pl.col("m1") > 0)["npoz"].to_list())
        c.advanced = set(positions.filter(pl.col("adv") > 0)["npoz"].to_list()) - c.elected
    elif c.tip == CL or c.totals is not None:
        seats = lines.group_by("npoz").agg((pl.col("m1") + pl.col("m2")).sum().alias("s"))
        c.seats = {n: s for n, s in seats.iter_rows() if s > 0}


# ---------------------------------------------------------------- the years


@dataclass
class Year:
    year: str
    by_vote: dict[str, list[Contest]]
    files: list[str]


def build_1996() -> Year:
    votes = {v.name: v for v in VOTES_BY_YEAR["1996"]}
    first = contests_of("1996", votes["1996-06-02"], rows_1996("2iunie/2iunie.dbf"))
    second = contests_of("1996", votes["1996-06-16"], rows_1996("16iunie/16iunie.dbf"))
    third = contests_of("1996", votes["1996-06-30"], rows_1996("30iunie/30iunie.dbf"))
    for c in first + second + third:
        mark(c)
    went_on = {(c.tip, c.jud, c.circ): c for c in first if c.office and not c.elected}
    runoff, repeat = [], []
    for c in second:
        earlier = went_on.get((c.tip, c.jud, c.circ))
        if c.office and earlier is not None:
            c.round_number, c.previous = 2, earlier.name
            carried(c, earlier)
            runoff.append(c)
        else:
            c.vote = votes["1996-06-16-repeat"]
            repeat.append(c)
    again = {(c.tip, c.jud, c.circ): c for c in repeat if c.office and not c.elected}
    for c in third:
        earlier = again.get((c.tip, c.jud, c.circ)) or went_on.get((c.tip, c.jud, c.circ))
        if earlier is None:
            raise ValueError(f"1996: a second round of 30 June without a first round: {c.name}")
        c.round_number, c.previous = 2, earlier.name
        carried(c, earlier)
    # The county councils and the General Mayor carry their seats and winner in the declared table, with the day on
    # which the county bureau declared them.
    declared = declared_1996()
    days = {"2 iunie": "1996-06-02", "16 iunie": "1996-06-16"}
    for c in [*first, *repeat, *runoff]:
        if c.tip not in (CJ, TOP):
            continue
        rows = declared.filter((pl.col("jud") == c.jud) & (pl.col("tip") == c.tip))
        if not rows.height:
            raise ValueError(f"1996: no declared result for {c.name}")
        day = days[rows["data"][0]]
        if c.vote.name.removesuffix("-repeat") != day or (c.tip == TOP and c.round_number == 1 and day != "1996-06-02"):
            if c.tip == CJ:
                c.note = (
                    "The Authority's table of results declared by the county bureaus records the council of this "
                    f"county as elected in the vote of {rows['data'][0]} 1996, so this count carries no seats."
                )
            continue
        c.totals = rows.select(
            "npoz", "codu", "ta", "tapu", "tvve", "tvn", "vve", pl.col("seats").alias("m1"), pl.lit(0).alias("m2")
        ).with_columns((pl.col("npoz") == 0).alias("total"), pl.lit(0).alias("adv"))
        if c.office:
            c.elected = set(c.totals.filter(~pl.col("total") & (pl.col("m1") > 0))["npoz"].to_list())
        else:
            c.seats = {n: s for n, s in c.totals.filter(~pl.col("total")).select("npoz", "m1").iter_rows() if s > 0}
    return Year(
        "1996",
        {"1996-06-02": first, "1996-06-16": runoff, "1996-06-16-repeat": repeat, "1996-06-30": third},
        ["1996"],
    )


def build_x(year: str) -> Year:
    votes = VOTES_BY_YEAR[year]
    third = {"2004": ["xbaza3.DBF"], "2008": ["xbaza3_20.DBF", "xbaza3_36.dbf"]}[year]
    first = contests_of(year, votes[0], rows_x(year, "xbaza.DBF"))
    second = contests_of(year, votes[1], rows_x(year, "xbaza2.DBF"))
    later = [c for file in third for c in contests_of(year, votes[2], rows_x(year, file))]
    for c in first + second + later:
        mark(c)
    went_on = {(c.tip, c.jud, c.circ): c for c in first if c.office}
    for c in second + later:
        earlier = went_on.get((c.tip, c.jud, c.circ))
        if earlier is None or not c.office:
            raise ValueError(f"{year}: a second round without a first round: {c.name}")
        c.round_number, c.previous = 2, earlier.name
        carried(c, earlier)
    return Year(year, {votes[0].name: first, votes[1].name: second, votes[2].name: later}, [year])


def carried(c: Contest, earlier: Contest) -> None:
    """Name the second-round candidates the rows leave blank after the first-round candidate of the same party."""
    by_party: dict[int, list[str]] = {}
    for codu, name in earlier.lines().filter(pl.col("codu") != INDEPENDENT).select("codu", "name").iter_rows():
        by_party.setdefault(codu, []).append(name)
    for npoz, codu, name in c.lines().select("npoz", "codu", "name").iter_rows():
        if not name and len(by_party.get(codu, [])) == 1 and by_party[codu][0]:
            c.names[npoz] = by_party[codu][0]


# ---------------------------------------------------------------- the registry


PARTY_LABELS: dict[str, str] = {}


def party(label: str) -> str:
    PARTY_LABELS.setdefault(fold(label), tidy(label))
    return key("party", "local", fold(label))


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Romanian parties, alliances, and minority organizations on the ballots of the local elections of 1996, "
        "2004, and 2008, and the local councils, county councils, and mayors they elected, from the Permanent "
        "Electoral Authority's archive",
    )
    p.external.add(ROMANIA)
    p.add(
        "source",
        id=LOCAL_SOURCE,
        kind="official_body",
        publisher="Autoritatea Electorală Permanentă",
        title="Databases of past local elections of the Permanent Electoral Authority (roaep.ro)",
        url=f"{roaep.SITE}/prezentare/baze-de-date-alegeri-electorale/",
        attribution="Autoritatea Electorală Permanentă",
    )
    for url in URLS.values():
        p.artifact(raw.cached(SOURCE, url), LOCAL_SOURCE)
    for body, english, romanian in (
        (LOCAL_COUNCIL, "Local councils of Romania", "Consiliile locale"),
        (COUNTY_COUNCIL, "County councils of Romania", "Consiliile județene"),
        (BUCHAREST_COUNCIL, "General Council of Bucharest", "Consiliul General al Municipiului București"),
    ):
        p.add("body", id=body, polity_id=ROMANIA, kind="council")
        p.name(body, english, "en", "official")
        p.name(body, romanian, "ro", "native", False)
    for office, kind, english, romanian in (
        (MAYOR, "mayor", "Mayor of a Romanian municipality, town, commune, or Bucharest sector", "Primar"),
        (GENERAL_MAYOR, "mayor", "General Mayor of Bucharest", "Primarul General al Municipiului București"),
        (COUNTY_PRESIDENT, "other", "President of a county council", "Președintele consiliului județean"),
    ):
        p.add("office", id=office, polity_id=ROMANIA, kind=kind)
        p.name(office, english, "en", "official")
        p.name(office, romanian, "ro", "native", False)
    for system, rules, text in (
        (
            TWO_ROUND,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A candidate who wins a majority of the valid votes in the first round is elected; otherwise the two "
                "leading candidates meet in a second round two weeks later, which the candidate with the most votes "
                "wins."
            ),
        ),
        (PLURALITY, {"formula": "fptp"}, "The candidate with the most votes in a single round is elected."),
        (
            LISTS,
            {"formula": "hare"},
            (
                "Seats are allocated to the lists and independent candidates that pass the threshold, first by the "
                "electoral coefficient (the valid votes for them divided by the seats) and then from the unused "
                "votes. The threshold and the order in which unused votes are counted are not stored here."
            ),
        ),
    ):
        p.add("electoral_system", id=system, polity_id=ROMANIA, rules=rules, rules_version=1)
        p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=text, lang="en")
    for name, label in sorted(PARTY_LABELS.items()):
        pid = key("party", "local", name)
        bloc = name.startswith(("ALIANTA", "CONVENTIA", "UNIUNEA SOCIAL DEMOCRATA"))
        p.add("party", id=pid, kind="bloc" if bloc else "party", polity_id=ROMANIA)
        p.name(pid, label, "ro", "official")
    return p


# ---------------------------------------------------------------- the packages


CLASSES = {
    P: ("mayor", "Mayor", "Primar"),
    CL: ("local-council", "Local council", "Consiliul local"),
    CJ: ("county-council", "County council", "Consiliul județean"),
}


def describe(c: Contest) -> tuple[str, str, str, dict[str, object]]:
    """The class slug, English and Romanian names, and the contest's holder fields."""
    if c.tip == TOP and c.bucharest:
        fields: dict[str, object] = {"office_id": GENERAL_MAYOR, "electoral_system_id": TWO_ROUND}
        return "general-mayor", "General Mayor of Bucharest", "Primarul General al Municipiului București", fields
    if c.tip == TOP:
        fields = {"office_id": COUNTY_PRESIDENT, "electoral_system_id": PLURALITY}
        return (
            "county-council-president",
            "President of the county council",
            "Președintele consiliului județean",
            fields,
        )
    if c.tip == CJ and c.bucharest:
        fields = {"body_id": BUCHAREST_COUNCIL, "electoral_system_id": LISTS}
        return (
            "bucharest-council",
            "General Council of Bucharest",
            "Consiliul General al Municipiului București",
            fields,
        )
    slug, english, romanian = CLASSES[c.tip]
    if c.tip == P:
        fields: dict[str, object] = {"office_id": MAYOR, "electoral_system_id": TWO_ROUND}
    else:
        fields = {"body_id": COUNTY_COUNCIL if c.tip == CJ else LOCAL_COUNCIL, "electoral_system_id": LISTS}
    return slug, english, romanian, fields


class Builder:
    def __init__(self, p: Package, vote: Vote, minted: set[str]) -> None:
        self.p = p
        self.vote = vote
        self.minted = minted
        self.units: dict[str, tuple[str, str, str, str]] = {}
        self.slugs: set[str] = set()

    def unit(self, jud: int, circ: int, sv: int, place: str) -> str:
        if sv:
            code = f"{self.vote.name}:SV/{jud}/{circ}/{sv}"
            label = f"{place_name(place)} (station {sv})"
            scheme, kind = STATIONS, "standard"
        else:
            code = f"{self.vote.name}:CE/{jud}/{circ}"
            label = place_name(place)
            scheme, kind = CIRCUMSCRIPTIONS, "aggregate"
        if code not in self.units:
            self.units[code] = (key("unit", code), scheme, kind, label)
        return self.units[code][0]

    def stage_units(self) -> None:
        p = self.p
        rows = list(self.units.items())
        ids = [u[0] for _, u in rows]
        p.frame(
            "reporting_unit",
            pl.DataFrame(
                {
                    "id": ids,
                    "polity_id": ROMANIA,
                    "scheme_code": [u[1] for _, u in rows],
                    "code": [c for c, _ in rows],
                    "area_relation": "unlocated",
                    "kind": [u[2] for _, u in rows],
                }
            ),
        )
        p.frame(
            "name",
            pl.DataFrame(
                {
                    "id": [p.id("name", u[0], "ro", "official", u[3]) for _, u in rows],
                    "entity_id": ids,
                    "text": [u[3] for _, u in rows],
                    "lang": "ro",
                    "kind": "official",
                    "is_preferred": True,
                }
            ),
        )

    def slug(self, c: Contest, cls: str) -> str:
        county = slugify(county_name(c.county)) or str(c.jud)
        base = f"{self.vote.slug}/{cls}/{county}"
        if c.circ:
            base += f"/{slugify(place_name(c.place)) or c.circ}"
        path = base if base not in self.slugs else f"{base}-{c.circ}"
        self.slugs.add(path)
        return path

    def person(self, c: Contest, name: str) -> str:
        who = key("person", c.year, c.jud, c.circ, c.tip, fold(name))
        if who not in self.minted:
            self.minted.add(who)
            self.p.add("person", id=who)
            self.p.name(who, title(name), "ro", "official")
        return who

    def contest(self, c: Contest, election: str) -> None:
        p = self.p
        cls, english, romanian, fields = describe(c)
        county = county_name(c.county)
        where = f"{place_name(c.place)} ({county})" if c.circ else ("" if c.bucharest else f"{county} County")
        where_ro = f"{place_name(c.place)} ({county})" if c.circ else ("" if c.bucharest else county)
        suffix = {1: "", 2: ", second round"}[c.round_number]
        cid = key("contest", c.name)
        p.add(
            "contest",
            id=cid,
            election_id=election,
            kind="office" if c.office else "list_allocation",
            round_number=c.round_number,
            previous_round_id=key("contest", c.previous) if c.previous else None,
            default_result_kind="counted",
            magnitude=1 if c.office else None,
            **fields,
        )
        year = c.vote.day.year
        p.name(
            cid,
            f"{year} Romanian local elections, {english}" + (f", {where}" if where else "") + suffix,
            "en",
            "official",
        )
        p.name(
            cid,
            romanian
            + (f", {where_ro}" if where_ro else "")
            + (", turul al doilea" if c.round_number == 2 else "")
            + f", {year}",
            "ro",
            "native",
            False,
        )
        p.add("slug", path=self.slug(c, cls), entity_id=cid, is_current=True)
        rid = key("result_set", c.name)
        p.add(
            "result_set",
            id=rid,
            election_id=election,
            contest_id=cid,
            stage="official",
            kind="counted",
            source_id=LOCAL_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add(
            "note",
            id=key("note", rid),
            entity_id=rid,
            kind="methodology",
            body_md=METHOD[c.year] + (f" {c.note}" if c.note else ""),
            lang="en",
        )
        options = self.options(c, cid)
        self.values(c, rid, options)
        self.outcomes(c, rid, options)

    def options(self, c: Contest, cid: str) -> dict[int, str]:
        p = self.p
        lines = c.lines()
        if c.totals is not None:
            positions = set(c.totals.filter(~pl.col("total"))["npoz"].to_list())
            if positions - set(lines["npoz"].to_list()):
                raise ValueError(f"{c.name}: the totals print a ballot line the units do not")
        found: dict[int, str] = {}
        for npoz, codu, name, label in lines.select("npoz", "codu", "name", "party").iter_rows():
            oid = key("option", c.name, npoz)
            found[npoz] = oid
            independent = codu == INDEPENDENT
            candidate = c.office or independent
            person = candidate and bool(name)
            pid = None if independent or not label else party(label)
            p.add(
                "contest_option",
                id=oid,
                contest_id=cid,
                kind="candidate" if candidate else "list",
                party_id=pid,
                ballot_position=npoz,
                ballot_label=title(name) if person else label,
            )
            if person:
                p.add(
                    "candidacy",
                    id=key("candidacy", c.name, npoz),
                    person_id=self.person(c, name),
                    option_id=oid,
                    role="head",
                    ballot_name=title(name),
                    is_independent=independent,
                )
        return found

    def values(self, c: Contest, rid: str, options: dict[int, str]) -> None:
        units = c.units
        if units.select("jud", "circ", "sv", "npoz").is_duplicated().any():
            raise ValueError(f"{c.name}: a unit prints a ballot line twice")
        ids = [
            self.unit(j, ci, sv, place) for j, ci, sv, place in units.select("jud", "circ", "sv", "place").iter_rows()
        ]
        units = units.with_columns(pl.Series("unit", ids))
        minutes = units.filter(pl.col("total"))
        lines = units.filter(~pl.col("total"))
        parts = [
            minutes.select(
                "unit",
                pl.lit(None, pl.String).alias("option_id"),
                pl.lit(measure, pl.Int16).alias("measure"),
                pl.col(column).alias("value"),
            )
            for column, measure in (("ta", REGISTERED), ("tapu", CAST), ("tvve", VALID), ("tvn", INVALID))
        ]
        parts.append(
            lines.select(
                "unit",
                pl.col("npoz").replace_strict(options, return_dtype=pl.String).alias("option_id"),
                pl.lit(VOTES, pl.Int16).alias("measure"),
                pl.col("vve").alias("value"),
            )
        )
        stacked = pl.concat(parts)
        self.p.frame(
            "result_value",
            stacked.select(
                pl.lit(rid).alias("result_set_id"),
                pl.col("unit").alias("unit_id"),
                "option_id",
                pl.lit(0, pl.Int16).alias("channel"),
                "measure",
                pl.lit(0, pl.Int16).alias("count_round"),
                pl.lit(0, pl.Int16).alias("flags"),
                "value",
            ),
        )
        if c.totals is None:
            return
        totals = c.totals
        minutes_total = totals.filter(pl.col("total"))
        if minutes_total.height != 1:
            raise ValueError(f"{c.name}: the total prints its minutes {minutes_total.height} times")
        sums = lines.group_by("npoz").agg(pl.col("vve").sum())
        check = totals.filter(~pl.col("total")).join(sums, on="npoz", how="left", suffix="_units")
        bad = check.filter(pl.col("vve") != pl.col("vve_units"))
        if bad.height:
            raise ValueError(f"{c.name}: the units do not sum to the printed total: {bad.head(3).to_dicts()}")
        for column, measure in (("ta", REGISTERED), ("tapu", CAST), ("tvve", VALID), ("tvn", INVALID)):
            self.p.add(
                "declared", result_set_id=rid, option_id=None, measure=measure, value=int(minutes_total[column][0])
            )
        for npoz, vve in totals.filter(~pl.col("total")).select("npoz", "vve").iter_rows():
            self.p.add("declared", result_set_id=rid, option_id=options[npoz], measure=VOTES, value=int(vve))

    def outcomes(self, c: Contest, rid: str, options: dict[int, str]) -> None:
        for npoz in sorted(c.elected):
            self.p.add("outcome", result_set_id=rid, option_id=options[npoz], kind="elected", seats=1)
        for npoz in sorted(c.advanced):
            self.p.add("outcome", result_set_id=rid, option_id=options[npoz], kind="advanced", seats=None)
        for npoz, seats in sorted(c.seats.items()):
            self.p.add("outcome", result_set_id=rid, option_id=options[npoz], kind="won", seats=seats)


def election_package(vote: Vote, contests: list[Contest], minted: set[str]) -> Package:
    depends = [REGISTRY, *(f"{SOURCE}/local-{d}" for d in vote.depends)]
    p = Package(
        f"{SOURCE}/local-{vote.name}",
        SOURCE,
        f"{vote.english} ({vote.day.isoformat()}), by circumscription and polling station, from the Permanent "
        "Electoral Authority's archive",
        depends=depends,
    )
    p.external.update({ROMANIA, STATIONS, CIRCUMSCRIPTIONS})
    p.uses(raw.cached(SOURCE, URLS[vote.year]))
    eid = key("election", f"local-{vote.name}")
    fields: dict[str, object] = {}
    if vote.rerun_of:
        fields["rerun_of_id"] = key("election", f"local-{vote.rerun_of}")
    p.add(
        "election",
        id=eid,
        polity_id=ROMANIA,
        kind=vote.kind,
        date_from=vote.day,
        date_to=vote.day,
        status="held",
        **fields,
    )
    p.add("slug", path=vote.slug, entity_id=eid, is_current=True)
    p.name(eid, vote.english, "en", "official")
    p.name(eid, vote.romanian, "ro", "native", False)
    p.accept(
        "accounting.ballots_sum",
        "in some circumscriptions and polling stations the valid and null votes the Authority's tables print do not "
        "add up to the voters who took part; stored as printed",
    )
    b = Builder(p, vote, minted)
    for c in contests:
        b.contest(c, eid)
    b.stage_units()
    return p


def packages() -> list[str]:
    return PACKAGES


def build() -> list[Package]:
    out: list[Package] = []
    for year in (build_1996(), build_x("2004"), build_x("2008")):
        minted: set[str] = set()
        for vote in VOTES_BY_YEAR[year.year]:
            out.append(election_package(vote, year.by_vote[vote.name], minted))
    return [registry(), *out]


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(SOURCE, url, suffix=".zip")


def parse() -> None:
    for p in build():
        p.write()
