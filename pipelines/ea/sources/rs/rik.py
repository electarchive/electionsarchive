import csv
import functools
import io
import json
import re
import time
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid5

import fastexcel
import httpx
import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.rs import rik_parties

SOURCE = "rs.rik"
BASE = "https://www.rik.parlament.gov.rs"
RESULTS = f"{BASE}/542645/rezultati/"
SERBIA = str(uuid5(package.NAMESPACE, "polity\x1fRS"))
USER_AGENT = "electionsarchive/0.1 (+https://electionsarchive.com)"
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
ABROAD, PRISONS = "33", "55"
LICENSE = (
    "Creative Commons Attribution-NonCommercial-NoDerivs 3.0 Serbia, as stated on the Republic Electoral "
    "Commission's website"
)

METHOD = (
    "Counted by the polling board at each polling station (biračko mesto) and reported to the Republic Electoral "
    "Commission on the station's minutes, as published on the Commission's site. Registered voters are the extract "
    "of the voter register for the station, voters are those who voted, ballots cast are the ballots found in the "
    "box, and valid votes are the valid votes for the lists. Ballots received and unused are published for some "
    "elections and are not stored here. Voters abroad and in prisons are reported in stations of their own."
)
METHOD_APP = (
    "Counted by the polling board at each polling station (biračko mesto) and published station by station in the "
    "Republic Electoral Commission's results application. Registered voters are the extract of the voter register "
    "for the station, voters are those who voted, and valid votes are the valid votes for the lists. The "
    "application gives no count of ballots in the box for this election, so only voters, valid, and invalid ballots "
    "are stored. Voters abroad and in prisons are reported in stations of their own."
)


@dataclass(frozen=True)
class Layout:
    """Where a spreadsheet of the Commission's archive keeps each figure; every year laid its table out afresh."""

    file: str
    sheet: str
    header: int  # the row that names the lists or candidates
    first: int  # the first row of data
    number: int  # the polling station number
    figures: tuple[tuple[int, int], ...]  # (measure, column)
    lists: int  # the first list column
    step: int = 1
    name: int | None = 0  # the municipality's name, when the table has no codes
    code: int | None = None  # the municipality's code, when it has one
    okrug: int | None = None
    label: int | None = None
    national: int | None = None  # the row of the national totals, when the table carries them
    leaves: tuple[str, ...] = ()  # rows that report a whole group of voters with no polling stations beneath them
    unnamed: str = (
        ""  # numbered rows with no name in the name column that follow a zone's group row belong to this zone
    )


@dataclass(frozen=True)
class Election:
    slug: str  # package name
    day: date
    kind: str  # general, referendum
    family: str  # parliament, referendum, provincial, local
    title: str
    native: str
    seats: int | None
    method: str
    layout: Layout | None = None
    round: int = 1
    previous: str | None = None  # the package of the first round, for a runoff


ELECTIONS = [
    Election(
        "2020-06-21",
        date(2020, 6, 21),
        "general",
        "parliament",
        "2020 Serbian parliamentary election",
        "Избори за народне посланике Народне скупштине 2020.",
        250,
        METHOD,
    ),
    Election(
        "2022-01-16",
        date(2022, 1, 16),
        "referendum",
        "referendum",
        "2022 Serbian constitutional referendum",
        "Републички референдум ради потврђивања Акта о промени Устава Републике Србије 2022.",
        None,
        METHOD,
    ),
    Election(
        "2023-12-17",
        date(2023, 12, 17),
        "general",
        "parliament",
        "2023 Serbian parliamentary election",
        "Избори за народне посланике Народне скупштине 2023.",
        250,
        METHOD_APP,
    ),
    Election(
        "2023-12-17-vojvodina",
        date(2023, 12, 17),
        "general",
        "provincial",
        "2023 Vojvodina provincial election",
        "Избори за посланике Скупштине АП Војводине 2023.",
        120,
        METHOD_APP,
    ),
    Election(
        "2024-06-02-belgrade",
        date(2024, 6, 2),
        "local",
        "local",
        "2024 Belgrade City Assembly election",
        "Избори за одборнике Скупштине града Београда 2024.",
        110,
        METHOD_APP,
    ),
    Election(
        "2026-10-25",
        date(2026, 10, 25),
        "general",
        "parliament",
        "2026 Serbian parliamentary election",
        "Избори за народне посланике Народне скупштине 2026.",
        250,
        METHOD,
    ),
]
# LEGACY_ELECTIONS is appended below, once the layouts exist
# the results application's election type and round for each election it serves
APP = {
    "2023-12-17": (2, 341140),
    "2023-12-17-vojvodina": (7, 341143),
    "2024-06-02-belgrade": (3, 567956),
    "2026-10-25": (2, 680072),
}
ROUND_2026 = 680072


def xls(folder: int, name: str) -> str:
    return f"{BASE}/extfile/sr/{folder}/{urllib.parse.quote(name)}"


ARCHIVE = "https://arhiva.rik.parlament.gov.rs/doc/arhiva"


def archived(path: str) -> str:
    return f"{ARCHIVE}/{urllib.parse.quote(path)}"


