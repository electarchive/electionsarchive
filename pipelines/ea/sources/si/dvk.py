import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from uuid import uuid5

import fastexcel
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "si.dvk"
BASE = "https://www.dvk-rs.si/fileadmin/user_upload/dokumenti/volitve"
ARCHIVE = "https://www.dvk-rs.si/arhivi"
VOLITVE = "https://volitve.dvk-rs.si"
SLOVENIA = str(uuid5(package.NAMESPACE, "polity\x1fSI"))
ELECTORAL_UNITS = range(1, 9)
DISTRICTS = range(1, 12)


@dataclass(frozen=True)
class Vote:
    """One published polling-station workbook."""

    kind: str
    day: date
    path: str
    sheet: str
    title: str
    native: str
    round: int = 1
    note: str = ""
    layout: str = "table"
    question: str = ""
    advisory: bool = False

    @property
    def url(self) -> str:
        return self.path if self.path.startswith("https://") else f"{BASE}/{self.path}"


VOTES = [
    Vote(
        "dz",
        date(2011, 12, 4),
        "drzavni_zbor_2011/Voltive-DZ2011-koncni-izid-po-voliscih.xls",
        "Celota",
        "2011 Slovenian parliamentary election",
        "Predčasne volitve v Državni zbor 2011",
    ),
    Vote(
        "dz",
        date(2014, 7, 13),
        "drzavni_zbor_2014/VsaVolisca20140721-_12_.xlsx",
        "Vsa volišča",
        "2014 Slovenian parliamentary election",
        "Predčasne volitve v Državni zbor 2014",
    ),
    Vote(
        "dz",
        date(2018, 6, 3),
        "drzavni_zbor_2018/Koncni-izid_VsaVolisca20180612Internet.xlsx",
        "Vsa volišča internet",
        "2018 Slovenian parliamentary election",
        "Predčasne volitve v Državni zbor 2018",
    ),
    Vote(
        "dz",
        date(2022, 4, 24),
        "DZ/2022/izidi_volitev/VoliscaDZ20220506Koncni.xlsx",
        "Vsi",
        "2022 Slovenian parliamentary election",
        "Volitve v Državni zbor 2022",
    ),
    Vote(
        "dz",
        date(2026, 3, 22),
        f"{ARCHIVE}/dz2026/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2026 Slovenian parliamentary election",
        "Volitve v Državni zbor 2026",
        layout="izvoz",
    ),
    Vote(
        "pres",
        date(2022, 10, 23),
        f"{ARCHIVE}/volitve2022/vp2022/data/prvikrog/izvoz.xlsx",
        "Podatki po voliščih",
        "2022 Slovenian presidential election, first round",
        "Volitve predsednika republike 2022, 1. krog",
        layout="izvoz",
    ),
    Vote(
        "pres",
        date(2022, 11, 13),
        f"{ARCHIVE}/volitve2022/vp2022/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2022 Slovenian presidential election, second round",
        "Volitve predsednika republike 2022, 2. krog",
        round=2,
        layout="izvoz",
    ),
    Vote(
        "pres",
        date(2017, 10, 22),
        "predsednika_2017/Koncni-izid_volitev-predsednika_prvi_krog_stanje_2.11.2017.xlsx",
        "vsi",
        "2017 Slovenian presidential election, first round",
        "Volitve predsednika republike 2017, 1. krog",
    ),
    Vote(
        "pres",
        date(2017, 11, 12),
        "predsednika_2017/Koncni_izid_drgu_krog_21.11.2017.xlsx",
        "998",
        "2017 Slovenian presidential election, second round",
        "Volitve predsednika republike 2017, 2. krog",
        round=2,
    ),
    Vote(
        "ep",
        date(2014, 5, 25),
        "evropski_parlament_2014/volitve-v-ep-2014-izzidi-glasovanja-po-voliscih.xlsx",
        "Vsi",
        "2014 European Parliament election in Slovenia",
        "Volitve poslancev iz Republike Slovenije v Evropski parlament 2014",
    ),
    Vote(
        "ep",
        date(2024, 6, 9),
        f"{ARCHIVE}/ep2024/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2024 European Parliament election in Slovenia",
        "Volitve poslancev iz Republike Slovenije v Evropski parlament 2024",
        layout="izvoz",
    ),
    Vote(
        "ref",
        date(2012, 3, 25),
        "Referendum-druzinski-zakonik-koncni-izid-po-voliscih.xls",
        "Po voliščih 3.4.2012",
        "2012 Slovenian referendum on the Family Code",
        "Zakonodajni referendum o Družinskem zakoniku 2012",
        note="Family Code (Družinski zakonik)",
    ),
    Vote(
        "ref",
        date(2014, 6, 8),
        "referendum_2014/Izidi-referenduma_AS2014Volisca20140616_koncni.xlsx",
        "Sheet1",
        "2014 Slovenian referendum on the Archives Act",
        "Zakonodajni referendum o Zakonu o arhivskem gradivu in arhivih 2014",
        note="Archival Material and Archives Act (ZAGA)",
    ),
    Vote(
        "ref",
        date(2015, 12, 20),
        "referendum_2015/Izid_glasovanja_po_volis__c__ih_SLO_dan_glasovanja_20.12.2015_2_2016.xlsx",
        "Sheet2",
        "2015 Slovenian referendum on the Marriage and Family Relations Act amendment",
        "Zakonodajni referendum o Zakonu o spremembi Zakona o zakonski zvezi in družinskih razmerjih 2015",
        note="Act amending the Marriage and Family Relations Act (ZZZDR-D)",
    ),
    Vote(
        "ref",
        date(2017, 9, 24),
        "referendum_2017/Izid_glaosvanja_ZIUGDT_konc__ni_16.10.2017_za_poroc__ilo_DVK.xlsx",
        "Sheet1",
        "2017 Slovenian referendum on the Divača–Koper railway act",
        "Zakonodajni referendum o Zakonu o izvedbi investicije drugega tira železniške proge Divača–Koper 2017",
        note="Act on the second track of the Divača–Koper railway (ZIUGDT)",
    ),
    Vote(
        "ref",
        date(2018, 5, 13),
        "referendum_2018/Izid_po_volis__c__ih_SLO_na_dan_glasovanja_13.5.2018.xlsx",
        "Sheet1",
        "2018 Slovenian repeat referendum on the Divača–Koper railway act",
        "Ponovno glasovanje na referendumu o ZIUGDT 2018",
        note="Repeat vote on the Act on the second track of the Divača–Koper railway (ZIUGDT)",
    ),
    Vote(
        "ref",
        date(2021, 7, 11),
        "referendum_2021/Koncni-izid-glasovanja_po-voliscih.xlsx",
        "VSI",
        "2021 Slovenian referendum on the Waters Act amendment",
        "Zakonodajni referendum o Zakonu o spremembah in dopolnitvah Zakona o vodah (ZV-1G) 2021",
        note="Act amending the Waters Act (ZV-1G)",
    ),
    Vote(
        "ref",
        date(2022, 11, 27),
        f"{ARCHIVE}/volitve2022/referendum-oskrba/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2022 Slovenian referendum on the Long-Term Care Act amendment",
        "Referendum o noveli Zakona o dolgotrajni oskrbi (ZDOsk-A)",
        note="Act amending the Long-Term Care Act (ZDOsk-A)",
        layout="izvoz",
        question="oskrba",
    ),
    Vote(
        "ref",
        date(2022, 11, 27),
        f"{ARCHIVE}/volitve2022/referendum-rtv/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2022 Slovenian referendum on the Radiotelevizija Slovenija Act amendment",
        "Referendum o noveli Zakona o Radioteleviziji Slovenija (ZRTVS-1B)",
        note="Act amending the Radiotelevizija Slovenija Act (ZRTVS-1B)",
        layout="izvoz",
        question="rtv",
    ),
    Vote(
        "ref",
        date(2022, 11, 27),
        f"{ARCHIVE}/volitve2022/referendum-vlada/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2022 Slovenian referendum on the Government Act amendment",
        "Referendum o noveli Zakona o vladi (ZVRS-J)",
        note="Act amending the Government Act (ZVRS-J)",
        layout="izvoz",
        question="vlada",
    ),
    Vote(
        "ref",
        date(2024, 6, 9),
        f"{ARCHIVE}/referendum-evtanazija-2024/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2024 Slovenian advisory referendum on assisted dying",
        "Posvetovalni referendum o ureditvi pravice do pomoči pri prostovoljnem končanju življenja",
        note="Whether the right to assistance in voluntarily ending life should be regulated",
        layout="izvoz",
        question="evtanazija",
        advisory=True,
    ),
    Vote(
        "ref",
        date(2024, 6, 9),
        f"{ARCHIVE}/referendum-konoplja-medicina-2024/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2024 Slovenian advisory referendum on growing cannabis for medical use",
        "Posvetovalni referendum o gojenju in predelovanju konoplje v medicinske namene",
        note="Whether growing and processing cannabis for medical purposes should be allowed",
        layout="izvoz",
        question="konoplja-medicina",
        advisory=True,
    ),
    Vote(
        "ref",
        date(2024, 6, 9),
        f"{ARCHIVE}/referendum-konoplja-raba-2024/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2024 Slovenian advisory referendum on growing cannabis for personal use",
        "Posvetovalni referendum o gojenju in posedovanju konoplje za omejeno osebno rabo",
        note="Whether growing and possessing cannabis for limited personal use should be allowed",
        layout="izvoz",
        question="konoplja-raba",
        advisory=True,
    ),
    Vote(
        "ref",
        date(2024, 6, 9),
        f"{ARCHIVE}/referendum-preferencni-glas-2024/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2024 Slovenian advisory referendum on preference voting",
        "Posvetovalni referendum o uvedbi preferenčnega glasu za volitve v državni zbor",
        note="Whether preference voting should be introduced for National Assembly elections",
        layout="izvoz",
        question="preferencni-glas",
        advisory=True,
    ),
    Vote(
        "ref",
        date(2025, 5, 11),
        f"{ARCHIVE}/referendum-zdpidu/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2025 Slovenian referendum on the Pension Supplement for Exceptional Achievements in Art Act",
        "Zakonodajni referendum o Zakonu o dodatku k pokojnini za izjemne dosežke na področju umetnosti (ZDPIDU)",
        note="Act on the Pension Supplement for Exceptional Achievements in the Field of Art (ZDPIDU)",
        layout="izvoz",
    ),
    Vote(
        "ref",
        date(2025, 11, 23),
        f"{ARCHIVE}/referendum-zppkz/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2025 Slovenian referendum on the Assisted Voluntary End of Life Act",
        "Zakonodajni referendum o Zakonu o pomoči pri prostovoljnem končanju življenja (ZPPKŽ)",
        note="Act on Assistance in the Voluntary Ending of Life (ZPPKŽ)",
        layout="izvoz",
    ),
    Vote(
        "ref",
        date(2026, 10, 11),
        f"{VOLITVE}/referendum-rtv-prispevek/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2026 Slovenian advisory referendum on the RTV Slovenija contribution",
        "Posvetovalni referendum o ukinitvi obveznega plačevanja prispevka za programe RTV Slovenija",
        note="Whether the mandatory contribution for RTV Slovenija programs should be abolished",
        layout="izvoz",
        question="rtv-prispevek",
        advisory=True,
    ),
    Vote(
        "ref",
        date(2026, 10, 11),
        f"{VOLITVE}/referendum-socialna-pomoc/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2026 Slovenian advisory referendum on conditioning social assistance on community work",
        "Posvetovalni referendum o pogojevanju pravice do denarne socialne pomoči z aktivnim vključevanjem "
        "delovno sposobnih upravičencev v družbeno koristna dela",
        note="Whether cash social assistance should require able-bodied recipients to do community work",
        layout="izvoz",
        question="socialna-pomoc",
        advisory=True,
    ),
    Vote(
        "ref",
        date(2026, 10, 11),
        f"{VOLITVE}/referendum-volilna-pravica-tujcev/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2026 Slovenian advisory referendum on foreigners' voting rights in local elections",
        "Posvetovalni referendum o volilni pravici na lokalnih volitvah v Republiki Sloveniji",
        note="Whether foreigners should vote in local elections",
        layout="izvoz",
        question="volilna-pravica-tujcev",
        advisory=True,
    ),
    Vote(
        "ref",
        date(2026, 10, 11),
        f"{VOLITVE}/referendum-preiskava/data/izvoz.xlsx",
        "Podatki po voliščih",
        "2026 Slovenian referendum on the Parliamentary Inquiry Act amendment",
        "Zakonodajni referendum o Zakonu o spremembah in dopolnitvah Zakona o parlamentarni preiskavi (ZPPre-B)",
        note="Act amending the Parliamentary Inquiry Act (ZPPre-B)",
        layout="izvoz",
        question="preiskava",
    ),
]

