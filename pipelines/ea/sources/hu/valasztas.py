"""Hungary's national elections from the National Election Office (Nemzeti Választási Iroda, valasztas.hu).

Elections from 1990 to 2024 come from the office's archive of processable results, one bundle per election in the
format the office used that year (pipe-separated text, CSV, or spreadsheets). Elections the archive does not hold yet,
and the 2022 and 2026 parliamentary elections, come from the office's results service (vtr.valasztas.hu), whose
per-settlement polling-station files are crawled once and kept unchanged in one zip per election.
"""

import functools
import html
import io
import json
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import cast
from uuid import uuid5

import fastexcel
import httpx
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "hu.valasztas"
ARCHIVE = "https://static.valasztas.hu/dyn/letoltesek/valasztasi_eredmenyek_1990-2024.zip"
ARCHIVE_PAGE = "https://www.valasztas.hu/korabbi-valasztasok-feldolgozhato-adatai"
VTR = "https://vtr.valasztas.hu"
HUNGARY = "e62e9390-07d2-5939-8163-733a6ca2b373"
TERRITORY = "59a0ba57-5c5c-5af8-9987-c96feb05b10a"
ABROAD = "99"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NVI = key("source", SOURCE)
PARLIAMENT = key("body", "parliament")
EP_BODY = key("body", "european-parliament")


# ---------------------------------------------------------------------------------------------------------------
# Raw files


def _archive() -> zipfile.ZipFile:
    return zipfile.ZipFile(raw.cached(SOURCE, ARCHIVE).path)


def _decoded(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp852")
    except UnicodeError:
        return info.filename


@functools.cache
def bundle(name: str) -> dict[str, bytes]:
    """Every file of one election's bundle in the archive, keyed by its lowercased path inside the bundle."""
    outer = _archive()
    (inner,) = [i for i in outer.infolist() if _decoded(i).split("/")[-1].removesuffix(".zip") == name]
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(outer.read(inner))) as archive:
        for info in archive.infolist():
            path = _decoded(info)
            if info.is_dir() or "__MACOSX" in path:
                continue
            files[path.lower()] = archive.read(info)
    return files


def member(name: str, suffix: str) -> bytes:
    found = [path for path in bundle(name) if path.endswith(suffix.lower())]
    if len(found) != 1:
        raise LookupError(f"{name}: {suffix} matches {found}")
    return bundle(name)[found[0]]


def text(data: bytes) -> str:
    data = data.removeprefix(b"\xef\xbb\xbf")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1250")


def piped(name: str, suffix: str) -> list[list[str]]:
    return [
        [cell.strip() for cell in line.split("|")] for line in text(member(name, suffix)).splitlines() if line.strip()
    ]


def delimited(name: str, suffix: str) -> list[dict[str, str]]:
    body = text(member(name, suffix))
    first = body.split("\n", 1)[0]
    frame = pl.read_csv(
        io.StringIO(body), separator=";" if first.count(";") > first.count(",") else ",", infer_schema_length=0
    )
    frame = frame.rename({column: column.strip().strip("'").strip() for column in frame.columns})
    return [{k: (v or "").strip() for k, v in row.items()} for row in frame.iter_rows(named=True)]


def sheets(name: str, suffix: str) -> dict[str, list[list[str]]]:
    reader = fastexcel.read_excel(member(name, suffix))
    out: dict[str, list[list[str]]] = {}
    for sheet in reader.sheet_names:
        frame = reader.load_sheet_by_name(sheet, header_row=None, dtypes="string").to_polars()
        out[sheet] = [[_cell(value) for value in row] for row in frame.iter_rows()]
    return out


def _cell(value: object) -> str:
    if value is None:
        return ""
    cell = str(value).strip()
    return cell[:-2] if re.fullmatch(r"-?\d+\.0", cell) else cell


def number(cell: str | None) -> int | None:
    if cell is None:
        return None
    cell = cell.strip().replace(" ", "").replace("\xa0", "")
    if not re.fullmatch(r"[+-]?\d+(\.0+)?", cell):
        return None
    return int(float(cell))


# Results service (vtr.valasztas.hu): catalogs are fetched one by one; the per-settlement polling-station files are
# crawled once per election and stored unchanged in one zip, keyed by the folder they came from.
VTR_ELECTIONS = {
    "ogy2022": ("04022333", "04161400"),
    "ogy2026": ("04112100", "05071600"),
    "ep2024": ("06091753", "06201531"),
    "onk2024": ("06091753", "08021106"),
}


def vtr_url(election: str, part: str, name: str) -> str:
    ver, final = VTR_ELECTIONS[election]
    return f"{VTR}/{election}/data/{ver if part == 'ver' else final}/{part}/{name}"


def vtr_json(election: str, part: str, name: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(raw.cached(SOURCE, vtr_url(election, part, name)).path.read_bytes()))


def _crawl_key(election: str) -> str:
    return vtr_url(election, "szavossz", "") + "#SzavkorJkv"


def vtr_settlements(election: str) -> list[dict[str, object]]:
    rows = cast(list[dict[str, object]], vtr_json(election, "ver", "Telepulesek.json")["list"])
    return [cast(dict[str, object], row.get("leiro", row)) for row in rows]


@functools.cache
def vtr_precincts(election: str) -> dict[str, dict[str, object]]:
    """The crawled polling-station files of one election, keyed by MAZ and TAZ."""
    out: dict[str, dict[str, object]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, _crawl_key(election)).path) as archive:
        for name in archive.namelist():
            found = re.search(r"SzavkorJkv-(\d\d)-(\d\d\d)\.json$", name)
            if found:
                out[found[1] + found[2]] = json.loads(archive.read(name))
    return out


VTR_CATALOGS = {
    "ogy2022": (
        ["Telepulesek.json", "EgyeniJeloltek.json", "ListakEsJeloltek.json", "Szervezetek.json", "OevkAdatok.json"],
        ["OevkJkv.json", "ListasJkv.json", "LevelJkv.json"],
    ),
    "ogy2026": (
        ["Telepulesek.json", "EgyeniJeloltek.json", "ListakEsJeloltek.json", "Szervezetek.json", "OevkAdatok.json"],
        ["OevkJkv.json", "ListasJkv.json", "LevelJkv.json"],
    ),
}


def _crawl(election: str) -> None:
    if _crawl_key(election) in raw.keys(SOURCE):
        return
    settlements = vtr_settlements(election)
    _ver, final = VTR_ELECTIONS[election]

    def get(entry: dict[str, object]) -> tuple[str, bytes | None]:
        maz, taz = str(entry["maz"]), str(entry["taz"])
        url = f"{VTR}/{election}/data/{final}/szavossz/{maz}/SzavkorJkv-{maz}-{taz}.json"
        for _ in range(5):
            try:
                response = httpx.get(url, headers={"User-Agent": raw.USER_AGENT}, timeout=120)
            except httpx.TransportError:
                continue
            if response.status_code == 404:
                return url, None
            response.raise_for_status()
            return url, response.content
        raise RuntimeError(f"{url} failed five times")

    with ThreadPoolExecutor(8) as pool:
        fetched = list(pool.map(get, settlements))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for url, content in sorted(fetched, key=lambda item: item[0]):
            if content is None:
                continue
            info = zipfile.ZipInfo(url.removeprefix(f"{VTR}/"), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    missing = sum(content is None for _, content in fetched)
    print(f"{election}: {len(fetched) - missing:,} polling-station files, {missing} settlements without one")
    raw.store(
        SOURCE,
        _crawl_key(election),
        buffer.getvalue(),
        suffix=".zip",
        retrieved_at=datetime.now(UTC),
        media_type="application/zip",
    )


PV14 = "https://static.valasztas.hu/dyn/pv14/szavossz/hu/"
PV14_PAGES = ("orszlist.html", "oevker.html")
PV14_STATIONS = PV14 + "#szkjkv"
# The nationality names on a polling-station page, and the label each national minority list carries on the national
# result page.
PV14_MINORITIES = {
    "Bolgár": "BOLGÁR ORSZÁGOS ÖNKORMÁNYZAT",
    "Görög": "MAGYARORSZÁGI GÖRÖGÖK",
    "Horvát": "ORSZÁGOS HORVÁT ÖNKORMÁNYZAT",
    "Lengyel": "ORSZÁGOS LENGYEL ÖNKORMÁNYZAT",
    "Német": "MNOÖ",
    "Örmény": "ÖRMÉNY ÖNKORMÁNYZAT",
    "Roma": "ORSZÁGOS ROMA ÖNKORMÁNYZAT",
    "Román": "MROÖ",
    "Ruszin": "ORÖ",
    "Szerb": "SZOÖ",
    "Szlovák": "SZLOVÁK LISTA",
    "Szlovén": "ORSZÁGOS SZLOVÉN ÖNKORMÁNYZAT",
    "Ukrán": "UKRÁN ORSZÁGOS ÖNKORMÁNYZAT",
}
EP14 = "https://static.valasztas.hu/dyn/ep14/szavossz/hu/eredm.html"


def table_rows(url: str, encoding: str = "iso-8859-2") -> list[list[str]]:
    """The cells of every table row of a cached results page."""
    page = raw.cached(SOURCE, url).path.read_bytes().decode(encoding)
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.DOTALL | re.IGNORECASE):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL | re.IGNORECASE)
        ]
        if cells:
            rows.append(cells)
    return rows


def _crawl14() -> None:
    """Fetch every polling-station page of the 2014 results site into one stored archive."""
    if PV14_STATIONS in raw.keys(SOURCE):
        return

    def get(path: str) -> tuple[str, bytes]:
        for _ in range(5):
            try:
                response = httpx.get(PV14 + path, headers={"User-Agent": raw.USER_AGENT}, timeout=60)
            except httpx.TransportError:
                continue
            response.raise_for_status()
            return path, response.content
        raise RuntimeError(f"{path} failed five times")

    districts = sorted(
        set(
            re.findall(
                r'href="(M\d\d/E\d\d/evkjkv\.html)"',
                raw.cached(SOURCE, PV14 + "oevker.html").path.read_text("iso-8859-2"),
            )
        )
    )
    with ThreadPoolExecutor(8) as pool:
        pages = list(pool.map(get, districts))
        links = sorted(
            {
                district.rsplit("/", 2)[0] + "/" + link.removeprefix("../")
                for district, page in pages
                for link in re.findall(r'href="(\.\./T\d+/szkjkv_\d+\.html)"', page.decode("iso-8859-2"))
            }
        )
        fetched = list(pool.map(get, links))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(fetched):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    print(f"2014: {len(fetched):,} polling-station pages")
    raw.store(
        SOURCE,
        PV14_STATIONS,
        buffer.getvalue(),
        suffix=".zip",
        retrieved_at=datetime.now(UTC),
        media_type="application/zip",
    )


def minority_votes() -> dict[tuple[str, str, int], dict[str, int]]:
    """The 2014 national minority list votes of each polling station, keyed by county, settlement and station."""
    out: dict[tuple[str, str, int], dict[str, int]] = {}
    with zipfile.ZipFile(raw.cached(SOURCE, PV14_STATIONS).path) as archive:
        for name in archive.namelist():
            found = re.fullmatch(r"M(\d\d)/T(\d+)/szkjkv_(\d+)\.html", name)
            if not found:
                continue
            page = archive.read(name).decode("iso-8859-2")
            votes: dict[str, int] = {}
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.DOTALL | re.IGNORECASE):
                cells = [
                    re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip().replace(" ", "")
                    for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL | re.IGNORECASE)
                ]
                if len(cells) == 7 and cells[0] in PV14_MINORITIES:
                    votes[cells[0]] = number(cells[6]) or 0
            out[(found[1], found[2], int(found[3]))] = votes
    return out


def fetch() -> None:
    raw.fetch(SOURCE, ARCHIVE, suffix=".zip")
    for page in PV14_PAGES:
        raw.fetch(SOURCE, PV14 + page, suffix=".html")
    _crawl14()
    raw.fetch(SOURCE, EP14, suffix=".html")
    for election, (catalogs, results) in VTR_CATALOGS.items():
        raw.fetch(SOURCE, f"{VTR}/{election}/data/config.json", suffix=".json")
        for name in catalogs:
            raw.fetch(SOURCE, vtr_url(election, "ver", name), suffix=".json")
        for name in results:
            raw.fetch(SOURCE, vtr_url(election, "szavossz", name), suffix=".json")
        _crawl(election)
    _fetch_winner_pages()


# ---------------------------------------------------------------------------------------------------------------
# Places

COUNTIES = {
    "01": "Budapest",
    "02": "Baranya",
    "03": "Bács-Kiskun",
    "04": "Békés",
    "05": "Borsod-Abaúj-Zemplén",
    "06": "Csongrád-Csanád",
    "07": "Fejér",
    "08": "Győr-Moson-Sopron",
    "09": "Hajdú-Bihar",
    "10": "Heves",
    "11": "Jász-Nagykun-Szolnok",
    "12": "Komárom-Esztergom",
    "13": "Nógrád",
    "14": "Pest",
    "15": "Somogy",
    "16": "Szabolcs-Szatmár-Bereg",
    "17": "Tolna",
    "18": "Vas",
    "19": "Veszprém",
    "20": "Zala",
}
ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50}


def _roman(text: str) -> int:
    total = 0
    for index, char in enumerate(text):
        value = ROMAN[char]
        total += -value if index + 1 < len(text) and ROMAN[text[index + 1]] > value else value
    return total


def fold(name: str) -> str:
    """A settlement name reduced for matching across spellings, cases, and accents."""
    plain = unicodedata.normalize("NFC", name.lower()).replace("õ", "ő").replace("û", "ű").replace("ô", "ő")
    found = re.match(r"\s*budapest\W*([ivxl]+|\d+)\b", plain)
    if found:
        part = found[1]
        return f"budapest{int(part) if part.isdigit() else _roman(part)}"
    plain = plain.translate(str.maketrans("íóőúű", "ioöuü"))
    return re.sub(r"[^a-z0-9áéöü]", "", plain)


def county_key(name: str) -> str:
    folded = fold(name)
    if folded.startswith("szolnok"):
        return "11"
    for code, county in COUNTIES.items():
        own = fold(county)
        if own.startswith(folded) or folded[:5] == own[:5]:
            return code
    raise LookupError(f"no county is named {name}")


@dataclass
class Places:
    """Settlements: the current list from the results service, and every name the elections use."""

    names: dict[str, str] = field(default_factory=dict)
    by_name: dict[tuple[str, str], str] = field(default_factory=dict)
    national: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    used: dict[str, tuple[str, str]] = field(default_factory=dict)
    historic: dict[str, tuple[str, str]] = field(default_factory=dict)

    def add(self, code: str, name: str) -> None:
        self.names.setdefault(code, name)
        self.by_name.setdefault((code[:2], fold(name)), code)
        self.national[fold(name)].add(code)

    def resolve(self, maz: str, taz: str | None, name: str, trust_code: bool) -> str:
        """The settlement area code for a settlement as one election names it."""
        maz = maz.zfill(2)
        code = None if taz is None else maz + taz.zfill(3)
        folded = fold(name)
        if code is not None and code in self.names and fold(self.names[code]) == folded:
            found = code
        elif (maz, folded) in self.by_name:
            found = self.by_name[(maz, folded)]
        elif len(self.national.get(folded, ())) == 1:
            (found,) = self.national[folded]
        elif trust_code and code is not None and code in self.names:
            found = code
        else:
            found = f"{maz}-{changeset.slug(name)}"
            self.historic[found] = (maz, name)
        self.used[found] = (maz, name)
        return found


@functools.cache
def places() -> Places:
    found = Places()
    for election in ("ogy2026", "ogy2022"):
        for entry in vtr_settlements(election):
            if str(entry["maz"]) != ABROAD:
                found.add(f"{entry['maz']}{entry['taz']}", str(entry["megnev"]))
    return found


def settlement(code: str) -> str:
    return key("area", "hu.telepules", code)


def county(code: str) -> str:
    return key("area", "hu.megye", code)


def district(period: int, maz: str, oevk: str) -> str:
    return key("area", "hu.oevk", period, maz, oevk)


OEVK_NAMES: dict[tuple[int, str, str], str] = {}


def oevk(period: int, maz: str, number: str, name: str | None = None) -> str:
    maz, number = maz.zfill(2), number.zfill(2)
    OEVK_NAMES.setdefault(
        (period, maz, number), name or f"{COUNTIES[maz]} {'főváros' if maz == '01' else 'megye'} {number}. OEVK"
    )
    return district(period, maz, number)


def oevk_period(day: date) -> int:
    return 1990 if day.year < 2014 else 2014 if day.year < 2026 else 2026


# ---------------------------------------------------------------------------------------------------------------
# Parties

PARTIES: dict[str, str] = {}
INDEPENDENT = re.compile(r"^(FÜGG(ETLEN)?\.?( JELÖLT)?|FÜGGETLEN JELÖLT|független( jelölt)?|)$", re.IGNORECASE)


def party_label(label: str) -> str:
    return re.sub(r"\s+", " ", label.replace("|", ",")).strip(" ,")


def party(label: str | None) -> str | None:
    if label is None:
        return None
    label = party_label(label)
    if INDEPENDENT.match(label):
        return None
    folded = label.upper()
    PARTIES.setdefault(folded, label)
    return key("party", folded)


# ---------------------------------------------------------------------------------------------------------------
# Building one election day


