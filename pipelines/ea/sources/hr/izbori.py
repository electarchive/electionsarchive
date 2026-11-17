import csv
import functools
import io
import json
import re
import subprocess
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import fastexcel
import httpx
import polars as pl

from ea import package, raw
from ea.package import Package

SOURCE = "hr.izbori"
SITE = "https://www.izbori.hr/arhiva-izbora"
DATA = f"{SITE}/data"
CROATIA = str(uuid5(package.NAMESPACE, "polity\x1fHR"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fHRV"))
VOTES, REGISTERED, CAST, VALID, INVALID, PREFERENCE, VOTERS = 1, 2, 3, 4, 5, 7, 14


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)


# The authority's archive of every election it ran; each entry is one election day.
@dataclass(frozen=True)
class Day:
    family: str  # parlament, predsjednik, referendum, euparlament
    year: int  # the archive folder
    day: date
    round: int = 1
    previous: date | None = None


PARLIAMENT = {
    2000: date(2000, 1, 3),
    2003: date(2003, 11, 23),
    2007: date(2007, 11, 25),
    2011: date(2011, 12, 4),
    2015: date(2015, 11, 8),
    2016: date(2016, 9, 11),
    2020: date(2020, 7, 5),
    2024: date(2024, 4, 17),
}
PRESIDENT = {
    2000: (date(2000, 1, 24), date(2000, 2, 7)),
    2005: (date(2005, 1, 2), date(2005, 1, 16)),
    2009: (date(2009, 12, 27), date(2010, 1, 10)),
    2014: (date(2014, 12, 28), date(2015, 1, 11)),
    2019: (date(2019, 12, 22), date(2020, 1, 5)),
    2024: (date(2024, 12, 29), date(2025, 1, 12)),
}
REFERENDUM = {2012: date(2012, 1, 22), 2013: date(2013, 12, 1)}
EUROPARLAMENT = {
    2013: date(2013, 4, 14),
    2014: date(2014, 5, 25),
    2019: date(2019, 5, 26),
    2024: date(2024, 6, 9),
}
EP_SEATS = {2013: 12, 2014: 11, 2019: 11, 2024: 12}


def days() -> list[Day]:
    out = [Day("parlament", year, day) for year, day in PARLIAMENT.items()]
    for year, (first, second) in PRESIDENT.items():
        out += [Day("predsjednik", year, first), Day("predsjednik", year, second, 2, first)]
    out += [Day("referendum", year, day) for year, day in REFERENDUM.items()]
    out += [Day("euparlament", year, day) for year, day in EUROPARLAMENT.items()]
    return sorted(out, key=lambda d: d.day)


def zip_url(d: Day) -> str:
    return f"{DATA}/{d.family}/{d.year}/zip/rezultati.zip"


def clean(text: object) -> str:
    return " ".join(str(text if text is not None else "").replace("\xa0", " ").split())


def fold(text: object) -> str:
    plain = unicodedata.normalize("NFKD", clean(text).replace("đ", "d").replace("Đ", "D"))
    return " ".join(plain.encode("ascii", "ignore").decode().lower().split())


def decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("undecodable file")


# Reading the per-polling-station files.


@dataclass
class Sheet:
    """One table of the authority's results by polling station (biračko mjesto)."""

    member: str
    contest: str  # parliament: "<district>" or "12-<minority>"; president and referendum: ""
    part: str  # regular, special, abroad, mixed, homeland (diaspora voters voting in Croatia)
    header: list[str]
    rows: list[list[str]]


def number(text: object) -> int:
    value = clean(text).replace(".", "").replace(" ", "")
    if value in ("", "-"):
        return 0
    if not value.isdigit():
        raise ValueError(f"not a count: {text!r}")
    return int(value)


def _rows(archive: zipfile.ZipFile, member: str) -> list[list[str]]:
    data = archive.read(member)
    if member.lower().endswith(".xlsx"):
        reader = fastexcel.read_excel(data)
        frame = reader.load_sheet(reader.sheet_names[0], header_row=None, dtypes="string").to_polars()
        return [["" if cell is None else str(cell) for cell in row] for row in frame.rows()]
    return list(csv.reader(io.StringIO(decode(data)), delimiter=";"))


def _header(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows[:10]):
        if any(fold(cell) in REGISTERED_LABELS for cell in row):
            return index
    raise ValueError("no header row")


REGISTERED_LABELS = {"ukupno biraca", "broj biraca", "upisano biraca"}


def _part(name: str, family: str, contest: str) -> str:
    low = fold(name.rsplit("/", 1)[-1])
    if "posebn" in low:
        return "special"
    if "mjesovit" in low or "mjeovit" in low or "mje" in low and "ovita bm" in low:
        return "mixed"
    if "u_rh" in low or "u rh" in low:
        return "homeland" if family == "parlament" else "regular"
    if "inozem" in low:
        return "abroad"
    if family == "parlament" and contest == "11":
        return "abroad"
    return "regular"


def _skip(name: str) -> bool:
    low = fold(name)
    return any(word in low for word in ("sumari", "gradovi", "grdovi", "drzave", "stranke", "sheet1"))


def _parliament_contest(year: int, name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    patterns = {
        2000: r"ij(\d\d)_ij",
        2003: r"IJ(\d\d)(?:-(\d))?",
        2007: r"^(\d\d)_[IVX]+(?:_nm(\d))?_izborna",
        2011: r"^(\d\d)_[IVX]+(?:_nm(\d))?_izborna",
        2016: r"^0(\d\d)_(\d\d)_rezultati",
    }
    found = re.search(patterns.get(year, r"^(\d\d)_(\d\d)_rezultati"), base)
    if found is None:
        raise ValueError(f"{name}: no district")
    first, second = (*found.groups(), None)[:2]
    if year in (2015, 2020, 2024):
        return str(int(str(second))) if first == "02" else f"12-{int(str(first)) // 10}"
    if year == 2016:
        return str(int(str(first))) if second == "00" else f"12-{int(str(second)) // 10}"
    return f"12-{int(second)}" if second else str(int(str(first)))


def _round(name: str) -> int:
    low = fold(name.rsplit("/", 1)[-1] if "krug" in fold(name.rsplit("/", 1)[-1]) else name)
    found = re.search(r"([12])\.? ?_?krug|krug ?_?([12])|bm\((\d)\)|(prvi|drugi) krug", low)
    if found is None:
        raise ValueError(f"{name}: no round")
    text = next(group for group in found.groups() if group)
    return {"prvi": 1, "drugi": 2}.get(text, int(text) if text.isdigit() else 0)


@functools.cache
def sheets(family: str, year: int) -> tuple[Sheet, ...]:
    archive = zipfile.ZipFile(raw.cached(SOURCE, f"{DATA}/{family}/{year}/zip/rezultati.zip").path)
    names = [n for n in archive.namelist() if n.lower().endswith(".csv") and not _skip(n)]
    if family == "euparlament":
        # Each archive holds the counts by list and, from 2019, a second file of the same stations with percentages.
        names = [n for n in names if "liste" not in fold(n)]
    if family == "predsjednik" and year >= 2019:
        names = [n for n in archive.namelist() if n.lower().endswith(".xlsx")]
    if family == "parlament" and year == 2003:
        names = [n for n in names if "redovita+mje" in fold(n)]
    out: list[Sheet] = []
    for name in sorted(names):
        rows = _rows(archive, name)
        start = _header(rows)
        contest = _parliament_contest(year, name) if family == "parlament" else ""
        if family == "predsjednik":
            contest = str(_round(name))
        out.append(Sheet(name, contest, _part(name, family, contest), rows[start], rows[start + 1 :]))
    return tuple(out)


ROLES = {
    "registered": REGISTERED_LABELS,
    "voters": {"glasovalo biraca", "glasovalo ukupno"},
    "ballots": {
        "glasovalo biraca (po listicima)",
        "glasovalo biraca , (po listicima)",
        "glasovalo (prema listicimma)",
        "glasovalo biraca (prema listicima)",
        "ukupno listica",
        "listici ukupno",
    },
    "valid": {"vazeci listici", "vazecih listica", "broj vazecih listica", "listici vazeci"},
    "invalid": {"nevazeci listici", "nevazecih listica", "broj nevazecih listica", "listici nevazeci"},
    "bm": {
        "broj birackog mjesta",
        "redni broj birackog mjesta",
        "rbr bm",
        "bm rbr",
        "broj bm",
        "broj biackog mjesta",
        "rbr gm",
        "redni broj glasackog mjesta",
        "biracko mjesto",
    },
    "bm_name": {"naziv bm", "bm naziv", "naziv gm", "glasacko mjesto - naziv", "naziv"},
    "county_no": {"rbr.zupanije", "rbr. zupanije", "rbr zupanije", "redni broj zupanije"},
    "county": {"zupanija", "zupanija/inozemstvo"},
    "muni": {
        "grad/ opcina",
        "grad/opcina",
        "grad / opcina",
        "grad/opcina/drzava",
        "grad / opcina / drzava",
        "grad / opcine",
        "drzava",
    },
    "kind": {"oznaka gr/op/dr", "oznaka grop"},
    "district": {"rbr ij", "izborna jedinica", "rbr izborne jedinice biraca"},
}
IGNORED = (
    "lokacija",
    "adresa",
    "naziv izborne jedinice",
    "izborna jedinica grada",
    "%",
    "(%)",
    "postotak",
)


def roles(header: list[str]) -> tuple[dict[str, int], list[int]]:
    found: dict[str, int] = {}
    options: list[int] = []
    last = -1
    for index, label in enumerate(header):
        text = fold(label.replace("\n", " "))
        role = next((role for role, labels in ROLES.items() if text in labels), None)
        if role is not None and role not in found and not options:
            found[role] = index
            if role in ("registered", "voters", "ballots", "valid", "invalid"):
                last = index
            continue
        if not text or text.startswith(IGNORED) or last < 0 or index <= last:
            continue
        options.append(index)
    if "bm_name" in found and fold(header[found["bm_name"]]) == "naziv" and "muni" in found:
        # The 2000 presidential files name the place in "Naziv" and its kind in "Grad/općina".
        found["kind"] = found["muni"]
        found["muni"] = found.pop("bm_name")
    for role in ("registered", "valid", "invalid"):
        if role not in found:
            raise ValueError(f"no {role} column in {header[:16]}")
    return found, options


def label(text: str) -> str:
    """A column label folded to its words, so the same option matches across files."""
    return " ".join(re.findall(r"[a-z0-9]+", fold(text.replace("\n", " "))))


# The authority's own totals: the files its results pages read, one per contest and round.


MINORITIES = {
    1: "Serb",
    2: "Hungarian",
    3: "Italian",
    4: "Czech and Slovak",
    5: "Austrian, Bulgarian, German, Jewish, Polish, Roma, Romanian, Rusyn, Russian, Turkish, Ukrainian, and Vlach",
    6: "Albanian, Bosniak, Macedonian, Montenegrin, and Slovene",
}


SHORT = {
    1: "serb",
    2: "hungarian",
    3: "italian",
    4: "czech-and-slovak",
    5: "austrian-german-and-others",
    6: "albanian-bosniak-and-others",
}


def contests(d: Day) -> list[str]:
    if d.family != "parlament":
        return [""]
    groups = 5 if d.year == 2000 else 6
    return [str(n) for n in range(1, 12)] + [f"12-{n}" for n in range(1, groups + 1)]


def declared_url(d: Day, contest: str) -> str:
    if d.family == "euparlament":
        return f"{DATA}/euparlament/{d.year}/rezultati/1/r_14_00_0000_000.{'json' if d.year >= 2019 else 'csv'}"
    suffix = "json" if d.year >= 2019 and d.family != "referendum" else "csv"
    if d.family == "predsjednik":
        return f"{DATA}/predsjednik/{d.year}/rezultati/{d.round}/r_01_00_0000_000.{suffix}"
    if d.family == "referendum":
        return f"{DATA}/referendum/{d.year}/rezultati/1/r_81_00_0000_000.csv"
    if contest.startswith("12-"):
        return f"{DATA}/parlament/{d.year}/rezultati/1/r_{contest[3:]}3_012_0000_000.{suffix}"
    return f"{DATA}/parlament/{d.year}/rezultati/1/r_02_{int(contest):03d}_0000_000.{suffix}"


def fetch() -> None:
    from ea.sources.hr import skenovi, zupanijski

    skenovi.fetch()
    zupanijski.fetch()
    for family, year in sorted({(d.family, d.year) for d in days()}):
        raw.fetch(SOURCE, f"{DATA}/{family}/{year}/zip/rezultati.zip", suffix=".zip")
    for d in days():
        for contest in contests(d):
            url = declared_url(d, contest)
            try:
                raw.fetch(SOURCE, url, suffix="." + url.rsplit(".", 1)[1])
            except httpx.HTTPStatusError as failure:
                print(f"{url}: {failure.response.status_code}", flush=True)
        if d.family == "euparlament":
            raw.fetch(SOURCE, elected_url(d), suffix=".pdf")


@dataclass
class Option:
    number: int
    label: str
    parties: list[str]
    leader: str | None
    votes: int
    seats: int | None = None
    candidates: list[tuple[int, str, int]] = field(default_factory=list)
    elected: list[int] = field(default_factory=list)  # ballot numbers of the candidates declared elected


@dataclass
class Declared:
    url: str
    registered: int
    voters: int
    ballots: int
    valid: int
    invalid: int
    options: list[Option]


def _declared_csv(d: Day, url: str, rows: list[list[str]]) -> Declared:
    """The summary files the results pages read: accounting, then one row per option (and its candidates)."""
    options: list[Option] = []
    shift = -1 if d.family == "referendum" else 0
    for raw_row in rows:
        row = [clean(cell) for cell in raw_row]
        while row and not row[-1]:
            row.pop()
        if d.family == "referendum":
            options.append(Option(int(row[18]), row[19], [], None, int(row[20])))
            continue
        modern = d.family == "parlament" and d.year in (2015, 2016)
        at = 36 if modern else len(row) - 2
        candidates: list[tuple[int, str, int]] = []
        seats: int | None = None
        if modern:
            label_, leader, parties = row[35], None, [c for c in row[20:34] if c]
            rest = row[at + 2 :]
            if rest:
                seats = int(rest[0])
                rest = rest[1:]
            while len(rest) >= 3 and rest[0].isdigit():
                candidates.append((int(rest[0]), rest[1], int(rest[2])))
                rest = rest[4:]
        else:
            leader = row[at - 3] or None
            label_ = row[at - 1] or row[at - 3]
            parties = [c for c in row[20 : at - 3] if c]
        options.append(Option(int(row[19]), label_, parties, leader, int(row[at]), seats, candidates))
    head = [clean(cell) for cell in rows[0]]
    return Declared(
        url,
        number(head[11 + shift]),
        number(head[12 + shift]),
        number(head[14 + shift]),
        number(head[15 + shift]),
        number(head[17 + shift]),
        options,
    )


def _declared_json(url: str, data: dict) -> Declared:
    options = [
        Option(
            number=int(entry["rbr"]),
            label=clean(entry["naziv"]),
            parties=[clean(p["naziv"]) for p in entry.get("stranke") or entry.get("predlagatelji") or []],
            leader=None,
            votes=int(entry["glasova"]),
            candidates=[(int(c["rbr"]), clean(c["naziv"]), int(c["glasova"])) for c in entry.get("lista") or []],
        )
        for entry in data["lista"]
    ]
    return Declared(
        url,
        int(data["biraciUkupno"]),
        int(data["biraciGlasovalo"]),
        int(data["listiciUkupno"]),
        int(data["listiciVazeci"]),
        int(data["listiciNevazeci"]),
        options,
    )


def _declared_ep_csv(url: str, rows: list[list[str]]) -> Declared:
    """The 2013 and 2014 national files: one row per list, repeating the accounting, then its candidates."""
    options: list[Option] = []
    for raw_row in rows:
        row = [clean(cell) for cell in raw_row]
        rest = row[30:]
        candidates: list[tuple[int, str, int]] = []
        for at in range(2, len(rest) - 2, 4):
            candidates.append((int(rest[at]), rest[at + 1], int(rest[at + 2])))
        options.append(
            Option(int(row[18]), row[30], [c for c in row[19:28] if c], None, int(row[29]), None, candidates)
        )
    head = [clean(cell) for cell in rows[0]]
    return Declared(
        url, number(head[10]), number(head[11]), number(head[13]), number(head[14]), number(head[16]), options
    )


@functools.cache
def declared(d: Day, contest: str) -> Declared | None:
    url = declared_url(d, contest)
    try:
        artifact = raw.cached(SOURCE, url)
    except LookupError:
        return None
    data = artifact.path.read_bytes()
    if url.endswith(".json"):
        return _declared_json(url, json.loads(decode(data)))
    rows = [row for row in csv.reader(io.StringIO(decode(data)), delimiter=";") if any(cell.strip() for cell in row)]
    if d.family == "euparlament":
        return _declared_ep_csv(url, rows)
    return _declared_csv(d, url, rows)


# The archive already holds each European Parliament election in Croatia as a national result set, from the pipelines
# that cover every member state. The polling-station sets below supersede those, so they reuse the ids those
# pipelines derive from their own keys.


def _external(scheme: str, value: str) -> str:
    return str(uuid5(package.NAMESPACE, f"external_id:{scheme}\x1f{value}"))


def _minted_source(publisher: str, title: str) -> str:
    """The id `Changeset.source` mints for a source from its publisher and title."""
    return str(uuid5(package.NAMESPACE, f"source\x1f{publisher}\x1f{title}"))


@dataclass(frozen=True)
class National:
    election: str
    contest: str
    result_set: str
    source: str


def national(year: int) -> National:
    contest = f"HR:{year}:eu.member_state:HR"
    if year == 2024:
        title = "Izbori članova u Europski parlament iz Republike Hrvatske 2024: rezultati"
        return National(
            _external("eu.election", f"HR:{year}:european-parliament"),
            _external("eu.contest", contest),
            _external("eu.ep2024.result_set", f"HR:{year}"),
            _minted_source("Državno izborno povjerenstvo Republike Hrvatske", title),
        )
    return National(
        _external("eu.election", f"HR:{year}:european-parliament"),
        _external("eu.contest", contest),
        _external("eu.ep_cadmus.result_set", contest),
        _minted_source("European University Institute", "EP election results at constituency level, 1979–2019"),
    )


def elected_url(d: Day) -> str:
    return f"{DATA}/euparlament/{d.year}/izabrani/i_14_000_0000.pdf"


def _list_names(lines: list[str]) -> set[str]:
    return {
        label(re.sub(r"^kandidacijska lista\s+", "", line.strip(), flags=re.IGNORECASE))
        for line in lines
        if line.strip()
    }


def elected(d: Day, target: Declared) -> None:
    """Fill each list's seats and elected candidates from the commission's decision on the distribution of seats."""
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, elected_url(d)).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    start = re.search(r"^\s*IV\. ", text, re.MULTILINE)
    if start is None:
        raise ValueError(f"{d.year}: no distribution of seats")
    blocks: list[tuple[list[str], int, list[int]]] = []
    names: list[str] = []
    seats: int | None = None
    members: list[int] = []
    for line in text[start.end() :].splitlines():
        if re.match(r"^\s*V\. |^\s*PREDSJEDNIK", line, re.IGNORECASE):
            break
        found = re.search(r"dobila je (\d+) mjest", line)
        member = re.search(r"pod rednim brojem (\d+)\.\)", line)
        header = re.match(r"^\s*\d+\.\s+(\S.*)$", line)
        if found:
            seats = int(found.group(1))
        elif member:
            members.append(int(member.group(1)))
        elif header and "(kandidat" not in line:
            if seats is not None:
                blocks.append((names, seats, members))
            names, seats, members = [header.group(1)], None, []
        elif seats is None and line.strip() and not re.match(r"^\s*(Nositelj|parlament)", line):
            names.append(line)
    if seats is not None:
        blocks.append((names, seats, members))
    for o in target.options:
        o.seats = 0
    used: set[int] = set()
    for names, seats, members in blocks:
        wanted = _list_names(names)
        best = max(
            (o for o in target.options if o.number not in used),
            key=lambda o: (
                len(wanted & _list_names([o.label, *o.parties])) / len(wanted | _list_names([o.label, *o.parties]))
            ),
        )
        have = _list_names([best.label, *best.parties])
        if len(wanted & have) / len(wanted | have) < 0.5 and not (
            "grupe biraca" in " ".join(wanted) and "grupe biraca" in " ".join(have)
        ):
            raise ValueError(f"{d.year}: no list for {names}")
        used.add(best.number)
        best.seats = seats
        best.elected = members
        if len(members) != seats:
            raise ValueError(f"{d.year}: {names[0]} has {seats} seats but {len(members)} elected members")


# Polling stations and their counts.


@dataclass
class Station:
    contest: str
    part: str
    district: str
    county_no: str
    county: str
    muni: str
    muni_kind: str
    bm: str
    name: str
    counts: dict[int, int]
    values: dict[tuple[int, int], int]  # (option number, candidate number or 0) -> votes


TITLES = {
    "dr",
    "sc",
    "mr",
    "prof",
    "doc",
    "dipl",
    "ing",
    "iur",
    "oec",
    "univ",
    "bacc",
    "mag",
    "med",
    "akademik",
    "el",
    "nositelj",
    "liste",
    "kandidat",
    "zamjenik",
    "stom",
    "pharm",
    "spec",
    "struc",
}


def words(text: str) -> set[str]:
    return {w for w in label(text).split() if len(w) > 1 and w not in TITLES and not w.isdigit()}


def similar(a: str, b: str) -> float:
    left, right = words(a), words(b)
    return 2 * len(left & right) / (len(left) + len(right)) if left and right else 0.0


def _stranke(d: Day, contest: str) -> dict[str, str]:
    """The 2000 files number their columns; a companion file names each number."""
    if not (d.family == "parlament" and d.year == 2000):
        return {}
    archive = zipfile.ZipFile(raw.cached(SOURCE, zip_url(d)).path)
    member = next(n for n in archive.namelist() if n.endswith(f"ij{int(contest):02d}_Stranke.csv"))
    names: dict[str, list[str]] = {}
    current = ""
    for row in list(csv.reader(io.StringIO(decode(archive.read(member))), delimiter=";"))[1:]:
        if len(row) < 2:
            continue
        if row[0].strip():
            current = row[0].strip().rstrip(".")
        names.setdefault(current, []).append(clean(row[1]))
    return {number: ", ".join(parts) for number, parts in names.items()}


def _flat(
    d: Day, contest: str, laid: list[tuple[Sheet, dict[str, int], list[int]]], options: list[Option]
) -> dict[tuple[int, int], tuple[int, int]]:
    """Align the columns of every file of a contest, then match each to an option by name and total."""
    named = _stranke(d, contest)

    def text(sheet: Sheet, column: int) -> str:
        return label(named.get(clean(sheet.header[column]), sheet.header[column]))

    ref = max(range(len(laid)), key=lambda i: len(laid[i][0].rows))
    groups = [text(laid[ref][0], column) for column in laid[ref][2]]
    members: dict[int, list[tuple[int, int]]] = {g: [(ref, column)] for g, column in enumerate(laid[ref][2])}
    for index, (sheet, _, columns) in enumerate(laid):
        if index == ref:
            continue
        texts = [text(sheet, column) for column in columns]
        if len(columns) == len(groups) and all(
            a == b or similar(a, b) >= 0.5 for a, b in zip(texts, groups, strict=True)
        ):
            for g, column in enumerate(columns):
                members[g].append((index, column))
            continue
        free = set(range(len(groups)))
        for column, here in zip(columns, texts, strict=True):
            g = max(sorted(free), key=lambda g: (here == groups[g], similar(here, groups[g])))
            if here != groups[g] and similar(here, groups[g]) < 0.3:
                raise ValueError(f"{sheet.member}: column {here!r} matches no column of {laid[ref][0].member}")
            members[g].append((index, column))
            free.remove(g)
    sums: dict[int, int] = {}
    for g, cells in members.items():
        total = 0
        for index, column in cells:
            sheet, found, _ = laid[index]
            total += sum(number(row[column]) for row in sheet.rows if _keep(row, found) and column < len(row))
        sums[g] = total

    def score(g: int, o: Option) -> float:
        group = groups[g]
        texts = [o.label, o.leader or "", " ".join(o.parties), f"{' '.join(o.parties)} {o.leader or ''}"]
        best = max(similar(group, text) for text in texts)
        person = words(o.leader or (o.label if not o.parties else ""))
        return best + (1.0 if person and person <= words(group) else 0.0) + (3.0 if sums[g] == o.votes else 0.0)

    pairs = sorted(((score(g, o), g, o.number) for g in members for o in options), key=lambda x: (-x[0], x[1], x[2]))
    chosen: dict[int, int] = {}
    taken: set[int] = set()
    for value, g, option in pairs:
        if g in chosen or option in taken or value < 0.3:
            continue
        chosen[g] = option
        taken.add(option)
    missing = [(groups[g], sums[g]) for g in members if g not in chosen]
    if any(total for _, total in missing):
        raise ValueError(f"{d.day} {contest}: columns match no option: {missing[:3]}")
    if missing:
        print(f"{d.day} {contest}: empty columns of no printed option: {missing}", flush=True)
    return {cell: (chosen[g], 0) for g, cells in members.items() for cell in cells if g in chosen}


def _blocks(
    sheet: Sheet, index: int, columns: list[int], options: list[Option]
) -> dict[tuple[int, int], tuple[int, int]]:
    """Since 2015 each list's column is followed by one column of preference votes per candidate."""
    out: dict[tuple[int, int], tuple[int, int]] = {}
    at = 0
    used: set[int] = set()
    while at < len(columns):
        best: tuple[float, Option] | None = None
        for o in options:
            if o.number in used:
                continue
            following = [sheet.header[c] for c in columns[at + 1 : at + 1 + len(o.candidates)]]
            if len(following) < len(o.candidates):
                continue
            score = (
                sum(similar(a, b[1]) >= 0.8 for a, b in zip(following, o.candidates, strict=True)) / len(o.candidates)
                if o.candidates
                else 0.0
            )
            score += 0.01 * similar(sheet.header[columns[at]], ", ".join(o.parties) or o.label)
            if best is None or score > best[0]:
                best = (score, o)
        if best is None or best[0] < 0.5:
            raise ValueError(f"{sheet.member}: no list fits the column {sheet.header[columns[at]]!r}")
        option = best[1]
        used.add(option.number)
        out[(index, columns[at])] = (option.number, 0)
        for offset, candidate in enumerate(option.candidates, start=1):
            out[(index, columns[at + offset])] = (option.number, candidate[0])
        at += 1 + len(option.candidates)
    return out


def stations(d: Day) -> tuple[list[Station], dict[str, Declared | None], dict[str, list[str]]]:
    """Every polling station of the day, with its counts keyed by option, and the authority's totals."""
    found = [s for s in sheets(d.family, d.year) if d.family != "predsjednik" or s.contest == str(d.round)]
    out: list[Station] = []
    printed = {contest: declared(d, contest) for contest in contests(d)}
    problems: dict[str, list[str]] = {}
    for contest in contests(d):
        here = [s for s in found if d.family == "predsjednik" or s.contest == contest]
        if not here:
            continue
        laid: list[tuple[Sheet, dict[str, int], list[int]]] = []
        for sheet in here:
            found_roles, columns = roles(sheet.header)
            laid.append((sheet, found_roles, columns))
        target = printed[contest]
        if target is None:
            raise ValueError(f"{d.day} {contest}: no totals from the authority")
        if any(o.candidates for o in target.options) and not contest.startswith("12-"):
            mapping: dict[tuple[int, int], tuple[int, int]] = {}
            for index, (sheet, _, columns) in enumerate(laid):
                mapping |= _blocks(sheet, index, columns, target.options)
        else:
            mapping = _flat(d, contest, laid, target.options)
        for index, (sheet, found_roles, columns) in enumerate(laid):
            for row in sheet.rows:
                if not _keep(row, found_roles):
                    continue
                station = _station(contest, sheet, found_roles, row)
                for column in columns:
                    if (index, column) in mapping:
                        station.values[mapping[(index, column)]] = number(row[column]) if column < len(row) else 0
                if d.family == "euparlament" and station.part == "abroad" and not station.counts.get(REGISTERED):
                    # Voters abroad have no roll of their own in these files; the roll is national only.
                    station.counts.pop(REGISTERED, None)
                out.append(station)
        mine = [s for s in out if s.contest == contest]
        if contest.startswith("12-") and 2 * sum(s.counts.get(REGISTERED, 0) for s in mine) < target.registered:
            # Since 2020 the minority files leave each station's minority roll at zero; the roll is national only.
            for station in mine:
                station.counts.pop(REGISTERED, None)
        problems[contest] = compare(target, mine)
    return out, printed, problems


def _cell(row: list[str], found: dict[str, int], role: str) -> str:
    at = found.get(role)
    return clean(row[at]) if at is not None and at < len(row) else ""


def _keep(row: list[str], found: dict[str, int]) -> bool:
    if len(row) <= found["invalid"] or not any(
        clean(row[found[role]]).replace(".", "").isdigit() for role in ("registered", "valid")
    ):
        return False
    if "bm" in found:
        return _cell(row, found, "bm").isdigit()
    place = fold(_cell(row, found, "muni"))
    return bool(place) and not place.startswith(("ukupno", "sveukupno", "svega"))


def _station(contest: str, sheet: Sheet, found: dict[str, int], row: list[str]) -> Station:
    counts = {REGISTERED: number(row[found["registered"]])}
    for measure, role in ((VOTERS, "voters"), (CAST, "ballots"), (VALID, "valid"), (INVALID, "invalid")):
        if role in found:
            counts[measure] = number(row[found[role]])
    part = sheet.part
    kind = fold(_cell(row, found, "kind"))
    county = _cell(row, found, "county")
    consulate = fold(_cell(row, found, "muni")).startswith(("veleposl", "g.konzulat", "generalni konzulat", "konzulat"))
    if kind == "drzava" or fold(county) in ("inozemstvo", "drzave") or sheet.header[found.get("muni", 0)] == "Država":
        part = "abroad" if part != "special" else part
    if consulate:
        part = "abroad"
    return Station(
        contest=contest,
        part=part,
        district=_cell(row, found, "district"),
        county_no=_cell(row, found, "county_no"),
        county=county,
        muni=_cell(row, found, "muni"),
        muni_kind=kind,
        bm=_cell(row, found, "bm").lstrip("0") or "0",
        name=_cell(row, found, "bm_name"),
        counts=counts,
        values={},
    )


def compare(target: Declared, here: list[Station]) -> list[str]:
    """Differences between the stations' sums and the authority's totals for one contest."""
    out: list[str] = []
    for measure, value in ((REGISTERED, target.registered), (VALID, target.valid), (INVALID, target.invalid)):
        summed = sum(s.counts.get(measure, 0) for s in here)
        if summed != value and any(measure in s.counts for s in here):
            out.append(f"measure {measure}: {summed:,} summed, {value:,} printed")
    for o in target.options:
        summed = sum(s.values.get((o.number, 0), 0) for s in here)
        if summed != o.votes:
            out.append(f"option {o.number} {o.label}: {summed:,} summed, {o.votes:,} printed")
        for number_, name, votes in o.candidates:
            summed = sum(s.values.get((o.number, number_), 0) for s in here)
            if summed != votes:
                out.append(f"candidate {o.number}/{number_} {name}: {summed:,} summed, {votes:,} printed")
    return out


# Places.


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
    # Zagreb's polling stations are filed under the city's parts or its electoral districts.
    return "zagreb" if folded.startswith("zagreb") or folded == "grad zagreb" else folded


@functools.cache
def municipalities() -> dict[tuple[str, str], tuple[str, str, str]]:
    """The 556 towns and municipalities of the 2024 presidential files: (county, folded name) -> name, kind."""
    sheet = next(s for s in sheets("predsjednik", 2024) if s.contest == "1")
    found, _ = roles(sheet.header)
    out: dict[tuple[str, str], tuple[str, str, str]] = {}
    for row in sheet.rows:
        if not _keep(row, found) or fold(_cell(row, found, "kind")) == "drzava":
            continue
        county = _cell(row, found, "county_no").zfill(2)
        muni = _cell(row, found, "muni")
        out[(county, place(muni))] = (county, muni, _cell(row, found, "kind"))
    return out


@functools.cache
def counties() -> dict[str, str]:
    sheet = next(s for s in sheets("predsjednik", 2024) if s.contest == "1")
    found, _ = roles(sheet.header)
    return {
        _cell(row, found, "county_no").zfill(2): _cell(row, found, "county")
        for row in sheet.rows
        if _keep(row, found) and _cell(row, found, "county_no")
    }


@functools.cache
def _by_name() -> dict[str, str | None]:
    """A folded municipality name -> its county, when the name is unique in Croatia."""
    seen: dict[str, set[str]] = {}
    for county, name in municipalities():
        seen.setdefault(name, set()).add(county)
    return {name: next(iter(found)) if len(found) == 1 else None for name, found in seen.items()}


@functools.cache
def _county_numbers() -> dict[str, str]:
    return {label(name).replace(" zupanija", ""): number_ for number_, name in counties().items()}


def locate(station: Station) -> tuple[str, str | None]:
    """The area a polling station lies within: its municipality, else its county, else its district."""
    if station.part == "abroad":
        return "unlocated", None
    county = station.county_no.zfill(2) if station.county_no else ""
    if not county and station.county:
        county = _county_numbers().get(label(station.county).replace(" zupanija", ""), "")
    name = place(station.muni)
    if county and (county, name) in municipalities():
        return "within", area("hr.opcina", f"{county}:{name}")
    if not county and _by_name().get(name):
        return "within", area("hr.opcina", f"{_by_name()[name]}:{name}")
    if county and (county, re.sub(r"^sveti ", "sveta ", name)) in municipalities():
        return "within", area("hr.opcina", f"{county}:{re.sub(r'^sveti ', 'sveta ', name)}")
    if county in counties():
        return "within", area("hr.zupanija", county)
    if station.contest.isdigit() and int(station.contest) <= 10:
        return "within", area("hr.izborna_jedinica", station.contest)
    return "within", COUNTRY


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Croatia's 21 counties, 556 towns and municipalities, and 11 territorial parliamentary electoral districts, "
        "as the State Electoral Commission names them in its results files",
    )
    p.external |= {CROATIA, COUNTRY}
    _source(p)
    p.uses(raw.cached(SOURCE, f"{DATA}/predsjednik/2024/zip/rezultati.zip"))
    for code, rank, kind, noun in (
        ("hr.zupanija", 1, "administrative", "county"),
        ("hr.opcina", 2, "administrative", "municipality"),
        ("hr.izborna_jedinica", 1, "electoral_district", "electoral district"),
        ("hr.bm", 3, "precinct", "polling station"),
    ):
        kinds = {"grad": "town", "opcina": "municipality"} if code == "hr.opcina" else None
        p.add("scheme", code=code, polity_id=CROATIA, kind=kind, rank=rank, noun=noun, kinds=kinds, code_system=None)
    for code, name in sorted(counties().items()):
        id = area("hr.zupanija", code)
        p.add("area", id=id, scheme_code="hr.zupanija")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, title(name), "hr", "official")
    for (county, folded), (_, name, kind) in sorted(municipalities().items()):
        id = area("hr.opcina", f"{county}:{folded}")
        p.add("area", id=id, scheme_code="hr.opcina")
        p.add("area_code", area_id=id, code=f"{county}:{folded}")
        p.add("area_parent", area_id=id, parent_id=area("hr.zupanija", county))
        p.add("area_kind", area_id=id, kind={"grad": "grad", "općina": "opcina"}[kind], source_id=SOURCE_ID)
        p.name(id, title(name), "hr", "official")
    for n in range(1, 12):
        id = area("hr.izborna_jedinica", str(n))
        p.add("area", id=id, scheme_code="hr.izborna_jedinica")
        p.add("area_code", area_id=id, code=str(n))
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, f"{ROMAN[n]}. izborna jedinica", "hr", "official")
        p.name(id, f"Electoral district {ROMAN[n]}" + (" (voters abroad)" if n == 11 else ""), "en", "official", False)
    return p