DOCS = "https://www.dvk-rs.si/fileadmin/user_upload/dokumenti/volitve"
# The commission's records of the National Assembly counts (the seat allocation and the national list totals) and
# the mandate lists (list number; candidate; electoral unit; district) it publishes beside them.
REPORTS = {
    2014: f"{DOCS}/drzavni_zbor_2014/Porocilo-o-izidu---predcasne-volitve-v-DZ-2014--anonimizirano.pdf",
    2018: f"{DOCS}/drzavni_zbor_2018/011dyenp.pdf",
    2022: f"{DOCS}/DZ/2022/izidi_volitev/VDZ_2022_Zapisnik_o_ugotovitvi_izida_sprejet_7.5.2022.pdf",
    # The results site's data file, which gives each list's seats nationally and by electoral unit.
    2026: f"{ARCHIVE}/dz2026/data/data.json",
}
MANDATES = {
    2018: f"{DOCS}/DZ/2022/navodila_medijem/mandati.csv",
    2022: "https://www.dvk-rs.si/fileadmin/user_upload/mandati.csv",
    2026: f"{ARCHIVE}/dz2026/data/mandati.csv",
}
# The mandate lists name each list by the number of its list in the file: the ballot position in 2018, and a code
# the commission assigned to each running list in 2022 and 2026.
MANDATE_LISTS = {
    2018: {str(position): position for position in range(1, 26)},
    2022: {"241141": 4, "241152": 15, "241149": 12, "241154": 17, "241143": 6},
    2026: {"107753": 1, "107752": 2, "107757": 5, "107760": 6, "107749": 10, "107754": 11, "107755": 15},
}
# The archived results page of an election whose files print no seats: its table of each list's mandates by
# electoral unit.
ARCHIVE_SEATS = {2011: f"{ARCHIVE}/dz2011/rezultati/izidi_enote.html"}
# Seats the commission declared each list at the national level, by ballot position.
NATIONAL_SEATS = {
    2011: {7: 28, 11: 26, 10: 10, 4: 8, 3: 6, 13: 6, 9: 4},
    2014: {13: 36, 10: 21, 1: 10, 9: 6, 4: 6, 6: 5, 16: 4},
    2018: {15: 25, 9: 13, 20: 10, 18: 10, 8: 9, 12: 7, 23: 5, 2: 5, 17: 4},
    2022: {4: 41, 15: 27, 12: 8, 17: 7, 6: 5},
    2026: {11: 29, 5: 28, 10: 9, 1: 6, 15: 6, 2: 5, 6: 5},
}
# 2014: the seats of each list in each electoral unit, the first-stage seats plus the national remainder seats the
# record names unit by unit, by ballot position.
UNIT_SEATS_2014 = {
    1: {13: 4, 10: 3, 1: 1, 6: 1, 4: 1, 16: 1},
    2: {13: 4, 10: 2, 1: 1, 9: 1, 4: 1, 6: 1, 16: 1},
    3: {13: 5, 10: 2, 4: 1, 1: 1, 6: 1, 16: 1},
    4: {13: 4, 10: 2, 1: 1, 4: 1, 6: 1, 9: 1, 16: 1},
    5: {13: 5, 10: 3, 1: 2, 9: 1},
    6: {13: 5, 10: 3, 1: 1, 9: 1, 4: 1},
    7: {13: 5, 10: 2, 1: 2, 4: 1, 9: 1},
    8: {13: 4, 10: 4, 1: 1, 9: 1, 6: 1},
}


