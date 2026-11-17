import functools
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "my.spr"
PORTAL = "https://opendata.spr.gov.my"
CATALOG = f"{PORTAL}/data/_index.json"
FILES = {"pru": "keputusan-pru", "dun": "keputusan-pru-dun", "prk": "keputusan-prk"}
ROLL = "daftar-pemilih-induk-2012-2025"
CENTRES = "dppr-pru-prn-prk-2008-keatas"
# The 2013 and 2018 general election files print no electorate. The master roll gives one for each polling district,
# and summing a constituency's districts fills the gap. The 2012 roll is the one under the constituencies of 2013, and
# the 2018 roll the one under the constituencies redrawn for 2018; each reproduces the printed turnout of its
# election to within a fraction of a point, where the neighboring year's roll does not.
ROLL_YEARS = {"2013-05-05": 2012, "2018-05-09": 2018}
MALAYSIA = str(uuid5(package.NAMESPACE, "polity\x1fMY"))
VOTES, REGISTERED, INVALID = 1, 2, 5

STATES = {
    "JOHOR": "Johor",
    "KEDAH": "Kedah",
    "KELANTAN": "Kelantan",
    "MELAKA": "Melaka",
    "NEGERI SEMBILAN": "Negeri Sembilan",
    "PAHANG": "Pahang",
    "PERAK": "Perak",
    "PERLIS": "Perlis",
    "PULAU PINANG": "Penang",
    "SABAH": "Sabah",
    "SARAWAK": "Sarawak",
    "SELANGOR": "Selangor",
    "TERENGGANU": "Terengganu",
    "W.P KUALA LUMPUR": "Kuala Lumpur",
    "W.P LABUAN": "Labuan",
    "W.P PUTRAJAYA": "Putrajaya",
}
# Kuala Lumpur, Labuan, and Putrajaya have no state assembly.
ASSEMBLIES = [state for state in STATES if not state.startswith("W.P")]
BLOCS = {"BN", "PH", "PN", "GPS", "GRS"}
# Rows the source files under another party's abbreviation: Parti Sosialis Malaysia candidates of 2018 carry PH.
PARTY_FIXES = {("PH", "PARTI SOSIALIS MALAYSIA (BARU)"): "PSM"}
# Two abbreviations the source gives one party: the Bersama Malaysia party appears as both MU and BERSAMA.
ALIASES = {"MU": "BERSAMA"}
# The 2013 file gives the status of a candidate who stood for both a parliamentary and a state seat to both
# contests alike. In each of these five contests the status contradicts the printed votes: the winner is the
# candidate with the most votes, and in the parliamentary contests the printed majority equals that gap.
STATUS_FIXES = {
    (2013, "SABAH", "P.170", "DATUK SERI PANGLIMA WILFRED M. BUMBURING"): "KLH",
    (2013, "SABAH", "N.09", "DATUK SERI PANGLIMA WILFRED M. BUMBURING"): "MNG",
    (2013, "SABAH", "P.180", "DATUK DR. JEFFREY KITINGAN"): "KLH",
    (2013, "SABAH", "N.33", "DATUK DR. JEFFREY KITINGAN"): "MNG",
    (2013, "SABAH", "N.25", "LAJIM BIN UKIN"): "MNG",
}
WINNERS = {"MNG", "MENANG", "MTB"}
LOSERS = {"KLH", "HD", "KALAH", "HILANG DEPOSIT"}