def _source(p: Package) -> None:
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Državno izborno povjerenstvo Republike Hrvatske",
        title="Arhiva izbora: rezultati po biračkim mjestima i ukupni rezultati",
        url=f"{SITE}/",
        license="Official results of the Republic of Croatia, published for public use; reuse with the source "
        "acknowledged",
        license_url=f"{SITE}/",
        attribution="Državno izborno povjerenstvo Republike Hrvatske (DIP), izbori.hr",
    )


# Bodies, offices, systems, and parties.


HOUSE_2000 = key("body", "zastupnicki_dom")
SABOR = key("body", "sabor")
PRESIDENCY = key("office", "president")
EP_BODY = key("body", "european-parliament")
INDEPENDENT = ("neovisna lista", "neovisni kandidat", "kandidat grupe biraca", "grupa biraca")


def system(name: str) -> str:
    return key("electoral_system", name)


SYSTEMS: list[tuple[str, date, date | None, dict, str]] = [
    (
        system("closed_list"),
        date(1999, 1, 1),
        date(2015, 1, 1),
        {"formula": "dhondt", "threshold": {"level": "list", "scope": "contest", "percent": 5}},
        (
            "Each territorial electoral district elects 14 members from closed lists by D'Hondt, among the lists with at "
            "least 5% of the district's valid votes. The district of voters abroad elects a varying number of members "
            "(6 in 2000, 4 in 2003, 5 in 2007, 3 from 2011) the same way."
        ),
    ),
    (
        system("preference_list"),
        date(2015, 1, 1),
        None,
        {
            "formula": "dhondt",
            "threshold": {"level": "list", "scope": "contest", "percent": 5},
            "lists": {"order": "flexible", "vacancy": "next_on_list"},
        },
        (
            "Each territorial electoral district elects 14 members by D'Hondt among the lists with at least 5% of the "
            "district's valid votes, and the district of voters abroad elects 3. A voter may mark one preference on the "
            "list; a candidate with preference votes of at least 10% of the list's votes moves to the top of its order."
        ),
    ),
    (
        system("minority_single"),
        date(1999, 1, 1),
        None,
        {"formula": "fptp"},
        (
            "Members of the national minorities elect their representatives in the nationwide twelfth district; the "
            "candidate with the most votes in each minority group is elected."
        ),
    ),
    (
        system("minority_block"),
        date(2003, 1, 1),
        None,
        {"formula": "block"},
        (
            "Members of the Serb minority elect three representatives in the nationwide twelfth district; each voter may "
            "vote for up to three candidates, and the three with the most votes are elected."
        ),
    ),
    (
        system("european_parliament"),
        date(2013, 1, 1),
        None,
        {
            "formula": "dhondt",
            "threshold": {"level": "list", "scope": "contest", "percent": 5},
            "lists": {"order": "flexible", "vacancy": "next_on_list"},
        },
        (
            "Croatia elects its members of the European Parliament in one nationwide constituency, from lists of "
            "candidates, by D'Hondt among the lists with at least 5% of the valid votes (12 seats in 2013 and 2024, 11 "
            "in 2014 and 2019). A voter may mark one preference on the list."
        ),
    ),
    (
        system("president"),
        date(1992, 1, 1),
        None,
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "A candidate who wins more than half of the votes cast is elected; otherwise the two leading candidates go to "
            "a second round fourteen days later."
        ),
    ),
    (
        system("referendum"),
        date(1991, 1, 1),
        None,
        {"formula": "referendum"},
        (
            "A national referendum carries when a majority of the voters who took part vote for it; since the 2010 "
            "constitutional amendments no turnout quorum applies."
        ),
    ),
]