def person_text(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip()
    if name.isupper():
        name = " ".join(part.capitalize() if not re.fullmatch(r"[A-Z]+\.", part) else part for part in name.split())
        name = re.sub(r"-(\w)", lambda m: "-" + m[1].upper(), name)
    return name


TITLES = re.compile(r"^((DR|IFJ|ID|ÖZV|PROF|ID\.)\.?\s+)+", re.IGNORECASE)


def same_name(name: str) -> str:
    return re.sub(r"[^A-ZÁÉÍÓÖŐÚÜŰ]", "", TITLES.sub("", re.sub(r"\s+", " ", name).strip()).upper())


CITED: set[str] = set()


def cited() -> set[str]:
    """Every raw file an election package cites, so the registry cites each as an artifact of the office."""
    return CITED


CAVEATS = {
    "accounting.votes_valid": (
        (
            "In {n} reporting unit{s} the votes for the options do not add up to the valid ballots in the office's file. "
            "The figures are stored as printed."
        ),
        "the office's polling-station lines do not add up to the valid ballots it prints; stored as printed",
    ),
    "accounting.ballots_sum": (
        (
            "In {n} reporting unit{s} the valid and invalid ballots do not add up to the ballots in the box in the "
            "office's file. The figures are stored as printed."
        ),
        "the valid and invalid ballots the office prints do not add up to the ballots in the box; stored as printed",
    ),
    "accounting.turnout": (
        (
            "In {n} reporting unit{s} more ballots were in the box than voters were on the roll at its close, because "
            "voters with a certificate or a transfer voted there. The figures are stored as printed."
        ),
        "more ballots than voters on the station's roll, from voters who voted there with a certificate or transfer",
    ),
    "accounting.declared": (
        (
            "{n} of the office's declared totals for this contest differ from the sum of its own polling-station lines. "
            "Both are stored as published."
        ),
        "",
    ),
}


class Day:
    """One package: every election the office held on one date."""

    def __init__(self, day: date, summary: str, depends: Iterable[str] = ()) -> None:
        self.day = day
        self.p = Package(
            f"{SOURCE}/{day}",
            SOURCE,
            summary,
            depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", *depends],
        )
        self.p.external |= {HUNGARY, TERRITORY}
        self.values: dict[tuple[str, str, str | None, int], int] = {}
        self.units: dict[str, str] = {}
        self.options: dict[tuple[str, str], str] = {}
        self.sets: dict[str, str] = {}
        self.declared: dict[tuple[str, str | None, int], int] = {}
        self.outcomes: set[tuple[str, str, str | None]] = set()
        self.people: dict[str, str] = {}
        self.seen: set[tuple[str, str]] = set()
        self.inherited: set[str] = set()
        self.reasons: dict[str, str] = {}
        self.cited: set[str] = set()
        self.elections: set[str] = set()
        self.contest_ids: dict[str, str] = {}
        for depend in depends:
            built = package.folder(depend) / "build" / "person.parquet"
            if built.exists():
                self.inherited |= set(pl.read_parquet(built, columns=["id"])["id"])

    def first(self, tag: str, label: str) -> bool:
        """Whether this is the first time the label is seen in the contest."""
        if (tag, label) in self.seen:
            return False
        self.seen.add((tag, label))
        return True

    def cite(self, name: str) -> None:
        if name not in self.cited:
            self.cited.add(name)
            CITED.add(name)
            self.p.uses(raw.cached(SOURCE, name))

    def contest_id(self, tag: str) -> str:
        """The contest's id: an existing contest of another pipeline, or this package's own."""
        return self.contest_ids.get(tag) or key("contest", tag)

    def election(
        self,
        tag: str,
        kind: str,
        english: str,
        native: str,
        path: str,
        day: date | None = None,
        parent: str | None = None,
    ) -> str:
        id = key("election", tag)
        if id in self.elections:
            return id
        self.elections.add(id)
        on = day or self.day
        self.p.add(
            "election", id=id, polity_id=HUNGARY, parent_id=parent, kind=kind, date_from=on, date_to=on, status="held"
        )
        self.p.name(id, english, "en", "official")
        self.p.name(id, native, "hu", "native", False)
        self.p.add("slug", path=path, entity_id=id, is_current=True)
        return id

    def contest(
        self,
        tag: str,
        election: str,
        kind: str,
        name: str,
        path: str,
        *,
        district_area: str = TERRITORY,
        magnitude: int | None = 1,
        round_number: int = 1,
        previous: str | None = None,
        system: str | None = None,
        body: str | None = None,
        office: str | None = None,
        method: str,
        english: str | None = None,
    ) -> str:
        """Stage a contest and its official result set; returns the result set id."""
        contest = self.contest_id(tag)
        if tag in self.contest_ids:
            self.p.external |= {contest, election}
            return self._result_set(tag, election, contest, method, default=False)
        self.p.add(
            "contest",
            id=contest,
            election_id=election,
            kind=kind,
            office_id=office,
            body_id=body,
            district_area_id=district_area,
            magnitude=magnitude,
            round_number=round_number,
            previous_round_id=None if previous is None else key("contest", previous),
            electoral_system_id=system,
            default_result_kind="counted",
        )
        self.p.name(contest, name, "hu", "official")
        if english:
            self.p.name(contest, english, "en", "english", False)
        self.p.add("slug", path=path, entity_id=contest, is_current=True)
        return self._result_set(tag, election, contest, method)

    def _result_set(self, tag: str, election: str, contest: str, method: str, default: bool = True) -> str:
        result_set = key("result_set", tag)
        self.p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=NVI,
            status="active",
            is_default=default,
            completeness=1.0,
        )
        self.p.add(
            "note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en"
        )
        self.sets[tag] = result_set
        return result_set

    def option(
        self,
        tag: str,
        label: str,
        kind: str,
        *,
        nominator: str | None = None,
        position: int | None = None,
        ident: str | None = None,
    ) -> str:
        """Stage an option of the contest `tag`; the option's own key follows the label, or `ident` when given."""
        index = (tag, ident or same_name(label) or label)
        found = self.options.get(index)
        if found is not None:
            return found
        id = key("option", tag, index[1])
        self.p.add(
            "contest_option",
            id=id,
            contest_id=self.contest_id(tag),
            kind=kind,
            party_id=party(nominator) if kind != "answer" else None,
            ballot_position=position,
            ballot_label=re.sub(r"\s+", " ", label).strip(),
        )
        self.options[index] = id
        return id

    def candidate(
        self,
        tag: str,
        option: str,
        person: str,
        name: str,
        *,
        role: str = "head",
        position: int | None = None,
        nominator: str | None = None,
    ) -> str:
        """Stage a person (once per person key) and their candidacy on an option."""
        person_id = self.people.get(person)
        if person_id is None:
            person_id = self.people[person] = key("person", person)
            if person_id not in self.inherited:
                self.p.add("person", id=person_id)
                self.p.name(person_id, person_text(TITLES.sub("", name.strip())), "hu", "official")
        candidacy = key("candidacy", tag, option, person)
        self.p.add(
            "candidacy",
            id=candidacy,
            person_id=person_id,
            option_id=option,
            role=role,
            list_position=position,
            ballot_name=re.sub(r"\s+", " ", name).strip(),
            member_of_party_id=None,
            is_independent=nominator is not None and party(nominator) is None,
        )
        return candidacy

    def unit(self, area_code: str, number: str | int, kind: str = "standard", relation: str = "within") -> str:
        code = (
            f"{self.day}:{area_code}:{int(number):03d}" if str(number).isdigit() else f"{self.day}:{area_code}:{number}"
        )
        found = self.units.get(code)
        if found is None:
            found = self.units[code] = key("unit", code)
            self.p.add(
                "reporting_unit",
                id=found,
                polity_id=HUNGARY,
                scheme_code="hu.szavazokor",
                code=code,
                area_relation=relation,
                area_id=None if relation == "unlocated" else settlement(area_code),
                kind=kind,
            )
        return found

    def settlement_unit(self, area_code: str) -> str:
        code = f"{self.day}:{area_code}"
        found = self.units.get(code)
        if found is None:
            found = self.units[code] = key("unit", code)
            self.p.add(
                "reporting_unit",
                id=found,
                polity_id=HUNGARY,
                scheme_code="hu.telepules",
                code=code,
                area_relation="equals",
                area_id=settlement(area_code),
                kind="standard",
            )
        return found

    def special_unit(self, name: str, kind: str) -> str:
        code = f"{self.day}:{name}"
        found = self.units.get(code)
        if found is None:
            found = self.units[code] = key("unit", code)
            self.p.add(
                "reporting_unit",
                id=found,
                polity_id=HUNGARY,
                scheme_code="hu.szavazokor",
                code=code,
                area_relation="unlocated",
                area_id=None,
                kind=kind,
            )
        return found

    def value(self, tag: str, unit: str, option: str | None, measure: int, amount: int | None) -> None:
        if amount is None:
            return
        index = (self.sets[tag], unit, option, measure)
        if index in self.values:
            raise ValueError(f"{self.day} {tag}: a second value for {index}")
        self.values[index] = amount

    def accounting(
        self,
        tag: str,
        unit: str,
        registered: int | None,
        voters: int | None,
        cast: int | None,
        invalid: int | None,
        valid: int | None,
    ) -> None:
        for measure, amount in ((2, registered), (14, voters), (3, cast), (5, invalid), (4, valid)):
            self.value(tag, unit, None, measure, amount)

    def declare(self, tag: str, option: str | None, measure: int, amount: int | None) -> None:
        if amount is not None:
            self.declared[(self.sets[tag], option, measure)] = amount

    def outcome(self, tag: str, option: str, kind: str, seats: int | None = None, candidacy: str | None = None) -> None:
        index = (self.sets[tag], option, candidacy)
        if index in self.outcomes:
            return
        self.outcomes.add(index)
        self.p.add(
            "outcome", result_set_id=self.sets[tag], option_id=option, candidacy_id=candidacy, kind=kind, seats=seats
        )

    def reconcile(self) -> None:
        """Count where the office's figures disagree with each other, note it on each result set, and accept it."""
        kinds = {row["id"]: row for row in self.p.rows.get("contest", [])}
        checked = {
            self.sets[tag]
            for tag in self.sets
            if tag in self.contest_ids
            or (row := kinds[key("contest", tag)])["magnitude"] == 1
            or row["kind"] == "list_allocation"
        }
        units: dict[tuple[str, str], dict[int, int]] = defaultdict(dict)
        votes: Counter[tuple[str, str]] = Counter()
        for (result_set, unit, option, measure), amount in self.values.items():
            if option is None:
                units[(result_set, unit)][measure] = amount
            elif measure == 1:
                votes[(result_set, unit)] += amount
        found: dict[str, Counter[str]] = {code: Counter() for code in CAVEATS}
        for (result_set, unit), figures in units.items():
            valid, invalid, cast_, registered = figures.get(4), figures.get(5), figures.get(3), figures.get(2)
            if result_set in checked and valid is not None and votes[(result_set, unit)] != valid:
                found["accounting.votes_valid"][result_set] += 1
            if valid is not None and cast_ is not None and valid + (invalid or 0) != cast_:
                found["accounting.ballots_sum"][result_set] += 1
            if cast_ is not None and registered is not None and cast_ > registered:
                found["accounting.turnout"][result_set] += 1
        sums: Counter[tuple[str, str | None, int]] = Counter()
        for (result_set, _, option, measure), amount in self.values.items():
            sums[(result_set, option, measure)] += amount
        gaps = {index: amount - sums[index] for index, amount in self.declared.items() if sums[index] != amount}
        for result_set, _, _ in gaps:
            found["accounting.declared"][result_set] += 1
        for code, counts in found.items():
            if not counts:
                continue
            what, reason = CAVEATS[code]
            reason = self.reasons.get(code, reason)
            total = counts.total()
            if code == "accounting.declared" and code in self.reasons:
                self.p.accept(code, f"{total} declared totals in {len(counts)} contests: {reason}")
            elif code == "accounting.declared":
                largest = max(abs(gap) for gap in gaps.values())
                self.p.accept(
                    code,
                    f"{total} declared totals in {len(counts)} contests differ from the sum of the office's own "
                    f"reporting-unit lines (largest gap {largest:,} votes); both are stored as published",
                )
            else:
                self.p.accept(code, f"{total} reporting units in {len(counts)} contests: {reason}")
            for result_set, n in counts.items():
                self.p.add(
                    "note",
                    id=key("note", result_set, code),
                    entity_id=result_set,
                    kind="caveat",
                    body_md=what.format(n=n, s="s" if n > 1 else ""),
                    lang="en",
                )

    def write(self) -> None:
        self.reconcile()
        counted = {result_set for result_set, _, _, _ in self.values}
        for row in self.p.rows.get("result_set", []):
            if row["id"] not in counted:
                row["completeness"] = 0.0
        if self.values:
            self.p.frame(
                "result_value",
                pl.DataFrame(
                    {
                        "result_set_id": [k[0] for k in self.values],
                        "unit_id": [k[1] for k in self.values],
                        "option_id": [k[2] for k in self.values],
                        "channel": [0] * len(self.values),
                        "measure": [k[3] for k in self.values],
                        "count_round": [0] * len(self.values),
                        "flags": [0] * len(self.values),
                        "value": list(self.values.values()),
                    },
                    schema_overrides={"option_id": pl.String},
                ),
            )
        for (result_set, option, measure), amount in self.declared.items():
            self.p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=amount)
        self.p.write()

    def summed(self, tag: str, measure: int = 1) -> Counter[str | None]:
        totals: Counter[str | None] = Counter()
        result_set = self.sets[tag]
        for (rs, _, option, m), amount in self.values.items():
            if rs == result_set and m == measure:
                totals[option] += amount
        return totals


# ---------------------------------------------------------------------------------------------------------------
# Electoral systems and methods

SMD_TWO_ROUND = key("electoral_system", "smd", 1990)
LIST_1990 = key("electoral_system", "regional-list", 1990)
LIST_1994 = key("electoral_system", "regional-list", 1994)
SMD_FPTP = key("electoral_system", "smd", 2014)
LIST_2014 = key("electoral_system", "national-list", 2014)
EP_SYSTEM = key("electoral_system", "ep", 2004)
SYSTEMS: list[tuple[str, date, date | None, dict[str, object], str]] = [
    (
        SMD_TWO_ROUND,
        date(1989, 10, 23),
        date(2011, 12, 30),
        {"formula": "two_round", "majority": 0.5, "advance": 3},
        (
            "176 single-member districts. In the first round a candidate needed more than half of the valid votes, with "
            "a turnout above half of the electorate. Otherwise every candidate with at least 15% of the votes, and at "
            "least the top three, could run in the second round, where the most votes won if a quarter of the electorate "
            "turned out."
        ),
    ),
    (
        LIST_1990,
        date(1989, 10, 23),
        date(1994, 1, 1),
        {
            "formula": "hagenbach_bischoff_quota",
            "threshold": {"level": "list", "scope": "national", "percent": 4},
        },
        (
            "152 seats on the regional lists of the 19 counties and Budapest, allocated by the Hagenbach-Bischoff quota "
            "among the lists that won 4% of the regional list votes nationwide; the remaining seats went to national "
            "compensation lists from the votes that won no seat."
        ),
    ),
    (
        LIST_1994,
        date(1994, 1, 1),
        date(2011, 12, 30),
        {
            "formula": "hagenbach_bischoff_quota",
            "threshold": {"level": "list", "scope": "national", "percent": 5},
        },
        (
            "152 seats on the regional lists of the 19 counties and Budapest, allocated by the Hagenbach-Bischoff quota "
            "among the lists that won 5% of the regional list votes nationwide (10% for a joint list of two parties, 15% "
            "for three or more); the remaining seats went to national compensation lists."
        ),
    ),
    (
        SMD_FPTP,
        date(2011, 12, 30),
        None,
        {"formula": "fptp"},
        "106 single-member districts, each won by the candidate with the most votes in a single round.",
    ),
    (
        LIST_2014,
        date(2011, 12, 30),
        None,
        {"formula": "dhondt", "threshold": {"level": "list", "scope": "national", "percent": 5}},
        (
            "93 seats from national lists, allocated by D'Hondt among the lists that won 5% of the list votes (10% for a "
            "joint list of two parties, 15% for three or more), counting the votes that won no district seat and the "
            "winners' surplus votes. National minority lists win a seat at a reduced quota."
        ),
    ),
    (
        EP_SYSTEM,
        date(2004, 1, 1),
        None,
        {"formula": "dhondt", "threshold": {"level": "list", "scope": "national", "percent": 5}},
        (
            "Hungary's members of the European Parliament, elected from closed national lists by D'Hondt among the lists "
            "that won 5% of the valid votes."
        ),
    ),
]
PRECINCT_METHOD = (
    "Counted by the polling-station commissions and published by the National Election Office (Nemzeti Választási "
    "Iroda) per polling station (szavazókör). Registered voters are the electoral roll at the close of voting, voters "
    "are those who signed the roll, ballots cast are the stamped ballots in the box, and valid and invalid are the "
    "valid and invalid ballots.{extra}"
)


def method(extra: str = "") -> str:
    return PRECINCT_METHOD.format(extra=f" {extra}" if extra else "")


# ---------------------------------------------------------------------------------------------------------------
# Parliament


def ordinal(n: int) -> str:
    return {1: "first", 2: "second"}[n]


def _parliament_elections(d: Day, year: int, round_number: int) -> tuple[str, str]:
    path = f"hu/{d.day}-parliamentary" + ("-runoff" if round_number == 2 else "")
    suffix = "" if round_number == 1 else ", second round"
    election = d.election(
        f"ogy:{year}:{round_number}",
        "general" if round_number == 1 else "runoff",
        f"{year} Hungarian parliamentary election{suffix}",
        f"{year}. évi országgyűlési választás" + ("" if round_number == 1 else ", második forduló"),
        path,
    )
    return election, path


def smd_tag(year: int, round_number: int, maz: str, number: str) -> str:
    return f"ogy:{year}:egyeni:{round_number}:{maz.zfill(2)}-{number.zfill(2)}"


def smd_contest(d: Day, year: int, round_number: int, election: str, path: str, maz: str, number: str) -> str:
    tag = smd_tag(year, round_number, maz, number)
    if tag in d.sets:
        return tag
    period = oevk_period(d.day)
    area = oevk(period, maz, number)
    name = OEVK_NAMES[(period, maz.zfill(2), number.zfill(2))]
    d.contest(
        tag,
        election,
        "body_seats",
        name,
        f"{path}/{changeset.slug(name)}",
        district_area=area,
        magnitude=1,
        round_number=round_number,
        previous=smd_tag(year, 1, maz, number) if round_number == 2 else None,
        system=SMD_TWO_ROUND if year < 2014 else SMD_FPTP,
        body=PARLIAMENT,
        method=method(),
    )
    return tag


def regional_tag(year: int, round_number: int, maz: str) -> str:
    return f"ogy:{year}:lista:{round_number}:{maz.zfill(2)}"


def regional_contest(d: Day, year: int, round_number: int, election: str, path: str, maz: str) -> str:
    tag = regional_tag(year, round_number, maz)
    if tag in d.sets:
        return tag
    maz = maz.zfill(2)
    name = f"{COUNTIES[maz]} {'fővárosi' if maz == '01' else 'megyei'} területi lista"
    d.contest(
        tag,
        election,
        "list_allocation",
        name,
        f"{path}/lista-{changeset.slug(COUNTIES[maz])}",
        district_area=county(maz),
        magnitude=None,
        round_number=round_number,
        previous=regional_tag(year, 1, maz) if round_number == 2 else None,
        system=LIST_1990 if year == 1990 else LIST_1994,
        body=PARLIAMENT,
        method=method(),
        english=f"{COUNTIES[maz]} regional list",
    )
    return tag


def national_list_contest(d: Day, year: int, election: str, path: str) -> str:
    tag = f"ogy:{year}:lista"
    d.contest(
        tag,
        election,
        "list_allocation",
        "Országos lista",
        f"{path}/orszagos-lista",
        magnitude=93,
        system=LIST_2014,
        body=PARLIAMENT,
        method=method(
            "Postal votes from voters without a Hungarian address are counted nationally and stored on one unit "
            "without a place. National minority lists are options of the same contest."
        ),
        english="National list",
    )
    return tag


OLD_PARLIAMENT = {
    1990: ("1990_parlamenti", date(1990, 3, 25), date(1990, 4, 8), "evktet.txt", "tertet.txt"),
    1994: ("1994_parlamenti", date(1994, 5, 8), date(1994, 5, 29), "oevk_jkv_tetel.txt", "megye_jkv_tetel.txt"),
    1998: ("1998_parlamenti", date(1998, 5, 10), date(1998, 5, 24), "evktet.txt", "tertet.txt"),
}


def _declare_candidates(d: Day, tag: str, rows: Iterable[tuple[str, int]]) -> list[str]:
    missing = []
    for name, votes in rows:
        option = d.options.get((tag, same_name(name)))
        if option is None:
            missing.append(f"{tag} {name}")
            continue
        d.declare(tag, option, 1, votes)
    return missing


def _unrepeat(
    rows: list[list[str]],
    label: Callable[[list[str]], str],
    votes: Callable[[list[str]], int],
    valid: dict[tuple[str, str, str], int],
) -> tuple[list[list[str]], Counter[tuple[str, str, str]]]:
    """Drop the repeated line of a polling station that names one option twice: keep the occurrence that brings the
    station closest to its valid ballots, the later one on a tie."""
    blocks: dict[tuple[str, str, str], list[list[str]]] = defaultdict(list)
    for row in rows:
        blocks[(row[1], row[2], row[3])].append(row)
    kept: list[list[str]] = []
    repeated: Counter[tuple[str, str, str]] = Counter()
    for place, block in blocks.items():
        labels = [label(row) for row in block]
        if len(set(labels)) == len(labels):
            kept += block
            continue
        first: dict[str, list[str]] = {}
        last: dict[str, list[str]] = {}
        for row, text_ in zip(block, labels, strict=True):
            first.setdefault(text_, row)
            last[text_] = row
        target = valid.get(place)
        options = (list(last.values()), list(first.values()))
        choice = min(options, key=lambda rows_: abs(sum(map(votes, rows_)) - (target or 0)))
        repeated[place] += len(block) - len(choice)
        kept += choice
    return kept, repeated