# The open data files carry the year of each election but not its day. Days are given only where the election's
# polling day is a matter of public record the files name; every other election is dated to its year.
GENERAL_DAYS = {2008: date(2008, 3, 8), 2013: date(2013, 5, 5), 2018: date(2018, 5, 9), 2022: date(2022, 11, 19)}
STATE_DAYS = {
    "PILIHAN RAYA UMUM DEWAN UNDANGAN NEGERI SARAWAK KE-11": date(2016, 5, 7),
    "PILIHAN RAYA UMUM DUN SABAH KE 16": date(2020, 9, 26),
    "PILIHAN RAYA UMUM DUN MELAKA KE 15": date(2021, 11, 20),
    "PILIHAN RAYA UMUM DUN SARAWAK KE 12": date(2021, 12, 18),
    "PILIHAN RAYA UMUM DUN JOHOR KE 15": date(2022, 3, 12),
    "PILIHAN RAYA UMUM DEWAN NEGERI KEDAH KE-15": date(2023, 8, 12),
    "PILIHAN RAYA UMUM DEWAN NEGERI KELANTAN KE-15": date(2023, 8, 12),
    "PILIHAN RAYA UMUM DEWAN NEGERI NEGERI SEMBILAN KE-15": date(2023, 8, 12),
    "PILIHAN RAYA UMUM DEWAN NEGERI SELANGOR KE-15": date(2023, 8, 12),
    "PILIHAN RAYA UMUM DEWAN UNDANGAN NEGERI TERENGGANU KE-15": date(2023, 8, 12),
    "PILIHAN RAYA UMUM DEWAN UNDANGAN PULAU PINANG KE-15": date(2023, 8, 12),
    "PILIHAN RAYA UMUM DEWAN UNDANGAN SABAH KE-17": date(2025, 11, 29),
    # Media statement 18/2026 of the Election Commission (12 June 2026) sets both polling days.
    "PILIHAN RAYA UMUM DEWAN NEGERI JOHOR KE-16": date(2026, 7, 11),
    "PILIHAN RAYA UMUM DEWAN NEGERI, NEGERI SEMBILAN KE-16": date(2026, 8, 1),
}
# Polling days of by-elections, keyed by the contest's seat code and year, each with the Commission's media statement
# that gives it: the statement on the eve of polling, or the one that fixed the election's dates. A by-election not
# listed here is dated to its year; the Commission's statement archive does not reach back before 2022.
STATEMENTS = "https://spr.gov.my/wp-content/uploads"
PULAI = (
    f"{STATEMENTS}/2025/10/KM-BIL-31-2023-MENJELANG-PROSES-PENGUNDIAN-PRK-PARLIMEN-P161-PULAI-DAN-DEWAN-NEGERI-"
    "N13-SIMPANG-JERAM-JOHOR.pdf"
)
BY_DAYS: dict[tuple[str, int], tuple[date, str]] = {
    ("P.161", 2023): (
        date(2023, 9, 9),
        PULAI,
    ),
    ("N.13", 2023): (
        date(2023, 9, 9),
        PULAI,
    ),
    ("N.36", 2023): (
        date(2023, 10, 7),
        f"{STATEMENTS}/2025/10/KM-BIL-42-2023-MENJELANG-PROSES-PENGUNDIAN-PRK-DEWAN-NEGERI-N36-PELANGAI-PAHANG.pdf",
    ),
    ("N.67", 2023): (
        date(2023, 11, 4),
        f"{STATEMENTS}/2025/10/KM-BIL-38-2023-URUSAN-PRK-DEWAN-UNDANGAN-NEGERI-N67-JEPAK-SARAWAK-1.pdf",
    ),
    ("N.06", 2024): (
        date(2024, 5, 11),
        f"{STATEMENTS}/2025/02/KM-BIL-12-2024-MENJELANG-PROSES-PENGUNDIAN-PRK-DN-N06-KUALA-KUBU-BAHARU-SELANGOR-1.pdf",
    ),
    ("N.43", 2024): (
        date(2024, 8, 17),
        f"{STATEMENTS}/2024/12/KM-BIL-31-2024-MENJELANG-HARI-MENGUNDI-PRK-DEWAN-NEGERI-N.43-NENGGIRI-KELANTAN.pdf",
    ),
    ("N.29", 2024): (
        date(2024, 9, 28),
        f"{STATEMENTS}/2024/12/KM-BIL-39-2024-MENJELANG-HARI-MENGUNDI-PRK-N29-MAHKOTA-JOHOR.pdf",
    ),
    ("P.187", 2026): (
        date(2026, 1, 24),
        f"{STATEMENTS}/2025/12/KM-BIL-42-2025-MESYUARAT-KHAS-PRK-P.187-KINABATANGAN-DAN-N.58-LAMAG-SABAH-final.pdf",
    ),
    ("N.58", 2026): (
        date(2026, 1, 24),
        f"{STATEMENTS}/2025/12/KM-BIL-42-2025-MESYUARAT-KHAS-PRK-P.187-KINABATANGAN-DAN-N.58-LAMAG-SABAH-final.pdf",
    ),
}
# Contests the source classes as by-elections that are postponed polls of the 2022 general election.
POSTPONED = ("PILIHAN RAYA UMUM KE-15",)


def data_url(slug: str) -> str:
    return f"{PORTAL}/data/{slug}.json"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
STATEMENTS_ID = key("source", "kenyataan-media")
DEWAN_RAKYAT = key("body", "dewan-rakyat")
SYSTEM = key("electoral_system", "fptp")


def fetch() -> None:
    raw.fetch(SOURCE, CATALOG, suffix=".json")
    for slug in [*FILES.values(), ROLL, CENTRES]:
        raw.fetch(SOURCE, data_url(slug), suffix=".json")
    for _, url in BY_DAYS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


