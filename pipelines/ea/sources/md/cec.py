import re
from datetime import date
from uuid import uuid5

import fastexcel

from ea import package, raw
from ea.package import Package
from ea.sources.md import decisions

SOURCE = "md.cec"
BASE = "https://a.cec.md/index.php/ro/upload"
MOLDOVA = str(uuid5(package.NAMESPACE, "polity\x1fMD"))
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = 1, 2, 3, 4, 5, 14
PAGE = "https://a.cec.md/index.php/ro/alegeri-si-referendumuri-2830.html"

URLS = {
    "ps2014": f"{BASE}/2/89886/attached_files",
    "ps2014-districts": f"{BASE}/1/89886/attached_files",
    "ps2014-totals": f"{BASE}/3/89886/attached_files",
    "ps2021": f"{BASE}/620f7ee327cca/99908/attached_files",
    "pres2016-1": f"{BASE}/1/90358/attached_files",
    "pres2016-2": f"{BASE}/1/90359/attached_files",
    "pres2024-1": f"{BASE}/6858ff67671b8/113995/attached_files",
    "pres2024-2": f"{BASE}/68590e5edd817/113998/attached_files",
    "ref2024": f"{BASE}/67aee6476d633/111800/attached_files",
    "ref2019-1": "http://a.cec.md/storage/ckfinder/files/Procese%20verbale/Ref%202/Rezultate_Referendum_%C3%8Entrebarea_1.xlsx",
    "ref2019-2": "http://a.cec.md/storage/ckfinder/files/Procese%20verbale/Procese%20verbale%20uninominal/Rezultate_Referendum_%C3%8Entrebarea_2.xlsx",
    "ps2025": f"{BASE}/68e657b72b3ec/115495/attached_files",
    "pres2020-1": "https://a.cec.md/storage/ckfinder/files/Alegeri%20Preziden%C8%9Biale_1%20noiembrie%202020/"
    "Alegeri_Prezidentiale_2020_Tur_I.xlsx",
    "pres2020-2": "https://a.cec.md/storage/ckfinder/files/Alegeri_Prezidentiale_2020_Tur_II.xlsx",
    "ps2019-totals": f"{BASE}/5caef9fc3c7ab/92794/attached_files",
}
FILES = "http://a.cec.md/storage/ckfinder/files"
PS2019_LIST = {
    1: f"{FILES}/1_Briceni.xlsx",
    2: f"{FILES}/2_Ocnita(1).xlsx",
    3: f"{FILES}/3_Edinet(1).xlsx",
    4: f"{FILES}/4_Riscani(1).xlsx",
    5: f"{FILES}/5_Glodeni(1).xlsx",
    6: f"{FILES}/6_Drochia(1).xlsx",
    7: f"{FILES}/7_Soroca(1).xlsx",
    8: f"{FILES}/Totalizator_proportional/8_Floresti.xlsx",
    9: f"{FILES}/Totalizator_proportional/9_Balti.xlsx",
    10: f"{FILES}/Totalizator_proportional/10_Balti.xlsx",
    11: f"{FILES}/Totalizator_proportional/11_Falesti.xlsx",
    12: f"{FILES}/Totalizator_proportional/12_Singerei.xlsx",
    13: f"{FILES}/Totalizator_proportional/13_Rezina.xlsx",
    14: f"{FILES}/Totalizator_proportional/14_Telenesti.xlsx",
    15: f"{FILES}/Totalizator_proportional/15_Calarasi.xlsx",
    16: f"{FILES}/Totalizator_proportional/16_Ungheni.xlsx",
    17: f"{FILES}/Totalizator_proportional/17_Nisporeni.xlsx",
    18: f"{FILES}/Totalizator_proportional/18_Orhei.xlsx",
    19: f"{FILES}/Totalizator_proportional/19_Ivancea.xlsx",
    20: f"{FILES}/Totalizator_proportional/20_Straseni.xlsx",
    21: f"{FILES}/Totalizator_proportional/21_Criuleni.xlsx",
    22: f"{FILES}/Totalizator_proportional/22_Ialoveni.xlsx",
    23: f"{FILES}/Totalizator_proportional/23_Chisinau.xlsx",
    24: f"{FILES}/Totalizator_proportional/24_Chisinau.xlsx",
    25: f"{FILES}/Totalizator_proportional/25_Chisinau.xlsx",
    26: f"{FILES}/Totalizator_proportional/26_Chisinau.xlsx",
    27: f"{FILES}/Totalizator_proportional/27_Chisinau.xlsx",
    28: f"{FILES}/Totalizator_proportional/28_Chisinau.xlsx",
    29: f"{FILES}/Totalizator_proportional/29_Chisinau.xlsx",
    30: f"{FILES}/Totalizator_proportional/30_Chisinau.xlsx",
    31: f"{FILES}/Totalizator_proportional/31_Chisinau.xlsx",
    32: f"{FILES}/Totalizator_proportional/32_Chisinau.xlsx",
    33: f"{FILES}/Totalizator_proportional/33_Chisinau.xlsx",
    34: f"{FILES}/Totalizator_proportional/34_Anenii%20Noi.xlsx",
    35: f"{FILES}/Totalizator_proportional/35_Causeni.xlsx",
    36: f"{FILES}/Totalizator_proportional/36_Stefan%20Voda.xlsx",
    37: f"{FILES}/Totalizator_proportional/37_Razeni.xlsx",
    38: f"{FILES}/Totalizator_proportional/38_Hincesti.xlsx",
    39: f"{FILES}/Totalizator_proportional/39_Sarata%20Galbena.xlsx",
    40: f"{FILES}/Totalizator_proportional/40_Cimislia.xlsx",
    41: f"{FILES}/Totalizator_proportional/41_Leova.xlsx",
    42: f"{FILES}/Totalizator_proportional/42_Cantemir.xlsx",
    43: f"{FILES}/Totalizator_proportional/43_Cahul.xlsx",
    44: f"{FILES}/Totalizator_proportional/44_Taraclia.xlsx",
    45: f"{FILES}/Totalizator_proportional/45_Comrat.xlsx",
    46: f"{FILES}/Totalizator_proportional/46_Ceadir-Lunga.xlsx",
    47: f"{FILES}/Totalizator_proportional/47_Camenca%2C%20R%C3%AEbni%C8%9Ba%2C%20Dub%C4%83sari%20%C8%99i%20Grigoriopol.xlsx",
    48: f"{FILES}/Totalizator_proportional/48_Slobozia%2C%20mun_%20Tiraspol%20%C8%99i%20Bender.xlsx",
    49: f"{FILES}/Totalizator_proportional/49_est%20de%20Republica%20Moldova.xlsx",
    50: f"{FILES}/Totalizator_proportional/50_vest%20de%20Republica%20Moldova.xlsx",
    51: f"{FILES}/Totalizator_proportional/51_SUA%2C%20Canada.xlsx",
}
PS2019_DISTRICT = {
    1: f"{FILES}/Totalizator_uninominal/1_Briceni.xlsx",
    2: f"{FILES}/Totalizator_uninominal/2_Ocnita.xlsx",
    3: f"{FILES}/Totalizator_uninominal/3_Edinet.xlsx",
    4: f"{FILES}/Totalizator_uninominal/4_Riscani.xlsx",
    5: f"{FILES}/Totalizator_uninominal/5_Glodeni.xlsx",
    6: f"{FILES}/Totalizator_uninominal/6_Drochia.xlsx",
    7: f"{FILES}/Totalizator_uninominal/7_Soroca.xlsx",
    8: f"{FILES}/Totalizator_uninominal/8_Floresti.xlsx",
    9: f"{FILES}/Totalizator_uninominal/9_Balti.xlsx",
    10: f"{FILES}/Totalizator_uninominal/10_Balti.xlsx",
    11: f"{FILES}/Totalizator_uninominal/11_Falesti.xlsx",
    12: f"{FILES}/Totalizator_uninominal/12_Singerei.xlsx",
    13: f"{FILES}/Totalizator_uninominal/13_Rezina.xlsx",
    14: f"{FILES}/Totalizator_uninominal/14_Telenesti.xlsx",
    15: f"{FILES}/Totalizator_uninominal/15_Calarasi.xlsx",
    16: f"{FILES}/Totalizator_uninominal/16_Ungheni.xlsx",
    17: f"{FILES}/Totalizator_uninominal/17_Nisporeni.xlsx",
    18: f"{FILES}/Totalizator_uninominal/18_Orhei.xlsx",
    19: f"{FILES}/Totalizator_uninominal/19_Ivancea.xlsx",
    20: f"{FILES}/Totalizator_uninominal/20_Straseni.xlsx",
    21: f"{FILES}/Totalizator_uninominal/21_Criuleni.xlsx",
    22: f"{FILES}/Totalizator_uninominal/22_Ialoveni.xlsx",
    23: f"{FILES}/23_Chisinau.xlsx",
    24: f"{FILES}/24_Chisinau.xlsx",
    25: f"{FILES}/25_Chisinau.xlsx",
    26: f"{FILES}/26_Chisinau.xlsx",
    27: f"{FILES}/27_Chisinau.xlsx",
    28: f"{FILES}/Totalizator_uninominal/28_Chisinau.xlsx",
    29: f"{FILES}/29_Chisinau.xlsx",
    30: f"{FILES}/30_Chisinau.xlsx",
    31: f"{FILES}/31_Chisinau.xlsx",
    32: f"{FILES}/32_Chisinau.xlsx",
    33: f"{FILES}/33_Chisinau.xlsx",
    34: f"{FILES}/Totalizator_uninominal/34_Anenii%20Noi.xlsx",
    35: f"{FILES}/Totalizator_uninominal/35_Causeni.xlsx",
    36: f"{FILES}/Totalizator_uninominal/36_Stefan%20Voda.xlsx",
    37: f"{FILES}/Totalizator_uninominal/37_Razeni.xlsx",
    38: f"{FILES}/Totalizator_uninominal/38_Hincesti.xlsx",
    39: f"{FILES}/Totalizator_uninominal/39_Sarata%20Galbena.xlsx",
    40: f"{FILES}/Totalizator_uninominal/40_Cimislia.xlsx",
    41: f"{FILES}/Totalizator_uninominal/41_Leova.xlsx",
    42: f"{FILES}/Totalizator_uninominal/42_Cantemir.xlsx",
    43: f"{FILES}/Totalizator_uninominal/43_Cahul.xlsx",
    44: f"{FILES}/Totalizator_uninominal/44_Taraclia.xlsx",
    45: f"{FILES}/Totalizator_uninominal/45_Comrat.xlsx",
    46: f"{FILES}/Totalizator_uninominal/46_Ceadir-Lunga.xlsx",
    47: f"{FILES}/Totalizator_uninominal/47_Camenca%2C%20R%C3%AEbni%C8%9Ba%2C%20Dub%C4%83sari%20%C8%99i%20Grigoriopol.xlsx",
    48: f"{FILES}/Totalizator_uninominal/48_Slobozia%2C%20Tiraspol%20%C8%99i%20Bender.xlsx",
    49: f"{FILES}/Totalizator_uninominal/49_la%20est%20de%20Republica%20Moldova.xlsx",
    50: f"{FILES}/Totalizator_uninominal/50_la%20vest%20de%20Republica%20Moldova.xlsx",
    51: f"{FILES}/Totalizator_uninominal/51_SUA%2C%20Canada.xlsx",
}
SUFFIX = {"ps2014": ".xls", "ps2014-districts": ".xls", "ps2014-totals": ".xls", "ps2019-totals": ".pdf"}
DAYS = {
    "2010-11-28": date(2010, 11, 28),
    "2014-11-30": date(2014, 11, 30),
    "2016-10-30": date(2016, 10, 30),
    "2016-11-13": date(2016, 11, 13),
    "2019-02-24": date(2019, 2, 24),
    "2020-11-01": date(2020, 11, 1),
    "2020-11-15": date(2020, 11, 15),
    "2021-07-11": date(2021, 7, 11),
    "2024-10-20": date(2024, 10, 20),
    "2024-11-03": date(2024, 11, 3),
    "2025-09-28": date(2025, 9, 28),
}