def old_parliament(d: Day, year: int, round_number: int) -> None:
    name, _, _, smd_file, list_file = OLD_PARLIAMENT[year]
    d.cite(ARCHIVE)
    election, path = _parliament_elections(d, year, round_number)
    found = places()
    where: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    valid: dict[tuple[str, str, str], int] = {}
    valid_lists: dict[tuple[str, str, str], int] = {}
    for row in piped(name, "szkf.txt"):
        kind, ford, code, taz, sorsz, county_name, telep, district_number = row[:8]
        if int(ford) != round_number:
            continue
        maz = county_key(county_name)
        nums, _ = _numbers(row[8:])
        area = found.resolve(maz, None, telep, trust_code=False)
        unit = d.unit(area, sorsz)
        if kind.lower().startswith("egy"):
            tag = smd_contest(d, year, round_number, election, path, maz, district_number)
            where[("egy", code, taz, sorsz)] = (unit, tag)
            valid[(code, taz, sorsz)] = nums[8]
        else:
            tag = regional_contest(d, year, round_number, election, path, maz)
            where[("lis", code, taz, sorsz)] = (unit, tag)
            valid_lists[(code, taz, sorsz)] = nums[8]
        d.accounting(tag, unit, nums[3], nums[4], nums[5], nums[7], nums[8])
    positioned = year > 1990
    smd_agg: dict[tuple[str, str], tuple[str, int, str]] = {}
    for row in piped(name, smd_file):
        if int(row[0]) == round_number:
            tag = smd_tag(year, round_number, county_key(row[2]), row[3])
            label = " ".join(cell for cell in row[4:-2] if not cell.isdigit())
            smd_agg[(tag, str(number(row[4])) if positioned else same_name(label))] = (
                label,
                number(row[-2]) or 0,
                row[-1],
            )
    list_agg: dict[tuple[str, str], tuple[str, int]] = {}
    for row in piped(name, list_file):
        if int(row[0]) == round_number:
            tag = regional_tag(year, round_number, county_key(row[2]))
            list_agg[(tag, str(number(row[3])) if positioned else same_name(row[-2]))] = (row[-2], number(row[-1]) or 0)
    smd_rows, repeated = _unrepeat(
        [row for row in piped(name, "egy_szkt.txt") if int(row[0]) == round_number],
        lambda row: str(number(row[4])) if positioned else same_name(" ".join(row[4:6])),
        lambda row: number(row[-2]) or 0,
        valid,
    )
    list_rows, repeated_lists = _unrepeat(
        [row for row in piped(name, "lis_szkt.txt") if int(row[0]) == round_number],
        lambda row: str(number(row[4])) if positioned else same_name(row[-2]),
        lambda row: number(row[-1]) or 0,
        valid_lists,
    )
    for kind, counts in (("egy", repeated), ("lis", repeated_lists)):
        by_tag: Counter[str] = Counter()
        for (maz, taz, sorsz), n in counts.items():
            by_tag[where[(kind, maz, taz, sorsz)][1]] += n
        for tag, n in by_tag.items():
            d.p.add(
                "note",
                id=key("note", d.sets[tag], "repeated"),
                entity_id=d.sets[tag],
                kind="caveat",
                body_md=f"In {n} polling station{'s' if n > 1 else ''} the office's file prints one "
                f"{'candidate' if kind == 'egy' else 'list'} twice, once in place of another line. The line that brings "
                "the polling station closest to its valid ballots is kept and the other is left out.",
                lang="en",
            )
    for row in smd_rows:
        _, maz, taz, sorsz = row[:4]
        if positioned:
            position, title, person, votes, nominator = row[4], row[5], row[6], row[7], row[8]
        else:
            position, title, person, votes, nominator = "", row[4], row[5], row[6], row[7]
        unit, tag = where[("egy", maz, taz, sorsz)]
        label = f"{title} {person}".strip()
        ident = str(number(position)) if positioned else same_name(label)
        if (tag, ident) in smd_agg:
            label, _, nominator = smd_agg[(tag, ident)]
        option = d.option(tag, label, "candidate", nominator=nominator, position=number(position), ident=ident)
        if d.first(tag, ident):
            d.candidate(tag, option, f"ogy:{year}:{tag.split(':')[-1]}:{same_name(label)}", label, nominator=nominator)
        d.value(tag, unit, option, 1, number(votes))
    for row in list_rows:
        _, maz, taz, sorsz = row[:4]
        position, label, votes = (row[4], row[5], row[6]) if positioned else ("", row[4], row[5])
        unit, tag = where[("lis", maz, taz, sorsz)]
        ident = str(number(position)) if positioned else same_name(label)
        if (tag, ident) in list_agg:
            label = list_agg[(tag, ident)][0]
        option = d.option(tag, label, "list", nominator=label, position=number(position), ident=ident)
        d.value(tag, unit, option, 1, number(votes))
    missing = []
    for (tag, ident), (label, votes, _) in smd_agg.items():
        option = d.options.get((tag, ident))
        if option is None:
            missing.append(f"{tag} {label}")
        else:
            d.declare(tag, option, 1, votes)
    for (tag, ident), (label, votes) in list_agg.items():
        option = d.options.get((tag, ident))
        if option is None:
            missing.append(f"{tag} {label}")
        else:
            d.declare(tag, option, 1, votes)
    if missing:
        print(f"{d.day}: {len(missing)} declared rows match no option: {missing[:5]}")


MID_PARLIAMENT = {
    2002: ("2002_parlamenti", date(2002, 4, 7), date(2002, 4, 21), ("1_ford/", "2_ford/"), 3),
    2006: ("2006_parlamenti", date(2006, 4, 9), date(2006, 4, 23), ("txt_1ford/", "txt_2ford/"), 4),
    2010: ("2010_parlamenti", date(2010, 4, 11), date(2010, 4, 25), ("txt_1ford/", "txt_2ford/"), 4),
}


def _numbers(cells: list[str]) -> tuple[list[int], list[str]]:
    values: list[int] = []
    for index, cell in enumerate(cells):
        found = number(cell)
        if found is None:
            return values, cells[index:]
        values.append(found)
    return values, []


def _candidate_cells(year: int, cells: list[str]) -> tuple[str, str, int | None, str]:
    """Position, printed name, votes, and nominating parties from the cells after the location."""
    if year == 2002:
        position, title, person, votes, rest = cells[0], cells[1], cells[2], cells[3], cells[5:]
    else:
        position, title, person, votes, rest = cells[0], cells[1], f"{cells[2]} {cells[3]}", cells[4], cells[6:]
    return position, f"{title} {person}".strip(), number(votes), ",".join(cell for cell in rest if cell)


def mid_parliament(d: Day, year: int, round_number: int) -> None:
    name, _, _, folders, registered = MID_PARLIAMENT[year]
    folder = folders[round_number - 1]
    d.cite(ARCHIVE)
    election, path = _parliament_elections(d, year, round_number)
    found = places()
    kinds = ["egy", "lis"] if round_number == 1 else ["egy"]
    where: dict[tuple[str, str, str, str], tuple[str, str]] = {}
    for kind in kinds:
        for row in piped(name, f"{folder}{kind}_szkf.txt"):
            maz, taz, sorsz, _, telep, district_number = row[:6]
            nums, rest = _numbers(row[6:])
            area = found.resolve(maz, taz, telep, trust_code=True)
            abroad = bool(rest) and rest[0].lower().startswith("kül")
            unit = d.unit(area, sorsz, "abroad_mission" if abroad else "standard")
            if kind == "egy":
                tag = smd_contest(d, year, round_number, election, path, maz, district_number)
            else:
                tag = regional_contest(d, year, round_number, election, path, maz)
            where[(kind, maz, taz, sorsz)] = (unit, tag)
            d.accounting(tag, unit, nums[registered], nums[-5], nums[-4], nums[-2], nums[-1])
        for row in piped(name, f"{folder}{kind}_szkt.txt"):
            maz, taz, sorsz = row[:3]
            unit, tag = where[(kind, maz, taz, sorsz)]
            if kind == "egy":
                position, label, votes, nominator = _candidate_cells(year, row[3:])
                option = d.option(tag, label, "candidate", nominator=nominator, position=number(position))
                if d.first(tag, label):
                    person = f"ogy:{year}:{tag.split(':')[-1]}:{same_name(label)}"
                    d.candidate(tag, option, person, label, nominator=nominator)
            else:
                position, label, votes = row[3], row[4], number(row[5])
                nominator = row[7] if len(row) > 7 and row[7] else label
                option = d.option(tag, label, "list", nominator=nominator, position=number(position))
            d.value(tag, unit, option, 1, votes)
    missing: list[str] = []
    positions = {}
    for row in piped(name, f"{folder}egy_ert.txt"):
        tag = smd_tag(year, round_number, row[0], row[1])
        position, label, votes, _ = _candidate_cells(year, row[2:])
        missing += _declare_candidates(d, tag, [(label, votes or 0)])
        positions[(tag, number(position))] = d.options.get((tag, same_name(label)))
    for row in piped(name, f"{folder}egy_erf.txt"):
        tag = smd_tag(year, round_number, row[0], row[1])
        nums, rest = _numbers(row[4:])
        verdict = rest[0].lower() if rest else ""
        winner = number(rest[2]) if len(rest) > 2 else None
        option = d.options.get((tag, same_name(" ".join(rest[3:])))) or positions.get((tag, winner))
        if "eredményes" in verdict and "eredménytelen" not in verdict and winner and option:
            d.outcome(tag, option, "elected")
    if round_number == 1:
        for row in piped(name, f"{folder}lis_ert.txt"):
            tag = regional_tag(year, 1, row[0])
            option = d.options.get((tag, same_name(row[2]) or row[2]))
            if option is None:
                missing.append(f"{tag} {row[2]}")
                continue
            d.declare(tag, option, 1, number(row[3]))
    if missing:
        print(f"{d.day}: {len(missing)} declared rows match no option: {missing[:5]}")


NEW_PARLIAMENT = {
    2014: ("2014_parlamenti", date(2014, 4, 6), "egyéni-szavazás-jkv.csv", "listás-szavazás-jkv.csv"),
    2018: ("2018_parlamenti", date(2018, 4, 8), "egyéni_szavazás_szkjkv.csv", "listás_szavazás_szkjkv_teljes_nemz.csv"),
}


def _protocols(rows: list[dict[str, str]], id_column: str) -> Iterator[tuple[dict[str, str], list[dict[str, str]]]]:
    """Group a protocol file's head rows (F, or a row naming the protocol type) with the item rows that follow."""
    head: dict[str, str] | None = None
    items: list[dict[str, str]] = []
    kinds = [
        column for column in (rows[0] if rows else {}) if column.upper() in ("LEVÉLBEN", "JKV_TÍPUS", "JKV_MEGNEV")
    ]
    for row in rows:
        tag = row[id_column]
        if tag.endswith("F") or row.get("TIPUS") == "F" or any(row[column] for column in kinds):
            if head is not None:
                yield head, items
            head, items = row, []
        else:
            items.append(row)
    if head is not None:
        yield head, items


def namesake(label: str, nominator: str) -> str:
    """A candidate's key within a contest where namesakes ran: the name and the nominating organization."""
    return f"{same_name(label)}|{party_label(nominator).upper()}"


def new_parliament(d: Day, year: int) -> None:
    name, _, smd_file, list_file = NEW_PARLIAMENT[year]
    d.cite(ARCHIVE)
    election, path = _parliament_elections(d, year, 1)
    found = places()
    list_tag = national_list_contest(d, year, election, path)
    stations: dict[tuple[str, str, int], str] = {}
    for file, kind in ((smd_file, "egy"), (list_file, "lis")):
        for head, items in _protocols(delimited(name, file), "SZAVAZÓKÖR_AZON"):
            maz, taz, telep = head["MEGYEKÓD"], head["TELEPÜLÉS_SORSZÁM"], head["TELEPÜLÉS"]
            area = found.resolve(maz, taz, telep, trust_code=True)
            unit = d.unit(area, head["SZAVAZÓKÖR"])
            if kind == "lis" and head["SZAVAZÓKÖR"].isdigit():
                stations[(maz.zfill(2), taz.zfill(3), int(head["SZAVAZÓKÖR"]))] = unit
            tag = smd_contest(d, year, 1, election, path, maz, head["OEVK"]) if kind == "egy" else list_tag
            d.accounting(
                tag,
                unit,
                number(head["VÁLASZTÓPOLGÁR"]),
                number(head["MEGJELENTEK"]),
                number(head["URNÁBAN_LEVŐ"]),
                number(head["ÉRVÉNYTELEN"]),
                number(head["ÉRVÉNYES"]),
            )
            for item in items:
                if kind == "egy":
                    label, nominator = item["JELÖLT"], item["SZERVEZET"]
                    ident = namesake(label, nominator)
                    option = d.option(tag, label, "candidate", nominator=nominator, ident=ident)
                    if d.first(tag, ident):
                        person = f"ogy:{year}:{tag.split(':')[-1]}:{ident}"
                        d.candidate(tag, option, person, label, nominator=nominator)
                else:
                    label = item["LISTA"]
                    option = d.option(tag, label, "list", nominator=label)
                d.value(tag, unit, option, 1, number(item["SZAVAZAT"]))
    postal = [file for file in bundle(name) if "levélben" in file and file.endswith(".csv")]
    for head, items in _protocols(delimited(name, postal[0].split("/")[-1]), "JKV_AZONOSÍTÓ"):
        unit = d.special_unit("levelben", "postal_district")
        d.accounting(
            list_tag,
            unit,
            number(head["VÁLASZTÓPOLGÁR"]),
            number(head["ÉRVÉNYES_SZAVAZÁSI_IRATOK"]),
            number(head["IRATOKBAN_LEVŐ_SZAVAZÓLAP"]),
            number(head["ÉRVÉNYTELEN"]),
            number(head["ÉRVÉNYES"]),
        )
        for item in items:
            option = d.option(list_tag, item["LISTA"], "list", nominator=item["LISTA"])
            d.value(list_tag, unit, option, 1, number(item["SZAVAZAT"]))
    if year == 2014:
        for page in PV14_PAGES:
            d.cite(PV14 + page)
        for label in PV14_MINORITIES.values():
            d.option(list_tag, label, "list", nominator=label)
        labels = {same_name(label): option for (tag, label), option in d.options.items() if tag == list_tag}
        for cells in table_rows(PV14 + "orszlist.html"):
            if len(cells) >= 7 and number(cells[5].replace(" ", "")) is not None:
                option = labels.get(same_name(cells[0]))
                if option is not None:
                    d.declare(list_tag, option, 1, number(cells[5].replace(" ", "")))
        chunks = [row[i : i + 6] for row in table_rows(PV14 + "oevker.html") for i in range(0, len(row), 6)]
        for cells in chunks:
            if len(cells) >= 5 and re.fullmatch(r"\d\d", cells[1]):
                tag = smd_tag(year, 1, county_key(cells[0]), cells[1])
                option = d.options.get((tag, namesake(cells[3], cells[4])))
                if option is None:
                    print(f"{d.day}: {tag} first-placed {cells[3]} ({cells[4]}) matches no candidate")
                    continue
                d.outcome(tag, option, "elected")
        d.cite(PV14_STATIONS)
        unmatched = 0
        for station, votes in minority_votes().items():
            unit = stations.get(station)
            if unit is None:
                unmatched += 1
                continue
            for nationality, label in PV14_MINORITIES.items():
                if nationality in votes:
                    d.value(list_tag, unit, d.options[(list_tag, same_name(label) or label)], 1, votes[nationality])
        if unmatched:
            print(f"{d.day}: {unmatched} polling-station pages match no unit of the list file")
    if year == 2018:
        for head, items in _protocols(delimited(name, "egyéni_szavazás_erjkv.csv"), "TIPUS"):
            tag = smd_tag(year, 1, head["MEGYEKÓD"], head["OEVK"])
            for item in items:
                option = d.options.get((tag, namesake(item["JELÖLT"], item["SZERVEZET"])))
                if option is None:
                    print(f"{d.day}: {tag} {item['JELÖLT']} is in the district protocol only")
                    continue
                d.declare(tag, option, 1, number(item["SZAVAZAT"]))
                if item.get("NYERTES", "").lower().startswith("nyertes"):
                    d.outcome(tag, option, "elected")
        rows = delimited(name, "orsz_listás_erjkv1.csv")
        for row in rows:
            label = row.get("PÁRT_LISTA") or row.get("NEMZ_LISTA")
            if not label:
                continue
            option = d.options.get((list_tag, same_name(label) or label))
            if option is None:
                print(f"{d.day}: national list {label} is in the national protocol only")
                continue
            votes = number(row.get("PÁRT_LISTA_SZAVAZAT") or row.get("NEMZ_LISTA_SZAVAZAT"))
            d.declare(list_tag, option, 1, votes)
            seats = number(row.get("NYERT_MANDÁTUM") or row.get("KEDV_MANDÁTUM"))
            if seats:
                d.outcome(list_tag, option, "won", seats)


VTR_PARLIAMENT = {2022: ("ogy2022", date(2022, 4, 3)), 2026: ("ogy2026", date(2026, 4, 12))}


def _items(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value or [])


def _int(value: object) -> int | None:
    return None if value is None else int(cast(int, value))