@functools.cache
def roll() -> dict[tuple[int, str, str], int]:
    """Registered voters in the master roll, summed to each (year, state, seat code) of both chambers."""
    out: dict[tuple[int, str, str], int] = defaultdict(int)
    for r in json.loads(raw.cached(SOURCE, data_url(ROLL)).path.read_text()):
        year, state = int(r["TAHUN"]), re.sub(r"^WP\b", "W.P", clean(r["Negeri"]))
        voters = int(r["PEMILIH BERDAFTAR"] or 0)
        for column in ("PARLIMEN", "DUN"):
            found = place(r[column])
            if found is not None:
                out[(year, state, found[0])] += voters
    return dict(out)


@functools.cache
def centres() -> dict[tuple[str, str, str], int]:
    """Registered voters at the 2026 polling centres, summed to each (election name, state, seat code)."""
    out: dict[tuple[str, str, str], int] = defaultdict(int)
    for r in json.loads(raw.cached(SOURCE, data_url(CENTRES)).path.read_text()):
        for column in ("PARLIMEN", "DEWAN UNDANGAN NEGERI"):
            found = place(r[column])
            if found is not None:
                out[(clean(r["NamaPilihanraya"]), clean(r["Negeri"]), found[0])] += int(r["JumlahPemilih"] or 0)
    return dict(out)


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def number(text: str | None) -> int | None:
    value = clean(text).replace(",", "")
    return None if value in ("", "NULL", "-") else int(float(value))


def decimal(text: str | None) -> float | None:
    value = clean(text)
    return None if value in ("", "NULL", "-") else float(value)


def place(text: str | None) -> tuple[str, str] | None:
    found = re.fullmatch(r"([PN])\.\s*(\d+)\s*(.*)", clean(text))
    return None if found is None else (f"{found[1]}.{found[2]}", clean(found[3]))


def title(text: str) -> str:
    return re.sub(r"\b([A-Za-z])([A-Za-z`']*)", lambda m: m[1].upper() + m[2].lower(), text)


@dataclass(frozen=True)
class Row:
    event: str  # the source's election name
    year: int
    kind: str  # "general", "state", or "by"
    state: str
    parliament: tuple[str, str] | None
    assembly: tuple[str, str] | None
    ballot: str
    full: str | None
    party: str
    abbreviation: str
    votes: int | None
    status: str
    majority: int | None
    unreturned: int | None
    rejected: int | None
    turnout: float | None
    registered: int | None
    corrected: str | None = None


@functools.cache
def rows() -> list[Row]:
    out: list[Row] = []
    for family, slug in FILES.items():
        for r in json.loads(raw.cached(SOURCE, data_url(slug)).path.read_text()):
            if family == "pru":
                year, name = int(r["TAHUN PILIHAN RAYA"]), clean(r["NamaPR"])
                kind, status = "general", clean(r["StatusCalon"])
                full = None
                ballot = clean(r["NAMA ATAS KERTAS UNDI"])
            elif family == "dun":
                year, name = int(r["TAHUN PILIHAN RAYA"]), clean(r["NAMA PILIHANRAYA"])
                kind, status = "state", clean(r["StatusCalon"])
                full = None
                ballot = clean(r["NAMA ATAS KERTAS UNDI"])
            else:
                year, name = int(r["TAHUN PILIHAN RAYA"]), clean(r["NamaPR"])
                kind, status = "by", clean(r["STATUS"])
                full = clean(r["NAMA PENUH CALON"]) or None
                ballot = clean(r["NAMA KERTAS UNDI"])
            parliament, assembly = place(r["PARLIMEN"]), place(r["DEWAN UNDANGAN NEGERI"])
            party = clean(r["NAMA PARTI BERTANDING"])
            abbreviation = clean(r["SINGKATAN NAMA PARTI BERTANDING"])
            abbreviation = ALIASES.get(PARTY_FIXES.get((abbreviation, party), abbreviation), abbreviation)
            out.append(
                Row(
                    name,
                    year,
                    kind,
                    clean(r["NEGERI"]),
                    parliament,
                    assembly,
                    ballot,
                    full,
                    party,
                    abbreviation,
                    number(r["BILANGAN UNDI"]),
                    status,
                    number(r["MAJORITI"]),
                    number(r["UNDI TAK KEMBALI"]),
                    number(r["UNDI DITOLAK"]),
                    decimal(r["PERATUS UNDI"]),
                    number(r["JumlahPemilih"]),
                )
            )
            seat = (
                (parliament if assembly is None else assembly)
                if (assembly := place(r["DEWAN UNDANGAN NEGERI"]))
                else parliament
            )
            fixed = STATUS_FIXES.get((year, clean(r["NEGERI"]), seat[0] if seat else "", ballot))
            if fixed is not None and fixed != status:
                out[-1] = replace(out[-1], status=fixed, corrected=status)
    return _named(out)


