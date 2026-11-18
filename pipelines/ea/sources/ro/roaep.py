"""Romanian parliamentary and presidential elections of 1990 to 2014, from the Permanent Electoral Authority's archive.

The Authority's site keeps a database of past elections: one archive per cycle with the files of the computer
application that counted it. From 1992 to 2004 these are FoxPro tables with one row per polling station and one
column per competitor; 2008 adds Excel tables of the stations, colleges, and candidates; 2009 is a FoxPro table per
round of the presidential election; and 2014 is a set of Excel tables per round. For 1990 the site publishes Excel
tables with the votes of every competitor in each county, the competitors that took seats, and the list of members
elected, so 1990 is held by county.

From 1990 to 2004 the deputies and senators were elected from party lists in each county (circumscription), so a
contest is one county of one chamber and its options are the lists. In 2008 they were elected in single-member
colleges, so a contest is one college of one chamber and its options are the candidates, as the 2012 election that
`ro.aep` holds is shaped. The presidential election is one contest per round.
"""

import difflib
import io
import re
import struct
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "ro.roaep"
ROMANIA = str(uuid5(package.NAMESPACE, "polity\x1fRO"))
SITE = "https://www.roaep.ro"
ARCHIVE = f"{SITE}/prezentare/wp-content/uploads/2025/02"
Y1990 = f"{SITE}/alegeri_1990/wp-content/uploads"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
STATIONS, COUNTIES = "ro.sv", "ro.circumscription"

URLS = {
    "1992": f"{ARCHIVE}/elect_1992.zip",
    "1996": f"{ARCHIVE}/elect_1996.zip",
    "2000": f"{ARCHIVE}/elect_2000.zip",
    "2004": f"{ARCHIVE}/elect_2004.zip",
    "2008": f"{ARCHIVE}/elect_2008.zip",
    "2009": f"{ARCHIVE}/prez_2009.zip",
    "2014": f"{ARCHIVE}/Alegeri-prezidentiale-2014.zip",
    "1990-cd": f"{Y1990}/vve_competitori_circumscriptii_deputati.xlsx",
    "1990-s": f"{Y1990}/vve_competitori_circumscriptii_senatori.xlsx",
    "1990-p": f"{Y1990}/vve_competitori_circumscriptii_presedinte.xlsx",
    "1990-cd-members": f"{Y1990}/mandate_nominale_deputati.xlsx",
    "1990-s-members": f"{Y1990}/mandate_nominale_senatori.xlsx",
}
SUFFIX = {".zip": ".zip", ".xlsx": ".xlsx"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ARCHIVE_SOURCE = key("source", "archive")
CHAMBER = key("body", "chamber")
SENATE = key("body", "senate")
PRESIDENT = key("office", "president")
TWO_ROUND = key("electoral_system", "two-round")

INDEPENDENTS = ("Candidați independenți", "independents")
METHOD = {
    "1990": (
        "Votes of each competitor in each county (electoral circumscription), from the Authority's tables of the "
        "1990 election. The tables print no registered voters, voters, or null votes. The members elected and the "
        "seats are those of the Authority's list of members elected in each county."
    ),
    "1992": (
        "Counted per polling station, from the tables of the Authority's ELECT'92 application. Registered voters are "
        "the voters on the permanent lists, ballots cast are the voters who took part (on the permanent and the "
        "special lists), valid votes are the valid votes and invalid ballots the null votes. When the null votes were "
        "checked again, the county bureaus found some of them valid; the Authority prints those votes for each county "
        "and competitor apart from the stations, and they are stored in a unit of their own for each county, with no "
        "figures but the votes, so the station rows stay as the stations counted them."
    ),
    "1996": (
        "Counted per polling station, from the tables of the Authority's ELECT'96 application. Registered voters are "
        "the voters on the permanent lists, ballots cast are the voters who took part (on the permanent and the "
        "special lists), valid votes are the valid votes and invalid ballots the null votes."
    ),
    "2000": (
        "Counted per polling station, from the Authority's tables of the 2000 elections. Registered voters are the "
        "voters on the permanent lists, ballots cast are the voters who took part (on the permanent and the special "
        "lists), valid votes are the valid votes and invalid ballots the null votes. Stations abroad belong to the "
        "Bucharest circumscription."
    ),
    "2004": (
        "Counted per polling station, from the Authority's tables of the 2004 elections. Registered voters are the "
        "voters on the copy of the permanent list and on the special lists of the first type, ballots cast are the "
        "voters who took part, valid votes are the valid votes and invalid ballots the null votes. The county totals "
        "the Authority prints are stored as declared. Stations abroad belong to the Bucharest circumscription, and the "
        "tables name the Bucharest sector that counted them in place of their own location."
    ),
    "2008": (
        "Counted per polling station, from the Authority's tables of the 2008 elections. Registered voters are the "
        "voters on the lists of the station, ballots cast are the voters who took part, valid votes are the valid "
        "votes, invalid ballots the null votes, and blank ballots the blank votes. The college totals the Authority "
        "prints are stored as declared. The tables sum the votes of all independent candidates of a college into one "
        "column; where a college had a single independent candidate the column is that candidate's, and otherwise it "
        "is stored as the independent candidates together. The tables do not say who took a seat."
    ),
    "2009": (
        "Counted per polling station, from the Authority's table of each round. Registered voters are the voters on "
        "the copy of the permanent list, ballots cast are the voters who took part, valid votes are the valid votes "
        "and invalid ballots the null votes. Stations abroad print no registered voters, so theirs are left empty."
    ),
    "2014": (
        "Counted per polling station, from the Authority's tables of each round. Registered voters are the voters on "
        "the permanent lists, ballots cast are the voters who took part, valid votes are the valid votes and invalid "
        "ballots the null votes. Stations abroad print no registered voters, so theirs are left empty. The county "
        "totals the Authority prints are stored as declared."
    ),
}


# ---------------------------------------------------------------- reading


def fold(text: object) -> str:
    """A key for a printed name: capitals without diacritics or punctuation, with î and â read alike."""
    value = str(text or "").upper().replace("Â", "Î").replace("Ã", "Ă")
    value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", value).split())


def tidy(text: object) -> str:
    value = str(text or "").translate(str.maketrans({"Ş": "Ș", "ş": "ș", "Ţ": "Ț", "ţ": "ț", "ã": "ă", "Ã": "Ă"}))
    return " ".join(value.split())


def title(text: str) -> str:
    return "".join(part.capitalize() if part.isupper() else part for part in re.split(r"(?<=[ -])", tidy(text)))


def archive(name: str) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, URLS[name]).path.read_bytes()))


def member(book: zipfile.ZipFile, name: str) -> bytes:
    for info in book.infolist():
        if info.filename.rsplit("/", 1)[-1].lower() == name.lower():
            return book.read(info)
    raise LookupError(name)


def dbf(content: bytes, encoding: str = "cp1250") -> pl.DataFrame:
    """A dBase table, every column as text."""
    count, header, width = struct.unpack("<IHH", content[4:12])
    fields: list[tuple[str, int]] = []
    offset = 32
    while content[offset] != 0x0D:
        fields.append((content[offset : offset + 11].split(b"\0")[0].decode("ascii").upper(), content[offset + 16]))
        offset += 32
    columns: dict[str, list[str]] = {name: [] for name, _ in fields}
    for row in range(count):
        record = content[header + row * width : header + (row + 1) * width]
        if record[:1] == b"*":
            continue
        position = 1
        for name, size in fields:
            columns[name].append(record[position : position + size].decode(encoding, errors="replace").strip())
            position += size
    return pl.DataFrame(columns)


def sheet(content: bytes, name: str | None = None) -> list[list[str | None]]:
    book = fastexcel.read_excel(content)
    frame = book.load_sheet_by_name(name or book.sheet_names[0], header_row=None, dtypes="string").to_polars()
    return [[None if cell is None else str(cell) for cell in row] for row in frame.rows()]