def vtr_parliament(d: Day, year: int) -> None:
    election_name, _ = VTR_PARLIAMENT[year]
    for name in VTR_CATALOGS[election_name][0]:
        d.cite(vtr_url(election_name, "ver", name))
    for name in VTR_CATALOGS[election_name][1]:
        d.cite(vtr_url(election_name, "szavossz", name))
    d.cite(_crawl_key(election_name))
    election, path = _parliament_elections(d, year, 1)
    found = places()
    period = oevk_period(d.day)
    for row in _items(vtr_json(election_name, "ver", "OevkAdatok.json")["list"]):
        oevk(period, str(row["maz"]), str(row["evk"]), str(row["evk_nev"]))
    organizations = {
        int(cast(int, row["szkod"])): str(row["nev"])
        for row in _items(vtr_json(election_name, "ver", "Szervezetek.json")["list"])
    }
    candidates = {
        int(cast(int, row["ej_id"])): row
        for row in _items(vtr_json(election_name, "ver", "EgyeniJeloltek.json")["list"])
    }
    lists = {
        int(cast(int, row["tl_id"])): row
        for row in _items(vtr_json(election_name, "ver", "ListakEsJeloltek.json")["list"])
    }
    list_tag = national_list_contest(d, year, election, path)

    def nominator(row: dict[str, object]) -> str:
        codes = cast(list[int], row.get("jelolo_szervezetek") or [])
        if codes == [0] or not codes:
            return "Független jelölt"
        return str(row.get("jlcs_nev") or ", ".join(organizations.get(code, str(code)) for code in codes))

    def candidate_option(ej_id: int) -> tuple[str, str]:
        row = candidates[ej_id]
        tag = smd_contest(d, year, 1, election, path, str(row["maz"]), str(row["evk"]))
        label = str(row["neve"]) if not row.get("dr_jelzo") else f"{row['dr_jelzo']} {row['neve']}"
        party_name = nominator(row)
        option = d.option(
            tag, label, "candidate", nominator=party_name, position=_int(row.get("szavlap_sorsz")), ident=f"ej{ej_id}"
        )
        if d.first(tag, f"ej{ej_id}"):
            d.candidate(tag, option, f"kpn:{row.get('kpn_id') or ej_id}", label, nominator=party_name)
        return tag, option

    def list_option(tl_id: int) -> str:
        row = lists[tl_id]
        label = str(row["jlcs_nev"])
        option = d.option(
            list_tag, label, "list", nominator=label, position=_int(row.get("szavlap_sorsz")), ident=f"tl{tl_id}"
        )
        if d.first(list_tag, f"tl{tl_id}"):
            for member_row in _items(row.get("jeloltek")):
                printed = str(member_row["neve"])
                if member_row.get("dr_jelzo"):
                    printed = f"{member_row['dr_jelzo']} {printed}"
                d.candidate(
                    list_tag,
                    option,
                    f"kpn:{member_row.get('kpn_id') or member_row['tj_id']}",
                    printed,
                    role="list_member",
                    position=_int(member_row.get("sorsz")),
                )
        return option

    for code, payload in sorted(vtr_precincts(election_name).items()):
        for row in _items(payload.get("list")):
            maz, taz = str(row["maz"]), str(row["taz"])
            if maz == ABROAD:
                area, kind = None, "abroad_mission"
            else:
                area, kind = found.resolve(maz, taz, found.names.get(maz + taz, ""), trust_code=True), "standard"
            unit = (
                d.unit(area, str(row["sorsz"]), kind)
                if area
                else d.special_unit(f"kulkepviselet:{taz}:{row['sorsz']}", kind)
            )
            smd = cast(dict[str, object] | None, row.get("egyeni_jkv"))
            if smd and _items(smd.get("tetelek")):
                tags = set()
                for item in _items(smd["tetelek"]):
                    tag, option = candidate_option(int(cast(int, item["ej_id"])))
                    tags.add(tag)
                    d.value(tag, unit, option, 1, _int(item.get("szavazat")))
                (tag,) = tags
                d.accounting(
                    tag,
                    unit,
                    _int(smd.get("vp_osszes")),
                    _int(smd.get("szavazott_osszesen")),
                    _int(smd.get("szl_belyegzett_urna")),
                    _int(smd.get("szl_ervenytelen")),
                    _int(smd.get("szl_ervenyes")),
                )
            listed = cast(dict[str, object] | None, row.get("listas_jkv"))
            if listed and _items(listed.get("tetelek")):
                for item in _items(listed["tetelek"]):
                    d.value(list_tag, unit, list_option(int(cast(int, item["tl_id"]))), 1, _int(item.get("szavazat")))
                d.accounting(
                    list_tag,
                    unit,
                    _int(listed.get("vp_osszes")),
                    _int(listed.get("szavazott_osszesen")),
                    _int(listed.get("szl_belyegzett_urna")),
                    _int(listed.get("szl_ervenytelen")),
                    _int(listed.get("szl_ervenyes")),
                )
    for row in _items(vtr_json(election_name, "szavossz", "LevelJkv.json")["list"]):
        unit = d.special_unit("levelben", "postal_district")
        for item in _items(row.get("tetelek")):
            d.value(list_tag, unit, list_option(int(cast(int, item["tl_id"]))), 1, _int(item.get("szavazat")))
        d.accounting(
            list_tag,
            unit,
            _int(row.get("vp_level")),
            _int(row.get("beerkezett_ervenyes_irat")),
            _int(row.get("szl_belyegzett_urna")),
            _int(row.get("szl_ervenytelen")),
            _int(row.get("szl_ervenyes")),
        )
    for row in _items(vtr_json(election_name, "szavossz", "OevkJkv.json")["list"]):
        protocol = cast(dict[str, object], row["egyeni_jkv"])
        for item in _items(protocol.get("tetelek")):
            tag, option = candidate_option(int(cast(int, item["ej_id"])))
            d.declare(tag, option, 1, _int(item.get("szavazat")))
            if item.get("mandatum") == 1:
                d.outcome(tag, option, "elected")
        d.declare(tag, None, 4, _int(protocol.get("szl_ervenyes")))
    for row in _items(vtr_json(election_name, "szavossz", "ListasJkv.json")["list"]):
        if str(row.get("oszint")) != "5":
            continue
        for item in _items(row.get("tetelek")):
            option = list_option(int(cast(int, item["tl_id"])))
            d.declare(list_tag, option, 1, _int(item.get("osszes_szavazat")))
            seats = _int(item.get("mandatum"))
            if seats:
                d.outcome(list_tag, option, "won", seats)


# ---------------------------------------------------------------------------------------------------------------
# Registry and geography


def registry() -> Package:
    _complete("registry")
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Hungary's National Assembly, its electoral systems, and the parties and lists of the National Election "
        "Office's results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {HUNGARY}
    p.add(
        "source",
        id=NVI,
        kind="official_body",
        publisher="Nemzeti Választási Iroda",
        title="Választási eredmények (valasztas.hu)",
        url=ARCHIVE_PAGE,
        license="Public information under Act CXII of 2011; reuse with the source acknowledged",
        license_url="https://www.valasztas.hu/impresszum",
        attribution="Nemzeti Választási Iroda, valasztas.hu",
    )
    stored: set[str] = set()
    for url in sorted(cited()):
        artifact = raw.cached(SOURCE, url)
        if artifact.storage_key not in stored:
            stored.add(artifact.storage_key)
            p.artifact(artifact, NVI)
    p.add("body", id=PARLIAMENT, polity_id=HUNGARY, kind="unicameral")
    p.name(PARLIAMENT, "National Assembly", "en", "official")
    p.name(PARLIAMENT, "Országgyűlés", "hu", "native", False)
    for id, since, until, rules, text in SYSTEMS:
        p.add(
            "electoral_system", id=id, polity_id=HUNGARY, valid_from=since, valid_to=until, rules=rules, rules_version=1
        )
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    found = places()
    for id, (table, kind, code) in sorted(LOCAL_BODIES.items()):
        name = found.names.get(code) or found.historic.get(code, ("", code))[1]
        name = person_text(name) if name.isupper() else name
        if table == "office":
            p.add("office", id=id, polity_id=HUNGARY, kind="mayor")
            english = "Lord Mayor of Budapest" if code == "01" else f"Mayor of {name}"
            native = "Budapest főpolgármestere" if code == "01" else f"{name} polgármestere"
        elif kind.startswith("nationality-"):
            place, nation = code.split("|")
            p.add("body", id=id, polity_id=HUNGARY, kind="council")
            level = kind.removeprefix("nationality-")
            if level == "national":
                english, native = f"National {nation} self-government", f"Országos {nation.lower()} önkormányzat"
            elif level == "territorial":
                english = f"{nation} self-government of {COUNTIES[place]}"
                native = f"{COUNTIES[place]} területi {nation.lower()} önkormányzat"
            else:
                town = found.names.get(place) or found.historic.get(place, ("", place))[1]
                town = person_text(town) if town.isupper() else town
                english, native = f"{nation} self-government of {town}", f"{town} {nation.lower()} önkormányzat"
        elif kind == "county-assembly":
            p.add("body", id=id, polity_id=HUNGARY, kind="council")
            english = "Budapest Assembly" if code == "01" else f"{COUNTIES[code]} County Assembly"
            native = "Fővárosi Közgyűlés" if code == "01" else f"{COUNTIES[code]} Vármegyei Közgyűlés"
        else:
            p.add("body", id=id, polity_id=HUNGARY, kind="council")
            english, native = f"{name} council", f"{name} képviselő-testülete"
        p.name(id, english, "en", "official")
        p.name(id, native, "hu", "native", False)
    for folded, label in sorted(PARTIES.items()):
        id = key("party", folded)
        p.add("party", id=id, kind="bloc" if re.search(r",|-KDNP|\bÉS\b", label) else "party", polity_id=HUNGARY)
        p.add("external_id", entity_id=id, scheme="hu.valasztas.party", value=folded)
        p.name(id, label, "hu", "official")
    return p


def geo() -> Package:
    _complete("geo")
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Hungary's counties, settlements, and parliamentary districts as the National Election Office names them",
    )
    p.external |= {HUNGARY, TERRITORY}
    for code, rank, kind, noun in (
        ("hu.megye", 1, "administrative", "county"),
        ("hu.oevk", 2, "electoral_district", "parliamentary constituency"),
        ("hu.telepules", 3, "administrative", "settlement"),
        ("hu.szavazokor", 4, "precinct", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=HUNGARY, kind=kind, rank=rank, noun=noun, code_system=None)
    for code, name in COUNTIES.items():
        id = county(code)
        p.add("area", id=id, scheme_code="hu.megye")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, "Budapest" if code == "01" else f"{name} vármegye", "hu", "official")
    found = places()
    for code, name in sorted({**found.names, **{c: n for c, (_, n) in found.historic.items()}}.items()):
        id = settlement(code)
        p.add("area", id=id, scheme_code="hu.telepules")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=county(code[:2]))
        p.name(id, person_text(name) if name.isupper() else name, "hu", "official")
    for (period, maz, number_), name in sorted(OEVK_NAMES.items()):
        id = district(period, maz, number_)
        p.add("area", id=id, scheme_code="hu.oevk")
        p.add("area_code", area_id=id, code=f"{period}:{maz}-{number_}")
        p.add("area_parent", area_id=id, parent_id=county(maz))
        p.name(id, name, "hu", "official")
    return p


Builder = Callable[[Day], None]
DAYS: dict[date, list[tuple[str, Builder]]] = defaultdict(list)
DEPENDS: dict[date, list[str]] = {}
BUILT: set[date] = set()
OTHER: list[tuple[date, str, Builder]] = []


def _register() -> None:
    if DAYS:
        return
    for year, (_, first, second, _, _) in OLD_PARLIAMENT.items():
        for round_number, day in ((1, first), (2, second)):
            text = f"{year} parliamentary election, {ordinal(round_number)} round"
            DAYS[day].append((text, functools.partial(old_parliament, year=year, round_number=round_number)))
        DEPENDS[second] = [f"{SOURCE}/{first}"]
    for year, (_, first, second, _, _) in MID_PARLIAMENT.items():
        for round_number, day in ((1, first), (2, second)):
            text = f"{year} parliamentary election, {ordinal(round_number)} round"
            DAYS[day].append((text, functools.partial(mid_parliament, year=year, round_number=round_number)))
        DEPENDS[second] = [f"{SOURCE}/{first}"]
    for year, (_, day, _, _) in NEW_PARLIAMENT.items():
        DAYS[day].append((f"{year} parliamentary election", functools.partial(new_parliament, year=year)))
    for year, (_, day) in VTR_PARLIAMENT.items():
        DAYS[day].append((f"{year} parliamentary election", functools.partial(vtr_parliament, year=year)))
    for day, text, builder in OTHER:
        DAYS[day].append((text, builder))


def build(day: date) -> Day:
    texts = [text for text, _ in DAYS[day]]
    d = Day(
        day,
        f"Hungary's {' and '.join(texts)} by polling station, from the National Election Office",
        depends=DEPENDS.get(day, []),
    )
    for _, builder in DAYS[day]:
        builder(d)
    BUILT.add(day)
    return d


def _complete(name: str) -> None:
    _register()
    if missing := sorted(set(DAYS) - BUILT):
        raise RuntimeError(
            f"{SOURCE}/{name} gathers the settlements, parties, and bodies of every day, but {len(missing)} days "
            f"(from {missing[0]}) were not built in this process; only a full parse builds it"
        )


def packages() -> list[str]:
    _register()
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in sorted(DAYS))]


def parse(only: str | None = None) -> None:
    _register()
    for day in sorted(DAYS):
        if only and only not in str(day):
            continue
        build(day).write()
        bundle.cache_clear()
    if only:
        print(f"{SOURCE}/geo and {SOURCE}/registry are left as they were, since only a full parse builds them")
        return
    geo().write()
    registry().write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------------------------------------------------------
# National referendums

REFERENDUMS: dict[date, tuple[str, str]] = {
    date(1997, 11, 16): ("1997_nepszavazas", "optional_referendum"),
    date(2003, 4, 12): ("2003_nepszavazas", "optional_referendum"),
    date(2004, 12, 5): ("2004_nepszavazas", "initiative"),
    date(2008, 3, 9): ("2008_nepszavazas", "initiative"),
    date(2016, 10, 2): ("2016_nepszavazas", "optional_referendum"),
    date(2022, 4, 3): ("2022_nepszavazas", "optional_referendum"),
}
REFERENDUM_METHOD = (
    "Counted per polling station (szavazókör) and published by the National Election Office. Registered voters are "
    "the electoral roll, voters are those who signed the roll, ballots cast are the stamped ballots in the box, and "
    "valid and invalid are the ballots valid or invalid on this question.{extra}"
)


class Referendum:
    def __init__(self, d: Day, kind: str) -> None:
        self.d = d
        self.kind = kind
        path = f"hu/{d.day}-referendum"
        self.path = path
        self.election = d.election(
            f"nep:{d.day}",
            "referendum",
            f"{d.day.year} Hungarian referendum",
            f"{d.day.year}. évi országos népszavazás",
            path,
        )
        self.tags: dict[int, str] = {}

    def question(self, number_: int, text_: str, extra: str = "") -> str:
        tag = self.tags.get(number_)
        if tag is not None:
            return tag
        tag = self.tags[number_] = f"nep:{self.d.day}:{number_}"
        self.d.contest(
            tag,
            self.election,
            "ballot_question",
            re.sub(r"\s+", " ", text_).strip().strip('"„”'),
            f"{self.path}/{number_}",
            magnitude=None,
            method=REFERENDUM_METHOD.format(extra=f" {extra}" if extra else ""),
            english=f"Question {number_}",
        )
        self.d.p.add("ballot_question", contest_id=key("contest", tag), type=self.kind)
        self.d.option(tag, "Igen", "answer", position=1)
        self.d.option(tag, "Nem", "answer", position=2)
        return tag

    def answers(self, tag: str, unit: str, yes: int | None, no: int | None) -> None:
        self.d.value(tag, unit, self.d.options[(tag, same_name("Igen"))], 1, yes)
        self.d.value(tag, unit, self.d.options[(tag, same_name("Nem"))], 1, no)

    def declare(self, tag: str, yes: int | None, no: int | None) -> None:
        self.d.declare(tag, self.d.options[(tag, same_name("Igen"))], 1, yes)
        self.d.declare(tag, self.d.options[(tag, same_name("Nem"))], 1, no)


def referendum(d: Day) -> None:
    name, kind = REFERENDUMS[d.day]
    if d.day.year != 2022:
        d.cite(ARCHIVE)
    r = Referendum(d, kind)
    found = places()
    year = d.day.year
    if year == 1997:
        heads = {}
        for row in piped(name, "szkf.txt"):
            maz = county_key(row[3])
            nums, _ = _numbers(row[5:])
            heads[tuple(row[:3])] = (d.unit(found.resolve(maz, None, row[4], trust_code=False), row[2]), nums)
        for row in piped(name, "szkt.txt"):
            unit, nums = heads[tuple(row[:3])]
            tag = r.question(int(row[3]), row[4])
            invalid, valid, yes, no = (number(cell) for cell in row[5:9])
            d.accounting(tag, unit, nums[3], nums[4], nums[5], invalid, valid)
            r.answers(tag, unit, yes, no)
        for row in piped(name, "orszt.txt"):
            tag = r.question(int(row[1]), row[2])
            r.declare(tag, number(row[5]), number(row[6]))
    elif year == 2003:
        (national,) = piped(name, "orszageredm.txt")
        tag = r.question(1, national[12])
        for row in piped(name, "szaveredm.txt"):
            nums, _ = _numbers(row[7:])
            unit = d.unit(found.resolve(row[0], row[4], row[5], trust_code=True), row[6])
            d.accounting(tag, unit, nums[3], nums[5], nums[6], nums[8], nums[9])
            r.answers(tag, unit, nums[10], nums[11])
        r.declare(tag, number(national[10]), number(national[11]))
    elif year == 2004:
        questions = {int(row[0]): row[1] for row in piped(name, "orszkerd.txt")}
        heads = {}
        for row in piped(name, "szaveredm.txt"):
            nums, _ = _numbers(row[7:])
            heads[(row[0], row[4], row[6])] = (
                d.unit(found.resolve(row[0], row[4], row[5], trust_code=True), row[6]),
                nums,
            )
        for row in piped(name, "szavkerd.txt"):
            unit, nums = heads[(row[0], row[1], row[2])]
            tag = r.question(int(row[3]), questions[int(row[3])])
            invalid, valid, yes, no = (number(cell) for cell in row[5:9])
            d.accounting(tag, unit, nums[3], nums[5], nums[6], invalid, valid)
            r.answers(tag, unit, yes, no)
        (abroad,) = piped(name, "kuleredm.txt")
        nums, _ = _numbers(abroad)
        unit = d.special_unit("kulkepviselet", "abroad_mission")
        for row in piped(name, "kulkerd.txt"):
            tag = r.question(int(row[0]), questions[int(row[0])])
            invalid, valid, yes, no = (number(cell) for cell in row[2:6])
            d.accounting(tag, unit, nums[0], nums[2], nums[3], invalid, valid)
            r.answers(tag, unit, yes, no)
        for row in piped(name, "orszkerd.txt"):
            tag = r.question(int(row[0]), row[1])
            r.declare(tag, number(row[4]), number(row[5]))
    elif year == 2008:
        book = sheets(name, ".xls")
        totals: Counter[tuple[int, str]] = Counter()
        for sheet, rows in book.items():
            if sheet.startswith("Param"):
                continue
            abroad = sheet.upper().startswith("KÜLK")
            maz = None if abroad else county_key(sheet)
            texts = {}
            for row in rows[:4]:
                for index, cell in enumerate(row):
                    found_q = re.match(r"(\d)\. kérdés: (.*)", cell)
                    if found_q:
                        texts[index] = (int(found_q[1]), found_q[2])
            starts = sorted(texts)
            for row in rows[3:]:
                place_name = row[0]
                if place_name.lower().startswith("megye összesen"):
                    for start in starts:
                        totals[(texts[start][0], "Igen")] += number(row[start + 11]) or 0
                        totals[(texts[start][0], "Nem")] += number(row[start + 12]) or 0
                    continue
                if not place_name or number(row[1]) is None or place_name.lower().startswith(("összes", "megye")):
                    continue
                if abroad:
                    unit = d.special_unit(f"kulkepviselet:{changeset.slug(place_name)}", "abroad_mission")
                else:
                    unit = d.settlement_unit(found.resolve(cast(str, maz), None, place_name, trust_code=False))
                for start in starts:
                    question, text_ = texts[start]
                    tag = r.question(question, text_, "This referendum is published per settlement only.")
                    cells = [number(cell) for cell in row[start + 2 : start + 13]]
                    d.accounting(tag, unit, cells[2], cells[4], cells[5], cells[7], cells[8])
                    r.answers(tag, unit, cells[9], cells[10])
        for question, tag in r.tags.items():
            r.declare(tag, totals[(question, "Igen")], totals[(question, "Nem")])
    elif year == 2016:
        book = sheets(name, ".xls")
        domestic = book["Belf. szavkörök jkv."]
        header = domestic[0]
        text_ = book["Kontrol adatok"][1][1]
        tag = r.question(1, text_)
        for row in domestic[1:]:
            cells = dict(zip(header, row, strict=False))
            if not cells.get("Megye kód"):
                continue
            unit = d.unit(found.resolve(row[0], row[2], row[3], trust_code=True), row[4])
            d.accounting(
                tag,
                unit,
                number(cells["Névjegyzéki létszám (A)"]),
                number(cells["Megjelent vál.polg. (E)"]),
                number(cells["Szavazólap bélyegzett (J)"]),
                number(cells["Érvénytelen szavazólap (K)"]),
                number(cells["Érvényes szavazólap (L)"]),
            )
            r.answers(tag, unit, number(cells["Igen szavazat (I)"]), number(cells["Nem szavazat (N)"]))
        for sheet, kind, unit_name in (
            ("Külképviseleti jkv.", "abroad_mission", "kulkepviselet"),
            ("Levélben szavazás jkv.", "postal_district", "levelben"),
        ):
            rows = book[sheet]
            header = rows[0]
            for index, row in enumerate(rows[1:]):
                cells = dict(zip(header, row, strict=False))
                if not any(cells.values()) or number(cells.get("Igen szavazat (I)")) is None:
                    continue
                label = next((value for column, value in cells.items() if "megnev" in column.lower()), str(index))
                unit = d.special_unit(f"{unit_name}:{changeset.slug(label) or index}", kind)
                figures = {column.split("(")[-1].rstrip(")"): number(value) for column, value in cells.items()}
                registered = next((number(v) for c, v in cells.items() if "létszám" in c.lower()), None)
                voters = next((number(v) for c, v in cells.items() if "megjelent" in c.lower()), None)
                d.accounting(
                    tag,
                    unit,
                    registered,
                    voters,
                    figures.get("J"),
                    figures.get("K"),
                    figures.get("L"),
                )
                r.answers(tag, unit, figures.get("I"), figures.get("N"))
        d.reasons["accounting.declared"] = (
            "the national totals include the ballots cast at foreign missions, for which the office's file prints the "
            "envelopes but no votes"
        )
        national = dict(zip(book["Országos eredmény"][0], book["Országos eredmény"][1], strict=False))
        r.declare(tag, number(national["Igen szavazat (I)"]), number(national["Nem szavazat (N)"]))
    elif year == 2022:
        archive = "2022_nepszavazas"
        d.cite(ARCHIVE)
        rows = [
            dict(zip(raw_rows[0], row, strict=False))
            for raw_rows in [next(iter(sheets(archive, "szavazóköri_jkv_20220502.xlsx").values()))]
            for row in raw_rows[1:]
        ]
        for head, items in _protocols(rows, "SZAVAZÓKÖR_AZON"):
            unit = d.unit(
                found.resolve(head["MEGYEKÓD"], head["TELEPÜLÉS_SORSZÁM"], head["TELEPÜLÉS"], True), head["SZAVAZÓKÖR"]
            )
            for item in items:
                tag = r.question(int(cast(int, number(item["KÉRDÉS_SORSZÁM"]))), item["KÉRDÉS_SZÖVEGE"])
                invalid = number(next(v for c, v in item.items() if c.startswith("ÉRVÉNYES_SZAVAZATOT_NEM")))
                d.accounting(
                    tag,
                    unit,
                    number(head["VÁLASZTÓPOLGÁR"]),
                    number(head["MEGJELENTEK"]),
                    number(head["URNÁBAN_LEVŐ"]),
                    invalid,
                    number(item["ÉRVÉNYES_SZAVAZATOK"]),
                )
                r.answers(tag, unit, number(item["IGEN_SZAVAZAT"]), number(item["NEM_SZAVAZAT"]))
        for suffix, kind, unit_name in (
            ("levélben_leadott_szavazatok_számlálási_jkv_20220502.xlsx", "postal_district", "levelben"),
        ):
            sheet_rows = next(iter(sheets(archive, suffix).values()))
            table = [dict(zip(sheet_rows[0], row, strict=False)) for row in sheet_rows[1:]]
            unit = d.special_unit(unit_name, kind)
            head = table[0]
            for item in table:
                question = number(item.get("KÉRDÉS_SORSZÁM"))
                if question is None:
                    head = item
                    continue
                tag = r.question(question, item.get("KÉRDÉS_SZÖVEGE", ""))
                invalid = number(next((v for c, v in item.items() if c.startswith("ÉRVÉNYES_SZAVAZATOT_NEM")), None))
                d.accounting(
                    tag,
                    unit,
                    number(next((v for c, v in head.items() if c.startswith("VÁLASZTÓPOLGÁR")), None)),
                    number(next((v for c, v in head.items() if "IRAT" in c and "ÉRVÉNYES" in c), None)),
                    number(next((v for c, v in head.items() if "SZAVAZÓLAP" in c and "LEVŐ" in c), None)),
                    invalid,
                    number(item.get("ÉRVÉNYES_SZAVAZATOK")),
                )
                r.answers(tag, unit, number(item.get("IGEN_SZAVAZAT")), number(item.get("NEM_SZAVAZAT")))
        sheet_rows = next(iter(sheets(archive, "eredmény_jkv_20220502.xlsx").values()))
        for row in sheet_rows[1:]:
            item = dict(zip(sheet_rows[0], row, strict=False))
            question = number(item.get("KÉRDÉS_SORSZÁM"))
            if question is not None and question in r.tags:
                r.declare(r.tags[question], number(item.get("IGEN_SZAVAZAT")), number(item.get("NEM_SZAVAZAT")))


