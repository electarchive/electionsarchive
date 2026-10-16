import html
import itertools
import re
import struct
import subprocess
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.ysk import (
    INDEPENDENTS,
    LICENSE,
    PROVINCES,
    PUBLISHER,
    REGISTERED,
    VALID,
    VOTERS,
    VOTES,
    title,
    upper,
)
from ea.store import DATA, connect

SOURCE = "tr.ysk_local"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
DOCS = "https://www.ysk.gov.tr/doc/dosyalar/docs/"
SEATS = 16
DAYS = {
    1963: date(1963, 11, 17),
    1968: date(1968, 6, 2),
    1973: date(1973, 12, 9),
    1977: date(1977, 12, 11),
    1984: date(1984, 3, 25),
    1989: date(1989, 3, 26),
    1994: date(1994, 3, 27),
    1999: date(1999, 4, 18),
    2004: date(2004, 3, 28),
    2009: date(2009, 3, 29),
    2014: date(2014, 3, 30),
}
OLD = {
    (1963, "il_genel"): "Mahalli/1963/KesinSecimSonuclari/1963_İl_Genel_Meclisi_Uyeligi_Secimleri_Sonucu.pdf",
    (1963, "belediye_baskani"): "Mahalli/1963/KesinSecimSonuclari/1963_Belediye_Baskanligi_Secimleri_Sonucu.pdf",
    (1963, "belediye_meclisi"): "Mahalli/1963/KesinSecimSonuclari/1963_Belediye_Meclis_Uyeligi_Secimleri_Sonucu.pdf",
    (1968, "il_genel"): "Mahalli/1968/KesinSecimSonuclari/1968_Il_Genel_Meclisi_Uyeligi_Secimleri_Sonucu.pdf",
    (1968, "belediye_baskani"): "Mahalli/1968/KesinSecimSonuclari/1968_Belediye_Başkanligi_Secimleri_Sonucu.pdf",
    (1968, "belediye_meclisi"): "Mahalli/1968/KesinSecimSonuclari/1968_Belediye_Meclis_Uyeligi_Secimleri_Sonucu.pdf",
    (1973, "il_genel"): "Mahalli/1973/KesinSecimSonuclari/1973_Il_Genel_Meclisi_Uyeligi_Secimleri_Sonucu.pdf",
    (1973, "belediye_baskani"): "Mahalli/1973/KesinSecimSonuclari/1973_Belediye_Baskanlıgı_Secimleri_Sonucu.pdf",
    (1973, "belediye_meclisi"): "Mahalli/1973/KesinSecimSonuclari/1973_Belediye_Meclis_Uyeligi_Secimleri_Sonucu.pdf",
    (1977, "il_genel"): "Mahalli/1977/KesinSecimSonuclari/1977_Il_Genel_Meclisi_Uyeligi_Secimleri_Sonucu.pdf",
    (1977, "belediye_baskani"): "Mahalli/1977/KesinSecimSonuclari/1977_Belediye_Baskanligi_Secimleri_Sonucu.pdf",
    (1977, "belediye_meclisi"): "Mahalli/1977/KesinSecimSonuclari/1977_Belediye_Meclis_Uyeligi_Secimleri_Sonucu.pdf",
}
GRIDS = {
    (year, office): (f"Mahalli/{year}/{folder}/{prefix}{name}-Secimleri-Sonucu.pdf", labels)
    for year, folder, prefix, labels in (
        (1984, "KesinSecimSonuclari", "1984-", ("ANAP", "DYP", "HP", "MDP", "RP", "SDP")),
        (1989, "KesinSecimSonuclari", "1989-", ("ANAP", "DSP", "DYP", "IDP", "MÇP", "RP", "SHP")),
        (
            1994,
            "KesinSecimSonuclari",
            "1994-",
            ("ANAP", "BBP", "CHP", "DP", "DSP", "DYP", "İP", "MİLLET", "MHP", "RP", "SBP", "SHP", "YDP"),
        ),
        (
            1999,
            "KesinSecimsonuclar",
            "",
            (
                "ANAP",
                "BP",
                "BBP",
                "CHP",
                "DP",
                "DBP",
                "DSP",
                "DTP",
                "DYP",
                "DEHAP",
                "DEPAR",
                "EMEP",
                "FP",
                "HADEP",
                "İP",
                "LDP",
                "MİLLET",
                "MHP",
                "ÖDP",
                "SİP",
                "YDP",
            ),
        ),
    )
    for office, name in (
        ("il_genel", "Il-Genel-Meclisi-Uyeligi"),
        ("buyuksehir", "Buyuksehir-Belediye-Baskanligi"),
        ("belediye_baskani", "Belediye-Baskanligi"),
        ("belediye_meclisi", "Belediye-Meclis-Uyeligi"),
    )
}
NO_DEHAP = {(1999, "il_genel")}
NATIONAL = {
    (2004, "il_genel"): "2004MahalliIdareler/ilgenel/ilGenel.pdf",
    (2004, "buyuksehir"): "2004MahalliIdareler/buyuksehir/Buyuksehir.pdf",
    (2004, "belediye_baskani"): "2004MahalliIdareler/belediyebsk/Belediyebsk.pdf",
    (2004, "belediye_meclisi"): "2004MahalliIdareler/belediyebsk/Belediyebsk.pdf",
    (2009, "il_genel"): "2009MahalliIdareler/ResmiGazete/IlGenel.pdf",
    (2009, "buyuksehir"): "2009MahalliIdareler/ResmiGazete/Buyuksehir.pdf",
    (2009, "belediye_baskani"): "2009MahalliIdareler/ResmiGazete/BelediyeBaskanligi.pdf",
    (2009, "belediye_meclisi"): "2009MahalliIdareler/ResmiGazete/BelediyeMeclisUyeligi.pdf",
    (2014, "il_genel"): "2014MahalliIdareler/ilGenel2014.pdf",
    (2014, "buyuksehir"): "2014MahalliIdareler/Buyuksehir2014.pdf",
    (2014, "belediye_baskani"): "2014MahalliIdareler/BelediyeBaskanligi2014.pdf",
    (2014, "belediye_meclisi"): "2014MahalliIdareler/BelediyeMeclisUyeligi2014.pdf",
}
SEAT_GRIDS = {
    (2004, "il_genel"): (
        "2004MahalliIdareler/ilgenel/Illerdeilgenel.pdf",
        ("EMEP", "DSP", "ANAP", "AKP", "BBP", "DYP", "CHP", "GP", "SHP", "SP", "DP", "MHP", "BAĞ."),
    ),
    (2004, "buyuksehir"): ("2004MahalliIdareler/buyuksehir/Illerdebuyuksehir.pdf", ("AKP", "DSP", "CHP", "SHP")),
    (2004, "belediye_baskani"): (
        "2004MahalliIdareler/belediyebsk/Illerdebelediyebsk.pdf",
        ("DSP", "ANAP", "BTP", "AKP", "BBP", "ÖDP", "DYP", "CHP", "GP", "YTP", "SHP", "SP", "DP", "MHP", "BAĞ."),
    ),
    (2004, "belediye_meclisi"): (
        "2004MahalliIdareler/belediyebsk/Illerdebelediyemeslisuyeligi.pdf",
        (
            "EMEP",
            "DSP",
            "ANAP",
            "BTP",
            "AKP",
            "BBP",
            "İP",
            "ÖDP",
            "LDP",
            "TKP",
            "DYP",
            "DTP",
            "MİLLET",
            "CHP",
            "GP",
            "YTP",
            "SHP",
            "SP",
            "DP",
            "MHP",
            "BAĞ.",
        ),
    ),
    (2009, "buyuksehir"): (
        "2009MahalliIdareler/SecimSonuclari/Buyuksehir.pdf",
        ("MHP", "DSP", "DTP", "CHP", "AK PARTİ"),
    ),
    **{
        (2009, office): (
            f"2009MahalliIdareler/SecimSonuclari/{name}.pdf",
            (
                "ANAVATAN",
                "MHP",
                "LDP",
                "DSP",
                "DTP",
                "İP",
                "CHP",
                "HAK-PAR",
                "BBP",
                "BDP",
                "MİLLET",
                "ÖDP",
                "TKP",
                "DP",
                "SAADET",
                "AK PARTİ",
                "BTP",
                "EMEP",
                "HYP",
                "BAĞ.",
            ),
        )
        for office, name in (
            ("il_genel", "IllerdeIlGenel"),
            ("belediye_baskani", "IllerdeBelediyeBaskanligi"),
            ("belediye_meclisi", "IllerdeBelediyeMeclisUyeligi"),
        )
    },
}
TABLES_2014 = {
    "il_genel_votes": "2014MahalliIdareler/2014ilGenel-iller.xlsx",
    "il_genel_seats": "2014MahalliIdareler/2014ilgenel.xlsx",
    "belediye_meclisi_votes": "2014MahalliIdareler/2014BelediyeMeclis-ilbazindaOySayilari.xlsx",
    "belediye_meclisi_places": "2014MahalliIdareler/2014BelediyeMeclis-ilce-belde.xlsx",
    "belediye_meclisi_seats": "2014MahalliIdareler/2014BelediyeMeclis.xlsx",
    "belediye_baskani_seats": "2014MahalliIdareler/2014BelediyeBsk.xlsx",
    "buyuksehir_seats": "2014MahalliIdareler/2014Buyuksehir.xlsx",
    "buyuksehir_winners": "2014MahalliIdareler/Buyuksehir-KazananAdaylar.xlsx",
    "belediye_baskani_winners": "2014MahalliIdareler/BelediyeBsk-KazananAdaylar.xlsx",
}
CANDIDATES_2009 = "2009MahalliIdareler/2009Buyuksehir-AdayListesi.pdf"
PLATES = {upper(name): f"{plate:02}" for plate, name in enumerate(PROVINCES, 1)} | {
    "AFYON": "03",
    "İÇEL": "33",
    "K.MARAŞ": "46",
    "İÇEL(Mersin)": "33",
    "TOPLAM": "domestic",
}
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')
GLUED = re.compile(r"^(\D+?)(\d{1,3}(?:\.\d{3})+|\d+)$")
NUMBER = re.compile(r"^(?:\d{1,3}(?:\.\d{3})+|\d+)(?:,0)?$|^-$")
HEADINGS = {"SAYI", "ORAN(%)", "SAYISI", "VE", "ORANI", "GEÇERLİ", "OY", "KAZANILAN", "BAŞKANLIK", "ÜYELİK", "ASIL"}
FIELDS = {
    "SEÇMEN SAYISI": REGISTERED,
    "SEÇMEN SAYISI (CEZAEVİ SEÇMENLERİ DAHİL)": REGISTERED,
    "OY KULLANAN SEÇMEN SAYISI": VOTERS,
    "OYUNU KULLANAN SEÇMEN SAYISI": VOTERS,
    "GEÇERLİ OY SAYISI": VALID,
    "GEÇERLİ SAYILAN OY SAYISI": VALID,
}