def _named(out: list[Row]) -> list[Row]:
    """The 2013 file prints state constituencies by number alone. The 2013 election used the constituencies of 2008,
    so a number takes the name the 2008 file gives it in the same state and parliamentary constituency."""
    names = {
        (r.state, r.parliament, r.assembly[0]): r.assembly[1]
        for r in out
        if r.year == 2008 and r.assembly is not None and r.assembly[1]
    }
    filled: list[Row] = []
    for r in out:
        if r.assembly is not None and not r.assembly[1]:
            name = names.get((r.state, r.parliament, r.assembly[0]), "")
            r = replace(r, assembly=(r.assembly[0], name))
            FILLED[r.year] += 1 if name else 0
            MISSING[r.year] += 0 if name else 1
        filled.append(r)
    return filled


FILLED: Counter[int] = Counter()
MISSING: Counter[int] = Counter()


def canonical(name: str) -> str:
    return clean(re.sub(r"\([^)]*\)", " ", name)).upper()


@functools.cache
def parties() -> dict[str, list[str]]:
    """Party abbreviation -> its published names, most used first. An abbreviation that shares its name with a more
    used one is folded into it."""
    names: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows():
        names[row.abbreviation][canonical(row.party)] += 1
    by_name: dict[str, list[str]] = defaultdict(list)
    for abbreviation, counter in names.items():
        for name in counter:
            by_name[name].append(abbreviation)
    for abbreviation, counter in list(names.items()):
        for name in counter:
            best = max(by_name[name], key=lambda a: sum(names[a].values()))
            if best != abbreviation and abbreviation in names and set(names[abbreviation]) == {name}:
                FOLDED[abbreviation] = best
    return {a: [n for n, _ in c.most_common()] for a, c in names.items() if a not in FOLDED}


FOLDED: dict[str, str] = {}


def party(abbreviation: str) -> str | None:
    parties()
    abbreviation = FOLDED.get(abbreviation, abbreviation)
    return None if abbreviation == "BEBAS" else key("party", abbreviation)


@dataclass
class Contest:
    id: str
    state: str
    parliament: tuple[str, str] | None
    assembly: tuple[str, str] | None
    rows: list[Row] = field(default_factory=list)

    @property
    def seat(self) -> tuple[str, str]:
        found = self.assembly or self.parliament
        assert found is not None
        return found

    @property
    def tacit(self) -> bool:
        return len(self.rows) == 1 and self.rows[0].status == "MTB"


@dataclass
class Event:
    id: str  # the package name
    kind: str  # the election kind
    year: int
    day: date | None
    name: str
    native: list[str]
    note: str | None = None
    contests: list[Contest] = field(default_factory=list)
    # The year-dated name a by-election was first loaded under. Its entities keep the ids derived from that name, so
    # dating it updates those rows in place rather than adding a second election, and its old address redirects.
    first: str | None = None
    statement: str | None = None  # the media statement that gives the polling day

    @property
    def anchor(self) -> str:
        return self.first or self.id


def _contest(event: Event, row: Row) -> Contest:
    parts = (row.state, row.parliament, row.assembly)
    id = key("contest", event.anchor, *parts)
    for found in event.contests:
        if found.id == id:
            return found
    event.contests.append(Contest(id, row.state, row.parliament, row.assembly))
    return event.contests[-1]


