"""Folketing, European Parliament, and referendum results by polling district (afstemningsområde), 1979 to 2005,
and the municipal and regional councils by polling district, 2009 to 2021.

Statistics Denmark's election database (valgdatabasen, served at valgdatabase.dst.dk) holds every polling district's
count for the national elections it covers. Each election is one extract of five semicolon-separated files: the
election (Valg.csv), the parties (Partier.csv), the polling districts and the areas above them (Geografi.csv), how each
district maps to the geography the extract was cut on (Udregning.csv), and the counts (ValgData.csv). Every extract
used here maps each district to itself (no divergence and no merged groups), so the counts are the districts' own.

The Folketing and European Parliament elections and the 1998 and 2000 referendums are already held by the volumes
of Statistics Denmark and the Ministry of the Interior at nomination-district grain. Those sets stay the default and
keep the outcomes; this source adds the finer polling-district count to the same contests as a second official set,
with its completeness against the volume's valid votes. The 1986, 1992, and 1993 referendums are new elections.

The municipal and regional elections are read from the database's data service, which returns the same extract as
JSON for an election id. Their councils are held by Statistics Denmark's Statbank tables at council grain (`dk.dst`),
which builds the polling-district sets from these extracts as each council's default count, superseding its own
per-council set.
"""

import csv
import io
import json
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from pathlib import Path
from urllib.parse import quote
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.raw import Artifact
from ea.sources.dk import dst_folketing, dst_hist, ep_hist
from ea.store import DATA

SOURCE = "dk.valgdatabasen"
SITE = "https://valgdatabase.dst.dk/"
BROWSER = "browser://20261117-210113-0ae8d8/"
INBOX = DATA / "inbox" / SOURCE
DENMARK = str(uuid5(package.NAMESPACE, "polity\x1fDK"))
SCHEME = "dk.afstemningsomraade"
REGISTRY = f"{SOURCE}/registry"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
ACCOUNTING = {
    "Stemmeberettigede": REGISTERED,
    "Afgivne stemmer": CAST,
    "Blanke stemmer": BLANK,
    "Andre ugyldige stemmer": INVALID,
    "Gyldige stemmer": VALID,
}
ANSWERS = {"J": "yes", "N": "no"}
KINDS = {"FV": "fv", "EV": "ev", "FA": "fa"}


@dataclass(frozen=True)
class Vote:
    kind: str  # the database's category: FV (Folketing), EV (European Parliament), FA (referendum)
    day: date
    url: str = ""

    @property
    def file(self) -> str:
        return self.url or f"{BROWSER}dk-{KINDS[self.kind]}-{self.day}-voting-stations.zip"

    @property
    def held(self) -> bool:
        """Whether the election is already in the archive from the nomination-district volumes."""
        return self.kind != "FA" or self.day.year in (1998, 2000)