def url(path: str) -> str:
    return DOCS + quote(path)


def number(text: str) -> int:
    return 0 if text == "-" else int(text.removesuffix(",0").replace(".", ""))


def fetch() -> None:
    paths = {*OLD.values(), *NATIONAL.values(), *TABLES_2014.values(), CANDIDATES_2009}
    paths |= {path for path, _ in GRIDS.values()} | {path for path, _ in SEAT_GRIDS.values()}
    for path in sorted(paths):
        for attempt in range(5):
            try:
                raw.fetch(SOURCE, url(path), suffix="." + path.rsplit(".", 1)[1])
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 ** (attempt + 1))


def render(pdf: raw.Artifact, page: int) -> dict[str, object]:
    image = PAGES / f"{pdf.sha256}-{page}.png"
    if not image.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", "100", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(pdf.path), str(image.with_suffix(""))],
            check=True,
        )
    width, height = struct.unpack(">II", image.read_bytes()[16:24])
    return {"url": pdf.url, "page": page, "image": f"pages/{SOURCE}/{image.name}", "width": width, "height": height}


type Word = tuple[float, float, float, float, str]


def words(pdf: raw.Artifact) -> list[tuple[float, float, list[Word]]]:
    pages = []
    for chunk in subprocess.run(
        ["pdftotext", "-bbox", str(pdf.path), "-"], check=True, capture_output=True, text=True
    ).stdout.split("<page ")[1:]:
        size = re.match(r'width="([\d.]+)" height="([\d.]+)"', chunk)
        if size is None:
            raise ValueError(f"{pdf.url}: page without a size")
        found: list[Word] = []
        for a, b, c, d, text in WORD.findall(chunk):
            x0, y0, x1, y1, text = float(a), float(b), float(c), float(d), html.unescape(text)
            glued = GLUED.match(text)
            if glued is None or not re.search(r"[A-ZÇĞİÖŞÜ]$", glued[1]):
                found.append((x0, y0, x1, y1, text))
                continue
            cut = x0 + (x1 - x0) * len(glued[1]) / len(text)
            found += [(x0, y0, cut, y1, glued[1]), (cut, y0, x1, y1, glued[2])]
        pages.append((float(size[1]), float(size[2]), found))
    return pages


