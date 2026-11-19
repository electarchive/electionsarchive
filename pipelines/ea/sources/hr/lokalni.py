"""Croatian local elections 2013-2025 by polling station, from the State Electoral Commission's results archive.

Each election year's zip holds one table per contest: a county assembly (or the Zagreb City Assembly), a town or
municipal council, a mayor or župan with a runoff, and the deputies elected by national minorities. The archive's
results pages also read one summary file per contest with the printed totals, which the pipeline compares with the
polling-station tables.
"""

import csv
import functools
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.hr import granice

SOURCE = "hr.lokalni"
IZBORI = "hr.izbori"
SITE = "https://www.izbori.hr/arhiva-izbora"
DATA = f"{SITE}/data/lokalni"
CROATIA = str(uuid5(package.NAMESPACE, "polity\x1fHR"))
REGISTERED, VOTERS, CAST, VALID, INVALID = 2, 14, 3, 4, 5
VOTES = 1
ZAGREB = "21"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def izbori_area(scheme: str, code: str) -> str:
    """An area the Croatian geography package of the parliamentary pipeline already holds."""
    return str(uuid5(package.NAMESPACE, f"{IZBORI}\x1farea\x1f{scheme}\x1f{code}"))


SOURCE_ID = key("source", SOURCE)


@dataclass(frozen=True)
class Round:
    year: int
    number: int
    start: date
    end: date
    precision: str = "day"
    tag: str = ""  # a package key suffix when a round is not the year's own first or second


YEARS = [2013, 2017, 2021, 2025]
FIRST = {2013: date(2013, 5, 19), 2017: date(2017, 5, 21), 2021: date(2021, 5, 16), 2025: date(2025, 5, 18)}
SECOND = {2013: date(2013, 6, 2), 2017: date(2017, 6, 4), 2021: date(2021, 5, 30), 2025: date(2025, 6, 1)}


def rounds() -> list[Round]:
    out = []
    for year in YEARS:
        out.append(Round(year, 1, FIRST[year], FIRST[year]))
        out.append(Round(year, 2, SECOND[year], SECOND[year]))
    # Kraljevec na Sutli's runoff tied, so its mayor was chosen in a third vote that the archive files without a date.
    out.append(Round(2021, 3, date(2021, 6, 1), date(2021, 6, 30), "month", "2021-06-kraljevec-na-sutli"))
    return sorted(out, key=lambda r: (r.start, r.number))


def package_key(r: Round) -> str:
    return f"{SOURCE}/{r.tag or r.start}"


def zip_url(year: int) -> str:
    return f"{DATA}/{year}/zip/rezultati.zip"


def dao_url(year: int) -> str:
    return f"{SITE}/app/dao/lokalni/lokalni-{year}-dao.js"


def clean(text: object) -> str:
    return " ".join(str(text if text is not None else "").replace("\xa0", " ").split())


def fold(text: object) -> str:
    plain = unicodedata.normalize("NFKD", clean(text).replace("đ", "d").replace("Đ", "D"))
    return " ".join(plain.encode("ascii", "ignore").decode().lower().split())


def label(text: str) -> str:
    """A label folded to its words, so the same name matches across files."""
    return " ".join(re.findall(r"[a-z0-9]+", fold(text.replace("\n", " "))))


def decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("undecodable file")


SMALL = {"na", "i", "u", "pri", "od", "kod", "nad", "pod", "ob", "o", "županija", "di", "del", "sv."}


def title(text: str) -> str:
    out = []
    for index, word in enumerate(clean(text).lower().split()):
        if index and word in SMALL:
            out.append(word)
        else:
            out.append("-".join(part[:1].upper() + part[1:] for part in word.split("-")))
    return " ".join(out)


def place(text: str) -> str:
    """A municipality's name folded for matching across years: no "grad" or "općina", no second-language name."""
    folded = label(clean(text).split(" - ")[0])
    for prefix in ("grad ", "opcina "):
        if folded.startswith(prefix) and folded != "grad zagreb":
            folded = folded[len(prefix) :]
    folded = re.sub(r"^sv ", "sveti ", folded)
    return "zagreb" if folded.startswith("zagreb") or folded == "grad zagreb" else folded


# The results pages read one summary file per contest; the page's own script lists which contests exist.

COUNTY_TYPES = {"06", "15", "19", "27"}  # assembly, župan, and the county deputies of minorities
LOCAL_TYPES = {"08", "17", "21", "25"}  # council, mayor, and the municipal deputies of minorities
RUNOFF_TYPES = {"15", "17", "19", "21", "25", "27"}
ENTRY = re.compile(r'"code"\s*:\s*"([^"]*)"\s*,\s*"label"\s*:\s*"([^"]*)"\s*,\s*"codetop"\s*:\s*"([^"]*)"')


@functools.cache
def listing(year: int) -> tuple[dict[str, tuple[str, str]], list[tuple[str, str, str, str]]]:
    """The towns (code -> county, name) and the contests (type, county, town code, label) the year's page lists."""
    text = raw.cached(SOURCE, dao_url(year)).path.read_text(encoding="utf-8-sig")
    block = re.search(r"var gradOpcina = \[(.*?)\];", text, re.DOTALL)
    kinds = re.search(r"var vrsta = \[(.*?)\];", text, re.DOTALL)
    assert block is not None and kinds is not None
    towns = {code: (top, name) for code, name, top in ENTRY.findall(block.group(1))}
    found: dict[tuple[str, str, str], str] = {}
    for code, name, top in ENTRY.findall(kinds.group(1)):
        if code in COUNTY_TYPES:
            county = top if len(top) == 2 else towns.get(top, ("", ""))[0]
            if county:
                found.setdefault((code, county, "0000"), clean(name))
        elif code in LOCAL_TYPES and top in towns:
            found.setdefault((code, towns[top][0], top), clean(name))
    return towns, [(*k, v) for k, v in sorted(found.items())]