def party_kind(name: str) -> str | None:
    folded = label(name)
    if folded.startswith(INDEPENDENT) or "kandidat grupe biraca" in folded or folded.startswith("nk "):
        return None
    return "party"


def party(name: str) -> str:
    return key("party", label(name))


def bloc(names: list[str]) -> str:
    return key("party", "bloc", *sorted(label(n) for n in names))


def abbreviation(name: str) -> str | None:
    found = re.search(r" - ([^-]{1,30})$", clean(name))
    return found.group(1).strip() if found else None


def option_party(o: Option) -> str | None:
    parties = [n for n in o.parties if party_kind(n)]
    if len(parties) == 1 and len(o.parties) == 1:
        return party(parties[0])
    if len(parties) > 1:
        return bloc(parties)
    return None


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Croatian Parliament, the presidency, Croatia's members of the European Parliament, their electoral systems, and the parties and coalitions on the "
        "State Electoral Commission's results since 1991",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {CROATIA}
    p.add("body", id=HOUSE_2000, polity_id=CROATIA, kind="lower_house")
    p.name(HOUSE_2000, "Chamber of Representatives of the Croatian Parliament", "en", "official")
    p.name(HOUSE_2000, "Zastupnički dom Hrvatskoga sabora", "hr", "native", False)
    p.add("body", id=SABOR, polity_id=CROATIA, kind="unicameral")
    p.name(SABOR, "Croatian Parliament", "en", "official")
    p.name(SABOR, "Hrvatski sabor", "hr", "native", False)
    p.add("body", id=EP_BODY, polity_id=CROATIA, kind="council")
    p.name(EP_BODY, "Croatian members of the European Parliament", "en", "official")
    p.name(EP_BODY, "Zastupnici iz Republike Hrvatske u Europskom parlamentu", "hr", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=CROATIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Republic of Croatia", "en", "official")
    p.name(PRESIDENCY, "Predsjednik Republike Hrvatske", "hr", "native", False)
    for id, since, until, rules, method in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=CROATIA, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    spellings: dict[str, Counter[str]] = {}
    blocs: dict[str, tuple[list[str], Counter[str]]] = {}
    for d in days():
        if d.family == "referendum":
            continue
        for contest in contests(d):
            found = declared(d, contest)
            if found is None:
                continue
            for o in found.options:
                named = [n for n in o.parties if party_kind(n)]
                for n in named:
                    spellings.setdefault(party(n), Counter())[n] += 1
                if len(named) > 1:
                    blocs.setdefault(bloc(named), (named, Counter()))[1][o.label] += 1
    for id, names in sorted(spellings.items()):
        name = names.most_common(1)[0][0]
        p.add("party", id=id, kind="party", polity_id=CROATIA)
        p.add("external_id", entity_id=id, scheme="hr.izbori.stranka", value=label(name))
        p.name(id, name, "hr", "official")
        short = abbreviation(name)
        if short:
            p.name(id, short, "hr", "abbreviation", False)
    for id, (members, labels) in sorted(blocs.items()):
        p.add("party", id=id, kind="bloc", polity_id=CROATIA)
        p.name(id, labels.most_common(1)[0][0], "hr", "official")
        p.name(id, ", ".join(sorted({clean(m) for m in members})), "hr", "ballot", False)
    from ea.sources.hr import skenovi, zupanijski

    known = set(spellings) | set(blocs)
    skenovi.registry(p, known)
    zupanijski.registry(p, known)
    return p


# One package per election day. The 1991-1997 elections, published only as scans, are built in skenovi and zupanijski.

XI_SEATS = {2000: 6, 2003: 4, 2007: 5}
MINORITY_2000 = {5: "Austrian, German, Jewish, Rusyn, and Ukrainian"}
QUESTIONS = {
    2012: (
        "mandatory_referendum",
        "Accession of the Republic of Croatia to the European Union",
        "Jeste li za članstvo Republike Hrvatske u Europskoj uniji?",
        "eu-accession",
    ),
    2013: (
        "initiative",
        "Definition of marriage in the Constitution as a union of a woman and a man",
        (
            "Jeste li za to da se u Ustav Republike Hrvatske unese odredba po kojoj je brak životna zajednica žene "
            "i muškarca?"
        ),
        "marriage",
    ),
}
METHOD = (
    "Counted by the State Electoral Commission (Državno izborno povjerenstvo) per polling station (biračko mjesto), "
    "as published in its results archive. Registered voters are the polling station's roll, voters are those who "
    "signed the roll, ballots cast are the ballots found in the box, and valid and invalid ballots are as printed. "
    "Special polling stations (in hospitals, care homes, prisons, and on ships) and polling stations abroad are "
    "separate units; units abroad are not placed in any Croatian area. A polling station is placed in its town or "
    "municipality when the file names one that matches the current list, otherwise in its county or electoral "
    "district."
)


def package_key(d: Day) -> str:
    kind = {"parlament": "sabor", "predsjednik": "predsjednik", "referendum": "referendum", "euparlament": "ep"}[
        d.family
    ]
    return f"{SOURCE}/{kind}-{d.day}"


def packages() -> list[str]:
    from ea.sources.hr import skenovi, zupanijski

    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *skenovi.packages(),
        *zupanijski.packages(),
        *(package_key(d) for d in days()),
    ]