EARLIER = {
    "2016-11-13": ["md.cec/2016-10-30"],
    "2020-11-15": ["md.cec/2020-11-01"],
    "2024-11-03": ["md.cec/2024-10-20"],
}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CEC_SOURCE = key("source", "cec")
PARLIAMENT = key("body", "parliament")
PRESIDENCY = key("office", "president")
SYSTEM_LIST = key("electoral_system", "parliament", "list")
SYSTEM_LIST_2014 = key("electoral_system", "parliament", "list", "2014")
SYSTEM_LIST_MIXED = key("electoral_system", "parliament", "list", "mixed")
SYSTEM_DISTRICT = key("electoral_system", "parliament", "single_member")
SYSTEM_PRESIDENT = key("electoral_system", "president", "two_round")
SYSTEM_REFERENDUM = key("electoral_system", "referendum")

METHOD = (
    "Counted per polling station and published by the Central Electoral Commission in its results workbook. "
    "Registered voters are the voters on the basic roll plus those on the supplementary roll. Voters are the voters "
    "who received a ballot, ballots cast are the voters who took part, and invalid ballots are those declared "
    "invalid. Polling stations abroad and on the left bank of the Dniester are listed in circumscriptions of their "
    "own."
)


class Station:
    def __init__(self, circumscription: int, code: str, place: str, name: str, row: list[int | None]) -> None:
        self.circumscription = circumscription
        self.code = code
        self.place = place
        self.name = name
        self.row = row