def fetch() -> None:
    for vote in VOTES:
        raw.fetch(SOURCE, vote.url, suffix="xlsx" if vote.layout == "izvoz" else vote.path.rsplit(".", 1)[1])
    for url in (*REPORTS.values(), *MANDATES.values(), *ARCHIVE_SEATS.values()):
        raw.fetch(SOURCE, url, suffix=url.rsplit(".", 1)[1])


def _page_seats(year: int, totals: dict[int, int | None]) -> dict[int, dict[int, int]]:
    """The seats an archived results page prints for each list in each unit, by ballot position. The page names
    lists by short labels, so each is paired with the ballot line whose national votes it shares."""
    from ea.sources.si import dvk_okraji  # imported here because dvk_okraji builds on this module

    url = ARCHIVE_SEATS[year]
    found = dvk_okraji.tables_at(url)
    _, _, printed = dvk_okraji.unit_votes(found, year, url)
    seats: dict[int, dict[int, int]] = {unit: {} for unit in ELECTORAL_UNITS}
    for label, by_unit in dvk_okraji.seat_table(found, url).items():
        lines = [position for position, votes in totals.items() if votes == printed.votes[label]]
        if len(lines) != 1:
            raise ValueError(f"{year}: {label} shares its national votes with ballot lines {lines}")
        for unit, n in by_unit.items():
            seats[unit][lines[0]] = n
    return seats


def unit_seats(year: int, totals: dict[int, int | None] | None = None) -> dict[int, dict[int, int]] | None:
    """Seats of each list in each electoral unit, by ballot position, checked against the national totals. Where the
    seats come from an archived page, `totals` are the ballot lines' declared national votes by ballot position."""
    if year not in NATIONAL_SEATS:
        return None
    if year in ARCHIVE_SEATS:
        if totals is None:
            raise ValueError(f"{year}: the ballot lines' national votes are needed to read the seats")
        seats = _page_seats(year, totals)
    elif year in MANDATES:
        found: dict[int, Counter[int]] = {}
        text = raw.cached(SOURCE, MANDATES[year]).path.read_text(encoding="utf-8-sig")
        for line in text.splitlines()[1:]:
            fields = line.strip().split(";")
            if len(fields) == 4:
                found.setdefault(int(fields[2]), Counter())[MANDATE_LISTS[year][fields[0]]] += 1
        seats = {unit: dict(counts) for unit, counts in found.items()}
    else:
        seats = UNIT_SEATS_2014
    if sorted(seats) != list(ELECTORAL_UNITS):
        raise ValueError(f"{year}: seats are missing for some electoral units")
    for unit, lists in seats.items():
        if sum(lists.values()) != 11:
            raise ValueError(f"{year}: unit {unit} holds {sum(lists.values())} seats, not 11")
    national: Counter[int] = Counter()
    for lists in seats.values():
        national.update(lists)
    if dict(national) != NATIONAL_SEATS[year]:
        raise ValueError(f"{year}: unit seats {dict(national)} disagree with the declared totals")
    if REPORTS.get(year, "").endswith(".json"):
        # The results site's own figures: each list's seats nationally and in each electoral unit, by list code.
        positions = MANDATE_LISTS[year]
        data = json.loads(raw.cached(SOURCE, REPORTS[year]).path.read_text(encoding="utf-8-sig"))["slovenija"]
        declared = {positions[str(line["st"])]: line["man"] for line in data["rez"]["rez"] if line["man"]}
        if declared != NATIONAL_SEATS[year]:
            raise ValueError(f"{year}: the results site declares {declared}")
        for unit in data["enote"]:
            lines = {positions[str(line["st"])]: line["man"] for line in unit["rez"]["rez"] if line.get("man")}
            if lines != seats[unit["st"]]:
                raise ValueError(f"{year}: unit {unit['st']} seats {lines} disagree with the mandate list")
    return seats


def plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def clean(text: str | None) -> str:
    return " ".join((text or "").replace("\xa0", " ").split())


def whole(text: str | None) -> int | None:
    if text is None or text.strip() in ("", "NULL"):
        return None
    return int(float(text.replace(",", ".")))


@dataclass
class Row:
    ve: str
    vo: str
    stv: str
    place: str
    ve_name: str
    vo_name: str
    registered: int | None
    voters: int | None
    cast: int | None
    invalid: int | None
    valid: int | None
    options: list[int | None]
    roll: int = 0
    certificates: int = 0


@dataclass
class Sheet:
    rows: list[Row]
    total: Row | None
    options: list[str]
    titles: dict[int, str] = field(default_factory=dict)


def find(names: list[str], *candidates: str, prefix: bool = False) -> int | None:
    for candidate in candidates:
        for position, name in enumerate(names):
            if name == candidate or (prefix and name.startswith(candidate)):
                return position
    return None


def load(vote: Vote) -> Sheet:
    if vote.layout == "izvoz":
        return load_izvoz(vote)
    artifact = raw.cached(SOURCE, vote.url)
    book = fastexcel.read_excel(str(artifact.path))
    frame = book.load_sheet_by_name(vote.sheet, header_row=0, dtypes="string").to_polars()
    header = list(frame.columns)
    names = [plain(column) for column in header]
    ve, vo, stv, place = (
        find(names, "ve"),
        find(names, "vo", "ovk"),
        find(names, "stv", "stvol", "tvol"),
        find(names, "volisce", "volise"),
    )
    cast, invalid = find(names, "oddan", "oddane", prefix=True), find(names, "neveljav", prefix=True)
    if None in (ve, vo, stv, place, cast, invalid):
        raise LookupError(f"{vote.path} lacks a station column: {header[:16]}")
    registered = find(names, "skupajvolivcev", "volivcev")
    voters = find(names, "skupajglasovalo", "glasovalo")
    valid = find(names, "skupajveljavnih", "veljavnih")
    roll, certificates = find(names, "imenik"), find(names, "potrdila", "potrdilo")
    ballots = [find(names, name) for name in ("glimenik", "glpotrdila", "glinv")]
    ve_name, vo_name = find(names, "imeve", "enota"), find(names, "imevo", "imeovk", "okraj")
    if vote.kind == "ep":
        first = next(i for i, name in enumerate(names) if name[:1].isdigit())
    elif vote.kind == "ref":
        first = len(header) - 2
    else:
        first = max(i for i in (ve, vo, stv, place, registered, voters, cast, invalid, valid) if i is not None) + 1
    options = list(range(first, len(header)))

    def number(line: tuple[object, ...], position: int | None) -> int | None:
        text = None if position is None else line[position]
        return None if text is None or str(text).strip() in ("", "NULL") else int(float(str(text).replace(",", ".")))

    def text(line: tuple[object, ...], position: int | None) -> str:
        return "" if position is None or line[position] is None else clean(str(line[position]))

    rows: list[Row] = []
    total: Row | None = None
    for line in frame.rows():
        if text(line, ve).lower() == "ve":
            continue
        row = Row(
            ve=text(line, ve),
            vo=text(line, vo),
            stv=text(line, stv),
            place=text(line, place),
            ve_name=text(line, ve_name),
            vo_name=text(line, vo_name),
            registered=number(line, registered),
            voters=number(line, voters),
            cast=number(line, cast),
            invalid=number(line, invalid),
            valid=number(line, valid),
            options=[number(line, position) for position in options],
            roll=number(line, roll) or 0,
            certificates=number(line, certificates) or 0,
        )
        if vote.kind == "ep":
            row.registered = row.roll + row.certificates
            row.voters = sum(number(line, position) or 0 for position in ballots)
        if row.stv in ("", "None"):
            if any(cell not in (None, "") for cell in line):
                total = row
            continue
        rows.append(row)
    return Sheet(rows, total, [header[position] for position in options])