CURRENT = "https://arhiva.rik.parlament.gov.rs/doc"
FILES = {
    "2004-report-1": archived("predsednik/2004/cir/3. 2004 izvestaj o rezultatima pr (prvi krug).doc"),
    "2004-report-2": archived(
        "predsednik/2004/cir/4. 2004 izvestaj o rezultatina ponovljenog glasanja (drugi krug).doc"
    ),
    "2007-report": archived("poslanici/2007/2. izvestaj o rezultatima np.pdf"),
    "2016-stations": f"{CURRENT}/izbori-2016/rezultati/{urllib.parse.quote('3. Rezultati izbora po birackim mestima.XLS')}",
    "2016-municipalities": f"{CURRENT}/izbori-2016/rezultati/{urllib.parse.quote('2. Rezultati po opstinama.XLS')}",
    "2017-stations": f"{CURRENT}/izbori-2017/dokumenta/{urllib.parse.quote('3 Rezultati po birackim mestima.xls')}",
    "2017-totals": f"{CURRENT}/izbori-2017/dokumenta/{urllib.parse.quote('1 Rezultati izbora.XLS')}",
    "2002-president": archived("predsednik/2002 I/cir/5. 2002 I rezultati izbora rzs.xls"),
    "2003-president": archived("predsednik/2003/cir/4. 2003 rezultati izbora rzs.xls"),
    "2003-parliament": archived("poslanici/2003/4. rezultati 2003 np rzs.xls"),
    "2004-president": archived("predsednik/2004/2004.xls"),
    "2007-parliament": archived("poslanici/2007/5. rezultati 2007 np rzs.XLS"),
    "2008-parliament": archived("poslanici/2008/4. rezultati 2008 np rzs.xls"),
    "2008-president": archived("predsednik/2008/6. 2008 rezultati izbora rzs.xls"),
    "2012-parliament": archived("poslanici/2012/4. 2012 np rzs.xls"),
    "2012-president": archived("predsednik/2012/6. 2012 rezultati izbora rzs.xls"),
    "2014-parliament": archived("poslanici/2014/4. Rezultati izbora po BM2014 np rzs.xls"),
    "2002-report-1": archived("predsednik/2002 I/cir/3. 2002 I izvestaj o rezultatima pr 29.09. (prvi krug).doc"),
    "2002-report-2": archived(
        "predsednik/2002 I/cir/4. 2002 I izvestaj o rezultatima pr 29.09. i 13.10. (drugi krug).doc"
    ),
    "2002-report-3": archived("predsednik/2002 II/cir/2. 2002 II izvestaj o rezultatima pr.doc"),
    "2003-president-report": archived("predsednik/2003/cir/2. 2003 izvestaj sa podacima o rezultatima pr.doc"),
    "2008-report-1": archived("predsednik/2008/cir/3. 2008 izvestaj o rezultatima pr (prvi krug).doc"),
    "2008-report-2": archived("predsednik/2008/cir/4. 2008 izvestaj o konacnim rezultatima pr (drugi krug).doc"),
    "2012-report-1": archived("predsednik/2012/cir/3. 2012 izvestaj o rezultatima pr 06.05.docx"),
    "2012-report-2": archived("predsednik/2012/cir/4. 2012 izvestaj o konacnim rezultatima pr 20.05.doc"),
    "2017-report": f"{CURRENT}/izbori-2017/dokumenta/{urllib.parse.quote('izvestaj o konacnim rezultatina izbora odrzanih 2. aprila 2017.doc')}",
    "2003-parliament-report": archived("poslanici/2003/CIR/2. izvestaj o rezultatima np.doc"),
    "2008-parliament-report": archived("poslanici/2008/cir/2. izvestaj o ukupnim rezultatima np.doc"),
    "2012-parliament-report": archived("poslanici/2012/CIR/2. izvestaj o ukupnim rezultatima np.docx"),
    "2014-parliament-report": archived("poslanici/2014/cir/2. Izvestaj o ukupnim rezultatima np.doc"),
    "2016-decision": f"{CURRENT}/izbori-2016/odluke/{urllib.parse.quote('odluka o dodeli mandata narodnih poslanika.doc')}",
    "2023-decision": f"{BASE}/extfile/sr/files/additionalDocuments/565/094/{urllib.parse.quote('RESENJE O DODELI MANDATA (2).pdf')}",
    "2026-decision": f"{BASE}/extfile/sr/files/additionalDocuments/822/554/{urllib.parse.quote('Rešenje o dodeli mandata narodnih poslanika Narodne skupštine.pdf')}",
    "2020-totals": xls(9419, "1 Ukupni rezultatic.XLS"),
    "2020-municipalities": xls(9407, "2 po opstinama1.XLS"),
    "2020-stations": xls(9428, "3 po bir mesrima1.XLS"),
    "2022-totals": xls(41895, "Tabela2_konacna_Nuts_.XLS"),
    "2022-stations": xls(41904, "Tabela3_konacna_GM_.XLS"),
    "2026-export": f"{BASE}/export-results/{ROUND_2026}/2",
}


def crawl_url(slug: str) -> str:
    return f"{RESULTS}#crawl-{slug}"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
PRESIDENCY = key("office", "president")
BODIES = {
    "parliament": key("body", "national-assembly"),
    "provincial": key("body", "vojvodina-assembly"),
    "local": key("body", "belgrade-assembly"),
}


def packages() -> list[str]:
    from ea.sources.rs import rik_2022, rik_poslanici, rik_predsednik, rik_referendum

    return [
        f"{SOURCE}/geo",
        f"{SOURCE}/registry",
        *rik_predsednik.packages(),
        *rik_poslanici.packages(),
        *rik_referendum.packages(),
        *rik_2022.packages(),
        *(f"{SOURCE}/{e.slug}" for e in ELECTIONS),
    ]


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    geo().write()
    registry().write()
    for election in ELECTIONS:
        build(election).write()
    from ea.sources.rs import rik_predsednik

    rik_predsednik.parse()
    from ea.sources.rs import rik_poslanici

    rik_poslanici.parse()
    from ea.sources.rs import rik_referendum

    rik_referendum.parse()
    from ea.sources.rs import rik_2022

    rik_2022.parse()


# fetching ---------------------------------------------------------------------------------------


def fetch() -> None:
    from ea.sources.rs import rik_predsednik

    rik_predsednik.fetch()
    from ea.sources.rs import rik_poslanici

    rik_poslanici.fetch()
    from ea.sources.rs import rik_referendum

    rik_referendum.fetch()
    from ea.sources.rs import rik_2022

    rik_2022.fetch()
    for name, url in FILES.items():
        for attempt in range(4):
            try:
                suffix = ".csv" if name.endswith("export") else "." + url.rsplit(".", 1)[1].lower()
                artifact = raw.fetch(SOURCE, url, suffix=suffix)
                if artifact.bytes == 0 or "html" in (artifact.media_type or ""):
                    raise RuntimeError(f"{url} was served as an empty or HTML page; remove its raw entry and run again")
                break
            except httpx.HTTPError as failure:
                print(f"{url}: {failure}", flush=True)
                time.sleep(3 * (attempt + 1))
        else:
            raise RuntimeError(f"{url} did not download")
    for slug in ("2026-10-25", *(s for s in APP if s != "2026-10-25")):
        try:
            raw.cached(SOURCE, crawl_url(slug))
        except LookupError:
            crawl(slug)


class App:
    """The results application of the Republic Electoral Commission, driven the way its own page drives it."""

    def __init__(self) -> None:
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "X-Requested-With": "XMLHttpRequest", "Referer": RESULTS},
            timeout=120,
            follow_redirects=True,
        )
        self.client.get(RESULTS).raise_for_status()

    def post(self, path: str, **data: object) -> dict[str, Any]:
        for attempt in range(6):
            try:
                response = self.client.post(f"{BASE}/{path}/", data={k: str(v) for k, v in data.items()})
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as failure:
                if attempt == 5:
                    raise RuntimeError(f"{path} {data}: {failure}") from failure
                time.sleep(2 * (attempt + 1))
        raise AssertionError

    def results(
        self, kind: int, round_id: int, region: int = 0, municipality: int = 0, station: int = 0
    ) -> dict[str, Any]:
        return self.post(
            "get_results",
            should_update_pies=1,
            type=kind,
            election_round=round_id,
            region=region,
            municipality=municipality,
            election_station=station,
        )