def summary_name(kind: str, county: str, town: str, number: int, year: int) -> str:
    suffix = "json" if year >= 2021 else "csv"
    return f"{DATA}/{year}/rezultati/{number}/r_{kind}_{county}_{town}_000.{suffix}"


def summary_urls(year: int) -> list[str]:
    urls = []
    for kind, county, town, _ in listing(year)[1]:
        urls.append(summary_name(kind, county, town, 1, year))
        if kind in RUNOFF_TYPES:
            urls.append(summary_name(kind, county, town, 2, year))
    return urls


def fetch() -> None:
    raw.fetch(SOURCE, GB_COUNTIES, suffix=".geojson")
    raw.fetch(SOURCE, GB_TOWNS, suffix=".geojson")
    granice.fetch()
    for year in YEARS:
        raw.fetch(SOURCE, zip_url(year), suffix=".zip")
        raw.fetch(SOURCE, dao_url(year), suffix=".js")
    for year in YEARS:
        urls = summary_urls(year)
        got = raw.fetch_many(SOURCE, urls, suffix="." + urls[0].rsplit(".", 1)[1], workers=6)
        print(f"{year}: {len(got):,} of {len(urls):,} summary files", flush=True)


# Reading the per-polling-station tables of the results archive.


@dataclass
class Station:
    county: str
    muni: str  # the name as printed
    muni_kind: str  # grad or općina
    bm: str
    name: str
    counts: dict[int, int]
    votes: list[int]


@dataclass
class Contest:
    year: int
    round: int
    kind: str  # council, mayor, deputy, assembly, governor, county_deputy
    county: str
    place: str  # the folded municipality name, or "" for a county-wide contest
    muni: str
    muni_kind: str
    header: list[str]
    stations: list[Station]
    member: str
    county_name: str = ""
    deputy: str = ""  # a deputy contest's minority, in the archive's own words
    summary: Summary | None = None
    link: list[int | None] = field(default_factory=list)

    @property
    def level(self) -> str:
        return "county" if self.kind in ("assembly", "governor", "county_deputy") or self.county == ZAGREB else "muni"

    @property
    def ident(self) -> tuple[str, ...]:
        head = (self.kind, self.county, "" if self.level == "county" else self.place)
        return (*head, label(self.deputy)) if self.kind in ("deputy", "county_deputy") else head

    @property
    def declared(self) -> Summary | None:
        """The commission's printed summary, when every column of the tables is one of its options."""
        if self.summary is None or len(self.link) != len(self.header) or any(x is None for x in self.link):
            return None
        return self.summary

    @property
    def totals(self) -> dict[int, int]:
        out: Counter[int] = Counter()
        for s in self.stations:
            out.update(s.counts)
        return dict(out)


def kind_of(name: str) -> str | None:
    low = name.rsplit("/", 1)[-1].lower()
    if "zamjenic" in low or "zamjenik" in low:
        return "county_deputy" if re.search(r"[zž]upan|ºupan", low) else "deputy"
    if "vije" in low:
        return "council"
    if "elnik" in low:
        return "mayor"
    if "skup" in low:
        return "assembly"
    if "upan" in low:
        return "governor"
    return None


def round_of(name: str) -> int:
    found = re.search(r"(\d)_?krug|krug-(\d)", name.lower())
    if found is None:
        raise ValueError(f"{name}: no round")
    return int(found.group(1) or found.group(2))


def number(text: object) -> int:
    value = clean(text).replace(".", "")
    if not value:
        return 0
    if not value.isdigit():
        raise ValueError(f"not a count: {text!r}")
    return int(value)


def _start(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows[:10]):
        if row and fold(row[0]).startswith("rbr") and "zupanije" in fold(row[0]):
            return index
    raise ValueError("no header row")


def _tables(year: int) -> list[tuple[str, str | None, list[list[str]]]]:
    """Every table of the year's zip: its member name, its sheet's name, and its rows from the header on."""
    archive = zipfile.ZipFile(raw.cached(SOURCE, zip_url(year)).path)
    out: list[tuple[str, str | None, list[list[str]]]] = []
    for member in sorted(archive.namelist()):
        low = member.lower()
        if member.endswith("/"):
            continue
        if year == 2025:
            if not re.search(r"/557_\d\d(_[^/]*)?\.xlsx$", low):
                continue
            reader = fastexcel.read_excel(archive.read(member))
            for name in reader.sheet_names:
                frame = reader.load_sheet(name, header_row=None, dtypes="string").to_polars()
                rows = [["" if cell is None else str(cell) for cell in row] for row in frame.rows()]
                out.append((member, name, rows[_start(rows) :]))
        elif low.endswith(".csv"):
            rows = list(csv.reader(io.StringIO(decode(archive.read(member))), delimiter=";"))
            out.append((member, None, rows[_start(rows) :]))
    return out