def parse() -> None:
    geo().write()
    registry().write()
    from ea.sources.hr import skenovi, zupanijski

    skenovi.parse()
    zupanijski.parse()
    for d in days():
        election(d).write()


def emit() -> None:
    package.run("load", packages())


def _election_names(d: Day) -> tuple[str, str, str]:
    if d.family == "euparlament":
        return (
            f"{d.year} European Parliament election in Croatia",
            f"Izbori za članove u Europski parlament iz Republike Hrvatske {d.year}.",
            f"hr/{d.day}-european-parliament",
        )
    if d.family == "parlament":
        return (
            f"{d.year} Croatian parliamentary election",
            f"Izbori za zastupnike u Hrvatski sabor {d.year}.",
            f"hr/{d.day}-parliamentary",
        )
    if d.family == "predsjednik":
        suffix = "" if d.round == 1 else ", second round"
        return (
            f"{d.year}–{str(d.year + 1)[2:]} Croatian presidential election{suffix}"
            if d.year in (2009, 2014, 2019, 2024)
            else f"{d.year} Croatian presidential election{suffix}",
            f"Izbori za predsjednika Republike Hrvatske {d.year}., {d.round}. krug",
            f"hr/{d.day}-presidential" + ("" if d.round == 1 else "-runoff"),
        )
    return (
        f"{d.year} Croatian referendum",
        f"Državni referendum {d.year}.",
        f"hr/{d.day}-referendum",
    )