ELECTIONS = [
    Vote("FV", date(1979, 10, 23), "https://valgdatabase.dst.dk/data/20261117/2246bw18qw/data.zip"),
    *(
        Vote("FV", day)
        for day in (
            date(1981, 12, 8),
            date(1984, 1, 10),
            date(1987, 9, 8),
            date(1988, 5, 10),
            date(1990, 12, 12),
            date(1994, 9, 21),
            date(1998, 3, 11),
            date(2001, 11, 20),
            date(2005, 2, 8),
        )
    ),
    *(
        Vote("EV", day)
        for day in (
            date(1979, 6, 7),
            date(1984, 6, 14),
            date(1989, 6, 15),
            date(1994, 6, 9),
            date(1999, 6, 10),
            date(2004, 6, 13),
        )
    ),
    *(Vote("FA", day) for day in (date(1986, 2, 27), date(1992, 6, 2), date(1993, 5, 18), date(1998, 5, 28))),
    Vote("FA", date(2000, 9, 28)),
]
REFERENDUMS = {
    date(1986, 2, 27): (
        "1986 referendum on the Single European Act",
        "Folkeafstemningen den 27. februar 1986 om EF-pakken",
    ),
    date(1992, 6, 2): (
        "1992 referendum on the Maastricht Treaty",
        "Folkeafstemningen den 2. juni 1992 om Maastricht-traktaten",
    ),
    date(1993, 5, 18): (
        "1993 referendum on the Maastricht Treaty and the Edinburgh Agreement",
        "Folkeafstemningen den 18. maj 1993 om Maastricht-traktaten og Edinburgh-afgørelsen",
    ),
}
# The valid votes Statistics Denmark's printed volume gives for the whole country, for the referendums no other
# package holds: the measure the polling districts' completeness is taken against.
PRINTED_VALID = {date(1986, 2, 27): 2_898_269, date(1992, 6, 2): 3_259_731, date(1993, 5, 18): 3_402_305}
METHOD = (
    "Counted per polling district (afstemningsområde) and read from Statistics Denmark's election database "
    "(valgdatabasen), one extract per election cut on the election's own polling districts, so every count is the "
    "district's own. Registered voters are the voters entitled to vote, ballots cast are the ballots given in, and "
    "blank and other invalid ballots are kept apart; valid votes are the valid ballots. The extracts hold the "
    "districts of Denmark proper only, none in the Faroe Islands or Greenland. A district marked with a dash for a "
    "party, where the party did not stand there, holds no value."
)
INDEPENDENTS = (
    " The extract has no column for candidates standing outside the parties, so in districts where such candidates "
    "stood the party votes add up to less than the valid votes; the difference is those candidates' votes."
)
FINER = (
    " The nomination-district count from the printed volume remains this contest's default result and holds its "
    "outcome; this set adds the polling districts."
)
SHORT = (
    " The polling districts add up to {valid:,} valid votes, {gap:,} fewer than the {printed:,} that Statistics "
    "Denmark's printed volume gives for the whole country; the set's completeness is that share."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", "valgdatabasen")


def votes() -> list[Vote]:
    return sorted(ELECTIONS, key=lambda v: v.day)


def slug(vote: Vote) -> str:
    return f"{SOURCE}/{vote.day}"


def packages() -> list[str]:
    return [REGISTRY, *(slug(v) for v in votes())]


def fetch() -> None:
    """Fetch the municipal and regional extracts from the database's data service, load the extracts saved from valgdatabase.dst.dk (data/inbox/dk.valgdatabasen/) into the raw cache, and fetch
    the printed referendum volumes the new referendums' completeness is measured against."""
    fetch_local()
    for day in PRINTED_VALID:
        raw.fetch(dst_hist.SOURCE, dst_hist.FILES[str(day.year)], suffix=".pdf")
    index = json.loads((INBOX / "index.json").read_text())
    for name, entry in index.items():
        content = (INBOX / entry["file"]).read_bytes()
        raw.store(
            SOURCE,
            entry.get("source", name),
            content,
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


def parse() -> None:
    registry().write()
    for vote in votes():
        build(vote).write()


def emit() -> None:
    package.run("load", packages())


# Reading -------------------------------------------------------------------------------------------------------------


@dataclass
class District:
    code: str  # Valgsted Id: the municipality number and the district's number in it
    name: str
    municipality: str
    accounting: dict[int, int]
    votes: dict[str, int]  # the party letter (or J and N for a referendum) to its votes


def sheet(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    text = archive.read(name).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text, newline=""), delimiter=";"))


@cache
def districts(vote: Vote) -> tuple[list[District], dict[str, str]]:
    """The extract's polling districts in file order, and the party letters to the names the extract prints."""
    with zipfile.ZipFile(raw.cached(SOURCE, vote.file).path) as archive:
        (election,) = sheet(archive, "Valg.csv")
        if election["Kategori"] != vote.kind or election["Valgdag"] != vote.day.strftime("%Y%m%d"):
            raise ValueError(f"{vote.file} holds {election['Kategori']} {election['Valgdag']}, not {vote}")
        parties = {row["Partinavn"]: row["Parti"] for row in sheet(archive, "Partier.csv")}
        places = {row["Valgsted Id"]: row for row in sheet(archive, "Geografi.csv")}
        for row in sheet(archive, "Udregning.csv"):
            if row["Divergens"] != "0" or row["Multi"] != "1" or row["DivergensGruppe"] != row["GeoId"]:
                raise ValueError(f"{vote}: district {row['GeoId']} is not mapped to itself")
        rows = sheet(archive, "ValgData.csv")
    prefix = f"{vote.kind}{vote.day.year} - "
    found: list[District] = []
    for row in rows:
        if row["Group"] != row["VotingStationId"]:
            raise ValueError(f"{vote}: group {row['Group']} is not a single polling district")
        place = places[row["Group"]]
        accounting: dict[int, int] = {}
        counts: dict[str, int] = {}
        for column, cell in row.items():
            if not column.startswith(prefix):
                continue
            label = column.removeprefix(prefix)
            if cell == "-":
                continue
            value = int(cell)
            if label in ACCOUNTING:
                accounting[ACCOUNTING[label]] = value
            else:
                counts[parties[label]] = value
        if set(accounting) != set(ACCOUNTING.values()):
            raise ValueError(f"{vote}: district {row['Group']} lacks some of the accounting columns")
        if accounting[VALID] + accounting[INVALID] + accounting[BLANK] != accounting[CAST]:
            raise ValueError(f"{vote}: district {row['Group']} ballots do not add up")
        found.append(
            District(row["Group"], place["Valgsted navn"].strip(), place["Kommune navn"].strip(), accounting, counts)
        )
    codes = [d.code for d in found]
    if len(set(codes)) != len(codes):
        raise ValueError(f"{vote}: a polling district appears twice")
    letters = {
        letter: name
        for name, letter in parties.items()
        if any(column == prefix + name for column in rows[0]) and name not in ACCOUNTING
    }
    return found, letters