@functools.cache
def contests(year: int) -> tuple[Contest, ...]:
    out: list[Contest] = []
    for member, sheet, rows in _tables(year):
        kind = kind_of(sheet if sheet is not None else member)
        if kind is None and round_of(member) == 3:
            kind = "mayor"  # the third round is one municipality's mayoral vote, filed under the municipality's name
        if kind is None:
            raise ValueError(f"{member}: of no known kind")
        header, body = rows[0], [row for row in rows[1:] if any(cell.strip() for cell in row)]
        width = len(header)
        stations: list[Station] = []
        for row in body:
            if len(row) != width:
                raise ValueError(f"{member}: a row of {len(row)} cells under {width} headings")
            registered, voters, cast, valid, invalid = (number(cell) for cell in row[8:13])
            stations.append(
                Station(
                    row[0].zfill(2),
                    clean(row[3]),
                    clean(row[2]).lower(),
                    row[4],
                    clean(row[5]),
                    {REGISTERED: registered, VOTERS: voters, CAST: cast, VALID: valid, INVALID: invalid},
                    [number(cell) for cell in row[13:]],
                )
            )
        towns_ = {(s.county, place(s.muni)) for s in stations}
        counties = {s.county for s in stations}
        if len(counties) != 1 or (kind in ("council", "mayor", "deputy") and len(towns_) != 1):
            raise ValueError(f"{member}: spans {sorted(counties)} and {len(towns_)} towns")
        first = stations[0]
        out.append(
            Contest(
                year,
                round_of(member),
                kind,
                first.county,
                place(first.muni) if kind in ("council", "mayor", "deputy") else "",
                first.muni,
                first.muni_kind,
                [clean(cell) for cell in header[13:]],
                stations,
                member if sheet is None else f"{member}#{sheet}",
                clean(body[0][1]),
            )
        )
    unique: dict[tuple[object, ...], Contest] = {}
    for c in out:
        attach(c)
        first = unique.setdefault((c.round, *c.ident), c)
        # The 2017 second round files the Zagreb mayor twice, under two folders; the copies are the same table.
        if first is not c and (first.header, first.stations) != (c.header, c.stations):
            raise ValueError(f"{c.member} and {first.member} are two different tables for one contest")
    return tuple(unique.values())


# The archive's own totals.


@dataclass
class SOption:
    rbr: int
    name: str
    parties: list[str]
    votes: int


@dataclass
class Summary:
    url: str
    registered: int
    voters: int
    ballots: int
    valid: int
    invalid: int
    options: list[SOption] = field(default_factory=list)


def _suffix(text: str) -> str:
    return clean(re.sub(r"\s*-\s*(nositelj(ica)?|kandidat\w*|zamjenik\w*)\b.*$", "", clean(text), flags=re.IGNORECASE))


@functools.cache
def summary(url: str) -> Summary | None:
    try:
        data = raw.cached(SOURCE, url).path.read_bytes()
    except LookupError:
        return None
    if url.endswith(".json"):
        body = json.loads(decode(data))
        return Summary(
            url,
            int(body["biraciUkupno"]),
            int(body["biraciGlasovalo"]),
            int(body["listiciUkupno"]),
            int(body["listiciVazeci"]),
            int(body["listiciNevazeci"]),
            [
                SOption(
                    int(entry["rbr"]),
                    _suffix(entry["naziv"]),
                    [clean(p["naziv"]) for p in entry.get("strankePuniNaziv") or []],
                    int(entry["glasova"]),
                )
                for entry in body["lista"]
            ],
        )
    rows = [row for row in csv.reader(io.StringIO(decode(data)), delimiter=";") if any(c.strip() for c in row)]
    head = rows[0]
    options = []
    for row in rows:
        names = [clean(c) for c in row[30:39] if clean(c) and not clean(c).startswith("-")]
        options.append(
            SOption(
                int(row[19]),
                _suffix(names[0]) if names else "",
                [clean(c) for c in row[20:30] if clean(c)],
                number(row[-2]),
            )
        )
    return Summary(
        url, number(head[11]), number(head[12]), number(head[14]), number(head[15]), number(head[17]), options
    )


TYPES = {
    "council": ("08",),
    "mayor": ("17",),
    "deputy": ("21", "25"),
    "assembly": ("06",),
    "governor": ("15",),
    "county_deputy": ("19", "27"),
}
TITLES = {"dipl", "ing", "iur", "oec", "univ", "bacc", "mag", "med", "stom", "pharm", "spec", "struc", "prof", "doc"}


def words(text: str) -> set[str]:
    return {w for w in label(text).split() if len(w) > 2 and w not in TITLES and not w.isdigit()}


def similar(a: str, b: str) -> float:
    left, right = words(a), words(b)
    return 2 * len(left & right) / (len(left) + len(right)) if left and right else 0.0


def _strip(name: str) -> str:
    """A candidate's name without the academic titles the files print after a comma."""
    return clean(name.split(",")[0]) if "," in name else clean(name)


def link(header: list[str], sums: list[int], options: list[SOption]) -> list[int | None]:
    """For each column of a contest's tables, the summary option it is: the summary orders its options by votes."""
    out: list[int | None] = [None] * len(header)
    used: set[int] = set()
    by_votes: dict[int, list[int]] = {}
    for index, o in enumerate(options):
        by_votes.setdefault(o.votes, []).append(index)

    def close(name: str, index: int) -> float:
        return max(similar(name, options[index].name), similar(name, " ".join(options[index].parties)))

    for column, (name, votes) in enumerate(zip(header, sums, strict=True)):
        found = [i for i in by_votes.get(votes, []) if i not in used]
        if found:
            best = max(found, key=lambda i: close(name, i))
            out[column] = best
            used.add(best)
    for column, name in enumerate(header):
        if out[column] is None:
            rest = [i for i in range(len(options)) if i not in used]
            best = max(rest, key=lambda i: close(name, i), default=None)
            if best is not None and close(name, best) >= 0.6:
                out[column] = best
                used.add(best)
    return out


@functools.cache
def towns(year: int) -> dict[tuple[str, str], str]:
    """(county, folded name) -> the page's code for the town or municipality."""
    out: dict[tuple[str, str], str] = {}
    for code, (county, name) in listing(year)[0].items():
        out.setdefault((county, place(name)), code)
    return out