def _contest_frame(d: Day, contest: str) -> tuple[str, str, str, int, str, str | None, str]:
    """kind, english name, croatian name, magnitude, system, body, slug."""
    body = HOUSE_2000 if d.year == 2000 else SABOR
    if d.family == "euparlament":
        return (
            "list_allocation",
            f"{d.year} European Parliament election in Croatia",
            f"Izbori za Europski parlament {d.year}.",
            EP_SEATS[d.year],
            system("european_parliament"),
            EP_BODY,
            "croatia",
        )
    if d.family == "predsjednik":
        name = f"President of the Republic of Croatia, {'first' if d.round == 1 else 'second'} round"
        return (
            "office",
            name,
            f"Predsjednik Republike Hrvatske, {d.round}. krug",
            1,
            system("president"),
            None,
            "president",
        )
    if d.family == "referendum":
        _, text, native, slug = QUESTIONS[d.year]
        return "ballot_question", text, native, 1, system("referendum"), None, slug
    if contest.startswith("12-"):
        group = int(contest[3:])
        minority = MINORITY_2000.get(group, MINORITIES[group]) if d.year == 2000 else MINORITIES[group]
        seats = 3 if group == 1 and d.year > 2000 else 1
        rules = system("minority_block") if seats > 1 else system("minority_single")
        return (
            "body_seats",
            f"Electoral district XII: {minority} minorit{'y' if ',' not in minority else 'ies'}",
            f"XII. izborna jedinica: {minority}",
            seats,
            rules,
            body,
            f"district-xii-{SHORT[group]}",
        )
    n = int(contest)
    rules = system("preference_list") if d.year >= 2015 else system("closed_list")
    seats = XI_SEATS.get(d.year, 3) if n == 11 else 14
    english = f"Electoral district {ROMAN[n]}" + (" (voters abroad)" if n == 11 else "")
    return (
        "list_allocation",
        english,
        f"{ROMAN[n]}. izborna jedinica",
        seats,
        rules,
        body,
        f"district-{ROMAN[n].lower()}",
    )