@functools.cache
def events() -> list[Event]:
    made: dict[str, Event] = {}
    for row in rows():
        if row.kind == "general":
            day = GENERAL_DAYS[row.year]
            id = str(day)
            event = made.setdefault(
                id,
                Event(
                    id,
                    "general",
                    row.year,
                    day,
                    f"{row.year} Malaysian general election",
                    [],
                    "Parliamentary and concurrent state assembly seats, as the Election Commission publishes them "
                    "together."
                    + (
                        " The Commission's 2013 file gives each state constituency by number only; the names here are "
                        "those its 2008 file gives the same number in the same state and parliamentary constituency, "
                        "the constituencies being unchanged between the two elections."
                        if row.year == 2013
                        else ""
                    ),
                ),
            )
        elif row.kind == "state":
            day = STATE_DAYS.get(row.event)
            if day is not None:
                id = str(day)
                label = (
                    f"{day.year} Malaysian state elections"
                    if sum(d == day for d in STATE_DAYS.values()) > 1
                    else f"{day.year} {STATES[row.state].split(' ')[0]} state election"
                )
            else:
                id = f"{row.year}-{changeset.slug(STATES[row.state])}-state"
                label = f"{row.year} {STATES[row.state]} state election"
            event = made.setdefault(id, Event(id, "general", row.year, day, label, []))
        else:
            seat = row.assembly or row.parliament
            assert seat is not None
            postponed = any(row.event.startswith(prefix) for prefix in POSTPONED)
            slug = f"{'postponed' if postponed else 'by'}-{changeset.slug(seat[0] + ' ' + seat[1])}"
            dated = BY_DAYS.get((seat[0], row.year))
            first = f"{row.year}-{slug}"
            id, day, statement = first, None, None
            if dated is not None:
                day, statement = dated
                same = sum(d == day for d, _ in BY_DAYS.values()) > 1
                id = f"{day}-{slug}" if same else str(day)
            body = "state seat" if row.assembly else "parliamentary seat"
            label = (
                f"{row.year} {title(seat[1])} {'postponed poll' if postponed else 'by-election'}"
                if not postponed
                else f"{row.year} {title(seat[1])} postponed general election poll"
            )
            event = made.setdefault(
                id,
                Event(
                    id,
                    "general" if postponed else "by_election",
                    row.year,
                    day,
                    label,
                    [],
                    f"Contest for the {body} {seat[0]} {title(seat[1])}, {STATES[row.state]}."
                    + (
                        " The Election Commission lists this poll as part of the 15th general election; it was held "
                        "after polling day."
                        if postponed
                        else ""
                    ),
                    first=first if day is not None else None,
                    statement=statement,
                ),
            )
        if row.event not in event.native:
            event.native.append(row.event)
        _contest(event, row).rows.append(row)
    return sorted(made.values(), key=lambda e: (e.day or date(e.year, 12, 31), e.id))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{event.id}" for event in events())]


def emit() -> None:
    package.run("load", packages())


def area(scheme: str, *parts: object) -> str:
    return key("area", scheme, *parts)


def state_area(state: str) -> str:
    return area("my.negeri", state)


def parliament_area(state: str, seat: tuple[str, str]) -> str:
    return area("my.parlimen", state, *seat)


def assembly_area(state: str, seat: tuple[str, str], parliament: tuple[str, str] | None) -> str:
    return area("my.dun", state, *seat, *(parliament or ()))


def seat_area(contest: Contest) -> str:
    if contest.assembly is not None:
        return assembly_area(contest.state, contest.assembly, contest.parliament)
    assert contest.parliament is not None
    return parliament_area(contest.state, contest.parliament)


def all_contests() -> list[Contest]:
    return [contest for event in events() for contest in event.contests]


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "Malaysia's states and federal territories, parliamentary constituencies, and state constituencies as the "
        "Election Commission names them in its results",
    )
    p.external.add(MALAYSIA)
    for code, rank, kind, noun in (
        ("my.negeri", 1, "administrative", "state"),
        ("my.parlimen", 2, "electoral_district", "parliamentary constituency"),
        ("my.dun", 3, "electoral_district", "state constituency"),
        ("my.keputusan", 4, "precinct", "constituency count"),
    ):
        p.add("scheme", code=code, polity_id=MALAYSIA, kind=kind, rank=rank, noun=noun, code_system=None)
    for state, name in STATES.items():
        id = state_area(state)
        p.add("area", id=id, scheme_code="my.negeri")
        p.add("area_code", area_id=id, code=state)
        p.name(id, {"W.P KUALA LUMPUR": "Kuala Lumpur"}.get(state, name), "en", "english")
        p.name(id, title(state), "ms", "official", False)
    made: set[str] = set()
    for contest in all_contests():
        for seats, scheme in ((contest.parliament, "my.parlimen"), (contest.assembly, "my.dun")):
            if seats is None:
                continue
            id = (
                parliament_area(contest.state, seats)
                if scheme == "my.parlimen"
                else assembly_area(contest.state, seats, contest.parliament)
            )
            if id in made:
                continue
            made.add(id)
            p.add("area", id=id, scheme_code=scheme)
            p.add("area_code", area_id=id, code=seats[0])
            p.name(id, title(seats[1]), "ms", "official")
            parent = (
                state_area(contest.state)
                if scheme == "my.parlimen" or contest.parliament is None
                else parliament_area(contest.state, contest.parliament)
            )
            p.add("area_parent", area_id=id, parent_id=parent)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Malaysia's parties, the Dewan Rakyat and the state assemblies, and the first-past-the-post system, from the "
        "Election Commission's results",
        depends=[f"{SOURCE}/geo"],
    )
    p.external.add(MALAYSIA)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Suruhanjaya Pilihan Raya Malaysia",
        title="Portal Data Terbuka SPR: Keputusan PRU, Keputusan PRU DUN, and Keputusan PRK",
        url=PORTAL,
        license="No license stated; published on the Election Commission's open data portal",
        license_url=f"{PORTAL}/dasar-dasar-dan-hak-cipta",
        attribution="Suruhanjaya Pilihan Raya Malaysia (SPR)",
    )
    for url in [CATALOG, *(data_url(slug) for slug in FILES.values())]:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add(
        "source",
        id=STATEMENTS_ID,
        kind="official_body",
        publisher="Suruhanjaya Pilihan Raya Malaysia",
        title="Kenyataan Media SPR (media statements of the Election Commission)",
        url="https://spr.gov.my/arkib-kenyataan-media/",
        license="No license stated; published on the Election Commission's website",
        attribution="Suruhanjaya Pilihan Raya Malaysia (SPR)",
    )
    for url in sorted({url for _, url in BY_DAYS.values()}):
        p.artifact(raw.cached(SOURCE, url), STATEMENTS_ID)
    p.add("body", id=DEWAN_RAKYAT, polity_id=MALAYSIA, kind="lower_house")
    p.name(DEWAN_RAKYAT, "House of Representatives", "en", "english")
    p.name(DEWAN_RAKYAT, "Dewan Rakyat", "ms", "native", False)
    for state in ASSEMBLIES:
        id = key("body", "dun", state)
        p.add("body", id=id, polity_id=MALAYSIA, kind="unicameral")
        p.name(id, f"{STATES[state]} State Legislative Assembly", "en", "english")
        p.name(id, f"Dewan Undangan Negeri {STATES[state]}", "ms", "native", False)
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=MALAYSIA,
        valid_from=None,
        valid_to=None,
        rules={"formula": "fptp"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Each constituency elects one member by plurality, with no runoff.",
        lang="en",
    )
    for abbreviation, names in sorted(parties().items()):
        id = party(abbreviation)
        if id is None:
            continue
        p.add("party", id=id, kind="bloc" if abbreviation in BLOCS else "party", polity_id=MALAYSIA)
        p.add("external_id", entity_id=id, scheme="my.spr.party", value=abbreviation)
        for index, name in enumerate(names):
            p.name(id, title(name), "ms", "official", index == 0)
        p.name(id, abbreviation, "ms", "abbreviation", False)
    return p