def attach(c: Contest) -> None:
    """Find the archive's summary file for a contest and, for a deputy contest, the minority that elects it."""
    town = "0000" if c.level == "county" else towns(c.year).get((c.county, c.place))
    if town is None:
        return
    labels = {(k, cty, t): text for k, cty, t, text in listing(c.year)[1]}
    sums = [sum(s.votes[i] for s in c.stations) for i in range(len(c.header))]
    best: tuple[int, str, Summary, list[int | None]] | None = None
    for kind in TYPES["governor" if c.kind == "mayor" and c.county == ZAGREB else c.kind]:
        found = summary(summary_name(kind, c.county, town, c.round, c.year))
        if found is None:
            continue
        linked = link(c.header, sums, found.options)
        score = sum(1 for x in linked if x is not None)
        if best is None or score > best[0]:
            best = (score, kind, found, linked)
    if best is None or (c.kind in ("deputy", "county_deputy") and best[0] == 0):
        return
    _, kind, c.summary, c.link = best
    text = labels.get((kind, c.county, town))
    if c.kind in ("deputy", "county_deputy") and text:
        c.deputy = text


# Parties.

INDEPENDENT = (
    "kandidacijska lista grupe biraca",
    "kandidat grupe biraca",
    "grupa biraca",
    "nezavisna lista",
    "nezavisni kandidat",
    "neovisna lista",
    "neovisni kandidat",
    "nezavisni",
)


def party_kind(name: str) -> bool:
    folded = label(name)
    return not (folded.startswith(INDEPENDENT) or "grupe biraca" in folded or folded.startswith("nk "))


def split_names(text: str) -> list[str]:
    """The parties of a coalition label, which the archive prints as one string."""
    if "\n" in text:
        return [clean(part) for part in text.split("\n") if clean(part)]
    out: list[str] = []
    current = ""
    for fragment in text.split(", "):
        current = f"{current}, {fragment}" if current else fragment
        if re.search(r" - [^,]{1,40}$", current):
            out.append(clean(current))
            current = ""
    if current:
        out.append(clean(current))
    return out


def option_names(c: Contest, column: int) -> list[str]:
    """The parties behind a ballot column: the summary's own list when the column is linked to it."""
    index = c.link[column] if c.link else None
    if c.summary is not None and index is not None:
        return c.summary.options[index].parties
    if c.kind in ("council", "assembly"):
        return split_names(c.header[column])
    return []


def party_id(name: str) -> str:
    return key("party", label(name))


def bloc_id(names: list[str]) -> str:
    return key("party", "bloc", *sorted(label(n) for n in names))


def option_party(c: Contest, column: int) -> str | None:
    names = [n for n in option_names(c, column) if party_kind(n)]
    if len(names) == 1:
        return party_id(names[0])
    if len(names) > 1:
        return bloc_id(names)
    return None


def abbreviation(name: str) -> str | None:
    found = re.search(r" - ([^-]{1,30})$", clean(name))
    return found.group(1).strip() if found else None


# Bodies, offices, and electoral systems.


def system(name: str) -> str:
    return key("electoral_system", name)


SYSTEMS = [
    (
        system("council"),
        {"formula": "dhondt", "threshold": {"level": "list", "scope": "contest", "percent": 5}},
        (
            "A county assembly, the Zagreb City Assembly, and each town and municipal council is elected from closed "
            "lists in one constituency, with seats divided by the D'Hondt method among the lists that win at least 5% "
            "of the valid votes (Zakon o lokalnim izborima, NN 144/2012). Where a national minority reaches the "
            "thresholds of the Constitutional Act on National Minorities, seats are reserved for it. The results "
            "files do not print the seats each list won, so none are recorded."
        ),
    ),
    (
        system("executive"),
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A mayor, a župan, or a deputy elected by a national minority is chosen in a vote in which a candidate "
            "who wins more than half of the valid votes is elected; otherwise the two leading candidates meet in a "
            "second round two weeks later (Zakon o lokalnim izborima, art. 94)."
        ),
    ),
]
MINORITIES = {
    "srpsk": "Serb",
    "talijansk": "Italian",
    "madjarsk": "Hungarian",
    "cesk": "Czech",
    "romsk": "Romani",
    "rusinsk": "Rusyn",
    "bosnjack": "Bosniak",
    "slovack": "Slovak",
    "albansk": "Albanian",
    "ukrajinsk": "Ukrainian",
    "slovensk": "Slovene",
    "makedonsk": "Macedonian",
    "njemack": "German",
    "hrvatskog naroda": "Croatian people",
}


def minority(text: str) -> str:
    low = fold(text)
    for stem, english in MINORITIES.items():
        if fold(stem) in low:
            return english
    return "a national minority"


def county_title(name: str) -> str:
    return (
        title(re.sub(r"\s+županija$", "", clean(name), flags=re.IGNORECASE))
        if name.upper() != "GRAD ZAGREB"
        else "Zagreb"
    )


def muni_title(name: str) -> str:
    return title(clean(name).split(" - ")[0])