def election(d: Day) -> Package:
    english, native, path = _election_names(d)
    depends = [f"{SOURCE}/geo", f"{SOURCE}/registry"]
    previous: Day | None = None
    if d.round == 2:
        previous = next(x for x in days() if x.family == d.family and x.year == d.year and x.round == 1)
        depends.append(package_key(previous))
    p = Package(
        package_key(d), SOURCE, f"The {english} by polling station from the State Electoral Commission", depends
    )
    p.external |= {CROATIA, COUNTRY}
    if d.round == 1:
        p.artifact(raw.cached(SOURCE, zip_url(d)), SOURCE_ID)
    else:
        p.uses(raw.cached(SOURCE, zip_url(d)))
    for contest in contests(d):
        try:
            p.artifact(raw.cached(SOURCE, declared_url(d, contest)), SOURCE_ID)
        except LookupError:
            pass
    eu = national(d.year) if d.family == "euparlament" else None
    election_id = key("election", d.day)
    kind = {
        "parlament": "general",
        "euparlament": "general",
        "predsjednik": "general" if d.round == 1 else "runoff",
    }.get(d.family, "referendum")
    if eu is not None:
        p.external |= {eu.election, eu.contest, eu.source}
        p.artifact(raw.cached(SOURCE, elected_url(d)), SOURCE_ID)
        election_id = eu.election
    else:
        p.add("election", id=election_id, polity_id=CROATIA, kind=kind, date_from=d.day, date_to=d.day, status="held")
        p.name(election_id, english, "en", "official")
        p.name(election_id, native, "hr", "native", False)
        p.add("slug", path=path, entity_id=election_id, is_current=True)
    found, printed, problems = stations(d)
    values = Facts()
    seen: Counter[str] = Counter()
    caveats: Counter[str] = Counter()
    for contest in contests(d):
        target = printed[contest]
        if target is None:
            continue
        here = [s for s in found if s.contest == contest]
        ckind, cname, cnative, seats, rules, body, slug = _contest_frame(d, contest)
        contest_id = key("contest", d.day, contest)
        district = area("hr.izborna_jedinica", contest) if contest.isdigit() else COUNTRY
        previous_round = key("contest", previous.day, contest) if previous is not None else None
        if eu is not None:
            contest_id = eu.contest
            elected(d, target)
        else:
            p.add(
                "contest",
                id=contest_id,
                election_id=election_id,
                kind=ckind,
                office_id=PRESIDENCY if d.family == "predsjednik" else None,
                body_id=body,
                district_area_id=district,
                magnitude=seats,
                round_number=d.round,
                previous_round_id=previous_round,
                electoral_system_id=rules,
                default_result_kind="counted",
            )
            p.name(contest_id, cname, "en", "official")
            p.name(contest_id, cnative, "hr", "native", False)
            p.add("slug", path=f"{path}/{slug}", entity_id=contest_id, is_current=True)
        if d.family == "referendum":
            p.add("ballot_question", contest_id=contest_id, type=QUESTIONS[d.year][0])
        result_set = key("result_set", d.day, contest)
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
            completeness=1.0,
            supersedes_id=eu.result_set if eu is not None else None,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        if eu is not None:
            _supersede(p, d, eu, result_set)
        options = _options(p, d, contest, contest_id, result_set, target, previous)
        _declared_rows(p, result_set, target, options, {m for s in here for m in s.counts} if here else None)
        if not here:
            _aggregate(p, d, contest, result_set, district, target, options, values)
            caveats["aggregate"] += 1
            continue
        for station in here:
            if not any(station.counts.values()) and not any(station.values.values()):
                continue
            # A few special polling stations share a number and a name; the files list them apart, so they stay apart.
            code = _unit_code(d, station)
            seen[code] += 1
            if seen[code] > 1:
                code = f"{code}#{seen[code]}"
            unit = _unit(p, d, station, code)
            for measure, value in station.counts.items():
                values.add(result_set, unit, None, measure, value)
            for (option, candidate), value in station.values.items():
                measure = PREFERENCE if candidate else VOTES
                values.add(result_set, unit, options[(option, candidate)], measure, value)
        _notes(p, d, contest, result_set, problems.get(contest, []), here, caveats)
    p.frame("result_value", values.frame())
    _accept(p, d, caveats)
    return p