class Table:
    """A workbook's polling stations, its printed national row, and its option labels, in the sheet's own order."""

    def __init__(
        self,
        stations: list[Station],
        total: list[int | None],
        labels: list[str],
        tags: list[str],
        accounting: tuple[int, ...],
        first: int,
        end: int,
        named: bool = False,
    ) -> None:
        self.named = named  # whether the tags are party names rather than abbreviations
        self.stations = stations
        self.total = total
        self.labels = labels
        self.tags = tags
        self.accounting = accounting  # indexes of registered base, supplementary, voters, cast, invalid, valid
        self.first = first  # index of the first option
        self.end = end  # index after the last option


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def number(value: object) -> int | None:
    text = clean(value).replace(" ", "")
    if text in ("", "None", "-"):
        return None
    return int(float(text))


def sheet(url: str, name: str | None = None) -> list[list[str | None]]:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = book.load_sheet_by_name(name or book.sheet_names[0], header_row=None, dtypes="string").to_polars()
    return [[None if cell is None else str(cell) for cell in row] for row in frame.rows()]


def _district(text: str) -> int | None:
    found = re.match(r"\s*(\d+)\s*-\s*Circumscrip", text) or re.search(r"nr\.\s*(\d+)\s*$", text)
    return int(found.group(1)) if found else None


def _row(row: list[str | None], first: int, end: int) -> list[int | None]:
    return [number(cell) for cell in row[first:end]]


def read_2021() -> Table:
    rows = sheet(URLS["ps2021"])
    width = len(rows[3])
    stations: list[Station] = []
    for row in rows[4:]:
        if row[2] is not None and re.fullmatch(r"\d+", clean(row[0])) and row[3] is not None:
            district = int(clean(row[0]))
            stations.append(
                Station(district, f"{district}/{int(clean(row[2]))}", clean(row[1]), clean(row[3]), _row(row, 4, width))
            )
    labels = [clean(cell) for cell in rows[3][10 : width - 3]]
    return Table(
        stations, _row(rows[4], 4, width), labels, [""] * len(labels), (0, 1, 2, 3, 5, width - 7), 6, width - 7
    )


def read_2024(url: str) -> Table:
    rows = sheet(url)
    width = len(rows[0])
    stations: list[Station] = []
    district = 0
    tags: list[str] = []
    for row in rows[1:]:
        text = clean(row[0])
        if "Circumscrip" in text and row[2] is None:
            district = _district(text) or 0
        elif row[0] is None and row[2] is None and not tags and any(row[9:]):
            tags = [clean(cell) for cell in row[9 : width - 3]]
        elif row[1] is not None and row[2] is not None and re.fullmatch(r"\d+", clean(row[1])):
            stations.append(
                Station(district, f"{district}/{int(clean(row[1]))}", text, clean(row[2]), _row(row, 3, width))
            )
    labels = [clean(cell) for cell in rows[0][9 : width - 3]]
    count = width - 3 - 9
    return Table(stations, _row(rows[1], 3, width), labels, tags, (0, 1, 2, 3, 5, 6 + count), 6, 6 + count)


def read_2025() -> Table:
    rows = sheet(URLS["ps2025"])
    width = max(i for i, cell in enumerate(rows[1]) if cell is not None) + 1
    stations: list[Station] = []
    district = 0
    for row in rows[3:]:
        text = clean(row[0])
        if row[2] is None and "Circumscrip" in text:
            district = _district(text) or 0
        elif re.fullmatch(r"\d+/\d+", text) and row[2] is not None:
            stations.append(Station(district, text, "", clean(row[1]), _row(row, 2, width)))
    labels = [clean(cell) for cell in rows[0][11:width]]
    return Table(stations, _row(rows[2], 2, width), labels, [""] * len(labels), (0, 1, 2, 3, 5, 6), 9, width - 2)


def read_2014() -> Table:
    rows = sheet(URLS["ps2014"], "Лист1")
    stations: list[Station] = []
    district = 0
    for row in rows[2:]:
        text = clean(row[0])
        if text.upper().startswith("CIRCUMSCRIP"):
            district = _district(text) or 0
        elif (
            row[1] is not None
            and clean(row[1]) != "Total"
            and re.fullmatch(r"\d+", clean(row[2]) if row[2] else "")
            and (re.fullmatch(r"\d+", text) or not text)
        ):
            numbered = int(text) if text else f"u{len(stations)}"
            stations.append(Station(district, f"{district}/{numbered}", clean(row[1]), "", _row(row, 2, 36)))
    by_district = sheet(URLS["ps2014-totals"], "Лист1")
    total = _row(next(row for row in by_district if clean(row[1]) == "TOTAL"), 2, 36)
    labels = [clean(cell) for cell in rows[1][8:33]]
    return Table(stations, total, labels, [""] * len(labels), (0, 1, 2, 3, 5, 6 + 25), 6, 6 + 25)


def read_2016(url: str) -> Table:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    frame = book.load_sheet_by_name(book.sheet_names[0], header_row=None, dtypes="string").to_polars()
    main = [[None if cell is None else str(cell) for cell in row] for row in frame.rows()]
    width = len(main[2])
    districts = [int(clean(row[0])) for row in main[4:] if re.fullmatch(r"\d+", clean(row[0]))]
    stations: list[Station] = []
    for district, name in zip(districts, book.sheet_names[1:], strict=True):
        rows = sheet(url, name)
        for row in rows[4:]:
            if clean(row[1]).upper().startswith("TOTAL"):
                continue
            if re.fullmatch(r"\d+", clean(row[0])) and row[2] is not None:
                stations.append(
                    Station(district, f"{district}/{int(clean(row[0]))}", clean(row[1]), "", _row(row, 2, width))
                )
    names, tags = [], []
    for cell in main[2][8 : width - 3]:
        name, _, party = clean(cell).partition(", ")
        names.append(name)
        tags.append("" if party.lower().startswith("candidat independent") else party)
    total = _row(next(row for row in main if clean(row[1]) == "TOTAL"), 2, width)
    count = width - 3 - 8
    return Table(stations, total, names, tags, (0, 1, 2, 3, 5, 6 + count), 6, 6 + count, named=True)