def _one(values: list[int | float | None]) -> int | float | None:
    found = {value for value in values if value is not None}
    if len(found) > 1:
        raise ValueError(f"one contest carries different values {sorted(found)}")
    return found.pop() if found else None


def electorate(event: Event, contest: Contest) -> tuple[int | None, int | None]:
    """The constituency's registered voters as printed, and where it prints none, the master roll's sum."""
    printed = _one([row.registered for row in contest.rows])
    year = ROLL_YEARS.get(event.id)
    if printed or contest.tacit or year is None:
        return (None if printed is None else int(printed)), None
    return None, roll().get((year, contest.state, contest.seat[0]))


@dataclass
class Audit:
    contests: int = 0
    majority_ok: int = 0
    majority_bad: list[str] = field(default_factory=list)
    turnout_ok: int = 0
    turnout_bad: list[str] = field(default_factory=list)
    turnout_unchecked: int = 0
    roll_filled: int = 0
    roll_ok: int = 0
    roll_bad: list[str] = field(default_factory=list)
    caveats: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))


def audit(event: Event) -> Audit:
    """Compare each contest's own figures with the ones the source prints beside them: the winner's majority, and
    the turnout percentage where the file also prints the electorate."""
    found = Audit()
    for contest in event.contests:
        found.contests += 1
        rows_ = contest.rows
        label = f"{event.id} {contest.state} {contest.seat[0]} {contest.seat[1]}"
        majority = max((row.majority for row in rows_ if row.majority is not None), default=None)
        if not contest.tacit and len(rows_) > 1:
            ranked = sorted((row.votes or 0 for row in rows_), reverse=True)
            if majority is None or majority == ranked[0] - ranked[1]:
                found.majority_ok += 1
            else:
                found.majority_bad.append(f"{label}: printed {majority}, votes give {ranked[0] - ranked[1]}")
                found.caveats[contest.id].append(
                    f"The printed majority of {majority:,} differs from the {ranked[0] - ranked[1]:,} that the printed "
                    "candidate votes give. Both are as the Election Commission published them."
                )
        printed, from_roll = electorate(event, contest)
        registered, turnout = printed or from_roll, _one([row.turnout for row in rows_])
        found.roll_filled += from_roll is not None
        rejected, unreturned = _one([row.rejected for row in rows_]), _one([row.unreturned for row in rows_])
        if contest.tacit or not registered or turnout is None or rejected is None:
            found.turnout_unchecked += 1
            continue
        cast = sum(row.votes or 0 for row in rows_) + rejected + (unreturned or 0)
        tolerance = 0.51 if turnout == int(turnout) else 0.051 if round(turnout, 1) == turnout else 0.011
        if from_roll is not None:
            # The roll is a snapshot of its year, not of polling day, and the 2018 roll runs a point or two above the
            # electorate of polling day in the states that grew most, so only a gap of two points is noted.
            if abs(cast * 100 / registered - turnout) <= 2.0:
                found.roll_ok += 1
            else:
                found.roll_bad.append(f"{label}: printed {turnout}%, counts give {cast * 100 / registered:.2f}%")
                found.caveats[contest.id].append(
                    f"The printed turnout of {turnout}% differs from the {cast * 100 / registered:.2f}% that the "
                    "printed votes, rejected ballots, unreturned ballots, and the master roll's electorate give."
                )
        elif abs(cast * 100 / registered - turnout) <= tolerance:
            found.turnout_ok += 1
        else:
            found.turnout_bad.append(f"{label}: printed {turnout}%, counts give {cast * 100 / registered:.2f}%")
            found.caveats[contest.id].append(
                f"The printed turnout of {turnout}% differs from the {cast * 100 / registered:.2f}% that the printed "
                "votes, rejected ballots, unreturned ballots, and electorate give. Both are as published."
            )
    return found