def _count(text: object) -> int | None:
    digits = re.sub(r"\D", "", str(text or ""))
    return int(digits) if digits else None


def _station(app: App, kind: int, round_id: int, place: dict[str, object]) -> dict[str, object]:
    reply = app.results(
        kind, round_id, int(str(place["region"])), int(str(place["municipality"])), int(str(place["id"]))
    )
    numbers = reply["stat_sum_numbers"]
    shares = reply["sum_config"]["data"]["datasets"][0]["data"]
    return {
        **place,
        "processed": numbers["processed_stations"],
        "registered": _count(numbers["total_voters"]),
        "voters": _count(numbers["available"]),
        "valid": shares[0],
        "invalid": shares[1],
        "stamp": numbers["datetime"],
        "lists": {int(row["count_number"]): _count(row["won_number"]) for row in reply["table_data"]},
    }


def crawl(slug: str) -> None:
    kind, round_id = APP[slug]
    app = App()
    totals = app.results(kind, round_id)
    places: list[dict[str, object]] = []
    regions = app.post("get-regions", election_type=kind, election_round=round_id)
    if slug == "2026-10-25":
        # the station tables come from the Commission's CSV export; the crawl keeps only the declared totals
        data: dict[str, object] = {"totals": totals, "regions": regions, "stations": []}
    else:
        for region, region_name in regions["regions"].items():
            code = regions["data_id"].get(region)
            reply = app.client.post(
                f"{BASE}/get-municipalities/",
                data={"election_type": kind, "election_round": round_id, "election_region": region},
            )
            for option in re.finditer(
                r'<option data-id="(\d*)" value="(\d+)">(.*?)</option>', reply.text.replace("\n", " ")
            ):
                municipality = option.group(2)
                stations = app.post(
                    "get-election-stations",
                    election_type=kind,
                    election_round=round_id,
                    election_region=region,
                    election_municipality=municipality,
                )["election_stations"]
                for station, name in stations.items():
                    places.append(
                        {
                            "region": region,
                            "region_name": region_name,
                            "region_code": code,
                            "municipality": municipality,
                            "municipality_code": option.group(1),
                            "municipality_name": " ".join(option.group(3).split()),
                            "id": station,
                            "name": " ".join(name.split()),
                        }
                    )
        print(f"{slug}: {len(places):,} polling stations to read", flush=True)
        with ThreadPoolExecutor(6) as pool:
            stations = list(pool.map(lambda place: _station(app, kind, round_id, place), places))
        data = {"totals": totals, "regions": regions, "stations": stations}
    raw.store(
        SOURCE,
        crawl_url(slug),
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode(),
        suffix=".json",
        retrieved_at=datetime.now(UTC),
        media_type="application/json",
    )


# reading -----------------------------------------------------------------------------------------


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def _sheet(url: str, sheet: str | None = None) -> pl.DataFrame:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    return book.load_sheet_by_name(sheet or book.sheet_names[0], header_row=0).to_polars()


def _grid(url: str, sheet: str | None = None) -> list[list[object]]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = book.load_sheet_by_name(sheet or book.sheet_names[0], header_row=None).to_polars()
    return [list(row) for row in frame.iter_rows()]


def _crawled(slug: str) -> dict:
    return json.loads(raw.cached(SOURCE, crawl_url(slug)).path.read_text())