for _day in REFERENDUMS:
    OTHER.append((_day, f"{_day.year} referendum", referendum))


# ---------------------------------------------------------------------------------------------------------------
# European Parliament

EP_DAYS = {
    date(2004, 6, 13): "2004_EP",
    date(2009, 6, 7): "2009_EP",
    date(2014, 5, 25): "2014_EP",
    date(2019, 5, 26): "2019_EP",
    date(2024, 6, 9): "ep2024",
}
EP_SEATS = {2004: 24, 2009: 22, 2014: 21, 2019: 21, 2024: 21}


EP_EXISTING = {
    2004: ("e7c661fe-79d4-5152-b5fc-5a043125f958", "b166b65a-228a-5e1b-8be2-19f3dd531d31"),
    2009: ("afdd3d58-4062-54b1-b0f1-85f742212c07", "bf2a5330-414d-599f-8eb8-901f4e649df3"),
    2014: ("63ecb31f-804d-5a2c-ad88-f15e237533b4", "6d916294-c6de-5cfe-927f-5105f542d65d"),
    2019: ("b915f044-4fe1-5ae9-a5e7-5a51bdc07df1", "9fe5f9aa-109a-5b5b-b6ed-57ff97d4b16e"),
    2024: ("b76f9384-28f7-59b8-b9f5-a553c5fed1ce", "099838e8-47fc-57b0-9b21-df55d809ec70"),
}


def ep_contest(d: Day) -> str:
    """Stage the polling-station result set on the Hungarian contest the European Parliament pipelines hold."""
    tag = f"ep:{d.day.year}"
    election, contest = EP_EXISTING[d.day.year]
    d.contest_ids[tag] = contest
    d.contest(
        tag,
        election,
        "list_allocation",
        "Európai parlamenti képviselők választása",
        "",
        method=method(
            "The national result of this election is also held from the European Parliament's own figures; this "
            "result set holds the National Election Office's polling-station returns."
        ),
    )
    return tag


def european(d: Day) -> None:
    year = d.day.year
    name = EP_DAYS[d.day]
    tag = ep_contest(d)
    found = places()
    if year in (2004, 2009):
        d.cite(ARCHIVE)
        heads: dict[tuple[str, str, str], str] = {}
        for row in piped(name, "cdszavf.txt"):
            nums, _ = _numbers(row[5:])
            if row[0] == ABROAD:
                unit = d.special_unit(f"kulkepviselet:{int(row[4]):03d}", "abroad_mission")
            else:
                unit = d.unit(found.resolve(row[0], row[2], row[3], trust_code=True), row[4])
            heads[(row[0], row[2], row[4])] = unit
            d.accounting(tag, unit, nums[-7], nums[-5], nums[-4], nums[-2], nums[-1])
        for row in piped(name, "cdszavt.txt"):
            option = d.option(tag, row[6], "list", nominator=row[6], position=number(row[5]), ident=str(number(row[5])))
            d.value(tag, heads[(row[0], row[2], row[4])], option, 1, number(row[7]))
        for row in piped(name, "cdorszt.txt"):
            option = d.option(tag, row[1], "list", nominator=row[1], position=number(row[0]), ident=str(number(row[0])))
            d.declare(tag, option, 1, number(row[2]))
            if number(row[4]):
                d.outcome(tag, option, "won", number(row[4]))
        for row in piped(name, "cdorszj.txt"):
            option = d.options[(tag, str(number(row[0])))]
            candidacy = d.candidate(
                tag,
                option,
                f"ep:{year}:{number(row[0])}:{same_name(row[3])}",
                row[3],
                role="list_member",
                position=number(row[2]),
            )
            if row[4] == "1":
                d.outcome(tag, option, "elected", candidacy=candidacy)
    elif year == 2014:
        d.cite(ARCHIVE)
        for head, items in _protocols(delimited(name, ".csv"), "SZAVAZÓKÖR_AZON"):
            if head["JKV_TÍPUS"].lower().startswith("külk"):
                unit = d.special_unit(f"kulkepviselet:{head['SZAVAZÓKÖR_AZON']}", "abroad_mission")
            else:
                area = found.resolve(head["MEGYEKÓD"], head["TELEPÜLÉS_SORSZÁM"], head["TELEPÜLÉS"], trust_code=True)
                unit = d.unit(area, head["SZAVAZÓKÖR"])
            d.accounting(
                tag,
                unit,
                number(head["VÁLASZTÓPOLGÁR"]),
                number(head["MEGJELENTEK"]),
                number(head["URNÁBAN_LEBÉLYEGZETT"]),
                number(head["ÉRVÉNYTELEN"]),
                number(head["ÉRVÉNYES"]),
            )
            for item in items:
                option = d.option(tag, item["LISTA"], "list", nominator=item["LISTA"])
                d.value(tag, unit, option, 1, number(item["SZAVAZAT"]))
        d.cite(EP14)
        for cells in table_rows(EP14):
            option = d.options.get((tag, same_name(cells[0]))) if len(cells) >= 4 else None
            if option is None:
                continue
            d.declare(tag, option, 1, number(cells[1].replace(" ", "")))
            if number(cells[3]):
                d.outcome(tag, option, "won", number(cells[3]))
    elif year == 2019:
        d.cite(ARCHIVE)
        for sheet, rows in sheets(name, "szavazóköri_eredmény.xlsx").items():
            header = rows[0]
            labels = {index: re.sub(r"^\d+\.\s*", "", cell).strip() for index, cell in enumerate(header) if index >= 11}
            for row in rows[1:]:
                if not row[2] or number(row[4]) is None:
                    continue
                unit = d.unit(found.resolve(county_key(row[1] or sheet), None, row[2], trust_code=False), row[3])
                d.accounting(tag, unit, number(row[4]), number(row[5]), number(row[7]), number(row[9]), number(row[10]))
                for index, label in labels.items():
                    position = number(header[index].split(".")[0])
                    option = d.option(tag, label, "list", nominator=label, position=position, ident=str(position))
                    d.value(tag, unit, option, 1, number(row[index]))
        d.p.add(
            "note",
            id=key("note", d.sets[tag], "domestic"),
            entity_id=d.sets[tag],
            kind="caveat",
            body_md="The office's 2019 polling-station file holds the polling stations in Hungary only; votes cast at "
            "foreign missions and by post are not in it.",
            lang="en",
        )
    else:
        vtr_european(d, tag)