def totals(vote: Vote) -> dict[str | None, int]:
    """The national sums: the accounting measures under their measure number as text, and each letter's votes."""
    found, letters = districts(vote)
    sums: dict[str | None, int] = {}
    for measure in ACCOUNTING.values():
        sums[str(measure)] = sum(d.accounting[measure] for d in found)
    for letter in letters:
        sums[letter] = sum(d.votes.get(letter, 0) for d in found)
    return sums


# The contests already held -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    package: str
    election: str
    contest: str
    options: dict[str, str]  # the extract's letter to the held contest's option


def target(vote: Vote) -> Target:
    _, letters = districts(vote)
    if vote.kind == "FV":
        volume = dst_folketing.volume_of(vote.day)
        return Target(
            f"{dst_folketing.SOURCE}/{vote.day}",
            dst_folketing.key("election", volume.slug),
            dst_folketing.key("contest", volume.slug),
            {letter: dst_folketing.key("option", volume.slug, letter) for letter in letters},
        )
    if vote.kind == "EV":
        year = vote.day.year
        return Target(
            f"{ep_hist.SOURCE}/{vote.day}",
            ep_hist.key("election", year),
            ep_hist.key("contest", year, "parliament"),
            {letter: ep_hist.key("option", year, "parliament", letter) for letter in letters},
        )
    return Target(
        f"{dst_hist.SOURCE}/{vote.day}",
        dst_hist.key("election", vote.day),
        dst_hist.key("contest", vote.day, "question"),
        {letter: dst_hist.key("option", vote.day, "question", ANSWERS[letter]) for letter in letters},
    )


def printed_valid(held: Target) -> int:
    """The valid votes the held contest's volume prints for the whole country."""
    path = package.folder(held.package) / "build" / "declared.parquet"
    frame = pl.read_parquet(path).filter(
        (pl.col("option_id").is_null()) & (pl.col("measure") == VALID) & (pl.col("result_set_id").is_not_null())
    )
    sets = pl.read_parquet(package.folder(held.package) / "build" / "result_set.parquet")
    chosen = sets.filter(pl.col("contest_id") == held.contest)["id"].to_list()
    values = frame.filter(pl.col("result_set_id").is_in(chosen))["value"].to_list()
    if len(values) != 1:
        raise ValueError(f"{held.package}: expected one printed national valid figure, found {values}")
    return int(values[0])


# Packages ------------------------------------------------------------------------------------------------------------


def registry() -> Package:
    p = Package(REGISTRY, SOURCE, "Statistics Denmark's election database, the source of the polling-district counts")
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Danmarks Statistik",
        title="Valgdatabasen: elections by polling district",
        url=SITE,
        license="Statistics Denmark data may be reused with attribution to Statistics Denmark as the source",
        attribution="Danmarks Statistik",
        notes="Extracts cut on each election's own polling districts.",
    )
    return p


def label(vote: Vote) -> str:
    return {"FV": "Folketing election", "EV": "European Parliament election", "FA": "referendum"}[vote.kind]