def _int(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(float(str(value)))


@dataclass
class Station:
    okrug: str
    okrug_name: str
    opstina: str
    opstina_name: str
    number: int
    name: str | None
    registered: int | None
    voters: int | None
    cast: int | None
    invalid: int | None
    valid: int | None
    lists: dict[int, int | None]


@dataclass
class Table:
    stations: list[Station]
    labels: dict[int, str]  # ballot position -> list name (or the answer)
    declared: dict[int, int]  # measure -> national figure
    declared_lists: dict[int, int]
    seats: dict[int, int]
    files: list[str]
    outcomes: dict[int, str] = field(
        default_factory=dict
    )  # ballot position -> declared outcome of a presidential round


def _tidy_list(text: str) -> str:
    return clean(text.replace("\r", " ").replace("\n", " ")).strip(" -–")


def _from_xls(url: str, sheet: str, lists: int | None) -> tuple[list[Station], dict[int, str]]:
    frame = _sheet(url, sheet)
    head = frame.columns
    labels = {n + 1: _tidy_list(name) for n, name in enumerate(head[12:])}
    out = []
    for row in frame.iter_rows():
        out.append(
            Station(
                okrug=str(row[0]).strip().zfill(2),
                okrug_name=clean(row[1]),
                opstina=str(_int(row[2])),
                opstina_name=clean(row[3]),
                number=int(float(row[4])),
                name=None,
                registered=_int(row[5]),
                voters=_int(row[8]),
                cast=_int(row[9]),
                invalid=_int(row[10]),
                valid=_int(row[11]),
                lists={n + 1: _int(v) for n, v in enumerate(row[12:])},
            )
        )
    return out, labels


def _from_export() -> tuple[list[Station], dict[int, str]]:
    totals = _crawled("2026-10-25")["totals"]
    labels = {
        int(row["count_number"]): re.sub(r"^\d+\.\s*", "", clean(row["list_name"])) for row in totals["table_data"]
    }
    text = raw.cached(SOURCE, FILES["2026-export"]).path.read_text(encoding="utf-8-sig")
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        code = row["sifra_okruga_regiona"].split("/")[1]
        code = {"33000": ABROAD, "55001": PRISONS}.get(code, code)
        out.append(
            Station(
                okrug=code,
                okrug_name=clean(row["okrug_region"].split("/", 1)[1]) if "/" in row["okrug_region"] else "",
                opstina=row["sifra_opstine"],
                opstina_name=clean(row["opstina"]),
                number=int(row["bm_broj"]),
                name=clean(row["bm_naziv"]),
                registered=_int(row["upisani_biraci"]),
                voters=_int(row["glasali_biraci"]),
                cast=_int(row["listici_u_kutiji"]),
                invalid=_int(row["nevazeci_listici"]),
                valid=_int(row["vazeci_glasovi"]),
                lists={n: _int(row.get(f"lista_{n}")) for n in labels},
            )
        )
    return out, labels


def _declared_totals(file: str, votes: int, seats: int | None) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
    """Read the national table the Commission prints with every election: the counts, then each list's votes."""
    rows = _grid(FILES[file])
    figure = {
        str(r[0]).strip(): r[8] for r in rows if r[0] and str(r[0]).strip().rstrip(".").isdigit() and r[8] is not None
    }
    declared = {
        REGISTERED: _int(figure["4."]) or 0,
        VOTERS: _int(figure["5."]) or 0,
        CAST: _int(figure["9."]) or 0,
        INVALID: _int(figure["10."]) or 0,
        VALID: _int(figure["11."]) or 0,
    }
    lists: dict[int, int] = {}
    won: dict[int, int] = {}
    for r in rows:
        label = str(r[0]).strip() if r[0] is not None else ""
        if label.isdigit() and r[votes] is not None and r[2] is None:
            if label == "1" and lists:
                break
            lists[int(label)] = _int(r[votes]) or 0
            if seats is not None:
                won[int(label)] = _int(r[seats]) or 0
    return declared, lists, won


def _declared_2016() -> tuple[dict[int, int], dict[int, int]]:
    url = FILES["2016-municipalities"]
    national = next(r for r in _grid(url, "Tabela21") if clean(r[0]) == "РЕПУБЛИКА СРБИЈА")
    declared = {
        REGISTERED: _count(national[5]) or 0,
        VOTERS: _int(national[6]) or 0,
        CAST: _int(national[10]) or 0,
        INVALID: _int(national[11]) or 0,
        VALID: _int(national[13]) or 0,
    }
    row = next(r for r in _grid(url, "Tabela22") if clean(r[0]) == "РЕПУБЛИКА СРБИЈА")
    return declared, {
        n + 1: _int(row[2 + 2 * n]) or 0
        for n in range(len(row))
        if 2 + 2 * n < len(row) and row[2 + 2 * n] is not None and n < 40
    }


def _declared_2022() -> tuple[dict[int, int], dict[int, int]]:
    grid = _grid(FILES["2022-totals"])
    row = next(r for r in grid if clean(r[0]) == "РЕПУБЛИКА СРБИЈА")
    declared = {
        REGISTERED: _int(row[4]) or 0,
        VOTERS: _int(row[5]) or 0,
        CAST: _int(row[9]) or 0,
        INVALID: _int(row[10]) or 0,
        VALID: _int(row[12]) or 0,
    }
    return declared, {1: _int(row[14]) or 0, 2: _int(row[16]) or 0}


def _declared_app(totals: dict) -> tuple[dict[int, int], dict[int, int]]:
    numbers = totals["stat_sum_numbers"]
    valid, invalid = totals["sum_config"]["data"]["datasets"][0]["data"]
    declared = {
        REGISTERED: _count(numbers["total_voters"]) or 0,
        VOTERS: _count(numbers["available"]) or 0,
        INVALID: invalid,
        VALID: valid,
    }
    return declared, {int(r["count_number"]): _count(r["won_number"]) or 0 for r in totals["table_data"]}


def _norm(text: object) -> str:
    words = [w for w in re.split(r"[^\w]+", clean(text).lower()) if w and w not in {"град", "градска", "општина"}]
    return "".join(words)


# names the archive's older tables print that two municipalities share, and the one each means
ALIASES = {"палилула": "70203", "новисад": "89010", "петровац": "70912"}
# municipalities that appear in older tables only, with their okrug
OLDER = {"70858": ("20", "Град Ниш"), "90247": ("25", "Подујево")}


def _match(name: str) -> str:
    key = _norm(name)
    if key in ALIASES:
        return ALIASES[key]
    head, _, tail = name.partition(" - ")
    if tail and _norm(head) == "ниш":
        return _by_name().get(_norm(f"{tail} Ниш")) or _by_name().get(_norm(tail), "")
    return _by_name().get(key, "")


@functools.cache
def _by_name() -> dict[str, str]:
    found: dict[str, set[str]] = {}
    for code, name in gazetteer()[2].items():
        found.setdefault(_norm(name), set()).add(code)
    return {name: next(iter(codes)) for name, codes in found.items() if len(codes) == 1}


# the totals these elections' own tables leave out, as the Commission printed them in its report of results
REPORTED: dict[str, tuple[str, dict[int, int], list[int]]] = {
    "2004-06-13-president": (
        "2004-report-1",
        {REGISTERED: 6532263, VOTERS: 3119087, INVALID: 38047, VALID: 3081040},
        [11796, 16795, 125952, 16907, 5785, 13980, 5546, 62737, 568691, 414971, 5442, 954339, 14317, 10198, 853584],
    ),
    "2004-06-27-president": (
        "2004-report-2",
        {REGISTERED: 6532940, VOTERS: 3158571, INVALID: 42975, VALID: 3115596},
        [1434068, 1681528],
    ),
    "2007-01-21": (
        "2007-report",
        {REGISTERED: 6653851, VOTERS: 4029286, CAST: 4028492, INVALID: 65405, VALID: 3963087},
        [],
    ),
}


def _from_layout(e: Election) -> Table:
    assert e.layout is not None
    lay = e.layout
    grid = _grid(FILES[lay.file], lay.sheet)

    def cell(row: list[object], column: int | None) -> object:
        return row[column] if column is not None and column < len(row) else None

    def figure(row: list[object], column: int | None) -> int | None:
        value = cell(row, column)
        try:
            return _int(value)
        except ValueError:
            return None

    labels: dict[int, str] = {}
    columns: dict[int, int] = {}
    column = lay.lists
    while column < len(grid[lay.header]) and clean(grid[lay.header][column]):
        number = len(labels) + 1
        name = _tidy_list(str(grid[lay.header][column]))
        labels[number] = re.sub(r"\s+-\s+broj osvojenih.*$", "", name) if e.family == "president" else name
        columns[number] = column
        column += lay.step
    stations: list[Station] = []
    zone = ""
    for row in grid[lay.first :]:
        first = clean(cell(row, lay.name)) if lay.name is not None else ""
        if lay.name is not None and not figure(row, lay.number) and first:
            low = first.lower()
            zone = "abroad" if low.startswith("иностран") else "prison" if low.startswith("завод") else zone
        number = figure(row, lay.number)
        if number is not None and not first and lay.unnamed and zone:
            zone = lay.unnamed
            first = "Заводи за извршење заводских санкција"
        if number is None and first and any(first.startswith(leaf) for leaf in lay.leaves):
            low = first.lower()
            group = ABROAD if low.startswith("иностран") else PRISONS if low.startswith("завод") else "GROUP"
            by = dict(lay.figures)
            stations.append(
                Station(
                    okrug=group,
                    okrug_name="",
                    opstina=f"~{_norm(first)}",
                    opstina_name=first,
                    number=0,
                    name=first,
                    registered=figure(row, by.get(REGISTERED)),
                    voters=figure(row, by.get(VOTERS)),
                    cast=figure(row, by.get(CAST)),
                    invalid=figure(row, by.get(INVALID)),
                    valid=figure(row, by.get(VALID)),
                    lists={n: figure(row, c) for n, c in columns.items()},
                )
            )
            continue
        if (
            number is None
            or (lay.name is not None and not first)
            or (lay.code is not None and not clean(cell(row, lay.code)))
        ):
            continue
        if lay.code is not None:
            opstina = str(_int(cell(row, lay.code)))
            okrug = clean(cell(row, lay.okrug)) if lay.okrug is not None else ""
            okrug = okrug or ("33" if opstina.startswith("33") else "55" if opstina.startswith("55") else "")
            name = opstina
        else:
            opstina = _match(first)
            okrug = ABROAD if zone == "abroad" else PRISONS if zone == "prison" else ""
            name = first
        stations.append(
            Station(
                okrug=okrug if okrug in (ABROAD, PRISONS) else "",
                okrug_name="",
                opstina=opstina or f"~{_norm(name)}",
                opstina_name=name,
                number=number,
                name=clean(cell(row, lay.label)) or None if lay.label is not None else None,
                registered=None,
                voters=None,
                cast=None,
                invalid=None,
                valid=None,
                lists={n: figure(row, c) for n, c in columns.items()},
            )
        )
        by = dict(lay.figures)
        stations[-1].registered = figure(row, by.get(REGISTERED))
        stations[-1].voters = figure(row, by.get(VOTERS))
        stations[-1].cast = figure(row, by.get(CAST))
        stations[-1].invalid = figure(row, by.get(INVALID))
        stations[-1].valid = figure(row, by.get(VALID))
    declared: dict[int, int] = {}
    declared_lists: dict[int, int] = {}
    if lay.national is not None:
        row = grid[lay.national]
        declared = {m: v for m, c in lay.figures if (v := figure(row, c)) is not None}
        declared_lists = {n: v for n, c in columns.items() if (v := figure(row, c)) is not None}
    files = [lay.file]
    if e.slug in REPORTED:
        report, declared, votes = REPORTED[e.slug]
        declared_lists = dict(enumerate(votes, 1))
        files.append(report)
    return Table(stations, labels, declared, declared_lists, {}, files)


def table(e: Election, places: dict[str, tuple[str, str]] | None = None) -> Table:
    """The election's stations and declared totals, with the seats and outcomes its reports declare."""
    from ea.sources.rs import rik_seats

    t = _table(e, places)
    if e.family == "parliament" and not t.seats:
        votes = dict(t.declared_lists)
        for s in t.stations:
            for number, count in s.lists.items():
                if count is not None and number not in t.declared_lists:
                    votes[number] = votes.get(number, 0) + count
        found = rik_seats.seats(e.slug, t.labels, votes)
        if found:
            t.seats, name = found
            t.files.append(name)
            if sum(t.seats.values()) != e.seats:
                raise ValueError(
                    f"{e.slug}: the seats read from {name} add up to {sum(t.seats.values())}, not {e.seats}"
                )
    if e.family == "president":
        outcomes = rik_seats.presidential(e.slug, t.labels)
        if outcomes:
            t.outcomes, name = outcomes
            if name not in t.files:
                t.files.append(name)
        elif e.slug in rik_seats.UNSUCCESSFUL and rik_seats.UNSUCCESSFUL[e.slug] not in t.files:
            t.files.append(rik_seats.UNSUCCESSFUL[e.slug])
    return t


def _table(e: Election, places: dict[str, tuple[str, str]] | None = None) -> Table:
    if e.layout is not None:
        return _from_layout(e)
    if e.slug == "2020-06-21":
        stations, labels = _from_xls(FILES["2020-stations"], "Sheet1", 20)
        declared, votes, seats = _declared_totals("2020-totals", 6, 8)
        files = ["2020-totals", "2020-municipalities", "2020-stations"]
        return Table(stations, labels, declared, votes, seats, files)
    if e.slug == "2016-04-24":
        stations, labels = _from_xls(FILES["2016-stations"], "Sheet1", None)
        declared, votes = _declared_2016()
        return Table(stations, labels, declared, votes, {}, ["2016-stations", "2016-municipalities"])
    if e.slug == "2017-04-02-president":
        stations, labels = _from_xls(FILES["2017-stations"], "Sheet1", None)
        declared, votes, _ = _declared_totals("2017-totals", 8, None)
        return Table(stations, labels, declared, votes, {}, ["2017-stations", "2017-totals"])
    if e.slug == "2022-01-16":
        stations, _ = _from_xls(FILES["2022-stations"], "Tabela3", None)
        for s in stations:
            s.lists = {1: s.lists[1], 2: s.lists[2]}
        declared, votes = _declared_2022()
        return Table(stations, {1: "ДА", 2: "НЕ"}, declared, votes, {}, ["2022-totals", "2022-stations"])
    if e.slug == "2026-10-25":
        stations, labels = _from_export()
        declared, votes = _declared_app(_crawled(e.slug)["totals"])
        return Table(stations, labels, declared, votes, {}, ["2026-export", f"crawl-{e.slug}"])
    assert places is not None
    data = _crawled(e.slug)
    labels = {
        int(r["count_number"]): re.sub(r"^\d+\.?\s*", "", clean(r["list_name"])) for r in data["totals"]["table_data"]
    }
    stations = []
    for s in data["stations"]:
        if s["processed"] not in (1, "1"):
            continue
        code = s["municipality_code"]
        okrug, okrug_name = places[code]
        stations.append(
            Station(
                okrug=okrug,
                okrug_name=okrug_name,
                opstina=code,
                opstina_name=clean(s["municipality_name"]),
                number=int(s["name"].split(" - ", 1)[0]),
                name=clean(s["name"].split(" - ", 1)[1]) if " - " in s["name"] else None,
                registered=s["registered"],
                voters=s["voters"],
                cast=None,
                invalid=s["invalid"],
                valid=s["valid"],
                lists={int(n): v for n, v in s["lists"].items()},
            )
        )
    declared, votes = _declared_app(data["totals"])
    return Table(stations, labels, declared, votes, {}, [f"crawl-{e.slug}"])


# places ------------------------------------------------------------------------------------------


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def unit_code(e: Election, s: Station) -> str:
    return f"{e.day}:{s.opstina}:{s.number}"


@functools.cache
def gazetteer() -> tuple[dict[str, str], dict[str, tuple[str, str]], dict[str, str]]:
    """Okrug names, each opština's okrug, and each opština's name, newest source first."""
    okruzi: dict[str, str] = {}
    opstine: dict[str, tuple[str, str]] = {}
    names: dict[str, str] = {}
    sources = (
        _from_export()[0],
        _from_xls(FILES["2020-stations"], "Sheet1", 20)[0],
        _from_xls(FILES["2022-stations"], "Tabela3", None)[0],
        _from_xls(FILES["2016-stations"], "Sheet1", None)[0],
        _from_xls(FILES["2017-stations"], "Sheet1", None)[0],
    )
    for stations in sources:
        for s in stations:
            okruzi.setdefault(s.okrug, s.okrug_name)
            opstine.setdefault(s.opstina, (s.okrug, s.okrug_name))
            names.setdefault(s.opstina, s.opstina_name)
    for code, (okrug, name) in OLDER.items():
        opstine.setdefault(code, (okrug, okruzi.get(okrug, "")))
        names.setdefault(code, name)
    # the Commission's export names every okrug as "region/okrug"; older tables name the okrug alone
    return okruzi, opstine, names


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Serbia's administrative districts and municipalities, and the scheme of polling stations, as the "
        "Republic Electoral Commission names them",
    )
    p.external.add(SERBIA)
    for code, rank, kind, noun in (
        ("rs.okrug", 1, "administrative", "administrative district"),
        ("rs.opstina", 2, "administrative", "municipality"),
        ("rs.biracko_mesto", 3, "precinct", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=SERBIA, kind=kind, rank=rank, noun=noun, code_system=None)
    okruzi, opstine, names = gazetteer()
    special = {ABROAD, PRISONS}
    for code, name in sorted(okruzi.items()):
        if code in special:
            continue
        id = area("rs.okrug", code)
        p.add("area", id=id, scheme_code="rs.okrug")
        p.add("area_code", area_id=id, code=code)
        p.name(id, name, "sr", "official")
    for code, (okrug, _) in sorted(opstine.items()):
        if okrug in special:
            continue
        id = area("rs.opstina", code)
        p.add("area", id=id, scheme_code="rs.opstina")
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=area("rs.okrug", okrug))
        p.name(id, names[code], "sr", "official")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The legislatures Serbia's Republic Electoral Commission publishes results for, their electoral systems, and the "
        "parties and coalitions behind the lists",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(SERBIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Републичка изборна комисија",
        title="Results of elections and referendums, Republic Electoral Commission of Serbia",
        url=BASE,
        license=LICENSE,
        license_url="https://creativecommons.org/licenses/by-nc-nd/3.0/rs/",
        attribution="Републичка изборна комисија, rik.parlament.gov.rs",
    )
    for url in [*FILES.values(), *(crawl_url(s) for s in APP)]:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    for family, kind, en, sr in (
        ("parliament", "unicameral", "National Assembly", "Народна скупштина"),
        ("provincial", "unicameral", "Assembly of Vojvodina", "Скупштина Аутономне Покрајине Војводине"),
        ("local", "council", "Belgrade City Assembly", "Скупштина града Београда"),
    ):
        p.add("body", id=BODIES[family], polity_id=SERBIA, kind=kind)
        p.name(BODIES[family], en, "en", "official")
        p.name(BODIES[family], sr, "sr", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=SERBIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Republic of Serbia", "en", "official")
    p.name(PRESIDENCY, "Председник Републике Србије", "sr", "native", False)
    for id, rules, method in SYSTEMS:
        p.add("electoral_system", id=id, polity_id=SERBIA, valid_from=None, valid_to=None, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=method, lang="en")
    p.add(
        "source",
        id=COLORS_SOURCE,
        kind="community",
        publisher="electionsarchive.com",
        title="Brand colors of Serbian parties, taken from their logos",
        url="https://electionsarchive.com",
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="electionsarchive.com",
    )
    labels = {e.slug: table(e, gazetteer()[1]).labels for e in ELECTIONS if e.slug in rik_parties.LISTS}
    rik_parties.add_parties(p, labels, SERBIA, COLORS_SOURCE)
    return p


COLORS_SOURCE = key("source", "rs.rik.party_colors")


def party(slug: str, number: int) -> str | None:
    """The party row of a list, for the elections whose lists have one."""
    return key("party", rik_parties.party_key(slug, number)) if slug in rik_parties.LISTS else None


SYSTEMS: list[tuple[str, dict[str, object], str]] = [
    (
        key("electoral_system", "parliament"),
        {
            "formula": "dhondt",
            "threshold": {"level": "list", "scope": "national", "percent": 3},
            "lists": {"order": "closed", "vacancy": "next_on_list"},
        },
        (
            "All 250 members of the National Assembly are elected from one nationwide constituency by D'Hondt among "
            "the lists that win at least 3% of the valid votes; lists of national minorities are exempt from the "
            "threshold. The list holder assigns seats to candidates in any order. Until the 2020 election the "
            "threshold was 5%."
        ),
    ),
    (
        key("electoral_system", "assembly"),
        {"formula": "dhondt", "lists": {"order": "closed", "vacancy": "next_on_list"}},
        (
            "Members are elected by D'Hondt from closed lists in one constituency. The threshold the law sets for the "
            "assembly is not recorded here."
        ),
    ),
    (
        key("electoral_system", "referendum"),
        {"formula": "referendum"},
        "A national referendum carries when a majority of the voters who take part vote for the question.",
    ),
]
SYSTEMS.append(
    (
        key("electoral_system", "president"),
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        (
            "The president is elected directly. A candidate who wins a majority of the votes cast is elected; otherwise "
            "the two leading candidates go to a second round two weeks later. Until 2004 the law also required that "
            "half of the registered voters take part, and several elections failed on that ground."
        ),
    )
)
SYSTEM_OF = {
    "president": SYSTEMS[3][0],
    "parliament": SYSTEMS[0][0],
    "provincial": SYSTEMS[1][0],
    "local": SYSTEMS[1][0],
    "referendum": SYSTEMS[2][0],
}


# elections ---------------------------------------------------------------------------------------


def _path(e: Election) -> str:
    suffix = {
        "parliament": "general",
        "referendum": "referendum",
        "provincial": "provincial",
        "local": "local",
        "president": "presidential",
    }
    return f"rs/{e.day}-{suffix[e.family]}" + ("-belgrade" if e.family == "local" else "-runoff" if e.round > 1 else "")


def build(e: Election) -> Package:
    from ea.sources.rs import rik_seats

    p = Package(
        f"{SOURCE}/{e.slug}",
        SOURCE,
        f"The {e.title[:4]} {e.title[5:]} by polling station from the Republic Electoral Commission",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", *([f"{SOURCE}/{e.previous}"] if e.previous else [])],
    )
    p.external.add(SERBIA)
    t = table(e, gazetteer()[1])
    for name in t.files:
        url = crawl_url(name.removeprefix("crawl-")) if name.startswith("crawl-") else FILES[name]
        p.uses(raw.cached(SOURCE, url))
    election = key("election", e.slug)
    path = _path(e)
    p.add("election", id=election, polity_id=SERBIA, kind=e.kind, date_from=e.day, date_to=e.day, status="held")
    p.name(election, e.title, "en", "official")
    p.name(election, e.native, "sr", "native", False)
    p.add("slug", path=path, entity_id=election, is_current=True)
    referendum = e.family == "referendum"
    president = e.family == "president"
    contest = key("contest", e.slug)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="ballot_question" if referendum else "office" if president else "list_allocation",
        office_id=PRESIDENCY if president else None,
        body_id=None if referendum or president else BODIES[e.family],
        magnitude=e.seats,
        round_number=e.round,
        previous_round_id=key("contest", e.previous) if e.previous else None,
        electoral_system_id=SYSTEM_OF[e.family],
        default_result_kind="counted",
    )
    p.name(contest, e.title, "en", "official")
    leaf = "question" if referendum else "president" if president else "assembly"
    p.add("slug", path=f"{path}/{leaf}", entity_id=contest, is_current=True)
    if referendum:
        p.add("ballot_question", contest_id=contest, type="mandatory_referendum")
    result_set = key("result_set", e.slug)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = e.method
    if e.seats and e.family == "parliament" and t.seats:
        method += (
            " Seats are as the Republic Electoral Commission declared them for each list, in its report of results or "
            "its decision allocating the seats; a list with no seats has no outcome."
        )
    if t.outcomes:
        method += (
            " Outcomes are as the Republic Electoral Commission declared them in its report of results: the candidates "
            "who go on to the next round, or the candidate elected."
        )
    elif e.slug in rik_seats.UNSUCCESSFUL:
        method += (
            " The Commission recorded that too few voters came out, so the election was unsuccessful and no one was "
            "elected; no outcome is declared for any candidate."
        )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    options: dict[int, str] = {}
    for number, label in sorted(t.labels.items()):
        option = options[number] = key("option", e.slug, number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="answer" if referendum else "candidate" if president else "list",
            ballot_position=number,
            ballot_label=label,
            party_id=party(e.slug, number),
        )
        if referendum:
            p.name(option, {1: "Yes", 2: "No"}[number], "en", "official")
        if president:
            person = key("person", e.slug, number)
            name = re.sub(r"\s*\([^)]*\)\s*$", "", re.sub(r"(?<=[а-яa-z])-(?=[а-яa-z])", "", label))
            p.add("person", id=person, birth_precision=None)
            p.name(person, name, "sr", "official")
            p.add(
                "candidacy",
                id=key("candidacy", e.slug, number),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=label,
            )
    if referendum:
        yes, no = t.declared_lists[1], t.declared_lists[2]
        p.add("outcome", result_set_id=result_set, option_id=options[1], kind="won" if yes > no else "lost")
        p.add("outcome", result_set_id=result_set, option_id=options[2], kind="won" if no > yes else "lost")
    for number, seats in t.seats.items():
        if seats:
            p.add("outcome", result_set_id=result_set, option_id=options[number], kind="won", seats=seats)
    for number, kind in t.outcomes.items():
        p.add("outcome", result_set_id=result_set, option_id=options[number], kind=kind)
    for measure, value in t.declared.items():
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for number, value in t.declared_lists.items():
        p.add("declared", result_set_id=result_set, option_id=options[number], measure=VOTES, value=value)
    rows: list[tuple[str, str | None, int, int]] = []
    seen: set[str] = set()
    for s in t.stations:
        code = unit_code(e, s)
        if code in seen:
            raise ValueError(f"{e.slug}: polling station {code} appears twice")
        seen.add(code)
        unit = key("unit", code)
        if s.okrug == ABROAD:
            relation, place, kind = "unlocated", None, "abroad_mission"
        elif s.okrug == PRISONS:
            relation, place, kind = "unlocated", None, "prison"
        elif s.okrug == "GROUP":
            relation, place, kind = "unlocated", None, "aggregate"
        elif s.opstina not in gazetteer()[2]:
            relation, place, kind = "unlocated", None, "standard"
        else:
            relation, place, kind = "within", area("rs.opstina", s.opstina), "standard"
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=SERBIA,
            scheme_code="rs.biracko_mesto",
            code=code,
            area_relation=relation,
            area_id=place,
            kind=kind,
        )
        if s.name:
            p.name(unit, s.name, "sr", "official")
        for measure, value in (
            (REGISTERED, s.registered),
            (VOTERS, s.voters),
            (CAST, s.cast),
            (INVALID, s.invalid),
            (VALID, s.valid),
        ):
            if value is not None:
                rows.append((unit, None, measure, value))
        for number, votes in s.lists.items():
            if votes is not None and number in options:
                rows.append((unit, options[number], VOTES, votes))
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": [result_set] * len(rows),
                "unit_id": [r[0] for r in rows],
                "option_id": [r[1] for r in rows],
                "channel": [0] * len(rows),
                "measure": [r[2] for r in rows],
                "count_round": [0] * len(rows),
                "flags": [0] * len(rows),
                "value": [r[3] for r in rows],
            },
            schema_overrides={"option_id": pl.String},
        ),
    )
    difference = _compare(e, t)
    if difference:
        p.accept(
            "accounting.declared",
            "The Commission's printed national totals differ from the sum of the polling-station rows in its own "
            "tables (station sum against printed): " + difference,
        )
    odd = [s for s in t.stations if s.cast is not None and s.valid is not None and s.valid + (s.invalid or 0) != s.cast]
    if odd:
        shown = "; ".join(
            f"{unit_code(e, s)} (valid {s.valid} plus invalid {s.invalid or 0} against {s.cast} in the box)"
            for s in odd[:5]
        )
        p.accept(
            "accounting.ballots_sum",
            f"{len(odd)} polling station(s) where the Commission's table prints valid plus invalid ballots that "
            f"differ from the ballots in the box; stored as printed: {shown}",
        )
    return p