def names_of(c: Contest) -> tuple[str, str, str, str]:
    """English and Croatian names of the body or office a contest fills, and of the contest itself, with a slug."""
    zagreb = c.county == ZAGREB
    if c.level == "county":
        who = "Zagreb" if zagreb else county_title(c.county_name)
        place_en = who if zagreb else f"{who} County"
        place_hr = "Grada Zagreba" if zagreb else f"{who} županija"
        where = f"{c.county}-{slug(who)}"
    else:
        who = muni_title(c.muni)
        place_en = who
        place_hr = who
        where = f"{c.county}-{slug(who)}"
    town = c.muni_kind == "grad"
    if c.kind == "council":
        english = f"{who} {'Town' if town else 'Municipal'} Council"
        native = f"{'Gradsko' if town else 'Općinsko'} vijeće: {who}"
        return english, native, where, "council"
    if c.kind == "assembly":
        english = "Zagreb City Assembly" if zagreb else f"{place_en} Assembly"
        native = "Skupština Grada Zagreba" if zagreb else f"Županijska skupština: {place_hr}"
        return english, native, where, "assembly"
    if c.kind == "governor":
        return f"Prefect of {place_en}", f"Župan: {place_hr}", where, "prefect"
    if c.kind == "mayor":
        native = "Gradonačelnik Grada Zagreba" if zagreb else f"{'Gradonačelnik' if town else 'Načelnik'}: {who}"
        return f"Mayor of {place_en}", native, where, "mayor"
    group = minority(c.deputy)
    owner = f"the {place_en}" if c.kind == "county_deputy" else place_en
    english = f"Deputy {'prefect' if c.kind == 'county_deputy' else 'mayor'} of {owner.removeprefix('the ')} ({group})"
    native = f"Zamjenik ({group}): {place_hr if c.kind == 'county_deputy' else who}"
    return english, native, where, f"deputy-{slug(group)}"


OFFICE_KIND = {"governor": "governor", "mayor": "mayor", "deputy": "deputy_mayor", "county_deputy": "deputy_mayor"}


@functools.cache
def all_contests() -> tuple[Contest, ...]:
    return tuple(c for year in YEARS for c in contests(year))


GEOBOUNDARIES = "https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/HRV"
GB_COUNTIES = f"{GEOBOUNDARIES}/ADM1/geoBoundaries-HRV-ADM1.geojson"
GB_TOWNS = f"{GEOBOUNDARIES}/ADM2/geoBoundaries-HRV-ADM2.geojson"
COUNTIES_BY_SHAPE = {
    "Zagreb County": "01",
    "Krapina-Zagorje": "02",
    "Sisak-Moslavina": "03",
    "Karlovac": "04",
    "Varaždin": "05",
    "Koprivnica-Križevci": "06",
    "Bjelovar-Bilogora": "07",
    "Primorje-Gorski Kotar": "08",
    "Lika-Senj": "09",
    "Virovitica-Podravina": "10",
    "Požega-Slavonia": "11",
    "Brod-Posavina": "12",
    "Zadar County": "13",
    "Osijek-Baranja": "14",
    "Šibenik-Knin": "15",
    "Vukovar-Syrmia": "16",
    "Split-Dalmatia": "17",
    "Istria": "18",
    "Dubrovnik-Neretva": "19",
    "Međimurje": "20",
    "City of Zagreb": "21",
}
# Spellings that differ between the shapes' names and the commission's, each the only unmatched shape for its unit.
SHAPE_ALIASES = {
    "hvratska dubica": "hrvatska dubica",
    "veliki pisanica": "velika pisanica",
    "kastelir": "kastelir labinci",
    "magdenovac": "magadenovac",
    "malinska": "malinska dubasnica",
    "donji kukuzari": "donji kukuruzari",
    "opicina muter kornati": "murter",
    "opicina pirovac": "pirovac",
}
BOUNDARIES_FROM = date(2013, 1, 1)
# The State Geodetic Administration's shapes (hr.granice) take over from the 2021 census units.
BOUNDARIES_TO = date(2021, 1, 1)


def _shape(feature: dict[str, object]) -> shapely.Geometry:
    found = shapely.make_valid(shapely.geometry.shape(feature["geometry"]))  # type: ignore[arg-type]
    parts = [part for part in shapely.get_parts(found) if shapely.get_type_id(part) in (3, 6)]
    polygons = [q for part in parts for q in shapely.get_parts(part)]
    return shapely.set_srid(shapely.MultiPolygon(polygons), 4326)


def _geometry(p: Package, area: str, shape: shapely.Geometry, source: str, quality: str = "osm") -> None:
    p.add(
        "area_geometry",
        area_id=area,
        valid_from=BOUNDARIES_FROM,
        valid_to=BOUNDARIES_TO,
        geom=shapely.to_wkb(shape, hex=True, include_srid=True),
        label_point=shapely.to_wkb(
            shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
        ),
        quality=quality,
        source_id=source,
    )


def shaped() -> tuple[dict[str, shapely.Geometry], dict[tuple[str, str], shapely.Geometry]]:
    """The county shapes by county number and the town shapes by (county number, folded name)."""
    counties_file = json.loads(raw.cached(SOURCE, GB_COUNTIES).path.read_bytes())
    counties = {COUNTIES_BY_SHAPE[f["properties"]["shapeName"]]: _shape(f) for f in counties_file["features"]}
    names: dict[str, set[str]] = {}
    for c in all_contests():
        if c.level == "muni":
            names.setdefault(c.place, set()).add(c.county)
    out: dict[tuple[str, str], shapely.Geometry] = {}
    for f in json.loads(raw.cached(SOURCE, GB_TOWNS).path.read_bytes())["features"]:
        name = f["properties"]["shapeName"]
        if name.startswith("Otok "):
            continue  # an island, which holds several municipalities
        folded = place(re.sub(r"^(općina|grad)\s+", "", name, flags=re.IGNORECASE))
        folded = SHAPE_ALIASES.get(folded, folded)
        counties_of = names.get(folded)
        if not counties_of:
            continue
        shape = _shape(f)
        if len(counties_of) > 1:
            point = shapely.point_on_surface(shape)
            home = [code for code in counties_of if counties[code].contains(point)]
            if len(home) != 1:
                continue
            county = home[0]
        else:
            county = next(iter(counties_of))
        out.setdefault((county, folded), shape)
    return counties, out