TITLES = re.compile(r"^(?:(?:dr|mag|prof|doc)\.\s*)+", re.IGNORECASE)
IZVOZ_COLUMNS = (
    "Volilna enota",
    "Volilni okraj",
    "ID Volišča",
    "Volišče",
    "Št. volilnih upr.",
    "Udeležba",
    "Odstotek udeležbe",
    "Št. oddanih glasovnic",
    "Št. veljavnih glasovnic",
    "Št. neveljavnih glasovnic",
    "Odstotek preštetih glasovnic",
)
AREA_CELL = re.compile(r"^(?:VE|VO)\s+(\d+)\s*-\s*(.*)$")
VOTES_CELL = re.compile(r"\s*-\s*št\. glasov$")
ANSWER_CELL = re.compile(r"^Glasovi\s+(.+)$")
PREFERENCES_CELL = re.compile(r"\s*-\s*št\. pref\. glasov$")


def bare(name: str) -> str:
    """A ballot name without its academic titles, which the commission adds to some files and not others."""
    return TITLES.sub("", clean(name))


def count(cell: object) -> int | None:
    text = None if cell is None else str(cell).strip()
    return None if text in (None, "", "NULL") else int(float(text.replace(",", ".")))


def grid(book: fastexcel.ExcelReader, sheet: str) -> list[tuple[object, ...]]:
    return book.load_sheet_by_name(sheet, header_row=None, dtypes="string").to_polars().rows()


def header_row(rows: list[tuple[object, ...]], sheet: str) -> int:
    for position, line in enumerate(rows):
        if line[0] == "Volilna enota":
            return position
    raise LookupError(f"{sheet} has no header row")


def load_izvoz(vote: Vote) -> Sheet:
    """Read the commission's izvoz.xlsx export: a Podatki sheet of printed totals, a Kandidati sheet, and a sheet of
    figures by polling station with one '<party or candidate>- št. glasov' column per ballot line."""
    book = fastexcel.read_excel(str(raw.cached(SOURCE, vote.url).path))
    stations = grid(book, "Podatki po voliščih")
    top = header_row(stations, "Podatki po voliščih")
    header = [clean(None if cell is None else str(cell)) for cell in stations[top]]
    if tuple(header[: len(IZVOZ_COLUMNS)]) != IZVOZ_COLUMNS:
        raise LookupError(f"{vote.url} has unexpected station columns: {header[:12]}")
    names: list[str] = []
    positions: list[int] = []
    titles: dict[int, str] = {}
    lists = 0
    in_list = False
    nested = any(PREFERENCES_CELL.search(cell) for cell in header)
    for position in range(len(IZVOZ_COLUMNS), len(header)):
        cell = header[position]
        if VOTES_CELL.search(cell):
            name = VOTES_CELL.sub("", cell)
            lists += 1
            in_list = True
            if nested:
                if top and stations[0][position]:
                    titles[len(names)] = clean(str(stations[0][position]))
                name = f"{lists}. {name}"
            names.append(name)
            positions.append(position)
        elif PREFERENCES_CELL.search(cell):
            if in_list:
                in_list = False  # the first preference column of a list is the list's own total
                continue
            names.append(PREFERENCES_CELL.sub("", cell))
            positions.append(position)
    if not names:
        for position in range(len(IZVOZ_COLUMNS), len(header)):
            answer = ANSWER_CELL.match(header[position])
            if answer:
                names.append(answer.group(1))
                positions.append(position)
    if not names:
        raise LookupError(f"{vote.url} has no ballot columns")
    rows: list[Row] = []
    for line in stations[top + 1 :]:
        if not any(cell not in (None, "") for cell in line) or str(line[0]).startswith("*"):
            continue  # a blank line, or a footnote beneath the table
        unit, vo = AREA_CELL.match(clean(str(line[0]))), AREA_CELL.match(clean(str(line[1])))
        if unit is None or vo is None:
            raise ValueError(f"{vote.url}: unreadable area in {line[:4]}")
        rows.append(
            Row(
                ve=unit.group(1),
                vo=vo.group(1),
                stv=clean(str(line[2])),
                place=clean(str(line[3])).rstrip("* "),
                ve_name=clean(unit.group(2)),
                vo_name=clean(vo.group(2)),
                registered=count(line[4]),
                voters=count(line[5]),
                cast=count(line[7]),
                invalid=count(line[9]),
                valid=count(line[8]),
                options=[count(line[position]) for position in positions],
            )
        )
    return Sheet(rows, izvoz_total(vote, book, names), names, titles)


def izvoz_total(vote: Vote, book: fastexcel.ExcelReader, names: list[str]) -> Row:
    """The national row of the Podatki sheet, with candidate totals from the Kandidati sheet where the list totals
    are all the Podatki sheet prints."""
    printed = grid(book, "Podatki")
    top = header_row(printed, "Podatki")
    heads = [clean(None if cell is None else str(cell)) for cell in printed[top]]
    line = next(row for row in printed[top + 1 :] if not clean(str(row[0] or "")) and not clean(str(row[1] or "")))
    by_name = {
        bare(VOTES_CELL.sub("", head)): count(line[position])
        for position, head in enumerate(heads)
        if VOTES_CELL.search(head)
    }
    for position, head in enumerate(heads):
        answer = ANSWER_CELL.match(head)
        if answer:
            by_name[bare(answer.group(1))] = count(line[position])
    candidates: dict[str, list[int | None]] = {}
    if any(PREFERENCES_CELL.search(name) for name in names) or any(re.match(r"\d+\.", name) for name in names):
        sheet = grid(book, "Kandidati")
        candidates = {"": [count(row[4]) for row in sheet[1:] if row[1] and count(row[4]) is not None]}
    options: list[int | None] = []
    queue = iter(candidates.get("", []))
    for name in names:
        listed = re.match(r"\d+\.\s*(.*)", name)
        if listed:
            options.append(by_name[bare(listed.group(1))])
        elif candidates:
            options.append(next(queue))
        else:
            options.append(by_name[bare(name)])
    return Row(
        ve="",
        vo="",
        stv="",
        place="",
        ve_name="",
        vo_name="",
        registered=count(line[2]),
        voters=count(line[3]),
        cast=count(line[5]),
        invalid=count(line[7]),
        valid=count(line[6]),
        options=options,
    )


SEATS_CELL = re.compile(r"\s*-\s*št\. mandatov\s*$")
EP_SEATS = {2014: 8, 2024: 9}


def printed_seats(vote: Vote) -> dict[str, int]:
    """The seats the Podatki sheet of an izvoz workbook prints for each list, by list name."""
    book = fastexcel.read_excel(str(raw.cached(SOURCE, vote.url).path))
    printed = grid(book, "Podatki")
    top = header_row(printed, "Podatki")
    heads = [clean(None if cell is None else str(cell)) for cell in printed[top]]
    line = next(row for row in printed[top + 1 :] if not clean(str(row[0] or "")) and not clean(str(row[1] or "")))
    return {
        bare(SEATS_CELL.sub("", head)): value
        for position, head in enumerate(heads)
        if SEATS_CELL.search(head) and (value := count(line[position])) is not None
    }