def _supersede(p: Package, d: Day, eu: National, result_set: str) -> None:
    """Hand the contest's default to the polling-station set and keep the national totals as a superseded set.

    The national set is re-staged with its own election, contest, and source. Its completeness is left open: the
    package holds none of its facts, and a set with facts missing from the package fails the empty-set check.
    """
    p.add(
        "result_set",
        id=eu.result_set,
        election_id=eu.election,
        contest_id=eu.contest,
        stage="official",
        kind="counted",
        source_id=eu.source,
        status="superseded",
        is_default=False,
        completeness=None,
    )
    p.add(
        "note",
        id=key("note", result_set, "supersedes"),
        entity_id=result_set,
        kind="methodology",
        body_md="This set holds the same contest by polling station, with each candidate's preference votes and the "
        "seats and elected members from the commission's decision on the distribution of seats. It supersedes "
        "the set of national totals.",
        lang="en",
    )
    if d.year == 2019:
        p.add(
            "note",
            id=key("note", result_set, "twelfth"),
            entity_id=result_set,
            kind="caveat",
            body_md="The commission's decision lists twelve elected members: eleven for the seats contested on election "
            "day, and a twelfth that point V of the decision gives to Romana Jerković of the Social Democratic list, "
            "under the presidential decision calling the election.",
            lang="en",
        )


class Facts:
    """Result values gathered column by column, since a parliamentary election holds about three million."""

    def __init__(self) -> None:
        names = ("result_set_id", "unit_id", "option_id", "measure", "value", "flags")
        self.columns: dict[str, list[object]] = {name: [] for name in names}

    def add(self, result_set: str, unit: str, option: str | None, measure: int, value: int, flags: int = 0) -> None:
        for column, item in zip(self.columns.values(), (result_set, unit, option, measure, value, flags), strict=True):
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
                "flags": pl.Int16,
            },
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16))


def _unit_code(d: Day, s: Station) -> str:
    county = s.county_no.zfill(2) if s.county_no else s.county
    return ":".join([str(d.day), s.contest or "0", s.part, county, s.muni, s.bm, s.name])


def _unit(p: Package, d: Day, s: Station, code: str) -> str:
    id = key("unit", code)
    relation, place_id = locate(s)
    p.add(
        "reporting_unit",
        id=id,
        polity_id=CROATIA,
        scheme_code="hr.bm",
        code=code,
        area_relation=relation,
        area_id=place_id,
        kind="abroad_mission" if s.part == "abroad" else "standard",
    )
    return id


def _person(p: Package, d: Day, tag: str, name: str, create: bool = True) -> str:
    person = key("person", d.family, d.year, tag)
    if create:
        p.add("person", id=person)
        p.name(person, title(name), "hr", "official")
    return person


def _strip_titles(name: str) -> str:
    """Drop academic titles the files print around a name ("dr. sc.", ", dipl. iur.")."""
    text = clean(name.split(",")[0]) if "," in name else clean(name)
    kept = [w for w in text.split() if label(w) not in TITLES and not re.fullmatch(r"[a-zA-Z]{1,4}\.", w)]
    return " ".join(kept) or clean(name)