def read_2019(url: str) -> Table:
    book = fastexcel.read_excel(str(raw.cached(SOURCE, url).path))
    names = {
        int(clean(row[0])): clean(row[1]) for row in sheet(url, "TOTAL")[4:] if re.fullmatch(r"\d+", clean(row[0]))
    }
    stations: list[Station] = []
    for name in book.sheet_names[1:]:
        for row in sheet(url, name)[4:]:
            if re.fullmatch(r"\d+", clean(row[0])):
                district = int(name)
                # Constituency 51 numbers its referendum stations apart from its parliamentary ones.
                number_ = f"{'r' if district == 51 else ''}{int(clean(row[0]))}"
                stations.append(Station(district, f"{district}/{number_}", names[district], "", _row(row, 1, 12)))
    rows = sheet(url, "TOTAL")
    total = _row(next(row for row in rows if clean(row[0]) == "Total"), 2, 13)
    labels = [clean(cell) for cell in rows[2][13:] if cell] or [clean(cell) for cell in sheet(url, "1")[2][10:12]]
    return Table(stations, total, labels, [""] * len(labels), (0, 1, 2, 3, 5, 6), 9, 11)


def read_2020(url: str) -> Table:
    """A presidential round of 2020: one sheet with a national row, circumscription rows, and polling stations, and a
    header that prints each candidate's name over the party that nominated them."""
    rows = sheet(url)
    width = len(rows[0])
    stations: list[Station] = []
    district = 0
    for row in rows[1:]:
        text = clean(row[0])
        if "Circumscrip" in text and row[2] is None:
            district = _district(text) or 0
        elif row[2] is not None and re.fullmatch(r"\d+", clean(row[1])):
            stations.append(
                Station(district, f"{district}/{int(clean(row[1]))}", text, clean(row[2]), _row(row, 3, width))
            )
    names, tags = [], []
    for cell in rows[0][9 : width - 3]:
        name, party = [clean(part) for part in str(cell).strip().split("\n") if clean(part)]
        names.append(name)
        tags.append("" if party.lower().startswith("candidat independent") else party)
    total = _row(next(row for row in rows if clean(row[0]).startswith("Alegeri")), 3, width)
    count = width - 3 - 9
    return Table(stations, total, names, tags, (0, 1, 2, 3, 5, 6 + count), 6, 6 + count, named=True)


def _numbered(text: str) -> bool:
    return re.fullmatch(r"\d+", clean(text)) is not None


def _station_2019(district: int, row: list[str | None], values: list[int | None]) -> Station:
    place, _, name = str(row[1] or "").strip().partition("\n")
    return Station(district, f"{district}/{int(clean(row[0]))}", clean(place), clean(name), values)


def read_2019_list() -> Table:
    """The national list contest of 2019, from one workbook per single-member constituency. Each prints its own
    total row, and the national row is their sum."""
    stations: list[Station] = []
    total: list[int | None] = []
    labels: list[str] = []
    for district, url in PS2019_LIST.items():
        rows = sheet(url)
        header = next(i for i, row in enumerate(rows) if clean(row[0]).startswith("Nr. Sec"))
        width = len(rows[header]) - 2
        found = [clean(cell).replace("~", " ") for cell in rows[header][9:width]]
        if labels and found != labels:
            raise ValueError(f"constituency {district} lists other parties: {found}")
        labels = found
        printed = _row(rows[header + 1], 2, width)
        total = printed if not total else [(a or 0) + (b or 0) for a, b in zip(total, printed, strict=True)]
        for row in rows[header + 2 :]:
            if _numbered(str(row[0] or "")):
                stations.append(_station_2019(district, row, _row(row, 2, width)))
    return Table(stations, total, labels, [""] * len(labels), (0, 1, 2, 3, 5, 6), 7, 7 + len(labels))


def read_2019_district(district: int) -> Table:
    """One single-member constituency of 2019. The header prints each candidate as the nominating party (or
    "Candidat independent") over the candidate's name; two empty columns sit between the candidates."""
    rows = sheet(PS2019_DISTRICT[district])
    header = next(i for i, row in enumerate(rows) if clean(row[0]).startswith("Nr. Sec"))
    head = rows[header]
    valid = next(i for i, cell in enumerate(head) if cell and "total de voturi valabil" in cell)
    columns = [i for i in range(8, valid) if head[i]]
    labels, tags = [], []
    for i in columns:
        party, name = [clean(part) for part in str(head[i]).strip().split("\n") if clean(part)]
        labels.append(name)
        tags.append("" if party.lower().startswith("candidat independent") else party.replace("~", " "))
    keep = [*range(2, 8), *columns, valid]

    def pick(row: list[str | None]) -> list[int | None]:
        return [number(row[i]) for i in keep]

    stations = [_station_2019(district, row, pick(row)) for row in rows[header + 2 :] if _numbered(str(row[0] or ""))]
    count = len(columns)
    return Table(stations, pick(rows[header + 1]), labels, tags, (0, 1, 2, 3, 5, 6 + count), 6, 6 + count, True)


def table(day: str, which: str = "") -> Table:
    if which.startswith("pres2020"):
        return read_2020(URLS[which])
    if which.startswith("ref2019"):
        return read_2019(URLS[which])
    if which.startswith("pres2016"):
        return read_2016(URLS[which])
    if day == "2014-11-30":
        return read_2014()
    if day == "2021-07-11":
        return read_2021()
    if day == "2025-09-28":
        return read_2025()
    return read_2024(URLS[which])


def packages() -> list[str]:
    from ea.sources.md import admin, local, newlocal, referendums

    return [
        "md.cec/registry",
        *(f"md.cec/{day}" for day in DAYS),
        *local.packages(),
        *admin.packages(),
        *newlocal.packages(),
        *referendums.packages(),
    ]