def elected(vote: Vote) -> dict[str, int]:
    """The Mandat column of an izvoz EP workbook's Kandidati sheet, by candidate name."""
    book = fastexcel.read_excel(str(raw.cached(SOURCE, vote.url).path))
    sheet = grid(book, "Kandidati")
    heads = [clean(None if cell is None else str(cell)) for cell in sheet[0]]
    if heads[:3] != ["Stranka", "Kandidat", "Mandat"]:
        raise LookupError(f"{vote.url} has unexpected candidate columns: {heads[:5]}")
    return {bare(clean(str(row[1]))): count(row[2]) or 0 for row in sheet[1:] if row[1]}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


DVK_SOURCE = key("source", SOURCE)
CHAMBER_BODY = key("body", "national-assembly")
PRESIDENCY = key("office", "president")
SYSTEMS = {
    "dz": key("electoral_system", "dz"),
    "pres": key("electoral_system", "president"),
    "ep": key("electoral_system", "ep"),
    "ref": key("electoral_system", "referendum"),
}
METHOD = {
    "dz": (
        "Counted by the polling station (volišče) and published by the State Election Commission (Državna volilna "
        "komisija). The 88 deputies chosen by list are elected in 8 electoral units of 11 deputies each; every "
        "electoral unit is a contest here. Registered voters are the electoral roll plus voters holding a voting "
        "certificate, ballots cast are the ballots found in the box, and valid votes are the valid list votes. "
        "Stations numbered 970 and above are not places: they hold postal votes, votes cast at any station (OMNIA), "
        "votes from diplomatic and consular missions, and early voting. They have no roll of their own, so no "
        "registered voters are stored for them. Preference votes for candidates are not published at this level. "
        "The two seats of the Italian and Hungarian national communities are elected separately and are not here."
    ),
    "pres": (
        "Counted by the polling station (volišče) and published by the State Election Commission (Državna volilna "
        "komisija). Registered voters are the electoral roll plus voters holding a voting certificate, ballots cast "
        "are the ballots found in the box, and valid votes are the valid votes for candidates. Stations numbered 970 "
        "and above are not places: they hold postal votes, votes cast at any station (OMNIA), and votes from "
        "diplomatic and consular missions. They have no roll of their own, so no registered voters are stored for "
        "them."
    ),
    "ep": (
        "Counted by the polling station (volišče) and published by the State Election Commission (Državna volilna "
        "komisija). Slovenia forms one electoral unit for the European Parliament. Registered voters are the "
        "electoral roll plus voters holding a voting certificate, voters are those who voted by roll, certificate, "
        "or as disabled voters, and ballots cast are the ballots found in the box. Each candidate's preference "
        "votes are stored under the candidate. Stations numbered 970 and above are not places: they hold postal "
        "votes, votes cast at any station, and votes from diplomatic and consular missions. They have no roll of "
        "their own, so no registered voters are stored for them."
    ),
    "ref": (
        "Counted by the polling station (volišče) and published by the State Election Commission (Državna volilna "
        "komisija). Registered voters are the electoral roll plus voters holding a voting certificate, ballots cast "
        "are the ballots found in the box, and valid votes are the valid ballots for or against. Stations numbered "
        "970 and above, and the diplomatic and consular missions, are not places: they hold postal votes and votes "
        "cast at any station (OMNIA). They have no roll of their own, so no registered voters are stored for them. "
        "A law is rejected at a legislative referendum when most valid votes are against it and the votes against "
        "are at least a fifth of all registered voters. The For option wins when the law stands, even where more "
        "valid votes were against it but fewer than a fifth of the registered voters."
    ),
}
METHOD_REF_2012 = (
    METHOD["ref"].split(" A law is rejected")[0]
    + " At this referendum a law was rejected when most valid votes were against it: the requirement that the votes "
    "against be at least a fifth of all registered voters entered the Constitution in 2013."
)
METHOD_ADVISORY = (
    METHOD["ref"].split(" A law is rejected")[0]
    + " This was an advisory referendum: the National Assembly asked the voters a question, and the result does not "
    "bind it, so no outcome is declared."
)
DAY_TITLES = {
    date(2022, 11, 27): (
        "2022 Slovenian referendums on three amended acts",
        "Referendumi o noveli Zakona o dolgotrajni oskrbi, Zakona o Radioteleviziji Slovenija in Zakona o vladi 2022",
    ),
    date(2024, 6, 9): (
        "2024 Slovenian advisory referendums",
        "Posvetovalni referendumi 2024",
    ),
    date(2026, 10, 11): (
        "2026 Slovenian referendums",
        "Referendumi 2026",
    ),
}
SEAT_NOTE = (
    " Each list's seats in the electoral unit are the commission's declared outcome: the seats it won in the unit "
    "from the unit allocation and the national remainder allocation. The two seats of the national communities are "
    "not in these contests."
)
SYSTEM_RULES: dict[str, tuple[date, dict[str, object], str, str]] = {
    "dz": (
        date(2011, 1, 1),
        {
            "formula": "droop",
            "remainders": "national",
            "threshold": {"level": "list", "scope": "national", "percent": 4},
        },
        "National Assembly electoral system",
        "Volilni sistem Državnega zbora",
    ),
    "pres": (
        date(1992, 1, 1),
        {"formula": "two_round", "majority": 0.5, "advance": 2},
        "Presidential electoral system",
        "Volilni sistem predsednika republike",
    ),
    "ep": (
        date(2004, 5, 1),
        {"formula": "dhondt"},
        "European Parliament electoral system in Slovenia",
        "Volilni sistem za Evropski parlament",
    ),
    "ref": (
        date(1994, 1, 1),
        {"formula": "referendum"},
        "Legislative referendum",
        "Zakonodajni referendum",
    ),
}


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def title(text: str) -> str:
    return " ".join(word.capitalize() for word in clean(text).split(" "))


def district(ve: str, vo: str) -> tuple[int | None, int | None]:
    unit = int(ve)
    unit = unit // 1000 if unit >= 1000 else unit
    seat = int(vo)
    seat = seat % 1000 if seat >= 1000 else seat
    if unit not in ELECTORAL_UNITS:
        return None, None
    return unit, seat if seat in DISTRICTS else None


def station_kind(place: str) -> str:
    text = place.upper()
    if "POŠTA" in text:
        return "postal_district"
    if "DKP" in text or "AMBASADA" in text:
        return "abroad_mission"
    if "PO POŠTI" in text:
        return "postal_district"
    if "OMNIA" in text or "POSEBNI VOLILNI" in text or "PREDČASNO" in text:
        return "aggregate"
    return "standard"


def packages() -> list[str]:
    return ["si.dvk/geo", "si.dvk/registry", *dict.fromkeys(slug(vote) for vote in VOTES)]


def slug(vote: Vote) -> str:
    word = {"dz": "dz", "pres": "pres", "ep": "ep", "ref": "ref"}[vote.kind]
    return f"si.dvk/{word}-{vote.day}"


def emit() -> None:
    package.run("load", packages())


def parse() -> None:
    geo().write()
    registry().write()
    sheets = {vote: load(vote) for vote in VOTES}
    report: dict[str, dict[str, object]] = {}
    for vote, sheet in sheets.items():
        report[vote.url if vote.question else slug(vote)] = reconcile(vote, sheet)
        if vote.kind != "ref":
            election(vote, sheet).write()
    days: dict[date, list[tuple[Vote, Sheet]]] = {}
    for vote, sheet in sheets.items():
        if vote.kind == "ref":
            days.setdefault(vote.day, []).append((vote, sheet))
    for held in days.values():
        referendum(held).write()
    for slugged, found in report.items():
        print(f"{slugged}: totals {found}", flush=True)