def election(event: Event, checked: Audit) -> Package:
    p = Package(
        f"{SOURCE}/{event.id}",
        SOURCE,
        f"The {event.name} by constituency from the Election Commission's open data",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external.add(MALAYSIA)
    for url in [data_url(slug) for slug in FILES.values()]:
        p.uses(raw.cached(SOURCE, url))
    if event.id in ROLL_YEARS:
        p.uses(raw.cached(SOURCE, data_url(ROLL)))
    if event.statement is not None:
        p.uses(raw.cached(SOURCE, event.statement))
    id = key("election", event.anchor)
    if event.day is not None:
        first = last = event.day
        precision = "day"
        path = f"my/{event.day}-" + ("general" if event.kind == "general" else event.kind.replace("_", "-"))
        if event.name.endswith("state election") or "state elections" in event.name:
            path = f"my/{event.day}-state"
        if event.kind == "by_election" and event.id != str(event.day):
            path = f"my/{event.id}"
    else:
        first, last = date(event.year, 1, 1), date(event.year, 12, 31)
        precision = "year"
        path = f"my/{event.id}"
    p.add(
        "election",
        id=id,
        polity_id=MALAYSIA,
        kind=event.kind,
        date_from=first,
        date_to=last,
        date_precision=precision,
        original_text=" / ".join(event.native),
        status="held",
    )
    if event.first is not None:
        # The year-dated address comes first, so it stops being current before the new one becomes current.
        p.add("slug", path=f"my/{event.first}", entity_id=id, is_current=False)
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, event.name, "en", "official")
    for native in event.native:
        p.name(id, native, "ms", "native", False)
    if event.note:
        p.add("note", id=p.id("note", id), entity_id=id, kind="context", body_md=event.note, lang="en")
    if event.statement is not None:
        p.add(
            "note",
            id=p.id("note", id, "date"),
            entity_id=id,
            kind="context",
            body_md="The Election Commission's results files give this election's year but not its polling day. The "
            f"day is from the Commission's [media statement]({event.statement}) on the election.",
            lang="en",
        )
    if precision == "year":
        p.add(
            "note",
            id=p.id("note", id, "date"),
            entity_id=id,
            kind="caveat",
            body_md="The Election Commission's results files give this election's year but not its polling day, so "
            "it is dated to the year.",
            lang="en",
        )
    method = (
        "Counted per constituency and published by the Election Commission (SPR) in its open data. Each candidate's "
        "votes are as printed on the results line. Rejected ballots are the printed 'undi ditolak', and registered "
        "voters are the printed electorate where the file gives one. The files also print ballots issued but not "
        "returned ('undi tak kembali'), the turnout percentage, and the winner's majority; those are checked here "
        "and not stored. The Commission publishes no finer count in these files."
    )
    systems = {contest.id for contest in event.contests}
    assert len(systems) == len(event.contests)
    seen: set[str] = set()
    for contest in event.contests:
        seat = contest.seat
        state_name = STATES[contest.state]
        contest_id = contest.id
        body = key("body", "dun", contest.state) if contest.assembly is not None else DEWAN_RAKYAT
        p.add(
            "contest",
            id=contest_id,
            election_id=id,
            kind="body_seats",
            body_id=body,
            district_area_id=seat_area(contest),
            magnitude=1,
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="tacit" if contest.tacit else "counted",
        )
        english = f"{event.name}: {seat[0]} {title(seat[1])}, {state_name}"
        p.name(contest_id, english, "en", "english")
        slug = f"{path}/{changeset.slug(state_name)}-{changeset.slug(seat[0])}-{changeset.slug(seat[1])}"
        if slug in seen:
            raise ValueError(f"duplicate contest slug {slug}")
        seen.add(slug)
        if event.first is not None:
            old = f"my/{event.first}/{slug.removeprefix(path + '/')}"
            p.add("slug", path=old, entity_id=contest_id, is_current=False)
        p.add("slug", path=slug, entity_id=contest_id, is_current=True)
        result_set = key("result_set", contest_id)
        p.add(
            "result_set",
            id=result_set,
            election_id=id,
            contest_id=contest_id,
            stage="official",
            kind="tacit" if contest.tacit else "counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=None if contest.tacit else 1.0,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        fixes = [
            f"The file gives {row.ballot} the status {row.corrected} here, copied from the same person's other "
            f"contest of the day. The printed votes make this candidate "
            f"{'the winner' if row.status in WINNERS else 'a loser'}, so the status is set to {row.status}."
            for row in contest.rows
            if row.corrected is not None
        ]
        for index, caveat in enumerate([*fixes, *checked.caveats.get(contest.id, [])]):
            p.add(
                "note",
                id=p.id("note", result_set, "caveat", index),
                entity_id=result_set,
                kind="caveat",
                body_md=caveat,
                lang="en",
            )
        unit = key("unit", event.anchor, contest.state, seat[0])
        if not contest.tacit:
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=MALAYSIA,
                scheme_code="my.keputusan",
                code=f"{event.anchor}:{changeset.slug(contest.state)}:{seat[0]}",
                area_relation="equals",
                area_id=seat_area(contest),
                kind="standard",
            )
        for index, row in enumerate(contest.rows, start=1):
            option = key("option", contest_id, index)
            person = key("person", contest_id, index)
            party_id = party(row.abbreviation)
            p.add("person", id=person)
            if row.full:
                p.name(person, row.full, "ms", "official")
                p.name(person, row.ballot, "ms", "ballot", False)
            else:
                p.name(person, row.ballot, "ms", "ballot")
            p.add(
                "contest_option",
                id=option,
                contest_id=contest_id,
                kind="candidate",
                party_id=party_id,
                ballot_label=row.ballot,
            )
            p.add(
                "candidacy",
                id=key("candidacy", contest_id, index),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=row.ballot,
                member_of_party_id=party_id,
                is_independent=party_id is None,
            )
            if row.status in WINNERS:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            elif row.status in LOSERS:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="lost")
            if not contest.tacit and row.votes is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=VOTES,
                    count_round=0,
                    flags=0,
                    value=row.votes,
                )
        if not contest.tacit:
            printed, from_roll = electorate(event, contest)
            if from_roll is not None:
                p.add(
                    "note",
                    id=p.id("note", result_set, "electorate"),
                    entity_id=result_set,
                    kind="methodology",
                    body_md=f"The Election Commission's results file prints no electorate for this election. The "
                    f"registered voters here are the sum of this constituency's polling districts in the "
                    f"Commission's {ROLL_YEARS[event.id]} master roll of voters, a snapshot of that year rather "
                    "than of polling day.",
                    lang="en",
                )
            for measure, value in (
                (REGISTERED, printed if from_roll is None else from_roll),
                (INVALID, _one([row.rejected for row in contest.rows])),
            ):
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=int(value),
                    )
    return p


def parse() -> None:
    geo().write()
    registry().write()
    totals = Audit()
    for event in events():
        found = audit(event)
        for name in ("contests", "majority_ok", "turnout_ok", "turnout_unchecked", "roll_filled", "roll_ok"):
            setattr(totals, name, getattr(totals, name) + getattr(found, name))
        totals.majority_bad += found.majority_bad
        totals.turnout_bad += found.turnout_bad
        totals.roll_bad += found.roll_bad
        election(event, found).write()
    print(
        f"audit: {totals.contests} contests; majority ok {totals.majority_ok}, off {len(totals.majority_bad)}; "
        f"turnout ok {totals.turnout_ok}, off {len(totals.turnout_bad)}, unchecked {totals.turnout_unchecked}; "
        f"electorate from the roll {totals.roll_filled}, turnout ok {totals.roll_ok}, off {len(totals.roll_bad)}",
        flush=True,
    )
    for line in [*totals.majority_bad, *totals.turnout_bad, *totals.roll_bad]:
        print(f"  {line}", flush=True)