def integer(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    return int(float(text))


@dataclass
class Table:
    """One station table: `frame` holds circ, nr, place, abroad, the accounting columns, and the option columns."""

    frame: pl.DataFrame
    options: list[str]


def table(frame: pl.DataFrame, columns: dict[str, str], options: list[str], abroad: pl.Expr | None = None) -> Table:
    """Name the accounting columns (`columns` maps our name to the printed one) and cast the rest to integers."""
    named = frame.select(
        pl.col(columns["circ"]).cast(pl.Int64).alias("circ"),
        pl.col(columns["nr"]).cast(pl.Int64).alias("nr"),
        (pl.col(columns["place"]) if "place" in columns else pl.lit(None, pl.String)).alias("place"),
        (abroad if abroad is not None else pl.lit(False)).alias("abroad"),
        *(
            (pl.col(columns[m]).cast(pl.Float64).cast(pl.Int64) if m in columns else pl.lit(None, pl.Int64)).alias(m)
            for m in ("registered", "cast", "valid", "invalid", "blank")
        ),
        *(pl.col(o).replace("", "0").cast(pl.Float64).cast(pl.Int64).alias(o) for o in options),
    )
    named = named.with_columns(
        pl.when(pl.col("abroad") & (pl.col("registered") == 0)).then(None).otherwise(pl.col("registered"))
    )
    if named.select("circ", "nr").is_duplicated().any():
        raise ValueError("a polling station repeats")
    return Table(named, options)


# ---------------------------------------------------------------- the build


@dataclass
class Line:
    """A ballot line: `column` in the station table, its label, its kind, and its party or person."""

    column: str
    label: str
    kind: str
    party: str | None = None
    person: str | None = None
    position: int | None = None


class Build:
    def __init__(self, p: Package, day: str, counties: dict[int, str]) -> None:
        self.p = p
        self.day = day
        self.counties = counties
        self.units: dict[str, tuple[str, str, str, str]] = {}

    def county(self, circ: int) -> str:
        return self.counties[circ]

    def station_units(self, tables: list[Table]) -> None:
        for t in tables:
            for circ, nr, place, abroad in t.frame.select("circ", "nr", "place", "abroad").iter_rows():
                code = f"{self.day}:{circ}/{nr}"
                if code in self.units:
                    continue
                where = title(place) if place else ""
                county = "abroad" if abroad else self.county(circ)
                label = f"{where} ({county}, station {nr})" if where else f"{county.capitalize()}, station {nr}"
                self.units[code] = (key("unit", code), STATIONS, "abroad_mission" if abroad else "standard", label)

    def aggregate(self, code: str, label: str) -> str:
        full = f"{self.day}:{code}"
        if full not in self.units:
            self.units[full] = (key("unit", full), COUNTIES, "aggregate", label)
        return self.units[full][0]

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

    def unit_ids(self, frame: pl.DataFrame) -> pl.Series:
        return pl.Series(
            "unit", [key("unit", f"{self.day}:{c}/{n}") for c, n in frame.select("circ", "nr").iter_rows()]
        )


def election(p: Package, name: str, day: date, kind: str, english: str, romanian: str, path: str) -> str:
    eid = key("election", name)
    p.add("election", id=eid, polity_id=ROMANIA, kind=kind, date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=eid, is_current=True)
    p.name(eid, english, "en", "official")
    p.name(eid, romanian, "ro", "native", False)
    return eid


def contest(
    p: Package,
    name: str,
    eid: str,
    english: str,
    romanian: str,
    path: str,
    method: str,
    **fields: object,
) -> tuple[str, str]:
    cid = key("contest", name)
    p.add(
        "contest",
        id=cid,
        election_id=eid,
        round_number=fields.pop("round_number", 1),
        default_result_kind="counted",
        **fields,
    )
    p.name(cid, english, "en", "official")
    p.name(cid, romanian, "ro", "native", False)
    p.add("slug", path=path, entity_id=cid, is_current=True)
    rid = key("result_set", name)
    p.add(
        "result_set",
        id=rid,
        election_id=eid,
        contest_id=cid,
        stage="official",
        kind="counted",
        source_id=ARCHIVE_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", rid), entity_id=rid, kind="methodology", body_md=method, lang="en")
    return cid, rid


def options(p: Package, name: str, cid: str, lines: list[Line]) -> dict[str, str]:
    """Add the ballot lines of a contest; return the option id of each station column."""
    found: dict[str, str] = {}
    for line in lines:
        oid = key("option", name, line.column)
        found[line.column] = oid
        p.add(
            "contest_option",
            id=oid,
            contest_id=cid,
            kind=line.kind,
            party_id=line.party,
            ballot_position=line.position,
            ballot_label=line.label,
        )
        if line.person:
            who = line.person
            p.add(
                "candidacy",
                id=key("candidacy", name, line.column),
                person_id=who,
                option_id=oid,
                role="head",
                ballot_name=line.label,
                is_independent=line.party is None,
            )
    return found


def values(p: Package, rid: str, frame: pl.DataFrame, found: dict[str, str], accounting: bool = True) -> None:
    """Stage the accounting and option values of each row of `frame` (which holds `unit`)."""
    parts = []
    if accounting:
        for column, measure in (
            ("registered", REGISTERED),
            ("cast", CAST),
            ("valid", VALID),
            ("invalid", INVALID),
            ("blank", BLANK),
        ):
            if column in frame.columns:
                parts.append(
                    frame.select(
                        "unit",
                        pl.lit(None, pl.String).alias("option_id"),
                        pl.lit(measure, pl.Int16).alias("measure"),
                        pl.col(column).cast(pl.Int64).alias("value"),
                    )
                )
    for column, oid in found.items():
        parts.append(
            frame.select(
                "unit",
                pl.lit(oid).alias("option_id"),
                pl.lit(VOTES, pl.Int16).alias("measure"),
                pl.col(column).cast(pl.Int64).alias("value"),
            )
        )
    stacked = pl.concat(parts).filter(pl.col("value").is_not_null())
    p.frame(
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


def declare(p: Package, rid: str, totals: dict[int | str, int]) -> None:
    """`totals` maps a measure (int) or an option id (str) to the total the Authority prints."""
    for what, value in totals.items():
        if isinstance(what, int):
            p.add("declared", result_set_id=rid, option_id=None, measure=what, value=value)
        else:
            p.add("declared", result_set_id=rid, option_id=what, measure=VOTES, value=value)


def accept(p: Package) -> None:
    p.accept(
        "accounting.ballots_sum",
        "some stations print valid and null votes that do not add up to the voters who took part; stored as printed",
    )
    p.accept(
        "accounting.turnout",
        "some stations print more voters than voters on the permanent lists, because voters on the special lists "
        "are not counted among the registered voters; stored as printed",
    )


# ---------------------------------------------------------------- the registry


PARTY_LABELS: dict[str, str] = {}


def party(label: str) -> str:
    """The party of a printed label; the registry holds every label any year prints."""
    PARTY_LABELS.setdefault(fold(label), tidy(label))
    return key("party", fold(label))


def person(name: str) -> str:
    return key("person", fold(name))


PEOPLE: dict[str, str] = {}


def candidate(name: str) -> str:
    """A presidential candidate, who can stand in more than one election, so the registry holds them."""
    who = person(name)
    PEOPLE[who] = title(name)
    return who


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Romanian parties, alliances, minority organizations, and presidential candidates on the ballots of 1990 to "
        "2014, the two chambers of Parliament and the presidency, from the Permanent Electoral Authority's archive",
    )
    p.external.add(ROMANIA)
    p.add(
        "source",
        id=ARCHIVE_SOURCE,
        kind="official_body",
        publisher="Autoritatea Electorală Permanentă",
        title="Archive of past elections of the Permanent Electoral Authority (roaep.ro)",
        url=SITE,
        attribution="Autoritatea Electorală Permanentă",
    )
    for body, kind, english, romanian in (
        (CHAMBER, "lower_house", "Chamber of Deputies of Romania", "Camera Deputaților"),
        (SENATE, "upper_house", "Senate of Romania", "Senatul României"),
    ):
        p.add("body", id=body, polity_id=ROMANIA, kind=kind)
        p.name(body, english, "en", "official")
        p.name(body, romanian, "ro", "native", False)
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
        body_md="A candidate who wins a majority of the registered voters in the first round is elected; otherwise the "
        "two leading candidates meet in a second round, which the candidate with the most votes wins.",
        lang="en",
    )
    for name in sorted(PARTY_LABELS):
        label = PARTY_LABELS[name]
        pid = key("party", name)
        p.add("party", id=pid, kind="bloc" if name.startswith(("ALIANTA", "CONVENTIA")) else "party", polity_id=ROMANIA)
        p.name(pid, label, "ro", "official")
    for who, text in sorted(PEOPLE.items(), key=lambda item: item[1]):
        p.add("person", id=who)
        p.name(who, text, "ro", "official")
    return p


# ---------------------------------------------------------------- shared shapes of the elections

# The circumscriptions are numbered in the alphabetical order of the counties, Bucharest and the Ilfov agricultural
# sector last until 1996, and Ilfov in its place from 2000.
COUNTY_NAMES = [
    "Alba",
    "Arad",
    "Argeș",
    "Bacău",
    "Bihor",
    "Bistrița-Năsăud",
    "Botoșani",
    "Brașov",
    "Brăila",
    "Buzău",
    "Caraș-Severin",
    "Călărași",
    "Cluj",
    "Constanța",
    "Covasna",
    "Dâmbovița",
    "Dolj",
    "Galați",
    "Giurgiu",
    "Gorj",
    "Harghita",
    "Hunedoara",
    "Ialomița",
    "Iași",
    "Maramureș",
    "Mehedinți",
    "Mureș",
    "Neamț",
    "Olt",
    "Prahova",
    "Satu Mare",
    "Sălaj",
    "Sibiu",
    "Suceava",
    "Teleorman",
    "Timiș",
    "Tulcea",
    "Vaslui",
    "Vâlcea",
    "Vrancea",
]
COUNTIES_1992 = dict(enumerate([*COUNTY_NAMES, "București", "Ilfov"], 1))
COUNTIES_2000 = dict(enumerate([*COUNTY_NAMES[:24], "Ilfov", *COUNTY_NAMES[24:], "București", "Abroad"], 1))
CHAMBERS = {
    "cd": (CHAMBER, "Chamber of Deputies", "Camera Deputaților", "chamber"),
    "s": (SENATE, "Senate", "Senat", "senate"),
}


def parliament_election(p: Package, day: str, year: int) -> str:
    first = date.fromisoformat(day)
    return election(
        p,
        f"ps{day}",
        first,
        "general",
        f"{year} Romanian parliamentary election",
        f"Alegeri parlamentare din {first.day} {MONTHS[first.month]} {year}",
        f"ro/{day}-general",
    )


MONTHS = {5: "mai", 9: "septembrie", 10: "octombrie", 11: "noiembrie", 12: "decembrie"}


def presidential_election(p: Package, day: str, runoff: bool) -> str:
    first = date.fromisoformat(day)
    return election(
        p,
        f"president-{day}",
        first,
        "runoff" if runoff else "general",
        f"{first.year} Romanian presidential election" + (", second round" if runoff else ""),
        f"Alegerea Președintelui României din {first.day} {MONTHS[first.month]} {first.year}"
        + (", turul al doilea" if runoff else ""),
        f"ro/{day}-presidential" + ("-runoff" if runoff else ""),
    )


def lists(
    p: Package,
    b: Build,
    eid: str,
    year: int,
    chamber: str,
    t: Table,
    labels: dict[str, str],
    independents: set[str],
    magnitudes: dict[int, int],
    positions: dict[tuple[int, str], int] | None = None,
    rechecked: pl.DataFrame | None = None,
    declared: dict[int, dict[int | str, int]] | None = None,
) -> None:
    """One contest per county of `chamber`, held by the lists with votes there (or on its ballot, by `positions`)."""
    body, english, romanian, slug = CHAMBERS[chamber]
    frame = t.frame.with_columns(b.unit_ids(t.frame))
    totals = frame.group_by("circ").agg(pl.col(c).sum() for c in t.options)
    method = METHOD[str(year)]
    for circ in sorted(frame["circ"].unique().to_list()):
        row = totals.filter(pl.col("circ") == circ).row(0, named=True)
        voted = [c for c in t.options if row[c]]
        if positions is not None:
            listed = sorted((pos, c) for (k, c), pos in positions.items() if k == circ)
            missing = set(voted) - {c for _, c in listed}
            if missing:
                raise ValueError(f"{year} {chamber} {circ}: votes for lists not on the ballot: {sorted(missing)}")
            ordered = [(pos, c) for pos, c in listed]
        else:
            ordered = [(None, c) for c in voted]
        lines = []
        for pos, column in ordered:
            label = labels[column]
            if column in independents:
                lines.append(Line(column, INDEPENDENTS[0], INDEPENDENTS[1], position=pos))
            else:
                lines.append(Line(column, label, "list", party(label), position=pos))
        name = f"{b.day}-{chamber}-{circ}"
        county = b.county(circ)
        cid, rid = contest(
            p,
            name,
            eid,
            f"{year} Romanian {english} election, {county}",
            f"Alegeri pentru {romanian}, {county}, {year}",
            f"ro/{b.day}-general/{slug}/{circ}",
            method,
            kind="list_allocation",
            body_id=body,
            magnitude=magnitudes.get(circ),
        )
        found = options(p, name, cid, lines)
        values(p, rid, frame.filter(pl.col("circ") == circ), found)
        if rechecked is not None:
            extra = rechecked.filter(pl.col("circ") == circ)
            if extra.height:
                stray = [c for c in t.options if c not in found and (extra[c].sum() or 0)]
                if stray:
                    raise ValueError(f"{year} {chamber} {circ}: rechecked votes for lists with no votes: {stray}")
                unit = b.aggregate(f"{circ}/rechecked", f"{county}, null votes found valid on checking")
                values(
                    p,
                    rid,
                    extra.with_columns(pl.lit(unit).alias("unit")).with_columns(
                        pl.when(pl.col(c) == 0).then(None).otherwise(pl.col(c)).alias(c) for c in found
                    ),
                    found,
                    accounting=False,
                )
        if declared is not None:
            sums = declared[circ]
            declare(
                p,
                rid,
                {
                    **{m: v for m, v in sums.items() if isinstance(m, int)},
                    **{found[c]: v for c, v in sums.items() if isinstance(c, str) and c in found},
                },
            )
            stray = [c for c, v in sums.items() if isinstance(c, str) and c not in found and v]
            if stray:
                raise ValueError(f"{year} {chamber} {circ}: declared votes for lists with no ballot line: {stray}")


@dataclass
class Candidate:
    column: str
    name: str
    party: str | None = None
    independent: bool | None = None


def presidential(
    p: Package,
    b: Build,
    year: int,
    runoff: bool,
    t: Table,
    candidates: list[Candidate],
    rechecked: pl.DataFrame | None = None,
    declared: dict[int | str, int] | None = None,
) -> None:
    """The presidential contest of one round, held by the stations of `t`."""
    eid = presidential_election(p, b.day, runoff)
    name = f"president-{b.day}"
    cid, rid = contest(
        p,
        name,
        eid,
        f"{year} Romanian presidential election" + (", second round" if runoff else ""),
        "Președintele României" + (", turul al doilea" if runoff else "") + f", {year}",
        f"ro/{b.day}-presidential" + ("-runoff" if runoff else "") + "/president",
        METHOD[str(year)],
        kind="office",
        office_id=PRESIDENT,
        magnitude=1,
        electoral_system_id=TWO_ROUND,
        round_number=2 if runoff else 1,
        previous_round_id=key("contest", f"president-{FIRST_ROUND[b.day]}") if runoff else None,
    )
    found: dict[str, str] = {}
    for position, c in enumerate(candidates, 1):
        oid = key("option", name, c.column)
        found[c.column] = oid
        who = candidate(c.name)
        p.add(
            "contest_option",
            id=oid,
            contest_id=cid,
            kind="candidate",
            party_id=c.party,
            ballot_position=position,
            ballot_label=title(c.name),
        )
        p.add(
            "candidacy",
            id=key("candidacy", name, c.column),
            person_id=who,
            option_id=oid,
            role="head",
            ballot_name=title(c.name),
            is_independent=c.independent,
        )
    frame = t.frame.with_columns(b.unit_ids(t.frame))
    values(p, rid, frame, found)
    votes = {c.column: int(frame[c.column].sum()) for c in candidates}
    if rechecked is not None:
        for circ, *rest in rechecked.select("circ", *found).iter_rows():
            if not any(rest):
                continue
            unit = b.aggregate(f"{circ}/rechecked", f"{b.county(circ)}, null votes found valid on checking")
            row = rechecked.filter(pl.col("circ") == circ).with_columns(pl.lit(unit).alias("unit"))
            row = row.with_columns(pl.when(pl.col(c) == 0).then(None).otherwise(pl.col(c)).alias(c) for c in found)
            values(p, rid, row, found, accounting=False)
            for c, v in zip(found, rest, strict=True):
                votes[c] += v or 0
    if declared is not None:
        declare(p, rid, {(found[k] if isinstance(k, str) else k): v for k, v in declared.items()})
    ranked = sorted(votes, key=lambda c: -votes[c])
    if runoff:
        p.add("outcome", result_set_id=rid, option_id=found[ranked[0]], kind="elected")
    else:
        for c in ranked[:2]:
            p.add("outcome", result_set_id=rid, option_id=found[c], kind="advanced")


FIRST_ROUND = {
    "1992-10-11": "1992-09-27",
    "1996-11-17": "1996-11-03",
    "2000-12-10": "2000-11-26",
    "2004-12-12": "2004-11-28",
    "2009-12-06": "2009-11-22",
    "2014-11-16": "2014-11-02",
}


def package_of(day: str, summary: str, extra: list[str] | None = None) -> Package:
    p = Package(f"{SOURCE}/{day}", SOURCE, summary, depends=[f"{SOURCE}/registry", *(extra or [])])
    p.external.update({ROMANIA, STATIONS, COUNTIES})
    return p


def cite(p: Package, *names: str, cited: bool = False) -> None:
    """Cite the archives a package reads; a second round's package depends on the first, which already cites them."""
    for name in names:
        if cited:
            p.uses(raw.cached(SOURCE, URLS[name]))
        else:
            p.artifact(raw.cached(SOURCE, URLS[name]), ARCHIVE_SOURCE)


def abbreviations(frame: pl.DataFrame, label: str, short: str) -> dict[str, str]:
    """The label of each party by its folded short name, with the dots dropped."""
    return {fold(s).replace(" ", ""): tidy(lab) for s, lab in frame.select(short, label).iter_rows() if s}


def nominee(short: str, names: dict[str, str]) -> tuple[str | None, bool | None]:
    """The party and independence of a presidential candidate from the short name the table prints."""
    text = fold(short).replace(" ", "")
    if text in ("INDEPENDENT", "CANDIDATINDEPENDENT", "CI"):
        return None, True
    label = names.get(text)
    return (party(label), False) if label else (None, None)


# ---------------------------------------------------------------- 1992


def y1992() -> list[Package]:
    book = archive("1992")
    nomunic = dbf(member(book, "NOMUNIC.DBF"))
    labels = {
        f"P{int(code)}": tidy(f"{a} {b_} {c}")
        for code, a, b_, c in nomunic.select("COD_PARTID", "DEN_LUNG_1", "DEN_LUNG_2", "DEN_LUNG_3").iter_rows()
    }
    general = dbf(member(book, "NGEN.DBF"))
    seats = {
        "cd": {int(c): int(n) for c, n in general.select("NR_CIRC", "NR_DEP").iter_rows()},
        "s": {int(c): int(n) for c, n in general.select("NR_CIRC", "NR_SEN").iter_rows()},
    }
    names = abbreviations(nomunic.with_columns(pl.Series("label", list(labels.values()))), "label", "DEN_SCURTA")
    columns = {"circ": "NR_CIRC", "nr": "SECTIE", "registered": "A", "cast": "AP", "valid": "TVE", "invalid": "VN"}

    def read(name: str, count: int) -> Table:
        frame = dbf(member(book, name))
        return table(frame, columns, [f"P{i}" for i in range(1, count + 1)])

    def null_votes(name: str, count: int) -> pl.DataFrame:
        frame = dbf(member(book, name))
        return frame.select(
            pl.col("NR_CIRC").cast(pl.Int64).alias("circ"),
            *(pl.col(f"P{i}").replace("", "0").cast(pl.Int64) for i in range(1, count + 1)),
        )

    day, second = "1992-09-27", "1992-10-11"
    p = package_of(
        day,
        "The Romanian parliamentary and presidential elections of 27 September 1992 by polling station, "
        "from the Permanent Electoral Authority's archive",
    )
    cite(p, "1992")
    b = Build(p, day, COUNTIES_1992)
    chambers = {"cd": read("DEPUTATI.DBF", 85), "s": read("SENAT.DBF", 85)}
    first = read("P1.DBF", 6)
    b.station_units([*chambers.values(), first])
    eid = parliament_election(p, day, 1992)
    for chamber, t in chambers.items():
        lists(
            p,
            b,
            eid,
            1992,
            chamber,
            t,
            labels,
            {"P1"},
            seats[chamber],
            rechecked=null_votes("CD_NUL.DBF" if chamber == "cd" else "S_NUL.DBF", 85),
        )
    noms = dbf(member(book, "NOM_PRES.DBF"))
    presidential(
        p,
        b,
        1992,
        False,
        first,
        [Candidate(f"P{int(n)}", name, *nominee(short, names)) for n, name, short in noms.iter_rows()],
        rechecked=null_votes("P1_NUL.DBF", 6),
    )
    b.stage_units()
    accept(p)
    q = package_of(
        second,
        "The second round of the Romanian presidential election of 11 October 1992 by polling "
        "station, from the Permanent Electoral Authority's archive",
        [f"{SOURCE}/{day}"],
    )
    cite(q, "1992", cited=True)
    c = Build(q, second, COUNTIES_1992)
    run = read("P2.DBF", 2)
    c.station_units([run])
    noms = dbf(member(book, "NOM_P2.DBF"))
    presidential(
        q,
        c,
        1992,
        True,
        run,
        [Candidate(f"P{int(n)}", name, *nominee(short, names)) for n, name, short in noms.iter_rows()],
    )
    c.stage_units()
    accept(q)
    return [p, q]


# ---------------------------------------------------------------- 1996 and 2000


def y1996() -> list[Package]:
    book = archive("1996")
    nomunic = dbf(member(book, "NOMUNIC.DBF"))
    labels = {f"P{int(code)}": tidy(label) for code, label in nomunic.select("CODU", "DENL").iter_rows()}
    names = abbreviations(nomunic, "DENL", "DENI")
    columns = {
        "circ": "NCE",
        "nr": "NSV",
        "place": "DENL",
        "registered": "A_SVD",
        "cast": "AP_SVD",
        "valid": "TVE_SV",
        "invalid": "VN_SV",
    }

    def read(name: str, options: list[str]) -> Table:
        return table(dbf(member(book, name)), columns, options)

    day, second = "1996-11-03", "1996-11-17"
    p = package_of(
        day,
        "The Romanian parliamentary and presidential elections of 3 November 1996 by polling station, "
        "from the Permanent Electoral Authority's archive",
    )
    cite(p, "1996")
    b = Build(p, day, COUNTIES_1992)
    count = max(int(c[1:]) for c in labels)
    chambers = {
        "cd": read("TUR1_CD.DBF", [f"P{i}" for i in range(1, count + 1) if f"P{i}" in labels]),
        "s": read("TUR1_S.DBF", [f"P{i}" for i in range(1, count + 1) if f"P{i}" in labels]),
    }
    first = read("TUR1_P1.DBF", [f"P{i}" for i in range(1, 17)])
    b.station_units([*chambers.values(), first])
    eid = parliament_election(p, day, 1996)
    p.accept(
        "accounting.votes_valid",
        "in about a thousand stations the votes of the lists do not add up to the valid votes the station prints; "
        "stored as printed",
    )
    for chamber, t in chambers.items():
        lists(p, b, eid, 1996, chamber, t, labels, {"P1"}, {})
    noms = dbf(member(book, "PRES1.DBF"))
    presidential(
        p,
        b,
        1996,
        False,
        first,
        [Candidate(f"P{int(n)}", name, *nominee(short, names)) for n, name, short in noms.iter_rows()],
    )
    b.stage_units()
    accept(p)
    q = package_of(
        second,
        "The second round of the Romanian presidential election of 17 November 1996 by polling "
        "station, from the Permanent Electoral Authority's archive",
        [f"{SOURCE}/{day}"],
    )
    cite(q, "1996", cited=True)
    c = Build(q, second, COUNTIES_1992)
    run = read("TUR2_P2.DBF", ["ION", "EMIL"])
    c.station_units([run])
    by_name = {fold(n): (n, s) for _, n, s in noms.iter_rows()}
    finalists = [("ION", "ION ILIESCU"), ("EMIL", "EMIL CONSTANTINESCU")]
    presidential(
        q,
        c,
        1996,
        True,
        run,
        [Candidate(col, by_name[n][0], *nominee(by_name[n][1], names)) for col, n in finalists],
    )
    c.stage_units()
    accept(q)
    return [p, q]


def y2000() -> list[Package]:
    book = archive("2000")
    nomunic = dbf(member(book, "nomunic.dbf"))
    labels = {f"P{int(code):02d}": tidy(label) for code, label in nomunic.select("CODU", "DENL").iter_rows()}
    names = abbreviations(nomunic, "DENL", "DENI")
    columns = {
        "circ": "NCE",
        "nr": "NSV",
        "place": "NUME_L",
        "registered": "A_SV",
        "cast": "AP_SV",
        "valid": "TVE_SV",
        "invalid": "VN_SV",
    }
    abroad = pl.col("SVSPEC") == "2"

    def read(name: str, options: list[str]) -> Table:
        return table(dbf(member(book, name)), columns, options, abroad)

    day, second = "2000-11-26", "2000-12-10"
    p = package_of(
        day,
        "The Romanian parliamentary and presidential elections of 26 November 2000 by polling "
        "station, from the Permanent Electoral Authority's archive",
    )
    cite(p, "2000")
    b = Build(p, day, COUNTIES_2000)
    chambers = {"cd": read("cdep.dbf", list(labels)), "s": read("sen.dbf", list(labels))}
    first = read("p1.dbf", [f"P{i}" for i in range(1, 13)])
    b.station_units([*chambers.values(), first])
    eid = parliament_election(p, day, 2000)
    for chamber, t in chambers.items():
        lists(p, b, eid, 2000, chamber, t, labels, {"P01"}, {})
    noms = dbf(member(book, "pres.dbf"))
    presidential(
        p,
        b,
        2000,
        False,
        first,
        [
            Candidate(f"P{int(n)}", name, *nominee(short, names))
            for n, name, short in noms.select("CODU", "DENL", "DENI").iter_rows()
        ],
    )
    b.stage_units()
    accept(p)
    q = package_of(
        second,
        "The second round of the Romanian presidential election of 10 December 2000 by polling "
        "station, from the Permanent Electoral Authority's archive",
        [f"{SOURCE}/{day}"],
    )
    cite(q, "2000", cited=True)
    c = Build(q, second, COUNTIES_2000)
    run = read("p2.dbf", ["ILIESCU", "VADIM"])
    c.station_units([run])
    by_name = {fold(n): (n, s) for _, n, s in noms.select("CODU", "DENL", "DENI").iter_rows()}
    finalists = [("ILIESCU", "ION ILIESCU"), ("VADIM", "CORNELIU VADIM TUDOR")]
    presidential(
        q,
        c,
        2000,
        True,
        run,
        [Candidate(col, by_name[n][0], *nominee(by_name[n][1], names)) for col, n in finalists],
    )
    c.stage_units()
    accept(q)
    return [p, q]


# ---------------------------------------------------------------- 2004


def y2004() -> list[Package]:
    book = archive("2004")
    nomunic = dbf(member(book, "nomunic.DBF"))
    labels = {f"P{int(code)}": tidy(label) for code, label in nomunic.select("CODU", "DENL").iter_rows()}
    columns = {
        "circ": "NCE",
        "nr": "NSV",
        "place": "NUME_L",
        "registered": "A_SVD",
        "cast": "AP_SVD",
        "valid": "TVE_SV",
        "invalid": "VN_SV",
    }
    abroad = pl.col("SVSPEC") == "2"

    def read(name: str, options: list[str]) -> Table:
        t = table(dbf(member(book, name)), columns, options, abroad)
        # the tables name the Bucharest sector that counted a station abroad as its locality
        t.frame = t.frame.with_columns(pl.when(pl.col("abroad")).then(None).otherwise(pl.col("place")).alias("place"))
        return t

    def totals(name: str, options: list[str]) -> dict[int, dict[int | str, int]]:
        frame = dbf(member(book, name))
        found: dict[int, dict[int | str, int]] = {}
        for row in frame.iter_rows(named=True):
            sums: dict[int | str, int] = {
                REGISTERED: int(row["A_SVD"]),
                CAST: int(row["AP_SVD"]),
                VALID: int(row["TVE_SV"]),
                INVALID: int(row["VN_SV"]),
            }
            for o in options:
                sums[o] = integer(row[o]) or 0
            found[int(row["NCE"])] = sums
        return found

    circs = dbf(member(book, "xcirc.DBF"))
    seats = {
        "cd": {int(c): int(n) for c, n in circs.select("NCE", "MAN_CD").iter_rows()},
        "s": {int(c): int(n) for c, n in circs.select("NCE", "MAN_S").iter_rows()},
    }
    draws = dbf(member(book, "xtrags.DBF"))
    positions: dict[str, dict[tuple[int, str], int]] = {"s": {}, "cd": {}}
    for nce, nord, tippv, codu in draws.select("NCE", "NORD", "TIPPV", "CODU").iter_rows():
        positions["s" if tippv == "1" else "cd"][(int(nce), f"P{int(codu)}")] = int(nord)
    day, second = "2004-11-28", "2004-12-12"
    p = package_of(
        day,
        "The Romanian parliamentary and presidential elections of 28 November 2004 by polling "
        "station, from the Permanent Electoral Authority's archive",
    )
    cite(p, "2004")
    b = Build(p, day, COUNTIES_2000)
    chambers = {"cd": read("cdep.DBF", list(labels)), "s": read("sen.DBF", list(labels))}
    candidates = [f"P{i}" for i in range(1, 13)]
    first = read("xp1.DBF", candidates)
    b.station_units([*chambers.values(), first])
    eid = parliament_election(p, day, 2004)
    for chamber, t in chambers.items():
        lists(
            p,
            b,
            eid,
            2004,
            chamber,
            t,
            labels,
            {"P1"},
            seats[chamber],
            positions[chamber],
            declared=totals("xxcd.DBF" if chamber == "cd" else "xxs.DBF", list(labels)),
        )

    def roster(name: str) -> list[Candidate]:
        found = []
        for loc, nume, codu in dbf(member(book, name)).select("LOC", "NUME", "CODU").iter_rows():
            independent = codu == "1"
            found.append(
                Candidate(f"P{int(loc)}", nume, None if independent else party(labels[f"P{int(codu)}"]), independent)
            )
        return found

    def national(name: str, options: list[str]) -> dict[int | str, int]:
        found: dict[int | str, int] = {}
        for sums in totals(name, options).values():
            for k, v in sums.items():
                found[k] = found.get(k, 0) + v
        return found

    presidential(p, b, 2004, False, first, roster("xpres1.DBF"), declared=national("xxp1.DBF", candidates))
    b.stage_units()
    accept(p)
    q = package_of(
        second,
        "The second round of the Romanian presidential election of 12 December 2004 by polling "
        "station, from the Permanent Electoral Authority's archive",
        [f"{SOURCE}/{day}"],
    )
    cite(q, "2004", cited=True)
    c = Build(q, second, COUNTIES_2000)
    run = read("xp2.DBF", ["P1", "P2"])
    c.station_units([run])
    presidential(q, c, 2004, True, run, roster("xpres2.DBF"), declared=national("xxp2.DBF", ["P1", "P2"]))
    c.stage_units()
    accept(q)
    return [p, q]


# ---------------------------------------------------------------- 2008


def y2008() -> list[Package]:
    book = archive("2008")
    day = "2008-11-30"
    p = package_of(
        day,
        "The Romanian parliamentary election of 30 November 2008 by polling station and college, "
        "from the Permanent Electoral Authority's archive",
    )
    cite(p, "2008")
    b = Build(p, day, COUNTIES_2000)
    accounting = {
        "circ": "NCE",
        "nr": "NSV",
        "place": "LOCALITATE",
        "registered": "A_SV",
        "cast": "AP_SV",
        "valid": "TVE_SV",
        "invalid": "VN_SV",
        "blank": "VA_SV",
    }
    options_ = [f"P{i}" for i in range(1, 81)]
    tables: dict[str, tuple[Table, pl.Series]] = {}
    for chamber, name in (("cd", "xsv_cd.xls"), ("s", "xsv_s.xls")):
        rows = sheet(member(book, name))
        frame = pl.DataFrame([dict(zip(rows[0], r, strict=True)) for r in rows[1:]], orient="row")
        frame = frame.with_columns(pl.col(c).fill_null("0") for c in options_)
        t = table(frame, accounting, options_, pl.col("NCE") == "43")
        tables[chamber] = (t, frame["COLEGIU"].cast(pl.Float64).cast(pl.Int64))
    b.station_units([t for t, _ in tables.values()])
    rows = sheet(member(book, "candid.xls"))
    head = [str(h) for h in rows[0]]
    candid = pl.DataFrame([dict(zip(head, r, strict=True)) for r in rows[1:]], orient="row").with_columns(
        pl.col("NCE").cast(pl.Float64).cast(pl.Int64),
        pl.col("COLEGIU").cast(pl.Float64).cast(pl.Int64),
        pl.col("LOC").cast(pl.Float64).cast(pl.Int64),
        pl.col("CODU").cast(pl.Float64).cast(pl.Int64),
        pl.col("TIPPV").replace({"1": "cd", "2": "s"}).alias("chamber"),
    )
    eid = parliament_election(p, day, 2008)
    for chamber, (t, colleges) in tables.items():
        body, english, romanian, slug = CHAMBERS[chamber]
        frame = t.frame.with_columns(colleges.alias("college"), b.unit_ids(t.frame))
        rows = sheet(member(book, f"colegii_{chamber}.xls"))
        head = [str(h) for h in rows[0]]
        # the college tables number the colleges across the country, in the order of the circumscriptions, while
        # the stations and candidates number them within each circumscription
        national = sorted((int(float(r[0] or 0)), int(float(r[2] or 0))) for r in rows[1:] if r[0])
        first = {}
        for circ, number in national:
            first.setdefault(circ, number)
        printed = {
            (int(float(r[0])), int(float(r[2] or 0)) - first[int(float(r[0]))] + 1): dict(zip(head, r, strict=True))
            for r in rows[1:]
            if r[0]
        }
        for (circ, college), part in sorted(frame.group_by("circ", "college"), key=lambda g: (g[0][0], g[0][1])):
            circ, college = int(str(circ)), int(str(college))
            ballot = candid.filter(
                (pl.col("chamber") == chamber) & (pl.col("NCE") == circ) & (pl.col("COLEGIU") == college)
            ).sort("LOC")
            loners = ballot.filter(pl.col("CODU") == 1)
            name = f"{day}-{chamber}-{circ}-{college}"
            lines: list[Line] = []
            for loc, codu, nume, denp in ballot.select("LOC", "CODU", "NUME", "DENP").iter_rows():
                column = f"P{codu}"
                if codu == 1 and loners.height > 1:
                    if not any(line.column == column for line in lines):
                        lines.append(Line(column, INDEPENDENTS[0], INDEPENDENTS[1], position=loc))
                    continue
                who = key("person", name, fold(nume))
                p.add("person", id=who)
                p.name(who, title(nume), "ro", "official")
                lines.append(
                    Line(column, title(nume), "candidate", None if codu == 1 else party(denp), who, position=loc)
                )
            voted = {c for c in t.options if part[c].sum()}
            missing = voted - {line.column for line in lines}
            if missing:
                raise ValueError(f"2008 {chamber} {circ}/{college}: votes without a candidate: {sorted(missing)}")
            county = b.county(circ)
            cid, rid = contest(
                p,
                name,
                eid,
                f"2008 Romanian {english} election, {county}, college {college}",
                f"Alegeri pentru {romanian}, {county}, colegiul {college}, 2008",
                f"ro/{day}-general/{slug}/{circ}/{college}",
                METHOD["2008"],
                kind="body_seats",
                body_id=body,
            )
            found = options(p, name, cid, lines)
            values(p, rid, part, found)
            sums = printed[(circ, college)]
            declare(
                p,
                rid,
                {
                    REGISTERED: int(float(sums["a_sv"] or 0)),
                    CAST: int(float(sums["ap_sv"] or 0)),
                    VALID: int(float(sums["tve_sv"] or 0)),
                    INVALID: int(float(sums["vn_sv"] or 0)),
                    BLANK: int(float(sums["va_sv"] or 0)),
                    **{oid: int(float(sums[c.lower()] or 0)) for c, oid in found.items()},
                },
            )
            stray = [c for c in options_ if c not in found and int(float(sums[c.lower()] or 0))]
            if stray:
                raise ValueError(f"2008 {chamber} {circ}/{college}: declared votes without a candidate: {stray}")
    b.stage_units()
    accept(p)
    p.accept(
        "accounting.declared",
        "in the fifth Chamber college of Sibiu the Authority's college table prints 850 votes for the independent "
        "candidates, while its stations add up to 1,267; both are stored as printed",
    )
    return [p]


# ---------------------------------------------------------------- 2009


def y2009() -> list[Package]:
    book = archive("2009")
    columns = {
        "circ": "NCE",
        "nr": "SV",
        "place": "DENLOC",
        "registered": "A",
        "cast": "B",
        "valid": "C",
        "invalid": "D",
    }
    built = []
    for day, runoff, data, readme in (
        ("2009-11-22", False, "sectii_p.dbf", "citeste_ma_t1.txt"),
        ("2009-12-06", True, "sectii_tur2.dbf", "citeste_ma_t2.txt"),
    ):
        text = member(book, readme).decode("cp1250")
        names = dict(re.findall(r"^\s*(P\d+)\s*=\s*(.+?)\s*$", text, re.MULTILINE))
        frame = dbf(member(book, data))
        counties = {int(c): title(j) for c, j in frame.select("NCE", "JUDET").unique().iter_rows()}
        counties[48] = "Abroad"
        t = table(frame, columns, list(names), pl.col("SPEC") == "2")
        when = date.fromisoformat(day)
        p = package_of(
            day,
            f"The {'second round of the ' if runoff else ''}Romanian presidential election of {when.day} "
            f"{when.strftime('%B')} 2009 by polling station, from the Permanent Electoral Authority's archive",
            [f"{SOURCE}/2009-11-22"] if runoff else [],
        )
        cite(p, "2009", cited=runoff)
        b = Build(p, day, counties)
        b.station_units([t])
        presidential(p, b, 2009, runoff, t, [Candidate(c, n) for c, n in names.items()])
        b.stage_units()
        accept(p)
        built.append(p)
    return built


# ---------------------------------------------------------------- 2014


def y2014() -> list[Package]:
    book = archive("2014")
    built = []
    for day, runoff, data, counties_file in (
        (
            "2014-11-02",
            False,
            "SIAP2014_STAT_Statistica-la-nivel-de-sectie-de-votare-tur1.xlsx",
            "SIAP2014_STAT_Statistica-la-nivel-de-tara-pe-birou-electoral.xlsx",
        ),
        (
            "2014-11-16",
            True,
            "SIAP2014_STAT_Statistica-la-nivel-de-sectie-de-votare1.xlsx",
            "SIAP2014_STAT_Statistica-la-nivel-de-tara-pe-birou-electoral1.xlsx",
        ),
    ):
        rows = sheet(member(book, data))
        head = [str(h) for h in rows[0]]
        names = head[17:]
        rows = [rows[0], *(r for r in rows[1:] if str(r[0] or "").strip().isdigit())]
        frame = pl.DataFrame(
            {
                "circ": [r[0] for r in rows[1:]],
                "county": [r[1] for r in rows[1:]],
                "nr": [r[2] for r in rows[1:]],
                "place": [r[6] for r in rows[1:]],
                "registered": [r[8] for r in rows[1:]],
                "cast": [r[9] for r in rows[1:]],
                "valid": [r[13] for r in rows[1:]],
                "invalid": [r[14] for r in rows[1:]],
                **{f"C{i}": [r[17 + i] for r in rows[1:]] for i in range(len(names))},
            }
        )
        columns = {k: k for k in ("circ", "nr", "place", "registered", "cast", "valid", "invalid")}
        options_ = [f"C{i}" for i in range(len(names))]
        t = table(frame, columns, options_, pl.col("circ") == "48")
        counties = {int(c): title(j) for c, j in frame.select("circ", "county").unique().iter_rows()}
        counties[48] = "Abroad"
        printed = sheet(member(book, counties_file))
        declared: dict[int | str, int] = {}
        for r in printed[1:]:
            if not r[0] or not str(r[0]).strip().isdigit():
                continue
            for measure, cell in ((REGISTERED, 2), (CAST, 3), (VALID, 7), (INVALID, 8)):
                declared[measure] = declared.get(measure, 0) + (integer(r[cell]) or 0)
            for i in range(len(names)):
                declared[f"C{i}"] = declared.get(f"C{i}", 0) + (integer(r[11 + i]) or 0)
        if [str(h) for h in printed[0][11:]] != names:
            raise ValueError(f"{day}: the county table names other candidates")
        when = date.fromisoformat(day)
        p = package_of(
            day,
            f"The {'second round of the ' if runoff else ''}Romanian presidential election of {when.day} "
            f"{when.strftime('%B')} 2014 by polling station, from the Permanent Electoral Authority's archive",
            [f"{SOURCE}/2014-11-02"] if runoff else [],
        )
        cite(p, "2014", cited=runoff)
        b = Build(p, day, counties)
        b.station_units([t])
        presidential(p, b, 2014, runoff, t, [Candidate(f"C{i}", n) for i, n in enumerate(names)], declared=declared)
        b.stage_units()
        accept(p)
        built.append(p)
    return built


# ---------------------------------------------------------------- 1990


# Two columns of the 1990 Senate table whose county cells do not add up to the country total it prints. In Maramureș
# the Ukrainian union has 8,310 votes against a total of 3,310, and the cell of the Party for Honoring the Heroes of
# the Revolution holds a stray letter where the total prints 1,089. The county's own total adds up only with the
# cells as printed, so they are stored as printed and the result set carries the caveat.
SPELLINGS: set[tuple[str, str]] = set()
TOTAL_1990 = {
    ("s", fold("Uniunea Ucrainienilor din România")),
    ("s", fold("Partidul pentru Cinstirea Eroilor Revoluției și Salvare Națională")),
}
MARAMURES_1990 = (
    "In the Authority's Senate table the Maramureș cell of the Party for Honoring the Heroes of the Revolution and "
    "National Salvation holds a stray letter, while the country total prints 1,089 votes for the party, and the "
    "Ukrainian union has 8,310 votes here against a country total of 3,310. The county's own total of valid votes "
    "adds up only with the cells as printed, so they are stored as printed."
)


def y1990() -> list[Package]:
    day = "1990-05-20"
    p = package_of(
        day,
        "The Romanian parliamentary and presidential elections of 20 May 1990 by county, from the "
        "Permanent Electoral Authority's archive",
    )
    cite(p, *(name for name in URLS if name.startswith("1990")))
    counties: dict[int, str] = {}
    by_name: dict[str, int] = {}
    chambers: dict[str, tuple[list[str], list[tuple[int, list[int | None]]], list[int | None]]] = {}
    for chamber in ("cd", "s"):
        rows = sheet(raw.cached(SOURCE, URLS[f"1990-{chamber}"]).path.read_bytes())
        head = [tidy(h) for h in rows[0][2:]]
        if fold(head[-1]) != "TOTAL":
            raise ValueError(f"1990 {chamber}: the last column is not the total")
        data: list[tuple[int, list[int | None]]] = []
        total: list[int | None] = []
        for r in rows[1:]:
            if r[0] and str(r[0]).strip().isdigit():
                circ = int(str(r[0]).strip())
                counties[circ] = tidy(r[1])
                by_name[fold(r[1])] = circ
                # one cell of the Senate table for Maramureș holds a stray letter instead of a number
                data.append((circ, [None if v == "â" else integer(v) for v in r[2:]]))
            elif fold(r[1]) == "TOTAL":
                total = [integer(v) for v in r[2:]]
        for i in range(len(head)):
            summed = sum(v[i] or 0 for _, v in data)
            if summed != total[i] and (chamber, fold(head[i])) not in TOTAL_1990:
                raise ValueError(f"1990 {chamber}: {head[i]} sums to {summed}, the table prints {total[i]}")
        chambers[chamber] = (head, data, total)
    counties = {c: n.replace("Bacãu", "Bacău") for c, n in counties.items()}
    b = Build(p, day, counties)
    eid = parliament_election(p, day, 1990)
    houses = {
        "cd": (CHAMBER, "Assembly of Deputies", "Adunarea Deputaților", "chamber"),
        "s": (SENATE, "Senate", "Senat", "senate"),
    }
    for chamber, (head, data, _) in chambers.items():
        body, english, romanian, slug = houses[chamber]
        members: dict[int, list[tuple[str, str]]] = {}
        rows = sheet(raw.cached(SOURCE, URLS[f"1990-{chamber}-members"]).path.read_bytes())
        for r in rows[2:]:
            if not r[1]:
                continue
            circ = by_name.get(fold(r[2]))
            if circ is None:
                raise ValueError(f"1990 {chamber}: a member of an unknown county: {r}")
            members.setdefault(circ, []).append((tidy(r[0]), tidy(r[1])))
        columns = {fold(h): i for i, h in enumerate(head[:-1])}
        for circ, cells in data:
            name = f"{day}-{chamber}-{circ}"
            county = counties[circ]
            elected = members.get(circ, [])
            wanted = {i for i, v in enumerate(cells[:-1]) if v}
            seats: dict[int, list[str]] = {}
            for label, who in elected:
                i = columns.get(fold(label))
                if i is None and not fold(label).startswith("CANDIDAT INDEPENDENT"):
                    # the list of members spells some organizations a little differently from the table of votes
                    close = difflib.get_close_matches(fold(label), list(columns), n=1, cutoff=0.9)
                    if close:
                        SPELLINGS.add((label, head[columns[close[0]]]))
                        i = columns[close[0]]
                if i is None and fold(label).startswith("CANDIDAT INDEPENDENT"):
                    i = next((j for h, j in columns.items() if fold(who) in h), None)
                    if i is None:
                        i = next(
                            (j for h, j in columns.items() if h.startswith(("CANDIDATI INDEPENDENTI", "ALTI"))), None
                        )
                if i is None:
                    raise ValueError(f"1990 {chamber} {county}: a member of a list not on the table: {label}")
                seats.setdefault(i, []).append(who)
                wanted.add(i)
            lines = []
            for i in sorted(wanted):
                label = head[i]
                text = fold(label)
                if text.startswith(("CANDIDATI INDEPENDENTI", "ALTI CANDIDATI INDEPENDENTI")):
                    lines.append(Line(str(i), INDEPENDENTS[0], INDEPENDENTS[1]))
                elif text.startswith("CANDIDAT INDEPENDENT"):
                    person_name = re.sub(r"(?i)^candidat independent\s*", "", label)
                    who = key("person", name, fold(person_name))
                    p.add("person", id=who)
                    p.name(who, person_name, "ro", "official")
                    lines.append(Line(str(i), person_name, "candidate", None, who))
                else:
                    lines.append(Line(str(i), label, "list", party(label)))
            cid, rid = contest(
                p,
                name,
                eid,
                f"1990 Romanian {english} election, {county}",
                f"Alegeri pentru {romanian}, {county}, 1990",
                f"ro/{day}-general/{slug}/{circ}",
                METHOD["1990"],
                kind="list_allocation",
                body_id=body,
                magnitude=len(elected) or None,
            )
            found = options(p, name, cid, lines)
            if chamber == "s" and circ == 25:
                p.add(
                    "note",
                    id=key("note", rid, "caveat"),
                    entity_id=rid,
                    kind="caveat",
                    body_md=MARAMURES_1990,
                    lang="en",
                )
            unit = b.aggregate(str(circ), county)
            frame = pl.DataFrame(
                {"unit": [unit], "valid": [cells[-1]], **{str(i): [cells[i]] for i in sorted(wanted)}},
                schema_overrides={str(i): pl.Int64 for i in sorted(wanted)},
            )
            values(p, rid, frame, found)
            kinds = {line.column: line.kind for line in lines}
            for i, whom in seats.items():
                oid = found[str(i)]
                if kinds[str(i)] == "candidate":
                    p.add("outcome", result_set_id=rid, option_id=oid, kind="elected", seats=1)
                    continue
                p.add("outcome", result_set_id=rid, option_id=oid, kind="won", seats=len(whom))
                for who_name in whom:
                    who = key("person", name, fold(who_name))
                    p.add("person", id=who)
                    p.name(who, who_name, "ro", "official")
                    cand = key("candidacy", name, str(i), fold(who_name))
                    p.add(
                        "candidacy",
                        id=cand,
                        person_id=who,
                        option_id=oid,
                        role="list_member",
                        ballot_name=who_name,
                        is_independent=kinds[str(i)] == "independents",
                    )
                    p.add("outcome", result_set_id=rid, option_id=oid, candidacy_id=cand, kind="elected")
    rows = sheet(raw.cached(SOURCE, URLS["1990-p"]).path.read_bytes())
    names = [str(rows[0][i]) for i in (1, 3, 5)]
    county_rows = []
    total: dict[int | str, int] = {}
    for r in rows[2:]:
        if not r[0]:
            continue
        if fold(r[0]).startswith("TOTAL"):
            total = {f"P{k}": integer(r[1 + 2 * k]) or 0 for k in range(3)}
            continue
        circ = by_name.get(fold(r[0]))
        if circ is None:
            raise ValueError(f"1990 president: an unknown county {r[0]}")
        county_rows.append((circ, *(integer(r[1 + 2 * k]) for k in range(3))))
    frame = pl.DataFrame(county_rows, schema=["circ", "P0", "P1", "P2"], orient="row").with_columns(
        pl.lit(0, pl.Int64).alias("nr"),
        pl.lit(None, pl.String).alias("place"),
        pl.lit(False).alias("abroad"),
        *(pl.lit(None, pl.Int64).alias(m) for m in ("registered", "cast", "valid", "invalid", "blank")),
    )
    units = [b.aggregate(str(c), counties[c]) for c in frame["circ"]]
    for k in range(3):
        if int(frame[f"P{k}"].sum()) != total[f"P{k}"]:
            raise ValueError(
                f"1990 president: {names[k]} sums to {frame[f'P{k}'].sum()}, the table prints {total[f'P{k}']}"
            )
    presidential_1990(p, b, frame.with_columns(pl.Series("unit", units)), names, total)
    b.stage_units()
    return [p]


def presidential_1990(p: Package, b: Build, frame: pl.DataFrame, names: list[str], total: dict[int | str, int]) -> None:
    eid = presidential_election(p, b.day, False)
    name = f"president-{b.day}"
    cid, rid = contest(
        p,
        name,
        eid,
        "1990 Romanian presidential election",
        "Președintele României, 1990",
        f"ro/{b.day}-presidential/president",
        "Votes of each candidate in each county (electoral circumscription), from the Authority's table of the 1990 "
        "presidential election. The table prints no registered voters, voters, or null votes, and the country "
        "total it prints is stored as declared.",
        kind="office",
        office_id=PRESIDENT,
        magnitude=1,
        electoral_system_id=TWO_ROUND,
    )
    found: dict[str, str] = {}
    for k, n in enumerate(names):
        column = f"P{k}"
        oid = key("option", name, column)
        found[column] = oid
        p.add(
            "contest_option",
            id=oid,
            contest_id=cid,
            kind="candidate",
            ballot_position=k + 1,
            ballot_label=title(n),
        )
        p.add(
            "candidacy",
            id=key("candidacy", name, column),
            person_id=candidate(n),
            option_id=oid,
            role="head",
            ballot_name=title(n),
        )
    values(p, rid, frame, found, accounting=False)
    declare(p, rid, {found[str(k)]: v for k, v in total.items()})
    winner = max(found, key=lambda c: total[c])
    p.add("outcome", result_set_id=rid, option_id=found[winner], kind="elected")


# ---------------------------------------------------------------- the pipeline

YEARS = (y1990, y1992, y1996, y2000, y2004, y2008, y2009, y2014)
DAYS = [
    "1990-05-20",
    "1992-09-27",
    "1992-10-11",
    "1996-11-03",
    "1996-11-17",
    "2000-11-26",
    "2000-12-10",
    "2004-11-28",
    "2004-12-12",
    "2008-11-30",
    "2009-11-22",
    "2009-12-06",
    "2014-11-02",
    "2014-11-16",
]


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(SOURCE, url, suffix=SUFFIX[url[url.rfind(".") :]])


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def parse() -> None:
    for year in YEARS:
        for p in year():
            p.write()
    registry().write()


def emit() -> None:
    package.run("load", packages())