def fetch() -> None:
    for name, url in URLS.items():
        raw.fetch(SOURCE, url, suffix=SUFFIX.get(name, ".xlsx"))
    for url in [*PS2019_LIST.values(), *PS2019_DISTRICT.values()]:
        raw.fetch(SOURCE, url, suffix=".xlsx")
    decisions.fetch()
    from ea.sources.md import early2010

    early2010.fetch()
    from ea.sources.md import admin, local, newlocal, referendums

    local.fetch()
    admin.fetch()
    newlocal.fetch()
    referendums.fetch()


def parse() -> None:
    registry().write()
    for day in DAYS:
        build(day).write()
    from ea.sources.md import admin, local, newlocal, referendums

    local.parse()
    admin.parse()
    newlocal.parse()
    referendums.parse()


def emit() -> None:
    package.run("load", packages())


def party_key(label: str) -> str:
    # Ids already loaded derive from this exact normalization, so it must not change.
    text = re.sub(r"[„”“\"']", "", label).strip()
    text = re.sub(r"^(partidul politic|partid politic|partidul)\s+", "", text, flags=re.IGNORECASE)
    return clean(text).casefold()


def party_id(label: str) -> str:
    return key("party", party_key(label))


def tag_party(tag: str) -> str:
    return key("party", "tag", tag)


def list_labels() -> dict[str, str]:
    """Every list's printed name across the parliamentary elections, by party id."""
    found: dict[str, str] = {}
    labels = [label for day in ("2014-11-30", "2021-07-11", "2025-09-28") for label in table(day).labels]
    labels += read_2019_list().labels
    labels += [tag for district in PS2019_DISTRICT for tag in read_2019_district(district).tags if tag]
    for label in labels:
        if "Candidat independent" not in label:
            found.setdefault(party_id(label), re.sub(r"\s+", " ", label.strip()))
    return found


def candidate_tags() -> set[str]:
    tags: set[str] = set()
    for which in ("pres2024-1", "pres2024-2"):
        tags |= {tag for tag in table("2024-10-20", which).tags if tag and not tag.startswith("ci-")}
    return tags


def candidate_parties() -> dict[str, str]:
    found: dict[str, str] = {}
    for which in ("pres2016-1", "pres2016-2", "pres2020-1", "pres2020-2"):
        for tag in table("", which).tags:
            if tag:
                found.setdefault(party_id(tag), tag)
    return found


def registry() -> Package:
    p = Package(
        "md.cec/registry",
        SOURCE,
        "Moldovan parties, the Parliament, the presidency, and their electoral systems, from the Central Electoral "
        "Commission's results workbooks",
    )
    p.external.add(MOLDOVA)
    p.add(
        "source",
        id=CEC_SOURCE,
        kind="official_body",
        publisher="Comisia Electorală Centrală a Republicii Moldova",
        title="Election results workbooks published by the Central Electoral Commission",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Comisia Electorală Centrală a Republicii Moldova",
    )
    for url in [*URLS.values(), *PS2019_LIST.values(), *PS2019_DISTRICT.values(), *decisions.URLS]:
        p.artifact(raw.cached(SOURCE, url), CEC_SOURCE)
    p.add(
        "scheme",
        code="md.circumscription",
        polity_id=MOLDOVA,
        kind="electoral_district",
        rank=1,
        noun="circumscription",
    )
    p.add("scheme", code="md.sv", polity_id=MOLDOVA, kind="precinct", rank=2, noun="polling station")
    p.add("body", id=PARLIAMENT, polity_id=MOLDOVA, kind="unicameral")
    p.name(PARLIAMENT, "Parliament of Moldova", "en", "official")
    p.name(PARLIAMENT, "Parlamentul Republicii Moldova", "ro", "native", False)
    p.add("office", id=PRESIDENCY, polity_id=MOLDOVA, kind="head_of_state")
    p.name(PRESIDENCY, "President of Moldova", "en", "official")
    p.name(PRESIDENCY, "Președintele Republicii Moldova", "ro", "native", False)
    systems = (
        (
            SYSTEM_LIST,
            {"formula": "dhondt"},
            (
                "A single national constituency elects 101 deputies by party-list proportional representation. Each "
                "list that passes the minimum threshold of representation receives deputies by the successive "
                "division of its votes by 1 up to 101 (D'Hondt), as the Commission's seat decisions describe. Its "
                "exclusion decisions cite the threshold (art. 94(2) of Electoral Code No. 1381/1997 in 2021, art. "
                "122(2) of Code No. 325/2022 in 2025) without printing the percentages, so none is stored here."
            ),
        ),
        (
            SYSTEM_LIST_2014,
            {
                "formula": "dhondt",
                "threshold": [
                    {"level": "party", "scope": "national", "percent": 6},
                    {"level": "coalition", "scope": "national", "percent": 9},
                ],
            },
            (
                "A single national constituency elects 101 deputies by party-list proportional representation. The "
                "Commission's decision of 5 December 2014 excludes lists below the thresholds of valid votes "
                "nationwide: 6% for a party or other social-political organization, 9% for an electoral bloc of two "
                "parties, and 2% for an independent candidate. The independent threshold and the thresholds for "
                "larger blocs are not stored in the rules. Seats are then allocated by an electoral quotient (valid "
                "votes divided by 101), each list receiving its votes divided by the quotient rounded to the nearest "
                "whole number; the seats left over go one at a time to each list, starting with the one that holds "
                "the most. The stored formula is the nearest the engine offers and does not reproduce that "
                "procedure."
            ),
        ),
        (
            SYSTEM_PRESIDENT,
            {"formula": "two_round", "majority": 0.5, "advance": 2},
            (
                "A candidate with a majority of valid votes in the first round is elected; otherwise the two leading "
                "candidates go to a second round."
            ),
        ),
        (
            SYSTEM_LIST_MIXED,
            {"formula": "dhondt", "threshold": {"level": "party", "scope": "national", "percent": 6}},
            (
                "Under the mixed system used in 2019, a single national constituency elects 50 of the 101 deputies by "
                "party-list proportional representation. The Commission's exclusion decision of 3 March 2019 removes "
                "the parties and other social-political organizations with under 6% of the valid votes in the "
                "national constituency; the thresholds for electoral blocs are not printed there and are not stored. "
                "Seats are allocated by an electoral quotient (valid votes divided by 50) with rounding and a "
                "distribution of leftover seats, which the stored formula does not reproduce."
            ),
        ),
        (
            SYSTEM_DISTRICT,
            {"formula": "fptp"},
            (
                "Under the mixed system used in 2019, each of 51 single-member constituencies elects one deputy, the "
                "candidate with the most valid votes."
            ),
        ),
        (SYSTEM_REFERENDUM, {"formula": "referendum"}, "A yes or no question put to all voters."),
    )
    for id, rules, text in systems:
        p.add("electoral_system", id=id, polity_id=MOLDOVA, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    for id, text in sorted({**candidate_parties(), **list_labels()}.items(), key=lambda item: item[1]):
        p.add("party", id=id, kind="bloc" if text.lower().startswith("blocul") else "party", polity_id=MOLDOVA)
        p.name(id, text, "ro", "official")
    for tag in sorted(candidate_tags()):
        id = tag_party(tag)
        p.add("party", id=id, kind="party", polity_id=MOLDOVA)
        p.name(id, tag, "ro", "abbreviation")
    return p


def _units(p: Package, day: str, tables: list[Table]) -> dict[str, str]:
    units: dict[str, str] = {}
    held = {str(row["code"]) for row in p.rows.get("reporting_unit", [])}
    for stations in (t.stations for t in tables):
        for station in stations:
            code = f"{day}:{station.code}"
            unit = units[station.code] = key("unit", code)
            if code in held:
                continue
            held.add(code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=MOLDOVA,
                scheme_code="md.sv",
                code=code,
                area_relation="unlocated",
                area_id=None,
                kind="standard",
            )
            text = ", ".join(part for part in (station.place, station.name) if part)
            if text:
                p.name(
                    unit,
                    f"{text} (circumscription {station.circumscription}, station {station.code.split('/')[1]})",
                    "ro",
                    "official",
                )
    return units


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def _declared(p: Package, result_set: str, options: list[str], t: Table) -> None:
    a, b, c, d, f, h = (t.total[i] for i in t.accounting)
    registered = None if a is None or b is None else a + b
    for measure, value in ((REGISTERED, registered), (VOTERS, c), (CAST, d), (INVALID, f), (VALID, h)):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for option, value in zip(options, t.total[t.first : t.end], strict=True):
        if value is not None:
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=value)