def lines(found: list[Word]) -> list[list[Word]]:
    grouped: list[list[Word]] = []
    for word in sorted(found, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        if grouped and abs((grouped[-1][0][1] + grouped[-1][0][3]) / 2 - (word[1] + word[3]) / 2) < 3:
            grouped[-1].append(word)
        else:
            grouped.append([word])
    return [sorted(line) for line in grouped]


def grid(pdf: raw.Artifact, columns: list[str], year: int, office: str, rows: list[dict[str, object]]) -> None:
    previous: list[float] | None = None
    for page, (width, height, found) in enumerate(words(pdf), 1):
        anchors = [word for word in found if word[4] in PLATES and word[0] < width * 0.4]
        if not anchors:
            continue
        cells: dict[Word, list[Word]] = {anchor: [] for anchor in anchors}
        pitch = min(
            (b[1] - a[1] for a, b in zip(sorted(anchors, key=lambda w: w[1]), sorted(anchors, key=lambda w: w[1])[1:])),
            default=12.0,
        )
        for word in found:
            if not NUMBER.match(word[4]):
                continue
            middle = (word[1] + word[3]) / 2
            anchor = min(anchors, key=lambda a: abs((a[1] + a[3]) / 2 - middle))
            if abs((anchor[1] + anchor[3]) / 2 - middle) < pitch / 2 and word[0] > anchor[2]:
                cells[anchor].append(word)
        best: tuple[float, int, list[float]] | None = None
        for edge in (0, 2, 4):
            positions = sorted(
                float(token[edge]) if edge < 4 else (token[0] + token[2]) / 2
                for tokens in cells.values()
                for token in tokens
            )
            gaps = sorted(range(1, len(positions)), key=lambda i: positions[i] - positions[i - 1])[
                len(positions) - len(columns) :
            ]
            if len(gaps) != len(columns) - 1:
                continue
            bounds = [0, *sorted(gaps), len(positions)]
            groups = [positions[a:b] for a, b in itertools.pairwise(bounds)]
            split = min(positions[i] - positions[i - 1] for i in gaps)
            spread = max(group[-1] - group[0] for group in groups)
            if best is None or split / (spread + 1) > best[0]:
                best = (split / (spread + 1), edge, [sum(group) / len(group) for group in groups])
        if best is None or best[0] < 1:
            heads = [w for w in found if w[3] < min(a[1] for a in anchors)]
            located = []
            for label in columns:
                parts = label.split()
                for i, word in enumerate(sorted(heads)):
                    run = sorted(heads)[i : i + len(parts)]
                    if [w[4] for w in run] == parts:
                        located.append((run[0][0] + run[-1][2]) / 2)
                        break
            if len(located) == len(columns):
                edge, centers = 4, located
            elif previous is not None:
                edge, centers = 4, previous
            else:
                raise ValueError(f"{pdf.url} page {page}: columns do not separate")
        else:
            _, edge, centers = best
            previous = centers if edge == 4 else previous
        for anchor, tokens in cells.items():
            taken: dict[int, Word] = {}
            for token in tokens:
                position = float(token[edge]) if edge < 4 else (token[0] + token[2]) / 2
                column = min(range(len(columns)), key=lambda i: abs(centers[i] - position))
                spacing = min(abs(centers[i] - centers[column]) for i in range(len(columns)) if i != column)
                if abs(centers[column] - position) > spacing * 0.4:
                    raise ValueError(f"{pdf.url} page {page} {anchor[4]}: {token[4]} sits between columns")
                if column in taken:
                    raise ValueError(f"{pdf.url} page {page} {anchor[4]}: two cells in {columns[column]}")
                taken[column] = token
            for column, token in taken.items():
                rows.append(
                    {
                        "year": year,
                        "office": office,
                        "unit": PLATES[anchor[4]],
                        "column": columns[column],
                        "value": number(token[4]),
                        "url": pdf.url,
                        "page": page,
                        "box": [token[0] / width, token[1] / height, token[2] / width, token[3] / height],
                    }
                )


def national(pdf: raw.Artifact, year: int, office: str, rows: list[dict[str, object]]) -> None:
    fields: dict[str, tuple[int, int, Word, float, float]] = {}
    tables: list[list[tuple[str, Word, Word | None, int, float, float]]] = []

    def close(numbered: list[list[Word]], names: list[Word], page: int, width: float, height: float) -> None:
        middles = [(row[0][1] + row[0][3]) / 2 for row in numbered]
        table = []
        for index, row in enumerate(numbered):
            own = [
                w for w in names if min(range(len(middles)), key=lambda i: abs(middles[i] - (w[1] + w[3]) / 2)) == index
            ]
            name = " ".join(w[4] for w in sorted(own, key=lambda w: ((w[1] + w[3]) / 2, w[0])))
            table.append((name, row[0], row[-1] if len(row) > 1 else None, page, width, height))
        tables.append(table)

    for page, (width, height, found) in enumerate(words(pdf), 1):
        zone = False
        numbered: list[list[Word]] = []
        names: list[Word] = []

        for line in lines(found):
            text = " ".join(word[4] for word in line)
            numbers = [word for word in line if re.fullmatch(r"\d{1,3}(?:\.\d{3})+|\d+|-", word[4])]
            label = [word for word in line if word not in numbers and not re.fullmatch(r"%?[\d.,]+", word[4])]
            if re.search(r"(^|SİYASİ )PARTİLER|Siyasi Parti Adı", text):
                zone = True
                numbered, names = [], []
                continue
            if zone:
                if re.fullmatch(r"T ?O ?P ?L ?A ?M", " ".join(word[4] for word in label)) or (
                    label and not numbers and re.search(r"[a-zç]|^AÇIKLAMA|^Kaynak", text)
                ):
                    close(numbered, names, page, width, height)
                    zone = False
                elif {word[4] for word in label} <= HEADINGS and not numbers:
                    continue
                else:
                    names += label
                    if numbers:
                        numbered.append(numbers)
                continue
            pairs = re.findall(r"([^:]+?):\s*([\d.]+)", text) if ":" in text else []
            if not pairs and numbers and label:
                pairs = [(" ".join(word[4] for word in label), numbers[-1][4])]
            for field, value in pairs:
                field = upper(field.strip())
                kind = next(
                    (
                        column
                        for pattern, column in (
                            (r"SEÇİMİ YAPILAN|ÜYELİK SAYISI$|BAŞKANLIK SAYISI$|BELDE SAYISI$", "magnitude"),
                            (r"GEÇERLİ", "valid"),
                            (r"OY(UNU)? KULLANAN", "voters"),
                            (r"SEÇMEN SAYISI", "registered"),
                        )
                        if re.search(pattern, field)
                    ),
                    None,
                )
                if (
                    kind is None
                    or kind in fields
                    or re.search(r"BAĞIMSIZ|ORAN", field)
                    or office == "belediye_meclisi"
                    and "BAŞKANLIK" in field
                    or office == "belediye_baskani"
                    and "ÜYELİK" in field
                ):
                    continue
                word = next((w for w in line if w[4] == value), line[-1])
                fields[kind] = (number(value), page, word, width, height)
        if zone and numbered:
            close(numbered, names, page, width, height)
    if not tables:
        raise ValueError(f"{pdf.url}: no party table")
    table = tables[1 if (year, office) == (2004, "belediye_meclisi") else 0]
    for column, (value, page, word, width, height) in fields.items():
        rows.append(cell(year, office, "domestic", column, value, pdf.url, page, word, width, height))
    for name, votes, seats, page, width, height in table:
        name = "TOPLAM" if re.fullmatch(r"(T ?O ?P ?L ?A ?M)?", name) else name
        rows.append(cell(year, office, "domestic", name, number(votes[4]), pdf.url, page, votes, width, height))
        rows.append(
            cell(
                year,
                office,
                "domestic",
                f"{name}:seats",
                0 if seats is None else number(seats[4]),
                pdf.url,
                page,
                seats,
                width,
                height,
            )
        )


def cell(
    year: int,
    office: str,
    unit: str,
    column: str,
    value: int,
    link: str,
    page: int | None,
    word: Word | None,
    width: float,
    height: float,
) -> dict[str, object]:
    return {
        "year": year,
        "office": office,
        "unit": unit,
        "column": column,
        "value": value,
        "url": link,
        "page": page,
        "box": None if word is None else [word[0] / width, word[1] / height, word[2] / width, word[3] / height],
    }


def parse() -> None:
    rows: list[dict[str, object]] = []
    for (year, office), (path, labels) in GRIDS.items():
        parties = [label for label in labels if not (label == "DEHAP" and (year, office) in NO_DEHAP)]
        grid(
            raw.cached(SOURCE, url(path)),
            ["boxes", "registered", "voters", "valid", *parties, "BAĞIMSIZLAR"],
            year,
            office,
            rows,
        )
    for (year, office), (path, labels) in SEAT_GRIDS.items():
        columns = [*labels] if (year, office) == (2004, "buyuksehir") else [*labels, "TOPLAM"]
        grid(raw.cached(SOURCE, url(path)), columns, year, f"{office}:seats", rows)
    for (year, office), path in {**OLD, **NATIONAL}.items():
        national(raw.cached(SOURCE, url(path)), year, office, rows)
    for key in ("il_genel_votes", "belediye_meclisi_votes"):
        sheet = pl.read_excel(raw.cached(SOURCE, url(TABLES_2014[key])).path, has_header=False)
        header = [str(value) for value in sheet.row(0)]
        office = key.removesuffix("_votes")
        for values in sheet.slice(1).iter_rows():
            unit = PLATES[str(values[0])]
            if unit == "domestic":
                continue
            for index, label in enumerate(header):
                if values[index] is None or index == 0 or label.endswith("%") or label in ("None", "KATILIM ORANI %"):
                    continue
                column = {
                    "KAYITLI SEÇMEN SAYISI": "registered",
                    "OY KULLANAN SEÇMEN SAYISI": "voters",
                    "GEÇERLİ OY SAYISI": "valid",
                }.get(label, label)
                rows.append(
                    cell(2014, office, unit, column, int(values[index]), url(TABLES_2014[key]), None, None, 1, 1)
                )
    for key in ("il_genel_seats", "belediye_meclisi_seats", "belediye_baskani_seats", "buyuksehir_seats"):
        sheet = pl.read_excel(raw.cached(SOURCE, url(TABLES_2014[key])).path, has_header=False)
        header = [str(value) for value in sheet.row(3)]
        office = f"{key.removesuffix('_seats')}:seats"
        for values in sheet.slice(4).iter_rows():
            label = str(values[0])
            unit = PLATES.get(label)
            if unit is None or unit == "domestic":
                continue
            for index in range(1, len(header)):
                rows.append(
                    cell(2014, office, unit, header[index], int(values[index]), url(TABLES_2014[key]), None, None, 1, 1)
                )
    mayors: list[dict[str, object]] = []
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url(CANDIDATES_2009)).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for line in text.splitlines():
        city = next((name for name in sorted(PLATES, key=len, reverse=True) if line.startswith(f"{name} ")), None)
        if city is None:
            continue
        party, given, family = re.split(r"\s{2,}", line[len(city) :].strip())[:3]
        mayors.append(
            {
                "year": 2009,
                "office": "buyuksehir",
                "plate": PLATES[city],
                "district": None,
                "town": None,
                "party": party,
                "name": f"{given} {family}",
                "url": url(CANDIDATES_2009),
            }
        )
    for key in ("buyuksehir_winners", "belediye_baskani_winners"):
        sheet = pl.read_excel(raw.cached(SOURCE, url(TABLES_2014[key])).path, has_header=False)
        for values in sheet.slice(3).iter_rows():
            *place, party, given, family = (None if value is None else str(value).strip() for value in values)
            mayors.append(
                {
                    "year": 2014,
                    "office": key.removesuffix("_winners"),
                    "plate": PLATES[str(place[0])],
                    "district": place[1] if len(place) > 1 else None,
                    "town": place[2] if len(place) > 2 else None,
                    "party": party,
                    "name": f"{given} {family}",
                    "url": url(TABLES_2014[key]),
                }
            )
    frame = pl.DataFrame(rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "values.parquet")
    pl.DataFrame(mayors, infer_schema_length=None).write_parquet(PARSED / "mayors.parquet")
    print(frame.group_by("year", "office").agg(pl.col("unit").n_unique(), pl.len()).sort("year", "office"))