def official() -> dict[tuple[str, str], shapely.Geometry]:
    """The towns and municipalities with local elections that the geoBoundaries extract omits, from the State
    Geodetic Administration's statistical units. Their boundaries did not change between 2013 and 2021."""
    _, found = shaped()
    wanted = {(c.county, c.place) for c in all_contests() if c.level == "muni"}
    return {k: shape for k, shape in sorted(granice.towns().items()) if k in wanted and k not in found}


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Boundaries for Croatia's counties and the towns and municipalities that elect local councils, from "
        "OpenStreetMap through geoBoundaries",
        depends=[f"{IZBORI}/geo"],
    )
    county_source, town_source = key("source", "geoboundaries-adm1"), key("source", "geoboundaries-adm2")
    for id, title_, license_, license_url, notes in (
        (
            county_source,
            "geoBoundaries gbOpen: Croatia ADM1 (counties)",
            "Open Data Commons Open Database License 1.0 (OpenStreetMap contributors)",
            "https://opendatacommons.org/licenses/odbl/1-0/",
            "Boundary year 2017, built December 2023.",
        ),
        (
            town_source,
            "geoBoundaries gbOpen: Croatia ADM2 (municipalities and towns)",
            "Creative Commons Attribution-ShareAlike 2.0 (OpenStreetMap contributors)",
            "https://www.openstreetmap.org/copyright",
            (
                "Boundary year 2021, built December 2023. Shapes of islands are not used, and a municipality the "
                "extract does not draw is left without a shape."
            ),
        ),
    ):
        p.add(
            "source",
            id=id,
            kind="academic",
            publisher="geoBoundaries (William & Mary geoLab), from OpenStreetMap",
            title=title_,
            url="https://www.geoboundaries.org/",
            license=license_,
            license_url=license_url,
            attribution="geoBoundaries; © OpenStreetMap contributors",
            notes=notes,
        )
    p.artifact(raw.cached(SOURCE, GB_COUNTIES), county_source)
    p.artifact(raw.cached(SOURCE, GB_TOWNS), town_source)
    counties, towns_ = shaped()
    for code, shape in sorted(counties.items()):
        _geometry(p, izbori_area("hr.zupanija", code), shape, county_source)
    for (county, folded), shape in sorted(towns_.items()):
        _geometry(p, izbori_area("hr.opcina", f"{county}:{folded}"), shape, town_source)
    gaps = official()
    if gaps:
        dgu = key("source", "dgu-statistical-units")
        p.add(
            "source",
            id=dgu,
            kind="official_body",
            publisher="Državna geodetska uprava",
            title="Statistical units of the 2021 census (INSPIRE AreaStatisticalUnit, towns and municipalities)",
            url=granice.CATALOG,
            license="Open license of the Republic of Croatia (Otvorena dozvola)",
            license_url="https://data.gov.hr/otvorena-dozvola",
            attribution="Državna geodetska uprava (DGU), Državni zavod za statistiku (DZS)",
            notes=(
                "Used only for the towns and municipalities the geoBoundaries extract omits, the islands of Cres, "
                "Krk, and Lošinj among them. Boundaries valid for 2021, in ETRS89 and simplified to about 20 meters "
                "as one coverage, and applied back to 2013 because these units did not change."
            ),
        )
        p.artifact(raw.cached(granice.SOURCE, granice.UNITS), dgu)
        for (county, folded), shape in gaps.items():
            _geometry(p, izbori_area("hr.opcina", f"{county}:{folded}"), shape, dgu, "official")
    return p