def _accounting(p: Package, result_set: str, units: dict[str, str], t: Table) -> None:
    for station in t.stations:
        a, b, c, d, f, h = (station.row[i] for i in t.accounting)
        registered = None if a is None or b is None else a + b
        for measure, value in ((REGISTERED, registered), (VOTERS, c), (CAST, d), (INVALID, f), (VALID, h)):
            _put(p, result_set, units[station.code], None, measure, value)


def _result_set(p: Package, election: str, contest: str, name: str) -> str:
    result_set = key("result_set", name)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=CEC_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    return result_set


def _election(p: Package, name: str, day: date, kind: str, english: str, romanian: str, path: str) -> str:
    election = key("election", name)
    p.add("election", id=election, polity_id=MOLDOVA, kind=kind, date_from=day, date_to=day, status="held")
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.name(election, english, "en", "official")
    p.name(election, romanian, "ro", "native", False)
    return election


def _contest(
    p: Package, name: str, election: str, kind: str, english: str, romanian: str, path: str, **fields: object
) -> str:
    contest = key("contest", name)
    row = {"round_number": 1, "default_result_kind": "counted", **fields}
    p.add("contest", id=contest, election_id=election, kind=kind, **row)
    p.name(contest, english, "en", "official")
    p.name(contest, romanian, "ro", "native", False)
    p.add("slug", path=path, entity_id=contest, is_current=True)
    return contest


def build(day: str) -> Package:
    first = DAYS[day]
    p = Package(
        f"md.cec/{day}",
        SOURCE,
        f"The Moldovan elections of {first} by polling station, from the Central Electoral Commission's workbooks",
        depends=["md.cec/registry", *EARLIER.get(day, [])],
    )
    p.external.add(MOLDOVA)
    if day == "2010-11-28":
        from ea.sources.md import early2010

        early2010.build(p, day, first)
    elif day in ("2014-11-30", "2021-07-11", "2025-09-28"):
        _parliament(p, day, first)
    elif day == "2019-02-24":
        _parliament_2019(p, day, first)
        _referendum(p, day, first)
    elif day == "2024-10-20":
        _presidential(p, day, first, 1)
        _referendum(p, day, first)
    else:
        _presidential(p, day, first, 2 if day in EARLIER else 1)
    return p


def _uses(p: Package, *names: str) -> None:
    for name in names:
        p.uses(raw.cached(SOURCE, URLS[name]))


def _parliament(p: Package, day: str, first: date) -> None:
    year = first.year
    kind = "general"
    name = {"2014-11-30": "ps2014", "2021-07-11": "ps2021", "2025-09-28": "ps2025"}[day]
    _uses(p, name, *(("ps2014-totals",) if year == 2014 else ()))
    snap = "early " if year == 2021 else ""
    election = _election(
        p,
        f"ps{day}",
        first,
        kind,
        f"{first.year} Moldovan {snap}parliamentary election",
        f"Alegeri parlamentare {'anticipate ' if year == 2021 else ''}din {first.day}.{first.month:02d}.{first.year}",
        f"md/{day}-general",
    )
    contest = _contest(
        p,
        f"ps{day}",
        election,
        "list_allocation",
        f"{first.year} Moldovan {snap}parliamentary election",
        f"Alegeri parlamentare {first.year}",
        f"md/{day}-general/parliament",
        body_id=PARLIAMENT,
        magnitude=101,
        electoral_system_id=SYSTEM_LIST_2014 if year == 2014 else SYSTEM_LIST,
    )
    t = table(day)
    result_set = _result_set(p, election, contest, f"ps{day}")
    options: list[str] = []
    for position, (label, tag) in enumerate(zip(t.labels, t.tags, strict=True), 1):
        option = key("option", f"ps{day}", position)
        options.append(option)
        independent = "Candidat independent" in label or label.lower().startswith("candidat independent")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate" if independent else "list",
            party_id=None if independent else party_id(label),
            ballot_position=position,
            ballot_label=label,
        )
    units = _units(p, day, [t])
    _accounting(p, result_set, units, t)
    for station in t.stations:
        for option, value in zip(options, station.row[t.first : t.end], strict=True):
            _put(p, result_set, units[station.code], option, VOTES, value)
    _declared(p, result_set, options, t)
    _caveats(p, result_set, t)
    _list_outcomes(p, day, result_set, t.labels, options, 101)
    if day == "2025-09-28":
        p.accept(
            "accounting.declared",
            "the printed national row differs from the sum of its own polling-station rows by one in invalid "
            "ballots (30,857 against 30,856), valid votes (1,578,722 against 1,578,723), and one list's votes (0 "
            "against 1); stored as printed per station",
        )