def reconcile(vote: Vote, sheet: Sheet) -> dict[str, object]:
    """Compare the sum of the station rows with the national total row the commission prints."""
    total = sheet.total
    if total is None:
        raise ValueError(f"{vote.path} prints no total row")
    problems: list[str] = []
    for field_name in ("registered", "voters", "cast", "invalid", "valid"):
        got = sum(getattr(row, field_name) or 0 for row in sheet.rows)
        wanted = getattr(total, field_name)
        if wanted is not None and wanted != got:
            problems.append(f"{field_name}: stations {got:,}, printed {wanted:,}")
    for index, name in enumerate(sheet.options):
        got = sum(row.options[index] or 0 for row in sheet.rows)
        if total.options[index] != got:
            problems.append(f"{name}: stations {got:,}, printed {total.options[index]}")
    if problems:
        raise ValueError(f"{vote.path}: station rows disagree with the printed total: {problems}")
    return {
        "stations": len(sheet.rows),
        "registered": total.registered,
        "cast": total.cast,
        "valid": total.valid,
        "matches_printed": True,
    }


RAW_FILES = sorted(
    {vote.url for vote in VOTES} | set(REPORTS.values()) | set(MANDATES.values()) | set(ARCHIVE_SEATS.values())
)
PREFERENCE: set[str] = set()


def geo() -> Package:
    p = Package(
        "si.dvk/geo",
        SOURCE,
        "Slovenia's 8 National Assembly electoral units and 88 electoral districts, as numbered by the State "
        "Election Commission",
    )
    p.external.add(SLOVENIA)
    for code, rank, kind, noun in (
        ("si.ve", 1, "electoral_district", "electoral unit"),
        ("si.vo", 2, "electoral_district", "electoral district"),
        ("si.volisce", 3, "precinct", "polling station"),
    ):
        p.add("scheme", code=code, polity_id=SLOVENIA, kind=kind, rank=rank, noun=noun, code_system=None)
    vote = next(v for v in VOTES if v.kind == "dz" and v.day.year == 2018)
    units: dict[int, str] = {}
    seats: dict[tuple[int, int], str] = {}
    for row in load(vote).rows:
        unit, seat = district(row.ve, row.vo)
        if unit is not None:
            units.setdefault(unit, row.ve_name)
            if seat is not None:
                seats.setdefault((unit, seat), row.vo_name)
    for unit in ELECTORAL_UNITS:
        id = area("si.ve", str(unit))
        p.add("area", id=id, scheme_code="si.ve")
        p.add("area_code", area_id=id, code=str(unit))
        p.name(id, title(units[unit]), "sl", "official")
    for (unit, seat), text in sorted(seats.items()):
        id = area("si.vo", f"{unit}.{seat}")
        p.add("area", id=id, scheme_code="si.vo")
        p.add("area_code", area_id=id, code=f"{unit}.{seat}")
        p.add("area_parent", area_id=id, parent_id=area("si.ve", str(unit)))
        p.name(id, title(text), "sl", "official")
    return p


def registry() -> Package:
    p = Package(
        "si.dvk/registry",
        SOURCE,
        "The State Election Commission as a source, the National Assembly, the presidency, and the electoral systems",
        depends=["si.dvk/geo"],
    )
    p.external.add(SLOVENIA)
    p.add(
        "source",
        id=DVK_SOURCE,
        kind="official_body",
        publisher="Državna volilna komisija",
        title="Results of elections and referendums by polling station",
        url="https://www.dvk-rs.si/arhiv-volitev/",
        license="Public information of the State Election Commission",
        license_url="https://www.dvk-rs.si/informacije-javnega-znacaja/",
        attribution="Državna volilna komisija Republike Slovenije",
    )
    for url in RAW_FILES:
        p.artifact(raw.cached(SOURCE, url), DVK_SOURCE)
    p.add("body", id=CHAMBER_BODY, polity_id=SLOVENIA, kind="lower_house")
    p.name(CHAMBER_BODY, "National Assembly", "en", "official")
    p.name(CHAMBER_BODY, "Državni zbor", "sl", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=SLOVENIA, kind="head_of_state")
    p.name(PRESIDENCY, "President of the Republic of Slovenia", "en", "official")
    p.name(PRESIDENCY, "Predsednik Republike Slovenije", "sl", "native", False)
    for code, (since, rules, english, native) in SYSTEM_RULES.items():
        id = SYSTEMS[code]
        p.add(
            "electoral_system", id=id, polity_id=SLOVENIA, valid_from=since, valid_to=None, rules=rules, rules_version=1
        )
        p.name(id, english, "en", "official")
        p.name(id, native, "sl", "native", False)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=METHOD[code], lang="en")
    return p


def _overfull(p: Package, sheet: Sheet) -> None:
    found: Counter[str] = Counter()
    for row in sheet.rows:
        if row.registered and row.cast and row.cast > row.registered:
            found[station_kind(row.place)] += 1
    if not found:
        return
    parts = ", ".join(
        f"{n} {kind.replace('_', ' ')} station{'s' if n > 1 else ''}" for kind, n in sorted(found.items())
    )
    p.accept(
        "accounting.turnout",
        f"the commission prints more ballots in the box than registered voters at {parts}: early-voting, "
        "any-station (OMNIA), mission, and postal stations have no roll of their own, so the few voters registered "
        "there are those holding voting certificates, and a standard station can exceed its roll by the same "
        "certificates; the figures are stored as printed",
    )


def _facts(
    p: Package,
    sheet: Sheet,
    result_set: str | dict[int, str],
    units: list[str],
    options: Mapping[tuple[int, int], str],
) -> None:
    """Stage the accounting and option values of every station row."""
    sets: list[str] = []
    unit_ids: list[str] = []
    option_ids: list[str | None] = []
    measures: list[int] = []
    values: list[int] = []

    def add(rs: str, unit: str, option: str | None, measure: int, value: int) -> None:
        sets.append(rs)
        unit_ids.append(unit)
        option_ids.append(option)
        measures.append(measure)
        values.append(value)

    for row, unit in zip(sheet.rows, units, strict=True):
        electoral_unit, _ = district(row.ve, row.vo)
        rs = result_set if isinstance(result_set, str) else result_set[electoral_unit or 0]
        # Stations without a roll of their own print a roll of zero beside real ballots; the zero is not a count.
        if row.registered is not None and (row.registered > 0 or not row.cast):
            add(rs, unit, None, 2, row.registered)
        for measure, value in ((14, row.voters), (3, row.cast), (5, row.invalid), (4, row.valid)):
            if value is not None:
                add(rs, unit, None, measure, value)
        for index, count in enumerate(row.options):
            option = options.get((electoral_unit or 0, index) if isinstance(result_set, dict) else (0, index))
            if option is None:
                if count:
                    raise ValueError(f"option {index} has votes in unit {electoral_unit} but no ballot line")
                continue
            if count is not None:
                add(rs, unit, option, 7 if option in PREFERENCE else 1, count)
    size = len(values)
    p.frame(
        "result_value",
        pl.DataFrame(
            {
                "result_set_id": sets,
                "unit_id": unit_ids,
                "option_id": option_ids,
                "channel": [0] * size,
                "measure": measures,
                "count_round": [0] * size,
                "flags": [0] * size,
                "value": values,
            },
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "count_round": pl.Int16,
                "flags": pl.Int16,
                "value": pl.Int64,
            },
        ),
    )
    _overfull(p, sheet)