def _compare(e: Election, t: Table) -> str | None:
    """Report how the station rows add up against the totals the Commission declares for the election."""
    got: Counter[int] = Counter()
    lists: Counter[int] = Counter()
    for s in t.stations:
        for measure, value in (
            (REGISTERED, s.registered),
            (VOTERS, s.voters),
            (CAST, s.cast),
            (INVALID, s.invalid),
            (VALID, s.valid),
        ):
            got[measure] += value or 0
        for number, votes in s.lists.items():
            lists[number] += votes or 0
    names = {REGISTERED: "registered", VOTERS: "voters", CAST: "ballots cast", INVALID: "invalid", VALID: "valid"}
    off = [f"{names[m]} {got[m]:,} against {v:,}" for m, v in t.declared.items() if got[m] != v]
    off += [f"list {n} {lists[n]:,} against {v:,}" for n, v in t.declared_lists.items() if lists[n] != v]
    print(f"{e.slug}: {len(t.stations):,} stations, registered {got[REGISTERED]:,}, valid {got[VALID]:,}", flush=True)
    if not off:
        return None
    print(f"{e.slug}: station sums differ from the declared totals: {'; '.join(off)}", flush=True)
    agree = [names[m] for m, v in t.declared.items() if got[m] == v]
    agree += [f"list {n}" for n, v in t.declared_lists.items() if lists[n] == v]
    return "; ".join(off) + ("; printed figures that equal the station sums: " + ", ".join(agree) if agree else "")