def _parliament_2019(p: Package, day: str, first: date) -> None:
    """The mixed-system election of 2019: 50 deputies from a national list contest and 51 from single-member
    constituencies, every ballot counted at the same polling stations."""
    p.uses(raw.cached(SOURCE, URLS["ps2019-totals"]))
    for url in [*PS2019_LIST.values(), *PS2019_DISTRICT.values()]:
        p.uses(raw.cached(SOURCE, url))
    election = _election(
        p,
        f"ps{day}",
        first,
        "general",
        "2019 Moldovan parliamentary election",
        "Alegeri parlamentare din 24.02.2019",
        f"md/{day}-general",
    )
    contest = _contest(
        p,
        f"ps{day}",
        election,
        "list_allocation",
        "2019 Moldovan parliamentary election, national constituency",
        "Alegeri parlamentare 2019, circumscripția națională",
        f"md/{day}-general/parliament",
        body_id=PARLIAMENT,
        magnitude=50,
        electoral_system_id=SYSTEM_LIST_MIXED,
    )
    t = read_2019_list()
    result_set = _result_set(p, election, contest, f"ps{day}")
    options: list[str] = []
    for position, label in enumerate(t.labels, 1):
        option = key("option", f"ps{day}", position)
        options.append(option)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party_id(label),
            ballot_position=position,
            ballot_label=label,
        )
    units = _units(p, day, [t])
    _votes(p, result_set, units, options, t)
    _list_outcomes(p, day, result_set, t.labels, options, 50)
    p.add(
        "note",
        id=key("note", result_set, "national"),
        entity_id=result_set,
        kind="methodology",
        body_md="The Commission published this contest in one workbook per single-member constituency, each with "
        "its polling stations and a printed constituency total. The national totals stored as declared are the sum "
        "of those 51 totals, which equals the Commission's protocol on the national totalization of 3 March 2019.",
        lang="en",
    )
    winning = decisions.winners(day)
    for district in PS2019_DISTRICT:
        name = f"ps{day}-{district}"
        seat = _contest(
            p,
            name,
            election,
            "body_seats",
            f"2019 Moldovan parliamentary election, single-member constituency {district}",
            f"Alegeri parlamentare 2019, circumscripția uninominală nr. {district}",
            f"md/{day}-general/constituency-{district}",
            body_id=PARLIAMENT,
            magnitude=1,
            electoral_system_id=SYSTEM_DISTRICT,
        )
        t = read_2019_district(district)
        result_set = _result_set(p, election, seat, name)
        options = _candidates(p, name, 2019, 1, seat, t, True, f"ps2019:{district}")
        _votes(p, result_set, _units(p, day, [t]), options, t)
        found = _by_name(t.labels, options)
        winner = _named(found, winning[district])
        for option in options:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if option == winner else "lost")
        _declared_in(p, result_set, day, decisions.DISTRICTS)


def _by_name(labels: list[str], options: list[str]) -> dict[tuple[str, ...], str]:
    """The options by the words of their ballot label, so a decision's wording finds its option."""
    found: dict[tuple[str, ...], str] = {}
    for label, option in zip(labels, options, strict=True):
        words = decisions.tokens(label)
        if words:
            if words in found:
                raise ValueError(f"two ballot labels read alike: {label}")
            found[words] = option
    return found


# Names the decisions print differently from the ballot.
ALIASES = {"Uniunea Centristă din Moldova": "Uniunea Centristă"}


def _named(found: dict[tuple[str, ...], str], name: str) -> str:
    option = found.get(decisions.tokens(next((v for k, v in ALIASES.items() if k in name), name)))
    if option is None:
        raise ValueError(f"the decision names {name}, which is on no ballot")
    return option


def _declared_in(p: Package, result_set: str, day: str, *kinds: str) -> None:
    """Cite the decisions that declare a result set's outcomes, and keep their pages among the package's sources."""
    cited = []
    for kind in kinds:
        number = decisions.DECISIONS[day][kind][0]
        url = decisions.PRINT.format(number)
        p.uses(raw.cached(SOURCE, url))
        cited.append(f"decision {decisions.reference(day, kind)} ({url})")
    p.add(
        "note",
        id=key("note", result_set, "outcomes"),
        entity_id=result_set,
        kind="methodology",
        body_md="The outcomes are as the Central Electoral Commission declared them in its "
        + " and its ".join(cited)
        + ".",
        lang="en",
    )


def _list_outcomes(
    p: Package, day: str, result_set: str, labels: list[str], options: list[str], magnitude: int
) -> None:
    """Seats won by the lists that passed the threshold, and the others excluded from the allocation."""
    found = _by_name(labels, options)
    seen: set[str] = set()
    total = 0
    for name, count in decisions.seats(day):
        option = _named(found, name)
        p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=count)
        seen.add(option)
        total += count
    if total != magnitude:
        raise ValueError(f"the decision gives {total} seats of {magnitude}")
    for name in decisions.excluded(day):
        option = _named(found, name)
        if option in seen:
            raise ValueError(f"{name} is both seated and excluded")
        p.add("outcome", result_set_id=result_set, option_id=option, kind="lost")
        seen.add(option)
    for label, option in zip(labels, options, strict=True):
        if option not in seen and label.strip().endswith("Retras"):
            p.add("outcome", result_set_id=result_set, option_id=option, kind="withdrew")
            seen.add(option)
    missing = [label for label, option in zip(labels, options, strict=True) if label and option not in seen]
    if missing:
        p.add(
            "note",
            id=key("note", result_set, "unlisted"),
            entity_id=result_set,
            kind="caveat",
            body_md="The decisions name no outcome for "
            + "; ".join(missing)
            + ", which the Commission's results workbook lists with no votes.",
            lang="en",
        )
    _declared_in(p, result_set, day, decisions.SEATS, decisions.EXCLUDED)


def _round_outcomes(p: Package, day: str, result_set: str, round_number: int, t: Table, options: list[str]) -> None:
    """The candidates a first-round decision advances, or the president-elect a second-round decision declares."""
    found = _by_name(t.labels, options)
    votes = dict(zip(options, t.total[t.first : t.end], strict=True))
    if round_number == 1:
        advancing: set[str] = set()
        for name, count in decisions.advanced(day):
            option = _named(found, name)
            if votes[option] != count:
                raise ValueError(f"the decision prints {count} votes for {name}, the workbook {votes[option]}")
            advancing.add(option)
        if len(advancing) != 2:
            raise ValueError("a first-round decision advances two candidates")
        for option in options:
            if option in advancing:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="advanced")
            elif votes[option]:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="eliminated")
        _declared_in(p, result_set, day, decisions.ADVANCED)
        return
    winner = _named(found, decisions.elected(day))
    for option in options:
        p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if option == winner else "lost")
    _declared_in(p, result_set, day, decisions.ELECTED)