def _options(
    p: Package, d: Day, contest: str, contest_id: str, result_set: str, target: Declared, previous: Day | None
) -> dict[tuple[int, int], str]:
    out: dict[tuple[int, int], str] = {}
    later = None
    if d.family == "predsjednik" and d.round == 1:
        nxt = next((x for x in days() if x.family == d.family and x.year == d.year and x.round == 2), None)
        later = declared(nxt, "") if nxt is not None else None
        if nxt is not None:
            p.uses(raw.cached(SOURCE, declared_url(nxt, "")))
    winner = max(target.options, key=lambda o: o.votes)
    for o in target.options:
        option = out[(o.number, 0)] = key("option", d.day, contest, o.number)
        if d.family == "referendum":
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="answer",
                ballot_position=o.number,
                ballot_label=o.label,
            )
            p.name(option, {"ZA": "Yes", "PROTIV": "No"}.get(o.label, o.label), "en", "official")
            carried = o is winner and 2 * o.votes > target.valid
            p.add("outcome", result_set_id=result_set, option_id=option, kind="won" if carried else "lost")
            continue
        candidate_contest = d.family == "predsjednik" or contest.startswith("12-")
        if candidate_contest:
            name = _strip_titles(o.leader or o.label) if d.family != "predsjednik" else _strip_titles(o.label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="candidate",
                party_id=option_party(o),
                ballot_position=o.number,
                ballot_label=clean(o.leader or o.label),
            )
            tag = label(name) if d.family == "predsjednik" else f"{d.day}:{contest}:{o.number}"
            person = _person(p, d, tag, name, create=d.round == 1)
            p.add(
                "candidacy",
                id=key("candidacy", d.day, contest, o.number),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=clean(o.leader or o.label),
                member_of_party_id=option_party(o),
                is_independent=option_party(o) is None,
            )
            if d.family == "predsjednik":
                if d.round == 2:
                    kind = "elected" if o is winner else "lost"
                elif later is not None:
                    advanced = {frozenset(words(_strip_titles(x.label))) for x in later.options}
                    kind = "advanced" if frozenset(words(name)) in advanced else "eliminated"
                else:
                    kind = "elected" if 2 * o.votes > target.valid else "eliminated"
                p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
            continue
        p.add(
            "contest_option",
            id=option,
            contest_id=contest_id,
            kind="list",
            party_id=option_party(o),
            ballot_position=o.number,
            ballot_label=", ".join(clean(n) for n in o.parties) or o.label,
        )
        if o.seats is not None:
            p.add(
                "outcome",
                result_set_id=result_set,
                option_id=option,
                kind="won" if o.seats else "lost",
                seats=o.seats,
            )
        if o.leader and not o.candidates:
            tag = f"{d.day}:{contest}:{o.number}:1"
            person = _person(p, d, tag, _strip_titles(o.leader))
            p.add(
                "candidacy",
                id=key("candidacy", tag),
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=1,
                ballot_name=clean(o.leader),
                is_independent=option_party(o) is None,
            )
        for number_, name, _ in o.candidates:
            tag = f"{d.day}:{contest}:{o.number}:{number_}"
            child = out[(o.number, number_)] = key("option", d.day, contest, o.number, number_)
            p.add(
                "contest_option",
                id=child,
                contest_id=contest_id,
                kind="candidate",
                parent_option_id=option,
                party_id=option_party(o),
                ballot_position=number_,
                ballot_label=clean(name),
            )
            person = _person(p, d, tag, _strip_titles(name))
            p.add(
                "candidacy",
                id=key("candidacy", tag),
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=number_,
                ballot_name=clean(name),
                is_independent=option_party(o) is None,
            )
            if number_ in o.elected:
                p.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    candidacy_id=key("candidacy", tag),
                    kind="elected",
                )
    return out


def _declared_rows(
    p: Package, result_set: str, target: Declared, options: dict[tuple[int, int], str], measures: set[int] | None
) -> None:
    """The authority's totals, for the measures its polling-station files also print."""
    for measure, value in (
        (REGISTERED, target.registered),
        (VOTERS, target.voters),
        (CAST, target.ballots),
        (VALID, target.valid),
        (INVALID, target.invalid),
    ):
        if value and (measures is None or measure in measures):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for o in target.options:
        p.add("declared", result_set_id=result_set, option_id=options[(o.number, 0)], measure=VOTES, value=o.votes)
        for number_, _, votes in o.candidates:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=options[(o.number, number_)],
                measure=PREFERENCE,
                value=votes,
            )


def _aggregate(
    p: Package,
    d: Day,
    contest: str,
    result_set: str,
    district: str,
    target: Declared,
    options: dict[tuple[int, int], str],
    values: Facts,
) -> None:
    """A contest the authority publishes only as a total gets one aggregate unit holding that total."""
    code = f"{d.day}:{contest}:total"
    unit = key("unit", code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=CROATIA,
        scheme_code="hr.bm",
        code=code,
        area_relation="within",
        area_id=district,
        kind="aggregate",
    )
    for measure, value in (
        (REGISTERED, target.registered),
        (VOTERS, target.voters),
        (CAST, target.ballots),
        (VALID, target.valid),
        (INVALID, target.invalid),
    ):
        values.add(result_set, unit, None, measure, value, 32)
    for o in target.options:
        values.add(result_set, unit, options[(o.number, 0)], VOTES, o.votes, 32)
    p.add(
        "note",
        id=key("note", result_set, "aggregate"),
        entity_id=result_set,
        kind="caveat",
        body_md="The State Electoral Commission publishes this contest only as a district total, so it is stored as "
        "one aggregate unit rather than by polling station.",
        lang="en",
    )


def _notes(
    p: Package, d: Day, contest: str, result_set: str, problems: list[str], here: list[Station], caveats: Counter[str]
) -> None:
    if problems:
        caveats["declared"] += 1
        caveats[f"declared:{d.day}:{contest or 'all'}"] += 1
        lines = "; ".join(problems[:6]) + ("; and more" if len(problems) > 6 else "")
        p.add(
            "note",
            id=key("note", result_set, "declared"),
            entity_id=result_set,
            kind="caveat",
            body_md="The polling-station rows, summed, differ from the State Electoral Commission's own totals for "
            f"this contest ({lines}). Both are stored as printed: the rows as the polling-station values and the "
            "totals as declared figures.",
            lang="en",
        )
    over = sum(
        1
        for s in here
        if s.counts.get(CAST, s.counts.get(VOTERS, 0)) > s.counts.get(REGISTERED, 0) and REGISTERED in s.counts
    )
    if over:
        caveats["turnout"] += over
        p.add(
            "note",
            id=key("note", result_set, "turnout"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"In {over} polling station{'s' if over > 1 else ''}, more ballots were cast than voters are "
            "printed on the roll. Polling stations abroad and special polling stations take voters registered "
            "elsewhere, so the figures are stored as printed.",
            lang="en",
        )
    sums = sum(
        1
        for s in here
        if VALID in s.counts and CAST in s.counts and s.counts[VALID] + s.counts.get(INVALID, 0) != s.counts[CAST]
    )
    if sums:
        caveats["ballots"] += sums
    votes = sum(
        1
        for s in here
        if VALID in s.counts
        and not contest.startswith("12-1")
        and d.family != "referendum"
        and sum(v for (o, c), v in s.values.items() if not c) != s.counts[VALID]
    )
    if votes:
        caveats["votes"] += votes


def _accept(p: Package, d: Day, caveats: Counter[str]) -> None:
    contests_ = sorted(k.split(":", 2)[2] for k in caveats if k.startswith("declared:"))
    if caveats["declared"]:
        p.accept(
            "accounting.declared",
            f"in {caveats['declared']} contest(s) ({', '.join(contests_)}) the polling-station rows sum to figures "
            "that differ from the State Electoral Commission's printed totals (see each result set's caveat); both "
            "are stored as printed",
        )
    if caveats["turnout"]:
        p.accept(
            "accounting.turnout",
            f"{caveats['turnout']} polling stations (abroad, special, or mixed) print more ballots than voters on their "
            "roll, because they take voters registered elsewhere; stored as printed with a caveat",
        )
    if caveats["ballots"]:
        p.accept(
            "accounting.ballots_sum",
            f"in {caveats['ballots']} polling stations the printed valid and invalid ballots do not add up to the "
            "ballots found in the box; stored as printed",
        )
    if caveats["votes"]:
        p.accept(
            "accounting.votes_valid",
            f"in {caveats['votes']} polling stations the votes for the options do not add up to the printed valid "
            "ballots; stored as printed",
        )