def unshaped() -> list[str]:
    _, found = shaped()
    found = {**found, **official()}
    return sorted(
        {
            f"{c.muni_kind} {muni_title(c.muni)} ({c.county})"
            for c in all_contests()
            if c.level == "muni" and (c.county, c.place) not in found
        }
    )


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The county assemblies, town and municipal councils, mayoral, župan, and deputy offices, electoral systems, "
        "and parties of Croatia's local elections since 2013, and the source of their results",
    )
    p.external.add(CROATIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Državno izborno povjerenstvo Republike Hrvatske",
        title="Arhiva izbora: lokalni izbori, rezultati po biračkim mjestima i ukupni rezultati",
        url=f"{SITE}/",
        license="Official results of the Republic of Croatia, published for public use; reuse with the source "
        "acknowledged",
        license_url=f"{SITE}/",
        attribution="Državno izborno povjerenstvo Republike Hrvatske (DIP), izbori.hr",
    )
    for id, rules, method in SYSTEMS:
        p.add(
            "electoral_system",
            id=id,
            polity_id=CROATIA,
            valid_from=date(2013, 1, 1),
            rules=rules,
            rules_version=1,
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    seen: set[str] = set()
    for c in sorted(all_contests(), key=lambda c: -c.year):
        english, native, _, _ = names_of(c)
        # An office or body is the same across years and rounds; it is named once, from the latest year's spelling.
        id = key("body" if c.kind in ("council", "assembly") else "office", *c.ident)
        if id in seen:
            continue
        seen.add(id)
        if c.kind in ("council", "assembly"):
            p.add("body", id=id, polity_id=CROATIA, kind="council")
        else:
            p.add("office", id=id, polity_id=CROATIA, kind=OFFICE_KIND[c.kind])
        p.name(id, english, "en", "official")
        p.name(id, native, "hr", "native", False)
    spellings: dict[str, Counter[str]] = {}
    blocs: dict[str, tuple[list[str], Counter[str]]] = {}
    for c in all_contests():
        for column, text in enumerate(c.header):
            names = [n for n in option_names(c, column) if party_kind(n)]
            for n in names:
                spellings.setdefault(party_id(n), Counter())[n] += 1
            if len(names) > 1:
                blocs.setdefault(bloc_id(names), (names, Counter()))[1][
                    text if c.kind in ("council", "assembly") else ""
                ] += 1
    for id, names in sorted(spellings.items()):
        name = names.most_common(1)[0][0]
        p.add("party", id=id, kind="party", polity_id=CROATIA)
        p.add("external_id", entity_id=id, scheme="hr.lokalni.stranka", value=label(name))
        p.name(id, name, "hr", "official")
        short = abbreviation(name)
        if short:
            p.name(id, short, "hr", "abbreviation", False)
    for id, (members, labels) in sorted(blocs.items()):
        p.add("party", id=id, kind="bloc", polity_id=CROATIA)
        p.name(id, ", ".join(sorted({clean(m) for m in members})), "hr", "official")
    return p


# One package per round.


METHOD = (
    "Counted per polling station (biračko mjesto) as published by the State Electoral Commission "
    "(Državno izborno povjerenstvo) in its results archive. Registered voters are the polling station's roll, voters "
    "are those who signed the roll, ballots cast are the ballots found in the box, and valid and invalid ballots are as "
    "printed. A polling station is placed in its town or municipality. The commission's printed totals for a contest, "
    "where its results pages carry them, are stored as declared values beside the station counts."
)
CAVEAT = (
    "The commission publishes the elected candidates only as PDF reports. The outcome recorded here follows from the "
    "counted votes and the rule of Art. 94 of the Zakon o lokalnim izborima: a candidate with more than half of the "
    "valid votes is elected in the first round, the leading two advance, and the leader of the second round is "
    "elected."
)


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(package_key(r) for r in rounds())]


def parse() -> None:
    geo().write()
    registry().write()
    for r in rounds():
        election(r).write()


def emit() -> None:
    package.run("load", packages())


def election_names(r: Round) -> tuple[str, str, str]:
    if r.tag:
        return (
            f"{r.year} Kraljevec na Sutli mayoral election, third round",
            f"Izbor općinskog načelnika Općine Kraljevec na Sutli {r.year}., treći krug",
            f"hr/{r.tag}-mayor-third-round",
        )
    if r.number == 1:
        return f"{r.year} Croatian local elections", f"Lokalni izbori {r.year}.", f"hr/{r.start}-local"
    return (
        f"{r.year} Croatian local elections, second round",
        f"Lokalni izbori {r.year}., drugi krug",
        f"hr/{r.start}-local-runoff",
    )


def person_key(c: Contest, name: str) -> str:
    return " ".join(sorted(words(_strip(name))))


def person(c: Contest, name: str) -> str:
    return key("person", c.year, *c.ident, person_key(c, name))


def previous_of(c: Contest) -> Contest | None:
    return next((x for x in contests(c.year) if x.round == c.round - 1 and x.ident == c.ident), None)


def next_of(c: Contest) -> Contest | None:
    return next((x for x in contests(c.year) if x.round == c.round + 1 and x.ident == c.ident), None)


def verdicts(c: Contest) -> dict[int, str]:
    """Which candidates a single-seat contest elected, advanced, or eliminated, from its counted votes."""
    sums = [sum(s.votes[i] for s in c.stations) for i in range(len(c.header))]
    valid = sum(sums)
    if not sums or valid == 0:
        return {}
    if c.round == 1:
        for column, votes in enumerate(sums):
            if 2 * votes > valid:
                return {i: ("elected" if i == column else "lost") for i in range(len(sums))}
        later = next_of(c)
        if later is None:
            return {}
        advancing = {person_key(c, h) for h in later.header}
        return {i: ("advanced" if person_key(c, h) in advancing else "eliminated") for i, h in enumerate(c.header)}
    top = max(sums)
    leaders = [i for i, v in enumerate(sums) if v == top]
    if len(leaders) > 1:
        return {i: ("tied" if i in leaders else "lost") for i in range(len(sums))}
    return {i: ("elected" if i == leaders[0] else "lost") for i in range(len(sums))}


class Facts:
    """Result values gathered column by column, since a year of local elections holds a few hundred thousand."""

    def __init__(self) -> None:
        names = ("result_set_id", "unit_id", "option_id", "measure", "value")
        self.columns: dict[str, list[object]] = {name: [] for name in names}

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
        for column, item in zip(self.columns.values(), (result_set, unit, option, measure, value), strict=True):
            column.append(item)

    def frame(self) -> pl.DataFrame:
        return pl.DataFrame(
            self.columns,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16))


def district(c: Contest) -> str:
    if c.level == "county":
        return izbori_area("hr.zupanija", c.county)
    return izbori_area("hr.opcina", f"{c.county}:{c.place}")