PAGES_HTML = {
    1963: "17-kasim-1963-mahalli-i̇dareler-genel-secimleri/80092",
    1968: "2-haziran-1968-mahalli-i̇dareler-genel-secimleri/80091",
    1973: "9-aralik-1973-mahalli-i̇dareler-genel-secimleri/80090",
    1977: "11-aralik-1977-mahalli-i̇dareler-genel-secimleri/80089",
    1984: "25-mart-1984-mahalli-idareler-genel-secimi/80065",
    1989: "26-mart-1989-mahalli-idareler-genel-secimi/80064",
    1994: "27-mart-1994-mahalli-idareler-genel-secimi/2804",
    1999: "18-nisan-1999-mahalli-idareler-genel-secimi/2805",
    2004: "28-mart-2004-mahalli-idareler-genel-secimleri/2818",
    2009: "29-mart-2009-mahalli-idareler-genel-secimleri/2820",
    2014: "30-mart-2014-mahalli-idareler-genel-secimleri/2853",
}
LABELS = {
    "Adalet Partisi": "adalet-partisi-1961",
    "Bağımsızlar": None,
    "Birlik Partisi": "turkiye-birlik-partisi",
    "Cumhuriyet Halk Partisi": "cumhuriyet-halk-partisi",
    "Cumhuriyetçi Güven Partisi": "cumhuriyetci-guven-partisi",
    "Cumhuriyetçi Köylü Millet Partisi": "cumhuriyetci-koylu-millet-partisi",
    "Demokratik Parti": "demokratik-parti",
    "Güven Partisi": "guven-partisi",
    "Millet Partisi": "millet-partisi-1962",
    "Milli Selamet Partisi": "milli-selamet-partisi",
    "Milliyetçi Hareket Partisi": "cumhuriyetci-koylu-millet-partisi",
    "Sosyalist Devrim Partisi": "sosyalist-devrim-partisi",
    "Türkiye Birlik Partisi": "turkiye-birlik-partisi",
    "Türkiye Sosyalist İşçi Partisi": "turkiye-sosyalist-isci-partisi",
    "Türkiye Ulusal Kadınlar Partisi": "turkiye-ulusal-kadinlar-partisi",
    "Türkiye İşçi Partisi": "turkiye-isci-partisi-1961",
    "Yeni Türkiye Partisi": "yeni-turkiye-partisi-1961",
    "AK PARTİ": "adalet-ve-kalkinma-partisi",
    "AKP": "adalet-ve-kalkinma-partisi",
    "ANAP": "anavatan-partisi-1983",
    "ANAVATAN": "anavatan-partisi-1983",
    "BAĞ.": None,
    "BAĞIMSIZ": None,
    "BAĞIMSIZLAR": None,
    "BBP": "buyuk-birlik-partisi",
    "BDP": "baris-ve-demokrasi-partisi",
    "BP": "baris-partisi",
    "BTP": "bagimsiz-turkiye-partisi",
    "CHP": "cumhuriyet-halk-partisi",
    "DBP": "demokrasi-ve-baris-partisi",
    "DEHAP": "demokratik-halk-partisi",
    "DEPAR": "degisen-turkiye-partisi",
    "DP": "demokrat-parti-1992",
    "DSP": "demokratik-sol-parti",
    "DTP": "demokrat-turkiye-partisi",
    "DYP": "demokrat-parti",
    "EMEP": "emek-partisi",
    "FP": "fazilet-partisi",
    "GP": "genc-parti",
    "HADEP": "halkin-demokrasi-partisi",
    "HAK-PAR": "hak-ve-ozgurlukler-partisi",
    "HDP": "halklarin-demokratik-partisi",
    "HEPAR": "hak-ve-esitlik-partisi",
    "HP": "halkci-parti",
    "HYP": "halkin-yukselisi-partisi",
    "HÜDA PAR": "hur-dava-partisi",
    "IDP": "millet-partisi",
    "LDP": "liberal-demokrat-parti",
    "MDP": "milliyetci-demokrasi-partisi",
    "MHP": "milliyetci-hareket-partisi",
    "MÇP": "milliyetci-hareket-partisi",
    "MİLLET": "millet-partisi",
    "RP": "refah-partisi",
    "SAADET": "saadet-partisi",
    "SBP": "sosyalist-birlik-partisi",
    "SDP": "sosyal-demokrasi-partisi",
    "SHP": "sosyaldemokrat-halkci-parti",
    "SP": "saadet-partisi",
    "SİP": "turkiye-komunist-partisi",
    "TKP": "turkiye-komunist-partisi",
    "TURK PARTİ": "toplumsal-uzlasma-reform-ve-kalkinma-partisi",
    "YDP": "genc-parti",
    "YTP": "yeni-turkiye-partisi",
    "YURT-P": "yurt-partisi",
    "ÖDP": "sol-parti",
    "İP": "isci-partisi",
    "ADALET VE KALKINMA PARTİSİ": "adalet-ve-kalkinma-partisi",
    "ALTERNATİF PARTİ": "alternatif-parti",
    "ANAVATAN PARTİSİ": "anavatan-partisi-1983",
    "AYDINLIK TÜRKİYE PARTİSİ": "aydinlik-turkiye-partisi",
    "BARIŞ VE DEMOKRASİ PARTİSİ": "baris-ve-demokrasi-partisi",
    "BAĞIMSIZ TÜRKİYE PARTİSİ": "bagimsiz-turkiye-partisi",
    "BÜYÜK BİRLİK PARTİSİ": "buyuk-birlik-partisi",
    "CUMHURİYET HALK PARTİSİ": "cumhuriyet-halk-partisi",
    "DEMOKRAT PARTİ": "demokrat-parti-1992",
    "DEMOKRAT PARTİ ( DYP )": "demokrat-parti",
    "DEMOKRATİK SOL PARTİ": "demokratik-sol-parti",
    "DEMOKRATİK TOPLUM PARTİSİ": "demokratik-toplum-partisi",
    "DOĞRU YOL PARTİSİ": "demokrat-parti",
    "EMEK PARTİSİ": "emek-partisi",
    "EMEĞİN PARTİSİ": "emek-partisi",
    "GENÇ PARTİ": "genc-parti",
    "HAK VE EŞİTLİK PARTİSİ": "hak-ve-esitlik-partisi",
    "HAK VE ÖZGÜRLÜKLER PARTİSİ": "hak-ve-ozgurlukler-partisi",
    "HALKIN KURTULUŞ PARTİSİ": "halkin-kurtulus-partisi",
    "HALKIN YÜKSELİŞİ PARTİSİ": "halkin-yukselisi-partisi",
    "HALKLARIN DEMOKRATİK PARTİSİ": "halklarin-demokratik-partisi",
    "HÜR DAVA PARTİSİ": "hur-dava-partisi",
    "LİBERAL DEMOKRAT PARTİ": "liberal-demokrat-parti",
    "MUHAFAZAKÂR YÜKSELİŞ PARTİSİ": "muhafazakar-yukselis-partisi",
    "MİLLET PARTİSİ": "millet-partisi",
    "MİLLİYETÇİ HAREKET PARTİSİ": "milliyetci-hareket-partisi",
    "SAADET PARTİSİ": "saadet-partisi",
    "SOSYALDEMOKRAT HALK PARTİSİ": "sosyaldemokrat-halk-partisi",
    "TOPLUMSAL UZLAŞMA REFORM VE KALKINMA PARTİSİ": "toplumsal-uzlasma-reform-ve-kalkinma-partisi",
    "TÜRKİYE KOMÜNİST PARTİSİ": "turkiye-komunist-partisi",
    "YENİ TÜRKİYE PARTİSİ": "yeni-turkiye-partisi",
    "YURT PARTİSİ": "yurt-partisi",
    "ÖZGÜRLÜK VE DAYANIŞMA PARTİSİ": "sol-parti",
    "İŞÇİ PARTİSİ": "isci-partisi",
}
DATED = {
    ("Türkiye İşçi Partisi", 1977): "turkiye-isci-partisi-1961",
    ("SHP", 2004): "sosyaldemokrat-halk-partisi",
    ("DTP", 2004): "aydinlik-turkiye-partisi",
    ("DTP", 2009): "demokratik-toplum-partisi",
    ("DP", 2009): "demokrat-parti",
    ("DP", 2014): "demokrat-parti",
    ("DEMOKRAT PARTİ", 2009): "demokrat-parti",
    ("DEMOKRAT PARTİ", 2014): "demokrat-parti",
    ("DYP", 2014): "dogru-yol-partisi",
    ("DOĞRU YOL PARTİSİ", 2014): "dogru-yol-partisi",
}
MINTED = {
    "sosyalist-devrim-partisi": ("Sosyalist Devrim Partisi", None),
    "turkiye-sosyalist-isci-partisi": ("Türkiye Sosyalist İşçi Partisi", None),
    "turkiye-ulusal-kadinlar-partisi": ("Türkiye Ulusal Kadınlar Partisi", None),
    "sosyalist-birlik-partisi": ("Sosyalist Birlik Partisi", "SBP"),
    "sosyaldemokrat-halk-partisi": ("Sosyaldemokrat Halk Partisi", "SHP"),
    "alternatif-parti": ("Alternatif Parti", None),
    "muhafazakar-yukselis-partisi": ("Muhafazakâr Yükseliş Partisi", None),
}
ANAGRAMS = {"".join(sorted(label.replace(" ", ""))): label for label in LABELS if " " in label}
OFFICES = {
    "il_genel": ("provincial-councils", "Provincial councils", "İl Genel Meclisi Üyeliği"),
    "belediye_baskani": ("mayors", "Mayors", "Belediye Başkanlığı"),
    "belediye_meclisi": ("municipal-councils", "Municipal councils", "Belediye Meclisi Üyeliği"),
    "buyuksehir": ("metropolitan-mayors", "Metropolitan mayors", "Büyükşehir Belediye Başkanlığı"),
}
DISPUTED = {
    (1963, "belediye_baskani", "domestic"),
    (1963, "belediye_meclisi", "domestic"),
    (1984, "belediye_baskani", "12"),
    (1984, "belediye_baskani", "26"),
    (1984, "belediye_baskani", "37"),
    (1984, "belediye_baskani", "46"),
    (1984, "belediye_baskani", "53"),
    (1984, "belediye_meclisi", "03"),
    (1984, "belediye_meclisi", "06"),
    (1984, "il_genel", "17"),
    (1984, "il_genel", "25"),
    (1984, "il_genel", "63"),
    (1989, "il_genel", "10"),
    (1989, "il_genel", "26"),
    (1994, "il_genel", "38"),
    (1999, "belediye_baskani", "09"),
}
DROPPED = (1984, "belediye_meclisi", "01")
RENAMED_DISTRICTS = {
    ("06", "KAZAN"): "KAHRAMANKAZAN",
    ("34", "EYÜP"): "EYÜPSULTAN",
    ("55", "19 MAYIS"): "19-MAY",
    ("71", "BAHŞİLİ"): "BAHŞILI",
}
DISPUTED_CITIES = {(1999, "01")}
SHORTFALL = {(1963, "belediye_meclisi"): -4, (1973, "belediye_baskani"): 5, (2014, "belediye_meclisi"): 2}
MISSING_1999 = {"30", "31", "61", "62"}
WHOLE = {"34": date(2004, 3, 28), "41": date(2004, 3, 28)}
AGGREGATE = (
    "YSK's summary of every race for this office held across Turkey on this day, added up by province and "
    "nationwide. Each province's council, or each municipality's mayor and council, was elected in its own race, so "
    "these totals compare the parties across the country but are not the result of a single contest."
)
DISPUTED_CAVEAT = (
    "Some of YSK's printed totals for this table do not add up: a province with more voters than registered "
    "electors or more valid votes than voters, or party votes that differ from the valid votes by more than 1 "
    "percent. The archive stores the numbers as printed and marks those provinces' values as disputed."
)
ADANA_CAVEAT = (
    "YSK's 1984 municipal council table prints party votes for Adana that add up to about 4.5 million against "
    "336,078 valid votes, so the archive leaves out Adana's party votes and keeps its electors, voters, and valid "
    "votes."
)
MISSING_CAVEAT = "YSK's 1999 municipal council table has no rows for Hakkari, Hatay, Trabzon, or Tunceli."
SEATS_CAVEAT = (
    "YSK published this year's results nationwide and as seats won in each province, without each province's votes."
)
CITY_CAVEAT = (
    "Before the metropolitan municipality was extended to the whole province, it covered only the city's central "
    "districts, so these electors and votes are for the metropolitan area alone."
)
CITY_DISPUTED = (
    "YSK's table prints party votes that add up to 99 more than the valid votes, so the archive stores the numbers as "
    "printed and marks them as disputed."
)
WINNER_CAVEAT = "YSK publishes the party that won this race, but not its votes."
NAMED_2009 = (
    "YSK publishes the 2009 metropolitan results as the party that won each city, not the winner's name. The mayor "
    "named here is that party's candidate on YSK's final candidate list for the city."
)