def _units(p: Package, vote: Vote, sheet: Sheet, added: set[str] | None = None) -> list[str]:
    counts = Counter((row.ve, row.vo, row.stv) for row in sheet.rows)
    named = Counter((row.ve, row.vo, row.stv, changeset.slug(row.place)) for row in sheet.rows)
    seen: Counter[tuple[str, str, str, str]] = Counter()
    units: list[str] = []
    for row in sheet.rows:
        code = f"{vote.day}:{row.ve}:{row.vo}:{row.stv}"
        if counts[(row.ve, row.vo, row.stv)] > 1:
            code += f":{changeset.slug(row.place)}"
            # Missions abroad with too few voters to print are anonymized into several rows under one name.
            same = (row.ve, row.vo, row.stv, changeset.slug(row.place))
            if named[same] > 1:
                seen[same] += 1
                code += f":{seen[same]}"
        unit = key("unit", code)
        units.append(unit)
        if added is not None:
            if unit in added:
                continue
            added.add(unit)
        electoral_unit, seat = district(row.ve, row.vo)
        if electoral_unit is None:
            relation, place = "unlocated", None
        else:
            relation = "within"
            place = (
                area("si.vo", f"{electoral_unit}.{seat}") if seat is not None else area("si.ve", str(electoral_unit))
            )
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=SLOVENIA,
            scheme_code="si.volisce",
            code=code,
            area_relation=relation,
            area_id=place,
            kind=station_kind(row.place),
        )
        if row.place:
            p.name(unit, row.place, "sl", "official")
    if len(set(units)) != len(units):
        raise ValueError(f"{vote.day}: polling-station codes collide")
    return units


def _method(vote: Vote) -> str:
    if vote.advisory:
        return METHOD_ADVISORY
    if vote.kind == "ref" and vote.day.year < 2013:
        return METHOD_REF_2012
    return METHOD[vote.kind] + (SEAT_NOTE if vote.kind == "dz" and vote.day.year in NATIONAL_SEATS else "")


def _result_set(p: Package, election_id: str, contest: str, vote: Vote, tag: str) -> str:
    result_set = key("result_set", tag)
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=DVK_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=_method(vote),
        lang="en",
    )
    return result_set


def _declare(p: Package, result_set: str, sheet: Sheet, options: Mapping[int, str]) -> None:
    """Store the printed national total as the declared figures of a nationwide contest."""
    total = sheet.total
    if total is None:
        return
    for measure, value in (
        (2, total.registered),
        (14, total.voters),
        (3, total.cast),
        (5, total.invalid),
        (4, total.valid),
    ):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for index, option in options.items():
        value = total.options[index]
        if value is not None:
            p.add(
                "declared",
                result_set_id=result_set,
                option_id=option,
                measure=7 if option in PREFERENCE else 1,
                value=value,
            )


def _election(p: Package, vote: Vote, kind: str, slug_path: str) -> str:
    id = key("election", vote.kind, vote.day)
    p.add("election", id=id, polity_id=SLOVENIA, kind=kind, date_from=vote.day, date_to=vote.day, status="held")
    p.name(id, vote.title, "en", "official")
    p.name(id, vote.native, "sl", "native", False)
    p.add("slug", path=slug_path, entity_id=id, is_current=True)
    return id


def election(vote: Vote, sheet: Sheet) -> Package:
    build = {"dz": assembly, "pres": president, "ep": european}[vote.kind]
    return build(vote, sheet)


def _package(vote: Vote, summary: str, depends: list[str] | None = None) -> Package:
    p = Package(slug(vote), SOURCE, summary, depends=["si.dvk/geo", "si.dvk/registry", *(depends or [])])
    p.external.add(SLOVENIA)
    p.uses(raw.cached(SOURCE, vote.url))
    if vote.kind == "dz":
        for found in (REPORTS.get(vote.day.year), MANDATES.get(vote.day.year), ARCHIVE_SEATS.get(vote.day.year)):
            if found is not None:
                p.uses(raw.cached(SOURCE, found))
    return p


def _ballot_line(name: str, index: int) -> tuple[str, int]:
    label = clean(name)
    found = re.match(r"(\d+)\.\s*(.*)", label)
    return (found.group(2), int(found.group(1))) if found else (label, index + 1)


def assembly(vote: Vote, sheet: Sheet) -> Package:
    p = _package(vote, f"The {vote.day.year} Slovenian National Assembly election by polling station")
    election_id = _election(p, vote, "general", f"si/{vote.day}-general")
    full: dict[int, str] = {}
    if vote.day.year == 2011:
        book = fastexcel.read_excel(str(raw.cached(SOURCE, vote.url).path))
        for line in book.load_sheet_by_name("Šifrant list", header_row=0, dtypes="string").to_polars().rows():
            full[int(line[0])] = clean(line[1])
    lines = [_ballot_line(name, index) for index, name in enumerate(sheet.options)]
    parties: dict[int, str] = {}
    for index, (label, position) in enumerate(lines):
        party = parties[index] = key("party", vote.day, position, label)
        official = full.get(position, label)
        p.add("party", id=party, kind="party", polity_id=SLOVENIA)
        p.name(party, official, "sl", "official")
        if official != label:
            p.name(party, label, "sl", "short", False)
    units = _units(p, vote, sheet)
    by_unit: dict[int, list[Row]] = {}
    for row in sheet.rows:
        electoral_unit, _ = district(row.ve, row.vo)
        if electoral_unit is None:
            raise ValueError(f"{vote.day}: station {row.place} is outside the 8 electoral units")
        by_unit.setdefault(electoral_unit, []).append(row)
    result_sets: dict[int, str] = {}
    options: dict[tuple[int, int], str] = {}
    seats = unit_seats(
        vote.day.year,
        None
        if sheet.total is None
        else {position: sheet.total.options[index] for index, (_, position) in enumerate(lines)},
    )
    for unit in ELECTORAL_UNITS:
        name = title(next(row.ve_name for row in by_unit[unit] if row.ve_name))
        contest = key("contest", vote.day, unit)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=CHAMBER_BODY,
            district_area_id=area("si.ve", str(unit)),
            magnitude=11,
            round_number=1,
            electoral_system_id=SYSTEMS["dz"],
            default_result_kind="counted",
        )
        p.name(contest, f"Electoral unit {unit} ({name})", "en", "official")
        p.name(contest, f"{unit}. volilna enota ({name})", "sl", "native", False)
        p.add(
            "slug", path=f"si/{vote.day}-general/unit-{unit}-{changeset.slug(name)}", entity_id=contest, is_current=True
        )
        result_sets[unit] = _result_set(p, election_id, contest, vote, f"{vote.day}:{unit}")
        for index, (label, position) in enumerate(lines):
            if not any(row.options[index] for row in by_unit[unit]):
                continue
            option = options[(unit, index)] = key("option", vote.day, unit, position, label)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=parties[index],
                ballot_position=position,
                ballot_label=full.get(position, label),
            )
        if seats is not None:
            by_position = {
                position: options[(unit, index)]
                for index, (_, position) in enumerate(lines)
                if (unit, index) in options
            }
            for position, count in sorted(seats[unit].items()):
                p.add(
                    "outcome", result_set_id=result_sets[unit], option_id=by_position[position], kind="won", seats=count
                )
    _facts(p, sheet, result_sets, units, options)
    if seats is not None and vote.layout == "izvoz":
        won: Counter[str] = Counter()
        for unit_lists in seats.values():
            for position, n in unit_lists.items():
                won[bare(next(label for label, number in lines if number == position))] += n
        printed = {name: n for name, n in printed_seats(vote).items() if n}
        if dict(won) != printed:
            raise ValueError(f"{vote.day}: seats {dict(won)} disagree with the printed seats {printed}")
    return p