def election(r: Round) -> Package:
    english, native, path = election_names(r)
    depends = [f"{IZBORI}/geo", f"{SOURCE}/registry"]
    if r.number > 1:
        depends += [package_key(x) for x in rounds() if x.year == r.year and x.number < r.number]
    p = Package(
        package_key(r), SOURCE, f"The {english} by polling station from the State Electoral Commission", depends
    )
    p.external.add(CROATIA)
    if r.number == 1:
        p.artifact(raw.cached(SOURCE, zip_url(r.year)), SOURCE_ID)
    else:
        p.uses(raw.cached(SOURCE, zip_url(r.year)))
    # The page's own script names the minority each deputy contest is for and the code of each town's summary files.
    p.uses(raw.cached(SOURCE, dao_url(r.year)))
    election_id = key("election", r.year, r.number)
    p.add(
        "election",
        id=election_id,
        polity_id=CROATIA,
        kind="local" if r.number == 1 else "runoff",
        date_from=r.start,
        date_to=r.end,
        date_precision=r.precision,
        status="held",
    )
    p.name(election_id, english, "en", "official")
    p.name(election_id, native, "hr", "native", False)
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    p.add("note", id=key("note", election_id), entity_id=election_id, kind="methodology", body_md=METHOD, lang="en")
    values, units = Facts(), set()
    cited: set[str] = set()
    problems: list[str] = []
    here = [c for c in contests(r.year) if c.round == r.number]
    owned = {
        person(c, h)
        for c in contests(r.year)
        if c.round < r.number and c.kind not in ("council", "assembly")
        for h in c.header
    }
    for c in here:
        english_name, native_name, where, tail = names_of(c)
        contest_id = key("contest", r.year, r.number, *c.ident)
        result_set = key("result_set", r.year, r.number, *c.ident)
        listing_ = c.kind in ("council", "assembly")
        before = previous_of(c)
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="list_allocation" if listing_ else "office",
            office_id=None if listing_ else key("office", *c.ident),
            body_id=key("body", *c.ident) if listing_ else None,
            district_area_id=district(c),
            magnitude=None if listing_ else 1,
            round_number=r.number,
            previous_round_id=key("contest", r.year, r.number - 1, *c.ident) if before is not None else None,
            electoral_system_id=system("council" if listing_ else "executive"),
            default_result_kind="counted",
        )
        p.name(contest_id, english_name, "en", "official")
        p.name(contest_id, native_name, "hr", "native", False)
        p.add("slug", path=f"{path}/{where}/{tail}", entity_id=contest_id, is_current=True)
        sums = [sum(s.votes[i] for s in c.stations) for i in range(len(c.header))]
        total = c.totals
        declared = c.declared
        if declared is None and c.summary is not None:
            problems.append(f"{c.member}: columns the summary does not carry")
        completeness = 1.0
        if declared is not None and declared.valid:
            completeness = round(min(total[VALID] / declared.valid, 1.0), 6)
            if declared.url not in cited:
                cited.add(declared.url)
                p.artifact(raw.cached(SOURCE, declared.url), SOURCE_ID)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=completeness,
        )
        verdict = {} if listing_ else verdicts(c)
        if verdict:
            p.add("note", id=key("note", result_set), entity_id=result_set, kind="caveat", body_md=CAVEAT, lang="en")
        options: list[str] = []
        for column, name in enumerate(c.header):
            option = key("option", r.year, r.number, *c.ident, column + 1)
            options.append(option)
            party = option_party(c, column)
            if listing_:
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest_id,
                    kind="list",
                    party_id=party,
                    ballot_position=column + 1,
                    ballot_label=name,
                )
                continue
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="candidate",
                party_id=party,
                ballot_position=column + 1,
                ballot_label=name,
            )
            who = person(c, name)
            if who not in owned:
                p.add("person", id=who)
                p.name(who, title(_strip(name)), "hr", "official")
                owned.add(who)
            p.add(
                "candidacy",
                id=key("candidacy", r.year, r.number, *c.ident, column + 1),
                person_id=who,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=party,
                is_independent=party is None and declared is not None,
            )
            if column in verdict:
                p.add("outcome", result_set_id=result_set, option_id=option, kind=verdict[column])
        for s in c.stations:
            code = f"{r.start}:{r.number}:{s.county}:{place(s.muni)}:{s.bm}"
            unit = key("unit", code)
            if code not in units:
                units.add(code)
                p.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=CROATIA,
                    scheme_code="hr.bm",
                    code=code,
                    area_relation="within",
                    area_id=izbori_area("hr.opcina", f"{s.county}:{place(s.muni)}"),
                    kind="standard",
                )
                p.name(unit, f"{s.name}, {muni_title(s.muni)}" if s.name else muni_title(s.muni), "hr", "official")
            for measure, value in s.counts.items():
                values.add(result_set, unit, None, measure, value)
            for column, value in enumerate(s.votes):
                values.add(result_set, unit, options[column], VOTES, value)
        if declared is not None:
            for measure, value in (
                (REGISTERED, declared.registered),
                (VOTERS, declared.voters),
                (CAST, declared.ballots),
                (VALID, declared.valid),
                (INVALID, declared.invalid),
            ):
                p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
            for column, index in enumerate(c.link):
                assert index is not None
                p.add(
                    "declared",
                    result_set_id=result_set,
                    option_id=options[column],
                    measure=VOTES,
                    value=declared.options[index].votes,
                )
        del sums
    p.frame("result_value", values.frame())
    for line in problems:
        print(f"{p.key}: {line}", flush=True)
    _accept(p, here)
    return p


def _accept(p: Package, here: list[Contest]) -> None:
    short = [
        f"{names_of(c)[0]} (stations sum to {c.totals[VALID]:,} valid votes and {c.totals[REGISTERED]:,} registered "
        f"voters, the printed summary {d.valid:,} and {d.registered:,})"
        for c in here
        if (d := c.declared) is not None
        and (c.totals[VALID], c.totals[REGISTERED], c.totals[INVALID]) != (d.valid, d.registered, d.invalid)
    ]
    if short:
        p.accept(
            "accounting.declared",
            "The commission's printed contest totals differ from the sum of its own polling-station tables in "
            f"{len(short)} contests: " + "; ".join(short),
        )