# the archive's elections ----------------------------------------------------------------------------

METHOD_LEGACY = (
    "Counted by the polling board at each polling station (biračko mesto) and tabulated for the Republic Electoral "
    "Commission by the Republic Statistical Office, as published in the Commission's archive. Registered voters are the "
    "voter register, voters are those who turned out, ballots cast are the ballots found in the box, and valid votes "
    "are the valid ballots. Only the figures the table prints are stored."
)
FIGURES_FULL = ((REGISTERED, 2), (VOTERS, 3), (CAST, 7), (INVALID, 8), (VALID, 10))


def _sheet2012(name: str, file: str) -> Layout:
    return Layout(file, name, 1, 4, 1, FIGURES_FULL, 12, 2, national=3)


def _plain(file: str, sheet: str) -> Layout:
    return Layout(file, sheet, 2, 4, 1, ((REGISTERED, 3), (VOTERS, 4)), 6, 1, 0, None, None, 2, 3)


def _president(day: date, round: int, title: str, native: str, layout: Layout, previous: str | None) -> Election:
    return Election(
        f"{day}-president",
        day,
        "general" if round == 1 else "runoff",
        "president",
        title,
        native,
        1,
        METHOD_LEGACY,
        layout,
        round,
        previous,
    )


def _legacy() -> list[Election]:
    def parliament(day: date, layout: Layout, slug: str | None = None) -> Election:
        return Election(
            slug or f"{day}",
            day,
            "general",
            "parliament",
            f"{day.year} Serbian parliamentary election",
            f"Избори за народне посланике Народне скупштине {day.year}.",
            250,
            METHOD_LEGACY,
            layout,
        )

    out = [
        parliament(
            date(2003, 12, 28),
            Layout("2003-parliament", "zadcf", 2, 4, 1, ((REGISTERED, 3), (VOTERS, 4)), 6, 1, 0, None, None, 2, 3),
        ),
        parliament(
            date(2007, 1, 21),
            Layout(
                "2007-parliament",
                "za_CD",
                1,
                3,
                3,
                ((REGISTERED, 7), (VOTERS, 8), (CAST, 9), (INVALID, 10), (VALID, 11)),
                12,
                1,
                None,
                1,
                0,
            ),
        ),
        parliament(
            date(2008, 5, 11),
            Layout(
                "2008-parliament",
                "IzboriMaj2008",
                0,
                2,
                1,
                ((REGISTERED, 5), (VOTERS, 9), (CAST, 6), (INVALID, 7), (VALID, 8)),
                10,
                1,
                0,
                None,
                None,
                2,
                2,
                (
                    "Иностранство",
                    "Заводи за извршење заводских санкција",
                    "Интерно расељени бирачи са КиМ",
                    "Интерно расељени бирачи унутар КиМ",
                ),
            ),
        ),
        parliament(date(2012, 5, 6), _sheet2012("Парламентарни", "2012-parliament")),
        parliament(date(2014, 3, 16), _sheet2012("Резултати ", "2014-parliament")),
    ]

    def runoff(first: date, second: date, year: int, layout: Layout) -> None:
        out.append(
            _president(
                second,
                2,
                f"{year} Serbian presidential election, second round",
                f"Избори за председника Републике Србије {year}, други круг.",
                layout,
                f"{first}-president",
            )
        )

    def first_round(day: date, year: int, layout: Layout, suffix: str = "") -> None:
        out.append(
            _president(
                day,
                1,
                f"{year} Serbian presidential election{suffix}",
                f"Избори за председника Републике Србије {year}.",
                layout,
                None,
            )
        )

    sep, oct_ = date(2002, 9, 29), date(2002, 10, 13)
    first_round(sep, 2002, _plain("2002-president", "Први круг"), ", first round")
    runoff(sep, oct_, 2002, _plain("2002-president", "Други круг "))
    first_round(date(2002, 12, 8), 2002, _plain("2002-president", "Децембар"), ", repeat vote of December")
    first_round(date(2003, 11, 16), 2003, _plain("2003-president", "Izbori16.11.03"))
    latin = ((REGISTERED, 4), (VOTERS, 5), (CAST, 6), (INVALID, 7), (VALID, 8))
    jun13, jun27 = date(2004, 6, 13), date(2004, 6, 27)
    first_round(jun13, 2004, Layout("2004-president", "prvi krug", 2, 4, 1, latin, 9, 1, None, 0), ", first round")
    runoff(jun13, jun27, 2004, Layout("2004-president", "drugi krug", 2, 3, 1, latin, 9, 1, None, 0))
    figures08 = ((REGISTERED, 3), (VOTERS, 4), (CAST, 6))
    jan20, feb3 = date(2008, 1, 20), date(2008, 2, 3)
    layout08 = Layout("2008-president", "Први круг", 2, 4, 1, figures08, 8, 1, 0, None, None, 2, 3, unnamed="prison")
    first_round(jan20, 2008, layout08, ", first round")
    runoff(
        jan20,
        feb3,
        2008,
        Layout("2008-president", "Други круг", 2, 4, 1, figures08, 8, 1, 0, None, None, 2, 3),
    )
    may6, may20 = date(2012, 5, 6), date(2012, 5, 20)
    first_round(may6, 2012, _sheet2012("Председнички - први круг", "2012-president"), ", first round")
    runoff(may6, may20, 2012, _sheet2012("Председнички - други круг", "2012-president"))
    return out


ELECTIONS += _legacy()
ELECTIONS += [
    Election(
        "2016-04-24",
        date(2016, 4, 24),
        "general",
        "parliament",
        "2016 Serbian parliamentary election",
        "Избори за народне посланике Народне скупштине 2016.",
        250,
        METHOD_LEGACY,
    ),
    Election(
        "2017-04-02-president",
        date(2017, 4, 2),
        "general",
        "president",
        "2017 Serbian presidential election",
        "Избори за председника Републике Србије 2017.",
        1,
        METHOD_LEGACY,
    ),
]
ELECTIONS.sort(key=lambda e: (e.day, e.round, e.slug))