def party(label: str, year: int) -> str | None:
    canonical = label if label in LABELS else ANAGRAMS["".join(sorted(label.replace(" ", "")))]
    return DATED.get((canonical, year), LABELS[canonical])


def emit() -> Path:
    if len(ANAGRAMS) != sum(" " in label for label in LABELS):
        raise ValueError("two party labels share their letters")
    values = pl.read_parquet(PARSED / "values.parquet")
    mayors = pl.read_parquet(PARSED / "mayors.parquet")
    totals = ("registered", "voters", "valid", "boxes", "magnitude", "TOPLAM")
    votes = values.filter(~pl.col("office").str.ends_with(":seats"), ~pl.col("column").str.ends_with(":seats"))
    checked = votes.group_by("year", "office", "unit").agg(
        valid=pl.col("value").filter(pl.col("column") == "valid").first(),
        voters=pl.col("value").filter(pl.col("column") == "voters").first(),
        registered=pl.col("value").filter(pl.col("column") == "registered").first(),
        parties=pl.col("value").filter(~pl.col("column").is_in(totals)).sum(),
    )
    disputed = {
        (year, office, unit)
        for year, office, unit in checked.filter(
            ((pl.col("parties") - pl.col("valid")).abs() > pl.col("valid") * 0.01)
            | (pl.col("voters") > pl.col("registered"))
            | (pl.col("valid") > pl.col("voters"))
        )
        .select("year", "office", "unit")
        .iter_rows()
    }
    cities = votes.filter(pl.col("office") == "buyuksehir", pl.col("unit") != "domestic")
    mismatched = {
        (year, unit)
        for year, unit in cities.group_by("year", "unit")
        .agg(
            valid=pl.col("value").filter(pl.col("column") == "valid").first(),
            parties=pl.col("value").filter(~pl.col("column").is_in(totals)).sum(),
        )
        .filter(pl.col("valid") != pl.col("parties"))
        .select("year", "unit")
        .iter_rows()
    }
    if mismatched != DISPUTED_CITIES:
        raise ValueError(f"metropolitan votes disagree in {sorted(mismatched ^ DISPUTED_CITIES)}")
    if disputed != DISPUTED | {DROPPED}:
        raise ValueError(f"YSK's totals disagree in {sorted(disputed ^ (DISPUTED | {DROPPED}))}")
    for year, office in {**OLD, **NATIONAL}:
        table = values.filter(pl.col("year") == year, pl.col("office") == office, pl.col("unit") == "domestic")
        seats = table.filter(pl.col("column").str.ends_with(":seats"), pl.col("column") != "TOPLAM:seats")
        magnitude = table.filter(pl.col("column") == "magnitude")["value"]
        if magnitude.len() != 1 or magnitude[0] - seats["value"].sum() != SHORTFALL.get((year, office), 0):
            raise ValueError(f"{year} {office}: seats do not add up to {magnitude.to_list()}")
    for year, office in SEAT_GRIDS:
        grid_seats = values.filter(
            pl.col("year") == year,
            pl.col("office") == f"{office}:seats",
            pl.col("unit") != "domestic",
            pl.col("column") != "TOPLAM",
        )
        national_seats = values.filter(
            pl.col("year") == year,
            pl.col("office") == office,
            pl.col("unit") == "domestic",
            pl.col("column") == "magnitude",
        )["value"][0]
        if grid_seats["value"].sum() != national_seats:
            raise ValueError(f"{year} {office}: provinces' seats add up to {grid_seats['value'].sum()}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Turkish local elections from 1963 to 2014 from YSK: provincial council, mayoral, and municipal "
                "council totals by province or nationwide, metropolitan mayors by city, and mayors' names from 2009"
            ),
            polity="TR",
        )

        def name(entity: UUID, text: str, lang: str = "tr", kind: str = "official") -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official",
                valid_from=None,
                valid_from_precision="day",
                valid_to=None,
                valid_to_precision="day",
            )

        turkey = cs.polity("TR")
        nation = cs.area("tr.nation", "TR")
        provinces = {f"{plate:02}": cs.area("tr.il", f"{plate:02}") for plate in range(1, len(PROVINCES) + 1)}
        units = {code: cs.unit("tr.il", code) for code in provinces} | {"domestic": cs.unit("tr.nation", "domestic")}
        mayoral = cs.key("tr.electoral_system", "mayor")
        if cs.minted:
            raise ValueError("publish tr.ysk before tr.ysk_local")

        known: dict[str, list[tuple[str, date | None, date | None]]] = {}
        for key, text, start, end in conn.execute(
            """select e.value, n.text, n.valid_from, n.valid_to from external_id e
               join name n on n.entity_id = e.entity_id and n.lang = 'tr' and n.kind = 'official'
               where e.scheme = 'tr.party'"""
        ).fetchall():
            known.setdefault(key, []).append((text, start, end))
        parties: dict[str, UUID] = {}
        for key in sorted({value for value in LABELS.values() if value is not None} | {*DATED.values()}):
            parties[key] = cs.key("tr.party", key)
            if parties[key] in cs.minted:
                full, short = MINTED[key]
                cs.add("party", id=parties[key], kind="party", polity_id=turkey)
                name(parties[key], full)
                if short is not None:
                    name(parties[key], short, kind="abbreviation")
                known[key] = [(full, None, None)]

        def label(key: str | None, day: date) -> str:
            if key is None:
                return INDEPENDENTS
            dated = [
                text
                for text, start, end in known[key]
                if (start is None or start <= day) and (end is None or day < end)
            ]
            return (dated or [text for text, _, _ in known[key]])[0]

        sources: dict[int, UUID] = {}
        artifacts: dict[str, UUID] = {}
        page_ids: dict[tuple[str, int], str] = {}
        for year in DAYS:
            title_text = f"{year} local election results"
            sources[year] = cs.source(PUBLISHER, title_text)
            cs.add(
                "source",
                id=sources[year],
                kind="official_body",
                publisher=PUBLISHER,
                title=title_text,
                url=f"https://www.ysk.gov.tr/doc/sayfa/{PAGES_HTML[year]}.html",
                license=LICENSE,
            )
        for link in sorted({*values["url"], *mayors["url"]}):
            year = next(year for year in DAYS if f"/{year}" in link)
            artifact = raw.cached(SOURCE, link)
            artifacts[link] = cs.artifact(artifact, sources[year])
            for number in sorted(set(values.filter(pl.col("url") == link, pl.col("page").is_not_null())["page"])):
                page = render(artifact, number)
                page_ids[(link, number)] = str(cs.page(artifacts[link], number))
                cs.add(
                    "source_page",
                    id=page_ids[(link, number)],
                    artifact_id=artifacts[link],
                    page_number=number,
                    image_key=page["image"],
                    width=page["width"],
                    height=page["height"],
                )

        frames: list[pl.DataFrame] = []

        def collect(rows: pl.DataFrame, result_set: UUID, options: dict[str, str], unit_ids: dict[str, UUID]) -> None:
            frames.append(
                rows.with_columns(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("unit").replace_strict({code: str(id) for code, id in unit_ids.items()}),
                    option_id=pl.col("column").replace_strict(options, default=None),
                    page_id=pl.struct("url", "page").map_elements(
                        lambda cell: page_ids.get((cell["url"], cell["page"])), pl.String
                    ),
                )
            )

        for year, day in DAYS.items():
            election = cs.key("tr.election", f"{day}:local")
            cs.add("election", id=election, polity_id=turkey, kind="local", date_from=day, date_to=day, status="held")
            name(election, f"{year} Turkish local elections", "en")
            name(election, f"{year} Mahalli İdareler Genel Seçimleri", kind="native")
            cs.add("slug", path=f"tr/{day}-local", entity_id=election)
            held = values.filter(pl.col("year") == year)
            for office, (code, english, native) in OFFICES.items():
                rows = held.filter(pl.col("office").is_in([office, f"{office}:seats"]))
                if office == "buyuksehir":
                    rows = rows.filter(pl.col("unit") == "domestic")
                if rows.is_empty():
                    continue
                national_table = (year, office) in OLD or (year, office) in NATIONAL
                rows = rows.filter(
                    ~(national_table & (pl.col("office") == f"{office}:seats") & (pl.col("unit") == "domestic"))
                )
                if (year, office, "01") == DROPPED:
                    rows = rows.filter(~((pl.col("unit") == "01") & ~pl.col("column").is_in(totals)))
                magnitude = rows.filter(pl.col("column") == "magnitude", pl.col("unit") == "domestic")["value"]
                contest_key = f"{day}:{code}"
                contest = cs.key("tr.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    district_area_id=nation,
                    magnitude=int(magnitude[0]) if magnitude.len() else None,
                    round_number=1,
                    default_result_kind="counted",
                )
                name(contest, english, "en")
                name(contest, native, kind="native")
                cs.add("slug", path=f"tr/{day}-local/{code}", entity_id=contest)
                cs.add("note", entity_id=contest, kind="methodology", body_md=AGGREGATE)
                declared = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=declared,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources[year],
                    is_default=True,
                    completeness=0.95 if (year, office) == (1999, "belediye_meclisi") else 1,
                )
                for caveat, applies in (
                    (DISPUTED_CAVEAT, any(entry[:2] == (year, office) for entry in DISPUTED)),
                    (ADANA_CAVEAT, DROPPED[:2] == (year, office)),
                    (MISSING_CAVEAT, (year, office) == (1999, "belediye_meclisi")),
                    (SEATS_CAVEAT, year in (2004, 2009)),
                ):
                    if applies:
                        cs.add("note", entity_id=declared, kind="caveat", body_md=caveat)
                if (year, office) in SHORTFALL:
                    filled = int(magnitude[0])
                    cs.add(
                        "note",
                        entity_id=declared,
                        kind="caveat",
                        body_md=f"YSK's table gives {filled:,} seats filled, but its seats by party add up to "
                        f"{filled - SHORTFALL[(year, office)]:,}.",
                    )
                rows = rows.with_columns(
                    column=pl.col("column").str.replace(":seats$", ""),
                    measure=pl.when(pl.col("office").str.ends_with(":seats") | pl.col("column").str.ends_with(":seats"))
                    .then(SEATS)
                    .otherwise(
                        pl.col("column").replace_strict(
                            {"registered": REGISTERED, "voters": VOTERS, "valid": VALID}, default=VOTES
                        )
                    ),
                ).filter(~pl.col("column").is_in(["boxes", "magnitude", "TOPLAM"]))
                options: dict[str, str] = {}
                for column in sorted(set(rows["column"]) - {"registered", "voters", "valid"}):
                    key = party(column, year)
                    option = cs.key("tr.option", f"{contest_key}:{key or 'independents'}")
                    if str(option) not in options.values():
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="others" if key is None else "list",
                            party_id=None if key is None else parties[key],
                            ballot_label=label(key, day),
                        )
                    options[column] = str(option)
                rows = rows.with_columns(
                    flags=pl.when(
                        pl.col("unit").is_in([unit for *held_in, unit in DISPUTED if tuple(held_in) == (year, office)])
                    )
                    .then(16)
                    .otherwise(0)
                    .cast(pl.Int16)
                )
                for column, seats in (
                    rows.filter(pl.col("measure") == SEATS, pl.col("unit") == "domestic")
                    .select("column", "value")
                    .iter_rows()
                ):
                    if seats > 0:
                        cs.add("outcome", result_set_id=declared, option_id=options[column], kind="won", seats=seats)
                if rows.group_by("unit", "column", "measure").len().filter(pl.col("len") > 1).height:
                    raise ValueError(f"{year} {office}: a party appears twice in one province")
                collect(rows, declared, options, units)

        cities = sorted(
            set(
                values.filter(pl.col("office").is_in(["buyuksehir", "buyuksehir:seats"]), pl.col("unit") != "domestic")[
                    "unit"
                ]
            )
            | set(mayors.filter(pl.col("office") == "buyuksehir")["plate"])
        )
        metropolises: dict[str, UUID] = {}
        city_units: dict[str, UUID] = {}
        cs.add(
            "scheme",
            code="tr.buyuksehir",
            polity_id=turkey,
            kind="administrative",
            rank=2,
            noun="metropolitan municipality",
        )
        for plate in cities:
            city = slug(PROVINCES[int(plate) - 1])
            metropolises[plate] = cs.key("tr.polity", city)
            if metropolises[plate] in cs.minted:
                cs.add("polity", id=metropolises[plate], kind="municipality", iso_code=None)
                name(metropolises[plate], f"{PROVINCES[int(plate) - 1]} Metropolitan Municipality", "en")
                name(metropolises[plate], f"{PROVINCES[int(plate) - 1]} Büyükşehir Belediyesi", kind="native")
                cs.add("polity_parent", polity_id=metropolises[plate], parent_id=turkey)
                cs.add(
                    "polity_territory",
                    polity_id=metropolises[plate],
                    area_id=provinces[plate],
                    valid_from=WHOLE.get(plate, DAYS[2014]),
                    valid_from_precision="day",
                    valid_to=None,
                    valid_to_precision="day",
                )
                cs.add("slug", path=f"tr/{city}", entity_id=metropolises[plate])
                office = cs.key("tr.office", f"{city}-mayor")
                cs.add("office", id=office, polity_id=metropolises[plate], kind="mayor")
                name(office, f"Mayor of {PROVINCES[int(plate) - 1]}", "en")
                name(office, f"{PROVINCES[int(plate) - 1]} Büyükşehir Belediye Başkanı", kind="native")
            city_units[plate] = cs.unit("tr.buyuksehir", plate)
            cs.add(
                "reporting_unit",
                id=city_units[plate],
                polity_id=turkey,
                scheme_code="tr.buyuksehir",
                code=plate,
                parent_id=units[plate],
                area_relation="within",
                area_id=provinces[plate],
                kind="aggregate",
            )
            name(city_units[plate], f"{PROVINCES[int(plate) - 1]} Büyükşehir Belediyesi")

        def mayor_contest(
            key: str, polity: UUID, office: UUID, path: str, english: str, native: str, day: date, area: UUID | None
        ) -> tuple[UUID, UUID, str]:
            election = cs.key("tr.election", f"{day}:{key}-mayor")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=cs.key("tr.election", f"{day}:local"),
                kind="local",
                date_from=day,
                date_to=day,
                status="held",
            )
            name(election, f"{day.year} {english} mayoral election", "en")
            name(election, f"{day.year} {native} Belediye Başkanlığı Seçimi", kind="native")
            cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
            contest_key = f"{day}:{key}-mayor"
            contest = cs.key("tr.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=mayoral,
                default_result_kind="counted",
            )
            name(contest, f"Mayor of {english}", "en")
            cs.add("slug", path=f"{path}/{day}-local/mayor", entity_id=contest)
            return election, contest, contest_key

        def elect(
            contest: UUID,
            contest_key: str,
            declared: UUID,
            row: dict[str, object],
            day: date,
            option: UUID | None = None,
        ) -> None:
            key = party(str(row["party"]), day.year)
            if option is None:
                option = cs.key("tr.option", f"{contest_key}:{key or 'independents'}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None if key is None else parties[key],
                    ballot_label=label(key, day),
                )
            person = cs.key(f"{SOURCE}.person", contest_key)
            cs.add("person", id=person)
            name(person, title(str(row["name"])))
            candidacy = cs.key("tr.candidacy", f"{contest_key}:{slug(str(row['name']))}")
            cs.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="head",
                member_of_party_id=None if key is None else parties[key],
                is_independent=key is None,
            )
            if key is not None:
                cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=parties[key], kind="nominated")
            cs.add("outcome", result_set_id=declared, option_id=option, kind="elected")

        for year, day in DAYS.items():
            grid_rows = values.filter(
                pl.col("year") == year, pl.col("office") == "buyuksehir", pl.col("unit") != "domestic"
            )
            won = values.filter(
                pl.col("year") == year,
                pl.col("office") == "buyuksehir:seats",
                pl.col("unit") != "domestic",
                pl.col("column") != "TOPLAM",
                pl.col("value") > 0,
            )
            named = mayors.filter(pl.col("year") == year, pl.col("office") == "buyuksehir")
            for plate in sorted(set(grid_rows["unit"]) | set(won["unit"]) | set(named["plate"])):
                city = slug(PROVINCES[int(plate) - 1])
                election, contest, contest_key = mayor_contest(
                    city,
                    metropolises[plate],
                    cs.key("tr.office", f"{city}-mayor"),
                    f"tr/{city}",
                    PROVINCES[int(plate) - 1],
                    PROVINCES[int(plate) - 1] + " Büyükşehir",
                    day,
                    provinces[plate],
                )
                rows = grid_rows.filter(pl.col("unit") == plate)
                declared = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=declared,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources[year],
                    is_default=True,
                    completeness=0 if rows.is_empty() else 1,
                )
                if rows.is_empty():
                    cs.add("note", entity_id=declared, kind="caveat", body_md=WINNER_CAVEAT)
                if day < WHOLE.get(plate, DAYS[2014]):
                    cs.add("note", entity_id=contest, kind="caveat", body_md=CITY_CAVEAT)
                if (year, plate) in DISPUTED_CITIES:
                    cs.add("note", entity_id=declared, kind="caveat", body_md=CITY_DISPUTED)
                options: dict[str, str] = {}
                rows = rows.filter(~pl.col("column").is_in(["boxes", "magnitude", "TOPLAM"])).with_columns(
                    measure=pl.col("column").replace_strict(
                        {"registered": REGISTERED, "voters": VOTERS, "valid": VALID}, default=VOTES
                    ),
                    flags=pl.lit(16 if (year, plate) in DISPUTED_CITIES else 0, pl.Int16),
                )
                for column in sorted(set(rows["column"]) - {"registered", "voters", "valid"}):
                    key = party(column, year)
                    option = cs.key("tr.option", f"{contest_key}:{key or 'independents'}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if key is None else "candidate",
                        party_id=None if key is None else parties[key],
                        ballot_label=label(key, day),
                    )
                    options[column] = str(option)
                if not rows.is_empty():
                    collect(rows, declared, options, {plate: city_units[plate]})
                winners = won.filter(pl.col("unit") == plate)
                if winners.height > 1 or winners["value"].sum() > 1:
                    raise ValueError(f"{year} {plate}: several metropolitan winners")
                winning = None if winners.is_empty() else party(winners["column"][0], year)
                listed = [
                    row
                    for row in named.filter(pl.col("plate") == plate).to_dicts()
                    if year != 2009 or party(str(row["party"]), year) == winning
                ]
                if year == 2014 and winners.height and party(str(listed[0]["party"]), year) != winning:
                    raise ValueError(f"2014 {plate}: the winner's party differs from the seat table")
                if listed:
                    if len(listed) != 1:
                        raise ValueError(f"{year} {plate}: {len(listed)} candidates match the winning party")
                    elect(contest, contest_key, declared, listed[0], day)
                    if year == 2009:
                        cs.add("note", entity_id=declared, kind="context", body_md=NAMED_2009)
                elif winning is not None or not winners.is_empty():
                    option = cs.key("tr.option", f"{contest_key}:{winning or 'independents'}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=None if winning is None else parties[winning],
                        ballot_label=label(winning, day),
                    )
                    cs.add("outcome", result_set_id=declared, option_id=option, kind="elected")

        day = DAYS[2014]
        towns = mayors.filter(pl.col("office") == "belediye_baskani")
        districts = {
            (plate, upper(text)): id
            for plate, text, id in conn.execute(
                """select pc.code, n.text, a.id from area a
                   join area_parent ap on ap.area_id = a.id
                   join area p on p.id = ap.parent_id and p.scheme_code = 'tr.il'
                   join area_code pc on pc.area_id = p.id
                   join name n on n.entity_id = a.id
                   where a.scheme_code = 'tr.ilce'"""
            ).fetchall()
        }
        counted = towns.group_by("plate", pl.col("party").map_elements(lambda text: party(text, 2014), pl.String)).len()
        expected = values.filter(
            pl.col("year") == 2014,
            pl.col("office") == "belediye_baskani:seats",
            pl.col("unit") != "domestic",
            pl.col("value") > 0,
        ).select(
            plate="unit", party=pl.col("column").map_elements(lambda text: party(text, 2014), pl.String), len="value"
        )
        if set(counted.iter_rows()) != set(expected.with_columns(pl.col("len").cast(pl.UInt32)).iter_rows()):
            raise ValueError("2014 mayors' names disagree with the mayoralties by province")
        for row in towns.sort("plate", "district", "town", nulls_last=False).to_dicts():
            plate = str(row["plate"])
            province = slug(PROVINCES[int(plate) - 1])
            district = str(row["district"]).split("/")[0]
            central = district.endswith(" MERKEZ")
            parts = [province] + ([] if central and row["town"] is None else ["merkez" if central else slug(district)])
            if row["town"] is not None:
                parts.append(slug(str(row["town"])))
            label_text = title(str(row["town"] or (PROVINCES[int(plate) - 1] if central else district)))
            key = "-".join(parts)
            polity = cs.key("tr.polity", key)
            cs.add("polity", id=polity, kind="municipality", iso_code=None)
            name(polity, f"{label_text} Municipality", "en")
            name(polity, f"{label_text} Belediyesi", kind="native")
            cs.add("polity_parent", polity_id=polity, parent_id=metropolises.get(plate, turkey))
            path = "tr/" + "/".join(parts)
            cs.add("slug", path=path, entity_id=polity)
            office = cs.key("tr.office", f"{key}-mayor")
            cs.add("office", id=office, polity_id=polity, kind="mayor")
            name(office, f"Mayor of {label_text}", "en")
            name(office, f"{label_text} Belediye Başkanı", kind="native")
            election, contest, contest_key = mayor_contest(
                key,
                polity,
                office,
                path,
                label_text,
                label_text,
                day,
                districts[(plate, RENAMED_DISTRICTS.get((plate, district), district))],
            )
            declared = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=declared,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=sources[2014],
                is_default=True,
                completeness=0,
            )
            cs.add("note", entity_id=declared, kind="caveat", body_md=WINNER_CAVEAT)
            elect(contest, contest_key, declared, row, day)

        located = pl.concat(frames, how="diagonal_relaxed")
        keys = {
            "result_set_id": "result_set_id",
            "unit_id": "unit_id",
            "option_id": "option_id",
            "channel": pl.lit(0, pl.Int16),
            "measure": pl.col("measure").cast(pl.Int16),
            "count_round": pl.lit(0, pl.Int16),
        }
        cs.frame("result_value", located.select(**keys, flags="flags", value="value"))
        cs.frame(
            "value_locator",
            located.filter(pl.col("page_id").is_not_null(), pl.col("box").is_not_null()).select(
                **keys,
                page_id="page_id",
                x0=pl.col("box").list.get(0),
                y0=pl.col("box").list.get(1),
                x1=pl.col("box").list.get(2),
                y1=pl.col("box").list.get(3),
            ),
        )
        return cs.write()