def _single(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        return [cast(dict[str, object], value)]
    return _items(value)


def _payload(document: dict[str, object]) -> object:
    return document.get("list", document.get("data"))


def vtr_european(d: Day, tag: str) -> None:
    election_name = "ep2024"
    for name in VTR_CATALOGS[election_name][0]:
        d.cite(vtr_url(election_name, "ver", name))
    for name in VTR_CATALOGS[election_name][1]:
        d.cite(vtr_url(election_name, "szavossz", name))
    d.cite(_crawl_key(election_name))
    found = places()
    lists = {
        int(cast(int, row["szavlap_sorsz"])): row
        for row in _items(vtr_json(election_name, "ver", "euListakEsJeloltek.json")["list"])
        if row.get("szavlap_sorsz") is not None
    }
    winners = {
        int(cast(int, row["kpn_id"])) for row in _items(vtr_json(election_name, "szavossz", "epGyoztesek.json")["list"])
    }
    options: dict[int, str] = {}
    for position, row in sorted(lists.items()):
        label = str(row["jlcs_nev"])
        option = options[position] = d.option(
            tag, label, "list", nominator=label, position=position, ident=str(position)
        )
        for member in _items(row.get("jeloltek")):
            printed = str(member["neve"]) if not member.get("dr_jelzo") else f"{member['dr_jelzo']} {member['neve']}"
            candidacy = d.candidate(
                tag,
                option,
                f"kpn:{member['kpn_id']}",
                printed,
                role="list_member",
                position=_int(member.get("sorsz")),
            )
            if int(cast(int, member["kpn_id"])) in winners:
                d.outcome(tag, option, "elected", candidacy=candidacy)

    def votes(unit: str, protocol: dict[str, object], registered: object, voters: object) -> None:
        for item in _items(protocol.get("tetelek")):
            d.value(tag, unit, options[int(cast(int, item["szavlap_sorsz"]))], 1, _int(item.get("szavazat")))
        d.accounting(
            tag,
            unit,
            _int(registered),
            _int(voters),
            _int(protocol.get("szl_belyegzett_urna")),
            _int(protocol.get("szl_ervenytelen")),
            _int(protocol.get("szl_ervenyes")),
        )

    for code, payload in sorted(vtr_precincts(election_name).items()):
        data = cast(dict[str, object], payload.get("data") or {})
        area = found.resolve(code[:2], code[2:], found.names.get(code, ""), trust_code=True)
        for protocol in _items(data.get("jegyzokonyvek")):
            unit = d.unit(area, str(protocol["sorsz"]))
            votes(unit, protocol, protocol.get("vp_osszes"), protocol.get("szk_megjelent"))
    for row in _single(_payload(vtr_json(election_name, "szavossz", "KuviJkv.json"))):
        votes(d.special_unit("kulkepviselet", "abroad_mission"), row, row.get("vp_kulkepv"), row.get("kuvi_megjelent"))
    for row in _single(_payload(vtr_json(election_name, "szavossz", "LevelJkv.json"))):
        votes(
            d.special_unit("levelben", "postal_district"), row, row.get("vp_level"), row.get("beerkezett_ervenyes_irat")
        )
    for row in _items(vtr_json(election_name, "szavossz", "ListasJkv.json")["list"]):
        if str(row.get("oszint")) != "5":
            continue
        for item in _items(row.get("tetelek")):
            option = options[int(cast(int, item["szavlap_sorsz"]))]
            d.declare(tag, option, 1, _int(item.get("szavazat")))
            seats = _int(item.get("mandatum"))
            if seats:
                d.outcome(tag, option, "won", seats)


VTR_CATALOGS["onk2024"] = (
    ["Telepulesek.json", "onkJlcs.json", "onkSzervezetek.json", "NemzVal.json", "onkTelepulesiMandatumok.json"],
    [],
)
VTR_CATALOGS["ep2024"] = (
    ["Telepulesek.json", "euListakEsJeloltek.json", "euSzervezetek.json"],
    ["ListasJkv.json", "LevelJkv.json", "KuviJkv.json", "epGyoztesek.json"],
)
for _day in EP_DAYS:
    OTHER.append((_day, f"{_day.year} European Parliament election", european))


# ---------------------------------------------------------------------------------------------------------------
# Local elections

LOCAL_MAYOR = key("electoral_system", "local-mayor", 1990)
LOCAL_BLOCK = key("electoral_system", "local-block", 1990)
LOCAL_SMD = key("electoral_system", "local-smd", 1990)
LOCAL_LIST = key("electoral_system", "local-list", 1990)
LOCAL_COMP = key("electoral_system", "local-compensation", 1990)
SYSTEMS += [
    (
        LOCAL_COMP,
        date(1990, 1, 1),
        None,
        {"formula": "dhondt"},
        (
            "The compensation seats of a council elected partly in single-member wards go to lists built from the votes "
            "that won no ward, the fragment votes, allocated by D'Hondt."
        ),
    ),
    (
        LOCAL_MAYOR,
        date(1990, 1, 1),
        None,
        {"formula": "fptp"},
        (
            "Mayors, and the Lord Mayor of Budapest, are elected directly in a single round; the candidate with the most "
            "votes wins."
        ),
    ),
    (
        LOCAL_BLOCK,
        date(1990, 1, 1),
        None,
        {"formula": "block"},
        (
            "In settlements of up to 10,000 inhabitants the council is elected on one ballot from individual candidates: "
            "each voter may vote for as many candidates as there are seats, and the candidates with the most votes win."
        ),
    ),
    (
        LOCAL_SMD,
        date(1990, 1, 1),
        None,
        {"formula": "fptp"},
        (
            "In settlements of more than 10,000 inhabitants and in Budapest's districts, part of the council is elected in "
            "single-member wards by the most votes; the rest come from compensation lists of the votes that won no seat."
        ),
    ),
    (
        LOCAL_LIST,
        date(1990, 1, 1),
        None,
        {"formula": "dhondt", "threshold": {"level": "list", "scope": "contest", "percent": 5}},
        (
            "County assemblies (from the voters of settlements of up to 10,000 inhabitants) and, from 2024, the Budapest "
            "Assembly are elected from closed lists by D'Hondt among the lists that won 5% of the votes."
        ),
    ),
]

LOCAL_BODIES: dict[str, tuple[str, str, str]] = {}


def council(area_code: str) -> str:
    id = key("body", "council", area_code)
    LOCAL_BODIES[id] = ("body", "council", area_code)
    return id


def mayoralty(area_code: str) -> str:
    id = key("office", "mayor", area_code)
    LOCAL_BODIES[id] = ("office", "mayor", area_code)
    return id


def county_assembly(maz: str) -> str:
    id = key("body", "county-assembly", maz)
    LOCAL_BODIES[id] = ("body", "county-assembly", maz)
    return id


def local_election(d: Day, year: int) -> tuple[str, str]:
    path = f"hu/{d.day}-local"
    election = d.election(
        f"onk:{year}",
        "local",
        f"{year} Hungarian local elections",
        f"{year}. évi helyi önkormányzati választások",
        path,
    )
    return election, path


class Local:
    """Contests of one local election, named after the place they elect for."""

    def __init__(self, d: Day, year: int) -> None:
        self.d = d
        self.year = year
        self.election, self.path = local_election(d, year)

    def place_name(self, area_code: str) -> str:
        found = places()
        name = found.names.get(area_code) or found.historic.get(area_code, ("", area_code))[1]
        return person_text(name) if name.isupper() else name

    def contest(self, kind: str, area_code: str, ward: str | None = None, magnitude: int | None = 1) -> str:
        """kind: mayor, lord-mayor, council (block vote), ward, county-list, budapest-list."""
        tag = f"onk:{self.year}:{kind}:{area_code}" + (f":{ward}" if ward else "")
        if tag in self.d.sets:
            return tag
        name = (
            self.place_name(area_code)
            if kind not in ("county-list", "county-list-large", "lord-mayor", "budapest-list")
            else ""
        )
        maz = area_code[:2]
        if kind == "mayor":
            title, english = f"{name} polgármester", f"Mayor of {name}"
            office, body, area, system = mayoralty(area_code), None, settlement(area_code), LOCAL_MAYOR
            contest_kind = "office"
        elif kind == "lord-mayor":
            title, english = "Budapest főpolgármester", "Lord Mayor of Budapest"
            office, body, area, system = mayoralty("01"), None, county("01"), LOCAL_MAYOR
            contest_kind = "office"
        elif kind == "council":
            title, english = f"{name} képviselő-testület", f"{name} council"
            office, body, area, system = None, council(area_code), settlement(area_code), LOCAL_BLOCK
            contest_kind = "body_seats"
        elif kind == "ward":
            title, english = (
                f"{name} {int(cast(str, ward))}. egyéni választókerület",
                f"{name} ward {int(cast(str, ward))}",
            )
            office, body, area, system = None, council(area_code), settlement(area_code), LOCAL_SMD
            contest_kind = "body_seats"
        elif kind == "county-list-large":
            title = f"{COUNTIES[maz]} megyei közgyűlés, 10 ezer fő feletti települések"
            english = f"{COUNTIES[maz]} County Assembly, towns over 10,000"
            office, body, area, system = None, county_assembly(maz), county(maz), LOCAL_LIST
            contest_kind = "list_allocation"
        elif kind == "county-list":
            title, english = f"{COUNTIES[maz]} vármegyei közgyűlés", f"{COUNTIES[maz]} County Assembly"
            office, body, area, system = None, county_assembly(maz), county(maz), LOCAL_LIST
            contest_kind = "list_allocation"
        elif kind == "compensation":
            title, english = f"{name} kompenzációs lista", f"{name} compensation list"
            office, body, area, system = None, council(area_code), settlement(area_code), LOCAL_COMP
            contest_kind = "list_allocation"
        else:
            title, english = "Fővárosi közgyűlés", "Budapest Assembly"
            office, body, area, system = None, county_assembly("01"), county("01"), LOCAL_LIST
            contest_kind = "list_allocation"
        self.d.contest(
            tag,
            self.election,
            contest_kind,
            title,
            f"{self.path}/{changeset.slug(tag.split(':', 2)[2])}",
            magnitude=magnitude,
            method=method(),
            english=english,
            office=office,
            body=body,
            district_area=area,
            system=system,
        )
        return tag


VTR_LOCAL_KINDS = {
    "1": "mayor",
    "2": "lord-mayor",
    "3": "council",
    "4": "ward",
    "6": "county-list",
    "F": "budapest-list",
}


def vtr_local(d: Day) -> None:
    election_name = "onk2024"
    for name in VTR_CATALOGS[election_name][0]:
        d.cite(vtr_url(election_name, "ver", name))
    d.cite(_crawl_key(election_name))
    local = Local(d, 2024)
    found = places()
    groups = {
        int(cast(int, row["kod"])): str(row["nev"])
        for row in _items(vtr_json(election_name, "ver", "onkJlcs.json")["list"])
    }
    for code, payload in sorted(vtr_precincts(election_name).items()):
        data = cast(dict[str, object], payload["data"])
        area = found.resolve(code[:2], code[2:], found.names.get(code, ""), trust_code=True)
        ballots: dict[tuple[str, str | None], list[dict[str, object]]] = {}
        for ballot in _items(data.get("szavazolapok")):
            kind = str(ballot["valaltip"])
            if "tevk_csoport" in ballot:
                for ward in _items(ballot["tevk_csoport"]):
                    ballots[(kind, str(ward["tevk"]))] = _items(ward.get("jeloltek"))
            else:
                ballots[(kind, None)] = _items(ballot.get("jeloltek"))
        options: dict[tuple[str, str | None, int], str] = {}
        tags: dict[tuple[str, str | None], str] = {}
        for (kind, ward), entries in ballots.items():
            role = VTR_LOCAL_KINDS.get(kind)
            if role is None:
                continue
            elected = sum(1 for entry in entries if entry.get("mandatum") == 1)
            place = {"county-list": code[:2], "lord-mayor": "01", "budapest-list": "01"}.get(role, area)
            tag = tags[(kind, ward)] = local.contest(
                role,
                place,
                ward,
                magnitude=None if role.endswith("list") else max(elected, 1) if role == "council" else 1,
            )
            for entry in entries:
                position = int(cast(int, entry["szavlap_sorsz"]))
                nominator = groups.get(int(cast(int, entry.get("jlcs_id") or 0)), "Független")
                if entry.get("tl_id"):
                    option = d.option(
                        tag, nominator, "list", nominator=nominator, position=position, ident=f"tl{entry['tl_id']}"
                    )
                    seats = _int(entry.get("mandatum"))
                    if seats:
                        d.outcome(tag, option, "won", seats)
                else:
                    printed = str(entry["jelolt_neve"])
                    if entry.get("dr_jelzo"):
                        printed = f"{entry['dr_jelzo']} {printed}"
                    ident = f"ej{entry['ej_id']}"
                    option = d.option(tag, printed, "candidate", nominator=nominator, position=position, ident=ident)
                    if d.first(tag, ident):
                        d.candidate(
                            tag, option, f"kpn:{entry.get('kpn_id') or entry['ej_id']}", printed, nominator=nominator
                        )
                    if entry.get("mandatum") == 1:
                        d.outcome(tag, option, "elected")
                options[(kind, ward, position)] = option
        protocols = [(None, protocol) for protocol in _items(data.get("jegyzokonyvek"))]
        protocols += [
            (str(group["tevk"]), protocol)
            for group in _items(data.get("tevk_csoport"))
            for protocol in _items(group.get("jegyzokonyvek"))
        ]
        for ward, protocol in protocols:
            kind = str(protocol["valaltip"])
            index = (kind, ward if kind == "4" else None)
            if index not in tags:
                continue
            tag = tags[index]
            unit = d.unit(area, str(protocol["sorsz"]))
            for item in _items(protocol.get("tetelek")):
                d.value(
                    tag, unit, options[(*index, int(cast(int, item["szavlap_sorsz"])))], 1, _int(item.get("szavazat"))
                )
            d.accounting(
                tag,
                unit,
                _int(protocol.get("vp_osszes")),
                _int(protocol.get("szk_megjelent")),
                _int(protocol.get("szl_belyegzett_urna")),
                _int(protocol.get("szl_ervenytelen")),
                _int(protocol.get("szl_ervenyes")),
            )


OTHER.append((date(2024, 6, 9), "2024 local elections", vtr_local))


SHEETS_2019 = (
    ("Polgármester jelöltek", "Polgármesterek választása", "mayor"),
    ("Főpolgármester jelöltek", "Főpolgármester választása", "lord-mayor"),
    ("Egyéni listás jelöltek", "Egyéni listás választás", "council"),
    ("TEVK jelöltek", "Egyéni választókerületi", "ward"),
    ("Megyei közgyűlési listák", "Megyei közgyűlés választása", "county-list"),
)
ACCOUNTING_COLUMNS = ("A", "F", "K", "M", "N")


def _sheet(book: dict[str, list[list[str]]], prefix: str) -> list[list[str]] | None:
    found = [rows for name, rows in book.items() if name.startswith(prefix[:28])]
    return found[0] if found else None


def local_workbook(
    d: Day,
    local: Local,
    book: dict[str, list[list[str]]],
    maz: str,
    pairs: Iterable[tuple[str, str, str]],
) -> None:
    """One county's workbook of polling-station results: a names sheet and a results sheet per contest type."""
    found = places()
    for names_prefix, results_prefix, role in pairs:
        names_sheet, results_sheet = _sheet(book, names_prefix), _sheet(book, results_prefix)
        if names_sheet is None and role == "county-list":
            names_sheet = next((rows for rows in book.values() if rows and rows[0][:1] == ["Lista sorszám"]), None)
        if results_sheet is None:
            continue
        labels: dict[tuple[str, str | None, int], tuple[str, str]] = {}
        if names_sheet:
            header = names_sheet[0]
            for row in names_sheet[1:]:
                cells = dict(zip(header, row, strict=False))
                position = number(cells.get("Jelölt sorszáma") or cells.get("Lista sorszám"))
                if position is None:
                    continue
                place_name = fold(cells.get("Település", ""))
                ward = cells.get("TEVK") or None
                label = cells.get("Jelölt neve") or cells.get("Lista neve") or ""
                labels[(place_name, ward, position)] = (label, cells.get("Jelölő szervezet") or label)
        start = next(index for index, row in enumerate(results_sheet) if row and row[0] == "Település")
        header = results_sheet[start]
        columns = {name: index for index, name in enumerate(header)}
        option_columns = [(index, int(name)) for index, name in enumerate(header) if name.isdigit()]
        for row in results_sheet[start + 1 :]:
            if not row[0].strip() or number(row[columns["A"]]) is None:
                continue
            place_name = row[0].strip()
            area = found.resolve(maz, None, place_name, trust_code=False)
            ward = row[columns["TEVK"]] if "TEVK" in columns else None
            place = {"county-list": maz, "lord-mayor": "01"}.get(role, area)
            tag = local.contest(role, place, ward, magnitude=None if role in ("council", "county-list") else 1)
            unit = d.unit(area, row[columns["Sorsz."]])
            registered, voters, cast_, invalid, valid = (number(row[columns[c]]) for c in ACCOUNTING_COLUMNS)
            d.accounting(tag, unit, registered, voters, cast_, invalid, valid)
            for index, position in option_columns:
                votes = number(row[index]) if index < len(row) else None
                key_place = "" if role in ("county-list", "lord-mayor") else fold(place_name)
                entry = labels.get((key_place, ward, position))
                if entry is None:
                    if votes:
                        raise ValueError(f"{d.day} {tag}: column {position} has votes but no name")
                    continue
                label, nominator = entry
                kind = "list" if role.endswith("list") else "candidate"
                option = d.option(tag, label, kind, nominator=nominator, position=position, ident=str(position))
                if kind == "candidate" and d.first(tag, str(position)):
                    d.candidate(tag, option, f"{tag}:{position}", label, nominator=nominator)
                d.value(tag, unit, option, 1, votes)


def compensation(
    d: Day,
    local: Local,
    area: str,
    lists: dict[int, str],
    fragments: dict[int, int | None],
    seats: dict[int, int | None],
) -> None:
    """One settlement's compensation lists: the fragment votes each list held and the seats it won."""
    total = sum(seat or 0 for seat in seats.values())
    tag = local.contest("compensation", area, magnitude=total or None)
    unit = d.settlement_unit(area)
    for position, label in lists.items():
        option = d.option(tag, label, "list", nominator=label, position=position, ident=str(position))
        d.value(tag, unit, option, 1, fragments.get(position))
        if seats.get(position):
            d.outcome(tag, option, "won", seats[position])


def compensation_2019(d: Day, local: Local) -> None:
    found = places()
    for path in sorted(bundle("2019_onkormanyzati")):
        match = re.search(r"kompenzációs listák/(.+)_komp_listák\.xlsx$", path)
        if not match:
            continue
        maz = county_key(match[1].replace("_", "-"))
        book = sheets("2019_onkormanyzati", path)
        names: dict[str, dict[int, str]] = defaultdict(dict)
        table = next(rows for name, rows in book.items() if name.startswith("Param"))
        start = next(i for i, row in enumerate(table) if row[:1] == ["Főváros/település neve"])
        for row in table[start + 1 :]:
            if row[0] and number(row[1]) is not None:
                names[row[0]][cast(int, number(row[1]))] = row[2]
        rows = next(rows for name, rows in book.items() if not name.startswith("Param"))
        head = next(i for i, row in enumerate(rows) if row[:1] == ["Főváros/település neve"])
        for row in rows[head + 2 :]:
            if not row[0].strip():
                continue
            # The workbooks put list n's fragment votes in column 2 + n - 1 and its seats in column 10 + n - 1.
            fragments = {i - 1: number(row[i]) for i in range(2, 10) if i < len(row) and row[i]}
            seats = {i - 9: number(row[i]) for i in range(10, len(row)) if row[i]}
            area = found.resolve(maz, None, row[0].strip(), trust_code=False)
            lists = names[row[0].strip()]
            if not lists or not set(fragments) | set(seats) <= set(lists):
                raise ValueError(f"2019 {row[0]}: figures for lists without names: {fragments} {seats} {lists}")
            compensation(d, local, area, lists, fragments, seats)


def local_2019(d: Day) -> None:
    d.cite(ARCHIVE)
    local = Local(d, 2019)
    compensation_2019(d, local)
    for path in sorted(bundle("2019_onkormanyzati")):
        found = re.search(r"szavazóköri_eredmények_(\d\d)_", path)
        if found:
            local_workbook(d, local, sheets("2019_onkormanyzati", path.split("/")[-1]), found[1], SHEETS_2019)
    winners_2019(d, local)


# ---------------------------------------------------------------------------------------------------------------
# Declared winners of the 2014 and 2019 local elections: mayors, ward members, and block-vote councils

PAGES_2019 = "https://valtor.valasztas.hu/valtort/jsp/"
MAYORS_2019 = PAGES_2019 + "teljkv.jsp?EA=40&MAZ=<county>&TAZ=<settlement>&W=4"
WARDS_2019 = PAGES_2019 + "tevkjkvkiv.jsp?EA=40&MAZ=<county>&TAZ=<settlement>&W=5"
COUNCILS_2019 = PAGES_2019 + "teltag.jsp?EA=40&MAZ=<county>&TAZ=<settlement>"
ASSEMBLIES_2019 = PAGES_2019 + "mkiv.jsp?EA=40&URL=9"
SETTLEMENTS_2014 = "https://static.valasztas.hu/dyn/onk14/szavossz/hu/M<county>/T<settlement>/tjk.html"
ASSEMBLIES_2014 = "https://static.valasztas.hu/dyn/onk14/szavossz/hu/"
PAGE_START = re.compile(r"<!-- (https://\S+) \(([^)]*)\) -->")
PAGE_TIMEOUT = 60


def _page(url: str) -> str | None:
    """One results page decoded by the charset it declares, or None when the office has no such page."""
    for _ in range(5):
        try:
            response = httpx.get(url, headers={"User-Agent": raw.USER_AGENT}, timeout=PAGE_TIMEOUT)
        except httpx.TransportError:
            continue
        if response.status_code == 404:
            return None
        response.raise_for_status()
        declared = re.search(rb"charset=([\w-]+)", response.content[:2000], re.IGNORECASE)
        return response.content.decode(declared[1].decode() if declared else "utf-8", errors="replace")
    raise RuntimeError(f"{url} failed five times")


def _save_pages(key_url: str, found: list[tuple[str, str, str | None]]) -> None:
    """Store fetched pages as one file of saved pages: each page under a line with its address and place name."""
    parts = [f"<!-- {url} ({html.escape(place)}) -->\n{page}\n" for url, place, page in found if page is not None]
    print(f"{key_url}: {len(parts):,} pages, {len(found) - len(parts)} missing")
    raw.store(
        SOURCE,
        key_url,
        "".join(parts).encode("utf-8"),
        suffix=".html",
        retrieved_at=datetime.now(UTC),
        media_type="text/html; charset=utf-8",
    )


def _crawl_pages(targets: list[tuple[str, str]]) -> list[tuple[str, str, str | None]]:
    with ThreadPoolExecutor(8) as pool:
        fetched = list(pool.map(lambda target: _page(target[0]), targets))
    return [(url, place, page) for (url, place), page in zip(targets, fetched, strict=True)]


def _settlements_2014() -> dict[tuple[str, str], str]:
    """The county and settlement numbers of every settlement in the 2014 local election files, with its name."""
    out: dict[tuple[str, str], str] = {}
    for path in sorted(bundle("2014_onkormanyzati")):
        if FILES_2014.get(path.split("/")[-1]) not in ("mayor", "council", "ward"):
            continue
        for row in delimited("2014_onkormanyzati", path):
            if row.get("TELEPÜLÉS_SORSZÁM", "000") != "000" and row.get("MEGYEKÓD"):
                out.setdefault((row["MEGYEKÓD"].zfill(2), row["TELEPÜLÉS_SORSZÁM"].zfill(3)), row["TELEPÜLÉS"])
    return out


def _fetch_winner_pages() -> None:
    """Fetch the office's 2014 settlement result pages and 2019 winner pages, each kind stored as one file."""
    names = places().names
    if SETTLEMENTS_2014 not in raw.keys(SOURCE):
        targets = [
            (SETTLEMENTS_2014.replace("<county>", maz).replace("<settlement>", taz), names.get(maz + taz, name))
            for (maz, taz), name in sorted(_settlements_2014().items())
        ]
        _save_pages(SETTLEMENTS_2014, _crawl_pages(targets))
    codes = sorted((code, name) for code, name in names.items() if not code.startswith(ABROAD))

    def address(template: str, code: str) -> str:
        return template.replace("<county>", code[:2]).replace("<settlement>", code[2:])

    for template in (MAYORS_2019, COUNCILS_2019):
        if template not in raw.keys(SOURCE):
            _save_pages(template, _crawl_pages([(address(template, c), n) for c, n in codes]))
    if WARDS_2019 not in raw.keys(SOURCE):
        lists = _crawl_pages([(address(WARDS_2019, c), n) for c, n in codes])
        wards = [
            (PAGES_2019 + html.unescape(link), place)
            for _, place, page in lists
            if page
            for link in dict.fromkeys(
                re.findall(r'href="(teljkv\.jsp\?[^"]*W=5&(?:amp;)?TEVK=\d+)"', page, re.IGNORECASE)
            )
        ]
        _save_pages(WARDS_2019, [*lists, *_crawl_pages(wards)])


def pages(name: str) -> Iterator[tuple[str, str, str]]:
    """The settlement pages of one combined file of saved pages: each page's address, place name, and HTML."""
    content = raw.cached(SOURCE, name).path.read_bytes().decode("utf-8", errors="replace")
    marks = list(PAGE_START.finditer(content))
    for mark, following in zip(marks, [*marks[1:], None], strict=False):
        yield mark[1], html.unescape(mark[2]), content[mark.end() : following.start() if following else None]


def table_cells(page: str) -> list[list[str]]:
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.DOTALL | re.IGNORECASE):
        cells = re.findall(r"<t[dh][^>]*>(.*?)(?=<t[dh]|</tr>|$)", row, re.DOTALL | re.IGNORECASE)
        rows.append(
            [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", c)).replace("\xa0", " ")).strip() for c in cells]
        )
    return rows


def name_words(name: str) -> tuple[str, ...]:
    """The words of a printed name without titles, so a name that leaves out a given name still lines up."""
    words = re.sub(r"[^\w\s-]", " ", unicodedata.normalize("NFC", name).upper()).split()
    words = [word for word in words if word not in ("DR", "ID", "IFJ", "ÖZV", "PROF")]
    # A married name ends in -né on the last given name, which a shortened line may attach to an earlier one.
    return tuple(
        word[:-2].removesuffix("Á").removesuffix("É")
        + ("A" if word[:-2].endswith("Á") else "E" if word[:-2].endswith("É") else "")
        if word.endswith("NÉ") and len(word) > 4
        else word
        for word in words
    )


def is_subsequence(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    rest = iter(long)
    return bool(short) and all(word in rest for word in short)


class Winners:
    """Marks declared winners on the options of a built day, found by position or by the name printed on the ballot."""

    def __init__(self, d: Day, year: int) -> None:
        self.d = d
        self.year = year
        self.marked: Counter[str] = Counter()
        self.taken: set[tuple[str, str]] = set()
        self.totals: dict[str, Counter[str]] = {}
        self.missed: list[str] = []
        self.index: dict[str, dict[str, list[tuple[str, str | None]]]] = defaultdict(lambda: defaultdict(list))
        self.labels: dict[str, dict[tuple[str, ...], list[tuple[str, str | None]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for row in d.p.rows.get("contest_option", []):
            if row["kind"] == "candidate":
                pair = (cast(str, row["id"]), cast(str | None, row["party_id"]))
                self.index[cast(str, row["contest_id"])][same_name(cast(str, row["ballot_label"]))].append(pair)
                self.labels[cast(str, row["contest_id"])][name_words(cast(str, row["ballot_label"]))].append(pair)

    def tag(self, kind: str, area: str, ward: str | None = None) -> str | None:
        for spelling in dict.fromkeys([ward, ward and ward.lstrip("0"), ward and ward.zfill(2)]):
            tag = f"onk:{self.year}:{kind}:{area}" + (f":{spelling}" if spelling else "")
            if tag in self.d.sets:
                return tag
        return None

    def by_position(self, kind: str, tag: str, position: int) -> None:
        option = self.d.options.get((tag, str(position)))
        if option is None:
            self.missed.append(f"{tag}: no option at position {position}")
            return
        self.d.outcome(tag, option, "elected")
        self.marked[kind] += 1

    def by_name(self, kind: str, tag: str, name: str, nominator: str | None = None) -> None:
        """Mark the candidate whose ballot name is `name`, or, when the line also carries the organization, starts it."""
        names = self.index[self.d.contest_id(tag)]
        wanted = same_name(name)
        found = names.get(wanted) or next((v for k, v in names.items() if k and wanted.startswith(k)), [])
        if not found:
            words = name_words(name)
            found = [
                pair
                for label, pairs in self.labels[self.d.contest_id(tag)].items()
                if is_subsequence(words, label) or is_subsequence(label, words)
                for pair in pairs
            ]
        if len(found) > 1 and nominator:
            found = [pair for pair in found if pair[1] == party(nominator)] or found
        found = [pair for pair in found if (tag, pair[0]) not in self.taken] or found
        if len(found) > 1:
            # Namesakes the line cannot tell apart: the one with the most votes in the contest is the one elected.
            votes = self.votes(tag)
            top = max(votes.get(pair[0], 0) for pair in found)
            found = [pair for pair in found if votes.get(pair[0], 0) == top]
        if len(found) != 1:
            self.missed.append(f"{tag}: {name!r} matches {len(found)} candidates")
            return
        self.taken.add((tag, found[0][0]))
        self.d.outcome(tag, found[0][0], "elected")
        self.marked[kind] += 1

    def votes(self, tag: str) -> dict[str, int]:
        if tag not in self.totals:
            totals: Counter[str] = Counter()
            result_set = self.d.sets[tag]
            for (rs, _unit, option, measure), amount in self.d.values.items():
                if rs == result_set and option is not None and measure == 1:
                    totals[option] += amount
            self.totals[tag] = totals
        return self.totals[tag]


def winners_2019(d: Day, local: Local) -> None:
    found = places()
    winners = Winners(d, 2019)
    for name in (MAYORS_2019, WARDS_2019, COUNCILS_2019):
        d.cite(name)
    for address, place, page in pages(MAYORS_2019):
        match = re.search(r"MAZ=(\d+)&TAZ=(\d+)", address)
        assert match
        area = found.resolve(match[1], match[2], place, trust_code=True)
        tag = winners.tag("mayor", area)
        for row in table_cells(page):
            if len(row) == 6 and row[5] == "Igen" and tag:
                winners.by_position("mayor", tag, cast(int, number(row[0])))
    for address, place, page in pages(WARDS_2019):
        match = re.search(r"MAZ=(\d+)&TAZ=(\d+)&W=5&TEVK=(\d+)", address)
        if not match:
            continue
        area = found.resolve(match[1], match[2], place, trust_code=True)
        tag = winners.tag("ward", area, match[3])
        for row in table_cells(page):
            if len(row) == 6 and row[5] == "Igen":
                if tag:
                    winners.by_position("ward", tag, cast(int, number(row[0])))
                else:
                    winners.missed.append(f"{area} ward {match[3]}: no contest")
    for address, place, page in pages(COUNCILS_2019):
        match = re.search(r"MAZ=(\d+)&TAZ=(\d+)", address)
        assert match
        area = found.resolve(match[1], match[2], place, trust_code=True)
        if tag := winners.tag("council", area):
            for row in table_cells(page):
                if (
                    len(row) == 2
                    and row[0]
                    and row[0] != "Képviselő neve"
                    and not row[1].isdigit()
                    and row[0] != "Szervezet"
                ):
                    winners.by_name("council", tag, row[0], row[1])
    report("2019", winners)


def report(year: str, winners: Winners) -> None:
    print(f"{year} winners marked: {dict(winners.marked)}; unmatched: {len(winners.missed)}")
    for line in winners.missed[:15]:
        print("  ", line)
    if winners.missed:
        raise ValueError(f"{year}: {len(winners.missed)} declared winners did not match a candidate")


OTHER.append((date(2019, 10, 13), "2019 local elections", local_2019))


NATIONALITY_SYSTEM = key("electoral_system", "nationality", 2014)
SYSTEMS.append(
    (
        NATIONALITY_SYSTEM,
        date(1994, 1, 1),
        None,
        {"formula": "block"},
        (
            "Voters on the nationality roll elect the settlement nationality self-government on one ballot from "
            "individual candidates, voting for as many as there are seats; from 2014 the territorial and national "
            "nationality self-governments are elected from lists on the same day."
        ),
    )
)


# The office's 1994-2010 files call the Roma nationality "cigány"; from 2014 it writes "Roma".
NATION_NAMES = {"Cigány": "Roma"}


def nationality_body(level: str, place: str, nation: str) -> str:
    id = key("body", "nationality", level, place, nation)
    LOCAL_BODIES[id] = ("body", f"nationality-{level}", f"{place}|{nation}")
    return id


def legacy_nationality_body(place: str, nation: str) -> None:
    """Keep a 1994-2010 settlement self-government under the key earlier builds gave it, before the nationality names
    were brought in line with the later years, since results loaded from those builds still refer to it."""
    nationality_body("settlement", place, nation)


def county_unit(d: Day, maz: str, suffix: str = "") -> str:
    code = f"{d.day}:{maz}{suffix}"
    found = d.units.get(code)
    if found is None:
        found = d.units[code] = key("unit", code)
        d.p.add(
            "reporting_unit",
            id=found,
            polity_id=HUNGARY,
            scheme_code="hu.megye",
            code=code,
            area_relation="equals",
            area_id=county(maz),
            kind="aggregate",
        )
    return found


def nationality_contest(local: Local, level: str, place: str, nation: str) -> str:
    """level: national (lists, nationwide), territorial (lists, one county), settlement (candidates)."""
    d = local.d
    nation = NATION_NAMES.get(nation.capitalize(), nation)
    tag = f"nemz:{local.year}:{level}:{place}:{changeset.slug(nation)}"
    if tag in d.sets:
        return tag
    if level == "national":
        title, english, area = (
            f"Országos {nation.lower()} önkormányzat",
            f"National {nation} self-government",
            TERRITORY,
        )
    elif level == "territorial":
        title = f"{COUNTIES[place]} területi {nation.lower()} önkormányzat"
        english, area = f"{nation} self-government of {COUNTIES[place]}", county(place)
    else:
        name = local.place_name(place)
        title, english = f"{name} települési {nation.lower()} önkormányzat", f"{nation} self-government of {name}"
        area = settlement(place)
    d.contest(
        tag,
        local.election,
        "body_seats" if level == "settlement" else "list_allocation",
        title,
        f"{local.path}/{changeset.slug(tag.split(':', 2)[2])}",
        district_area=area,
        magnitude=None,
        body=nationality_body(level, place, nation),
        system=NATIONALITY_SYSTEM if level == "settlement" else LOCAL_LIST,
        method=method(
            "Nationality ballots are cast at the nationality polling stations. The territorial and national lists "
            "are published by county, the finest unit the office publishes for them."
            if level != "settlement"
            else "Nationality ballots are cast at the nationality polling stations."
        ),
        english=english,
    )
    return tag


FILES_2014 = {
    "polgármester_választás.csv": "mayor",
    "főpolgármester_választás.csv": "lord-mayor",
    "fïpolgármester_választás.csv": "lord-mayor",
    "egyéni_listás_választás.csv": "council",
    "egyéni_választókerületi_választás.csv": "ward",
    "megyei_közgy√lés_választás.csv": "county-list",
    "megyei_közgyűlés_választás.csv": "county-list",
    "országos_nemzetiségi_választás.csv": "nat-national",
    "területi_nemzetiségi_választás.csv": "nat-territorial",
    "települési_nemzetiségi_választás.csv": "nat-settlement",
}


def compensation_2014(d: Day, local: Local) -> None:
    found = places()
    area = ""
    lists: dict[int, str] = {}
    fragments: dict[int, int | None] = {}
    seats: dict[int, int | None] = {}
    for path in bundle("2014_onkormanyzati"):
        if path.endswith("kompenzációs_listás_választás.csv"):
            rows = delimited("2014_onkormanyzati", path)
    for row in [*rows, {"JVK_AZONOSÍTÓ": "00000F"}]:
        if row["JVK_AZONOSÍTÓ"].endswith("F"):
            if lists:
                compensation(d, local, area, lists, fragments, seats)
            lists, fragments, seats = {}, {}, {}
            if row.get("MEGYEKÓD"):
                area = found.resolve(
                    row["MEGYEKÓD"], row["TELEPÜLÉS_SORSZÁM"], row["FŐVÁROS_TELEPÜLÉS"], trust_code=True
                )
        else:
            position = cast(int, number(row["LISTA_SORSZÁM"]))
            lists[position] = row["LISTA_NEVE"]
            fragments[position] = number(row["TÖREDÉK_SZAVAZAT"])
            seats[position] = number(row["MANDÁTUM"])


def local_2014(d: Day) -> None:
    d.cite(ARCHIVE)
    local = Local(d, 2014)
    compensation_2014(d, local)
    found = places()
    for path in sorted(bundle("2014_onkormanyzati")):
        role = FILES_2014.get(path.split("/")[-1])
        if role is None:
            continue
        for head, items in _protocols(delimited("2014_onkormanyzati", path), "SZAVAZÓKÖR_AZON"):
            maz = head["MEGYEKÓD"]
            nation = head.get("NEMZETISÉG", "")
            if head["TELEPÜLÉS_SORSZÁM"] == "000":
                area, unit = None, county_unit(d, maz)
            else:
                area = found.resolve(maz, head["TELEPÜLÉS_SORSZÁM"], head["TELEPÜLÉS"], trust_code=True)
                unit = d.unit(area, head["SZAVAZÓKÖR"])
            if role.startswith("nat-"):
                level = role[4:]
                place = "HU" if level == "national" else maz if level == "territorial" else cast(str, area)
                tag = nationality_contest(local, level, place, nation)
            else:
                ward = head.get("EGYÉNI_VÁL_KER") or None
                place = {"county-list": maz, "lord-mayor": "01"}.get(role, cast(str, area))
                tag = local.contest(role, place, ward, magnitude=None if role in ("council", "county-list") else 1)
            d.accounting(
                tag,
                unit,
                number(head["VÁLASZTÓPOLGÁROK_SZÁMA"]),
                number(head["MEGJELENTEK_SZÁMA"]),
                number(head["URNÁBAN_LEVŐ_BÉLYEGZETT"]),
                number(head["ÉRVÉNYTELEN_SZAVAZÓLAP"]),
                number(head["ÉRVÉNYES_SZAVAZÓLAP"]),
            )
            for item in items:
                position = number(item["SZAVLAP_SORSZÁM"])
                if "LISTA" in item:
                    label = item["LISTA"]
                    option = d.option(tag, label, "list", nominator=label, position=position, ident=str(position))
                else:
                    label, nominator = item["JELÖLT"], item["SZERVEZET"]
                    option = d.option(
                        tag, label, "candidate", nominator=nominator, position=position, ident=str(position)
                    )
                    if d.first(tag, str(position)):
                        d.candidate(tag, option, f"{tag}:{position}", label, nominator=nominator)
                d.value(tag, unit, option, 1, number(item["SZAVAZAT"]))
    winners_2014(d, local)


def winners_2014(d: Day, local: Local) -> None:
    found = places()
    winners = Winners(d, 2014)
    d.cite(SETTLEMENTS_2014)
    for address, place, page in pages(SETTLEMENTS_2014):
        match = re.search(r"/M(\d+)/T(\d+)/", address)
        assert match
        area = found.resolve(match[1], match[2], place, trust_code=True)
        mayor = re.search(r"A polgármester</p>\s*<p[^>]*><strong>(.*?)</strong>", page, re.DOTALL)
        if mayor and (tag := winners.tag("mayor", area)):
            line = [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", part))).strip()
                for part in re.split(r"<br\s*/?>", mayor[1])
            ]
            if line[0]:
                winners.by_name("mayor", tag, line[0], line[1] if len(line) > 1 else None)
            else:
                winners.marked["no mayor declared"] += 1
        start = page.find("Az önkormányzat összetétele")
        if start < 0:
            continue
        block = page[start : page.find("</table>", start)]
        block_tag = winners.tag("council", area)
        for row in table_cells(block):
            if len(row) < 3 or not row[0] or row[0] == "A jelölt neve":
                continue
            ward = row[2].split()[0] if row[2].split() else None
            if ward:
                tag = winners.tag("ward", area, ward)
                if tag:
                    winners.by_name("ward", tag, row[0], row[1])
                else:
                    winners.missed.append(f"{area} ward {ward}: no contest")
            elif block_tag:
                winners.by_name("council", block_tag, row[0], row[1])
    report("2014", winners)


OTHER.append((date(2014, 10, 12), "2014 local and nationality elections", local_2014))


NATIONALITY_SETTLEMENT_FILES = {2019: "2019_Nemzetisegi telepulesi eredmenyek", 2024: "2024_nemzetisegi"}
NATIONS = ("Bolgár", "Görög", "Horvát", "Lengyel", "Német", "Örmény", "Roma", "Román", "Ruszin", "Szerb", "Szlovák")
NATIONS += ("Szlovén", "Ukrán")


def nationality_settlements(d: Day, local: Local) -> None:
    """Settlement nationality self-governments by settlement, the finest unit the office publishes for them."""
    name = NATIONALITY_SETTLEMENT_FILES[d.day.year]
    found = places()
    for path in sorted(bundle(name)):
        if not path.endswith(".xls"):
            continue
        book = sheets(name, path)
        rows = next((rows for sheet, rows in book.items() if sheet.startswith(("Települési", "Települési nemz"))), [])
        if not rows:
            continue
        maz = None
        nation = None
        header: list[str] = []
        for row in rows:
            first = row[0].strip() if row else ""
            if not any(row[1:]) and first:
                if first in NATIONS:
                    nation = first
                elif maz is None:
                    try:
                        maz = county_key(first)
                    except LookupError:
                        pass
                continue
            if first == "Település":
                header = row
                continue
            if not first or nation is None or not header or number(row[2]) is None:
                continue
            columns = {cell: index for index, cell in enumerate(header)}
            area = found.resolve(cast(str, maz), None, first, trust_code=False)
            tag = nationality_contest(local, "settlement", area, nation)
            unit = d.settlement_unit(area)
            d.accounting(
                tag,
                unit,
                number(row[columns["A"]]),
                number(row[columns["F"]]),
                number(row[columns["K"]]),
                number(row[columns["M"]]),
                number(row[columns["N"]]),
            )
            first_option = columns["N"] + 1
            candidates = [(index, index - first_option + 1) for index in range(first_option, len(row))]
            running = max((position for index, position in candidates if number(row[index])), default=0)
            for index, position in candidates:
                if position > running:
                    break
                option = d.option(tag, f"{position}. jelölt", "candidate", position=position, ident=str(position))
                d.value(tag, unit, option, 1, number(row[index]))
    for tag, result_set in d.sets.items():
        if tag.startswith(f"nemz:{local.year}:settlement:"):
            d.p.add(
                "note",
                id=key("note", result_set, "unnamed"),
                entity_id=result_set,
                kind="caveat",
                body_md="The office publishes these results per settlement and by the candidates' order on the ballot, "
                "without their names; the options are named by that order.",
                lang="en",
            )


NATIONALITY_LISTS_2019 = "2019_Nemzetisegi orsz és teruleti eredmenyek"


def list_names_2019(book: dict[str, list[list[str]]], nation: str) -> dict[tuple[str, str | None], dict[int, str]]:
    """The names of the registered lists, by level and county, from a nationality's list sheets."""
    names: dict[tuple[str, str | None], dict[int, str]] = {}
    for sheet, rows in book.items():
        if sheet == "Országos listák":
            level, maz = "national", None
        elif "területi list" in sheet:
            level = "territorial"
            label = next(row[1] for row in rows if row[:1] == ["Megye:"])
            maz = county_key(re.sub(r"(?i)\s*megye$", "", label))
        else:
            continue
        head = next(i for i, row in enumerate(rows) if row[:1] in (["Nemzetiség"], ["Megye"]))
        columns = {cell: index for index, cell in enumerate(rows[head])}
        order = next((index for cell, index in columns.items() if cell.startswith("Lista sorszám")), None)
        found: dict[int, str] = {}
        unnumbered: list[str] = []
        for row in rows[head + 1 :]:
            if len(row) <= columns["Lista neve"] or not row[columns["Lista neve"]]:
                continue
            position = number(row[order]) if order is not None and order < len(row) else None
            name = row[columns["Lista neve"]]
            if position is None:
                if name not in unnumbered:
                    unnumbered.append(name)
            else:
                found[position] = name
        if unnumbered:
            # Where the sheet prints no list number, the lists are numbered in the order the sheet gives them.
            if found:
                raise ValueError(f"{nation} {sheet}: unnumbered lists {unnumbered} beside {found}")
            found = {position: name for position, name in enumerate(unnumbered, 1)}
        elif list(found) != sorted(found):
            print(f"{nation} {sheet}: lists out of order {found}")
        names[(level, maz)] = found
    return names


def nationality_lists_2019(d: Day, local: Local) -> None:
    """National and territorial nationality lists, by settlement within each county.

    One results sheet per nationality holds two blocks: the national lists' votes by county and settlement, and the
    territorial lists' votes by settlement with each county's total and seats. The national block's nationwide row is
    printed empty, so it declares no national totals and no seats.
    """
    name = NATIONALITY_LISTS_2019
    found = places()
    for path in sorted(bundle(name)):
        book = sheets(name, path)
        nation = next(
            row[1]
            for sheet, rows in book.items()
            if sheet == "Országos listák"
            for row in rows
            if row[:1] == ["Nemzetiség:"]
        )
        titles = list_names_2019(book, nation)
        rows = next(rows for rows in book.values() if any(row[:2] == ["Megye", "Település"] for row in rows))
        level = ""
        header: list[str] = []
        for row in rows:
            row = row + [""] * (len(header) - len(row))
            heading = next(
                (cell.strip() for cell in row[:5] if cell.startswith(("Országos nemz", "Területi nemz"))), ""
            )
            if heading:
                level = "national" if heading.startswith("Országos") else "territorial"
                continue
            if row[:2] == ["Megye", "Település"]:
                header = row
                continue
            if not level or not header or number(row[4]) is None or row[0] == "Országos":
                continue
            county_name, place = row[0].strip(), row[1].strip()
            maz = county_key(county_name)
            lists = [(i, int(header[i])) for i in range(12, len(header), 2) if header[i].strip().isdigit()]
            tag = nationality_contest(local, level, "HU" if level == "national" else maz, nation)
            names = titles.get((level, None if level == "national" else maz), {})

            def option(position: int) -> str:
                label = names.get(position)
                return d.option(
                    tag,
                    label or f"{position}. lista",
                    "list",
                    nominator=label,
                    position=position,
                    ident=str(position),
                )

            if not place:
                if level == "territorial" and not row[2].startswith("Nincs rögzítve"):
                    d.declare(tag, None, 4, number(row[11]))
                    for index, position in lists:
                        if position not in names and not number(row[index]):
                            continue  # the sheet prints zeros for list numbers this county did not register
                        d.declare(tag, option(position), 1, number(row[index]))
                        won = number(row[index + 1])
                        if won:
                            d.outcome(tag, option(position), "won", won)
                continue  # a county total of the national lists; the settlement lines carry the same votes
            if place.startswith("TVB "):
                unit = d.special_unit(f"nemz-tvb:{maz}", "standard")
            else:
                unit = d.settlement_unit(found.resolve(maz, None, place, trust_code=False))
            d.accounting(tag, unit, number(row[4]), number(row[5]), number(row[8]), number(row[10]), number(row[11]))
            for index, position in lists:
                d.value(tag, unit, option(position), 1, number(row[index]))
    for tag, result_set in d.sets.items():
        if tag.startswith(("nemz:2019:national:", "nemz:2019:territorial:")):
            body = (
                "The office published these results as not yet final. Lines headed as the counting committee's "
                "protocol hold the ballots that committee counted itself, and are kept as separate units. Where the "
                "workbook marks a county's total as not recorded, only the settlement lines are stored."
            )
            if ":national:" in tag:
                body += " The workbooks leave the nationwide row and every seat of the national lists empty."
            d.p.add(
                "note",
                id=key("note", result_set, "lists"),
                entity_id=result_set,
                kind="caveat",
                body_md=body,
                lang="en",
            )


def nationality_2019(d: Day) -> None:
    d.cite(ARCHIVE)
    local = Local(d, 2019)
    nationality_settlements(d, local)
    nationality_lists_2019(d, local)


NATIONALITY_LISTS_2024 = "2024_nemzetisegi"
NATIONALITY_LISTS_FILE = "2024.xls"


def nationality_lists(d: Day, local: Local) -> None:
    """National and territorial nationality lists, by settlement within each county.

    One sheet per nationality holds two blocks. The national block gives the nationwide total, the county totals, and
    the settlement lines of the national lists; the territorial block gives, for each county, the county total and its
    settlement lines. Each line also has a counting-committee line ("TVB számlálás jegyzőkönyve") for the ballots the
    committee counted itself. The office prints the lists by number only.
    """
    name = NATIONALITY_LISTS_2024
    found = places()
    book = sheets(name, NATIONALITY_LISTS_FILE)
    roll = {str(row["nemznev"]): row for row in _items(vtr_json("onk2024", "ver", "NemzVal.json")["list"])}
    for nation in NATIONS:
        rows = book.get(nation)
        if rows is None:
            raise LookupError(f"{nation}: no sheet in the national and territorial workbook")
        level = ""
        header: list[str] = []
        seats: Counter[str] = Counter()
        for row in rows:
            row = row + [""] * (len(header) - len(row))
            heading = row[2].strip() if len(row) > 2 else ""
            if heading.startswith("Országos nemzetiségi"):
                level = "national"
                continue
            if heading.startswith("Területi nemzetiségi"):
                level = "territorial"
                continue
            if row[0] == "Vármegye":
                header = row
                continue
            if not level or not header or number(row[4]) is None:
                continue
            county_name, place = row[0].strip(), row[1].strip()
            lists = [
                (index, int(header[index])) for index in range(12, len(header), 2) if header[index].strip().isdigit()
            ]
            if level == "national" and not county_name:
                maz, tag = None, nationality_contest(local, "national", "HU", nation)
            else:
                maz = county_key(county_name)
                if level == "national" and place == "---":
                    continue  # a county total of the national lists; the settlement lines carry the same votes
                tag = nationality_contest(local, level, "HU" if level == "national" else maz, nation)
            if place == "---":
                total = tag
                d.declare(total, None, 4, number(row[11]))
                for index, position in lists:
                    option = d.option(tag, f"{position}. lista", "list", position=position, ident=str(position))
                    d.declare(total, option, 1, number(row[index]))
                    won = number(row[index + 1])
                    if won:
                        d.outcome(total, option, "won", won)
                        seats[tag] += won
                continue
            assert maz is not None
            if place.startswith("TVB "):
                unit = d.special_unit(f"nemz-tvb:{maz}", "standard")
            else:
                area = found.resolve(maz, None, place, trust_code=False)
                unit = d.settlement_unit(area)
            d.accounting(
                tag,
                unit,
                number(row[4]),
                number(row[5]),
                number(row[8]),
                number(row[10]),
                number(row[11]),
            )
            for index, position in lists:
                option = d.option(tag, f"{position}. lista", "list", position=position, ident=str(position))
                d.value(tag, unit, option, 1, number(row[index]))
        expected = roll[nation]
        national = seats[f"nemz:2024:national:HU:{changeset.slug(nation)}"]
        if national != expected.get("mandatum"):
            print(
                f"nationality lists {nation}: national seats {national}, NemzVal.json says {expected.get('mandatum')}"
            )
    for tag, result_set in d.sets.items():
        if tag.startswith(("nemz:2024:national:", "nemz:2024:territorial:")):
            d.p.add(
                "note",
                id=key("note", result_set, "unnamed"),
                entity_id=result_set,
                kind="caveat",
                body_md="The office publishes these results by settlement and by list number, without the lists' "
                "names; the options are named by that number. Lines headed as the counting committee's protocol "
                "hold the ballots that committee counted itself, and are kept as separate units.",
                lang="en",
            )


def nationality_2024(d: Day) -> None:
    d.cite(ARCHIVE)
    local = Local(d, 2024)
    nationality_settlements(d, local)
    nationality_lists(d, local)


OTHER.append((date(2019, 10, 13), "2019 nationality elections", nationality_2019))
OTHER.append((date(2024, 6, 9), "2024 nationality elections", nationality_2024))


def seat_key(position: str, label: str) -> str:
    """A local candidate's key: the ballot position and the name, since a withdrawn candidate keeps a position."""
    return f"{number(position)}:{same_name(label)}"


MID_LOCAL = {
    2002: ("2002_onkormanyzati", date(2002, 10, 20)),
    2006: ("2006_onkormanyzati", date(2006, 10, 1)),
    2010: ("2010_onkormanyzati", date(2010, 10, 3)),
}
LIST_KINDS = {"1": "county-list", "2": "county-list-large", "3": "budapest-list"}


def _txt(name: str, stem: str) -> list[list[str]]:
    found = [path for path in bundle(name) if path.split("/")[-1] == f"{stem}.txt"]
    return piped(name, found[0]) if found else []


def mid_local(d: Day) -> None:
    year = d.day.year
    name, _ = MID_LOCAL[year]
    d.cite(ARCHIVE)
    local = Local(d, year)
    found = places()
    registered_at = -6 if year == 2010 else -7
    nominators: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for stem, width in (("polgsz", 3), ("ejelersz", 4), ("kislersz", 3), ("kisersz", 4), ("fopolgsz", 1)):
        for row in _txt(name, stem):
            cells = row[:width]
            if stem == "kisersz" and year == 2010:
                cells[2] = cells[2].lstrip("0")
            nominators[(stem[:-2], *cells)].append(row[width + 2] if stem != "fopolgsz" else row[3])
    areas: dict[tuple[str, str], str] = {}

    def area_of(maz: str, taz: str, town: str) -> str:
        index = (maz, taz)
        if index not in areas:
            areas[index] = found.resolve(maz, taz, town, trust_code=True)
        return areas[index]

    def accounting(tag: str, unit: str, nums: list[int]) -> None:
        d.accounting(tag, unit, nums[registered_at], nums[-5], nums[-4], nums[-2], nums[-1])

    def candidate(
        tag: str, unit: str | None, position: str, title: str, printed: str, votes: str, nominator: str
    ) -> str:
        label = f"{title} {printed}".strip()
        ident = seat_key(position, label)
        option = d.option(tag, label, "candidate", nominator=nominator or None, position=number(position), ident=ident)
        if d.first(tag, ident):
            d.candidate(tag, option, f"{tag}:{ident}", label, nominator=nominator or None)
        if unit is not None:
            d.value(tag, unit, option, 1, number(votes))
        return option

    # Mayors
    for row in _txt(name, "polgsf"):
        area = area_of(row[0], row[2], row[3])
        nums, _ = _numbers(row[5:])
        accounting(local.contest("mayor", area), d.unit(area, row[4]), nums)
    for row in _txt(name, "polgsj"):
        area = areas[(row[0], row[1])]
        nominator = ",".join(nominators.get(("polg", row[0], row[1], row[3]), []))
        candidate(local.contest("mayor", area), d.unit(area, row[2]), row[3], row[4], row[5], row[6], nominator)
    for row in _txt(name, "polgoj"):
        if (row[0], row[1]) not in areas:
            continue
        tag = local.contest("mayor", areas[(row[0], row[1])])
        option = d.options.get((tag, seat_key(row[2], f"{row[3]} {row[4]}")))
        if option and number(row[6]):
            d.outcome(tag, option, "elected")
    # Lord Mayor of Budapest
    for row in _txt(name, "fopolgsf"):
        area = area_of("01", row[0], row[2])
        nums, _ = _numbers(row[3:])
        accounting(local.contest("lord-mayor", "01"), d.unit(area, row[1]), nums)
    for row in _txt(name, "fopolgsj"):
        area = areas[("01", row[0])]
        nominator = ",".join(nominators.get(("fopolg", row[2]), []))
        candidate(local.contest("lord-mayor", "01"), d.unit(area, row[1]), row[2], row[3], row[4], row[5], nominator)
    for row in _txt(name, "fopolgoj"):
        tag = local.contest("lord-mayor", "01")
        option = d.options.get((tag, seat_key(row[0], f"{row[1]} {row[2]}")))
        if option and number(row[4]):
            d.outcome(tag, option, "elected")
    # Single-member wards
    for row in _txt(name, "ejelersf"):
        area = area_of(row[0], row[2], row[3])
        nums, _ = _numbers(row[6:])
        accounting(local.contest("ward", area, row[4]), d.unit(area, row[5]), nums)
    for row in _txt(name, "ejelersj"):
        area = areas[(row[0], row[1])]
        nominator = ",".join(nominators.get(("ejeler", row[0], row[1], row[2], row[4]), []))
        tag = local.contest("ward", area, row[2])
        candidate(tag, d.unit(area, row[3]), row[4], row[5], row[6], row[7], nominator)
    for row in _txt(name, "ejelerej"):
        if (row[0], row[1]) not in areas:
            continue
        tag = local.contest("ward", areas[(row[0], row[1])], row[2])
        option = d.options.get((tag, seat_key(row[3], f"{row[4]} {row[5]}")))
        if option and number(row[7]):
            d.outcome(tag, option, "elected")
    # Councils of settlements up to 10,000 inhabitants (block vote)
    for row in _txt(name, "kislersf"):
        area = area_of(row[0], row[2], row[3])
        nums, _ = _numbers(row[5:])
        accounting(local.contest("council", area, magnitude=None), d.unit(area, row[4]), nums[:-1])
    for row in _txt(name, "kislersj"):
        area = areas[(row[0], row[1])]
        nominator = ",".join(nominators.get(("kisler", row[0], row[1], row[3]), []))
        candidate(
            local.contest("council", area, magnitude=None),
            d.unit(area, row[2]),
            row[3],
            row[4],
            row[5],
            row[6],
            nominator,
        )
    for row in _txt(name, "kislerej"):
        if (row[0], row[1]) not in areas:
            continue
        tag = local.contest("council", areas[(row[0], row[1])], magnitude=None)
        option = d.options.get((tag, seat_key(row[2], f"{row[3]} {row[4]}")))
        if option and number(row[6]):
            d.outcome(tag, option, "elected")
    # County and Budapest assembly lists
    for row in _txt(name, "kozgysf"):
        area = area_of(row[0], row[2], row[3])
        nums, _ = _numbers(row[7:])
        role = LIST_KINDS[row[5]]
        tag = local.contest(role, row[0], magnitude=None)
        accounting(tag, d.unit(area, row[4]), nums)
    for row in _txt(name, "kozgysl"):
        area = areas[(row[0], row[1])]
        tag = local.contest(LIST_KINDS[row[3]], row[0], magnitude=None)
        ident = str(number(row[4]))
        option = d.option(tag, row[5], "list", nominator=row[5], position=number(row[4]), ident=ident)
        d.value(tag, d.unit(area, row[2]), option, 1, number(row[6]))
    for row in _txt(name, "kozgyol"):
        tag = local.contest(LIST_KINDS[row[1]], row[0], magnitude=None)
        option = d.options.get((tag, str(number(row[2]))))
        if option is not None:
            d.declare(tag, option, 1, number(row[4]))
            if number(row[5]):
                d.outcome(tag, option, "won", number(row[5]))
    # Minority self-governments: candidate votes per polling station; seats from the settlement protocols
    nations: dict[str, str] = {}
    for row in _txt(name, "kiseref"):
        nations[row[4]] = row[5].strip().capitalize()
    by_station: set[str] = set()
    for row in _txt(name, "kisersf"):
        code, sorsz = (row[4], row[5]) if year == 2002 else (row[5], row[4])
        code = code.lstrip("0") if year == 2010 else code
        area = area_of(row[0], row[2], row[3])
        tag = nationality_contest(local, "settlement", area, nations.get(code, code))
        by_station.add(tag)
        unit = d.unit(area, sorsz)
        nums = [number(cell) for cell in row[7:]]
        if year == 2002:
            # Only the last two figures belong to this nationality; the rest count the station's ballots for all of them.
            d.value(tag, unit, None, 4, nums[11])
        elif year == 2006:
            d.accounting(tag, unit, nums[0], nums[2], nums[3], nums[5], nums[6])
        else:
            d.accounting(tag, unit, nums[0], nums[1], nums[2], nums[4], nums[5])
    for row in _txt(name, "kisersj"):
        if year == 2002:
            maz, taz, code, _, sorsz, position, title, printed, votes = row[:9]
        else:
            maz, taz, sorsz, code, position, title, printed, votes = row[:8]
        code = code.lstrip("0") if year == 2010 else code
        area = areas.get((maz, taz)) or area_of(maz, taz, "")
        tag = nationality_contest(local, "settlement", area, nations.get(code, code))
        nominator = ",".join(nominators.get(("kiser", maz, taz, code, position), []))
        candidate(tag, d.unit(area, sorsz), position, title, printed, votes, nominator)
    for row in _txt(name, "kiserej"):
        area = areas.get((row[0], row[1]))
        if area is None:
            continue
        code = row[2].lstrip("0") if year == 2010 else row[2]
        tag = nationality_contest(local, "settlement", area, nations.get(code, code))
        option = d.options.get((tag, seat_key(row[3], f"{row[4]} {row[5]}")))
        if option is None:
            continue
        d.declare(tag, option, 1, number(row[6]))
        if number(row[7]):
            d.outcome(tag, option, "elected")
    # The settlement protocols' valid ballots for each nationality, declared against the polling-station sums
    valid_at = {2002: 20, 2006: 15, 2010: 13}[year]
    for row in _txt(name, "kiseref"):
        area = areas.get((row[0], row[2]))
        if area is None:
            continue
        code = row[4].lstrip("0") if year == 2010 else row[4]
        tag = nationality_contest(local, "settlement", area, nations.get(row[4], code))
        if tag in by_station:
            d.declare(tag, None, 4, number(row[valid_at]))
        else:
            # No polling-station protocols for it: the settlement's own figure is the only count.
            d.value(tag, d.settlement_unit(area), None, 4, number(row[valid_at]))
    # Earlier builds keyed these self-governments by the nationality cell of the polling-station file as printed
    # (a name, or a bare code where the cell held one); loaded results still name those bodies, so they are kept.
    legacy: dict[str, str] = {}
    for row in _txt(name, "kisersf"):
        code, nation = (row[4], row[6]) if year == 2002 else (row[5], row[6])
        legacy[code] = nation.strip().capitalize()
    for row in _txt(name, "kisersj"):
        maz, taz, code = (row[0], row[1], row[2]) if year == 2002 else (row[0], row[1], row[3])
        legacy_nationality_body(areas.get((maz, taz)) or area_of(maz, taz, ""), legacy.get(code, code))
    for row in _txt(name, "kiserej"):
        if area := areas.get((row[0], row[1])):
            legacy_nationality_body(area, legacy.get(row[2], row[2]))


for _year, (_, _day) in MID_LOCAL.items():
    OTHER.append((_day, f"{_year} local and minority elections", mid_local))


OLD_LOCAL = {1994: ("1994_onkormanyzati", date(1994, 12, 11)), 1998: ("1998_onkormanyzati", date(1998, 10, 18))}
# Column positions of the settlement-level candidate files: position, name cells, votes, won flag, nominator.
OLD_LOCAL_ITEMS = {
    1994: {
        "polgmjel": (4, (6,), 7, 8, 11),
        "ejeleret": (5, (7,), 8, 9, 12),
        "kisljel": (4, (6,), 7, 8, 11),
        "fopolgt": (0, (2,), 3, 4, 7),
        "kiseretj": (7, (8,), 13, 11, 10),
    },
    1998: {
        "polgmjel": (4, (6, 7), 8, 9, 12),
        "ejeleret": (5, (7, 8), 9, 10, 13),
        "kisljel": (4, (6, 7), 8, 9, 12),
        "fopolgt": (0, (2, 3), 4, 5, 8),
        "kiseretj": (7, (8, 9), 14, 12, 11),
    },
}
OLD_LIST_KINDS = {"A": "county-list", "B": "budapest-list", "C": "county-list-large"}


def old_local(d: Day) -> None:
    year = d.day.year
    name, _ = OLD_LOCAL[year]
    d.cite(ARCHIVE)
    local = Local(d, year)
    found = places()
    spec = OLD_LOCAL_ITEMS[year]

    def stem(text_: str) -> list[list[str]]:
        rows = [row for path in bundle(name) if path.split("/")[-1] == f"{text_}.txt" for row in piped(name, path)]
        return rows

    def accounting(tag: str, unit: str, cells: list[str], extra: int = 0) -> None:
        nums, _ = _numbers(cells)
        nums = nums[: len(nums) - extra] if extra else nums
        d.accounting(tag, unit, nums[-6] if len(nums) == 9 else nums[-7], nums[-5], nums[-4], nums[-2], nums[-1])

    def items(file: str, tag_of: Callable[[list[str]], tuple[str, str] | None]) -> None:
        position_at, name_at, votes_at, won_at, nominator_at = spec[file]
        for row in stem(file):
            target = tag_of(row)
            if target is None:
                continue
            tag, unit = target
            label = " ".join(row[i] for i in name_at if row[i]).strip()
            nominator = row[nominator_at].replace(";", ",") if nominator_at < len(row) else ""
            ident = seat_key(row[position_at], label)
            option = d.option(
                tag, label, "candidate", nominator=nominator or None, position=number(row[position_at]), ident=ident
            )
            if d.first(tag, ident):
                d.candidate(tag, option, f"{tag}:{ident}", label, nominator=nominator or None)
            d.value(tag, unit, option, 1, number(row[votes_at]))
            if row[won_at] == "1":
                d.outcome(tag, option, "elected")

    areas: dict[tuple[str, str], str] = {}

    def area_of(row: list[str]) -> str:
        index = (row[0], row[2])
        if index not in areas:
            areas[index] = found.resolve(county_key(row[1]), None, row[3], trust_code=False)
        return areas[index]

    for row in stem("polgmfej"):
        area = area_of(row)
        accounting(local.contest("mayor", area), d.settlement_unit(area), row[6:])
    items("polgmjel", lambda row: (local.contest("mayor", area_of(row)), d.settlement_unit(area_of(row))))
    for row in stem("ejelere"):
        area = area_of(row)
        accounting(local.contest("ward", area, row[4]), d.settlement_unit(area), row[7:])
    items("ejeleret", lambda row: (local.contest("ward", area_of(row), row[4]), d.settlement_unit(area_of(row))))
    for row in stem("kislfej"):
        area = area_of(row)
        accounting(
            local.contest("council", area, magnitude=None), d.settlement_unit(area), row[6:], 1 if year == 1994 else 3
        )
    items(
        "kisljel",
        lambda row: (local.contest("council", area_of(row), magnitude=None), d.settlement_unit(area_of(row))),
    )
    budapest = county_unit(d, "01")
    for row in stem("fopolgf"):
        accounting(local.contest("lord-mayor", "01"), budapest, row[2:])
    items("fopolgt", lambda row: (local.contest("lord-mayor", "01"), budapest))
    for row in stem("tlisere"):
        maz = county_key(row[1])
        role = OLD_LIST_KINDS.get(
            row[2], {"1": "county-list", "2": "county-list-large", "3": "budapest-list"}.get(row[2])
        )
        if role is None:
            continue
        tag = local.contest(role, maz, magnitude=None)
        accounting(tag, county_unit(d, maz, f":{row[2]}"), row[6:], 1 if year == 1994 else 3)
    for row in stem("tliseret"):
        maz = county_key(row[1])
        role = OLD_LIST_KINDS.get(
            row[2], {"1": "county-list", "2": "county-list-large", "3": "budapest-list"}.get(row[2])
        )
        if role is None:
            continue
        tag = local.contest(role, maz, magnitude=None)
        label, votes, seats = (row[6], row[7], row[8]) if year == 1994 else (row[6], row[9], row[10])
        option = d.option(tag, label, "list", nominator=label, position=number(row[4]), ident=str(number(row[4])))
        d.value(tag, county_unit(d, maz, f":{row[2]}"), option, 1, number(votes))
        if number(seats):
            d.outcome(tag, option, "won", number(seats))
    for row in stem("kiseret"):
        area = area_of(row)
        tag = nationality_contest(local, "settlement", area, row[5].capitalize())
        d.value(tag, d.settlement_unit(area), None, 4, number(row[10]))
    items(
        "kiseretj",
        lambda row: (
            nationality_contest(local, "settlement", area_of(row), row[5].capitalize()),
            d.settlement_unit(area_of(row)),
        ),
    )
    for row in stem("kiseretj"):
        legacy_nationality_body(area_of(row), row[5].capitalize())


for _year, (_, _day) in OLD_LOCAL.items():
    OTHER.append((_day, f"{_year} local and minority elections", old_local))