def president(vote: Vote, sheet: Sheet) -> Package:
    first = next(v for v in VOTES if v.kind == "pres" and v.round == 1 and v.day.year == vote.day.year)
    p = _package(
        vote,
        f"The {vote.day.year} Slovenian presidential election, {'first' if vote.round == 1 else 'second'} round, "
        "by polling station",
        depends=[] if vote.round == 1 else [slug(first)],
    )
    election_id = _election(p, vote, "general" if vote.round == 1 else "runoff", f"si/{vote.day}-presidential")
    contest = key("contest", vote.day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="office",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=vote.round,
        previous_round_id=None if vote.round == 1 else key("contest", first.day),
        electoral_system_id=SYSTEMS["pres"],
        default_result_kind="counted",
    )
    p.name(contest, vote.title, "en", "official")
    p.add("slug", path=f"si/{vote.day}-presidential/president", entity_id=contest, is_current=True)
    result_set = _result_set(p, election_id, contest, vote, str(vote.day))
    options: dict[int, str] = {}
    for index, name in enumerate(sheet.options):
        name = clean(name)
        person = key("person", f"pres:{vote.day.year}:{bare(name) if vote.layout == 'izvoz' else name}")
        option = options[index] = key("option", vote.day, index + 1, name)
        if vote.round == 1:
            p.add("person", id=person)
            p.name(person, title(name), "sl", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=index + 1,
            ballot_label=title(name),
        )
        p.add(
            "candidacy",
            id=key("candidacy", vote.day, index + 1, name),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=title(name),
        )
    units = _units(p, vote, sheet)
    _facts(p, sheet, result_set, units, {(0, index): option for index, option in options.items()})
    _declare(p, result_set, sheet, options)
    return p


def european(vote: Vote, sheet: Sheet) -> Package:
    p = _package(vote, f"The {vote.day.year} European Parliament election in Slovenia by polling station")
    election_id = _election(p, vote, "general", f"si/{vote.day}-european-parliament")
    contest = key("contest", vote.day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        magnitude=EP_SEATS[vote.day.year],
        round_number=1,
        electoral_system_id=SYSTEMS["ep"],
        default_result_kind="counted",
    )
    p.name(contest, vote.title, "en", "official")
    p.add("slug", path=f"si/{vote.day}-european-parliament/slovenia", entity_id=contest, is_current=True)
    result_set = _result_set(p, election_id, contest, vote, str(vote.day))
    options: dict[int, str] = {}
    candidacies: dict[int, str] = {}
    current: str | None = None
    position = 0
    seen: set[str] = set()
    for index, name in enumerate(sheet.options):
        label = clean(name)
        if re.match(r"\d+\.", label):
            text, number = _ballot_line(label, index)
            party = key("party", vote.day, number, text)
            official = sheet.titles.get(index, text)
            p.add("party", id=party, kind="party", polity_id=SLOVENIA)
            p.name(party, official, "sl", "official")
            if official != text:
                p.name(party, text, "sl", "short", False)
            current = options[index] = key("option", vote.day, number, text)
            p.add(
                "contest_option",
                id=current,
                contest_id=contest,
                kind="list",
                party_id=party,
                ballot_position=number,
                ballot_label=text,
            )
            position = 0
            continue
        if current is None or label in seen:
            raise ValueError(f"{label} is not a candidate of a list")
        seen.add(label)
        position += 1
        person = key("person", f"ep:{vote.day}:{label}")
        option = options[index] = key("option", vote.day, current, position, label)
        PREFERENCE.add(option)
        candidacies[index] = key("candidacy", vote.day, current, position, label)
        p.add("person", id=person)
        p.name(person, title(label), "sl", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            parent_option_id=current,
            ballot_position=position,
            ballot_label=title(label),
        )
        p.add(
            "candidacy",
            id=candidacies[index],
            person_id=person,
            option_id=option,
            role="list_member",
            list_position=position,
            ballot_name=title(label),
        )
    units = _units(p, vote, sheet)
    _facts(p, sheet, result_set, units, {(0, index): option for index, option in options.items()})
    _declare(p, result_set, sheet, options)
    if vote.layout == "izvoz":
        _elect(p, vote, sheet, result_set, options, candidacies)
    return p


def _elect(
    p: Package, vote: Vote, sheet: Sheet, result_set: str, options: dict[int, str], candidacies: dict[int, str]
) -> None:
    """Store the candidates the Kandidati sheet's Mandat column marks as elected, and the seats each list won."""
    marked = elected(vote)
    owner: dict[int, str] = {}
    current = ""
    for index, name in enumerate(sheet.options):
        if index in candidacies:
            owner[index] = current
        else:
            current = options[index]
    won: Counter[str] = Counter()
    for index, candidacy in candidacies.items():
        label = bare(clean(sheet.options[index]))
        if label not in marked:
            raise ValueError(f"{vote.day}: {label} is missing from the Kandidati sheet")
        if marked[label]:
            won[owner[index]] += 1
            p.add("outcome", result_set_id=result_set, option_id=owner[index], candidacy_id=candidacy, kind="elected")
    if sum(won.values()) != EP_SEATS[vote.day.year]:
        raise ValueError(f"{vote.day}: {sum(won.values())} candidates are marked elected")
    for option, seats in won.items():
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=seats)


def referendum(held: list[tuple[Vote, Sheet]]) -> Package:
    """One package for the referendum day: one contest for each question put to the voters that day."""
    first = held[0][0]
    if len(held) == 1:
        p = _package(first, f"The {first.day} Slovenian referendum, {first.note}, by polling station")
        vote = first
    else:
        title, native = DAY_TITLES[first.day]
        vote = replace(first, title=title, native=native)
        p = _package(vote, f"The {first.day} Slovenian referendums, {len(held)} questions, by polling station")
        for other, _ in held[1:]:
            p.uses(raw.cached(SOURCE, other.url))
    election_id = _election(p, vote, "referendum", f"si/{first.day}-referendum")
    added: set[str] = set()
    for question, sheet in held:
        units = _units(p, question, sheet, added)
        tag = f"{question.day}:{question.question}" if question.question else str(question.day)
        contest = key("contest", question.day, question.question) if question.question else key("contest", question.day)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="ballot_question",
            round_number=1,
            electoral_system_id=SYSTEMS["ref"],
            default_result_kind="counted",
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        p.name(contest, question.note, "en", "official")
        p.name(contest, question.native, "sl", "native", False)
        p.add(
            "slug",
            path=f"si/{question.day}-referendum/{question.question or 'law'}",
            entity_id=contest,
            is_current=True,
        )
        result_set = _result_set(p, election_id, contest, question, tag)
        if [plain(name) for name in sheet.options] != ["za", "proti"]:
            raise ValueError(f"{tag}: expected ZA and PROTI columns, found {sheet.options}")
        options: dict[int, str] = {}
        for index, (label, position) in enumerate((("For", 1), ("Against", 2))):
            option = options[index] = (
                key("option", tag, label) if question.question else key("option", question.day, label)
            )
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        _facts(p, sheet, result_set, units, {(0, index): option for index, option in options.items()})
        _declare(p, result_set, sheet, options)
        if not question.advisory:
            _decide(p, question, sheet, result_set, options)
    return p


def _decide(p: Package, vote: Vote, sheet: Sheet, result_set: str, options: dict[int, str]) -> None:
    """Record whether the law was adopted or rejected under the rule in force on the day of the vote."""
    total = sheet.total
    if total is None or total.registered is None or total.options[0] is None or total.options[1] is None:
        raise ValueError(f"{vote.day}: the printed total lacks what the rule needs")
    yes, no = total.options[0], total.options[1]
    # Since 2013 a law is rejected only when the votes against are also at least a fifth of the registered voters.
    fifth = vote.day.year < 2013 or no * 5 >= total.registered
    rejected = no > yes and fifth
    p.add("outcome", result_set_id=result_set, option_id=options[0], kind="lost" if rejected else "won")
    p.add("outcome", result_set_id=result_set, option_id=options[1], kind="won" if rejected else "lost")