def build(vote: Vote) -> Package:
    found, _ = districts(vote)
    held = target(vote) if vote.held else None
    depends = [REGISTRY, held.package] if held else [REGISTRY, f"{dst_hist.SOURCE}/registry"]
    p = Package(
        slug(vote),
        SOURCE,
        f"The {vote.day} Danish {label(vote)} by polling district, from Statistics Denmark's election database",
        depends=depends,
    )
    p.external.update({DENMARK, SCHEME})
    artifact = raw.cached(SOURCE, vote.file)
    p.artifact(artifact, SOURCE_ID)
    valid = sum(d.accounting[VALID] for d in found)
    if held:
        election, contest, options = held.election, held.contest, held.options
        printed = printed_valid(held)
    else:
        election, contest, options = new_referendum(p, vote)
        printed = PRINTED_VALID[vote.day]
        volume = raw.cached(dst_hist.SOURCE, dst_hist.FILES[str(vote.day.year)])
        p.artifact(volume, dst_hist.SOURCE_DST)
    if valid > printed:
        raise ValueError(f"{vote}: the polling districts hold {valid} valid votes, more than the printed {printed}")
    completeness = round(valid / printed, 6)
    result_set = key("result_set", vote.day)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=held is None,
        completeness=completeness,
    )
    independents = vote.kind == "FV" and any(sum(d.votes.values()) != d.accounting[VALID] for d in found)
    body = METHOD + (INDEPENDENTS if independents else "") + (FINER if held else "")
    if valid < printed:
        body += SHORT.format(valid=valid, gap=printed - valid, printed=printed)
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=body, lang="en")
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=SOURCE_ID,
        locator=json.dumps({"url": vote.file, "site": SITE, "files": ["ValgData.csv", "Geografi.csv"]}),
    )
    if independents:
        p.accept(
            "accounting.votes_valid",
            "The database's extract has no column for candidates standing outside the parties; in the districts "
            "where they stood, the party votes fall short of the valid votes by those candidates' votes.",
        )
    values: list[tuple[str, str | None, int, int]] = []
    for district in found:
        unit = key("unit", vote.day, district.code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=DENMARK,
            scheme_code=SCHEME,
            code=f"{KINDS[vote.kind]}-{vote.day}:{district.code}",
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, f"{district.name} ({district.municipality})", "da", "official")
        values += [(unit, None, measure, value) for measure, value in sorted(district.accounting.items())]
        values += [(unit, options[letter], VOTES, value) for letter, value in district.votes.items()]
    p.frame(
        "result_value",
        pl.DataFrame(values, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p


def new_referendum(p: Package, vote: Vote) -> tuple[str, str, dict[str, str]]:
    english, danish = REFERENDUMS[vote.day]
    election = key("election", vote.day)
    path = f"dk/{vote.day}-referendum"
    p.add(
        "election",
        id=election,
        polity_id=DENMARK,
        kind="referendum",
        date_from=vote.day,
        date_to=vote.day,
        status="held",
    )
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    p.name(election, danish, "da", "native", False)
    contest = key("contest", vote.day, "question")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question",
        electoral_system_id=dst_hist.SYSTEM,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, english, "en", "official")
    p.name(contest, danish, "da", "native", False)
    p.add("slug", path=f"{path}/question", entity_id=contest, is_current=True)
    p.add("ballot_question", contest_id=contest, type="referendum")
    options: dict[str, str] = {}
    for letter, text in (("J", "Ja"), ("N", "Nej")):
        options[letter] = key("option", vote.day, ANSWERS[letter])
        p.add("contest_option", id=options[letter], contest_id=contest, kind="answer", ballot_label=text)
    return election, contest, options


# Municipal and regional elections ------------------------------------------------------------------------------------

# The database's own ids for the municipal (KV) and regional (RV) elections, from its data service.
LOCAL = {
    date(2009, 11, 17): (70, 75),
    date(2013, 11, 19): (71, 76),
    date(2017, 11, 21): (277, 278),
    date(2021, 11, 16): (285, 286),
}
API = "https://valgdbtestapi.dst.dk/api/v1/HentData"
LOCAL_ACCOUNTING = {"BE": REGISTERED, "TO": CAST, "UB": BLANK, "UA": INVALID, "GY": VALID}
# The councils of the five regions (2007 to 2023) and the municipalities each takes in, by municipality number.
REGION_MUNICIPALITIES = {
    "084": (
        101,
        147,
        151,
        153,
        155,
        157,
        159,
        161,
        163,
        165,
        167,
        169,
        173,
        175,
        183,
        185,
        187,
        190,
        201,
        210,
        217,
        219,
        223,
        230,
        240,
        250,
        260,
        270,
        400,
        411,
    ),
    "085": (253, 259, 265, 269, 306, 316, 320, 326, 329, 330, 336, 340, 350, 360, 370, 376, 390),
    "083": (
        410,
        420,
        430,
        440,
        450,
        461,
        479,
        480,
        482,
        492,
        510,
        530,
        540,
        550,
        561,
        563,
        573,
        575,
        580,
        607,
        621,
        630,
    ),
    "082": (615, 657, 661, 665, 671, 706, 707, 710, 727, 730, 740, 741, 746, 751, 756, 760, 766, 779, 791),
    "081": (773, 787, 810, 813, 820, 825, 840, 846, 849, 851, 860),
}
REGION_OF = {f"{number}": region for region, numbers in REGION_MUNICIPALITIES.items() for number in numbers}
LOCAL_METHOD = (
    "Counted per polling district (afstemningsområde) and read from Statistics Denmark's election database "
    "(valgdatabasen) through its data service, one extract per election cut on the election's own polling districts, "
    "so every count is the district's own. Registered voters are the voters entitled to vote, ballots cast are the "
    "ballots given in, and blank and other invalid ballots are kept apart; valid votes are the valid ballots. The "
    "database gives each list its ballot letter; the letters of the national parties are kept as their lists, and "
    "the letters of local lists are summed into the lists without a reserved letter, as in the per-council count "
    "this set supersedes. Seats are those of the per-council count."
)


def local_url(valg: int, parties: list[str]) -> str:
    """The data service's address for one election's polling districts and the given list codes."""
    codes = quote(";".join(parties), safe=";")
    return f"{API}?valg={valg}&maxdiv=1&kortenavne=false&pers=false&mandater=false&geoniveau=0&partier={codes}"


def local_codes(valg: int) -> list[str]:
    """Every list and accounting code of the election, read from a small probe extract."""
    probe = json.loads(raw.cached(SOURCE, local_url(valg, ["BE"])).path.read_text())
    return [str(row["partiKode"]) for row in probe["partier"]]


def fetch_local() -> None:
    for kv, rv in LOCAL.values():
        for valg in (kv, rv):
            raw.fetch(SOURCE, local_url(valg, ["BE"]), suffix=".json")
            raw.fetch(SOURCE, local_url(valg, local_codes(valg)), suffix=".json")


@dataclass
class LocalDistrict:
    code: str  # Valgsted Id: the municipality number and the district's number in it
    name: str
    municipality: str  # the municipality number
    municipality_name: str
    accounting: dict[int, int]
    votes: dict[str, int]  # the list letter to its votes


def local_artifact(valg: int) -> Artifact:
    return raw.cached(SOURCE, local_url(valg, local_codes(valg)))


@cache
def local_districts(valg: int, day: date, kind: str) -> list[LocalDistrict]:
    """One municipal (KV) or regional (RV) election's polling districts in the extract's order."""
    data = json.loads(local_artifact(valg).path.read_text())
    (election,) = data["valg"]
    if election["kategori"] != kind or election["valgdag"][:10] != day.isoformat():
        raise ValueError(f"election {valg} is {election['kategori']} {election['valgdag']}, not {kind} {day}")
    for row in data["udregning"]:
        if row["divergens"] != 0 or row["multi"] != 1 or row["refValgId"] != valg:
            raise ValueError(f"{kind} {day}: district {row['valgstedId']} is not mapped to itself")
    places = {str(row["valgstedId"]): row for row in data["geografi"]}
    names = {str(row["partinavn"]): str(row["partiKode"]) for row in data["partier"]}
    prefix = f"{kind}{day.year} - "
    cells: dict[str, dict[str, int]] = defaultdict(dict)
    for column in data["valgData"]:
        label = str(column["navn"]).strip('"').removeprefix(prefix)
        code = names[label]
        for cell in column["list"]:
            group, value = str(cell["gruppe"]), cell["vaerdi"]
            if str(cell["valgstedId"]) != group:
                raise ValueError(f"{kind} {day}: group {group} is not a single polling district")
            if value in (None, "", "-"):
                continue
            cells[group][code] = int(value)
    found: list[LocalDistrict] = []
    for group, row in cells.items():
        accounting = {measure: row[code] for code, measure in LOCAL_ACCOUNTING.items() if code in row}
        # some districts print no registered voters (a dash); every ballot count is there
        if set(accounting) | {REGISTERED} != set(LOCAL_ACCOUNTING.values()):
            raise ValueError(f"{kind} {day}: district {group} lacks some of the ballot columns")
        if accounting[VALID] + accounting[INVALID] + accounting[BLANK] != accounting[CAST]:
            raise ValueError(f"{kind} {day}: district {group} ballots do not add up")
        votes = {code: value for code, value in row.items() if code not in LOCAL_ACCOUNTING}
        place = places[group]
        found.append(
            LocalDistrict(
                group,
                str(place["valgstedNavn"]).strip(),
                str(place["kommuneNr"]),
                str(place["kommuneNavn"]).strip(),
                accounting,
                votes,
            )
        )
    return found