def _votes(p: Package, result_set: str, units: dict[str, str], options: list[str], t: Table) -> None:
    _accounting(p, result_set, units, t)
    for station in t.stations:
        for option, value in zip(options, station.row[t.first : t.end], strict=True):
            _put(p, result_set, units[station.code], option, VOTES, value)
    _declared(p, result_set, options, t)
    _caveats(p, result_set, t)


def _caveats(p: Package, result_set: str, t: Table) -> None:
    over = 0
    for station in t.stations:
        a, b, _, d, _, _ = (station.row[i] for i in t.accounting)
        if a is not None and b is not None and d is not None and d > a + b:
            over += 1
    if over:
        earlier = p.accepted.get("accounting.turnout")
        counted = over + (int(str(earlier).split()[0].replace(",", "")) if earlier else 0)
        p.accept(
            "accounting.turnout",
            f"{counted:,} polling-station rows print more ballots cast than voters on the basic and supplementary "
            "rolls together; the Commission's own figures, stored as printed",
        )
        p.add(
            "note",
            id=key("note", result_set, "turnout"),
            entity_id=result_set,
            kind="caveat",
            body_md=f"At {over} polling stations, more ballots were cast than voters on the basic and supplementary "
            "rolls together. The figures are stored as printed.",
            lang="en",
        )


def _candidates(
    p: Package,
    name: str,
    year: int,
    round_number: int,
    contest: str,
    t: Table,
    build_people: bool,
    scope: str = "",
) -> list[str]:
    options: list[str] = []
    for position, (label, tag) in enumerate(zip(t.labels, t.tags, strict=True), 1):
        person = key("person", scope or f"pres{year}", label)
        if build_people:
            p.add("person", id=person)
            p.name(person, label, "ro", "official")
        option = key("option", f"{name}-{round_number}", position)
        options.append(option)
        independent = tag.startswith("ci-") or not tag
        party = None if independent else (party_id(tag) if t.named else tag_party(tag))
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=position,
            ballot_label=label,
        )
        p.add(
            "candidacy",
            id=key("candidacy", f"{name}-{round_number}", position),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=label,
            member_of_party_id=party,
            is_independent=independent,
        )
    return options


def _presidential(p: Package, day: str, first: date, round_number: int) -> None:
    year = first.year
    name = f"pres{year}"
    which = f"{name}-{round_number}"
    _uses(p, which)
    runoff = round_number == 2
    election = _election(
        p,
        which,
        first,
        "runoff" if runoff else "general",
        f"{year} Moldovan presidential election" + (", second round" if runoff else ""),
        f"Alegeri prezidențiale {year}" + (", turul II" if runoff else ""),
        f"md/{day}-presidential" + ("-runoff" if runoff else ""),
    )
    contest = _contest(
        p,
        which,
        election,
        "office",
        f"{year} Moldovan presidential election, {'second' if runoff else 'first'} round",
        f"Alegeri prezidențiale {year}, turul {'II' if runoff else 'I'}",
        f"md/{day}-presidential{'-runoff' if runoff else ''}/president",
        office_id=PRESIDENCY,
        magnitude=1,
        round_number=round_number,
        previous_round_id=key("contest", f"{name}-1") if runoff else None,
        electoral_system_id=SYSTEM_PRESIDENT,
    )
    t = table(day, which)
    result_set = _result_set(p, election, contest, which)
    options = _candidates(p, name, year, round_number, contest, t, not runoff)
    units = _units(p, day, [t])
    _accounting(p, result_set, units, t)
    for station in t.stations:
        for option, value in zip(options, station.row[t.first : t.end], strict=True):
            _put(p, result_set, units[station.code], option, VOTES, value)
    _declared(p, result_set, options, t)
    _caveats(p, result_set, t)
    _round_outcomes(p, day, result_set, round_number, t, options)
    if year == 2016:
        p.accept(
            "accounting.declared",
            "the Hîncești sheet of the workbook omits polling stations 29 and 30, while the circumscription and "
            "national totals it prints include them, so the stored station rows fall short of the printed totals",
        )
        p.add(
            "note",
            id=key("note", result_set, "omitted"),
            entity_id=result_set,
            kind="caveat",
            body_md="The published workbook lists no results for polling stations 29 and 30 of the Hîncești "
            "circumscription, although its circumscription and national totals count them. Those two stations are "
            "missing here.",
            lang="en",
        )


def _referendum(p: Package, day: str, first: date) -> None:
    year = first.year
    election = _election(
        p,
        f"ref{year}",
        first,
        "referendum",
        f"{year} Moldovan {'constitutional ' if year == 2024 else 'republican '}referendum",
        f"Referendum republican {'constituțional ' if year == 2024 else ''}{year}",
        f"md/{day}-referendum",
    )
    questions = ("ref2024",) if year == 2024 else ("ref2019-1", "ref2019-2")
    units: dict[str, str] = {}
    for index, which in enumerate(questions, 1):
        _uses(p, which)
        suffix = f", question {index}" if len(questions) > 1 else ""
        contest = _contest(
            p,
            which,
            election,
            "ballot_question",
            f"{year} Moldovan referendum{suffix}",
            f"Referendum republican {year}" + (f", întrebarea {index}" if len(questions) > 1 else ""),
            f"md/{day}-referendum/question" + (f"-{index}" if len(questions) > 1 else ""),
            electoral_system_id=SYSTEM_REFERENDUM,
        )
        p.add("ballot_question", contest_id=contest, type="referendum")
        t = table(day, which)
        result_set = _result_set(p, election, contest, which)
        options: list[str] = []
        for position, label in enumerate(t.labels, 1):
            option = key("option", which, position)
            options.append(option)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=position,
                ballot_label=label,
            )
        units = _units(p, day, [t])
        _accounting(p, result_set, units, t)
        for station in t.stations:
            for option, value in zip(options, station.row[t.first : t.end], strict=True):
                _put(p, result_set, units[station.code], option, VOTES, value)
        _declared(p, result_set, options, t)
        _caveats(p, result_set, t)
